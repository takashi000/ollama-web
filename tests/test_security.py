"""Security regression tests."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ollama_web.app import create_app
from ollama_web.config import settings


@pytest.fixture
def workspace_tmp() -> Path:
    path = Path("data/.pytest-security") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _client(workspace_tmp: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "data_dir", str(workspace_tmp))
    monkeypatch.setattr(settings, "pin", "123456")
    monkeypatch.setattr(settings, "secret_key", "test-secret")
    monkeypatch.setattr(settings, "allowed_origins", [])
    return TestClient(create_app())


def test_api_requires_login(workspace_tmp: Path, monkeypatch) -> None:
    client = _client(workspace_tmp, monkeypatch)
    res = client.get("/api/sessions")
    assert res.status_code == 401


def test_login_and_csrf_protect_state_changes(workspace_tmp: Path, monkeypatch) -> None:
    client = _client(workspace_tmp, monkeypatch)

    assert client.post("/api/auth/login", json={"pin": "bad"}).status_code == 401

    login = client.post("/api/auth/login", json={"pin": "123456"})
    assert login.status_code == 200
    csrf = login.headers["x-csrf-token"]

    assert client.post("/api/sessions").status_code == 403

    created = client.post("/api/sessions", headers={"X-CSRF-Token": csrf})
    assert created.status_code == 200
    assert created.json()["id"]


def test_cors_wildcard_is_not_enabled_by_default(workspace_tmp: Path, monkeypatch) -> None:
    client = _client(workspace_tmp, monkeypatch)
    res = client.get("/api/auth/status", headers={"Origin": "https://evil.example"})
    assert res.headers.get("access-control-allow-origin") != "*"


def test_invalid_session_id_is_rejected_after_login(workspace_tmp: Path, monkeypatch) -> None:
    client = _client(workspace_tmp, monkeypatch)
    login = client.post("/api/auth/login", json={"pin": "123456"})
    csrf = login.headers["x-csrf-token"]

    res = client.delete("/api/sessions/../x", headers={"X-CSRF-Token": csrf})
    assert res.status_code in {400, 404}


def test_private_urls_are_blocked() -> None:
    from ollama_web.tools.helper.safe_http import UnsafeURL, validate_public_http_url

    for url in (
        "http://127.0.0.1:11434",
        "http://localhost:8000",
        "http://192.168.1.1",
        "http://10.0.0.1",
        "http://169.254.169.254/latest/meta-data",
    ):
        try:
            validate_public_http_url(url)
        except UnsafeURL:
            continue
        raise AssertionError(f"URL should have been blocked: {url}")


def test_pdf_to_text_does_not_read_local_paths(workspace_tmp: Path) -> None:
    from ollama_web.tools.pdf import pdf_to_text

    secret = workspace_tmp / "secret.pdf"
    secret.write_bytes(b"%PDF-1.4\n")
    assert pdf_to_text(str(secret)) == "Only http(s) PDF URLs are supported."


def test_frontend_uses_local_sanitizer_and_no_cdn() -> None:
    html = Path("src/ollama_web/templates/index.html").read_text(encoding="utf-8")
    app_js = Path("src/ollama_web/static/app.js").read_text(encoding="utf-8")

    assert "cdnjs.cloudflare.com" not in html
    assert "vendor/purify.min.js" in html
    assert "DOMPurify.sanitize" in app_js


def test_katex_local_font_assets_are_present() -> None:
    css_path = Path("src/ollama_web/static/vendor/katex.min.css")
    vendor_dir = css_path.parent
    css = css_path.read_text(encoding="utf-8")
    font_paths = sorted(set(re.findall(r"fonts/KaTeX_[^)'\"\s]+", css)))

    assert font_paths
    assert not [path for path in font_paths if not (vendor_dir / path).exists()]


def test_session_delete_action_is_visible_and_touch_sized_on_mobile() -> None:
    css_path = Path(__file__).parents[1] / "src" / "ollama_web" / "static" / "style.css"
    css = css_path.read_text(encoding="utf-8")
    base_rule = css.index("#session-list li .delete {")
    touch_rule = css.index("@media (max-width: 768px), (hover: none), (pointer: coarse)")
    touch_block = css[touch_rule : css.index("#session-list li.empty", touch_rule)]

    # The touch override must follow the default display:none rule so it wins
    # in the cascade without changing hover-capable desktop behavior.
    assert touch_rule > base_rule
    assert "display: inline-flex" in touch_block
    assert "min-width: 44px" in touch_block
    assert "min-height: 44px" in touch_block


def test_session_delete_uses_in_app_dialog_instead_of_native_confirm() -> None:
    root = Path(__file__).parents[1] / "src" / "ollama_web"
    template = (root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="delete-session-modal"' in template
    assert 'role="dialog"' in template
    assert 'id="delete-session-cancel"' in template
    assert 'id="delete-session-confirm"' in template
    assert "confirm(" not in script
    assert 'sessionListEl.addEventListener("click"' in script
    assert "openDeleteSessionDialog(li.dataset.id, deleteButton)" in script


def test_authenticated_audio_content_supports_range_requests(
    workspace_tmp: Path, monkeypatch
) -> None:
    client = _client(workspace_tmp, monkeypatch)
    session = client.app.state.session_store.create()
    audio = client.app.state.session_store.add_file(
        session_id=session["id"],
        name="generated.mp3",
        data=b"0123456789",
        mime="audio/mpeg",
        text="",
        source="mcp__music__generate",
    )
    url = f"/api/sessions/{session['id']}/files/{audio.id}/content"

    assert client.get(url).status_code == 401
    assert client.post("/api/auth/login", json={"pin": "123456"}).status_code == 200
    response = client.get(url, headers={"Range": "bytes=2-5"})

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "media-src 'self'" in response.headers["content-security-policy"]


def test_file_content_does_not_inline_non_media(workspace_tmp: Path, monkeypatch) -> None:
    client = _client(workspace_tmp, monkeypatch)
    session = client.app.state.session_store.create()
    text_file = client.app.state.session_store.add_file(
        session_id=session["id"],
        name="notes.html",
        data=b"<script>alert(1)</script>",
        mime="text/html",
        text="",
    )
    client.post("/api/auth/login", json={"pin": "123456"})

    response = client.get(f"/api/sessions/{session['id']}/files/{text_file.id}/content")
    assert response.status_code == 415

    disguised_html = client.app.state.session_store.add_file(
        session_id=session["id"],
        name="disguised.png",
        data=b"<script>alert(1)</script>",
        mime="text/html",
        text="",
    )
    response = client.get(
        f"/api/sessions/{session['id']}/files/{disguised_html.id}/content"
    )
    assert response.status_code == 415


def test_uploaded_audio_is_playable_but_excluded_from_attachment_text(
    workspace_tmp: Path, monkeypatch
) -> None:
    from ollama_web.routes.chat import _attachment_context, _has_audio_attachments

    client = _client(workspace_tmp, monkeypatch)
    login = client.post("/api/auth/login", json={"pin": "123456"})
    csrf = login.headers["x-csrf-token"]
    session = client.app.state.session_store.create()
    response = client.post(
        f"/api/sessions/{session['id']}/files",
        headers={"X-CSRF-Token": csrf},
        files={"files": ("recording.mp3", b"audio-bytes", "audio/mpeg")},
    )

    assert response.status_code == 200
    audio = response.json()["files"][0]
    assert audio["text"] == ""
    assert _has_audio_attachments(
        client.app.state.session_store, session["id"], [audio["id"]]
    )
    assert (
        _attachment_context(
            client.app.state.session_store,
            session["id"],
            [audio["id"]],
            total_limit=4000,
            language="ja",
        )
        == ""
    )
    playable = client.get(f"/api/sessions/{session['id']}/files/{audio['id']}/content")
    assert playable.status_code == 200
    assert playable.content == b"audio-bytes"
