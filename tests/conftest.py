"""Every test writes inside a per-test output folder.

Writes outside the configured output folder are refused, so tests point
DOCS_OUTPUT_DIR at their own tmp_path (inputs live there too), and
tempfile's default directory moves under it so tests that build their files
in a TemporaryDirectory still write inside the output folder.
"""
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path
