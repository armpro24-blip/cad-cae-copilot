"""A package rewrite repairs a legacy stub manifest in passing (#513).

The migration script exists for packages nobody will touch again. Anything still
being worked on should not need it: every CAD write already rewrites the
package, so that rewrite is the natural place to upgrade a manifest that
predates the format library's builder. Self-healing beats a migration everyone
has to remember to run.

These tests exercise the decision function directly — no build123d needed, so
they run in the ordinary CI lanes where the end-to-end conformance ratchet
(which does need the CAD stack) skips itself.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.cad_generation import _new_package_manifest, _upgraded_package_manifest

_LEGACY_STUB = {"schema_version": "0.1", "resources": {"results": {"x": "results/x.json"}}}


def _package(tmp_path: Path, manifest: object | None, name: str = "abc123def456") -> Path:
    package = tmp_path / f"{name}.aieng"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("geometry/source.py", "result = None\n")
        if manifest is not None:
            zf.writestr("manifest.json", json.dumps(manifest))
    return package


def _decoded(payload: bytes | None) -> dict:
    assert payload is not None
    return json.loads(payload.decode("utf-8"))


def test_a_legacy_stub_is_upgraded(tmp_path: Path) -> None:
    manifest = _decoded(_upgraded_package_manifest(_package(tmp_path, _LEGACY_STUB)))

    assert manifest["model_id"] == "abc123def456", "taken from the package filename"
    assert manifest["format_version"]
    assert manifest["units"]["length"] == "mm"
    assert "schema_version" not in manifest
    assert manifest["resources"]["results"]["x"] == "results/x.json", "content survives"


def test_a_conforming_manifest_is_left_alone(tmp_path: Path) -> None:
    """Otherwise every build would churn `created_at` for no reason."""
    good = json.loads(_new_package_manifest(Path("abc123def456.aieng")).decode("utf-8"))
    assert _upgraded_package_manifest(_package(tmp_path, good)) is None


def test_a_package_with_no_manifest_gets_one(tmp_path: Path) -> None:
    manifest = _decoded(_upgraded_package_manifest(_package(tmp_path, None)))
    assert manifest["model_id"] == "abc123def456"


def test_an_unreadable_manifest_does_not_break_the_build(tmp_path: Path) -> None:
    """A rewrite is not the place to fail on someone else's malformed file.

    The build's job is the geometry; a manifest it cannot parse is left exactly
    as it was, and the validator still reports it.
    """
    package = tmp_path / "abc123def456.aieng"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", "{not json")
    assert _upgraded_package_manifest(package) is None

    assert _upgraded_package_manifest(tmp_path / "missing.aieng") is None


def test_a_manifest_that_is_not_an_object_is_left_alone(tmp_path: Path) -> None:
    assert _upgraded_package_manifest(_package(tmp_path, ["not", "a", "dict"])) is None
