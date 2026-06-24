"""Tests for the simulation runner module."""
from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from app.app_factory import create_app
from app.config import Settings
from app.project_io import (
    compute_topology_hash,
    rebind_cae_faces,
    validate_cae_topology_references,
)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _make_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )


def _make_project(settings: Settings, name: str, package: str) -> tuple[str, Path]:
    from app.main import default_project, project_dir, save_project

    project = save_project(settings, default_project(name))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / package
    project["aieng_file"] = package
    save_project(settings, project)
    return project_id, pkg_path


def test_read_member_does_not_enumerate_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.simulation_runner import _read_member

    pkg = tmp_path / "simulation.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("simulation/setup.yaml", b"analysis_type: linear_static\n")

    def fail_namelist(self):
        raise AssertionError("namelist should not be needed for direct member reads")

    monkeypatch.setattr(zipfile.ZipFile, "namelist", fail_namelist)

    assert _read_member(pkg, "simulation/setup.yaml") == b"analysis_type: linear_static\n"


_TOPOLOGY: dict[str, Any] = {
    "entities": [
        {"id": "face_001", "type": "face", "surface_type": "plane",
         "area": 5000.0, "normal": [0, 0, -1], "bounding_box": [0, 0, 0, 100, 50, 0]},
        {"id": "face_002", "type": "face", "surface_type": "plane",
         "area": 5000.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 100, 50, 10]},
        {"id": "face_003", "type": "face", "surface_type": "cylinder",
         "radius": 4.0, "bounding_box": [8, 8, 0, 12, 12, 10]},
    ]
}

_SETUP_YAML: dict[str, Any] = {
    "schema_version": "0.1",
    "ai_generated": True,
    "analysis_type": "static_structural",
    "material_name": "Al6061-T6",
    "material_reason": "lightweight bracket material",
    "materials": {
        "Al6061-T6": {
            "youngs_modulus_mpa": 69000,
            "poisson_ratio": 0.33,
            "density_kg_m3": 2700,
            "yield_strength_mpa": 276,
        }
    },
    "boundary_conditions": [
        {"id": "bc_001", "target_feature": "feat_hole_001", "type": "fixed", "reason": "bolted"},
    ],
    "loads": [
        {"id": "load_001", "target_feature": "feat_base_001", "type": "force",
         "value_n": 500.0, "direction": [0.0, 0.0, -1.0], "reason": "downward"},
    ],
    "mesh": {"target_size_mm": 2.5, "refinement_note": ""},
    "assumptions": [],
    "warnings": [],
}

_CAE_MAPPING: dict[str, Any] = {
    "schema_version": "0.1",
    "ai_generated": True,
    "mappings": [
        {
            "cae_entity": "FEAT_HOLE_001",
            "maps_to": {"feature_id": "feat_hole_001", "role": "fixed_support"},
            "face_ids": ["face_003"],
        },
        {
            "cae_entity": "FEAT_BASE_001_L",
            "maps_to": {"feature_id": "feat_base_001", "role": "load_application"},
            "face_ids": ["face_002"],
        },
    ],
}

_FAKE_STEP = b"ISO-10303-21;DATA;END-ISO-10303-21;"


def _build_test_package(pkg_path: Path, *, include_step: bool = True) -> None:
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1"}))
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("simulation/setup.yaml", yaml.dump(_SETUP_YAML))
        zf.writestr("simulation/cae_mapping.json", json.dumps(_CAE_MAPPING))
        if include_step:
            zf.writestr("geometry/generated.step", _FAKE_STEP)


# ── mesh preview helpers ──────────────────────────────────────────────────────

_TWO_TET_MESH_INP = """\
*Node
1, 0.0, 0.0, 0.0
2, 10.0, 0.0, 0.0
3, 5.0, 10.0, 0.0
4, 5.0, 5.0, 10.0
5, 15.0, 5.0, 10.0
*Element, type=C3D4, ELSET=EALL
1, 1, 2, 3, 4
2, 2, 3, 4, 5
"""


def test_parse_inp_elements_c3d4() -> None:
    from app.simulation_runner import _parse_inp_elements

    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "mesh.inp"
        inp.write_text(_MINIMAL_MESH_INP)
        elements = _parse_inp_elements(inp)

    assert len(elements) == 1
    assert elements[0]["id"] == 1
    assert elements[0]["type"] == "C3D4"
    assert elements[0]["nodes"] == [1, 2, 3, 4]


def test_extract_surface_wireframe_two_tets() -> None:
    from app.simulation_runner import _parse_inp_nodes, _parse_inp_elements, _extract_surface_wireframe

    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "mesh.inp"
        inp.write_text(_TWO_TET_MESH_INP)
        nodes = _parse_inp_nodes(inp)
        elements = _parse_inp_elements(inp)

    coords, edges = _extract_surface_wireframe(nodes, elements)
    # Five nodes are all on the surface of this two-tet strip.
    assert len(coords) == 5
    # Each tet has 4 triangular faces; the shared face is internal.  Surface
    # faces = 4 + 4 - 2 = 6 triangles → 6 * 3 / 2 (each edge shared by 2 tris)
    # = 9 unique edges.
    assert len(edges) == 9
    # Coordinates are in model frame (mm).
    assert [5.0, 5.0, 10.0] in coords


def test_get_mesh_preview_unavailable() -> None:
    from app.simulation_runner import get_mesh_preview

    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "empty.aieng"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("manifest.json", "{}")
        result = get_mesh_preview(pkg)

    assert result["available"] is False


def test_get_mesh_preview_available() -> None:
    from app.simulation_runner import get_mesh_preview

    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "mesh.aieng"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", "{}")
            zf.writestr("simulation/mesh.inp", _TWO_TET_MESH_INP)
            zf.writestr("simulation/setup.yaml", yaml.dump(_SETUP_YAML))
        result = get_mesh_preview(pkg)

    assert result["available"] is True
    assert result["node_count"] == 5
    assert result["element_count"] == 2
    assert result["element_type"] == "C3D4"
    assert result["target_size_mm"] == 2.5
    assert result["quality"]["coarse_flag"] is True
    assert len(result["nodes"]) == 5
    assert len(result["edges"]) == 9


# ── check_simulation_tools ────────────────────────────────────────────────────

def test_check_tools_structure() -> None:
    from app.simulation_runner import check_simulation_tools

    result = check_simulation_tools()
    assert "gmsh" in result
    assert "calculix" in result
    assert "ready" in result
    assert "missing" in result
    assert isinstance(result["missing"], list)


def test_check_tools_ready_only_when_both_present() -> None:
    from app.simulation_runner import check_simulation_tools

    with patch("app.simulation_runner._gmsh_available", return_value=True), \
         patch("app.simulation_runner._find_ccx", return_value="/usr/bin/ccx"):
        result = check_simulation_tools()
        assert result["ready"] is True
        assert result["missing"] == []

    with patch("app.simulation_runner._gmsh_available", return_value=False), \
         patch("app.simulation_runner._find_ccx", return_value=None):
        result = check_simulation_tools()
        assert result["ready"] is False
        assert set(result["missing"]) == {"gmsh", "ccx"}


# ── _nodes_on_face ────────────────────────────────────────────────────────────

def test_nodes_on_planar_face() -> None:
    from app.simulation_runner import _nodes_on_face

    nodes = {
        1: (10.0, 10.0, 10.0),   # on top face (z=10)
        2: (50.0, 25.0, 10.0),   # on top face
        3: (50.0, 25.0, 5.0),    # mid-body — not on top
        4: (0.0, 0.0, 0.0),      # on bottom face (z=0)
    }
    top_face = {"type": "face", "surface_type": "plane",
                "bounding_box": [0, 0, 10, 100, 50, 10]}
    on_top = _nodes_on_face(nodes, top_face)
    assert 1 in on_top
    assert 2 in on_top
    assert 3 not in on_top
    assert 4 not in on_top


def test_nodes_on_cylinder_face() -> None:
    from app.simulation_runner import _nodes_on_face
    import math

    r = 4.0
    cx, cy = 10.0, 10.0
    nodes = {
        1: (cx + r, cy, 5.0),          # on cylinder surface
        2: (cx, cy + r, 3.0),          # on cylinder surface
        3: (cx, cy, 5.0),              # at center — NOT on surface
        4: (cx + r + 2, cy, 5.0),      # outside
    }
    cyl_face = {"type": "face", "surface_type": "cylinder", "radius": r,
                "bounding_box": [8, 8, 0, 12, 12, 10]}
    on_cyl = _nodes_on_face(nodes, cyl_face)
    assert 1 in on_cyl
    assert 2 in on_cyl
    assert 3 not in on_cyl
    assert 4 not in on_cyl


def test_nodes_on_face_empty_bbox() -> None:
    from app.simulation_runner import _nodes_on_face

    nodes = {1: (0.0, 0.0, 0.0)}
    result = _nodes_on_face(nodes, {"surface_type": "plane", "bounding_box": []})
    assert result == []


# ── _build_nsets ──────────────────────────────────────────────────────────────

def test_build_nsets_maps_cylinder_face() -> None:
    from app.simulation_runner import _build_nsets
    import math

    r = 4.0
    cx, cy = 10.0, 10.0
    nodes = {
        1: (cx + r, cy, 5.0),
        2: (cx, cy + r, 3.0),
        3: (50.0, 25.0, 10.0),  # top face node
    }
    nsets = _build_nsets(nodes, _TOPOLOGY, _CAE_MAPPING)
    # face_003 is a cylinder → FEAT_HOLE_001 NSET
    assert "FEAT_HOLE_001" in nsets
    assert 1 in nsets["FEAT_HOLE_001"]
    assert 2 in nsets["FEAT_HOLE_001"]
    assert 3 not in nsets["FEAT_HOLE_001"]


def test_build_nsets_empty_when_no_topology() -> None:
    from app.simulation_runner import _build_nsets

    nodes = {1: (0.0, 0.0, 0.0)}
    nsets = _build_nsets(nodes, {}, _CAE_MAPPING)
    # no topology entities → all NSETs are empty lists
    for node_list in nsets.values():
        assert node_list == []


# ── _unresolved_bc_load_faces (fail-fast on empty mapped NSETs) ────────────────

def test_unresolved_bc_load_faces_flags_empty_mapped_nset() -> None:
    from app.simulation_runner import _unresolved_bc_load_faces

    # bc target's NSET (FEAT_HOLE_001) caught no nodes; load target is fine.
    nsets = {"FEAT_HOLE_001": [], "FEAT_BASE_001_L": [3, 4]}
    problems = _unresolved_bc_load_faces(_SETUP_YAML, _CAE_MAPPING, nsets)
    assert len(problems) == 1
    assert problems[0]["kind"] == "boundary_condition"
    assert problems[0]["cae_entity"] == "FEAT_HOLE_001"
    assert problems[0]["face_pointers"] == ["@face:face_003"]


def test_unresolved_bc_load_faces_none_when_all_resolved() -> None:
    from app.simulation_runner import _unresolved_bc_load_faces

    nsets = {"FEAT_HOLE_001": [5], "FEAT_BASE_001_L": [4]}
    assert _unresolved_bc_load_faces(_SETUP_YAML, _CAE_MAPPING, nsets) == []


# ── _build_calculix_deck ──────────────────────────────────────────────────────

_MINIMAL_MESH_INP = """\
*Node
1, 0.0, 0.0, 0.0
2, 10.0, 0.0, 0.0
3, 5.0, 10.0, 0.0
4, 5.0, 5.0, 10.0
5, 14.0, 10.0, 5.0
*Element, type=C3D4, ELSET=EALL
1, 1, 2, 3, 4
"""
# Node 4 lands on the top face (face_002 → load NSET); node 5 on the cylinder
# surface (face_003 → fixed-support NSET) so the full-mock run resolves both
# the load and the BC and reaches the solver (see fail-fast on empty mappings).


def test_build_calculix_deck_structure() -> None:
    from app.simulation_runner import _build_calculix_deck

    nsets = {
        "FEAT_HOLE_001": [1, 2],
        "FEAT_BASE_001_L": [3, 4],
    }
    deck, bc_count, load_count = _build_calculix_deck(_MINIMAL_MESH_INP, _SETUP_YAML, nsets, _CAE_MAPPING)

    assert "*MATERIAL" in deck
    assert "Al6061_T6" in deck
    assert "*ELASTIC" in deck
    assert "*SOLID SECTION" in deck
    assert "*STEP" in deck
    assert "*STATIC" in deck
    assert "*END STEP" in deck
    assert "*NODE FILE" in deck
    assert "*EL FILE" in deck
    assert bc_count == 1
    assert load_count == 1


def test_build_calculix_deck_nset_definitions() -> None:
    from app.simulation_runner import _build_calculix_deck

    nsets = {"FEAT_HOLE_001": [10, 20, 30]}
    deck, _, _ = _build_calculix_deck(_MINIMAL_MESH_INP, _SETUP_YAML, nsets, _CAE_MAPPING)

    assert "NSET=FEAT_HOLE_001" in deck
    assert "10, 20, 30" in deck


def test_build_calculix_deck_no_bc_when_nset_empty() -> None:
    from app.simulation_runner import _build_calculix_deck

    nsets = {"FEAT_HOLE_001": [], "FEAT_BASE_001_L": []}
    _, bc_count, load_count = _build_calculix_deck(_MINIMAL_MESH_INP, _SETUP_YAML, nsets, _CAE_MAPPING)

    assert bc_count == 0
    assert load_count == 0


# ── endpoint: requires confirmed=true ─────────────────────────────────────────

def test_run_simulation_requires_confirmed(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    project_id, pkg_path = _make_project(settings, "sim-test", "sim.aieng")
    _build_test_package(pkg_path)

    resp = client.post(f"/api/projects/{project_id}/run-simulation", json={})
    assert resp.status_code == 400
    assert "confirmed" in resp.text


# ── endpoint: missing package ─────────────────────────────────────────────────

def test_run_simulation_missing_package(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    from app.main import default_project, save_project
    project = save_project(settings, default_project("no-pkg"))
    project_id = project["id"]

    resp = client.post(
        f"/api/projects/{project_id}/run-simulation",
        json={"confirmed": True},
    )
    assert resp.status_code == 404


# ── endpoint: tools unavailable ───────────────────────────────────────────────

def test_run_simulation_tools_unavailable(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)

    project_id, pkg_path = _make_project(settings, "sim-notools", "sim_notools.aieng")
    _build_test_package(pkg_path)

    with patch("app.simulation_runner._gmsh_available", return_value=False), \
         patch("app.simulation_runner._find_ccx", return_value=None):
        resp = client.post(
            f"/api/projects/{project_id}/run-simulation",
            json={"confirmed": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "tools_unavailable"
    assert "gmsh" in data["missing_tools"]
    assert "ccx" in data["missing_tools"]


# ── endpoint: missing setup.yaml ─────────────────────────────────────────────

def test_run_simulation_missing_setup_yaml(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    project_id, pkg_path = _make_project(settings, "sim-nosetup", "sim_nosetup.aieng")
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("geometry/generated.step", _FAKE_STEP)

    with patch("app.simulation_runner._gmsh_available", return_value=True), \
         patch("app.simulation_runner._find_ccx", return_value="/usr/bin/ccx"):
        resp = client.post(
            f"/api/projects/{project_id}/run-simulation",
            json={"confirmed": True},
        )

    assert resp.status_code == 422
    assert "setup.yaml" in resp.text


# ── endpoint: full mock run ───────────────────────────────────────────────────

_MOCK_FRD_METRICS = {
    "schema_version": "0.1",
    "metrics_source": "CalculiX",
    "load_cases": {
        "load_case_001": {
            "max_displacement_mm": 0.123,
            "max_von_mises_stress_mpa": 45.6,
        }
    },
    "warnings": [],
}


def _fake_mesh(step_path, work_dir, mesh_size_mm):
    """Write a minimal valid .inp mesh file without running Gmsh."""
    out = work_dir / "mesh.inp"
    out.write_text(_MINIMAL_MESH_INP)
    return out


def _fake_calculix(inp_path, work_dir, timeout=180):
    """Pretend CalculiX succeeded and create a fake .frd file."""
    frd = work_dir / f"{inp_path.stem}.frd"
    frd.write_bytes(b"fake frd content")
    return 0, "CalculiX completed\n", frd


def test_run_simulation_full_mock(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)

    project_id, pkg_path = _make_project(settings, "sim-full", "sim_full.aieng")
    _build_test_package(pkg_path)

    with patch("app.simulation_runner._gmsh_available", return_value=True), \
         patch("app.simulation_runner._find_ccx", return_value="/usr/bin/ccx"), \
         patch("app.simulation_runner._mesh_with_gmsh", side_effect=_fake_mesh), \
         patch("app.simulation_runner._run_calculix", side_effect=_fake_calculix), \
         patch("app.simulation_runner._extract_metrics", return_value=_MOCK_FRD_METRICS):
        resp = client.post(
            f"/api/projects/{project_id}/run-simulation",
            json={"confirmed": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["von_mises_max_mpa"] == pytest.approx(45.6)
    assert data["displacement_max_mm"] == pytest.approx(0.123)
    assert "simulation/solver_log.txt" in data["written_artifacts"]
    assert "simulation/result.frd" in data["written_artifacts"]
    assert "simulation/results_summary.json" in data["written_artifacts"]

    # Verify artifacts written to package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
        assert "simulation/solver_log.txt" in names
        assert "simulation/results_summary.json" in names
        assert "simulation/result.frd" in names
        summary = json.loads(zf.read("simulation/results_summary.json"))
        assert summary["status"] == "success"
        assert summary["von_mises_max_mpa"] == pytest.approx(45.6)


def _fake_mesh_missing_hole(step_path, work_dir, mesh_size_mm):
    """Mesh that covers the top (load) face but NOT the cylinder (BC) face."""
    out = work_dir / "mesh.inp"
    out.write_text(
        "*Node\n"
        "1, 0.0, 0.0, 0.0\n"
        "4, 5.0, 5.0, 10.0\n"   # on top face → load NSET resolves; nothing on hole
        "*Element, type=C3D4, ELSET=EALL\n"
        "1, 1, 1, 1, 4\n"
    )
    return out


def test_run_simulation_aborts_on_unresolved_face(tmp_path: Path) -> None:
    """A load/BC face that matches zero mesh nodes aborts before the solver."""
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    project_id, pkg_path = _make_project(settings, "sim-unresolved", "sim_unresolved.aieng")
    _build_test_package(pkg_path)

    with patch("app.simulation_runner._gmsh_available", return_value=True), \
         patch("app.simulation_runner._find_ccx", return_value="/usr/bin/ccx"), \
         patch("app.simulation_runner._mesh_with_gmsh", side_effect=_fake_mesh_missing_hole), \
         patch("app.simulation_runner._run_calculix", side_effect=_fake_calculix) as ccx, \
         patch("app.simulation_runner._extract_metrics", return_value=_MOCK_FRD_METRICS):
        resp = client.post(
            f"/api/projects/{project_id}/run-simulation",
            json={"confirmed": True},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "unresolved_face_mapping"
    assert any("@face:face_003" in p["face_pointers"] for p in detail["unresolved"])
    # The solver must NOT have been invoked.
    ccx.assert_not_called()


# ── topology reference validation ─────────────────────────────────────────────

def _build_test_package_with_hash(
    pkg_path: Path,
    *,
    include_step: bool = True,
    topology_hash: str | None = None,
    cae_mapping: dict[str, Any] | None = None,
) -> None:
    """Build a package with an optional recorded topology hash."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    mapping = dict(_CAE_MAPPING)
    setup = dict(_SETUP_YAML)
    if topology_hash is not None:
        mapping["topology_hash"] = topology_hash
        setup["topology_hash"] = topology_hash
    if cae_mapping is not None:
        mapping = cae_mapping
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1"}))
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("simulation/setup.yaml", yaml.dump(setup))
        zf.writestr("simulation/cae_mapping.json", json.dumps(mapping))
        if include_step:
            zf.writestr("geometry/generated.step", _FAKE_STEP)


def test_compute_topology_hash_is_stable() -> None:
    h1 = compute_topology_hash(_TOPOLOGY)
    h2 = compute_topology_hash(_TOPOLOGY)
    assert h1 is not None
    assert h1 == h2
    # Mutating the topology changes the hash.
    changed = dict(_TOPOLOGY)
    changed["entities"] = _TOPOLOGY["entities"][:-1]
    assert compute_topology_hash(changed) != h1


def test_validate_topology_references_valid_without_hash(tmp_path: Path) -> None:
    project_id, pkg_path = _make_project(_make_settings(tmp_path), "valid-no-hash", "valid_no_hash.aieng")
    _build_test_package(pkg_path)
    result = validate_cae_topology_references(pkg_path)
    assert result["topology_available"] is True
    assert result["hash_status"] == "missing_hash"
    assert result["valid"] is True
    assert result["missing_face_ids"] == []


def test_validate_topology_references_detects_hash_mismatch(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    project_id, pkg_path = _make_project(settings, "hash-mismatch", "hash_mismatch.aieng")
    _build_test_package_with_hash(pkg_path, topology_hash="deadbeef")
    result = validate_cae_topology_references(pkg_path)
    assert result["hash_status"] == "mismatch"
    assert result["valid"] is False
    assert result["topology_hash_expected"] == "deadbeef"
    assert result["topology_hash_current"] == compute_topology_hash(_TOPOLOGY)


def test_validate_topology_references_detects_missing_face(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    project_id, pkg_path = _make_project(settings, "missing-face", "missing_face.aieng")
    bad_mapping = dict(_CAE_MAPPING)
    bad_mapping["mappings"] = [
        dict(_CAE_MAPPING["mappings"][0]),
        {
            "cae_entity": "FEAT_GONE_L",
            "maps_to": {"feature_id": "feat_gone", "role": "load_application"},
            "face_ids": ["face_999"],
        },
    ]
    _build_test_package_with_hash(pkg_path, cae_mapping=bad_mapping)
    result = validate_cae_topology_references(pkg_path)
    assert result["hash_status"] == "missing_hash"
    assert result["valid"] is False
    assert "face_999" in result["missing_face_ids"]
    assert any(r["face_id"] == "face_999" for r in result["stale_references"])


def test_run_simulation_aborts_on_topology_hash_mismatch(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    project_id, pkg_path = _make_project(settings, "sim-stale", "sim_stale.aieng")
    _build_test_package_with_hash(pkg_path, topology_hash="deadbeef")

    with patch("app.simulation_runner._gmsh_available", return_value=True), \
         patch("app.simulation_runner._find_ccx", return_value="/usr/bin/ccx"), \
         patch("app.simulation_runner._mesh_with_gmsh", side_effect=_fake_mesh), \
         patch("app.simulation_runner._run_calculix", side_effect=_fake_calculix) as ccx:
        resp = client.post(
            f"/api/projects/{project_id}/run-simulation",
            json={"confirmed": True},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "stale_topology_references"
    ccx.assert_not_called()


def test_run_simulation_aborts_on_missing_face(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    project_id, pkg_path = _make_project(settings, "sim-missing-face", "sim_missing_face.aieng")
    bad_mapping = dict(_CAE_MAPPING)
    bad_mapping["mappings"] = [
        dict(_CAE_MAPPING["mappings"][0]),
        {
            "cae_entity": "FEAT_GONE_L",
            "maps_to": {"feature_id": "feat_gone", "role": "load_application"},
            "face_ids": ["face_999"],
        },
    ]
    _build_test_package_with_hash(pkg_path, cae_mapping=bad_mapping)

    with patch("app.simulation_runner._gmsh_available", return_value=True), \
         patch("app.simulation_runner._find_ccx", return_value="/usr/bin/ccx"), \
         patch("app.simulation_runner._mesh_with_gmsh", side_effect=_fake_mesh), \
         patch("app.simulation_runner._run_calculix", side_effect=_fake_calculix) as ccx:
        resp = client.post(
            f"/api/projects/{project_id}/run-simulation",
            json={"confirmed": True},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "stale_topology_references"
    assert "face_999" in detail["topology_validation"]["missing_face_ids"]
    ccx.assert_not_called()


# ── GET /api/simulation/tools ─────────────────────────────────────────────────

def test_get_simulation_tools_endpoint(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get("/api/simulation/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "gmsh" in data
    assert "calculix" in data
    assert "ready" in data
    assert "missing" in data


# ── GET /api/projects/{project_id}/mesh-preview ───────────────────────────────

def test_mesh_preview_endpoint_missing_package(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    from app.main import default_project, save_project
    project = save_project(settings, default_project("mesh-preview-no-pkg"))
    project_id = project["id"]

    resp = client.get(f"/api/projects/{project_id}/mesh-preview")
    assert resp.status_code == 404


def test_mesh_preview_endpoint_no_mesh(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project_id, pkg_path = _make_project(settings, "mesh-preview-no-mesh", "no_mesh.aieng")
    _build_test_package(pkg_path)

    resp = client.get(f"/api/projects/{project_id}/mesh-preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False


def test_mesh_preview_endpoint_with_mesh(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project_id, pkg_path = _make_project(settings, "mesh-preview", "mesh_preview.aieng")
    _build_test_package(pkg_path)
    with zipfile.ZipFile(pkg_path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("simulation/mesh.inp", _TWO_TET_MESH_INP)

    resp = client.get(f"/api/projects/{project_id}/mesh-preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["node_count"] == 5
    assert data["element_count"] == 2
    assert data["element_type"] == "C3D4"
    assert data["target_size_mm"] == 2.5
    assert len(data["nodes"]) == 5
    assert len(data["edges"]) == 9


# ── mesh quality diagnostics (#279) ──────────────────────────────────────────

def test_compute_mesh_quality_good_tet_is_ok() -> None:
    from app.simulation_runner import compute_mesh_quality

    nodes = {1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0), 3: (0.0, 1.0, 0.0), 4: (0.0, 0.0, 1.0)}
    elements = [{"id": 1, "type": "C3D4", "nodes": [1, 2, 3, 4]}]

    q = compute_mesh_quality(nodes, elements)
    assert q["verdict"] == "ok"
    assert q["tet_count"] == 1
    assert q["degenerate_element_count"] == 0
    assert q["poor_element_count"] == 0
    assert q["max_aspect_ratio"] < 2.0


def test_compute_mesh_quality_degenerate_tet_fails() -> None:
    from app.simulation_runner import compute_mesh_quality

    # Four coplanar nodes (z=0) → zero volume → degenerate element.
    nodes = {1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0), 3: (0.0, 1.0, 0.0), 4: (1.0, 1.0, 0.0)}
    elements = [{"id": 7, "type": "C3D4", "nodes": [1, 2, 3, 4]}]

    q = compute_mesh_quality(nodes, elements)
    assert q["verdict"] == "fail"
    assert q["degenerate_element_count"] == 1
    assert 7 in q["degenerate_element_ids"]


def test_compute_mesh_quality_missing_node_is_broken() -> None:
    from app.simulation_runner import compute_mesh_quality

    nodes = {1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0), 3: (0.0, 1.0, 0.0)}  # node 4 absent
    elements = [{"id": 3, "type": "C3D4", "nodes": [1, 2, 3, 4]}]

    q = compute_mesh_quality(nodes, elements)
    assert q["verdict"] == "fail"
    assert q["broken_element_count"] == 1


def test_compute_mesh_quality_non_tet_is_unknown() -> None:
    from app.simulation_runner import compute_mesh_quality

    nodes = {i: (float(i), 0.0, 0.0) for i in range(1, 9)}
    elements = [{"id": 1, "type": "C3D8", "nodes": list(range(1, 9))}]

    q = compute_mesh_quality(nodes, elements)
    assert q["verdict"] == "unknown"
    assert q["tet_count"] == 0
    assert "C3D8" in q["unsupported_element_types"]


def test_mesh_diagnostics_endpoint_with_mesh(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project_id, pkg_path = _make_project(settings, "mesh-diag", "mesh_diag.aieng")
    _build_test_package(pkg_path)
    with zipfile.ZipFile(pkg_path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("simulation/mesh.inp", _TWO_TET_MESH_INP)

    resp = client.get(f"/api/projects/{project_id}/mesh-diagnostics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["verdict"] in {"ok", "warning", "fail"}
    assert data["tet_count"] == 2


def test_mesh_diagnostics_endpoint_no_mesh(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project_id, pkg_path = _make_project(settings, "mesh-diag-no-mesh", "mesh_diag_none.aieng")
    _build_test_package(pkg_path)

    resp = client.get(f"/api/projects/{project_id}/mesh-diagnostics")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


# ── surface-set coverage (#279 D3, the set-mapping half) ──────────────────────

def _coverage_topology():
    return {"entities": [
        {"id": "f_bottom", "type": "face", "surface_type": "plane",
         "normal": [0.0, 0.0, -1.0], "bounding_box": [0.0, 0.0, 0.0, 10.0, 10.0, 0.0]},
        {"id": "f_top", "type": "face", "surface_type": "plane",
         "normal": [0.0, 0.0, 1.0], "bounding_box": [0.0, 0.0, 100.0, 10.0, 10.0, 100.0]},
    ]}


def test_compute_set_coverage_resolved_is_ok() -> None:
    from app.simulation_runner import compute_set_coverage

    nodes = {1: (1.0, 1.0, 0.0), 2: (9.0, 1.0, 0.0), 3: (1.0, 9.0, 0.0), 4: (9.0, 9.0, 0.0)}
    cae_mapping = {"mappings": [{"cae_entity": "fixed_support", "face_ids": ["f_bottom"]}]}

    cov = compute_set_coverage(nodes, _coverage_topology(), cae_mapping)
    assert cov["verdict"] == "ok"
    fs = next(s for s in cov["sets"] if s["cae_entity"] == "fixed_support")
    assert fs["status"] == "ok"
    assert fs["resolved_node_count"] == 4


def test_compute_set_coverage_empty_set_fails() -> None:
    from app.simulation_runner import compute_set_coverage

    # All nodes are at z=0, but the load is bound to f_top (z=100) -> 0 nodes.
    nodes = {1: (1.0, 1.0, 0.0), 2: (9.0, 1.0, 0.0), 3: (1.0, 9.0, 0.0)}
    cae_mapping = {"mappings": [{"cae_entity": "pressure_load", "face_ids": ["f_top"]}]}

    cov = compute_set_coverage(nodes, _coverage_topology(), cae_mapping)
    assert cov["verdict"] == "fail"
    load = next(s for s in cov["sets"] if s["cae_entity"] == "pressure_load")
    assert load["status"] == "empty"
    assert cov["empty_set_count"] == 1


def test_compute_set_coverage_unresolved_face_fails() -> None:
    from app.simulation_runner import compute_set_coverage

    nodes = {1: (1.0, 1.0, 0.0), 2: (9.0, 1.0, 0.0), 3: (1.0, 9.0, 0.0)}
    cae_mapping = {"mappings": [{"cae_entity": "bc", "face_ids": ["f_ghost"]}]}

    cov = compute_set_coverage(nodes, _coverage_topology(), cae_mapping)
    assert cov["verdict"] == "fail"
    bc = next(s for s in cov["sets"] if s["cae_entity"] == "bc")
    assert bc["status"] == "unresolved_face"
    assert "f_ghost" in bc["missing_face_ids"]


def test_compute_set_coverage_no_mappings_is_unknown() -> None:
    from app.simulation_runner import compute_set_coverage

    cov = compute_set_coverage({}, _coverage_topology(), {"mappings": []})
    assert cov["verdict"] == "unknown"


def test_mesh_diagnostics_endpoint_flags_broken_set_mapping(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project_id, pkg_path = _make_project(settings, "mesh-diag-set", "mesh_diag_set.aieng")
    _build_test_package(pkg_path)
    # Mesh nodes are near the origin (from _TWO_TET_MESH_INP); bind a load to a
    # face far away so it resolves to zero nodes -> broken surface-set mapping.
    topology = {"entities": [
        {"id": "f_far", "type": "face", "surface_type": "plane",
         "normal": [0.0, 0.0, 1.0], "bounding_box": [1000.0, 1000.0, 1000.0, 1010.0, 1010.0, 1000.0]},
    ]}
    cae_mapping = {"mappings": [{"cae_entity": "load_1", "face_ids": ["f_far"]}]}
    with zipfile.ZipFile(pkg_path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("simulation/mesh.inp", _TWO_TET_MESH_INP)
        zf.writestr("geometry/topology_map.json", json.dumps(topology))
        zf.writestr("simulation/cae_mapping.json", json.dumps(cae_mapping))

    resp = client.get(f"/api/projects/{project_id}/mesh-diagnostics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["set_coverage"]["verdict"] == "fail"
    assert data["overall_verdict"] == "fail"


# ── streaming endpoint (run_simulation_stream) ────────────────────────────────
#
# These pin the streaming path, which previously had no coverage and had
# drifted from the sync path (notably it skipped the stale-topology guard).
# Both REST entry points now share _run_simulation_core, so these assert parity.

def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    """Parse `data: {json}` lines out of a buffered SSE response body."""
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def test_run_simulation_stream_aborts_on_topology_hash_mismatch(tmp_path: Path) -> None:
    """Parity guard: the streaming path must enforce the stale-topology check
    that the sync path enforces — previously it did not, so it could solve
    against stale face references and stream a wrong result as success."""
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    project_id, pkg_path = _make_project(settings, "sim-stream-stale", "sim_stream_stale.aieng")
    _build_test_package_with_hash(pkg_path, topology_hash="deadbeef")

    with patch("app.simulation_runner._gmsh_available", return_value=True), \
         patch("app.simulation_runner._find_ccx", return_value="/usr/bin/ccx"), \
         patch("app.simulation_runner._mesh_with_gmsh", side_effect=_fake_mesh), \
         patch("app.simulation_runner._run_calculix", side_effect=_fake_calculix) as ccx:
        resp = client.post(
            f"/api/projects/{project_id}/run-simulation-stream",
            json={"confirmed": True},
        )

    events = _parse_sse_events(resp.text)
    error_events = [e for e in events if e["step"] == "error"]
    assert error_events, f"expected a stale-topology error event, got {events}"
    assert error_events[-1]["code"] == "stale_topology_references"
    # The solver must NOT have been invoked.
    ccx.assert_not_called()


def test_run_simulation_stream_full_mock(tmp_path: Path) -> None:
    """Streaming success path emits progress steps then a terminal done event
    carrying the same result shape as the sync path."""
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)

    project_id, pkg_path = _make_project(settings, "sim-stream-full", "sim_stream_full.aieng")
    _build_test_package(pkg_path)

    with patch("app.simulation_runner._gmsh_available", return_value=True), \
         patch("app.simulation_runner._find_ccx", return_value="/usr/bin/ccx"), \
         patch("app.simulation_runner._mesh_with_gmsh", side_effect=_fake_mesh), \
         patch("app.simulation_runner._run_calculix", side_effect=_fake_calculix), \
         patch("app.simulation_runner._extract_metrics", return_value=_MOCK_FRD_METRICS):
        resp = client.post(
            f"/api/projects/{project_id}/run-simulation-stream",
            json={"confirmed": True},
        )

    events = _parse_sse_events(resp.text)
    steps = [e["step"] for e in events]
    assert "meshing" in steps
    assert "solving" in steps
    done = [e for e in events if e["step"] == "done"]
    assert done, f"expected a done event, got {steps}"
    result = done[-1]["result"]
    assert result["status"] == "success"
    assert result["von_mises_max_mpa"] == pytest.approx(45.6)
    assert "simulation/results_summary.json" in result["written_artifacts"]

    # Artifacts were written to the package, same as the sync path.
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "simulation/results_summary.json" in zf.namelist()


# ── adaptive face rebind tests ────────────────────────────────────────────────

def _build_package_from_topology(
    pkg_path: Path,
    topology: dict[str, Any],
    *,
    cae_mapping: dict[str, Any] | None = None,
    setup: dict[str, Any] | None = None,
    include_step: bool = True,
) -> None:
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    mapping = cae_mapping if cae_mapping is not None else dict(_CAE_MAPPING)
    setup_doc = setup if setup is not None else dict(_SETUP_YAML)
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1"}))
        zf.writestr("geometry/topology_map.json", json.dumps(topology))
        zf.writestr("simulation/setup.yaml", yaml.dump(setup_doc))
        zf.writestr("simulation/cae_mapping.json", json.dumps(mapping))
        if include_step:
            zf.writestr("geometry/generated.step", _FAKE_STEP)


def test_rebind_cae_faces_maps_planes_after_geometry_change(tmp_path: Path) -> None:
    old_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 100, 50, 10]},
            {"id": "face_load", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 5000.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 100, 50, 10]},
            {"id": "face_hole", "type": "face", "surface_type": "cylinder", "body_id": "body_1",
             "radius": 4.0, "normal": [0, 0, 1], "bounding_box": [8, 8, 0, 12, 12, 10]},
        ]
    }
    # Same bracket scaled in X; face IDs renamed.
    new_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 120, 50, 10]},
            {"id": "face_load_v2", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 6000.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 120, 50, 10]},
            {"id": "face_hole_v2", "type": "face", "surface_type": "cylinder", "body_id": "body_1",
             "radius": 4.0, "normal": [0, 0, 1], "bounding_box": [8, 8, 0, 12, 12, 10]},
        ]
    }
    old_mapping = {
        "mappings": [
            {"cae_entity": "LOAD", "maps_to": {"feature_id": "load"}, "face_ids": ["face_load"]},
            {"cae_entity": "FIX", "maps_to": {"feature_id": "hole"}, "face_ids": ["face_hole"]},
        ]
    }
    report = rebind_cae_faces(old_mapping, old_topology, new_topology)
    assert report["all_resolved"] is True
    assert not report["unresolved_face_ids"]
    new_mapping = report["cae_mapping"]
    assert "face_load_v2" in new_mapping["mappings"][0]["face_ids"]
    assert "face_hole_v2" in new_mapping["mappings"][1]["face_ids"]
    assert new_mapping["topology_hash"] == compute_topology_hash(new_topology)


def test_rebind_cae_faces_returns_independent_mapping_copy() -> None:
    old_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 100, 50, 10]},
            {"id": "face_load", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 5000.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 100, 50, 10]},
        ]
    }
    new_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 120, 50, 10]},
            {"id": "face_load_v2", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 6000.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 120, 50, 10]},
        ]
    }
    old_mapping = {
        "mappings": [
            {"cae_entity": "LOAD", "maps_to": {"feature_id": "load"}, "face_ids": ["face_load"]},
        ]
    }

    report = rebind_cae_faces(old_mapping, old_topology, new_topology)

    assert report["cae_mapping"] is not old_mapping
    assert report["cae_mapping"]["mappings"][0] is not old_mapping["mappings"][0]
    assert old_mapping == {
        "mappings": [
            {"cae_entity": "LOAD", "maps_to": {"feature_id": "load"}, "face_ids": ["face_load"]},
        ]
    }


def test_rebind_cae_faces_rejects_ambiguous_match() -> None:
    old_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 100, 50, 10]},
            {"id": "face_old", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 100.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 10, 10, 10]},
        ]
    }
    new_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 100, 50, 10]},
            {"id": "face_a", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 100.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 10, 10, 10]},
            {"id": "face_b", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 100.0, "normal": [0, 0, 1], "bounding_box": [0.1, 0.1, 10, 10.1, 10.1, 10]},
        ]
    }
    old_mapping = {"mappings": [{"cae_entity": "X", "maps_to": {"feature_id": "x"}, "face_ids": ["face_old"]}]}
    report = rebind_cae_faces(old_mapping, old_topology, new_topology)
    assert report["all_resolved"] is False
    assert "face_old" in report["ambiguous_face_ids"]


def test_rebind_cae_faces_rejects_surface_type_change() -> None:
    old_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 10, 10, 10]},
            {"id": "face_old", "type": "face", "surface_type": "cylinder", "body_id": "body_1",
             "radius": 4.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 0, 10, 10, 10]},
        ]
    }
    new_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 10, 10, 10]},
            {"id": "face_new", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 100.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 0, 10, 10, 0]},
        ]
    }
    old_mapping = {"mappings": [{"cae_entity": "X", "maps_to": {"feature_id": "x"}, "face_ids": ["face_old"]}]}
    report = rebind_cae_faces(old_mapping, old_topology, new_topology)
    assert report["all_resolved"] is False
    assert "face_old" in report["unresolved_face_ids"]


def test_solve_package_static_rebinds_faces_when_stale(tmp_path: Path) -> None:
    from app.simulation_runner import solve_package_static, check_simulation_tools

    # Baseline package with original face IDs.
    baseline_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 100, 50, 10]},
            {"id": "face_load", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 5000.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 100, 50, 10]},
            {"id": "face_hole", "type": "face", "surface_type": "cylinder", "body_id": "body_1",
             "radius": 4.0, "normal": [0, 0, 1], "bounding_box": [8, 8, 0, 12, 12, 10]},
        ]
    }
    baseline_mapping = {
        "schema_version": "0.1",
        "ai_generated": True,
        "mappings": [
            {"cae_entity": "FEAT_HOLE_001", "maps_to": {"feature_id": "feat_hole_001", "role": "fixed_support"},
             "face_ids": ["face_hole"]},
            {"cae_entity": "FEAT_BASE_001_L", "maps_to": {"feature_id": "feat_base_001", "role": "load_application"},
             "face_ids": ["face_load"]},
        ],
    }
    baseline_pkg = tmp_path / "baseline.aieng"
    _build_package_from_topology(baseline_pkg, baseline_topology, cae_mapping=baseline_mapping)

    # Variant package: same geometry, renamed face IDs.
    variant_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 100, 50, 10]},
            {"id": "face_load_v2", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 5000.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 100, 50, 10]},
            {"id": "face_hole_v2", "type": "face", "surface_type": "cylinder", "body_id": "body_1",
             "radius": 4.0, "normal": [0, 0, 1], "bounding_box": [8, 8, 0, 12, 12, 10]},
        ]
    }
    variant_pkg = tmp_path / "variant.aieng"
    _build_package_from_topology(variant_pkg, variant_topology, cae_mapping=baseline_mapping)

    with patch("app.simulation_runner.check_simulation_tools", return_value={"ready": True, "missing": []}), \
         patch("app.simulation_runner._mesh_with_gmsh", side_effect=_fake_mesh), \
         patch("app.simulation_runner._run_calculix", side_effect=_fake_calculix), \
         patch("app.simulation_runner._extract_metrics", return_value=_MOCK_FRD_METRICS):
        result = solve_package_static(
            variant_pkg,
            rebind_faces=True,
            baseline_package_path=baseline_pkg,
        )

    assert result["solver_executed"] is True, result
    assert result["status"] == "success"


# ── generate_mesh_for_package (STEP -> Gmsh -> persisted mesh artifacts) ──────

def _build_real_step_package(pkg_path: Path) -> None:
    """Build a minimal package with a REAL build123d box STEP (meshable)."""
    build123d = pytest.importorskip("build123d")
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        step_path = Path(tmp) / "box.step"
        box = build123d.Box(20.0, 20.0, 20.0)
        build123d.export_step(box, str(step_path))
        step_bytes = step_path.read_bytes()
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1"}))
        zf.writestr("geometry/generated.step", step_bytes)


def test_generate_mesh_for_package_meshes_real_box(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    from app.simulation_runner import (
        generate_mesh_for_package,
        get_mesh_preview,
        _read_member,
    )

    pkg = tmp_path / "box.aieng"
    _build_real_step_package(pkg)

    result = generate_mesh_for_package(pkg, mesh_size_mm=8.0)

    assert result["status"] == "success", result
    assert result["node_count"] > 0
    assert result["element_count"] > 0
    assert result["element_type"] == "C3D4"
    assert result["target_size_mm"] == 8.0
    assert "simulation/mesh.inp" in result["written_artifacts"]
    assert "simulation/mesh/mesh_metadata.json" in result["written_artifacts"]

    # Both artifacts are actually in the package.
    with zipfile.ZipFile(pkg, "r") as zf:
        names = zf.namelist()
        assert "simulation/mesh.inp" in names
        assert "simulation/mesh/mesh_metadata.json" in names
        # has_mesh preflight scans the simulation/mesh/ prefix.
        assert any(n.startswith("simulation/mesh/") for n in names)
        metadata = json.loads(zf.read("simulation/mesh/mesh_metadata.json"))
        assert metadata["node_count"] == result["node_count"]
        assert metadata["element_count"] == result["element_count"]
        assert metadata["generator"] == "gmsh"

    # The persisted mesh.inp lights up the existing mesh-preview reader.
    preview = get_mesh_preview(pkg)
    assert preview["available"] is True
    assert preview["element_count"] == result["element_count"]


def test_generate_mesh_for_package_no_geometry(tmp_path: Path) -> None:
    from app.simulation_runner import generate_mesh_for_package

    pkg = tmp_path / "empty.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")

    # Gmsh is checked first; force it "available" so we reach the geometry branch.
    with patch("app.simulation_runner._gmsh_available", return_value=True):
        result = generate_mesh_for_package(pkg)

    assert result["status"] == "no_geometry"


def test_generate_mesh_for_package_tools_unavailable(tmp_path: Path) -> None:
    from app.simulation_runner import generate_mesh_for_package

    pkg = tmp_path / "nogmsh.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("geometry/generated.step", _FAKE_STEP)

    with patch("app.simulation_runner._gmsh_available", return_value=False):
        result = generate_mesh_for_package(pkg)

    assert result["status"] == "tools_unavailable"
    assert "gmsh" in result["missing_tools"]


def test_cae_generate_mesh_tool_registered(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    tools = {t["name"]: t for t in client.get("/api/runtime/tools").json()}

    assert "cae.generate_mesh" in tools
    # Meshing is non-destructive geometry compute (like generate_solver_input):
    # no approval gate; only cae.run_solver is approval-gated.
    assert tools["cae.generate_mesh"]["requires_approval"] is False


def test_cae_generate_mesh_tool_meshes_via_invoke(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    pytest.importorskip("build123d")
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)

    project_id, pkg_path = _make_project(settings, "mesh-tool", "mesh_tool.aieng")
    _build_real_step_package(pkg_path)

    resp = client.post(
        "/api/agent/invoke-tool",
        json={"tool": "cae.generate_mesh", "input": {"project_id": project_id, "mesh_size_mm": 8.0}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True, data
    assert data["status"] == "completed"
    assert data["node_count"] > 0
    assert data["element_count"] > 0

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
        assert "simulation/mesh.inp" in names
        assert "simulation/mesh/mesh_metadata.json" in names


# ── source solver deck synthesis (close the decoupled MCP solve loop) ─────────

def test_build_source_deck_from_mesh_structure() -> None:
    from app.simulation_runner import build_source_deck_from_mesh

    nsets = {"FEAT_HOLE_001": [5], "FEAT_BASE_001_L": [4], "EMPTY_SET": []}
    deck, empty = build_source_deck_from_mesh(_MINIMAL_MESH_INP, _SETUP_YAML, nsets)

    # Mesh preserved + a solid section assigned to the volume element set.
    assert "*NODE" in deck.upper()
    assert "*ELEMENT" in deck.upper()
    assert "*SOLID SECTION, ELSET=EALL, MATERIAL=Al6061-T6" in deck
    # NSETs named per cae_entity so the deck generator's BC/load targets resolve.
    assert "*NSET, NSET=FEAT_HOLE_001" in deck
    assert "*NSET, NSET=FEAT_BASE_001_L" in deck
    # Empty sets are reported, never emitted as a dangling NSET reference.
    assert "*NSET, NSET=EMPTY_SET" not in deck
    assert empty == ["EMPTY_SET"]


def _build_meshed_setup_package(pkg_path: Path) -> None:
    """A package with a mesh + setup + cae_mapping but NO imported source deck."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1", "model_id": "src-deck-test"}))
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("geometry/generated.step", _FAKE_STEP)
        zf.writestr("simulation/setup.yaml", yaml.dump(_SETUP_YAML))
        zf.writestr("simulation/cae_mapping.json", json.dumps(_CAE_MAPPING))
        zf.writestr("simulation/mesh.inp", _MINIMAL_MESH_INP)


def test_ensure_source_deck_from_mesh_synthesizes_and_is_idempotent(tmp_path: Path) -> None:
    from app.simulation_runner import ensure_source_deck_from_mesh, SOURCE_SOLVER_DECK_PATH, _read_member

    pkg = tmp_path / "meshed.aieng"
    _build_meshed_setup_package(pkg)

    result = ensure_source_deck_from_mesh(pkg)
    assert result["created"] is True
    assert result["status"] == "synthesized"
    # node 5 -> cylinder face_003 (FEAT_HOLE_001); node 4 -> top face_002 (FEAT_BASE_001_L)
    assert set(result["nset_names"]) == {"FEAT_HOLE_001", "FEAT_BASE_001_L"}

    deck_bytes = _read_member(pkg, SOURCE_SOLVER_DECK_PATH)
    assert deck_bytes is not None
    assert b"*NSET, NSET=FEAT_HOLE_001" in deck_bytes

    # Idempotent: an existing (or imported) source deck is never overwritten.
    again = ensure_source_deck_from_mesh(pkg)
    assert again["created"] is False
    assert again["status"] == "exists"


def test_ensure_source_deck_from_mesh_no_mesh(tmp_path: Path) -> None:
    from app.simulation_runner import ensure_source_deck_from_mesh

    pkg = tmp_path / "nomesh.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")
    result = ensure_source_deck_from_mesh(pkg)
    assert result["created"] is False
    assert result["status"] == "no_mesh"


def test_generate_solver_input_consumes_synthesized_source_deck(tmp_path: Path) -> None:
    """End-to-end (no ccx): a meshed+setup package with no imported source deck
    now produces a runnable solver input deck via the staged MCP path, because
    cae.generate_solver_input synthesizes the source deck from the mesh first."""
    from app import aieng_bridge, simulation_runner
    from app.simulation_runner import _read_member

    pkg = tmp_path / "loop.aieng"
    _build_meshed_setup_package(pkg)

    synth = simulation_runner.ensure_source_deck_from_mesh(pkg)
    assert synth["created"] is True

    result = aieng_bridge.generate_solver_input(
        pkg, aieng_root=_WORKSPACE_ROOT / "aieng", run_id="run_001", overwrite=True
    )
    assert result["status"] == "ok"
    assert result["out_path"] == "simulation/runs/run_001/solver_input.inp"

    # The assembled deck binds the BC to the synthesized NSET name + has a step.
    deck = _read_member(pkg, "simulation/runs/run_001/solver_input.inp")
    assert deck is not None
    text = deck.decode(errors="replace")
    assert "FEAT_HOLE_001" in text   # fixed-support BC target NSET
    assert "*STEP" in text
    assert "*STATIC" in text


def test_cae_generate_solver_input_synthesizes_source_deck_via_invoke(tmp_path: Path) -> None:
    """Handler wiring: cae.generate_solver_input synthesizes the source deck from a
    persisted mesh, so a meshed+setup project produces a deck via MCP with no
    externally-imported source deck."""
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)

    project_id, pkg_path = _make_project(settings, "gen-input", "gen_input.aieng")
    _build_meshed_setup_package(pkg_path)

    resp = client.post(
        "/api/agent/invoke-tool",
        json={"tool": "cae.generate_solver_input", "input": {"project_id": project_id, "overwrite": True}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True, data
    assert data["status"] == "completed"
    assert data["source_deck_synthesis"]["created"] is True
    assert data["out_path"] == "simulation/runs/run_001/solver_input.inp"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "simulation/cae_imports/source_solver_deck.inp" in zf.namelist()
        assert "simulation/runs/run_001/solver_input.inp" in zf.namelist()


def test_solve_package_static_refuses_rebind_when_ambiguous(tmp_path: Path) -> None:
    from app.simulation_runner import solve_package_static

    baseline_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 100, 50, 10]},
            {"id": "face_old", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 100.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 10, 10, 10]},
        ]
    }
    baseline_mapping = {
        "schema_version": "0.1",
        "ai_generated": True,
        "mappings": [
            {"cae_entity": "X", "maps_to": {"feature_id": "x"}, "face_ids": ["face_old"]},
        ],
    }
    baseline_pkg = tmp_path / "baseline.aieng"
    _build_package_from_topology(baseline_pkg, baseline_topology, cae_mapping=baseline_mapping)

    variant_topology = {
        "entities": [
            {"id": "body_1", "type": "solid", "bounding_box": [0, 0, 0, 100, 50, 10]},
            {"id": "face_a", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 100.0, "normal": [0, 0, 1], "bounding_box": [0, 0, 10, 10, 10, 10]},
            {"id": "face_b", "type": "face", "surface_type": "plane", "body_id": "body_1",
             "area": 100.0, "normal": [0, 0, 1], "bounding_box": [0.1, 0.1, 10, 10.1, 10.1, 10]},
        ]
    }
    variant_pkg = tmp_path / "variant.aieng"
    _build_package_from_topology(variant_pkg, variant_topology, cae_mapping=baseline_mapping)

    with patch("app.simulation_runner.check_simulation_tools", return_value={"ready": True, "missing": []}):
        result = solve_package_static(
            variant_pkg,
            rebind_faces=True,
            baseline_package_path=baseline_pkg,
        )

    assert result["solver_executed"] is False
    assert result["status"] == "stale_topology_references"
    assert "rebind_report" in result
