"""HTTP routes of the Ask area, served by MedScout's web server (medscout/web/server.py).

  GET  /ask/                      the chat page (shown inside the console's "Ask" tab)
  GET  /ask/static/...            its scripts, styles and bundled libraries
  GET  /api/ask/status            Ollama + model status
  GET  /api/ask/settings          settings (the API key is never sent back)
  POST /api/ask/settings          change settings
  GET  /api/ask/chats             list chats;  GET /api/ask/chats/<id>  one chat
  POST /api/ask/send              ask a question; the answer streams back as one JSON event per line
  POST /api/ask/chats/<id>/stop   stop an answer;  POST .../rename  rename;  DELETE /api/ask/chats/<id>
  POST /api/ask/upload            upload a file (raw body, name in the X-Filename header)
  GET  /api/ask/files/<id>        an uploaded image (others download)
  POST /api/ask/attach-run        attach a MedScout run's brief.md as a file
  POST /api/ask/pull              download a model through Ollama (streams progress)

MedScout's server already refuses cross-origin POSTs and requests addressed to a foreign host.
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote

from .. import user_settings
from ..config import Settings as MedScoutSettings
from ..localdb import DatabaseError
from ..models import shared_router
from .chat import ChatService
from .config import CONTEXT_CHOICES, STATIC_DIR, Settings
from .files import MAX_UPLOAD, FileStore, UploadError
from .medtech import MedTechTools, demo_cases
from .ollama_client import CancelToken, Cancelled, OllamaError
from .store import ChatNotFound, ChatStore

# The page may be framed by the console (same origin) but by nothing else.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")


class AskApp:
    def __init__(self, base: MedScoutSettings, data_dir: Optional[Path] = None):
        self.base = base
        data_dir = Path(data_dir or (base.runs_dir / "ask"))
        self.settings = Settings(data_dir)
        self.store = ChatStore(data_dir)
        self.files = FileStore(data_dir)
        self.files.cleanup_orphans()
        self.medtech = MedTechTools(base)
        self.chats = ChatService(self.settings, self.store, self.files, self.medtech, router=None,
                                 database=lambda: user_settings.open_database(base.runs_dir),
                                 database_notes=lambda: user_settings.load(base.runs_dir)["database_notes"])
        self.chats.router = self.router
        self.pull_lock = threading.Lock()
        self.pull_cancel: Optional[CancelToken] = None

    @property
    def router(self):
        return shared_router(self.base)       # reloads when config/models.json changes

    # ----- data --------------------------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        model = self.settings.get("model")
        out: Dict[str, Any] = {"ollama_url": self.settings.ollama_url(), "model": model,
                               "context_choices": CONTEXT_CHOICES, "demo_cases": demo_cases()}
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
        reg = self.router.reg
        if out["ollama"]:
            reg.refresh_local()
        catalog = []
        for m in reg.all():
            ok, why = reg.available(m)
            catalog.append({"alias": m.alias, "label": m.label, "local": m.local, "tier": m.tier, "id": m.id,
                            "price_in": m.price_in, "price_out": m.price_out, "available": ok, "why": why})
        known = {m.id for m in reg.all()}
        for n in sorted(names - known):
            catalog.append({"alias": n, "label": n, "local": True, "tier": 0, "id": n, "price_in": 0, "price_out": 0,
                            "available": True, "why": ""})
        out["catalog"] = catalog
        if model == "auto":
            cls = reg.get(reg.router_cfg.get("classifier", "qwen-local"))
            out["download_model"] = cls.id
            out["model_installed"] = cls.id in names or (cls.id + ":latest") in names
            out["capabilities"] = ["tools", "vision", "thinking"]
        else:
            try:
                m = reg.get(model)
            except KeyError as exc:
                out["model_error"] = str(exc)
                return out
            out["download_model"] = m.id
            if m.local:
                out["model_installed"] = m.id in names or (m.id + ":latest") in names
                if out["model_installed"]:
                    info = self.chats.capabilities(m.id)
                    out["capabilities"] = info.get("capabilities", [])
                    out["max_context"] = info.get("context_length", 0)
            else:
                out["cloud"] = True
                out["model_installed"] = True
                out["key_missing"] = not reg.available(m)[0]
                out["capabilities"] = [c for c, ok in (("tools", m.tools), ("vision", m.vision),
                                                       ("thinking", m.thinking)) if ok]
        try:
            db = user_settings.open_database(self.base.runs_dir)
            out["database"] = {"connected": db is not None, "name": db.name if db else ""}
            if db:
                db.close()
        except DatabaseError as exc:
            out["database"] = {"connected": False, "error": str(exc)}
        return out

    def attach_run(self, run_id: str) -> Dict[str, Any]:
        """Turn a MedScout run's brief into an uploaded file the chat can read."""
        if not RUN_ID.fullmatch(run_id or ""):
            raise UploadError("Unknown run")
        run_dir = self.base.runs_dir / run_id
        brief = run_dir / "brief.md"
        if not brief.exists():
            raise UploadError("This run has no brief yet (it is written in the last phase).")
        company = ""
        try:
            company = json.loads((run_dir / "state.json").read_text()).get("company", "")
        except (OSError, ValueError):
            pass
        name = "MedScout brief - %s (%s).md" % (company or "run", run_id)
        return self.files.save(name, brief.read_bytes())

    # ----- HTTP ------------------------------------------------------------------------------------
    @staticmethod
    def handles(path: str) -> bool:
        return path in ("/ask", "/ask/") or path.startswith(("/ask/", "/api/ask/"))

    def dispatch(self, req: Any, method: str, path: str) -> None:
        """Answer one request. `req` is MedScout's BaseHTTPRequestHandler."""
        r = _Reply(req)
        try:
            if path == "/ask":
                return r.redirect("/ask/")
            if method == "GET":
                return self._get(r, path)
            if method == "POST":
                return self._post(r, req, path)
            if method == "DELETE":
                m = re.fullmatch(r"/api/ask/chats/([\w-]+)", path)
                if m:
                    self.store.get(m.group(1))
                    self.chats.stop(m.group(1))
                    self.store.delete(m.group(1))
                    self.files.delete_for_chat(m.group(1))
                    return r.json({"deleted": m.group(1)})
            return r.error(404, "Not found")
        except ChatNotFound:
            return r.error(404, "Chat not found")
        except UploadError as exc:
            return r.error(400, str(exc))
        except (ValueError, UnicodeDecodeError) as exc:
            return r.error(400, "Bad request: %s" % exc)
        except (BrokenPipeError, ConnectionResetError):
            return None

    def _get(self, r: "_Reply", path: str) -> None:
        if path == "/ask/":
            return r.static("index.html")
        if path.startswith("/ask/static/"):
            return r.static(unquote(path[len("/ask/static/"):]))
        if path == "/api/ask/status":
            return r.json(self.status())
        if path == "/api/ask/settings":
            return r.json(self.settings.public())
        if path == "/api/ask/chats":
            return r.json({"chats": self.store.list()})
        m = re.fullmatch(r"/api/ask/chats/([\w-]+)", path)
        if m:
            chat = self.store.get(m.group(1))
            chat["busy"] = self.chats.is_busy(chat["id"])
            return r.json(chat)
        m = re.fullmatch(r"/api/ask/files/([a-f0-9]{16})", path)
        if m:
            return r.file(self.files, m.group(1))
        return r.error(404, "Not found")

    def _post(self, r: "_Reply", req: Any, path: str) -> None:
        if path == "/api/ask/send":
            body = r.read_json()
            r.stream_start()
            return self.chats.send(body, r.stream_write)
        if path == "/api/ask/upload":
            length = int(req.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                return r.error(413, "File is too large (max %d MB)" % (MAX_UPLOAD // 1024 // 1024))
            name = unquote(req.headers.get("X-Filename") or "file")
            data = req.rfile.read(length) if length else b""
            return r.json(self.files.save(name, data))
        if path == "/api/ask/attach-run":
            return r.json(self.attach_run(str(r.read_json().get("run_id") or "")))
        if path == "/api/ask/settings":
            self.settings.update(r.read_json())
            return r.json(self.settings.public())
        if path == "/api/ask/pull":
            return self._pull(r)
        if path == "/api/ask/pull/stop":
            if self.pull_cancel:
                self.pull_cancel.cancel()
            return r.json({"ok": True})
        m = re.fullmatch(r"/api/ask/chats/([\w-]+)/(stop|rename)", path)
        if m:
            chat_id, action = m.groups()
            if action == "stop":
                return r.json({"stopped": self.chats.stop(chat_id)})
            title = str(r.read_json().get("title") or "").strip()[:120] or "Chat"
            chat = self.store.update(chat_id, lambda c: c.update(title=title))
            return r.json({"id": chat_id, "title": chat["title"]})
        return r.error(404, "Not found")

    def _pull(self, r: "_Reply") -> None:
        model = str(r.read_json().get("model") or self.settings.get("model")).strip()
        if not re.fullmatch(r"[\w.\-/:]+", model):
            return r.error(400, "Invalid model name")
        if not self.pull_lock.acquire(blocking=False):
            return r.error(409, "A download is already running")
        try:
            self.pull_cancel = CancelToken()
            r.stream_start()
            try:
                for chunk in self.chats.client().pull(model, self.pull_cancel):
                    r.stream_write(chunk)
                r.stream_write({"status": "success", "done": True})
            except Cancelled:
                r.stream_write({"error": "Download stopped"})
            except OllamaError as exc:
                r.stream_write({"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            if self.pull_cancel:
                self.pull_cancel.cancel()
        finally:
            self.pull_cancel = None
            self.pull_lock.release()


class _Reply:
    """Writes responses on MedScout's request handler."""

    def __init__(self, req: Any):
        self.req = req

    def headers(self, status: int, ctype: str, length: Optional[int] = None,
                extra: Optional[Dict[str, str]] = None) -> None:
        q = self.req
        q.send_response(status)
        q.send_header("Content-Type", ctype)
        if length is not None:
            q.send_header("Content-Length", str(length))
        if "Cache-Control" not in (extra or {}):
            q.send_header("Cache-Control", "no-store")
        q.send_header("X-Content-Type-Options", "nosniff")
        q.send_header("Referrer-Policy", "no-referrer")
        q.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            q.send_header(k, v)
        q.end_headers()

    def json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.headers(status, "application/json; charset=utf-8", len(body))
        self.req.wfile.write(body)

    def error(self, status: int, message: str) -> None:
        self.json({"error": message}, status)

    def redirect(self, to: str) -> None:
        self.req.send_response(302)
        self.req.send_header("Location", to)
        self.req.send_header("Content-Length", "0")
        self.req.end_headers()

    def read_json(self) -> Dict[str, Any]:
        length = int(self.req.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            raise ValueError("Request too large")
        raw = self.req.rfile.read(length) if length else b""
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def stream_start(self) -> None:
        self.headers(200, "application/x-ndjson; charset=utf-8", extra={"X-Accel-Buffering": "no"})

    def stream_write(self, event: Dict[str, Any]) -> None:
        self.req.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        self.req.wfile.flush()

    def static(self, rel: str) -> None:
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self.error(404, "Not found")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        body = target.read_bytes()
        cache = {"Cache-Control": "max-age=86400"} if "/vendor/" in target.as_posix() else None
        self.headers(200, ctype, len(body), cache)
        self.req.wfile.write(body)

    def file(self, files: FileStore, file_id: str) -> None:
        try:
            path, meta = files.path(file_id)
            body = path.read_bytes()
        except (UploadError, OSError):
            return self.error(404, "File not found")
        q = self.req
        q.send_response(200)
        if meta.get("kind") == "image":
            q.send_header("Content-Type", meta.get("mime", "image/jpeg"))
            q.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        else:
            name = re.sub(r'[^\w.\- ]+', "_", meta.get("name", "file"))
            q.send_header("Content-Type", "application/octet-stream")
            q.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
        q.send_header("Content-Length", str(len(body)))
        q.send_header("X-Content-Type-Options", "nosniff")
        q.send_header("Cache-Control", "max-age=3600")
        q.end_headers()
        q.wfile.write(body)
