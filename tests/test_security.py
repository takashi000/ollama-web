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
