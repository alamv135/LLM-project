"""Saved conversations: one JSON file per chat in data/chats/."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$")


class ChatNotFound(Exception):
    pass


class ChatStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "chats"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, chat_id: str) -> Path:
        if not _ID_RE.match(chat_id or ""):
            raise ChatNotFound(chat_id)
        return self.root / (chat_id + ".json")

    def create(self, title: str = "New chat") -> Dict[str, Any]:
        now = time.time()
        chat_id = time.strftime("%Y%m%d-%H%M%S-", time.localtime(now)) + secrets.token_hex(3)
        chat = {"id": chat_id, "title": title, "created": now, "updated": now, "messages": []}
        self.save(chat)
        return chat

    def get(self, chat_id: str) -> Dict[str, Any]:
        path = self._path(chat_id)
        with self._lock:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise ChatNotFound(chat_id) from None

    def save(self, chat: Dict[str, Any]) -> None:
        path = self._path(chat["id"])
        with self._lock:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)

    def update(self, chat_id: str, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        """Read, change and save a chat in one step (so two threads can't overwrite each other)."""
        with self._lock:
            chat = self.get(chat_id)
            change(chat)
            chat["updated"] = time.time()
            self.save(chat)
            return chat

    def delete(self, chat_id: str) -> None:
        with self._lock:
            try:
                self._path(chat_id).unlink()
            except FileNotFoundError:
                pass

    def list(self) -> List[Dict[str, Any]]:
        out = []
        with self._lock:
            for path in self.root.glob("*.json"):
                try:
                    chat = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                out.append({"id": chat["id"], "title": chat.get("title", "Chat"),
                            "updated": chat.get("updated", 0), "created": chat.get("created", 0)})
        out.sort(key=lambda c: c["updated"], reverse=True)
        return out


def title_from(text: str, limit: int = 48) -> str:
    """A chat title from the first message: its first line, shortened at a word boundary."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "").lstrip("#>*- ").strip()
    if not line:
        return "New chat"
    if len(line) <= limit:
        return line
    cut = line[:limit].rsplit(" ", 1)[0]
    return (cut if len(cut) > limit // 2 else line[:limit]).rstrip(",.;:") + "…"
