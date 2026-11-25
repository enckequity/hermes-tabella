"""documents.convert — Document format conversion via pandoc.

Supports: markdown → PDF/DOCX/HTML/ODT/EPUB and any pandoc-supported format pair.
PDF output is rendered by WeasyPrint from pandoc's HTML (no LaTeX).
Output directory is configurable via DOCS_OUTPUT_DIR (defaults to ~/Documents);
writes outside it are refused.
"""

import re
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .utils import (
    MissingDependencyError,
    OutsideOutputDirError,
    _resolve_output_dir,
    check_read_allowed,
    contained_output_path,
    require,
)

logger = logging.getLogger(__name__)


def _check_deps() -> Optional[str]:
    """Return an error string if pandoc is missing. Nothing is installed."""
    if not shutil.which("pandoc"):
        return (
            "Document conversion is not available here: the pandoc tool is not "
            "installed in this runtime."
        )
    return None


def _extract_pdf(src: Path) -> str:
    """Extract a PDF's text through the read lane (anydoc, then OCR).

    pandoc has no PDF reader — it converts *to* PDF only. Routing through
    read() means a text layer is used when present and a scanned page is
    OCR'd when not, so `doc_convert(x.pdf, "docx")` behaves like the user
    expects instead of failing inside pandoc.
    """
    from .read import read

    return read(str(src))


def _validate_extra_args(extra_args: Optional[list]) -> list:
    """Strict allowlist for pandoc flags.

    pandoc can execute code via --filter/--lua-filter/--pdf-engine, so only
    formatting flags survive (catalog review #2026-10-16, finding 1). Runs
    before the dependency check so a hostile flag is rejected as invalid,
    not masked by "pandoc is not installed".
    """
    if not extra_args:
        return []
    allowed = {"--toc", "--number-sections"}
    checked = []
    for a in extra_args:
        if a in allowed:
            checked.append(a)
        elif re.match(r"^-V\S*=", a):
            checked.append(a)
        else:
            raise ValueError(
                f"unsupported pandoc argument {a!r}: extra_args allows only "
                "--toc, --number-sections and -V <key>=<value>"
            )
    return checked


def _html_to_pdf(html_path: str, out_file: Path) -> None:
    """Render pandoc's standalone HTML to PDF with WeasyPrint (no LaTeX).

    Uses the same guarded URL fetcher as doc_create_document: no network, no
    host files, only allowlisted data: URIs.
    """
    require("weasyprint", "weasyprint", "PDF output")
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration

    from .create import _safe_url_fetcher

    font_config = FontConfiguration()
    fetcher = _safe_url_fetcher(font_config)
    html_str = Path(html_path).read_text(encoding="utf-8")
    HTML(string=html_str, url_fetcher=fetcher).write_pdf(
        str(out_file), font_config=font_config
    )


# ── tool ──
def doc_convert(
    input_path: str,
    to_format: str,
    from_format: str = None,
    output_path: str = None,
    extra_args: list = None,
    category: str = None,
) -> dict:
    """Convert a document from one format to another using pandoc.

    Args:
        input_path: Absolute path to the source document.
        to_format: Target format (e.g. "pdf", "docx", "html", "odt", "epub").
        from_format: Optional source format hint (e.g. "markdown", "html").
                     When omitted, pandoc infers from file extension.
        output_path: Optional output path inside the output folder (relative
                     paths are taken relative to it). Anything resolving
                     outside the output folder is refused.
                     When omitted, writes to the output folder.
        extra_args:  Optional extra pandoc CLI flags (e.g. ["--toc", "-Vmargin=1in"]).
                     NOTE: -V must be a single token ("-Vkey=value") — the
                     two-token "-V key=value" form is rejected by the allowlist.
        category:    Optional subfolder of the output folder (e.g. "reports").

    Returns:
        {"success": bool, "output_path": str, "from_format": str, "to_format": str,
         "pdf_engine": str | None, "message": str}
    """
    src = Path(input_path).expanduser().resolve()
    if not src.exists():
        return {"success": False, "error": f"Source file not found: {src}"}
    try:
        check_read_allowed(src)
    except PermissionError as e:
        return {"success": False, "error": str(e)}

    # Determine formats
    src_fmt = from_format or _infer_format(src.suffix.lstrip(".").lower())
    tgt_fmt = to_format.lower()
    _check_format(src_fmt, "from_format")
    _check_format(tgt_fmt, "to_format")
    extra = _validate_extra_args(extra_args)

    # Refuse a write target outside the output folder before any work, so a
    # refused call creates nothing (no directory, no temp file, no pandoc run).
    try:
        if output_path:
            out_file = contained_output_path(output_path)
        else:
            out_file = _resolve_output_dir(category) / f"{src.stem}.{tgt_fmt}"
    except OutsideOutputDirError as e:
        return {"success": False, "error": str(e)}

    # pandoc is always required. PDF output is rendered by WeasyPrint from
    # pandoc's HTML, so no TeX installation is needed or used.
    dep_err = _check_deps()
    if dep_err:
        return {"success": False, "error": dep_err}

    with tempfile.TemporaryDirectory(prefix="tabella-convert-") as work:
        # pandoc converts to PDF, never from it: extract the text first, then
        # convert the extracted markdown. Scanned pages come back OCR'd when
        # OCR is available, so this works where `pandoc -f pdf` cannot.
        pandoc_src, pandoc_fmt = str(src), src_fmt
        if src_fmt == "pdf":
            try:
                text = _extract_pdf(src)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"could not extract text from PDF: {type(e).__name__}: {e}",
                }
            if not text.strip():
                return {"success": False, "error": f"PDF has no extractable text: {src}"}
            pandoc_src = str(Path(work) / "extracted.md")
            Path(pandoc_src).write_text(text, encoding="utf-8")
            pandoc_fmt = "markdown"

        if tgt_fmt == "pdf":
            pandoc_out, pandoc_to = str(Path(work) / "render.html"), "html5"
        else:
            pandoc_out, pandoc_to = str(out_file), tgt_fmt

        # --sandbox: pandoc may read only the input file itself, so an
        # \input{...} / `.. include::` / image reference inside the document
        # cannot pull other host files into the output.
        cmd = ["pandoc", pandoc_src, "-o", pandoc_out, "-f", pandoc_fmt, "-t", pandoc_to,
               "--sandbox"]
        if tgt_fmt == "pdf":
            cmd.append("--standalone")
        cmd.extend(extra)

        out_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"pandoc failed (exit {result.returncode}): {result.stderr.strip()}",
                }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "pandoc timed out after 5 minutes."}
        except Exception as e:
            return {"success": False, "error": f"pandoc error: {e}"}

        if tgt_fmt == "pdf":
            try:
                _html_to_pdf(pandoc_out, out_file)
            except MissingDependencyError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                return {"success": False, "error": f"PDF rendering failed: {type(e).__name__}: {e}"}

    if not out_file.exists():
        return {"success": False, "error": f"Output file was not created: {out_file}"}

    return {
        "success": True,
        "output_path": str(out_file),
        "from_format": src_fmt,
        "to_format": tgt_fmt,
        "extracted_text": src_fmt == "pdf",
        "pdf_engine": "weasyprint" if tgt_fmt == "pdf" else None,
        "message": f"MEDIA:{out_file}\n\nConverted {src.name} → {tgt_fmt}: {out_file}",
    }


def _infer_format(ext: str) -> str:
    """Map common extensions to pandoc format names."""
    mapping = {
        "md": "markdown",
        "markdown": "markdown",
        # pandoc has no "plain" *reader*; markdown is the closest faithful
        # reading of a text file (headings/lists if present, prose otherwise).
        "txt": "markdown",
        "html": "html",
        "htm": "html",
        "docx": "docx",
        "odt": "odt",
        "pdf": "pdf",
        "tex": "latex",
        "rst": "rst",
        "epub": "epub",
        "typ": "typst",   # pandoc reads and writes Typst
    }
    return mapping.get(ext, ext)


# Pandoc treats a format name ending in ".lua" as a path to a custom Lua
# reader/writer and executes it with the full Lua stdlib (os.execute) —
# same code-exec class as --lua-filter via a different argument. Restrict
# format names to pandoc's built-in name grammar (catalog finding 2026-10-17).
_FORMAT_RE = re.compile(r"^[a-z0-9_]+([+-][a-z0-9_]+)*$")


def _check_format(name: str, which: str) -> None:
    """Reject anything that isn't a built-in pandoc format name."""
    if not _FORMAT_RE.match(name):
        raise ValueError(
            f"unsupported {which} {name!r}: must be a built-in pandoc format "
            "(alphanumeric/underscore, optional +ext/-ext modifiers)"
        )
