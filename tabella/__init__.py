"""tabella — Document lifecycle suite.

Three modules:
  - create  — Generate .docx/.xlsx/.pptx/.pdf/.odt/.ods/.odp from structured JSON
  - convert — Format conversion via pandoc (PDF output via WeasyPrint)
  - read    — Extract text and metadata via anydoc, with LiteParse for OCR

Nothing is installed or downloaded at runtime: the packages must already be
importable in the host Python.
"""

from __future__ import annotations

import re
from pathlib import Path


def _read_version() -> str:
    """The package version, from pyproject.toml, plugin.yaml or installed metadata.

    One source: pyproject.toml. Installed distributions read it back from
    their metadata (so a wheel cannot disagree with itself); a checkout or a
    plugin directory parses the file beside the package. plugin.json carries
    its own literal because a manifest must, and tests/test_version_sync.py
    fails when the two disagree.
    """
    # Source tree first: when this file sits next to a pyproject.toml we are
    # being imported from a checkout, and that file is the version being
    # edited — installed metadata would still say the previous release.
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.startswith("version = "):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    # Plugin directory without pyproject.toml (stripped for install): the
    # plugin.yaml manifest beside the package carries the same version.
    manifest = Path(__file__).resolve().parent.parent / "plugin.yaml"
    try:
        match = re.search(r'^version:\s*"?([^"\s]+)"?\s*$',
                          manifest.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    # Installed from a wheel/sdist: the built metadata is authoritative.
    try:
        from importlib.metadata import version

        return version("tabella-docs")
    except Exception:
        return "0+unknown"


__version__ = _read_version()

# ── Configuration ─────────────────────────────────────────────

def get_output_dir() -> Path:
    """Default output directory for generated documents.

    Delegates to tabella.utils so the DOCS_OUTPUT_DIR rule lives in one place
    (utils owns it; this name is the public one).
    """
    from .utils import _get_output_dir

    return _get_output_dir()


__all__ = ["get_output_dir"]