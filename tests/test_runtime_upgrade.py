"""Behavioral coverage for Python 3.14/3.15 and the media security update."""

from __future__ import annotations

import io
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from PIL import Image
from starlette.testclient import TestClient

from ollama_web.app import create_app
from ollama_web.config import settings
from ollama_web.mcp import load_mcp_config, save_mcp_config
from ollama_web.sessions import SessionStore
from ollama_web.settings_store import load_app_settings, save_app_settings
from ollama_web.tools.helper.image import resize_image


@pytest.mark.parametrize(
    ("fmt", "suffix", "mode"),
    [
        ("PNG", "png", "RGBA"),
        ("PNG", "png", "I;16"),
        ("JPEG", "jpg", "RGB"),
        ("GIF", "gif", "P"),
        ("BMP", "bmp", "RGB"),
        ("WEBP", "webp", "RGBA"),
        ("TIFF", "tiff", "RGB"),
    ],
)
def test_image_conversion_reencodes_and_resizes(fmt, suffix, mode):
    original = io.BytesIO()
    Image.new(mode, (64, 32)).save(original, format=fmt)
    data = original.getvalue()
    result = resize_image(data, name=f"画像.{suffix}", max_dimension=16)
    # A swallowed exception would return the original bytes and fail here.
    assert result != data
    with Image.open(io.BytesIO(result)) as image:
        image.load()
        assert image.size == (16, 8)
        assert image.format == fmt
        assert image.mode == ("P" if fmt == "GIF" else "RGB")


def test_multiframe_image_uses_first_frame():
    original = io.BytesIO()
    Image.new("RGB", (64, 32), "red").save(
        original,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (64, 32), "blue")],
    )
    result = resize_image(original.getvalue(), name="animation.gif", max_dimension=16)
    with Image.open(io.BytesIO(result)) as image:
        assert image.n_frames == 1
        assert image.size == (16, 8)
        assert image.convert("RGB").getpixel((0, 0)) == (255, 0, 0)


def test_image_limit_and_invalid_input_preserve_existing_fallback(monkeypatch):
    original = io.BytesIO()
    Image.new("RGB", (32, 32)).save(original, format="PNG")
    data = original.getvalue()
    monkeypatch.setattr(settings, "max_image_pixels", 100)
    assert resize_image(data) == data
    assert resize_image(b"not an image") == b"not an image"
    assert resize_image(b"") == b""


@pytest.mark.parametrize(
    "payload",
    [
        b"%PDF-1.7\ntruncated",
        b"%PDF-1.7\nxref\n0 999999999\ntrailer\n<< /Size 999999999 >>\n"
        b"startxref\n9\n%%EOF\n",
    ],
)
def test_malformed_pdf_finishes_in_isolated_process(payload, tmp_path):
    document = tmp_path / "malformed.pdf"
    document.write_bytes(payload)
    script = (
        "from pathlib import Path; import sys; "
        "from ollama_web.tools.helper.pdf import extract_pdf_page; "
        "result = extract_pdf_page(Path(sys.argv[1]).read_bytes(), max_chars=100); "
        "assert not result.has_more; print(result.text)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(document)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    assert "Failed to parse PDF" in result.stdout


def test_japanese_existing_data_and_cp932_upload_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "pin", "123456")
    store = SessionStore(tmp_path)
    session = store.create("以前の会話")
    session["messages"] = [{"role": "user", "content": "日本語の履歴", "attachments": []}]
    store.save(session)
    with TestClient(create_app()) as client:
        login = client.post("/api/auth/login", json={"pin": "123456"})
        csrf = {"X-CSRF-Token": login.headers["x-csrf-token"]}
        assert client.get(f"/api/sessions/{session['id']}").json() == session
        upload = client.post(
            f"/api/sessions/{session['id']}/files",
            headers=csrf,
            files={"files": ("添付.txt", "日本語の添付".encode("cp932"), "text/plain")},
        )
        assert upload.status_code == 200
        file = upload.json()["files"][0]
        assert file["name"] == "添付.txt"
        assert file["text"] == "日本語の添付"
    reloaded = SessionStore(tmp_path).get(session["id"])
    assert reloaded["messages"] == session["messages"]
    assert reloaded["files"][0]["name"] == "添付.txt"
    value = load_app_settings(tmp_path, "ja")
    value["ollama"]["system_prompt"] = "日本語で回答してください"
    save_app_settings(tmp_path, value)
    assert load_app_settings(tmp_path, "en") == value
    config = {"mcpServers": {"日本語": {"url": "https://example.com/mcp"}}}
    save_mcp_config(config=config)
    assert load_mcp_config() == config
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == value


def test_httpx_tls_accepts_trusted_ca_and_rejects_untrusted(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "ca.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"tls-ok")

        def log_message(self, *args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"https://localhost:{server.server_port}"
            trusted = ssl.create_default_context(cafile=str(cert_file))
            with httpx.Client(verify=trusted, trust_env=False, timeout=3) as client:
                assert client.get(url).text == "tls-ok"
            with httpx.Client(trust_env=False, timeout=3) as client:
                with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                    client.get(url)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_cli_serves_http_and_shuts_down(tmp_path):
    # The installed entry point runs the real Uvicorn server. The wrapper adds
    # a portable shutdown channel, avoiding platform-dependent console signals.
    startup = '''
import sys, threading, uvicorn
from importlib.metadata import distribution
def run(app, **kwargs):
    server = uvicorn.Server(uvicorn.Config(app, **kwargs))
    def stop():
        sys.stdin.readline()
        server.should_exit = True
    threading.Thread(target=stop, daemon=True).start()
    server.run()
uvicorn.run = run
entry = next(e for e in distribution("ollama-web").entry_points if e.name == "ollama-web")
sys.exit(entry.load()())
'''
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "OLLAMA_WEB_DATA_DIR": str(tmp_path),
        "OLLAMA_WEB_PORT": str(port),
        "OLLAMA_WEB_HOST": "127.0.0.1",
        "OLLAMA_WEB_PIN": "123456",
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.Popen(
        [sys.executable, "-c", startup],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 10
        with httpx.Client(trust_env=False, timeout=0.5) as client:
            while True:
                try:
                    response = client.get(f"http://127.0.0.1:{port}/api/auth/status")
                    assert response.status_code == 200
                    break
                except httpx.TransportError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        pytest.fail("CLI did not become ready")
                    time.sleep(0.05)
        stdout, stderr = process.communicate("stop\n", timeout=10)
        assert process.returncode == 0, stderr
        assert "ollama-web starting" in stdout
        assert "Application shutdown complete" in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
