"""documents.read — read documents via anydoc (primary) + LiteParse (OCR fallback).

Dependencies (firecrawl-anydoc, liteparse) must already be importable in the
host Python; nothing is installed or downloaded at runtime. OCR runs only when
English Tesseract language data is already on disk (TESSDATA_PREFIX, or the
Debian tesseract-ocr-eng location): LiteParse would otherwise fetch it from
the network, so without local data OCR is switched off.

Architecture:
  - Office formats (DOCX, DOC, XLSX, XLSB, PPTX, PPT, ODT, ODS, ODP, RTF,
    EPUB, CSV) and text-based PDFs → anydoc (Rust, in-process, ~4ms,
    GitHub-Flavored Markdown output).
  - Scanned PDFs and images (JPG, PNG, TIFF) → LiteParse (Tesseract OCR).
    Used when anydoc returns empty for a PDF, or for image files directly.
  - Plain text (.txt, .md, .json, .py, .yaml, etc.) → direct file read.
"""
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import check_read_allowed, require

# Where already-installed English OCR data may live. First hit wins.
_TESSDATA_DIRS = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
)


def _local_tessdata() -> Optional[str]:
    """Directory holding eng.traineddata on this host, or None.

    LiteParse downloads missing traineddata from GitHub on first OCR; passing a
    directory that already has it (and disabling OCR when none exists) keeps
    reading fully offline.
    """
    candidates = []
    configured = os.environ.get("TESSDATA_PREFIX")
    if configured:
        candidates.append(configured)
    candidates.extend(_TESSDATA_DIRS)
    for d in candidates:
        if (Path(d) / "eng.traineddata").is_file():
            return d
    return None

# ── Format classification ────────────────────────────────────────────────────

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".json", ".html", ".htm",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".rst", ".fountain", ".log", ".py", ".js", ".ts", ".css",
}

# Image formats that need OCR (LiteParse + Tesseract).
IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".gif",
}

# ── anydoc (primary engine) ──────────────────────────────────────────────────

def _run_anydoc(path: str, timeout: int = 60) -> str:
    """Convert a document to Markdown using anydoc.

    Returns the Markdown text, or empty string if the document has no
    extractable text (e.g. scanned PDF without OCR).
    """
    anydoc = require("anydoc", "firecrawl-anydoc", "Document reading")
    md = anydoc.to_markdown(path)
    return md or ""


# ── LiteParse (OCR fallback) ─────────────────────────────────────────────────

def _run_liteparse(path: str, extract_images: bool = True, timeout: int = 120) -> Dict[str, Any]:
    """Parse a document using LiteParse with OCR enabled.

    Used for scanned PDFs and images where anydoc returns empty.
    """
    liteparse = require("liteparse", "liteparse", "Scanned-page and image reading")
    LiteParse, ParseResult = liteparse.LiteParse, liteparse.ParseResult

    tessdata = _local_tessdata()
    ocr = bool(extract_images and tessdata)
    kwargs = {"ocr_enabled": ocr, "output_format": "text", "quiet": True}
    if ocr:
        kwargs["tessdata_path"] = tessdata
    parser = LiteParse(**kwargs)
    result = parser.parse(path)
    text = ""
    if isinstance(result, ParseResult):
        text = getattr(result, "text", None) or getattr(result, "markdown", None) or ""
    elif isinstance(result, str):
        text = result
    else:
        text = str(result)
    return {
        "text": text,
        "metadata": getattr(result, "metadata", {}) if hasattr(result, "metadata") else {},
    }


# ── Plain text fast path ─────────────────────────────────────────────────────

def _read_text(path: str, max_chars: Optional[int] = None) -> str:
    """Read a text file, or refuse it as binary.

    The old version looped over encodings with errors="replace", which never
    raises — so the loop only ever ran once, and a binary file came back as a
    page of U+FFFD instead of an error. Decode the bytes and say what happened
    instead: UTF-8 (BOM tolerated), then cp1252 for the Windows-era latin-1
    case, and a NUL byte means it was never text.
    """
    data = Path(path).read_bytes()
    if b"\x00" in data[:8192]:
        raise RuntimeError(f"{path} looks binary (NUL bytes) — not a text file")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"{path} is not UTF-8 or Windows-1252 text")
    return text[:max_chars] if max_chars else text


# ── Main dispatch ────────────────────────────────────────────────────────────

def read(path: str, max_chars: Optional[int] = None, extract_images: bool = True, max_pages: Optional[int] = None) -> str:
    """Extract text from a document.

    Dispatch:
      1. Plain text → direct file read (fast path)
      2. Office formats + text PDFs → anydoc (Markdown output)
      3. Images → LiteParse (OCR)
      4. PDFs with no text layer — anydoc returns empty or raises NeedsOcrError →
         LiteParse (OCR fallback for scanned PDFs)
    """
    check_read_allowed(path)
    suffix = Path(path).suffix.lower()

    # 1. Plain text fast path
    if suffix in TEXT_SUFFIXES:
        return _read_text(path, max_chars)

    # 2. Images → LiteParse OCR directly
    if suffix in IMAGE_SUFFIXES:
        if not (extract_images and _local_tessdata()):
            raise RuntimeError(
                "Reading text from images is not available here: OCR language "
                "data is not installed in this runtime."
            )
        data = _run_liteparse(path, extract_images=extract_images)
        text = data.get("text") or ""
        if max_chars:
            text = text[:max_chars]
        return text

    # 3. Everything left — office formats, PDFs, unknown extensions → anydoc.
    #    (Text returned above, so the old `or suffix not in TEXT_SUFFIXES`
    #    tail was dead.)
    text = ""
    anydoc_error = None
    try:
        text = _run_anydoc(path)
    except Exception as e:
        # A fallback boundary: any primary-engine failure means "try the other
        # engine". anydoc raises NeedsOcrError for a PDF with no text layer, and
        # its hierarchy (NeedsOcrError -> ConvertError -> Exception) is theirs,
        # not ours — enumerating it is what made the OCR lane unreachable for
        # scanned PDFs.
        anydoc_error = f"{type(e).__name__}: {e}"

    # 4. No text layer (scanned PDF) or anydoc failed → LiteParse OCR
    if not text.strip():
        try:
            data = _run_liteparse(path, extract_images=extract_images)
            text = data.get("text") or ""
        except Exception as liteparse_err:
            if anydoc_error:
                raise RuntimeError(
                    f"anydoc failed: {anydoc_error}; liteparse fallback also failed: {liteparse_err}"
                )
            raise RuntimeError(f"both anydoc and liteparse returned no text. liteparse: {liteparse_err}")

    if max_chars:
        text = text[:max_chars]
    return text


def read_meta(path: str) -> Dict[str, Any]:
    """Extract metadata from a document."""
    check_read_allowed(path)
    suffix = Path(path).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return {"path": path, "format": "text"}

    # LiteParse has the richest metadata extraction (office + PDF + images).
    try:
        data = _run_liteparse(path, extract_images=False)
        meta = data.get("metadata") or {}
        meta["path"] = path
        return meta
    except Exception:
        # LiteParse unavailable (e.g. its LibreOffice fallback missing for office
        # formats). Fall back to filesystem-truth metadata rather than failing:
        # callers get valid basics instead of a tool error.
        import os

        st = os.stat(path)
        return {
            "path": path,
            "format": suffix.lstrip("."),
            "size_bytes": st.st_size,
            "mtime": st.st_mtime,
        }
