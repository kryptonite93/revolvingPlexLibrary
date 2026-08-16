from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_base_url(value: str) -> str:
    candidate = value.strip()
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Enter a complete http:// or https:// URL.")
    if parts.username or parts.password:
        raise ValueError("Do not include credentials in the URL.")
    if parts.query or parts.fragment:
        raise ValueError("The base URL cannot contain a query or fragment.")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def url_from_arr_settings(settings: dict[str, object]) -> str:
    hostname = str(settings.get("hostname") or "").strip()
    if not hostname:
        raise ValueError("Discovered server has no hostname")
    if hostname.startswith(("http://", "https://")):
        return normalize_base_url(hostname)
    scheme = "https" if settings.get("useSsl") else "http"
    port = settings.get("port")
    base_path = str(settings.get("baseUrl") or "").strip("/")
    address = f"{scheme}://{hostname}"
    if port:
        address += f":{port}"
    if base_path:
        address += f"/{base_path}"
    return normalize_base_url(address)
