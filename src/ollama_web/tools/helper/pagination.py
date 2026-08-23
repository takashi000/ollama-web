"""Lossless pagination helpers for tool results."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_TOKEN_VERSION = 2
_SUPPORTED_TOKEN_VERSIONS = {1, 2}
MAX_TOKEN_CHARS = 8192

PAGE_INFO_MARKER = "\n---page-info---"


class InvalidTokenError(ValueError):
    """Raised when a continuation token is malformed or invalid."""


class PageSizeError(ValueError):
    """Raised when page metadata alone cannot fit in the result budget."""


@dataclass(frozen=True)
class PageResult:
    """One lossless source slice and the cursor immediately after that slice."""

    text: str
    start_cursor: dict[str, int]
    next_cursor: dict[str, int]
    has_more: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + padding)


def make_token(tool: str, url: str, offset: int, **extra: Any) -> str:
    """Create a version-2 opaque continuation token."""
    payload: dict[str, Any] = {
        "v": _TOKEN_VERSION,
        "tool": tool,
        "url": url,
        "offset": int(offset),
    }
    payload.update(extra)
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    token = _b64url_encode(raw)
    if len(token) > MAX_TOKEN_CHARS:
        raise PageSizeError(f"Continuation token exceeds {MAX_TOKEN_CHARS} characters.")
    return token


def parse_token(token: str | None, expected_tool: str) -> dict[str, Any]:
    """Validate and decode a version-1 or version-2 continuation token."""
    if not token or not token.strip():
        raise InvalidTokenError("Empty continuation token.")
    token = token.strip()
    if len(token) > MAX_TOKEN_CHARS:
        raise InvalidTokenError(f"Continuation token exceeds {MAX_TOKEN_CHARS} characters.")

    try:
        raw = _b64url_decode(token)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise InvalidTokenError(f"Token is not valid base64url: {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidTokenError(f"Token payload is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise InvalidTokenError("Token payload is not a JSON object.")
    if payload.get("v") not in _SUPPORTED_TOKEN_VERSIONS:
        raise InvalidTokenError(f"Unsupported token version: {payload.get('v')!r}")
    if payload.get("tool") != expected_tool:
        raise InvalidTokenError(
            f"Token was issued for tool {payload.get('tool')!r}, expected {expected_tool!r}."
        )
    if "url" not in payload or "offset" not in payload:
        raise InvalidTokenError("Token payload missing 'url' or 'offset'.")
    if not isinstance(payload["url"], str) or not payload["url"]:
        raise InvalidTokenError("Token 'url' is not a non-empty string.")
    try:
        payload["offset"] = int(payload["offset"])
    except (TypeError, ValueError) as exc:
        raise InvalidTokenError(f"Token 'offset' is not an integer: {exc}") from exc
    if payload["offset"] < 0:
        raise InvalidTokenError("Token 'offset' must not be negative.")
    return payload


def format_paged_result(text: str, *, has_more: bool, next_token: str | None) -> str:
    """Append the protected continuation footer when unread content remains."""
    if not has_more:
        return text
    if not next_token:
        raise PageSizeError("A continuation token is required when has_more is true.")
    return f"{text}{PAGE_INFO_MARKER}\nhas_more: true\nnext_token: {next_token}"


def format_page_to_limit(
    page_factory: Callable[[int], PageResult],
    token_factory: Callable[[dict[str, int]], str],
    *,
    requested_chars: int,
    result_limit: int,
    prefix: str = "",
    suffix: str = "",
) -> str:
    """Render the largest lossless page whose complete envelope fits the limit."""
    body_budget = max(1, min(requested_chars, result_limit))
    for _ in range(8):
        page = page_factory(body_budget)
        warning_text = "".join(f"[warning] {warning}\n" for warning in page.warnings)
        body = f"{prefix}{warning_text}{page.text}{suffix}"
        token = token_factory(page.next_cursor) if page.has_more else None
        rendered = format_paged_result(body, has_more=page.has_more, next_token=token)
        if len(rendered) <= result_limit:
            return rendered
        overflow = len(rendered) - result_limit
        new_budget = body_budget - overflow - 16
        if new_budget >= body_budget:
            new_budget = body_budget - 1
        if new_budget <= 0:
            break
        body_budget = new_budget
    raise PageSizeError("Paged result metadata exceeds the configured result limit.")


def text_page(text: str, offset: int, max_chars: int) -> PageResult:
    """Return a lossless character-offset page from ``text``."""
    if offset < 0 or offset > len(text):
        raise ValueError("Text pagination offset is out of range.")
    end = min(offset + max_chars, len(text))
    return PageResult(
        text=text[offset:end],
        start_cursor={"offset": offset},
        next_cursor={"offset": end},
        has_more=end < len(text),
    )


def parse_paged_result(result: str) -> tuple[str, bool, str | None]:
    """Split a formatted result into body, has-more flag, and token."""
    if PAGE_INFO_MARKER not in result:
        return result, False, None
    body, _, footer = result.partition(PAGE_INFO_MARKER)
    has_more = "has_more: true" in footer
    next_token: str | None = None
    if has_more:
        for line in footer.splitlines():
            if line.strip().startswith("next_token:"):
                next_token = line.split(":", 1)[1].strip()
                break
    return body, has_more, next_token
