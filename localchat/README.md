# Local Chat

A private, Claude-style chat page for your Mac. The AI model (**qwen3.5:9b**) runs on your own
computer through [Ollama](https://ollama.com). It can also **search the web** and **read files you
upload** (PDF, Word, Excel, PowerPoint, text, code and images).

- Answers stream in word by word, with formatted text, tables, code (with a Copy button) and math.
- **Web search**: the model decides when to search, reads the best pages, and cites them as [1] [2].
  Click a number to open the source.
- **Files**: attach them with the paperclip, by dragging them onto the page, or by pasting a
  screenshot. Long documents are cut to the parts that matter, and the model can search inside them.
- **Think**: optional step-by-step reasoning before the answer. Slower, but better for hard questions.
- Chats are saved and listed on the left (search with ⌘K, new chat with ⌘⇧O). **Stop** (Esc),
  **Retry** and **Copy** work like in Claude.
- Light and dark mode. No account, no cloud, and nothing to pay.

---

## Setup (one time, about 15 minutes, mostly the model download)

**1. Install Ollama.** Download it from <https://ollama.com/download>, drag it into Applications and
open it once. A llama icon appears in the menu bar. You need a recent version, because qwen3.5 is new.

**2. Get this folder** onto your Mac, for example:

```bash
git clone https://github.com/alamv135/LLM-project.git
```

**3. Start it.** In Finder, open `LLM-project/localchat` and double-click **`Start Local Chat.command`**.

- If macOS says it *"cannot be opened because it is from an unidentified developer"*: right-click
  the file, choose **Open**, then click **Open** again. macOS only asks this once.
- If a window asks you to install *command line developer tools*, click **Install**. This gives you
  Python. Then double-click the file again.

You can also start it from Terminal instead:

```bash
cd LLM-project/localchat
```

```bash
./start.sh
```

The first start downloads qwen3.5:9b (about 6.6 GB) and two small Python packages. After that your
browser opens **http://127.0.0.1:8800**.

**Every day after that:** double-click `Start Local Chat.command` again. Keep its Terminal window
open while you chat. To stop, close that window or press Ctrl+C in it.

**Memory:** qwen3.5:9b needs about 7 GB of RAM, so a Mac with 16 GB or more works best. On an 8 GB
Mac, use a smaller model: type its name in **Settings → Model name** (for example `qwen3.5:4b`), and
the page offers to download it. Every downloaded model also appears in the menu at the top of the page.

---

## Using it

| You want to... | Do this |
|---|---|
| Ask about something current (news, prices, versions) | Keep **Web search** on (the globe button). The model searches when it needs to. You can also say "search for ...". |
| Ask about a web page | Paste its link into your message. |
| Ask about a document | Click the paperclip (or drag the file in) and ask your question. |
| Ask about a picture or screenshot | Attach it or paste it (⌘V). qwen3.5 can see images. |
| Get a more careful answer | Turn on **Think** (the light-bulb button). |
| Stop a long answer | Click the square button or press Esc. |
| Get a different answer | Click **Retry** (the circular arrow under the last answer). |
| Paste a very long text | It is attached as a file automatically. |

**Files it can read:** `.pdf` (text PDFs; for scanned PDFs, upload screenshots of the pages instead),
`.docx`, `.xlsx`, `.pptx`, `.ipynb`, `.txt`, `.md`, `.csv`, `.json`, code files (`.py`, `.js`, `.java`,
...), on a Mac also `.doc`, `.rtf`, `.odt`, and images (`.png`, `.jpg`, `.webp`, `.heic` iPhone photos,
...). The limit is 50 MB per file.

### Web search

The model gets three tools and decides itself when to use them:

| Tool | What it does |
|---|---|
| `web_search` | Searches the web and gets back numbered results (title, link, snippet). |
| `fetch_url` | Reads a page (or an online PDF) in full. |
| `search_files` | Searches inside your attached files. Only offered when a file is too long to show the model whole. |

By default the search uses **DuckDuckGo**, which needs no account. DuckDuckGo sometimes blocks
automated searches for a while ("captcha"). For more reliable search, make a free account at
ollama.com, create a key at <https://ollama.com/settings/keys>, and paste it into
**Settings → Web search**. The app then uses Ollama's web search API automatically.

### Settings (the gear button, bottom left)

| Setting | Meaning |
|---|---|
| Context window | How much text the model can look at at once: your chat, files and web pages. **32K tokens** (about 25,000 words) is a good start on a 16 GB Mac. Bigger needs more memory and is slower. When a chat gets too long, the oldest messages are left out, and the page tells you. |
| Temperature | Leave it empty to use the model's recommended value. |
| Custom instructions | Added to every chat, for example "I'm a university student, explain simply". |
| Most search / read steps per answer | A safety limit so the model can't search forever (default 6). |

---

## What stays private

- The model runs **on your Mac**. Your questions and files go to the model on your computer and
  nowhere else.
- Chats, uploaded files and settings are saved in `localchat/data/`. Git ignores this folder, so it is
  never uploaded. Deleting a chat also deletes its files.
- Only two things leave your Mac: **search queries** (sent to DuckDuckGo, or to Ollama if you added a
  key) and **the web pages the model chooses to read**. Turn off Web search and nothing goes out.
- The page is only reachable from your own Mac (address 127.0.0.1). Other websites can't control it.
  The model can't use web reading to look into your Mac or home network (addresses like `localhost`
  or `192.168.x.x` are blocked).

---

## If something goes wrong

| Problem | Fix |
|---|---|
| Red banner "Can't reach Ollama" | Open the Ollama app (llama icon in the menu bar), then click **Try again**. |
| Banner "... is not downloaded yet" | Click **Download**, or run `ollama pull qwen3.5:9b` in Terminal. |
| "model ... not found" or a strange error from Ollama | Update Ollama (menu-bar icon → *Check for updates*). qwen3.5 needs a recent version. |
| Search fails with a "captcha" message | Wait a few minutes, or add an Ollama API key (see *Web search* above). |
| Answers are very slow | Turn off **Think**, lower the context window to 16K, and close other big apps. The first answer after a while is slower because the model loads into memory. |
| "Reading PDFs needs the pypdf package" | Start the app with `start.sh` or `Start Local Chat.command`. They install pypdf for you. |
| Certificate errors when searching | Same fix: start with `start.sh` (it installs `certifi`). |
| Port 8800 is busy | The app picks the next free port (8801, ...) and prints the address. |

---

## How it works (for learning)

```
 Browser page (static/)            Python server (localchat/)                  Ollama (port 11434)
 ──────────────────────            ──────────────────────────                  ───────────────────
 you type a question  ──POST /api/send──▶ chat.py builds the prompt  ──/api/chat──▶ qwen3.5:9b
                                          (system prompt + history                  │
                                           + files, trimmed to fit)                 │ streams words,
 words appear live    ◀──one JSON line────  passes each piece on   ◀────────────────┘ or asks for a tool
                         per piece          │
                                            ├─ tool "web_search" → web.py → DuckDuckGo / Ollama search
                                            ├─ tool "fetch_url"  → web.py → downloads + cleans the page
                                            └─ tool "search_files" → files.py → keyword search (BM25)
                                            then gives the result back to the model, which continues
 chat saved           ◀──────────────────  store.py writes data/chats/<id>.json
```

| File | What it does |
|---|---|
| `app.py` | Starts everything. `python3 app.py --port 8800 --open` |
| `localchat/server.py` | The web server: page files and the `/api/...` endpoints, plus the safety checks. |
| `localchat/chat.py` | **The core.** Builds the prompt, fits it into the context window, runs the model ↔ tool loop, saves the answer. |
| `localchat/ollama_client.py` | Talks to Ollama: streaming chat, model list, model abilities (tools / vision / thinking), downloads. |
| `localchat/web.py` | Web search (DuckDuckGo or Ollama) and reading web pages (removes menus, ads and scripts). |
| `localchat/files.py` | Uploads: text extraction for each file type, image preparation, splitting long texts into pieces and BM25 keyword ranking. |
| `localchat/store.py` | Saves chats as JSON files. |
| `localchat/config.py` | Settings and their defaults. |
| `localchat/static/` | The page: `index.html`, `app.css` (look), `app.js` (behaviour), `markdown.js` (formatting). `vendor/` holds open-source libraries (marked, DOMPurify, KaTeX, highlight.js) so the page also works offline. |
| `tests/` | Automated tests, plus `fake_ollama.py`, a pretend Ollama used by the tests. |
| `start.sh`, `Start Local Chat.command` | The one-click starters for the Mac. |

The server uses only Python's standard library, so there is no web framework to install. The
optional packages are `pypdf` (reads PDFs) and `certifi` (HTTPS certificates on some Mac Pythons).

### Run the tests

```bash
python3 -m unittest discover -s tests -t .
```

33 tests. They use the fake Ollama in `tests/fake_ollama.py`, so they need no model and no internet.

---

## Status: what has been tested and what has not

- **Tested:** all 33 automated tests pass on Python 3.9 and 3.13. The page was also clicked through
  in a real browser (Chromium, desktop and phone width, light and dark) against a fake Ollama: chat,
  search with citations, reading a page, thinking, files and images, Stop, Retry, reload, settings,
  and the model download.
- **Not tested yet:** the real qwen3.5:9b model, live DuckDuckGo results (both were unreachable from
  the build machine), Safari, and the Mac-only helpers (`sips` for HEIC photos, `textutil` for
  `.doc`/`.rtf`). Try those first. If something breaks, the message in the red box or in the Terminal
  window says what went wrong.
