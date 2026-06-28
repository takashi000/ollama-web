"""Tests for model listing and capability routes."""

from __future__ import annotations

from starlette.testclient import TestClient

from ollama_web.app import create_app
from ollama_web.config import settings


def _login_client(client: TestClient) -> str:
    """Authenticate the test client and return the CSRF token."""
    login = client.post("/api/auth/login", json={"pin": str(settings.pin)})
    return str(login.headers["x-csrf-token"])


def test_model_capabilities_route_supports_slash_in_model_name() -> None:
    """HuggingFace-style model names contain slashes and must reach the API.

    Starlette's default path converter does not allow ``/`` inside a segment,
    so the route uses the ``path`` converter. Encoding ``%2F`` must decode to
    the full model name, not split the URL into multiple segments.
    """
    with TestClient(create_app()) as client:
        csrf = _login_client(client)

        slash_model = "hf.co/hotdogs/gemma-4-E4B-it-ultra-uncensored-heretic-GGUF:Q5_K_M"
        encoded = slash_model.replace("/", "%2F").replace(":", "%3A")
        response = client.get(
            f"/api/models/{encoded}/capabilities",
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        payload = response.json()
        assert "capabilities" in payload
        assert all(
            cap in [c.lower() for c in payload["capabilities"]]
            for cap in ("completion", "vision", "tools", "thinking")
        )


def test_model_capabilities_route_still_works_without_slashes() -> None:
    """Model names without slashes (e.g. cloud aliases) must keep working."""
    with TestClient(create_app()) as client:
        csrf = _login_client(client)

        response = client.get(
            "/api/models/glm-5.2%3Acloud/capabilities",
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        payload = response.json()
        assert "capabilities" in payload
        assert "completion" in [c.lower() for c in payload["capabilities"]]
