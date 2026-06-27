"""Persistent session storage for ollama-web."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from .config import settings
from .i18n import t

_UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_valid_id(value: str) -> bool:
    """Return whether a route id is a generated UUID hex string."""
    return bool(_UUID_HEX_RE.fullmatch(value))


_IMAGE_MIMES: set[str] = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/bmp",
    "image/webp",
    "image/tiff",
    "image/x-png",
    "image/jpg",
}

_IMAGE_SUFFIXES: set[str] = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


@dataclass
class ChatFile:
    id: str
    name: str
    path: str
    mime: str
    size: int
    text: str
    source: str  # "upload" | "fetch"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "mime": self.mime,
            "size": self.size,
            "text": self.text,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatFile:
        return cls(
            id=data["id"],
            name=data["name"],
            path=data["path"],
            mime=data.get("mime", "application/octet-stream"),
            size=data.get("size", 0),
            text=data.get("text", ""),
            source=data.get("source", "upload"),
        )

    @property
    def is_image(self) -> bool:
        name_lower = self.name.lower()
        if self.mime.lower() in _IMAGE_MIMES:
            return True
        for suffix in _IMAGE_SUFFIXES:
            if name_lower.endswith(suffix):
                return True
        return False


@dataclass
class ChatMessage:
    role: str
    content: str
    attachments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "attachments": list(self.attachments),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatMessage:
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            attachments=list(data.get("attachments", [])),
        )


class SessionStore:
    """Manage chat sessions as JSON files under the configured data directory."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir or settings.data_dir).resolve()
        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def _session_file(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _files_dir(self, session_id: str) -> Path:
        files_dir = self._session_dir(session_id) / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        return files_dir

    def list_sessions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.sessions_dir.exists():
            return out
        for entry in sorted(
            self.sessions_dir.iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            if not entry.is_dir():
                continue
            session_file = entry / "session.json"
            if not session_file.exists():
                continue
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                out.append(
                    {
                        "id": data["id"],
                        "title": data.get("title", "Untitled"),
                        "updated_at": data.get("updated_at", data.get("created_at", "")),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        return out

    def create(self, title: str | None = None) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        now = _now()
        session = {
            "id": session_id,
            "title": title or t("session.default_title"),
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "files": [],
        }
        self._session_dir(session_id).mkdir(parents=True, exist_ok=True)
        self._save(session_id, session)
        return session

    def get(self, session_id: str) -> dict[str, Any] | None:
        if not is_valid_id(session_id):
            return None
        session_file = self._session_file(session_id)
        if not session_file.exists():
            return None
        try:
            return cast(dict[str, Any], json.loads(session_file.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return None

    def save(self, session: dict[str, Any]) -> None:
        if not is_valid_id(str(session.get("id", ""))):
            return
        session["updated_at"] = _now()
        self._save(session["id"], session)

    def _save(self, session_id: str, session: dict[str, Any]) -> None:
        if not is_valid_id(session_id):
            return
        session_file = self._session_file(session_id)
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete(self, session_id: str) -> bool:
        if not is_valid_id(session_id):
            return False
        session_dir = self._session_dir(session_id)
        try:
            session_dir.resolve().relative_to(self.sessions_dir.resolve())
        except ValueError:
            return False
        if not session_dir.exists():
            return False
        try:
            shutil.rmtree(session_dir)
            return True
        except Exception:  # noqa: BLE001
            return False

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
    ) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        session["messages"].append(
            ChatMessage(
                role=role,
                content=content,
                attachments=list(attachments or []),
            ).to_dict()
        )
        if role == "user" and len(session["messages"]) <= 2:
            first_user = next(
                (m for m in session["messages"] if m["role"] == "user"),
                None,
            )
            if first_user:
                title = first_user["content"].strip().replace("\n", " ")[:40]
                session["title"] = title + ("…" if len(first_user["content"]) > 40 else "")
        self.save(session)
        return session

    def add_file(
        self,
        session_id: str,
        name: str,
        data: bytes,
        mime: str,
        text: str,
        source: str = "upload",
    ) -> ChatFile:
        if not is_valid_id(session_id):
            raise ValueError("Invalid session id")
        self._session_dir(session_id).mkdir(parents=True, exist_ok=True)
        file_id = uuid.uuid4().hex
        ext = Path(name).suffix
        stored_name = f"{file_id}{ext}"
        file_path = self._files_dir(session_id) / stored_name
        file_path.write_bytes(data)
        rel_path = str(
            (self._session_dir(session_id) / "files" / stored_name).relative_to(self.data_dir)
        )
        chat_file = ChatFile(
            id=file_id,
            name=name,
            path=rel_path,
            mime=mime,
            size=len(data),
            text=text,
            source=source,
        )
        session = self.get(session_id)
        if session is None:
            session = self.create()
            session_id = session["id"]
        session["files"].append(chat_file.to_dict())
        self.save(session)
        return chat_file

    def remove_file(self, session_id: str, file_id: str) -> bool:
        if not is_valid_id(session_id) or not is_valid_id(file_id):
            return False
        session = self.get(session_id)
        if session is None:
            return False
        files = session.get("files", [])
        target = next((f for f in files if f["id"] == file_id), None)
        if target is None:
            return False
        files.remove(target)
        session["files"] = files
        try:
            abs_path = (self.data_dir / target["path"]).resolve()
            abs_path.relative_to(self.data_dir)
            if abs_path.exists():
                abs_path.unlink()
        except Exception:  # noqa: BLE001
            pass
        self.save(session)
        return True

    def get_file(self, session_id: str, file_id: str) -> ChatFile | None:
        if not is_valid_id(session_id) or not is_valid_id(file_id):
            return None
        session = self.get(session_id)
        if session is None:
            return None
        for f in session.get("files", []):
            if f["id"] == file_id:
                return ChatFile.from_dict(f)
        return None

    def get_file_data(self, session_id: str, file_id: str) -> bytes | None:
        chat_file = self.get_file(session_id, file_id)
        if chat_file is None:
            return None
        abs_path = (self.data_dir / chat_file.path).resolve()
        try:
            abs_path.relative_to(self.data_dir)
            return abs_path.read_bytes()
        except Exception:  # noqa: BLE001
            return None

    def clear_messages(self, session_id: str) -> dict[str, Any] | None:
        """Clear all messages and attached files from the session."""
        if not is_valid_id(session_id):
            return None
        session = self.get(session_id)
        if session is None:
            return None
        # Remove uploaded/fetched physical files.
        files_dir = self._files_dir(session_id)
        try:
            if files_dir.exists():
                shutil.rmtree(files_dir)
                files_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        session["messages"] = []
        session["files"] = []
        session["updated_at"] = _now()
        self._save(session_id, session)
        return session
