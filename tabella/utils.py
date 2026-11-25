"""Shared helpers for the documents plugin.

Owns three rules that every tool must apply the same way:

* where documents go (the output folder: DOCS_OUTPUT_DIR, else ~/Documents),
* that nothing is ever written outside that folder (real paths, symlinks
  followed), and
* that a missing Python package is reported plainly instead of being
  installed. This plugin never downloads or installs anything at runtime.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path


class OutsideOutputDirError(ValueError):
    """A write target that resolves outside the configured output folder."""


class MissingDependencyError(RuntimeError):
    """A feature whose Python package or system tool is not installed here."""


def _get_output_dir() -> Path:
    """Resolve output directory for documents.

    Uses DOCS_OUTPUT_DIR env var if set, otherwise defaults to ~/Documents.
    """
    d = os.environ.get("DOCS_OUTPUT_DIR")
    if d:
        p = Path(d).expanduser()
    else:
        p = Path.home() / "Documents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _output_root() -> Path:
    """The output folder as a real path (symlinks resolved)."""
    return _get_output_dir().resolve()


def _inside(root: Path, resolved: Path) -> bool:
    return resolved != root and root in resolved.parents


def contained_output_path(candidate) -> Path:
    """Return the real path for a write target, or refuse it.

    Relative paths are taken relative to the output folder. The path is
    resolved with every symlink followed (including a final component that is
    itself a symlink), and anything that does not land strictly inside the
    output folder raises OutsideOutputDirError. Nothing is created here: the
    caller creates parent directories only after this check passed.
    """
    root = _output_root()
    p = Path(str(candidate)).expanduser()
    if not p.is_absolute():
        p = root / p
    resolved = p.resolve()
    if not _inside(root, resolved):
        raise OutsideOutputDirError(
            f"refused to write {candidate}: documents can only be saved inside "
            f"the output folder {root}"
        )
    return resolved


def _is_in_output_dir(path: Path) -> bool:
    """Check if path is under the configured output directory."""
    try:
        return _inside(_output_root(), Path(path).expanduser().resolve())
    except Exception:
        return False


def _resolve_output_dir(category: str = None) -> Path:
    """Return output directory with optional category subfolder.

    The category must stay inside the output folder too ("../x" or a symlink
    pointing elsewhere is refused before anything is created).
    """
    root = _output_root()
    if not category:
        return root
    target = (root / category).resolve()
    if not _inside(root, target):
        raise OutsideOutputDirError(
            f"refused category {category!r}: it resolves outside the output folder {root}"
        )
    target.mkdir(parents=True, exist_ok=True)
    return target


def require(module: str, package: str, feature: str):
    """Import an already-installed module or raise a plain, neutral error.

    Never installs anything. Any import failure (including a missing system
    library behind a binding, e.g. pango for weasyprint) is reported as the
    feature being unavailable in this runtime.
    """
    try:
        return importlib.import_module(module)
    except Exception as exc:
        raise MissingDependencyError(
            f"{feature} is not available here: the Python package '{package}' "
            f"is not installed in this runtime ({type(exc).__name__})."
        ) from None


def check_read_allowed(path) -> None:
    """Apply the host's credential-file read guard when one is present.

    Inside the agent runtime, agent.file_safety refuses reads of credential and
    secret stores; standalone use has no such module and proceeds.
    """
    try:
        safety = importlib.import_module("agent.file_safety")
    except Exception:
        return
    blocked = safety.get_read_block_error(str(Path(str(path)).expanduser().resolve()))
    if blocked:
        raise PermissionError(blocked)
