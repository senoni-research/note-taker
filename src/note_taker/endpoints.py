"""Loopback-only endpoint validation."""

from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def assert_loopback(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            f"Refusing non-loopback endpoint {url!r}. "
            "Only 127.0.0.1 / localhost / ::1 are allowed by default."
        )
    return url
