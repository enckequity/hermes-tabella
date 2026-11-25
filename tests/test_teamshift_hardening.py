"""Hardening for the hosted plugin: contained writes, no runtime installs,
no PyMuPDF, no LaTeX, offline OCR, plugin registration and neutral wording.

Run: python -m pytest tests/test_teamshift_hardening.py
(the pandoc cases skip when pandoc is not on PATH; the weasyprint/docx cases
skip when those packages are absent).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tabella import convert as convert_mod  # noqa: E402
from tabella import read as read_mod  # noqa: E402
from tabella.convert import doc_convert  # noqa: E402
from tabella.create import create_document  # noqa: E402
from tabella.toolspec import TOOLS, handle_tool  # noqa: E402

needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")


def _outside(tmp_path):
    """A directory beside (not inside) the output folder."""
    out = tmp_path.parent / (tmp_path.name + "-outside")
    out.mkdir(exist_ok=True)
    return out


# ── 1. writes stay inside the output folder ───────────────────────────

@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_create_refuses_absolute_path_outside(tmp_path, fmt):
    target = _outside(tmp_path) / "sub" / f"x.{fmt}"
    res = create_document(fmt, "hello", str(target))
    assert res["success"] is False and "output folder" in res["error"]
    assert not target.parent.exists(), "refusal must happen before mkdir"


def test_create_refuses_dotdot_escape(tmp_path):
    res = create_document("docx", "hello", "../escape.docx")
    assert res["success"] is False
    assert not (tmp_path.parent / "escape.docx").exists()


def test_create_refuses_the_output_folder_itself(tmp_path):
    res = create_document("docx", "hello", str(tmp_path))
    assert res["success"] is False


def test_create_refuses_symlinked_dir_pointing_outside(tmp_path):
    outside = _outside(tmp_path)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    res = create_document("docx", "hello", str(tmp_path / "link" / "x.docx"))
    assert res["success"] is False
    assert not (outside / "x.docx").exists()


def test_create_refuses_symlinked_file_pointing_outside(tmp_path):
    victim = _outside(tmp_path) / "victim.docx"
    victim.write_text("keep me")
    (tmp_path / "x.docx").symlink_to(victim)
    res = create_document("docx", "hello", str(tmp_path / "x.docx"))
    assert res["success"] is False
    assert victim.read_text() == "keep me"


def test_create_relative_path_lands_in_output_folder(tmp_path):
    pytest.importorskip("docx")
    res = create_document("docx", "hello", "reports/q3.docx")
    assert res["success"] is True, res
    assert Path(res["path"]) == (tmp_path / "reports" / "q3.docx").resolve()
    assert Path(res["path"]).is_file()


def test_tool_default_path_is_inside_output_folder(tmp_path):
    pytest.importorskip("docx")
    res = json.loads(handle_tool("doc_create_document", {"format": "docx", "content": "hi"}))
    assert res["success"] is True, res
    assert Path(res["path"]).parent == tmp_path.resolve()


def test_injected_user_context_cannot_choose_a_folder(tmp_path):
    """The old handler honoured a model-supplied _user_context.user_files."""
    outside = _outside(tmp_path)
    res = json.loads(handle_tool("doc_create_document", {
        "format": "docx", "content": "hi",
        "output_path": str(outside / "x.docx"),
        "_user_context": {"user_files": str(outside)},
    }))
    assert res["success"] is False
    assert not (outside / "x.docx").exists()


def _src(tmp_path):
    p = tmp_path / "src.md"
    p.write_text("# Title\n\nbody text\n", encoding="utf-8")
    return str(p)


def test_convert_refuses_output_outside_before_running_pandoc(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_mod.subprocess, "run",
                        lambda *a, **k: pytest.fail("pandoc ran for a refused target"))
    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: "/usr/bin/pandoc")
    target = _outside(tmp_path) / "new" / "x.html"
    res = doc_convert(_src(tmp_path), "html", output_path=str(target))
    assert res["success"] is False and "output folder" in res["error"]
    assert not target.parent.exists()


def test_convert_refuses_category_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_mod.subprocess, "run",
                        lambda *a, **k: pytest.fail("pandoc ran for a refused target"))
    res = doc_convert(_src(tmp_path), "html", category="../../evil")
    assert res["success"] is False
    assert not (tmp_path.parent.parent / "evil").exists()


# ── 2. pandoc: sandboxed, PDF via WeasyPrint, never LaTeX ─────────────

def test_convert_command_is_sandboxed_and_never_uses_latex(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("-o") + 1]).write_text("<html><body>x</body></html>")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(convert_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(convert_mod.shutil, "which",
                        lambda name: "/usr/bin/pandoc" if name == "pandoc" else None)
    monkeypatch.setattr(convert_mod, "_html_to_pdf",
                        lambda html, out: Path(out).write_bytes(b"%PDF-1.7\n"))
    res = doc_convert(_src(tmp_path), "pdf")
    assert res["success"] is True, res
    cmd = seen["cmd"]
    assert "--sandbox" in cmd
    assert "--pdf-engine" not in cmd and cmd[cmd.index("-t") + 1] == "html5"
    assert res["pdf_engine"] == "weasyprint"
    assert Path(res["output_path"]).parent == tmp_path.resolve()


def test_missing_pandoc_is_a_neutral_message(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: None)
    res = doc_convert(_src(tmp_path), "docx")
    assert res["success"] is False
    assert "not available" in res["error"]
    assert "sudo" not in res["error"] and "apt" not in res["error"]


@needs_pandoc
def test_real_pdf_conversion_uses_weasyprint(tmp_path):
    pytest.importorskip("weasyprint")
    res = doc_convert(_src(tmp_path), "pdf")
    assert res["success"] is True, res
    assert Path(res["output_path"]).read_bytes().startswith(b"%PDF")


@needs_pandoc
def test_sandbox_blocks_including_other_files(tmp_path):
    secret = _outside(tmp_path) / "secret.txt"
    secret.write_text("TOPSECRET-123")
    src = tmp_path / "doc.rst"
    src.write_text(f"Title\n=====\n\n.. include:: {secret}\n")
    res = doc_convert(str(src), "html")
    if res["success"]:
        assert "TOPSECRET-123" not in Path(res["output_path"]).read_text()


# ── 3. nothing is installed or downloaded at runtime ──────────────────

def test_no_bootstrap_module_or_installer_remains():
    assert importlib.util.find_spec("tabella.bootstrap") is None
    assert not (ROOT / "scripts" / "setup.sh").exists()
    sources = [ROOT / "__init__.py", ROOT / "mcp_server.py",
               ROOT / "scripts" / "mcp_http_server.py", *sorted((ROOT / "tabella").glob("*.py"))]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for needle in ("ensure_venv", "pip install", "\"pip\"", "execv", "urlopen"):
            assert needle not in text, f"{path.name} still contains {needle!r}"


def test_missing_python_package_is_a_neutral_message(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    res = create_document("pdf", "hello", "x.pdf")
    assert res["success"] is False
    assert "not available" in res["error"] and "weasyprint" in res["error"]
    assert not (tmp_path / "x.pdf").exists()


def test_missing_anydoc_is_a_neutral_message(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "anydoc", None)
    monkeypatch.setitem(sys.modules, "liteparse", None)
    f = tmp_path / "a.docx"
    f.write_bytes(b"PK\x03\x04")
    with pytest.raises(RuntimeError, match="not available"):
        read_mod.read(str(f))


# ── 4. PyMuPDF is gone ───────────────────────────────────────────────

def test_pymupdf_is_never_used():
    names = {t["name"] for t in TOOLS}
    assert "doc_edit_pdf" not in names
    for path in sorted((ROOT / "tabella").glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        assert "pymupdf" not in text and "import fitz" not in text, path.name
    pins = (ROOT / "requirements-pinned.txt").read_text().lower()
    assert "pymupdf==" not in pins


# ── 5. OCR only with local language data ─────────────────────────────

class _FakeLiteParse:
    calls = []

    def __init__(self, **kw):
        _FakeLiteParse.calls.append(kw)

    def parse(self, path):
        return "fake text"


def _fake_liteparse(monkeypatch):
    _FakeLiteParse.calls = []
    mod = types.ModuleType("liteparse")
    mod.LiteParse = _FakeLiteParse
    mod.ParseResult = type("ParseResult", (), {})
    monkeypatch.setitem(sys.modules, "liteparse", mod)


def test_ocr_off_without_local_tessdata(tmp_path, monkeypatch):
    _fake_liteparse(monkeypatch)
    monkeypatch.setattr(read_mod, "_local_tessdata", lambda: None)
    read_mod._run_liteparse(str(tmp_path / "x.pdf"), extract_images=True)
    assert _FakeLiteParse.calls[-1]["ocr_enabled"] is False
    assert "tessdata_path" not in _FakeLiteParse.calls[-1]
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG")
    with pytest.raises(RuntimeError, match="not available"):
        read_mod.read(str(img))


def test_ocr_uses_local_tessdata_when_present(tmp_path, monkeypatch):
    _fake_liteparse(monkeypatch)
    (tmp_path / "eng.traineddata").write_bytes(b"x")
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    read_mod._run_liteparse(str(tmp_path / "x.pdf"), extract_images=True)
    assert _FakeLiteParse.calls[-1]["ocr_enabled"] is True
    assert _FakeLiteParse.calls[-1]["tessdata_path"] == str(tmp_path)


# ── 6. host read guard is honoured ───────────────────────────────────

def test_host_read_guard_blocks_credential_reads(tmp_path, monkeypatch):
    pkg = types.ModuleType("agent")
    pkg.__path__ = []
    fs = types.ModuleType("agent.file_safety")
    fs.get_read_block_error = lambda p: "Access denied." if p.endswith("auth.json") else None
    monkeypatch.setitem(sys.modules, "agent", pkg)
    monkeypatch.setitem(sys.modules, "agent.file_safety", fs)
    cred = tmp_path / "auth.json"
    cred.write_text('{"token": "x"}')
    res = json.loads(handle_tool("doc_read", {"path": str(cred)}))
    assert res["success"] is False and "Access denied" in res["error"]
    res = json.loads(handle_tool("doc_convert", {"input_path": str(cred), "to_format": "html"}))
    assert res["success"] is False and "Access denied" in res["error"]


# ── 7. plugin registration ──────────────────────────────────────────

class _Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, toolset, schema, handler, check_fn=None, **kw):
        self.tools[name] = dict(toolset=toolset, schema=schema, handler=handler, check_fn=check_fn)

    def register_hook(self, *a, **k):  # pragma: no cover - must not be called
        raise AssertionError("tabella registers no hooks")


def _load_plugin():
    """Import the plugin directory the way the host loader does."""
    name = "hermes_plugins_test.tabella"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("hermes_plugins_test", types.ModuleType("hermes_plugins_test"))
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_registers_four_tools_in_one_toolset(tmp_path):
    ctx = _Ctx()
    _load_plugin().register(ctx)
    assert set(ctx.tools) == {"doc_read", "doc_read_meta", "doc_convert", "doc_create_document"}
    for name, t in ctx.tools.items():
        assert t["toolset"] == "tabella"
        assert t["schema"]["name"] == name
        assert isinstance(t["schema"]["parameters"], dict)
        assert t["schema"]["parameters"]["type"] == "object"
    assert ctx.tools["doc_convert"]["check_fn"] is not None
    manifest = (ROOT / "plugin.yaml").read_text()
    for name in ctx.tools:
        assert f"  - {name}\n" in manifest
    assert "provides_hooks: []" in manifest


def test_registered_handler_writes_inside_output_folder(tmp_path):
    pytest.importorskip("docx")
    ctx = _Ctx()
    _load_plugin().register(ctx)
    res = json.loads(ctx.tools["doc_create_document"]["handler"](
        {"format": "docx", "content": "hi", "output_path": "a.docx"}, task_id="t"))
    assert res["success"] is True, res
    assert Path(res["path"]) == (tmp_path / "a.docx").resolve()
    bad = json.loads(ctx.tools["doc_create_document"]["handler"](
        {"format": "docx", "content": "hi", "output_path": "/etc/x.docx"}))
    assert bad["success"] is False


# ── 8. white-label: model- and user-facing text never names the host ─

def test_user_facing_text_does_not_name_the_host():
    for tool in TOOLS:
        assert "hermes" not in json.dumps(tool).lower()
    for path in [ROOT / "__init__.py", *sorted((ROOT / "tabella").glob("*.py"))]:
        assert "hermes" not in path.read_text(encoding="utf-8").lower(), path.name
    manifest = (ROOT / "plugin.yaml").read_text().lower()
    description = next(l for l in manifest.splitlines() if l.startswith("description:"))
    assert "hermes" not in description


def test_no_runtime_relies_on_package_declarations(tmp_path):
    """pyproject.toml is stripped on install; the version must still resolve."""
    text = (ROOT / "tabella" / "__init__.py").read_text()
    assert "plugin.yaml" in text
    assert os.path.exists(ROOT / "plugin.yaml")
