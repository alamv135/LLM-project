"""The local web server: serves the page and a small JSON API. Python standard library only.

Security: it listens on 127.0.0.1 (this Mac only), accepts only requests addressed to
127.0.0.1/localhost (blocks "DNS rebinding"), and refuses POST/DELETE requests coming from other
websites (blocks "cross-site request forgery").
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote

from . import __version__
from .chat import ChatService
from .config import CONTEXT_CHOICES, STATIC_DIR, Settings
from .files import MAX_UPLOAD, FileStore, UploadError
from .ollama_client import CancelToken, Cancelled, OllamaError
from .store import ChatNotFound, ChatStore

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class App:
    """Everything the request handlers need."""

    def __init__(self, settings: Settings, store: ChatStore, files: FileStore):
        self.settings = settings
        self.store = store
        self.files = files
        self.chats = ChatService(settings, store, files)
        self.pull_lock = threading.Lock()
        self.pull_cancel: Optional[CancelToken] = None
        self.port = 0

    def status(self) -> Dict[str, Any]:
        model = self.settings.get("model")
        out: Dict[str, Any] = {"app_version": __version__, "ollama_url": self.settings.get("ollama_url"),
                               "model": model, "context_choices": CONTEXT_CHOICES}
        client = self.chats.client()
        try:
            out["ollama_version"] = client.version()
            out["ollama"] = True
            out["models"] = [{"name": m.get("name"), "size": m.get("size"),
                              "parameter_size": (m.get("details") or {}).get("parameter_size", "")}
                             for m in client.models()]
        except OllamaError as exc:
            out["ollama"] = False
            out["ollama_error"] = str(exc)
            out["models"] = []
        names = {m["name"] for m in out["models"]}
        out["model_installed"] = model in names or (model + ":latest") in names
        if out["model_installed"]:
            info = self.chats.capabilities(model)
            out["capabilities"] = info.get("capabilities", [])
            out["max_context"] = info.get("context_length", 0)
        return out


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalChat"
    protocol_version = "HTTP/1.0"     # one request per connection; streamed answers end when it closes
    app: App = None  # type: ignore[assignment]  # set by make_server

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter terminal
        if self.app and getattr(self.app, "verbose", False):
            super().log_message(fmt, *args)

    # ----- helpers ---------------------------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in {"127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port}

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True               # not sent by a browser page (e.g. curl)
        port = self.server.server_address[1]
        return origin.lower() in {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port,
                                  "http://[::1]:%d" % port}

    def _headers(self, status: int, ctype: str, length: Optional[int] = None,
                 extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _read_body(self, limit: int = 2_000_000) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("Request too large")
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> Dict[str, Any]:
        raw = self._read_body()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def _stream_start(self) -> None:
        self._headers(200, "application/x-ndjson; charset=utf-8", extra={"X-Accel-Buffering": "no"})

    def _stream_write(self, event: Dict[str, Any]) -> None:
        self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    # ----- routing ---------------------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(403, "Use http://127.0.0.1:%d" % self.server.server_address[1])
        path = self.path.split("?", 1)[0]
        try:
            if path == "/" or path == "/index.html":
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(unquote(path[len("/static/"):]))
            if path == "/api/status":
                return self._json(self.app.status())
            if path == "/api/settings":
                return self._json(self.app.settings.public())
            if path == "/api/chats":
                return self._json({"chats": self.app.store.list()})
            m = re.match(r"^/api/chats/([\w-]+)$", path)
            if m:
                chat = self.app.store.get(m.group(1))
                chat["busy"] = self.app.chats.is_busy(chat["id"])
                return self._json(chat)
            m = re.match(r"^/api/files/([a-f0-9]{16})$", path)
            if m:
                return self._file(m.group(1))
            return self._error(404, "Not found")
        except ChatNotFound:
            return self._error(404, "Chat not found")
        except (BrokenPipeError, ConnectionResetError):
            return None

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok() or not self._origin_ok():
            return self._error(403, "Forbidden")
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/send":
                return self._send()
            if path == "/api/upload":
                return self._upload()
            if path == "/api/settings":
                self.app.settings.update(self._read_json())
                return self._json(self.app.settings.public())
            if path == "/api/pull":
                return self._pull()
            if path == "/api/pull/stop":
                if self.app.pull_cancel:
                    self.app.pull_cancel.cancel()
                return self._json({"ok": True})
            m = re.match(r"^/api/chats/([\w-]+)/(stop|rename)$", path)
            if m:
                chat_id, action = m.groups()
                if action == "stop":
                    return self._json({"stopped": self.app.chats.stop(chat_id)})
                title = str(self._read_json().get("title") or "").strip()[:120] or "Chat"
                chat = self.app.store.update(chat_id, lambda c: c.update(title=title))
                return self._json({"id": chat_id, "title": chat["title"]})
            return self._error(404, "Not found")
        except ChatNotFound:
            return self._error(404, "Chat not found")
        except (ValueError, UnicodeDecodeError) as exc:
            return self._error(400, "Bad request: %s" % exc)
        except (BrokenPipeError, ConnectionResetError):
            return None

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._host_ok() or not self._origin_ok():
            return self._error(403, "Forbidden")
        m = re.match(r"^/api/chats/([\w-]+)$", self.path.split("?", 1)[0])
        if not m:
            return self._error(404, "Not found")
        chat_id = m.group(1)
        try:
            self.app.store.get(chat_id)
        except ChatNotFound:
            return self._error(404, "Chat not found")
        self.app.chats.stop(chat_id)
        self.app.store.delete(chat_id)
        self.app.files.delete_for_chat(chat_id)
        return self._json({"deleted": chat_id})

    # ----- handlers --------------------------------------------------------------------------------
    def _static(self, rel: str) -> None:
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._error(404, "Not found")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        body = target.read_bytes()
        cache = {"Cache-Control": "max-age=86400"} if "/vendor/" in str(target) else None
        self._headers(200, ctype, len(body), cache)
        self.wfile.write(body)

    def _file(self, file_id: str) -> None:
        try:
            path, meta = self.app.files.path(file_id)
            body = path.read_bytes()
        except (UploadError, OSError):
            return self._error(404, "File not found")
        if meta.get("kind") == "image":
            ctype = meta.get("mime", "image/jpeg")
            extra = {"Content-Security-Policy": "default-src 'none'; sandbox"}
        else:
            ctype = "application/octet-stream"
            name = re.sub(r'[^\w.\- ]+', "_", meta.get("name", "file"))
            extra = {"Content-Disposition": 'attachment; filename="%s"' % name}
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "max-age=3600")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _upload(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return self._error(413, "File is too large (max %d MB)" % (MAX_UPLOAD // 1024 // 1024))
        name = unquote(self.headers.get("X-Filename") or "file")
        data = self.rfile.read(length) if length else b""
        try:
            meta = self.app.files.save(name, data)
        except UploadError as exc:
            return self._error(400, str(exc))
        return self._json(meta)

    def _send(self) -> None:
        req = self._read_json()
        self._stream_start()
        self.app.chats.send(req, self._stream_write)

    def _pull(self) -> None:
        model = str(self._read_json().get("model") or self.app.settings.get("model")).strip()
        if not re.match(r"^[\w.\-/:]+$", model):
            return self._error(400, "Invalid model name")
        if not self.app.pull_lock.acquire(blocking=False):
            return self._error(409, "A download is already running")
        try:
            self.app.pull_cancel = CancelToken()
            self._stream_start()
            try:
                for chunk in self.app.chats.client().pull(model, self.app.pull_cancel):
                    self._stream_write(chunk)
                self._stream_write({"status": "success", "done": True})
            except Cancelled:
                self._stream_write({"error": "Download stopped"})
            except OllamaError as exc:
                self._stream_write({"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            if self.app.pull_cancel:
                self.app.pull_cancel.cancel()
        finally:
            self.app.pull_cancel = None
            self.app.pull_lock.release()


def make_server(app: App, host: str = "127.0.0.1", port: int = 8800, tries: int = 20) -> Tuple[ThreadingHTTPServer, int]:
    """Start listening on the first free port from `port` upwards."""
    handler = type("BoundHandler", (Handler,), {"app": app})
    last: Optional[OSError] = None
    for p in range(port, port + tries):
        try:
            server = ThreadingHTTPServer((host, p), handler)
            server.daemon_threads = True
            app.port = server.server_address[1]
            return server, app.port
        except OSError as exc:
            last = exc
            if port == 0:
                break
    raise OSError("No free port from %d to %d: %s" % (port, port + tries - 1, last))
