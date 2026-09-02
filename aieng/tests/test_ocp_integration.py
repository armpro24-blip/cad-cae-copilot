"""Integration tests for the OCP-based real STEP topology extraction (Phase 7B.2).

All tests in this module are guarded by pytest.importorskip on OCP.STEPControl.
They are skipped in environments where OCP/CadQuery is not installed, so they
do not block core CI that runs without geometry dependencies.

To run these tests locally:
    pip install cadquery
    pytest tests/test_ocp_integration.py -v
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.geometry.step_importer import import_step_package
from aieng.geometry.topology_extractor import TOPOLOGY_MAP_PATH, extract_topology_package

# Guard: skip entire module if OCP is not installed.
pytest.importorskip("OCP.STEPControl", reason="OCP/CadQuery not installed; skipping OCP integration tests")

pytestmark = pytest.mark.geometry


# ---------------------------------------------------------------------------
# Fixture: programmatic minimal STEP file (10x20x30 box)
# ---------------------------------------------------------------------------

def _make_box_step(tmp_path: Path) -> Path:
    """Create a minimal STEP file containing a 10x20x30mm box using OCP."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer
    from OCP.IFSelect import IFSelect_RetDone

    try:
        from OCP.STEPControl import STEPControl_AsIs
    except ImportError:
        from OCP.STEPControl import STEPControl_StepModelType
        STEPControl_AsIs = STEPControl_StepModelType.STEPControl_AsIs

    box = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()
    step_path = tmp_path / "test_box.step"
    writer = STEPControl_Writer()
    writer.Transfer(box, STEPControl_AsIs)
    status = writer.Write(str(step_path))
    if status != IFSelect_RetDone:
        pytest.skip(f"OCP STEP writer failed (status={status}); cannot create fixture")
    return step_path


def _read_topology_map(package_path: Path) -> dict:
    with zipfile.ZipFile(package_path) as zf:
        return json.loads(zf.read(TOPOLOGY_MAP_PATH))


# ---------------------------------------------------------------------------
# Metadata correctness
# ---------------------------------------------------------------------------

def test_ocp_extraction_sets_extraction_backend_occ(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    assert topo["metadata"]["extraction_backend"] == "occ"


def test_ocp_extraction_sets_runtime_provider_ocp(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    assert topo["metadata"]["runtime_provider"] == "OCP"


def test_ocp_extraction_sets_real_step_parsing_true(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    assert topo["metadata"]["real_step_parsing"] is True


def test_ocp_extraction_sets_extraction_mode_parsed(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    assert topo["metadata"]["extraction_mode"] == "parsed_from_step"


def test_ocp_extraction_sets_phase_7b2(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    assert topo["metadata"]["phase"] == "7B.2"


def test_ocp_extraction_includes_limitations(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    assert isinstance(topo["metadata"]["limitations"], list)
    assert len(topo["metadata"]["limitations"]) > 0


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def test_ocp_extraction_produces_entities(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    assert len(topo["entities"]) > 0


def test_ocp_extraction_box_has_at_least_one_solid(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    solids = [e for e in topo["entities"] if e["type"] == "solid"]
    assert len(solids) >= 1


def test_ocp_extraction_box_has_faces(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    faces = [e for e in topo["entities"] if e["type"] == "face"]
    assert len(faces) >= 1


def test_ocp_extraction_box_has_six_faces(tmp_path):
    """A box has exactly 6 planar faces."""
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    faces = [e for e in topo["entities"] if e["type"] == "face"]
    assert len(faces) == 6


def test_ocp_extraction_box_faces_are_planar(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    faces = [e for e in topo["entities"] if e["type"] == "face"]
    for face in faces:
        assert face.get("surface_type") == "plane", f"Expected plane, got {face.get('surface_type')} for {face['id']}"


def test_ocp_extraction_box_faces_have_normals(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    faces = [e for e in topo["entities"] if e["type"] == "face"]
    for face in faces:
        assert "normal" in face, f"face {face['id']} missing normal"
        assert len(face["normal"]) == 3


def test_ocp_extraction_box_faces_have_area(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    faces = [e for e in topo["entities"] if e["type"] == "face"]
    for face in faces:
        assert "area" in face, f"face {face['id']} missing area"
        assert face["area"] > 0


def test_ocp_extraction_box_faces_have_bounding_box(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    faces = [e for e in topo["entities"] if e["type"] == "face"]
    for face in faces:
        assert "bounding_box" in face, f"face {face['id']} missing bounding_box"
        assert len(face["bounding_box"]) == 6


# ---------------------------------------------------------------------------
# ID determinism
# ---------------------------------------------------------------------------

def test_ocp_extraction_entity_ids_are_unique(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    ids = [e["id"] for e in topo["entities"]]
    assert len(ids) == len(set(ids))


def test_ocp_extraction_solid_ids_follow_deterministic_format(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    solids = [e for e in topo["entities"] if e["type"] == "solid"]
    for solid in solids:
        assert solid["id"].startswith("body_")


def test_ocp_extraction_face_ids_follow_deterministic_format(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    faces = [e for e in topo["entities"] if e["type"] == "face"]
    for face in faces:
        assert face["id"].startswith("face_")


def test_ocp_extraction_two_calls_produce_identical_ids(tmp_path):
    step_path = _make_box_step(tmp_path)
    pkg_a = tmp_path / "box_a.aieng"
    pkg_b = tmp_path / "box_b.aieng"
    import_step_package(step_path, pkg_a)
    import_step_package(step_path, pkg_b)
    extract_topology_package(pkg_a, backend="occ")
    extract_topology_package(pkg_b, backend="occ")
    topo_a = _read_topology_map(pkg_a)
    topo_b = _read_topology_map(pkg_b)
    ids_a = sorted(e["id"] for e in topo_a["entities"])
    ids_b = sorted(e["id"] for e in topo_b["entities"])
    assert ids_a == ids_b


# ---------------------------------------------------------------------------
# Schema conformance
# ---------------------------------------------------------------------------

def test_ocp_extraction_output_conforms_to_schema(tmp_path):
    from jsonschema import Draft202012Validator

    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "topology_map.schema.json").read_text(encoding="utf-8"))
    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    extract_topology_package(pkg, backend="occ")
    topo = _read_topology_map(pkg)
    errors = list(Draft202012Validator(schema).iter_errors(topo))
    assert errors == [], f"Schema validation errors: {errors}"


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_extract_topology_occ_exits_zero_with_ocp(tmp_path, capsys):
    from aieng.cli import main

    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    result = main(["extract-topology", str(pkg), "--backend", "occ"])
    assert result == 0


def test_cli_extract_topology_occ_prints_pass(tmp_path, capsys):
    from aieng.cli import main

    step_path = _make_box_step(tmp_path)
    pkg = tmp_path / "box.aieng"
    import_step_package(step_path, pkg)
    main(["extract-topology", str(pkg), "--backend", "occ"])
    output = capsys.readouterr().out
    assert "PASS extracted occ topology" in output
    assert "PASS geometry/topology_map.json written" in output


def test_cli_geometry_backends_shows_ocp_as_experimental(capsys):
    from aieng.cli import main

    main(["geometry-backends"])
    output = capsys.readouterr().out
    assert "occ" in output
    assert "OCP" in output or "experimental" in output or "detected" in output


# ---------------------------------------------------------------------------
# Empty/invalid STEP raises clearly
# ---------------------------------------------------------------------------

EMPTY_STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def test_ocp_extraction_raises_on_geometry_free_step(tmp_path):
    """Minimal valid-format STEP with no geometry should fail with a clear message."""
    step_path = tmp_path / "empty.step"
    step_path.write_bytes(EMPTY_STEP)
    pkg = tmp_path / "empty.aieng"
    import_step_package(step_path, pkg)
    with pytest.raises((ValueError, NotImplementedError)):
        extract_topology_package(pkg, backend="occ")
