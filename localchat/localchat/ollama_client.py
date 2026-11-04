"""A small client for the Ollama HTTP API (https://github.com/ollama/ollama/blob/main/docs/api.md).

Uses http.client directly (not urllib) so that
  * a proxy configured for the internet is never used for the local Ollama, and
  * a running generation can be stopped at once by closing its socket.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse


class OllamaError(Exception):
    """Something went wrong talking to Ollama. The message is meant for the user."""


class Cancelled(Exception):
    """The user pressed Stop."""


class CancelToken:
    """Shared between the request thread and the Stop button."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self._conn: Optional[http.client.HTTPConnection] = None
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def attach(self, conn: Optional[http.client.HTTPConnection]) -> None:
        with self._lock:
            self._conn = conn

    def cancel(self) -> None:
        self.event.set()
        with self._lock:
            conn = self._conn
        if conn is not None and conn.sock is not None:
            try:  # wakes up a thread blocked reading from Ollama; Ollama then stops generating
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 600.0):
        u = urlparse(base_url)
        self.base_url = base_url
        self.https = u.scheme == "https"
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or (443 if self.https else 11434)
        self.timeout = timeout
        self._show_cache: Dict[str, Dict[str, Any]] = {}

    # ----- plumbing -------------------------------------------------------------------------
    def _connect(self, timeout: float) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=timeout)

    def _open(self, method: str, path: str, body: Optional[dict], timeout: float):
        conn = self._connect(timeout)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
        except (ConnectionRefusedError, socket.gaierror) as exc:
            conn.close()
            raise OllamaError(
                "Can't reach Ollama at %s. Is the Ollama app running? (%s)" % (self.base_url, exc)
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise OllamaError("Ollama connection failed: %s" % exc) from exc
        if resp.status >= 400:
            text = resp.read().decode("utf-8", "replace")
            conn.close()
            raise OllamaError(_explain_error(resp.status, text))
        return conn, resp

    def _json(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 10.0) -> Any:
        conn, resp = self._open(method, path, body, timeout)
        try:
            return json.loads(resp.read().decode("utf-8") or "{}")
        except ValueError as exc:
            raise OllamaError("Ollama sent an answer that is not JSON") from exc
        finally:
            conn.close()

    # ----- API ------------------------------------------------------------------------------
    def version(self) -> str:
        return str(self._json("GET", "/api/version", timeout=3.0).get("version", "?"))

    def models(self) -> List[Dict[str, Any]]:
        """Installed models: [{name, size, details: {parameter_size, family, ...}}]."""
        data = self._json("GET", "/api/tags", timeout=5.0)
        models = data.get("models") or []
        return sorted(models, key=lambda m: m.get("name", ""))

    def show(self, model: str) -> Dict[str, Any]:
        """Model facts we need: capabilities (tools / vision / thinking) and maximum context."""
        if model in self._show_cache:
            return self._show_cache[model]
        data = self._json("POST", "/api/show", {"model": model}, timeout=15.0)
        info = data.get("model_info") or {}
        max_ctx = 0
        for key, value in info.items():
            if key.endswith(".context_length") and isinstance(value, int):
                max_ctx = value
        result = {
            "capabilities": list(data.get("capabilities") or []),
            "context_length": max_ctx,
            "family": (data.get("details") or {}).get("family", ""),
            "parameter_size": (data.get("details") or {}).get("parameter_size", ""),
        }
        self._show_cache[model] = result
        return result

    def stream(self, path: str, body: dict, cancel: Optional[CancelToken] = None) -> Iterator[Dict[str, Any]]:
        """POST and yield each JSON line of Ollama's streamed answer."""
        if cancel is not None and cancel.cancelled:
            raise Cancelled()
        conn, resp = self._open("POST", path, body, self.timeout)
        if cancel is not None:
            cancel.attach(conn)
        try:
            while True:
                try:
                    line = resp.readline()
                except (OSError, http.client.HTTPException) as exc:
                    if cancel is not None and cancel.cancelled:
                        raise Cancelled() from exc
                    raise OllamaError("Lost the connection to Ollama: %s" % exc) from exc
                if cancel is not None and cancel.cancelled:
                    raise Cancelled()
                if not line:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    raise OllamaError(_explain_error(500, json.dumps(chunk)))
                yield chunk
        finally:
            if cancel is not None:
                cancel.attach(None)
            conn.close()

    def chat(self, body: dict, cancel: Optional[CancelToken] = None) -> Iterator[Dict[str, Any]]:
        body = dict(body, stream=True)
        return self.stream("/api/chat", body, cancel)

    def pull(self, model: str, cancel: Optional[CancelToken] = None) -> Iterator[Dict[str, Any]]:
        return self.stream("/api/pull", {"model": model, "stream": True}, cancel)


def _explain_error(status: int, text: str) -> str:
    """Turn Ollama's error into a sentence that says what to do."""
    try:
        message = json.loads(text).get("error") or text
    except ValueError:
        message = text
    message = str(message).strip() or ("HTTP %d" % status)
    low = message.lower()
    if "not found" in low and "model" in low:
        return message + ". Download it with the button above, or in Terminal: ollama pull <model>"
    if "does not support tools" in low:
        return message + ". Turn off web search for this model."
    if "does not support thinking" in low:
        return message + ". Turn off Think for this model."
    if "out of memory" in low or "insufficient memory" in low or "requires more system memory" in low:
        return message + ". Try a smaller context window in Settings, or close other apps."
    return "Ollama error: " + message
