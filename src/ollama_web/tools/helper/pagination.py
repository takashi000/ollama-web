"""Pagination helpers for paged tool results.

This module provides an opaque continuation-token mechanism so that LLM-driven
tools can return large content in pages without requiring the model to remember
numeric offsets. The token encodes the resumption state (URL + offset) as
base64url(JSON) and is meant to be passed back verbatim by the model via the
``continue_from`` parameter.

Design rationale:
- The token is opaque: the LLM should copy the ``next_token`` value from a
  previous result and pass it to ``continue_from`` without interpreting it.
- The state is encoded in the token itself, so the server stays stateless and
  does not need to cache previous results.
- A ``tool`` field guards against a token issued by one tool being replayed
  against a different tool.
"""

from __future__ import annotations

import base64
import json
from typing import Any

# Token format version. Bump when the payload structure changes in a
# backwards-incompatible way; ``parse_token`` rejects unknown versions.
_TOKEN_VERSION = 1

# Marker that separates the page body from the page-info footer in tool
# results. Kept in sync with ``llm._trim_tool_result``.
PAGE_INFO_MARKER = "\n---page-info---"


class InvalidTokenError(ValueError):
    """Raised when a continuation token is malformed or invalid."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    # Re-add any stripped padding before decoding.
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + padding)


def make_token(tool: str, url: str, offset: int, **extra: Any) -> str:
    """Create an opaque continuation token.

    Args:
      tool: Name of the issuing tool (e.g. ``"scrape_url"``).
      url: The URL the content was fetched from.
      offset: The resumption offset (character or page index, tool-specific).
      **extra: Additional tool-specific fields to embed in the token payload
        (e.g. ``matched`` candidate list for ``search_and_fetch``).

    Returns:
      A base64url-encoded JSON string.
    """
    payload: dict[str, Any] = {
        "v": _TOKEN_VERSION,
        "tool": tool,
        "url": url,
        "offset": int(offset),
    }
    payload.update(extra)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _b64url_encode(raw)


def parse_token(token: str | None, expected_tool: str) -> dict[str, Any]:
    """Validate and decode a continuation token.

    Args:
      token: The opaque token string (typically from ``continue_from``).
        ``None`` or an empty string raises :class:`InvalidTokenError`.
      expected_tool: The tool name that should appear in the token's ``tool``
        field. Mismatches raise :class:`InvalidTokenError`.

    Returns:
      The decoded payload dict (includes ``url``, ``offset`` and any extra
      fields the issuing tool embedded).

    Raises:
      InvalidTokenError: If the token is empty, not valid base64, not valid
        JSON, missing required fields, has an unsupported version, or names a
        different tool than ``expected_tool``.
    """
    if not token or not token.strip():
        raise InvalidTokenError("Empty continuation token.")

    try:
        raw = _b64url_decode(token.strip())
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise InvalidTokenError(f"Token is not valid base64url: {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidTokenError(f"Token payload is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise InvalidTokenError("Token payload is not a JSON object.")

    version = payload.get("v")
    if version != _TOKEN_VERSION:
        raise InvalidTokenError(f"Unsupported token version: {version!r}")

    tool = payload.get("tool")
    if tool != expected_tool:
        raise InvalidTokenError(f"Token was issued for tool {tool!r}, expected {expected_tool!r}.")

    if "url" not in payload or "offset" not in payload:
        raise InvalidTokenError("Token payload missing 'url' or 'offset'.")

    try:
        payload["offset"] = int(payload["offset"])
    except (TypeError, ValueError) as exc:
        raise InvalidTokenError(f"Token 'offset' is not an integer: {exc}") from exc

    return payload


def format_paged_result(text: str, *, has_more: bool, next_token: str | None) -> str:
    """Wrap page body with a page-info footer.

    The footer is appended after :data:`PAGE_INFO_MARKER` so that
    ``llm._trim_tool_result`` can protect it from truncation. When
    ``has_more`` is False the ``next_token`` line is omitted.

    Args:
      text: The page body text.
      has_more: Whether more content is available.
      next_token: The continuation token to include when ``has_more`` is True.

    Returns:
      The formatted result string with the page-info footer.
    """
    if not has_more:
        # No footer needed when there is nothing more to fetch. Keeping the
        # body unchanged preserves backward compatibility for short results.
        return text

    if not next_token:
        # Defensive: has_more without a token is a programming error; fall back
        # to no footer to avoid confusing the model.
        return text

    footer = f"{PAGE_INFO_MARKER}\nhas_more: true\nnext_token: {next_token}"
    return f"{text}{footer}"


def parse_paged_result(result: str) -> tuple[str, bool, str | None]:
    """Split a paged result into (body, has_more, next_token).

    This is the inverse of :func:`format_paged_result` and is mainly useful in
    tests and for chaining tool results.

    Args:
      result: The formatted result string from :func:`format_paged_result`.

    Returns:
      A tuple of (body_text, has_more, next_token_or_None).
    """
    if PAGE_INFO_MARKER not in result:
        return result, False, None

    body, _, footer = result.partition(PAGE_INFO_MARKER)
    has_more = "has_more: true" in footer
    next_token: str | None = None
    if has_more:
        for line in footer.splitlines():
            line = line.strip()
            if line.startswith("next_token:"):
                next_token = line.split(":", 1)[1].strip()
                break
    return body, has_more, next_token
