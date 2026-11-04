#!/usr/bin/env python3
"""Start Local Chat:  python3 app.py  (then open http://127.0.0.1:8800)

Options:  --port 8800   --model qwen3.5:9b   --open (open the browser)   --verbose (log requests)
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

if sys.version_info < (3, 9):
    sys.exit("Local Chat needs Python 3.9 or newer (you have %s)." % sys.version.split()[0])

from localchat.config import DATA_DIR, Settings  # noqa: E402
from localchat.files import FileStore  # noqa: E402
from localchat.ollama_client import OllamaError  # noqa: E402
from localchat.server import App, make_server  # noqa: E402
from localchat.store import ChatStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Chat for Ollama")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--model", help="model to use, e.g. qwen3.5:9b (saved in settings)")
    parser.add_argument("--open", action="store_true", help="open the page in your browser")
    parser.add_argument("--verbose", action="store_true", help="print every request")
    args = parser.parse_args()

    settings = Settings(DATA_DIR)
    if args.model:
        settings.update({"model": args.model})
    files = FileStore(DATA_DIR)
    files.cleanup_orphans()
    app = App(settings, ChatStore(DATA_DIR), files)
    app.verbose = args.verbose  # type: ignore[attr-defined]
    server, port = make_server(app, port=args.port)
    url = "http://127.0.0.1:%d" % port

    print("Local Chat is running:  %s" % url)
    print("  model:  %s      data folder: %s" % (settings.get("model"), DATA_DIR))
    try:
        client = app.chats.client()
        print("  Ollama %s at %s" % (client.version(), settings.get("ollama_url")))
        names = [m.get("name") for m in client.models()]
        if settings.get("model") not in names:
            print("  ! %s is not downloaded yet. The page has a Download button, or run:  ollama pull %s"
                  % (settings.get("model"), settings.get("model")))
    except OllamaError as exc:
        print("  ! %s" % exc)
    print("Press Ctrl+C to stop.")

    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
