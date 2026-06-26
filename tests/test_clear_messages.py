"""Tests for clearing session messages and files."""

from __future__ import annotations

import tempfile

from ollama_web.sessions import SessionStore, is_valid_id


def make_store() -> SessionStore:
    tmp = tempfile.mkdtemp()
    return SessionStore(data_dir=tmp)


def test_clear_messages_removes_history_and_files():
    store = make_store()
    session = store.create(title="test")
    session_id = session["id"]
    assert is_valid_id(session_id)

    chat_file = store.add_file(
        session_id=session_id,
        name="notes.txt",
        data=b"hello",
        mime="text/plain",
        text="hello",
    )
    store.add_message(
        session_id=session_id,
        role="user",
        content="hi",
        attachments=[chat_file.id],
    )
    store.add_message(session_id=session_id, role="assistant", content="hello")

    files_dir = store._files_dir(session_id)
    stored_path = store.data_dir / chat_file.path
    assert stored_path.exists()
    assert (files_dir / f"{chat_file.id}.txt").exists()

    cleared = store.clear_messages(session_id)
    assert cleared is not None
    assert cleared["messages"] == []
    assert cleared["files"] == []
    assert not stored_path.exists()
    assert files_dir.exists()

    reloaded = store.get(session_id)
    assert reloaded is not None
    assert reloaded["messages"] == []
    assert reloaded["files"] == []


def test_clear_messages_invalid_session_id():
    store = make_store()
    assert store.clear_messages("not-a-valid-id") is None


def test_clear_messages_missing_session():
    store = make_store()
    assert store.clear_messages("0" * 32) is None