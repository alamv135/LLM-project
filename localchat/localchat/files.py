"""Uploaded files: store them and turn them into text (or images) the model can read.

Supported
  text and code (.txt .md .py .csv .json ...), PDF (needs the pypdf package), Word .docx,
  Excel .xlsx, PowerPoint .pptx, Jupyter .ipynb, and on a Mac also .doc .rtf .odt (macOS textutil).
  Images (.png .jpg .webp .heic ...) are passed to the model as pictures if it supports vision
  (qwen3.5 does). On a Mac, big or HEIC images are converted to a smaller JPEG with `sips`.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

MAX_UPLOAD = 50 * 1024 * 1024        # 50 MB per file
MAX_TEXT = 3_000_000                 # keep at most this many characters of a document
IMAGE_MAX_SIDE = 1600                # pixels; bigger pictures are shrunk (Mac only)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".webp": "image/webp"}
TEXTUTIL_EXT = {".doc", ".rtf", ".odt", ".rtfd", ".wordml", ".webarchive"}
_ID_RE = re.compile(r"^[a-f0-9]{16}$")
logging.getLogger("pypdf").setLevel(logging.ERROR)   # broken PDFs: we report it ourselves


class UploadError(Exception):
    pass


# ----- text extraction -------------------------------------------------------------------------
def decode_text(data: bytes) -> Optional[str]:
    """Bytes -> str, or None if it looks like a binary file."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Old Chinese files: Big5 (Hong Kong / Taiwan) or GB18030 (mainland). Both can "succeed" on the
    # same bytes, so keep the one with the most of the commonest Chinese characters.
    best, best_score = None, -1
    for enc in ("big5", "gb18030"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        score = sum(1 for c in text if c in _COMMON_HANZI)
        if score > best_score:
            best, best_score = text, score
    return best if best is not None else data.decode("latin-1")


_COMMON_HANZI = set(
    "的一是不了在人有我他这這个個们們中来來上大为為和国國地到以说說时時要就出会會可也你对對生能而子那得"
    "于於着著下自之年过過发發后後作里裡用道行所然家种種事成方多经經么麼去法学學如都同现現当當没沒动動面"
    "起看定天分还還进進好小部其些主样樣理心她本前开開但因只从從想实實日电電機机問问題题請请謝谢們資资料"
)


def pdf_to_text(data: bytes) -> Tuple[str, str]:
    """Returns (text, note). Each page starts with a [Page n] marker."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        raise UploadError("Reading PDFs needs the pypdf package. Run start.sh once, or: "
                          "pip install pypdf") from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                raise UploadError("This PDF is password protected") from None
        pages = []
        for i, page in enumerate(reader.pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one broken page should not lose the rest
                text = ""
            pages.append("[Page %d]\n%s" % (i, text.strip()))
    except UploadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UploadError("Could not read this PDF: %s" % exc) from exc
    text = "\n\n".join(pages)
    if len(re.sub(r"\[Page \d+\]|\s", "", text)) < 20 * max(1, len(pages)):   # < 20 letters a page
        return text, ("Almost no text found: this looks like a scanned PDF. Tip: take screenshots of the "
                      "pages and upload those instead - the model can read images.")
    return text, "%d pages" % len(pages)


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    info = zf.getinfo(name)
    if info.file_size > 100 * 1024 * 1024:
        raise UploadError("This document is too large to read")
    return ET.fromstring(zf.read(name))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def docx_to_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = _xml(zf, "word/document.xml")
    out: List[str] = []

    def para_text(p: ET.Element) -> str:
        bits = []
        for el in p.iter():
            name = _local(el.tag)
            if name == "t" and el.text:
                bits.append(el.text)
            elif name == "tab":
                bits.append("\t")
            elif name in ("br", "cr"):
                bits.append("\n")
        return "".join(bits)

    body = next((el for el in root if _local(el.tag) == "body"), root)
    for block in body:
        name = _local(block.tag)
        if name == "p":
            style = ""
            for el in block.iter():
                if _local(el.tag) == "pStyle":
                    style = next(iter(el.attrib.values()), "")
            text = para_text(block)
            if style.lower().startswith("heading") and text.strip():
                text = "## " + text
            out.append(text)
        elif name == "tbl":
            for row in block.iter():
                if _local(row.tag) != "tr":
                    continue
                cells = [para_text(c).strip().replace("\n", " ") for c in row if _local(c.tag) == "tc"]
                out.append("| " + " | ".join(cells) + " |")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def xlsx_to_text(data: bytes, max_rows: int = 5000) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in _xml(zf, "xl/sharedStrings.xml"):
                shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
        # sheet names in workbook order
        sheets: List[Tuple[str, str]] = []
        wb = _xml(zf, "xl/workbook.xml")
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in _xml(zf, "xl/_rels/workbook.xml.rels"):
                rels[rel.get("Id")] = rel.get("Target", "")
        for el in wb.iter():
            if _local(el.tag) == "sheet":
                rid = next((v for k, v in el.attrib.items() if k.endswith("}id")), "")
                target = rels.get(rid, "")
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = "xl/" + target
                sheets.append((el.get("name", "Sheet"), target))
        if not sheets:
            sheets = [(n.rsplit("/", 1)[-1], n) for n in sorted(names) if n.startswith("xl/worksheets/sheet")]
        out: List[str] = []
        for title, path in sheets:
            if path not in names:
                continue
            out.append("## Sheet: %s" % title)
            rows = 0
            for row in _xml(zf, path).iter():
                if _local(row.tag) != "row":
                    continue
                cells: List[str] = []
                for c in row:
                    if _local(c.tag) != "c":
                        continue
                    col = _col_index(c.get("r", ""))
                    while col is not None and len(cells) < col:
                        cells.append("")
                    value = ""
                    kind = c.get("t", "")
                    for v in c:
                        if _local(v.tag) == "v" and v.text is not None:
                            value = v.text
                            if kind == "s":
                                try:
                                    value = shared[int(value)]
                                except (ValueError, IndexError):
                                    pass
                        elif _local(v.tag) == "is":
                            value = "".join(t.text or "" for t in v.iter() if _local(t.tag) == "t")
                    cells.append(value.replace("\n", " "))
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    out.append(",".join(_csv_cell(x) for x in cells))
                    rows += 1
                    if rows >= max_rows:
                        out.append("[... more rows not shown]")
                        break
            out.append("")
    return "\n".join(out).strip()


def _col_index(ref: str) -> Optional[int]:
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return None
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _csv_cell(x: str) -> str:
    return '"%s"' % x.replace('"', '""') if ("," in x or '"' in x) else x


def pptx_to_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slides = sorted((n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        out = []
        for i, name in enumerate(slides, 1):
            paras = []
            for p in _xml(zf, name).iter():
                if _local(p.tag) == "p":
                    t = "".join(r.text or "" for r in p.iter() if _local(r.tag) == "t").strip()
                    if t:
                        paras.append(t)
            out.append("## Slide %d\n%s" % (i, "\n".join(paras)))
    return "\n\n".join(out)


def ipynb_to_text(data: bytes) -> str:
    nb = json.loads(data.decode("utf-8"))
    lang = ((nb.get("metadata") or {}).get("kernelspec") or {}).get("language", "python")
    out = []
    for cell in nb.get("cells", []):
        src = "".join(cell.get("source") or [])
        if cell.get("cell_type") == "code":
            out.append("```%s\n%s\n```" % (lang, src))
            for o in cell.get("outputs") or []:
                text = "".join(o.get("text") or (o.get("data") or {}).get("text/plain") or [])
                if text.strip():
                    out.append("Output:\n```\n%s\n```" % text.strip()[:4000])
        else:
            out.append(src)
    return "\n\n".join(out)


def textutil_to_text(path: Path) -> str:
    if not shutil.which("textutil"):
        raise UploadError("This file type can only be read on a Mac (textutil). Save it as .docx or .pdf.")
    try:
        res = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UploadError("Could not convert this file: %s" % exc) from exc
    if res.returncode != 0:
        raise UploadError("Could not convert this file: %s" % res.stderr.decode("utf-8", "replace")[:200])
    return res.stdout.decode("utf-8", "replace")


def extract_text(name: str, data: bytes, path: Optional[Path] = None) -> Tuple[str, str]:
    """Returns (text, note) for a document. Raises UploadError if it can't be read."""
    ext = Path(name).suffix.lower()
    try:
        if ext == ".pdf" or data[:5] == b"%PDF-":
            return pdf_to_text(data)
        if ext == ".docx":
            return docx_to_text(data), ""
        if ext in (".xlsx", ".xlsm"):
            return xlsx_to_text(data), ""
        if ext == ".pptx":
            return pptx_to_text(data), ""
        if ext == ".ipynb":
            return ipynb_to_text(data), ""
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
        raise UploadError("Could not read this %s file: %s" % (ext or "", exc)) from exc
    if ext in TEXTUTIL_EXT and path is not None:
        return textutil_to_text(path), ""
    text = decode_text(data)
    if text is None:
        raise UploadError("Can't read %s files. Supported: text, code, PDF, Word, Excel, PowerPoint, "
                          "images." % (ext or "this kind of"))
    return text, ""


# ----- images ----------------------------------------------------------------------------------
def prepare_image(path: Path) -> Path:
    """On a Mac: convert HEIC/TIFF/BMP/WebP to JPEG and shrink big pictures. Returns the file to use."""
    ext = path.suffix.lower()
    sips = shutil.which("sips")
    if not sips:
        if ext in (".heic", ".heif"):
            raise UploadError("HEIC photos can only be converted on a Mac")
        return path
    out = path.with_name("model.jpg")
    args = [sips, "-s", "format", "jpeg", "-s", "formatOptions", "85", "-Z", str(IMAGE_MAX_SIDE),
            str(path), "--out", str(out)]
    try:
        res = subprocess.run(args, capture_output=True, timeout=60)
        if res.returncode == 0 and out.exists():
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    if ext in (".heic", ".heif"):
        raise UploadError("Could not convert this HEIC photo")
    return path


# ----- storage ---------------------------------------------------------------------------------
class FileStore:
    """data/uploads/<id>/  holds  original file, meta.json and (for documents) text.txt."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "uploads"
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, file_id: str) -> Path:
        if not _ID_RE.match(file_id or ""):
            raise UploadError("Unknown file")
        return self.root / file_id

    def save(self, name: str, data: bytes) -> Dict[str, Any]:
        if len(data) > MAX_UPLOAD:
            raise UploadError("File is too large (max %d MB)" % (MAX_UPLOAD // 1024 // 1024))
        if not data:
            raise UploadError("The file is empty")
        name = os.path.basename(name.replace("\\", "/")).strip() or "file"
        safe = re.sub(r"[^\w.\- ()\[\]]+", "_", name)[:120] or "file"
        file_id = secrets.token_hex(8)
        folder = self._dir(file_id)
        folder.mkdir(parents=True)
        original = folder / safe
        original.write_bytes(data)
        ext = Path(name).suffix.lower()
        meta: Dict[str, Any] = {"id": file_id, "name": name, "stored": safe, "size": len(data),
                                "created": time.time(), "chat_id": None}
        try:
            if ext in IMAGE_EXT:
                model_file = prepare_image(original)
                meta.update(kind="image", model_file=model_file.name,
                            mime=IMAGE_MIME.get(model_file.suffix.lower(), "image/jpeg"))
            else:
                text, note = extract_text(name, data, original)
                text = text.replace("\r\n", "\n")
                truncated = len(text) > MAX_TEXT
                text = text[:MAX_TEXT]
                if not text.strip():
                    raise UploadError("No text could be read from this file")
                (folder / "text.txt").write_text(text, encoding="utf-8")
                meta.update(kind="text", chars=len(text), note=note + (" (cut to 3M characters)" if truncated else ""))
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return self.public(meta)

    @staticmethod
    def public(meta: Dict[str, Any]) -> Dict[str, Any]:
        keys = ("id", "name", "kind", "size", "chars", "note")
        return {k: meta[k] for k in keys if k in meta}

    def meta(self, file_id: str) -> Dict[str, Any]:
        try:
            return json.loads((self._dir(file_id) / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise UploadError("This file is no longer available") from None

    def text(self, file_id: str) -> str:
        try:
            return (self._dir(file_id) / "text.txt").read_text(encoding="utf-8")
        except OSError:
            return ""

    def image_b64(self, file_id: str) -> str:
        meta = self.meta(file_id)
        return base64.b64encode((self._dir(file_id) / meta.get("model_file", meta["stored"])).read_bytes()).decode()

    def path(self, file_id: str) -> Tuple[Path, Dict[str, Any]]:
        meta = self.meta(file_id)
        if meta.get("kind") == "image":
            return self._dir(file_id) / meta.get("model_file", meta["stored"]), meta
        return self._dir(file_id) / meta["stored"], meta

    def claim(self, file_id: str, chat_id: str) -> None:
        """Remember which chat uses the file (deleted together with the chat)."""
        meta = self.meta(file_id)
        if meta.get("chat_id") != chat_id:
            meta["chat_id"] = chat_id
            (self._dir(file_id) / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def delete_for_chat(self, chat_id: str) -> None:
        for folder in self.root.iterdir():
            try:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if meta.get("chat_id") == chat_id:
                shutil.rmtree(folder, ignore_errors=True)

    def cleanup_orphans(self, max_age_s: float = 86400) -> None:
        """Uploads never sent in a chat are removed after a day."""
        now = time.time()
        for folder in self.root.iterdir():
            try:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if folder.is_dir() and now - folder.stat().st_mtime > max_age_s:
                    shutil.rmtree(folder, ignore_errors=True)
                continue
            if not meta.get("chat_id") and now - meta.get("created", now) > max_age_s:
                shutil.rmtree(folder, ignore_errors=True)


# ----- finding the relevant parts of a long file (BM25 keyword search) --------------------------
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def tokenize(text: str) -> List[str]:
    """Lower-case words; Chinese/Japanese/Korean text becomes pairs of characters."""
    tokens: List[str] = []
    for word in re.findall(r"\w+", text.lower()):
        if _CJK.search(word):
            chars = [c for c in word if not c.isspace()]
            tokens.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
            if len(chars) == 1:
                tokens.append(chars[0])
        elif len(word) > 1 or word.isdigit():
            tokens.append(word)
    return tokens


def chunk_text(text: str, size: int = 1500, overlap: int = 150) -> List[Tuple[int, str]]:
    """Split into pieces of about `size` characters, cutting at paragraph or line ends.
    Returns [(start offset, piece)]."""
    chunks: List[Tuple[int, str]] = []
    start, n = 0, len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            cut = max(text.rfind("\n\n", start + size // 2, end), text.rfind("\n", start + size // 2, end))
            if cut <= start:
                cut = text.rfind(" ", start + size // 2, end)
            if cut > start:
                end = cut
        chunks.append((start, text[start:end]))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def bm25_rank(docs: List[str], query: str, k1: float = 1.4, b: float = 0.75) -> List[Tuple[float, int]]:
    """Scores each doc against the query (classic BM25). Returns [(score, index)] best first."""
    import math

    q = set(tokenize(query))
    if not q or not docs:
        return []
    toks = [tokenize(d) for d in docs]
    avg = sum(len(t) for t in toks) / max(1, len(toks)) or 1.0
    df = {term: sum(1 for t in toks if term in t) for term in q}
    n = len(docs)
    scores = []
    for i, t in enumerate(toks):
        counts: Dict[str, int] = {}
        for w in t:
            if w in q:
                counts[w] = counts.get(w, 0) + 1
        s = 0.0
        for term, tf in counts.items():
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(t) / avg))
        if s > 0:
            scores.append((s, i))
    scores.sort(reverse=True)
    return scores

