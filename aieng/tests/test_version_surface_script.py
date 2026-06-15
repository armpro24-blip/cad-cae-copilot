from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "update_version_surface.py"


def _load_script_module() -> ModuleType:
    """Load the version-surface script as an importable test module."""
    spec = importlib.util.spec_from_file_location("update_version_surface", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_directory_text_hash_is_independent_of_checkout_line_endings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text directory hashes must match for equivalent LF and CRLF files."""
    module = _load_script_module()
    schema = tmp_path / "schemas" / "example.schema.json"
    schema.parent.mkdir()

    schema.write_bytes(b'{\n  "type": "object"\n}\n')
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    lf_hash = module._hash_directory_texts("schemas/*.schema.json")

    schema.write_bytes(b'{\r\n  "type": "object"\r\n}\r\n')
    crlf_hash = module._hash_directory_texts("schemas/*.schema.json")

    assert crlf_hash == lf_hash
