"""Intake and standardisation (proposal Fig. 1, step 1).

Loads company materials into chunks that keep their original source location
(challenge C1: extracted claims must remain linked to where they came from).
Supported in the MVP: .md, .txt, .pdf (pypdf, text layer only).
Not yet: .docx/.pptx, OCR of image-only slides, webpages.
"""

from __future__ import annotations

import re
from pathlib import Path

from .state import SourceChunk

MAX_CHUNK_CHARS = 900
_DATE = re.compile(r"(20\d\d-\d\d(?:-\d\d)?)")


def _doc_date(name: str, text: str) -> str:
    m = re.search(r"^date:\s*(.+)$", text[:400], re.I | re.M) or _DATE.search(name)
    return m.group(1).strip() if m else ""


def _md_chunks(text: str) -> list[tuple[str, str]]:
    """Split by headings, then by blank-line paragraphs up to MAX_CHUNK_CHARS. Returns (location, text)."""
    out: list[tuple[str, str]] = []
    section, buf, start = "(top)", [], 1
    lines = text.splitlines()

    def flush(end: int) -> None:
        body = "\n".join(buf).strip()
        if body:
            out.append((f"section '{section}', lines {start}-{end}", body))

    for i, ln in enumerate(lines, 1):
        if ln.startswith("#"):
            flush(i - 1)
            section, buf, start = ln.lstrip("# ").strip(), [], i + 1
            continue
        if not ln.strip() and sum(len(b) for b in buf) > MAX_CHUNK_CHARS:
            flush(i - 1)
            buf, start = [], i + 1
            continue
        buf.append(ln)
    flush(len(lines))
    return out


def _pdf_chunks(path: Path) -> list[tuple[str, str]]:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        return [("whole file", "[PDF skipped: install pypdf]")]
    out = []
    for i, page in enumerate(PdfReader(str(path)).pages, 1):
        txt = (page.extract_text() or "").strip()
        if txt:
            out.append((f"page {i}", txt[:MAX_CHUNK_CHARS * 2]))
        else:
            out.append((f"page {i}", "[no text layer on this page — OCR not supported in MVP]"))
    return out


def load_materials(materials_dir: Path) -> tuple[list[SourceChunk], list[str]]:
    """Return (chunks, skipped files)."""
    chunks: list[SourceChunk] = []
    skipped: list[str] = []
    files = sorted(p for p in materials_dir.iterdir() if p.is_file() and not p.name.startswith("."))
    for d, path in enumerate(files, 1):
        ext = path.suffix.lower()
        if ext in (".md", ".txt"):
            text = path.read_text(errors="replace")
            parts = _md_chunks(text)
            date = _doc_date(path.name, text)
        elif ext == ".pdf":
            parts = _pdf_chunks(path)
            date = _doc_date(path.name, parts[0][1] if parts else "")
        else:
            skipped.append(path.name)
            continue
        for k, (loc, body) in enumerate(parts, 1):
            chunks.append(SourceChunk(chunk_id=f"D{d}#{k}", doc=path.name, location=loc, text=body, doc_date=date))
    return chunks, skipped
