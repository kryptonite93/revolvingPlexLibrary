from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"(?:api[-_]?key|token|secret|password|passwd|cookie|authorization|passkey)",
    re.IGNORECASE,
)
_INLINE_SECRET = re.compile(
    r"(?i)(api[-_]?key|token|secret|password|passwd|passkey)\s*([:=])\s*([^\s,&;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value

    try:
        hostname = parts.hostname or ""
        parsed_port = parts.port
    except ValueError:
        return REDACTED
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed_port}" if parsed_port else ""
    netloc = f"{hostname}{port}"

    query = [
        (key, REDACTED if _SECRET_KEY.search(key) else item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))


def _redact_string(value: str) -> str:
    sanitized = _redact_url(value)
    sanitized = _BEARER.sub(f"Bearer {REDACTED}", sanitized)
    return _INLINE_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        sanitized,
    )


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value
