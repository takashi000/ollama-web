"""Request-owned chat processes. Only the parent may persist generated output."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import signal
import subprocess
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import fields
from multiprocessing.connection import Connection
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anyio
from starlette.requests import Request

from .config import Settings, settings
from .sessions import ChatFile, SessionStore

logger = logging.getLogger("ollama_web.worker")


class _WorkerStore(SessionStore):
    """Read existing attachments locally; send all generated files to the parent."""

    def __init__(
        self, data_dir: str, connection: Connection, snapshot: dict[str, Any] | None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.sessions_dir = self.data_dir / "sessions"
        self.connection = connection
        self.snapshot = snapshot

    def get(self, session_id: str) -> dict[str, Any] | None:
        if self.snapshot is not None and self.snapshot["id"] == session_id:
            return self.snapshot
        return None

    def add_file(
        self,
        session_id: str,
        name: str,
        data: bytes,
        mime: str,
        text: str,
        source: str = "upload",
    ) -> ChatFile:
        self.connection.send(("file", (session_id, name, data, mime, text, source)))
        result = cast(ChatFile, self.connection.recv())
        if self.snapshot is not None:
            self.snapshot["files"].append(result.to_dict())
        return result


def _child(connection: Connection, stop: Any, runtime: Settings,
           payload: dict[str, Any], session_id: str | None,
           snapshot: dict[str, Any] | None) -> None:
    if os.name != "nt":
        cast(Any, os).setsid()
    # No model, tool or child process may start before containment is installed.
    if connection.recv() != "start":
        return
    for item in fields(runtime):
        setattr(settings, item.name, getattr(runtime, item.name))
    handler = logging.FileHandler(Path(runtime.data_dir) / "server.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    worker_logger = logging.getLogger("ollama_web")
    worker_logger.setLevel(logging.INFO)
    worker_logger.addHandler(handler)
    store = _WorkerStore(runtime.data_dir, connection, snapshot)
    request = cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=settings, session_store=store,
    ))))

    async def run() -> None:
        from .routes.chat import _chat_event_stream

        async def produce() -> None:
            async for event in _chat_event_stream(request, payload, session_id):
                if stop.is_set():
                    return
                connection.send(("event", event))

        task = asyncio.create_task(produce())
        while not task.done():
            if stop.is_set():
                task.cancel()
                break
            await asyncio.sleep(0.01)
        await task
        if not stop.is_set():
            connection.send(("finished", None))

    try:
        asyncio.run(run())
    except (asyncio.CancelledError, BrokenPipeError, EOFError):
        pass
    finally:
        worker_logger.removeHandler(handler)
        handler.close()
        connection.close()


class _WindowsJob:
    """Kill-on-close containment, including MCP grandchildren (Windows only)."""

    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IOCounts(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read", "write", "other")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IOCounts),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel: Any = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise cast(Any, ctypes).WinError(cast(Any, ctypes).get_last_error())
        process = None
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits),
                                                  ctypes.sizeof(limits)):
                raise cast(Any, ctypes).WinError(cast(Any, ctypes).get_last_error())
            process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
            if not process or not kernel.AssignProcessToJobObject(self.handle, process):
                raise cast(Any, ctypes).WinError(cast(Any, ctypes).get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                kernel.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _kill_posix_tree(pid: int) -> None:
    """Reap owned children even when the MCP SDK starts a separate session.

    Freeze parents before enumerating, so a tool cannot fork between discovery
    and termination. Never signal an inherited group belonging to the ASGI app.
    """
    posix = cast(Any, os)
    sig = cast(Any, signal)
    owned = {pid}
    groups: set[int] = set()

    def freeze(process_id: int) -> None:
        try:
            os.kill(process_id, sig.SIGSTOP)
        except ProcessLookupError:
            pass

    freeze(pid)
    try:
        while True:
            table: dict[int, tuple[int, int]] = {}
            if Path("/proc/self/stat").exists():
                for entry in Path("/proc").iterdir():
                    if not entry.name.isdecimal():
                        continue
                    try:
                        values = (entry / "stat").read_text().rpartition(")")[2].split()
                        table[int(entry.name)] = (int(values[1]), int(values[2]))
                    except (OSError, ValueError, IndexError):
                        continue
            else:
                # Portable POSIX fallback. Linux uses /proc and starts no helper.
                output = subprocess.check_output(
                    ["ps", "-A", "-o", "pid=,ppid=,pgid="], text=True, timeout=0.5,
                )
                for line in output.splitlines():
                    process_id, parent_id, group_id = map(int, line.split())
                    table[process_id] = (parent_id, group_id)
            discovered = set(owned)
            while True:
                children = {p for p, (parent, _) in table.items() if parent in discovered}
                if children <= discovered:
                    break
                discovered.update(children)
            for process_id in discovered - owned:
                freeze(process_id)
            groups.update(group for p, (_, group) in table.items() if p in discovered)
            if discovered == owned:
                break
            owned = discovered
    finally:
        for group_id in groups & owned:
            try:
                posix.killpg(group_id, sig.SIGKILL)
            except ProcessLookupError:
                pass
        for process_id in owned:
            try:
                os.kill(process_id, sig.SIGKILL)
            except ProcessLookupError:
                pass


async def worker_events(
    runtime: Settings, store: SessionStore, payload: dict[str, Any], session_id: str | None,
    before_close: Callable[[], None] | None = None,
) -> AsyncGenerator[dict[str, Any]]:
    """Run one chat and reap its entire local process tree on close/disconnect."""
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    stop = ctx.Event()
    snapshot = store.get(session_id) if session_id else None
    process = ctx.Process(target=_child, args=(child, stop, runtime, payload, session_id, snapshot))
    job = None
    started = False
    try:
        process.start()
        started = True
        child.close()
        if os.name == "nt":
            job = _WindowsJob(cast(int, process.pid))
        logger.info("chat worker started pid=%s", process.pid)
        parent.send("start")
        finished = False
        while not finished:
            if parent.poll():
                try:
                    kind, value = parent.recv()
                except EOFError:
                    raise RuntimeError("Chat worker closed before completing") from None
                if kind == "event":
                    yield value
                elif kind == "file":
                    # No await between accepting and committing a file.
                    if value[0] != session_id or not session_id or store.get(session_id) is None:
                        raise ValueError("Generated file belongs to an unavailable session")
                    parent.send(store.add_file(*value))
                elif kind == "finished":
                    finished = True
            elif not process.is_alive():
                raise RuntimeError(f"Chat worker exited unexpectedly: {process.exitcode}")
            else:
                await anyio.sleep(0.01)
    except Exception as exc:
        # Report failures before committing the accepted prefix in finally.
        # CancelledError/GeneratorExit deliberately bypass this path.
        logger.exception("chat worker failed")
        yield {"type": "error", "message": str(exc)}
        yield {"type": "done"}
    finally:
        # Cancelled ASGI scopes must still finish cleanup. Never drain late output.
        with anyio.CancelScope(shield=True):
            stop.set()
            # Commit the accepted prefix before waiting for worker teardown, so
            # an immediately submitted next request sees the partial answer.
            if before_close is not None:
                try:
                    before_close()
                except Exception:
                    logger.exception("failed to save chat before worker cleanup")
            deadline = time.monotonic() + 1.0
            while started and process.is_alive() and time.monotonic() < deadline:
                await anyio.sleep(0.01)
            if job is not None:
                job.close()
            elif (
                os.name != "nt" and started and process.pid is not None and process.is_alive()
            ):
                _kill_posix_tree(process.pid)
            if started:
                if process.is_alive():
                    process.kill()
                process.join(timeout=0.5)
                if process.is_alive():
                    raise RuntimeError("Chat worker did not stop")
                logger.info("chat worker stopped pid=%s exit=%s", process.pid, process.exitcode)
                process.close()
            parent.close()
            child.close()
