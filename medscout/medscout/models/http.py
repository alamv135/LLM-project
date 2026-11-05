"""Small HTTP helpers for the model providers (standard library only).

http.client is used directly so a running answer can be stopped by closing its socket, and so an
HTTPS proxy (HTTPS_PROXY, honouring NO_PROXY) still works.
"""

from __future__ import annotations

import http.client
import json
import ssl
import urllib.parse
import urllib.request
from typing import Any


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def normalize_url(raw: str, default_port: int) -> str:
    raw = raw.strip().rstrip("/")
    if "://" not in raw:
        raw = "http://" + raw
    u = urllib.parse.urlparse(raw)
    host = u.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    port = u.port or (443 if u.scheme == "https" else default_port)
    return f"{u.scheme}://{host}:{port}{u.path.rstrip('/')}"


def connect(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    """Open a connection for `url`; returns (connection, path with query)."""
    u = urllib.parse.urlparse(url)
    https = u.scheme == "https"
    port = u.port or (443 if https else 80)
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    proxy = urllib.request.getproxies().get("https" if https else "http")
    if proxy and not urllib.request.proxy_bypass(u.hostname or ""):
        p = urllib.parse.urlparse(proxy if "://" in proxy else "http://" + proxy)
        conn: http.client.HTTPConnection
        if https:
            conn = http.client.HTTPSConnection(p.hostname, p.port or 80, timeout=timeout, context=ssl_context())
            conn.set_tunnel(u.hostname, port)
        else:
            conn = http.client.HTTPConnection(p.hostname, p.port or 80, timeout=timeout)
            path = url
        return conn, path
    if https:
        return http.client.HTTPSConnection(u.hostname, port, timeout=timeout, context=ssl_context()), path
    return http.client.HTTPConnection(u.hostname, port, timeout=timeout), path


def request(url: str, method: str = "GET", body: Any = None, headers: dict[str, str] | None = None,
            timeout: float = 60.0) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    conn, path = connect(url, timeout)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json", **(headers or {})} if data is not None else dict(headers or {})
    conn.request(method, path, body=data, headers=hdrs)
    return conn, conn.getresponse()


def get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 15.0) -> Any:
    conn, resp = request(url, "GET", headers=headers, timeout=timeout)
    try:
        raw = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}: {raw[:200].decode(errors='replace')}")
        return json.loads(raw)
    finally:
        conn.close()
