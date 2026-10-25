"""Inject a non-cooperative parser into the real request worker for lifecycle tests."""

import os
from pathlib import Path


def blocked_pdf_worker(connection, stop, runtime, payload, session_id, snapshot):
    from ollama_web.chat_worker import _child
    from ollama_web.tools.helper import pdf

    def parse_forever(*args, **kwargs):
        marker = Path(payload["marker"])
        marker.with_suffix(".tmp").write_text(str(os.getpid()))
        marker.with_suffix(".tmp").replace(marker)
        while True:
            sum(range(10000))

    pdf.PdfReader = parse_forever
    _child(connection, stop, runtime, payload, session_id, snapshot)


def crashing_worker(connection, stop, runtime, payload, session_id, snapshot):
    if os.name != "nt":
        os.setsid()
    connection.recv()
    os._exit(23)
