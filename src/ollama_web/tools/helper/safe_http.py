"""Safe outbound HTTP fetching for LLM tools."""

from __future__ import annotations

import ipaddress
import socket

import httpx

from ...config import settings

_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
}


class UnsafeURL(ValueError):
    """Raised when an outbound URL is not allowed."""


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip in _METADATA_IPS:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_http_url(url: str) -> httpx.URL:
    """Return a parsed URL after rejecting private network targets."""
    parsed = httpx.URL(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURL("Only http(s) URLs are allowed.")
    host = parsed.host
    if not host:
        raise UnsafeURL("URL host is required.")
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise UnsafeURL("Localhost URLs are not allowed.")

    try:
        ip = ipaddress.ip_address(host)
        if not _is_public_ip(ip):
            raise UnsafeURL("Private or local IP addresses are not allowed.")
        return parsed
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeURL(f"Could not resolve host: {host}") from exc

    addresses = {item[4][0] for item in infos}
    if not addresses:
        raise UnsafeURL(f"Could not resolve host: {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not _is_public_ip(ip):
            raise UnsafeURL("Resolved private or local IP addresses are not allowed.")
    return parsed


def fetch_public_bytes(
    url: str,
    *,
    max_bytes: int | None = None,
    timeout: float | None = None,
) -> tuple[bytes, str, str]:
    """Fetch bytes from a public http(s) URL with redirect and size checks."""
    limit = max_bytes or settings.max_fetch_mb * 1024 * 1024
    current = validate_public_http_url(url)
    headers = {"User-Agent": "ollama-web/0.1 (+https://github.com/local)"}
    with httpx.Client(timeout=timeout or settings.scrape_timeout, headers=headers) as client:
        for _ in range(6):
            with client.stream("GET", current, follow_redirects=False) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise UnsafeURL("Redirect without Location header.")
                    current = validate_public_http_url(str(current.join(location)))
                    continue
                resp.raise_for_status()
                declared = resp.headers.get("content-length")
                if declared and int(declared) > limit:
                    raise UnsafeURL("Remote response exceeds the configured size limit.")
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise UnsafeURL("Remote response exceeds the configured size limit.")
                    chunks.append(chunk)
                content_type = resp.headers.get("content-type", "")
                return b"".join(chunks), str(resp.url), content_type
        raise UnsafeURL("Too many redirects.")
