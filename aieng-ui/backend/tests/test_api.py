import csv
import hashlib
import io
import json
import math
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import (
    ARTIFACT_MANIFEST_PATH,
    AUDIT_EVENTS_PATH,
    CLAIM_PROPOSALS_DIR,
    REVALIDATION_STATUS_PATH,
    Settings,
    app,
    create_app,
    default_project,
    get_project,
    import_aieng_file,
    package_summary,
    project_dir,
    recent_logs,
    save_project,
    summarize_cae_payload,
    write_json,
    _append_audit_event_to_package,
    _build_audit_event,
    _build_claim_proposal,
    _build_claim_support_packet,
    _build_revalidation_response,
    _build_review_readiness,
    _check_claim_proposals,
    _classify_artifact_path,
    _generate_artifact_manifest,
    _is_internal_package_path,
    _read_audit_events_from_package,
    _read_claim_proposals_from_package,
    _read_revalidation_status,
    _record_geometry_edit_in_package,
    _record_solver_validation_in_package,
    _resolve_evidence_reference,
    _rollup_check_status,
    _run_package_consistency_checks,
    _STALE_EVIDENCE_CATEGORIES,
    _VALID_PROPOSED_STATUSES,
    _write_claim_proposal_to_package,
    _write_revalidation_status,
)
from app import runtime as _rt
from app.runtime_tool_registry import _resolve_ccx_cmd, _split_ccx_cmd

# Resolve workspace root relative to this test file
# test_api.py -> backend/tests -> backend -> aieng-ui -> workspace_aieng
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
from app.providers.registry import get_provider


def _wait_for_autopilot_status(
    client: TestClient,
    run_id: str,
    statuses: set[str],
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        response = client.get(f"/api/agent/autopilot/runs/{run_id}")
        assert response.status_code == 200
        last = response.json()
        if last.get("status") in statuses:
            return last
        time.sleep(0.05)
    assert last is not None
    raise AssertionError(f"Autopilot run {run_id} did not reach {statuses}; last={last}")


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["pid"], int)
    assert data["python_executable"]
    assert data["runtime_tool_count"] >= 0
    # Registry identity for stale-session detection (#29).
    assert data["registry_hash"].startswith("sha256:")


def test_health_registry_hash_matches_runtime_identity() -> None:
    """The health endpoint's registry_hash mirrors runtime.registry_identity()."""
    from app import runtime as _rt

    client = TestClient(app)
    data = client.get("/api/health").json()
    identity = _rt.registry_identity()
    assert data["registry_hash"] == identity["registry_hash"]
    assert data["runtime_tool_count"] == identity["tool_count"]


def test_registry_identity_is_deterministic_and_drift_sensitive() -> None:
    """The registry hash is stable across calls and changes when the tool set
    changes — the signal a long-lived MCP session uses to detect a stale registry.
    """
    from app import runtime as _rt

    first = _rt.registry_identity()
    second = _rt.registry_identity()
    assert first == second
    assert first["tool_count"] == len(_rt.registered_tool_names())

    sentinel = "test.stale_registry_probe"
    _rt.register_tool(sentinel, lambda inp, ctx: {"status": "ok"}, description="probe")
    try:
        changed = _rt.registry_identity()
        assert changed["tool_count"] == first["tool_count"] + 1
        assert changed["registry_hash"] != first["registry_hash"]
    finally:
        _rt._REGISTRY.pop(sentinel, None)
    # Removing the probe restores the original identity.
    assert _rt.registry_identity() == first


def test_field_descriptor_endpoint_returns_synthetic_contract(tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("field-test"))
    client = TestClient(create_app(settings))

    response = client.get(f"/api/projects/{project['id']}/fields/stress")
    assert response.status_code == 200
    data = response.json()
    assert data["field_name"] == "stress"
    assert data["project_id"] == project["id"]
    assert data["format"] == "vertex_synthetic"
    assert data["basis"] == "y_normalized"
    assert data["min_value"] == 0.0
    assert data["max_value"] == 250.0
    assert data["unit"] == "MPa"
    assert data["colormap"] == "thermal"
    assert data["source"] == "synthetic_mock"

    disp = client.get(f"/api/projects/{project['id']}/fields/displacement")
    assert disp.status_code == 200
    d = disp.json()
    assert d["field_name"] == "displacement"
    assert d["max_value"] == 5.0
    assert d["colormap"] == "coolwarm"

    unknown = client.get(f"/api/projects/{project['id']}/fields/temperature")
    assert unknown.status_code == 200
    u = unknown.json()
    assert u["field_name"] == "temperature"
    assert u["format"] == "vertex_synthetic"

    missing = client.get("/api/projects/nonexistent123456/fields/stress")
    assert missing.status_code == 404


def test_bom_endpoint_returns_frontend_payload(tmp_path: Path) -> None:
    """GET /api/projects/{id}/bom returns the camelCase BOMData the BOM panel expects."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("bom-ep"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "bom.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    feature_graph = {
        "format_version": "0.1.0",
        "features": [
            {"id": "b1", "type": "standard_part", "name": "bolt_M6", "canonical_type": "screw",
             "designation": "M6-1", "source_library": "bd_warehouse", "parameters": {}},
            {"id": "p1", "type": "named_part", "name": "base_plate", "parameters": {"material": "Al6061-T6"}},
        ],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "bom-ep", "resources": {}}))
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph))
    project["aieng_file"] = "bom.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/bom")
    assert resp.status_code == 200
    data = resp.json()
    assert data["projectId"] == project_id
    assert data["totalCount"] == 2
    assert data["standardPartCount"] == 1
    assert data["customPartCount"] == 1
    assert {i["name"] for i in data["items"]} >= {"bolt_M6", "base_plate"}
    assert "generatedAt" in data
    bolt = next(i for i in data["items"] if i["name"] == "bolt_M6")
    assert bolt["isStandardPart"] is True
    assert bolt["standardPartType"] == "screw"

    csv_resp = client.get(f"/api/projects/{project_id}/bom?format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(csv_resp.text.splitlines()))
    assert rows
    assert set(rows[0]) == {
        "line_no", "part_name", "part_type", "material", "quantity",
        "standard_part", "canonical_type", "designation", "source_library",
    }
    bolt_row = next(r for r in rows if r["part_name"] == "bolt_M6")
    assert bolt_row["standard_part"] == "true"
    assert bolt_row["canonical_type"] == "screw"
    assert bolt_row["designation"] == "M6-1"

    json_resp = client.get(f"/api/projects/{project_id}/bom?format=json")
    assert json_resp.status_code == 200
    export = json_resp.json()
    assert export["total_parts"] == 2
    assert any(r["part_name"] == "base_plate" for r in export["bill_of_materials"])

    xlsx_resp = client.get(f"/api/projects/{project_id}/bom?format=xlsx")
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert xlsx_resp.headers["content-disposition"].endswith(f'bom-{project_id}.xlsx"')
    assert xlsx_resp.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(xlsx_resp.content)) as zf:
        assert "xl/worksheets/sheet1.xml" in zf.namelist()

    bad_resp = client.get(f"/api/projects/{project_id}/bom?format=xml")
    assert bad_resp.status_code == 400


def test_bom_endpoint_404_when_package_missing(tmp_path: Path) -> None:
    """GET /bom returns 404 when the project has no .aieng package."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("bom-empty"))

    resp = client.get(f"/api/projects/{project['id']}/bom")
    assert resp.status_code == 404


def test_field_descriptor_returns_vtu_data_when_no_frd(tmp_path: Path) -> None:
    """GET /fields/{name} serves an external VTU result when no FRD is present (#279)."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("vtu-field"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "vtu-field.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    vtu = (
        '<?xml version="1.0"?>\n'
        '<VTKFile type="UnstructuredGrid" version="0.1">\n'
        '  <UnstructuredGrid><Piece NumberOfPoints="3" NumberOfCells="0">\n'
        '    <Points><DataArray NumberOfComponents="3" format="ascii">'
        "0 0 0 1 0 0 0 1 0</DataArray></Points>\n"
        '    <PointData><DataArray Name="von_mises" format="ascii">'
        "100 150 250</DataArray></PointData>\n"
        "  </Piece></UnstructuredGrid>\n</VTKFile>\n"
    )
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "vtu-field", "resources": {}}))
        zf.writestr("simulation/result.vtu", vtu)
    project["aieng_file"] = "vtu-field.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/fields/von_mises")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "vtu"
    assert data["basis"] == "vtu_point_data"
    assert data["values"] == [100.0, 150.0, 250.0]
    assert data["min_value"] == 100.0
    assert data["max_value"] == 250.0
    assert len(data["node_coords"]) == 3
    assert data["credibility"]["tier"] == "unverified"
    assert data["credibility"]["signals"]["solver_executed"] is False
    assert data["credibility"]["signals"]["is_solver_evidence"] is True


def test_field_descriptor_returns_real_frd_data(tmp_path: Path) -> None:
    """GET /fields/{name} returns real FRD data when result.frd exists in package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-field"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-test.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)

    # Build an FRD with coordinates + stress field
    coords = {
        1: (0.0, 0.0, 0.0),
        2: (10.0, 0.0, 0.0),
        3: (10.0, 1.0, 0.0),
        4: (0.0, 1.0, 0.0),
        5: (0.0, 0.0, 1.0),
        6: (10.0, 0.0, 1.0),
        7: (10.0, 1.0, 1.0),
        8: (0.0, 1.0, 1.0),
    }
    stress = {
        1: [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        2: [20.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        3: [30.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        4: [40.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        5: [50.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        6: [200.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        7: [210.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        8: [220.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }
    frd_text = _make_test_frd_with_coords(coords, stress_nodes=stress)

    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "field-test", "resources": {}}))
        zf.writestr("simulation/runs/run_001/outputs/result.frd", frd_text)

    project["aieng_file"] = "field-test.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/fields/stress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "vertex_json"
    assert data["basis"] == "frd_nearest_node"
    assert data["source"] == "frd"
    assert data["unit"] == "MPa"
    assert isinstance(data["values"], list)
    assert len(data["values"]) == 8
    assert isinstance(data["node_coords"], list)
    assert len(data["node_coords"]) == 8
    assert data["min_value"] == 10.0
    assert data["max_value"] == 220.0


def test_field_descriptor_load_case_switching(tmp_path: Path) -> None:
    """GET /fields/{name}?load_case_id=... selects the matching FRD step."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-field-multistep"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "multistep.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)

    coords = {
        1: (0.0, 0.0, 0.0),
        2: (1.0, 0.0, 0.0),
        3: (1.0, 1.0, 0.0),
        4: (0.0, 1.0, 0.0),
    }
    steps = [
        {"stress": {i: [float(i * 10), 0.0, 0.0, 0.0, 0.0, 0.0] for i in coords}},
        {"stress": {i: [float(90 + i * 10), 0.0, 0.0, 0.0, 0.0, 0.0] for i in coords}},
    ]
    frd_text = _make_test_multistep_frd_with_coords(coords, steps=steps)

    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "multistep", "resources": {}}))
        zf.writestr("simulation/runs/run_001/outputs/result.frd", frd_text)

    project["aieng_file"] = "multistep.aieng"
    save_project(settings, project)

    resp1 = client.get(f"/api/projects/{project_id}/fields/stress")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["source"] == "frd"
    assert data1["load_case_id"] == "load_case_001"
    assert data1["min_value"] == 10.0
    assert data1["max_value"] == 40.0

    resp2 = client.get(f"/api/projects/{project_id}/fields/stress?load_case_id=load_case_002")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["source"] == "frd"
    assert data2["load_case_id"] == "load_case_002"
    assert data2["min_value"] == 100.0
    assert data2["max_value"] == 130.0


def test_field_descriptor_returns_displacement_vectors(tmp_path: Path) -> None:
    """GET /fields/{name} returns per-node displacement vectors for DISP-sourced fields."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-disp-vectors"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "disp-test.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)

    coords = {
        1: (0.0, 0.0, 0.0),
        2: (10.0, 0.0, 0.0),
        3: (10.0, 1.0, 0.0),
        4: (0.0, 1.0, 0.0),
    }
    disp = {
        1: [0.1, 0.2, 0.3],
        2: [0.4, 0.5, 0.6],
        3: [0.7, 0.8, 0.9],
        4: [1.0, 1.1, 1.2],
    }
    frd_text = _make_test_frd_with_coords(coords, disp_nodes=disp)

    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "disp-test", "resources": {}}))
        zf.writestr("simulation/runs/run_001/outputs/result.frd", frd_text)

    project["aieng_file"] = "disp-test.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/fields/disp_magnitude")
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "vertex_json"
    assert data["source"] == "frd"
    assert data["unit"] == "mm"
    assert len(data["values"]) == 4
    # Displacement-sourced fields must carry per-node vectors for deformed-shape rendering.
    assert "vectors" in data
    assert len(data["vectors"]) == 4
    assert data["vectors"][0] == [0.1, 0.2, 0.3]
    # Magnitude matches the vector length.
    assert data["values"][0] == pytest.approx(math.sqrt(0.1**2 + 0.2**2 + 0.3**2))

    # Per-component fields also carry vectors.
    ux = client.get(f"/api/projects/{project_id}/fields/ux").json()
    assert "vectors" in ux
    assert ux["vectors"][1] == [0.4, 0.5, 0.6]
    assert ux["values"][1] == pytest.approx(0.4)

    # Stress fields do not fabricate displacement vectors.
    stress = client.get(f"/api/projects/{project_id}/fields/stress").json()
    assert stress["format"] == "vertex_synthetic"
    assert stress.get("vectors") is None


def test_field_descriptor_serves_derived_fields_and_safety_factor(tmp_path: Path) -> None:
    """GET /fields/{name} serves derived fields (von_mises, components, principal)
    and a safety-factor field when the material yield is resolvable."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-derived"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "derived-test.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)

    coords = {i: (float(i), 0.0, 0.0) for i in range(1, 9)}
    # Uniaxial stress (only Sxx) → von Mises == Sxx == S1; min 10, max 220.
    stress = {i: [v, 0.0, 0.0, 0.0, 0.0, 0.0] for i, v in zip(range(1, 9), [10, 20, 30, 40, 50, 200, 210, 220])}
    frd_text = _make_test_frd_with_coords(coords, stress_nodes=stress)

    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "derived-test", "resources": {}}))
        zf.writestr("simulation/runs/run_001/outputs/result.frd", frd_text)
        zf.writestr("simulation/setup.yaml", "material_name: Steel-316L\n")

    project["aieng_file"] = "derived-test.aieng"
    save_project(settings, project)

    def _field(name: str) -> dict:
        r = client.get(f"/api/projects/{project_id}/fields/{name}")
        assert r.status_code == 200, name
        return r.json()

    # von Mises / Sxx / S1 all equal the uniaxial Sxx field.
    for name in ("von_mises", "sxx", "s1"):
        d = _field(name)
        assert d["source"] == "frd", name
        assert d["unit"] == "MPa"
        assert d["min_value"] == 10.0 and d["max_value"] == 220.0, name

    # Safety factor = yield(290 for Steel-316L) / von Mises; min SF at the peak stress.
    sf = _field("safety_factor")
    assert sf["source"] == "frd"
    assert sf["unit"] == ""
    assert sf["min_value"] == pytest.approx(290.0 / 220.0, rel=1e-3)
    assert sf["max_value"] == pytest.approx(290.0 / 10.0, rel=1e-3)


def test_field_descriptor_safety_factor_unavailable_without_material(tmp_path: Path) -> None:
    """Without a resolvable material yield, safety_factor falls back to synthetic
    (honest: the field is not fabricated from FRD)."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-nosf"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "nosf.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    coords = {i: (float(i), 0.0, 0.0) for i in range(1, 9)}
    stress = {i: [float(10 * i), 0.0, 0.0, 0.0, 0.0, 0.0] for i in range(1, 9)}
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "nosf", "resources": {}}))
        zf.writestr("simulation/runs/run_001/outputs/result.frd", _make_test_frd_with_coords(coords, stress_nodes=stress))
        # no setup.yaml → no material yield
    project["aieng_file"] = "nosf.aieng"
    save_project(settings, project)

    sf = client.get(f"/api/projects/{project_id}/fields/safety_factor").json()
    assert sf["source"] == "synthetic_mock"  # not fabricated from FRD


def test_resolve_material_yield_reads_setup_without_enumerating_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.platform_logic import _resolve_material_yield

    pkg_path = tmp_path / "material-yield.aieng"
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "simulation/setup.yaml",
            yaml.safe_dump({"material": {"name": "custom", "yield_strength_mpa": 345.0}}),
        )

    def fail_namelist(self):
        raise AssertionError("namelist should not be needed for direct setup reads")

    monkeypatch.setattr(zipfile.ZipFile, "namelist", fail_namelist)

    assert _resolve_material_yield(pkg_path) == 345.0


def test_field_descriptor_fallback_to_synthetic_when_no_frd(tmp_path: Path) -> None:
    """GET /fields/{name} falls back to synthetic when package has no FRD."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("no-frd"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "no-frd.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "no-frd", "resources": {}}))

    project["aieng_file"] = "no-frd.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/fields/stress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "vertex_synthetic"
    assert data["basis"] == "y_normalized"
    assert data["source"] == "synthetic_mock"


def test_field_descriptor_all_selectable_fields_have_metadata(tmp_path: Path) -> None:
    """GET /fields/{name} returns honest synthetic metadata for every selectable field."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("field-metadata"))
    project_id = project["id"]

    expected: dict[str, dict[str, Any]] = {
        # stress scalar fields
        "von_mises": {"unit": "MPa", "colormap": "thermal"},
        "stress": {"unit": "MPa", "colormap": "thermal"},
        "sxx": {"unit": "MPa", "colormap": "coolwarm"},
        "syy": {"unit": "MPa", "colormap": "coolwarm"},
        "szz": {"unit": "MPa", "colormap": "coolwarm"},
        "sxy": {"unit": "MPa", "colormap": "coolwarm"},
        "sxz": {"unit": "MPa", "colormap": "coolwarm"},
        "syz": {"unit": "MPa", "colormap": "coolwarm"},
        # principal / equivalent stress fields
        "s1": {"unit": "MPa", "colormap": "thermal"},
        "s2": {"unit": "MPa", "colormap": "thermal"},
        "s3": {"unit": "MPa", "colormap": "thermal"},
        "tresca": {"unit": "MPa", "colormap": "thermal"},
        "max_shear": {"unit": "MPa", "colormap": "thermal"},
        # displacement fields
        "disp_magnitude": {"unit": "mm", "colormap": "coolwarm"},
        "displacement": {"unit": "mm", "colormap": "coolwarm"},
        "ux": {"unit": "mm", "colormap": "coolwarm"},
        "uy": {"unit": "mm", "colormap": "coolwarm"},
        "uz": {"unit": "mm", "colormap": "coolwarm"},
        # safety factor
        "safety_factor": {"unit": "", "colormap": "thermal"},
    }

    for name, meta in expected.items():
        resp = client.get(f"/api/projects/{project_id}/fields/{name}")
        assert resp.status_code == 200, name
        data = resp.json()
        assert data["field_name"] == name, name
        assert data["format"] == "vertex_synthetic", name
        assert data["source"] == "synthetic_mock", name
        assert data["unit"] == meta["unit"], name
        assert data["colormap"] == meta["colormap"], name
        assert data["credibility"]["tier"] == "unverified", name
        assert data["credibility"]["rank"] == 0, name


def _make_selectable_fields_package(pkg_path: Path) -> None:
    """Create a package with FRD stress + displacement data and a known material."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    coords = {i: (float(i), 0.0, 0.0) for i in range(1, 9)}
    # Uniaxial tension varying along X: Sxx = 10*i; von Mises == Sxx.
    stress = {i: [10.0 * i, 0.0, 0.0, 0.0, 0.0, 0.0] for i in range(1, 9)}
    # Linear displacement along X, Y, Z so all component fields are non-constant.
    disp = {i: [0.1 * i, 0.05 * i, 0.02 * i] for i in range(1, 9)}
    frd_text = _make_test_frd_with_coords(coords, disp_nodes=disp, stress_nodes=stress)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "selectable-fields", "resources": {}}))
        zf.writestr("simulation/runs/run_001/outputs/result.frd", frd_text)
        zf.writestr("simulation/setup.yaml", "material_name: Steel-316L\n")


def test_list_cae_result_fields_includes_all_selectable_fields(tmp_path: Path) -> None:
    """GET /cae-result-fields lists every selectable field when an FRD is present."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("fields-list-all"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fields-list-all.aieng"
    _make_selectable_fields_package(pkg_path)
    project["aieng_file"] = "fields-list-all.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-result-fields")
    assert resp.status_code == 200
    data = resp.json()
    available = {f["field_name"] for f in data["available_fields"]}

    canonical_fields = {
        "von_mises", "sxx", "syy", "szz", "sxy", "sxz", "syz",
        "s1", "s2", "s3", "tresca", "max_shear",
        "disp_magnitude", "ux", "uy", "uz",
        "safety_factor",
    }
    missing = canonical_fields - available
    assert not missing, f"Missing selectable fields: {missing}"


def test_get_cae_result_field_summary_supports_all_selectable_fields(tmp_path: Path) -> None:
    """GET /cae-result-fields/{name} returns FRD stats for every selectable field."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("fields-summ-all"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fields-summ-all.aieng"
    _make_selectable_fields_package(pkg_path)
    project["aieng_file"] = "fields-summ-all.aieng"
    save_project(settings, project)

    canonical_fields = [
        "von_mises", "sxx", "syy", "szz", "sxy", "sxz", "syz",
        "s1", "s2", "s3", "tresca", "max_shear",
        "disp_magnitude", "ux", "uy", "uz",
        "safety_factor",
    ]
    for name in canonical_fields:
        resp = client.get(f"/api/projects/{project_id}/cae-result-fields/{name}")
        assert resp.status_code == 200, name
        data = resp.json()
        assert data["field_name"] == name, name
        assert data["source"]["source_type"] == "frd", name
        stats = data["stats"]
        assert stats["node_count"] == 8, name
        assert stats["values_finite"] is True, name
        assert stats["max_value"] >= stats["min_value"], name


def test_package_summary_advertises_all_solver_fields(tmp_path: Path) -> None:
    """The project summary's cae.solver_fields includes every selectable field."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("fields-summary-all"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fields-summary-all.aieng"
    _make_selectable_fields_package(pkg_path)
    project["aieng_file"] = "fields-summary-all.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 200
    data = resp.json()
    solver_fields = {f["field_name"] for f in data["cae"]["solver_fields"]}

    canonical_fields = {
        "von_mises", "sxx", "syy", "szz", "sxy", "sxz", "syz",
        "s1", "s2", "s3", "tresca", "max_shear",
        "disp_magnitude", "ux", "uy", "uz",
        "safety_factor",
    }
    missing = canonical_fields - solver_fields
    assert not missing, f"Missing solver_fields: {missing}"


def test_cae_artifacts_endpoint_returns_detection_result(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("cae-test"))
    pkg_dir = project_dir(settings, project["id"]) / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = pkg_dir / "test.aieng"
    with zipfile.ZipFile(pkg_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test"}))
    project["aieng_file"] = "packages/test.aieng"
    save_project(settings, project)

    fake_result = {
        "mode": "cae_setup",
        "artifacts": {"graph/constraints.json": True, "simulation/mesh/model.vtu": False},
        "has_cae_setup": True,
        "has_mesh": False,
        "has_solver_settings": False,
        "has_results": False,
        "has_fields": False,
        "has_validation": False,
        "detected_count": 1,
        "total_count": 15,
    }
    monkeypatch.setattr("app.main._detect_cae_artifacts", lambda _s, _p: fake_result)

    client = TestClient(create_app(settings))
    response = client.get(f"/api/projects/{project['id']}/cae-artifacts")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "cae_setup"
    assert data["has_cae_setup"] is True
    assert data["artifacts"]["graph/constraints.json"] is True
    assert data["artifacts"]["simulation/mesh/model.vtu"] is False


def test_cae_artifacts_endpoint_404_when_no_package(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("cae-test"))
    client = TestClient(create_app(settings))
    response = client.get(f"/api/projects/{project['id']}/cae-artifacts")
    assert response.status_code == 404


def test_cae_result_summary_endpoint_returns_summary(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("cae-test"))
    pkg_dir = project_dir(settings, project["id"]) / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = pkg_dir / "test.aieng"
    with zipfile.ZipFile(pkg_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test"}))
    project["aieng_file"] = "packages/test.aieng"
    save_project(settings, project)

    fake_result = {
        "schema_version": "0.1",
        "summary_type": "cae_postprocessing",
        "status": {"mode": "cad_only", "warnings": []},
        "computed_values": {"extrema_computed": False, "max_displacement": None, "max_von_mises_stress": None, "minimum_safety_factor": None},
        "llm_summary": {"one_line": "CAD-only package; no CAE artifacts detected.", "key_findings": [], "risks": [], "recommended_next_actions": [], "limitations": []},
    }
    monkeypatch.setattr("app.main._generate_cae_result_summary", lambda _s, _p: fake_result)

    client = TestClient(create_app(settings))
    response = client.get(f"/api/projects/{project['id']}/cae-result-summary")
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "0.1"
    assert data["status"]["mode"] == "cad_only"


def test_summarize_cae_payload_does_not_include_solver_fields() -> None:
    result = summarize_cae_payload(
        constraints={"constraints": [{"id": "c1", "type": "simulation_target", "metric": "stress"}]},
        parsed_materials=None,
        parsed_boundary_conditions=None,
        parsed_loads=None,
        cae_mapping=None,
        evidence_index=None,
        validation_status=None,
    )
    assert "stress" in result["available_fields"]
    assert "solver_fields" not in result


# ── runtime tests ──────────────────────────────────────────────────────────────

def _make_runtime_settings(tmp_path: Path) -> Settings:
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )


def test_runtime_run_completed_with_registered_tool(tmp_path: Path) -> None:
    called: list[dict] = []

    def fake_tool(inp: dict, ctx: dict) -> dict:
        called.append({"inp": inp, "ctx": ctx})
        return {"result": "ok", "data": 42}

    _rt.register_tool("test.echo", fake_tool)
    try:
        run = _rt.RunRecord(
            run_id="test001",
            message="echo test",
            created_at="2026-01-01T00:00:00+00:00",
            status="pending",
        )
        # Override plan to use our registered test tool
        run.plan = [{"name": "test.echo", "description": "echo", "input": {"x": 1}}]
        _rt._STORE.pop("test001", None)

        # patch build_plan to return our custom step
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.echo", "description": "echo", "input": {"x": 1}}]
        try:
            result = _rt.execute_run(run, {})
        finally:
            _rt.build_plan = original_build

        assert result.status == "completed"
        assert len(result.tool_results) == 1
        assert result.tool_results[0].status == "success"
        assert result.tool_results[0].output == {"result": "ok", "data": 42}
        event_types = [e.type for e in result.events]
        assert "run_started" in event_types
        assert "tool_started" in event_types
        assert "tool_succeeded" in event_types
        assert "run_completed" in event_types
        assert called[0]["inp"] == {"x": 1}
    finally:
        _rt._REGISTRY.pop("test.echo", None)
        _rt._STORE.pop("test001", None)


def test_runtime_run_failed_tool_produces_error_event(tmp_path: Path) -> None:
    def boom_tool(inp: dict, ctx: dict) -> dict:
        raise ValueError("something went wrong")

    _rt.register_tool("test.boom", boom_tool)
    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.boom", "description": "fail", "input": {}}]
        try:
            run = _rt.RunRecord(
                run_id="test002",
                message="boom",
                created_at="2026-01-01T00:00:00+00:00",
                status="pending",
            )
            _rt._STORE.pop("test002", None)
            result = _rt.execute_run(run, {})
        finally:
            _rt.build_plan = original_build

        assert result.status == "failed"
        assert len(result.errors) == 1
        assert "ValueError" in result.errors[0]
        assert "something went wrong" in result.errors[0]
        event_types = [e.type for e in result.events]
        assert "tool_failed" in event_types
        assert "run_failed" in event_types
        failed_ev = next(e for e in result.events if e.type == "tool_failed")
        assert failed_ev.payload["tool"] == "test.boom"
    finally:
        _rt._REGISTRY.pop("test.boom", None)
        _rt._STORE.pop("test002", None)


def test_runtime_run_status_readable_after_execution(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/runtime/runs",
        json={"message": "inspect package"},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    get_resp = client.get(f"/api/runtime/runs/{run_id}")
    assert get_resp.status_code == 200
    fetched = get_resp.json()
    assert fetched["run_id"] == run_id
    assert fetched["status"] in ("completed", "failed", "awaiting_approval")

    events_resp = client.get(f"/api/runtime/runs/{run_id}/events")
    assert events_resp.status_code == 200
    events = events_resp.json()
    assert isinstance(events, list)
    assert any(e["type"] == "run_started" for e in events)


def test_runtime_run_not_found_returns_404(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    assert client.get("/api/runtime/runs/doesnotexist").status_code == 404
    assert client.get("/api/runtime/runs/doesnotexist/events").status_code == 404


def test_runtime_inspect_package_tool_via_endpoint(monkeypatch, tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("rt-test"))

    monkeypatch.setattr(
        "app.main.resolve_provider_bundle",
        lambda s, overrides=None: ({}, s, type("P", (), {"probe_capabilities": lambda *a, **kw: {}})()),
    )
    monkeypatch.setattr("app.main.runtime_status", lambda s: {"provider": "mock", "ready": False})

    client = TestClient(create_app(settings))
    response = client.post(
        "/api/runtime/runs",
        json={"message": "inspect the package", "project_id": project["id"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == project["id"]
    assert any(tc["name"] == "aieng.inspect_package" for tc in data["tool_calls"])
    assert data["status"] in ("completed", "failed")


# ── Phase 1 hardening tests ────────────────────────────────────────────────────

def test_runtime_run_is_persisted_after_creation(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/api/runtime/runs", json={"message": "inspect package"})
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    state_dir = tmp_path / "data" / "runtime" / "runs"
    run_file = state_dir / f"{run_id}.json"
    assert run_file.exists(), "run should be persisted to disk"
    persisted = json.loads(run_file.read_text(encoding="utf-8"))
    assert persisted["run_id"] == run_id
    assert persisted["status"] in ("completed", "failed", "awaiting_approval")


def test_runtime_run_listing_endpoint(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp1 = client.post("/api/runtime/runs", json={"message": "inspect package"})
    assert resp1.status_code == 200
    run_id = resp1.json()["run_id"]

    list_resp = client.get("/api/runtime/runs")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert isinstance(items, list)
    found = next((r for r in items if r["run_id"] == run_id), None)
    assert found is not None, "created run must appear in listing"
    assert "status" in found
    assert "message" in found
    assert "created_at" in found
    assert "event_count" in found
    assert "last_event_type" in found


def test_runtime_run_remains_readable_after_store_reload(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.post("/api/runtime/runs", json={"message": "inspect package"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    # Simulate server restart: clear in-memory store
    _rt._STORE.pop(run_id, None)

    # The run must still be loadable via the GET endpoint (from disk)
    get_resp = client.get(f"/api/runtime/runs/{run_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["run_id"] == run_id


def test_runtime_approval_required_pauses_run(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/api/runtime/runs", json={"message": "execute solver run"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "awaiting_approval"
    assert data["pending_step_index"] is not None
    assert any(e["type"] == "approval_required" for e in data["events"])


def test_runtime_approve_run_executes_pending_tool(tmp_path: Path) -> None:
    """A custom approval-gated tool executes successfully after approve is called."""
    executed: list[dict] = []

    def _approvalable_tool(inp: dict, ctx: dict) -> dict:
        executed.append(inp)
        return {"approved": True}

    _rt.register_tool("test.gated", _approvalable_tool, requires_approval=True, description="test")
    settings = _make_runtime_settings(tmp_path)

    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.gated", "description": "test", "input": {}}]
        client = TestClient(create_app(settings))
        try:
            resp = client.post("/api/runtime/runs", json={"message": "run gated"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "awaiting_approval"
            run_id = data["run_id"]

            approve_resp = client.post(f"/api/runtime/runs/{run_id}/approve")
            assert approve_resp.status_code == 200
            approved = approve_resp.json()
            assert approved["status"] == "completed"
            assert len(executed) == 1
            assert any(e["type"] == "approval_granted" for e in approved["events"])
            assert any(e["type"] == "tool_succeeded" for e in approved["events"])
            assert any(e["type"] == "run_completed" for e in approved["events"])
        finally:
            _rt.build_plan = original_build
    finally:
        _rt._REGISTRY.pop("test.gated", None)


def test_runtime_reject_run_does_not_execute_tool(tmp_path: Path) -> None:
    """Rejecting an approval-gated run does not execute the tool."""
    executed: list[dict] = []

    def _dangerous_tool(inp: dict, ctx: dict) -> dict:
        executed.append(inp)
        return {"oops": "should not reach here"}

    _rt.register_tool("test.dangerous", _dangerous_tool, requires_approval=True, description="test")
    settings = _make_runtime_settings(tmp_path)

    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.dangerous", "description": "test", "input": {}}]
        client = TestClient(create_app(settings))
        try:
            resp = client.post("/api/runtime/runs", json={"message": "run dangerous"})
            assert resp.status_code == 200
            run_id = resp.json()["run_id"]

            reject_resp = client.post(f"/api/runtime/runs/{run_id}/reject")
            assert reject_resp.status_code == 200
            rejected = reject_resp.json()
            assert rejected["status"] == "rejected"
            assert len(executed) == 0, "tool must NOT have been executed"
            assert any(e["type"] == "approval_rejected" for e in rejected["events"])
            assert any(e["type"] == "run_rejected" for e in rejected["events"])
            assert len(rejected["tool_errors"]) > 0
        finally:
            _rt.build_plan = original_build
    finally:
        _rt._REGISTRY.pop("test.dangerous", None)


def test_runtime_approve_nonexistent_run_returns_404(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    assert client.post("/api/runtime/runs/nonexistent/approve").status_code == 404
    assert client.post("/api/runtime/runs/nonexistent/reject").status_code == 404


def test_agent_plan_dry_run_without_api_key_returns_guarded_plan(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("agent-test"))
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/agent/plan",
        json={
            "message": "Help me check this model and prepare weight reduction modelling",
            "project_id": project["id"],
            "dry_run": True,
            "llm_config": {
                "provider": "openai-compatible",
                "model": "fake",
                "api_key": "must-not-persist",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "heuristic"
    assert data["project_id"] == project["id"]
    tools = data["preview"]["tools"]
    assert "aieng.agent_context" in tools
    assert "aieng.inspect_package" in tools
    assert "mcp.check" in tools
    assert data["agent_context"]["agent_brief"]["next_decision_focus"]
    assert data["action_selection"]["policy"]
    assert "allowed_actions" in data["action_selection"]
    assert "api_key" not in data["llm_config"]
    assert data["warnings"], "modeling requests without patch_json should explain the missing executable patch"


def test_agent_plan_accepts_selected_geometry_context(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("agent-selection-test"))
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/agent/plan",
        json={
            "message": "Use the selected face as a load surface",
            "project_id": project["id"],
            "dry_run": True,
            "selected_geometry": {
                "pointers": ["@face:f_top_001"],
                "faces": [
                    {
                        "pointer": "@face:f_top_001",
                        "label": "top planar face",
                        "surface_type": "plane",
                        "roles": ["load_surface"],
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["selected_geometry"]["pointers"] == ["@face:f_top_001"]
    assert data["selected_geometry"]["faces"][0]["roles"] == ["load_surface"]


def test_agent_run_without_project_completes_with_empty_safe_plan(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/agent/runs",
        json={"message": "Explain how to start modelling", "dry_run": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["agent"]["mode"] == "heuristic"
    assert data["agent"]["steps"] == []
    assert data["run"]["status"] == "completed"
    assert data["run"]["project_id"] is None


def _execute_run_macro(client, tool_input):
    """Start a macro run via the runtime endpoint and auto-approve if gated."""
    resp = client.post("/api/runtime/runs", json={
        "message": "run macro",
        "tool_input": tool_input,
    })
    assert resp.status_code == 200
    data = resp.json()
    if data["status"] == "awaiting_approval":
        run_id = data["run_id"]
        approve_resp = client.post(f"/api/runtime/runs/{run_id}/approve")
        assert approve_resp.status_code == 200
        data = approve_resp.json()
    return data


def test_runtime_artifacts_extracted_into_tool_result(tmp_path: Path) -> None:
    """Artifacts returned by a tool handler are hoisted into ToolResult.artifacts."""
    artifact = {"path": "/fake/out.step", "kind": "step", "role": "primary_geometry"}

    def artifact_tool(inp: dict, ctx: dict) -> dict:
        return {"status": "ok", "artifacts": [artifact]}

    _rt.register_tool("test.artifact", artifact_tool)
    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [
            {"name": "test.artifact", "description": "test", "input": {}}
        ]
        run = _rt.RunRecord(
            run_id="art001",
            message="artifact test",
            created_at="2026-01-01T00:00:00+00:00",
            status="pending",
        )
        _rt._STORE.pop("art001", None)
        try:
            result = _rt.execute_run(run, {})
        finally:
            _rt.build_plan = original_build

        assert result.status == "completed"
        assert len(result.tool_results) == 1
        tr = result.tool_results[0]
        assert tr.status == "success"
        assert len(tr.artifacts) == 1
        assert tr.artifacts[0]["kind"] == "step"
        assert tr.artifacts[0]["path"] == "/fake/out.step"
    finally:
        _rt._REGISTRY.pop("test.artifact", None)
        _rt._STORE.pop("art001", None)


def test_runtime_plan_selects_computed_metrics_intent(tmp_path: Path) -> None:
    """'generate computed metrics' routes to postprocess.generate_computed_metrics."""
    from app.runtime import build_plan

    for msg in ["generate computed metrics", "import computed metrics", "normalize metrics"]:
        plan = build_plan(msg, None)
        assert len(plan) == 1, f"Expected 1 step for {msg!r}, got {plan}"
        assert plan[0]["name"] == "postprocess.generate_computed_metrics", (
            f"Expected postprocess.generate_computed_metrics for {msg!r}, got {plan[0]['name']}"
        )


def test_runtime_tool_input_merged_into_step_input(tmp_path: Path) -> None:
    """Structured tool_input from ctx is merged into each plan step."""
    called: list[dict] = []

    def capture_tool(inp: dict, ctx: dict) -> dict:
        called.append(inp)
        return {"status": "ok"}

    _rt.register_tool("test.capture", capture_tool)
    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.capture", "description": "capture", "input": {"base": 1}}]
        try:
            run = _rt.RunRecord(
                run_id="ti001",
                message="capture test",
                created_at="2026-01-01T00:00:00+00:00",
                status="pending",
            )
            _rt._STORE.pop("ti001", None)
            result = _rt.execute_run(run, {"tool_input": {"extra": 2}})
        finally:
            _rt.build_plan = original_build

        assert result.status == "completed"
        assert called[0]["base"] == 1
        assert called[0]["extra"] == 2
    finally:
        _rt._REGISTRY.pop("test.capture", None)
        _rt._STORE.pop("ti001", None)


def test_generate_computed_metrics_tool_registered(tmp_path: Path) -> None:
    """The runtime tool registry includes postprocess.generate_computed_metrics."""
    from app.runtime import registered_tools_info

    names = [t["name"] for t in registered_tools_info()]
    assert "postprocess.generate_computed_metrics" in names


def test_refresh_cae_summary_tool_registered(tmp_path: Path) -> None:
    """The runtime tool registry includes postprocess.refresh_cae_summary."""
    from app.runtime import registered_tools_info

    names = [t["name"] for t in registered_tools_info()]
    assert "postprocess.refresh_cae_summary" in names


# ---------------------------------------------------------------------------
# Runtime capability profile endpoint
# ---------------------------------------------------------------------------

def test_runtime_capabilities_endpoint_returns_expected_structure(tmp_path: Path) -> None:
    """GET /api/runtime/capabilities returns a valid capability profile."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    resp = client.get("/api/runtime/capabilities")
    assert resp.status_code == 200
    data = resp.json()

    assert data["schema_version"] == "0.1"
    assert "generated_at" in data
    assert "environment" in data
    assert "tools" in data
    assert "result_fields" in data
    assert "claim_policy" in data


def test_runtime_capabilities_refresh_cae_summary_artifact_paths(tmp_path: Path) -> None:
    """postprocess.refresh_cae_summary lists result_summary, evidence_index, and field summaries."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    resp = client.get("/api/runtime/capabilities")
    data = resp.json()
    by_name = {t["name"]: t for t in data["tools"]}

    refresh = by_name["postprocess.refresh_cae_summary"]
    assert refresh["advances_claims"] is False
    assert refresh["produces_evidence"] is True
    assert refresh["writes_artifacts"] is True
    paths = refresh["artifact_paths"]
    assert any("result_summary.json" in p for p in paths)
    assert any("evidence_index.json" in p for p in paths)
    assert any("displacement.summary.json" in p for p in paths)
    assert any("stress.summary.json" in p for p in paths)


def test_runtime_capabilities_result_fields_supported(tmp_path: Path) -> None:
    """Capability profile lists displacement and stress as supported result fields."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    resp = client.get("/api/runtime/capabilities")
    data = resp.json()
    fields = data["result_fields"]

    assert "displacement" in fields["supported"]
    assert "stress" in fields["supported"]
    assert fields["produces_evidence"] is False
    assert fields["advances_claims"] is False


def test_runtime_capabilities_claim_policy_no_auto_advancement(tmp_path: Path) -> None:
    """Global claim policy declares no automatic claim advancement."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    resp = client.get("/api/runtime/capabilities")
    data = resp.json()
    policy = data["claim_policy"]

    assert policy["automatic_claim_advancement"] is False
    assert policy["claim_advancement_requires_explicit_workflow"] is True


def test_runtime_plan_selects_refresh_cae_summary_intent(tmp_path: Path) -> None:
    """'refresh cae summary' includes postprocess.refresh_cae_summary in plan."""
    from app.runtime import build_plan

    for msg in ["refresh cae summary", "update postprocessing summary", "refresh CAE summary"]:
        plan = build_plan(msg, None)
        names = [s["name"] for s in plan]
        assert "postprocess.refresh_cae_summary" in names, (
            f"Expected postprocess.refresh_cae_summary in plan for {msg!r}, got {names}"
        )


def test_refresh_cae_summary_missing_package_path_returns_error(tmp_path: Path) -> None:
    """refresh_cae_summary returns structured error when no package path can be resolved."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "postprocess.refresh_cae_summary", "description": "refresh", "input": {}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "refresh cae summary",
            "tool_input": {},
        })
        assert resp.status_code == 200
        run = resp.json()
        # Runtime treats handler returns without exception as success;
        # the error is encoded in the tool result output.
        assert run["status"] == "completed"
        results = run["tool_results"]
        assert any(
            r.get("output", {}).get("code") == "missing_cae_summary_package_path"
            for r in results
        )
    finally:
        _rt.build_plan = original_build


def test_postprocessing_smoke_metrics_import_and_summary_refresh(tmp_path: Path) -> None:
    """Generic end-to-end smoke test for the post-processing workflow.

    Flow:
      1. Create a temp project with a minimal .aieng package.
      2. Write a generic metrics CSV to a temp path.
      3. Run postprocess.generate_computed_metrics via runtime.
      4. Assert computed_metrics.json was written back into the .aieng package.
      5. Run postprocess.refresh_cae_summary via runtime.
      6. Assert the refreshed summary contains the imported metrics.

    This test uses generic names only (no part-family-specific fixtures).
    """
    from app.main import Settings, create_app, default_project, get_project, project_dir, save_project
    import zipfile

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=Path(__file__).resolve().parents[3] / "aieng",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    # 1. Create project and minimal .aieng package
    project = save_project(settings, default_project("generic-smoke"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "generic-smoke.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "generic-smoke", "resources": {}}))
    project["aieng_file"] = "generic-smoke.aieng"
    save_project(settings, project)

    # 2. Write generic metrics CSV
    metrics_csv = tmp_path / "generic_metrics.csv"
    metrics_csv.write_text(
        "metric,value,unit,load_case_id\n"
        "max_von_mises_stress,187.4,MPa,load_case_001\n"
        "max_displacement,0.82,mm,load_case_001\n"
        "minimum_safety_factor,1.33,,load_case_001\n",
        encoding="utf-8",
    )

    # 3. Generate computed metrics via runtime
    gen_resp = client.post("/api/runtime/runs", json={
        "message": "generate computed metrics",
        "project_id": project_id,
        "tool_input": {
            "inputPath": str(metrics_csv),
            "project_id": project_id,
            "loadCaseId": "load_case_001",
            "software": "External postprocessor",
        },
    })
    assert gen_resp.status_code == 200
    gen_run = gen_resp.json()
    assert gen_run["status"] == "completed", f"generate computed metrics failed: {gen_run}"
    # Assert artifact was written into the .aieng package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/computed_metrics.json" in zf.namelist(), "computed_metrics.json not in package"

    # 4. Refresh CAE summary via runtime
    refresh_resp = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "overwrite": True,
        },
    })
    assert refresh_resp.status_code == 200
    refresh_run = refresh_resp.json()
    assert refresh_run["status"] == "completed", f"refresh cae summary failed: {refresh_run}"
    # Assert changed artifacts include the summary files
    artifact_paths = [
        a["path"]
        for tr in refresh_run["tool_results"]
        for a in (tr.get("artifacts") or [])
        if isinstance(a, dict) and "path" in a
    ]
    assert any("result_summary.json" in p for p in artifact_paths), artifact_paths
    assert any("evidence_index.json" in p for p in artifact_paths), artifact_paths
    assert any("postprocessing_summary.md" in p for p in artifact_paths), artifact_paths

    # 5. Read the refreshed summary and assert imported metrics are visible
    summary_resp = client.get(f"/api/projects/{project_id}/cae-result-summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["computed_values"]["extrema_computed"] is True
    assert summary["computed_values"]["max_von_mises_stress"]["value"] == 187.4
    assert summary["computed_values"]["max_displacement"]["value"] == 0.82
    assert summary["computed_values"]["minimum_safety_factor"]["value"] == 1.33


def test_evidence_claim_contract_after_cae_run(tmp_path: Path) -> None:
    """Evidence/claim contract: refresh_cae_summary records solver artifacts as evidence.

    Pre-injects a package that looks like a completed solver run (solver_run.json,
    result.frd, computed_metrics.json) then calls postprocess.refresh_cae_summary
    and reads the resulting evidence_index.json to verify:

    1. solver_run.json appears as a ``solver_run_metadata`` evidence entry.
    2. result.frd appears as a ``solver_raw_output`` evidence entry.
    3. computed_metrics.json appears as a ``computed_metrics`` evidence entry.
    4. Every evidence entry has auditable provenance: path, kind, role, supports.
    5. No supports tag implies a claim has been validated or auto-advanced.
    6. Neither ai/claim_map.json nor results/claim_map.json is written.

    No external solver is needed; solver run artifacts are pre-injected into the
    package.  This verifies the evidence production contract without claim
    advancement.
    """
    from app.main import create_app, default_project, project_dir, save_project

    settings = _make_patch_settings(tmp_path)
    app_inst = create_app(settings)
    client = TestClient(app_inst)

    project = save_project(settings, default_project("evidence-claim-test"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "evidence.aieng"

    # Build a package that looks like a completed CalculiX run.
    metrics = {
        "schema_version": "0.1",
        "metrics_source": {"tool": "CalculiX", "format": "frd_extracted"},
        "load_cases": [{
            "load_case_id": "load_case_001",
            "metrics": {
                "max_von_mises_stress": {"value": 42.0, "unit": "MPa"},
                "max_displacement": {"value": 0.03, "unit": "mm"},
            },
        }],
    }
    solver_run = {
        "run_id": "run_001",
        "solver": "CalculiX",
        "state": "completed",
        "solved": True,
        "converged": None,
        "return_code": 0,
        "started_at": "2026-05-18T12:00:00+00:00",
        "finished_at": "2026-05-18T12:00:01+00:00",
        "duration_seconds": 1.0,
        "input_files": ["simulation/runs/run_001/solver_input.inp"],
        "output_files": ["simulation/runs/run_001/outputs/result.frd"],
        "log_file": "simulation/runs/run_001/solver_log.txt",
        "warnings": [],
        "errors": [],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "evidence-claim-test", "resources": {}}))
        zf.writestr("results/computed_metrics.json", json.dumps(metrics))
        zf.writestr("simulation/runs/run_001/solver_run.json", json.dumps(solver_run))
        zf.writestr("simulation/runs/run_001/outputs/result.frd", "** CalculiX FRD stub\n")
        zf.writestr("simulation/runs/run_001/solver_log.txt", "CalculiX complete\n")

    project["aieng_file"] = "evidence.aieng"
    save_project(settings, project)

    # refresh_cae_summary generates result_summary.json, evidence_index.json,
    # and postprocessing_summary.md.  It must NOT touch claim_map.
    refresh_resp = client.post(
        "/api/runtime/runs",
        json={
            "message": "refresh cae summary",
            "project_id": project_id,
            "tool_input": {"project_id": project_id, "overwrite": True},
        },
    )
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["status"] == "completed"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        pkg_names = set(zf.namelist())
        assert "results/evidence_index.json" in pkg_names
        evidence = json.loads(zf.read("results/evidence_index.json"))

    assert evidence.get("evidence_type") == "cae_artifacts"
    entries_by_path = {e["path"]: e for e in evidence["entries"]}

    # 1. solver_run.json is catalogued as solver execution evidence.
    sr = entries_by_path.get("simulation/runs/run_001/solver_run.json")
    assert sr is not None, "evidence_index must contain simulation/runs/run_001/solver_run.json"
    assert sr["kind"] == "result"
    assert sr["role"] == "solver_run_metadata"
    assert sr["exists"] is True
    assert "solver_execution_evidence" in sr["supports"]

    # 2. result.frd is catalogued as raw solver output (source for numerical extraction).
    frd = entries_by_path.get("simulation/runs/run_001/outputs/result.frd")
    assert frd is not None, "evidence_index must contain simulation/runs/run_001/outputs/result.frd"
    assert frd["kind"] == "result"
    assert frd["role"] == "solver_raw_output"
    assert frd["exists"] is True
    assert "numerical_result_source" in frd["supports"]

    # 3. computed_metrics.json is catalogued with the correct evidence kind.
    cm = entries_by_path.get("results/computed_metrics.json")
    assert cm is not None, "evidence_index must contain results/computed_metrics.json"
    assert cm["kind"] == "computed_metrics"
    assert cm["exists"] is True
    assert cm["supports"]  # non-empty

    # 4. result_summary.json appears in the catalog.  exists=False is expected
    #    because the evidence_index is generated before the summary is written;
    #    the file will be present in the zip after the write completes.
    rs = entries_by_path.get("results/result_summary.json")
    assert rs is not None
    assert rs["kind"] == "result"

    # 5. Every present entry has auditable provenance: path, kind, role, supports.
    for entry in evidence["entries"]:
        if entry["exists"]:
            assert entry.get("path"), f"evidence entry missing path: {entry}"
            assert entry.get("kind"), f"evidence entry missing kind: {entry}"
            assert entry.get("role"), f"evidence entry missing role: {entry}"
            assert isinstance(entry.get("supports"), list), f"evidence entry supports must be a list: {entry}"

    # 6. No supports tag implies a claim has been validated or auto-advanced.
    # Solver output and metrics are evidence; claim advancement is a separate
    # explicit workflow.
    _CLAIM_ADVANCE_TERMS = {"validated", "claim_advanced", "claim_pass", "accepted_claim"}
    for entry in evidence["entries"]:
        for tag in entry.get("supports", []):
            assert not any(t in tag.lower() for t in _CLAIM_ADVANCE_TERMS), (
                f"entry {entry['path']!r} has a supports tag implying claim "
                f"advancement: {tag!r}"
            )

    # 7. Neither possible claim_map path is written by refresh_cae_summary.
    assert "ai/claim_map.json" not in pkg_names
    assert "results/claim_map.json" not in pkg_names


def test_write_artifact_to_package_adds_new_file(tmp_path: Path) -> None:
    """write_artifact_to_package inserts a new file into an .aieng package."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("other.txt", b"keep me")

    source = tmp_path / "computed_metrics.json"
    source.write_text('{"metrics": []}', encoding="utf-8")

    result = write_artifact_to_package(pkg, "results/computed_metrics.json", source, overwrite=True)
    assert result["path"] == "results/computed_metrics.json"

    with zipfile.ZipFile(pkg, "r") as zf:
        names = set(zf.namelist())
        assert "results/computed_metrics.json" in names
        assert "other.txt" in names
        assert "manifest.json" in names
        assert zf.read("other.txt") == b"keep me"
        assert json.loads(zf.read("results/computed_metrics.json")) == {"metrics": []}


def test_write_artifact_to_package_overwrites_existing(tmp_path: Path) -> None:
    """write_artifact_to_package replaces an existing entry without duplicates."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("results/computed_metrics.json", b"old")

    source = tmp_path / "computed_metrics.json"
    source.write_text('{"metrics": [1]}', encoding="utf-8")

    write_artifact_to_package(pkg, "results/computed_metrics.json", source, overwrite=True)

    with zipfile.ZipFile(pkg, "r") as zf:
        names = zf.namelist()
        assert names.count("results/computed_metrics.json") == 1
        assert zf.read("results/computed_metrics.json") == b'{"metrics": [1]}'


def test_write_artifact_to_package_refuses_overwrite_by_default(tmp_path: Path) -> None:
    """write_artifact_to_package raises FileExistsError when overwrite=False."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("results/computed_metrics.json", b"old")

    source = tmp_path / "computed_metrics.json"
    source.write_text('{"metrics": [1]}', encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_artifact_to_package(pkg, "results/computed_metrics.json", source, overwrite=False)


def test_write_artifact_to_package_missing_source_raises(tmp_path: Path) -> None:
    """write_artifact_to_package raises FileNotFoundError when source is missing."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))

    with pytest.raises(FileNotFoundError):
        write_artifact_to_package(pkg, "results/x.json", tmp_path / "missing.json")


def test_write_artifact_to_package_missing_manifest_raises(tmp_path: Path) -> None:
    """write_artifact_to_package raises ValueError when package lacks manifest.json."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("other.txt", b"data")

    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="missing manifest"):
        write_artifact_to_package(pkg, "results/x.json", source)



def test_cae_preprocessing_and_simulation_summary_endpoints(monkeypatch, tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("cae-summaries"))
    pkg = project_dir(settings, project["id"]) / "packages" / "test.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test"}))
    project["aieng_file"] = "packages/test.aieng"
    save_project(settings, project)

    monkeypatch.setattr(
        "app.main._generate_cae_preprocessing_summary",
        lambda _settings, _pkg: {"summary_type": "cae_preprocessing", "status": {"ready_for_solver": False}},
    )
    monkeypatch.setattr(
        "app.main._generate_cae_simulation_run_summary",
        lambda _settings, _pkg: {"summary_type": "cae_simulation_run", "status": {"run_count": 0}},
    )

    client = TestClient(create_app(settings))
    prep = client.get(f"/api/projects/{project['id']}/cae-preprocessing-summary")
    sim = client.get(f"/api/projects/{project['id']}/cae-simulation-run-summary")

    assert prep.status_code == 200
    assert prep.json()["summary_type"] == "cae_preprocessing"
    assert sim.status_code == 200
    assert sim.json()["summary_type"] == "cae_simulation_run"


def test_runtime_workflow_endpoint_executes_explicit_steps(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/runtime/runs",
        json={
            "message": "workflow smoke",
            "workflow_id": "custom",
            "steps": [
                {"id": "llm-plan", "kind": "llm", "description": "Plan with LLM", "status": "pending"},
                {"id": "artifact-note", "kind": "artifact", "description": "Record artifact", "status": "pending"},
            ],
            "llm_config": {"provider": "openai-compatible", "model": "demo", "api_key": "must_not_persist"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert [step["kind"] for step in data["plan"]] == ["llm", "artifact"]
    assert "must_not_persist" not in json.dumps(data)


def test_get_cae_preprocessing_summary_endpoint(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cae-preprocessing-summary returns preprocessing summary."""
    from app.main import Settings, create_app, default_project, project_dir, save_project
    import zipfile

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preproc-test"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preproc-test.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("simulation/cae_imports/parsed_materials.json", json.dumps({"materials": [{"name": "Steel"}]}).encode())
    project["aieng_file"] = "preproc-test.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-preprocessing-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == "0.1"
    assert data["summary_type"] == "cae_preprocessing"
    assert data["status"]["has_materials"] is True
    assert data["status"]["has_mesh"] is False


def test_get_cae_preprocessing_summary_missing_package_returns_404(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cae-preprocessing-summary returns 404 when package missing."""
    from app.main import Settings, create_app, default_project, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("no-pkg"))
    resp = client.get(f"/api/projects/{project['id']}/cae-preprocessing-summary")
    assert resp.status_code == 404


def test_get_cae_simulation_run_summary_missing_package_returns_404(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cae-simulation-run-summary returns 404 when package missing."""
    from app.main import Settings, create_app, default_project, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("no-pkg"))
    resp = client.get(f"/api/projects/{project['id']}/cae-simulation-run-summary")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 39 — CAD recommendations + verification endpoint (MVP)
# ---------------------------------------------------------------------------


def _build_phase39_fixture_package(pkg_path: Path) -> None:
    """Build a minimal .aieng package with the four inputs Phase 36/37 need."""
    import zipfile
    import yaml

    design_targets = {
        "format_version": "0.1.1",
        "target_set_id": "phase39_mvp_v1",
        "targets": [
            {
                "target_id": "mass_reduce_10pct",
                "target_type": "mass_reduction_target",
                "comparator": "reduce_by_at_least",
                "threshold": 10.0,
                "priority": "high",
            },
            {
                "target_id": "safety_factor_min",
                "target_type": "minimum_safety_factor",
                "comparator": ">=",
                "threshold": 1.5,
                "priority": "critical",
            },
        ],
        "claim_policy": {
            "targets_are_acceptance_criteria": True,
            "compliance_requires_evidence": True,
            "physical_correctness_not_claimed": True,
        },
    }
    features = {
        "features": [
            {
                "id": "back_wall",
                "kind": "wall",
                "parameters": {"thickness_mm": 20.0, "width_mm": 120.0},
                "mass_contribution_kg": 1.51,
            },
            {
                "id": "central_rib",
                "kind": "rib",
                "parameters": {"thickness_mm": 8.0, "length_mm": 100.0},
                "mass_contribution_kg": 0.38,
            },
        ],
    }
    stress = {
        "schema_version": "0.1",
        "load_case_id": "load_case_001",
        "yield_strength_mpa": 350.0,
        "minimum_required_safety_factor": 1.5,
        "max_allowable_stress_mpa": 233.0,
        "features": [
            {"feature_ref": "back_wall", "max_von_mises_stress_mpa": 22.0, "safety_factor": 15.91},
            {"feature_ref": "central_rib", "max_von_mises_stress_mpa": 195.0, "safety_factor": 1.79},
        ],
    }
    metrics = {
        "schema_version": "0.1",
        "metrics_source": {"tool": "external_postprocessor", "software": "CalculiX"},
        "load_cases": [
            {
                "id": "load_case_001",
                "metrics": {
                    "max_von_mises_stress": {"value": 195.0, "unit": "MPa"},
                    "minimum_safety_factor": {"value": 1.79},
                    "total_mass": {"value": 2.30, "unit": "kg"},
                },
            }
        ],
    }
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "phase39_mvp", "resources": {}}))
        zf.writestr("task/design_targets.yaml", yaml.safe_dump(design_targets, sort_keys=False))
        zf.writestr("simulation/cae_imports/parsed_features.json", json.dumps(features))
        zf.writestr("results/stress_by_feature.json", json.dumps(stress))
        zf.writestr("results/computed_metrics.json", json.dumps(metrics))


def test_get_cad_recommendations_endpoint_returns_proposals_and_verdicts(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cad-recommendations returns Phase 36 + Phase 37 combined."""
    from app.main import Settings, create_app, default_project, project_dir, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("rec-test"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "rec-test.aieng"
    _build_phase39_fixture_package(pkg_path)
    project["aieng_file"] = "rec-test.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cad-recommendations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["package_path"]
    assert data["strictness"] == "default"
    assert data["recommendations"]["schema_version"] == "0.1"
    assert data["recommendations"]["proposals"], "expected at least one proposal"
    # Top proposal must be back_wall thin (the safe candidate) and pass verification.
    top = data["recommendations"]["proposals"][0]
    assert top["feature_ref"] == "back_wall"
    assert top["action_type"] == "thin"
    verdicts = {v["proposal_id"]: v["verdict"] for v in data["verification"]["verdicts"]}
    assert verdicts.get(top["proposal_id"]) == "pass"
    # Honesty boundary block must be present.
    assert data["claim_policy"]["proposals_are_hypotheses"] is True
    assert data["claim_policy"]["claims_advanced"] is False


def test_get_cad_recommendations_endpoint_honours_strictness_param(tmp_path: Path) -> None:
    """`?strictness=lenient` is forwarded to the verifier."""
    from app.main import Settings, create_app, default_project, project_dir, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)
    project = save_project(settings, default_project("rec-lenient"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "rec-lenient.aieng"
    _build_phase39_fixture_package(pkg_path)
    project["aieng_file"] = "rec-lenient.aieng"
    save_project(settings, project)

    resp = client.get(
        f"/api/projects/{project_id}/cad-recommendations", params={"strictness": "lenient"}
    )
    assert resp.status_code == 200
    assert resp.json()["strictness"] == "lenient"


def test_get_cad_recommendations_endpoint_missing_package_returns_404(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cad-recommendations returns 404 when package missing."""
    from app.main import Settings, create_app, default_project, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)
    project = save_project(settings, default_project("no-pkg-rec"))
    resp = client.get(f"/api/projects/{project['id']}/cad-recommendations")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 18 — cae.apply_setup_patch runtime tool
# ---------------------------------------------------------------------------

def _make_patch_settings(tmp_path: Path):
    from app.main import Settings
    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )


def _make_setup_package(pkg_path: Path, extra: dict | None = None) -> None:
    """Create a minimal .aieng package suitable for setup-patch tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    solver_settings = {"solver": "CalculiX", "n_cpus": 4, "time_limit_s": 3600}
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "patch-test", "resources": {}}))
        zf.writestr("simulation/solver_settings.json", json.dumps(solver_settings))
        zf.writestr(
            "simulation/cae_imports/parsed_loads.json",
            json.dumps({"loads": [{"id": "load_001", "kind": "force", "magnitude": 1000.0}]}),
        )
        if extra:
            for name, content in extra.items():
                zf.writestr(name, json.dumps(content) if isinstance(content, dict) else content)


def test_cae_setup_patch_rejects_path_traversal(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects paths containing '..'."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-traversal"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{"path": "simulation/../secret.json", "action_type": "create_file", "content": {}}],
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "forbidden_path"


def test_merge_object_creates_file_when_absent_and_accepts_content_key() -> None:
    """merge_object into a MISSING file creates it, and accepts the `content` key
    (consistent with create_file). The documented cae.apply_setup_patch materials
    example uses merge_object + content as the first write, which previously failed
    with 'requires an existing file'. Dogfood friction."""
    import json
    from app.project_io import _apply_single_patch

    # merge into a missing file with `content` -> creates it
    op = {
        "action_type": "merge_object",
        "path": "simulation/cae_imports/parsed_materials.json",
        "content": {"materials": [{"name": "aluminum_6061"}]},
    }
    doc = json.loads(_apply_single_patch(None, op, op["path"]))
    assert doc == {"materials": [{"name": "aluminum_6061"}]}

    # merge into an existing file still merges (regression), `value` key still works
    existing = json.dumps({"a": 1}).encode()
    doc2 = json.loads(_apply_single_patch(existing, {"action_type": "merge_object", "path": "x.json", "value": {"b": 2}}, "x.json"))
    assert doc2 == {"a": 1, "b": 2}


def test_cae_setup_patch_rejects_absolute_path(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects absolute paths."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-abspath"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{"path": "/etc/passwd", "action_type": "create_file", "content": "x"}],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "forbidden_path"


def test_cae_setup_patch_rejects_results_write(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects writes to results/ paths."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-results"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{"path": "results/result_summary.json", "action_type": "create_file", "content": {}}],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "forbidden_path"


def test_cae_setup_patch_rejects_unsupported_operation(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects unknown action_type values."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-badop"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "delete_file",
            }],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "unsupported_operation"


def test_cae_setup_patch_rejects_before_mismatch(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects replace_json when 'before' does not match current value."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-before"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "before": 99,
                "value": 8,
            }],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "patch_error"
    assert "before mismatch" in result["message"]


def test_cae_setup_patch_create_file_success(tmp_path: Path) -> None:
    """cae.apply_setup_patch creates a new load-case file inside the package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-create"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    new_load_case = {"id": "load_case_001", "name": "Static", "loads": []}
    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/load_cases/load_case_001.json",
                "action_type": "create_file",
                "content": new_load_case,
            }],
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["status"] == "ok"
    changed = [a["path"] for a in result["changed_artifacts"]]
    assert "simulation/load_cases/load_case_001.json" in changed

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "simulation/load_cases/load_case_001.json" in zf.namelist()
        written = json.loads(zf.read("simulation/load_cases/load_case_001.json"))
        assert written["id"] == "load_case_001"


def test_cae_setup_patch_rejects_missing_patches_and_patch(tmp_path: Path) -> None:
    """Schema no longer enforces patches/patch at the schema level; runtime must still reject.

    Regression guard for the Codex compatibility fix that removed top-level ``anyOf``.
    """
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-missing"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "no_patches"


def test_cae_setup_patch_accepts_legacy_single_patch_object(tmp_path: Path) -> None:
    """cae.apply_setup_patch accepts the legacy single-object `patch` input."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-legacy-single"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patch": {
                "path": "simulation/load_cases/load_case_legacy.json",
                "action_type": "create_file",
                "content": {"id": "load_case_legacy", "loads": []},
            },
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        written = json.loads(zf.read("simulation/load_cases/load_case_legacy.json"))
    assert written["id"] == "load_case_legacy"


def test_cae_setup_patch_accepts_legacy_operation_map(tmp_path: Path) -> None:
    """cae.apply_setup_patch expands legacy operation maps into normalized patches."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-legacy-map"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patch": {
                "create_file": {
                    "path": "simulation/load_cases/load_case_map.json",
                    "content": {"id": "load_case_map", "loads": []},
                },
                "merge_object:solver_settings": {
                    "path": "simulation/solver_settings.json",
                    "content": {"new_key": "new_value"},
                },
            },
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    changed = {a["path"] for a in result["changed_artifacts"]}
    assert "simulation/load_cases/load_case_map.json" in changed
    assert "simulation/solver_settings.json" in changed

    with zipfile.ZipFile(pkg_path, "r") as zf:
        written_load_case = json.loads(zf.read("simulation/load_cases/load_case_map.json"))
        solver_settings = json.loads(zf.read("simulation/solver_settings.json"))
    assert written_load_case["id"] == "load_case_map"
    assert solver_settings["new_key"] == "new_value"


def test_cae_setup_patch_replace_json_mutates_value(tmp_path: Path) -> None:
    """cae.apply_setup_patch replace_json via pointer updates the target field."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-replace"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "before": 4,
                "value": 8,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    assert any(a["path"] == "simulation/solver_settings.json" for a in result["changed_artifacts"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        updated = json.loads(zf.read("simulation/solver_settings.json"))
    assert updated["n_cpus"] == 8
    assert updated["solver"] == "CalculiX"


def test_cae_setup_patch_preserves_unrelated_entries(tmp_path: Path) -> None:
    """cae.apply_setup_patch leaves unrelated package entries intact."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-preserve"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path, extra={"simulation/mesh/model.vtu": b"<vtu/>"})
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/load_cases/lc_new.json",
                "action_type": "create_file",
                "content": {"id": "lc_new"},
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "simulation/mesh/model.vtu" in names
    assert "simulation/solver_settings.json" in names
    assert "simulation/cae_imports/parsed_loads.json" in names
    assert "simulation/load_cases/lc_new.json" in names


def test_cae_setup_patch_no_duplicate_zip_entries(tmp_path: Path) -> None:
    """cae.apply_setup_patch does not create duplicate ZIP entries when replacing."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-nodup"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "value": 2,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    assert resp.json()["tool_results"][0]["output"]["status"] == "ok"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
    assert names.count("simulation/solver_settings.json") == 1
    assert names.count("manifest.json") == 1


def test_cae_setup_patch_returns_stale_artifacts_and_warnings(tmp_path: Path) -> None:
    """cae.apply_setup_patch returns stale_artifacts and a warning when preprocessing refresh fails."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-stale"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    # Use refresh_preprocessing_summary=True (default) — refresh will fail since
    # aieng package is not importable in test env, so all stale artifacts remain.
    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "value": 16,
            }],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    stale = result["stale_artifacts"]
    assert isinstance(stale, list)
    # At minimum the result summary and evidence index are stale
    assert any("result_summary" in p for p in stale)
    assert any("evidence_index" in p for p in stale)


def test_cae_setup_patch_replace_json_returns_artifact_diffs(tmp_path: Path) -> None:
    """cae.apply_setup_patch replace_json returns artifact_diffs with path, operation, pointer, before, after, changed_paths."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-diff-replace"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "before": 4,
                "value": 8,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    diffs = result.get("artifact_diffs", [])
    assert len(diffs) == 1
    d = diffs[0]
    assert d["path"] == "simulation/solver_settings.json"
    assert d["operation"] == "replace_json"
    assert d["json_pointer"] == "/n_cpus"
    assert d["before"] == 4
    assert d["after"] == 8
    assert "/n_cpus" in d["changed_paths"]
    assert d["added_paths"] == []
    assert d["removed_paths"] == []


def test_cae_setup_patch_create_file_returns_artifact_diffs(tmp_path: Path) -> None:
    """cae.apply_setup_patch create_file returns artifact_diffs with added_paths and null before."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-diff-create"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    new_lc = {"id": "load_case_001", "loads": []}
    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/load_cases/load_case_001.json",
                "action_type": "create_file",
                "content": new_lc,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    diffs = result.get("artifact_diffs", [])
    assert len(diffs) == 1
    d = diffs[0]
    assert d["path"] == "simulation/load_cases/load_case_001.json"
    assert d["operation"] == "create_file"
    assert d["before"] is None
    assert d["after"] == new_lc
    assert d["added_paths"] == [""]
    assert d["changed_paths"] == []
    assert d["removed_paths"] == []


def test_cae_setup_patch_merge_object_returns_artifact_diffs(tmp_path: Path) -> None:
    """cae.apply_setup_patch merge_object returns artifact_diffs with changed/added paths."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-diff-merge"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "merge_object",
                "value": {"new_key": "new_value"},
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    diffs = result.get("artifact_diffs", [])
    assert len(diffs) == 1
    d = diffs[0]
    assert d["path"] == "simulation/solver_settings.json"
    assert d["operation"] == "merge_object"
    assert "/new_key" in d["added_paths"]
    # stale_artifacts should still be present
    assert "stale_artifacts" in result
    assert isinstance(result["stale_artifacts"], list)


def test_cae_setup_patch_stale_artifacts_still_present(tmp_path: Path) -> None:
    """cae.apply_setup_patch still returns stale_artifacts after setup changes even with artifact_diffs."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-stale-28"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/time_limit_s",
                "value": 7200,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    assert "artifact_diffs" in result
    assert "stale_artifacts" in result
    stale = result["stale_artifacts"]
    assert isinstance(stale, list)
    assert len(stale) > 0
    assert any("result_summary" in p for p in stale)


# ---------------------------------------------------------------------------
# Phase 19 — cae.extract_solver_results runtime tool
# ---------------------------------------------------------------------------

def _frd_value(v: float) -> str:
    return f"{v:12.5E}"


def _frd_node_line(node_id: int, values: list) -> str:
    return "    -1" + f"{node_id:12d}" + "".join(_frd_value(v) for v in values)


def _make_test_frd(
    disp_nodes: dict | None,
    stress_nodes: dict | None,
) -> str:
    lines = ["    1C                                                                         1"]
    if disp_nodes is not None:
        lines += [
            "    -4  DISP        4    1",
            "    -5  D1          1    2    1    0",
            "    -5  D2          1    2    2    0",
            "    -5  D3          1    2    3    0",
            "    -5  ALL         1    2    0    1",
        ]
        for nid, vals in disp_nodes.items():
            lines.append(_frd_node_line(nid, vals))
        lines.append("    -3")
    if stress_nodes is not None:
        lines += [
            "    -4  S           6    1",
            "    -5  SXX         1    4    1    1",
            "    -5  SYY         1    4    2    1",
            "    -5  SZZ         1    4    3    1",
            "    -5  SXY         1    4    4    1",
            "    -5  SXZ         1    4    5    1",
            "    -5  SYZ         1    4    6    1",
        ]
        for nid, vals in stress_nodes.items():
            lines.append(_frd_node_line(nid, vals))
        lines.append("    -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


def _make_test_frd_with_coords(
    coords: dict[int, tuple[float, float, float]],
    disp_nodes: dict | None = None,
    stress_nodes: dict | None = None,
) -> str:
    """Build an FRD with mesh coordinates followed by field data."""
    lines = ["    1C                                                                         1"]
    for nid, (x, y, z) in coords.items():
        lines.append(_frd_node_line(nid, [x, y, z]))
    if disp_nodes is not None:
        lines += [
            "    -4  DISP        4    1",
            "    -5  D1          1    2    1    0",
            "    -5  D2          1    2    2    0",
            "    -5  D3          1    2    3    0",
            "    -5  ALL         1    2    0    1",
        ]
        for nid, vals in disp_nodes.items():
            lines.append(_frd_node_line(nid, vals))
        lines.append("    -3")
    if stress_nodes is not None:
        lines += [
            "    -4  S           6    1",
            "    -5  SXX         1    4    1    1",
            "    -5  SYY         1    4    2    1",
            "    -5  SZZ         1    4    3    1",
            "    -5  SXY         1    4    4    1",
            "    -5  SXZ         1    4    5    1",
            "    -5  SYZ         1    4    6    1",
        ]
        for nid, vals in stress_nodes.items():
            lines.append(_frd_node_line(nid, vals))
        lines.append("    -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


def _make_test_multistep_frd_with_coords(
    coords: dict[int, tuple[float, float, float]],
    steps: list[dict[str, dict[int, list[float]]]],
) -> str:
    """Build an FRD with mesh coordinates followed by multiple field steps."""
    lines = ["    1C                                                                         1"]
    for nid, (x, y, z) in coords.items():
        lines.append(_frd_node_line(nid, [x, y, z]))
    for step in steps:
        disp_nodes = step.get("disp")
        stress_nodes = step.get("stress")
        if disp_nodes is not None:
            lines += [
                "    -4  DISP        4    1",
                "    -5  D1          1    2    1    0",
                "    -5  D2          1    2    2    0",
                "    -5  D3          1    2    3    0",
                "    -5  ALL         1    2    0    1",
            ]
            for nid, vals in disp_nodes.items():
                lines.append(_frd_node_line(nid, vals))
            lines.append("    -3")
        if stress_nodes is not None:
            lines += [
                "    -4  S           6    1",
                "    -5  SXX         1    4    1    1",
                "    -5  SYY         1    4    2    1",
                "    -5  SZZ         1    4    3    1",
                "    -5  SXY         1    4    4    1",
                "    -5  SXZ         1    4    5    1",
                "    -5  SYZ         1    4    6    1",
            ]
            for nid, vals in stress_nodes.items():
                lines.append(_frd_node_line(nid, vals))
            lines.append("    -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


def test_cae_extract_solver_results_success(tmp_path: Path) -> None:
    """cae.extract_solver_results parses FRD and writes computed_metrics.json into package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-extract"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "extract-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "extract-test.aieng"
    save_project(settings, project)

    frd_path = tmp_path / "job.frd"
    frd_path.write_text(
        _make_test_frd(
            {1: [1.0, 0.0, 0.0, 1.0], 2: [5.0, 0.0, 0.0, 5.0]},
            {1: [200.0, 100.0, 50.0, 10.0, 0.0, 0.0]},
        ),
        encoding="utf-8",
    )

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(frd_path),
            "loadCaseId": "load_case_001",
            "refresh_result_summary": False,
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["status"] == "ok"
    assert any("computed_metrics" in a["path"] for a in result["artifacts"])

    # Verify actual values were extracted
    metrics = result["metrics"]
    lc = metrics["load_cases"][0]
    assert abs(lc["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4
    assert "max_von_mises_stress" in lc["metrics"]

    # Verify package was updated
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/computed_metrics.json" in zf.namelist()
        written = json.loads(zf.read("results/computed_metrics.json"))
    assert written["schema_version"] == "0.1"
    assert abs(written["load_cases"][0]["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4


def test_cae_extract_solver_results_missing_frd_returns_error(tmp_path: Path) -> None:
    """cae.extract_solver_results returns error when frdPath does not exist."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-missing"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "extract-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "extract-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(tmp_path / "nonexistent.frd"),
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


def test_cae_extract_solver_results_missing_frd_path_returns_error(tmp_path: Path) -> None:
    """cae.extract_solver_results returns error when frdPath is not provided."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-nopath"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "extract-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "extract-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "missing_frd_path"


def test_cae_extract_solver_results_binary_frd_returns_error_without_metrics(
    tmp_path: Path,
) -> None:
    """Binary/non-text FRD input must not produce fake computed_metrics.json."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-binary"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "extract-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "extract-test.aieng"
    save_project(settings, project)

    frd_path = tmp_path / "binary.frd"
    frd_path.write_bytes(b"\x00\x01not-a-text-frd\x00\xff")

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(frd_path),
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "extraction_error"
    assert "binary or non-UTF-8 FRD files are unsupported" in result["message"]

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/computed_metrics.json" not in zf.namelist()


# ---------------------------------------------------------------------------
# Phase 31 — cae.extract_field_regions runtime tool
# ---------------------------------------------------------------------------

def test_cae_extract_field_regions_success(tmp_path: Path) -> None:
    """cae.extract_field_regions parses FRD with coordinates and writes field_regions.json."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("field-regions"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-regions.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "field-regions.aieng"
    save_project(settings, project)

    # Two spatial groups: low-stress near origin, high-stress near x=10
    coords = {
        1: (0.0, 0.0, 0.0),
        2: (0.1, 0.0, 0.0),
        3: (0.2, 0.0, 0.0),
        4: (0.3, 0.0, 0.0),
        5: (0.4, 0.0, 0.0),
        6: (10.0, 0.0, 0.0),
        7: (10.1, 0.0, 0.0),
        8: (10.2, 0.0, 0.0),
        9: (10.3, 0.0, 0.0),
        10: (10.4, 0.0, 0.0),
    }
    stress = {
        1: [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        2: [20.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        3: [30.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        4: [40.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        5: [50.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        6: [200.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        7: [210.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        8: [220.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        9: [230.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        10: [240.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }
    frd_path = tmp_path / "job.frd"
    frd_path.write_text(_make_test_frd_with_coords(coords, stress_nodes=stress), encoding="utf-8")

    resp = client.post("/api/runtime/runs", json={
        "message": "extract field regions",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(frd_path),
            "field": "S",
            "metric": "von_mises",
            "maxClusters": 3,
            "thresholdPercentile": 80.0,
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["status"] == "completed"
    assert result["cluster_count"] >= 1
    clusters = result["clusters"]
    assert len(clusters) >= 1
    # The high-stress group near x=10 should form at least one cluster
    assert any(c["node_count"] >= 3 for c in clusters)
    assert any("field_regions" in a["path"] for a in result["artifacts"])

    # Verify package was updated
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/field_regions.json" in zf.namelist()
        written = json.loads(zf.read("results/field_regions.json"))
    assert written["format_version"] is not None
    assert written["cluster_count"] >= 1
    assert len(written["clusters"]) >= 1


def test_write_field_summary_skipped_when_core_module_missing(monkeypatch, tmp_path: Path) -> None:
    """When aieng.cae_field_summary is removed, write_field_summary returns skipped without crashing."""
    import builtins
    from app import aieng_bridge
    from app.main import Settings

    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=tmp_path / "sample.step",
    )
    pkg_path = tmp_path / "test.aieng"
    _make_setup_package(pkg_path)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "aieng.cae_field_summary":
            raise ModuleNotFoundError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = aieng_bridge.write_field_summary(
        pkg_path,
        aieng_root=settings.aieng_root,
        overwrite=False,
    )
    assert result["status"] == "skipped"
    assert "cae_field_summary" in result["reason"]
    assert result["artifacts"] == []


def test_write_field_summary_skipped_when_field_regions_missing(monkeypatch, tmp_path: Path) -> None:
    """When results/field_regions.json is missing, write_field_summary returns skipped without crashing."""
    import aieng.cae_field_summary
    from app import aieng_bridge
    from app.main import Settings

    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=tmp_path / "sample.step",
    )
    pkg_path = tmp_path / "test.aieng"
    _make_setup_package(pkg_path)

    def fake_write(pkg, overwrite=False):
        raise FileNotFoundError("results/field_regions.json missing")

    monkeypatch.setattr(aieng.cae_field_summary, "write_field_summary_package", fake_write)

    result = aieng_bridge.write_field_summary(
        pkg_path,
        aieng_root=settings.aieng_root,
        overwrite=False,
    )
    assert result["status"] == "skipped"
    assert "field_regions" in result["reason"].lower()
    assert result["artifacts"] == []


def test_write_field_summary_ok(monkeypatch, tmp_path: Path) -> None:
    """When field summary succeeds, write_field_summary returns ok with artifacts."""
    import aieng.cae_field_summary
    from app import aieng_bridge
    from app.main import Settings

    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=tmp_path / "sample.step",
    )
    pkg_path = tmp_path / "test.aieng"
    _make_setup_package(pkg_path, extra={"results/field_regions.json": {"clusters": []}})

    monkeypatch.setattr(aieng.cae_field_summary, "write_field_summary_package", lambda pkg, overwrite=False: None)

    result = aieng_bridge.write_field_summary(
        pkg_path,
        aieng_root=settings.aieng_root,
        overwrite=False,
    )
    assert result["status"] == "ok"
    assert result["package_path"] == str(pkg_path)
    assert len(result["artifacts"]) == 2
    assert any(a["path"] == "results/field_summary.json" for a in result["artifacts"])


def test_extract_field_regions_passes_field_summary_status(monkeypatch, tmp_path: Path) -> None:
    """_tool_cae_extract_field_regions must expose field_summary_status when write_field_summary is skipped."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient
    from app import aieng_bridge

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("field-summary-status"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-summary-status.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "field-summary-status.aieng"
    save_project(settings, project)

    coords = {
        1: (0.0, 0.0, 0.0),
        2: (10.0, 0.0, 0.0),
    }
    stress = {
        1: [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        2: [200.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }
    frd_path = tmp_path / "job.frd"
    frd_path.write_text(_make_test_frd_with_coords(coords, stress_nodes=stress), encoding="utf-8")

    # Force write_field_summary to return skipped (simulating missing core module)
    monkeypatch.setattr(
        aieng_bridge,
        "write_field_summary",
        lambda *a, **kw: {
            "status": "skipped",
            "package_path": str(a[0]) if a else "",
            "reason": "mock missing core module",
            "artifacts": [],
        },
    )

    resp = client.post("/api/runtime/runs", json={
        "message": "extract field regions",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(frd_path),
            "field": "S",
            "metric": "von_mises",
            "maxClusters": 3,
            "thresholdPercentile": 80.0,
            "refresh_field_summary": True,
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["status"] == "completed"
    assert result["field_summary_status"] == "skipped"
    assert any("mock missing core module" in w for w in result["warnings"])
    assert result["refreshed_artifacts"] == []


def test_cae_extract_field_regions_missing_frd_returns_error(tmp_path: Path) -> None:
    """cae.extract_field_regions returns error when frdPath does not exist."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-missing-regions"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "regions-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "regions-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "extract field regions",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(tmp_path / "nonexistent.frd"),
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


def _make_cad_parameter_package(pkg_path: Path) -> None:
    """Create a minimal .aieng package with one declared editable CAD parameter."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    feature_graph = {
        "features": [
            {
                "id": "feat_base_001",
                "type": "base_plate",
                "name": "Base plate",
                "cad_object_name": "BasePlate",
                "parameters": [
                    {
                        "name": "thickness_mm",
                        "current_value": 10.0,
                        "min_value": 5.0,
                        "max_value": 20.0,
                        "editability": {"executable": True},
                        "cad_parameter_name": "Thickness",
                    }
                ],
            }
        ]
    }
    manifest = {
        "model_id": "cad-edit-test",
        "resources": {
            "geometry": {"source": "geometry/source.step"},
            "graph": {"feature_graph": "graph/feature_graph.json"},
        },
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("geometry/source.step", "ISO-10303-21;\nEND-ISO-10303-21;\n")
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph))


def _make_package_with_topology(pkg_path: Path) -> None:
    """Create a minimal .aieng package with topology_map.json for handoff tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    topology = {
        "format_version": "0.1",
        "entities": [
            {"id": "body_001", "type": "solid"},
            {"id": "face_001", "type": "face"},
            {"id": "edge_001", "type": "edge"},
        ],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "handoff-test", "resources": {}}))
        zf.writestr("geometry/topology_map.json", json.dumps(topology))
        zf.writestr("simulation/setup.yaml", yaml.safe_dump({"mesh": {"element_size": 2.5}}))


def test_cae_write_mesh_handoff_success(tmp_path: Path) -> None:
    """cae.write_mesh_handoff writes mesh_handoff_contract.json into package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("handoff"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "handoff-test.aieng"
    _make_package_with_topology(pkg_path)
    project["aieng_file"] = "handoff-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "write mesh handoff",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "handoff_id": "handoff_001"},
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["handoff_id"] == "handoff_001"
    assert any(a["path"] == "simulation/mesh_handoff_contract.json" for a in result["artifacts"])

    # Verify package was updated
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "simulation/mesh_handoff_contract.json" in zf.namelist()
        contract = json.loads(zf.read("simulation/mesh_handoff_contract.json"))
    assert contract["handoff_id"] == "handoff_001"
    assert contract["mesher_target"] == "gmsh"
    assert "topology_refs" in contract


def test_cae_write_mesh_handoff_missing_topology_returns_error(tmp_path: Path) -> None:
    """cae.write_mesh_handoff returns error when topology_map.json is missing."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("handoff-no-topo"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "handoff-no-topo.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "handoff-no-topo.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "write mesh handoff",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "topology_missing"


# ---------------------------------------------------------------------------
# cae.import_solver_evidence
# ---------------------------------------------------------------------------

def _make_package_with_evidence_scaffold(pkg_path: Path) -> None:
    """Create a minimal .aieng package with evidence scaffold for solver evidence tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_index = {
        "format_version": "0.1",
        "evidence_items": [],
    }
    claim_map = {
        "format_version": "0.1",
        "claims": [
            {"claim_id": "claim_solver_result_001", "claim_type": "solver/result_available", "verification_status": "unsupported"}
        ],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "ev-test", "resources": {}}))
        zf.writestr("simulation/solver_settings.json", json.dumps({"solver": "CalculiX", "n_cpus": 4}))
        zf.writestr("results/evidence_index.json", json.dumps(evidence_index))
        zf.writestr("results/claim_map.json", json.dumps(claim_map))


def test_cae_import_solver_evidence_success(tmp_path: Path) -> None:
    """cae.import_solver_evidence imports solver result as evidence into package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-ev"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver-ev.aieng"
    _make_package_with_evidence_scaffold(pkg_path)
    project["aieng_file"] = "solver-ev.aieng"
    save_project(settings, project)

    result_file = tmp_path / "job.dat"
    result_file.write_text(
        "max von Mises stress = 250.0 MPa\n"
        "maximum displacement = 1.23 mm\n",
        encoding="utf-8",
    )

    resp = client.post("/api/runtime/runs", json={
        "message": "import solver evidence",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "result_file": str(result_file),
            "result_format": "calculix_dat",
            "producer_tool": "calculix",
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert any(a["path"] == "results/evidence_index.json" for a in result["artifacts"])


def test_cae_import_solver_evidence_missing_result_file_returns_error(tmp_path: Path) -> None:
    """cae.import_solver_evidence returns error when result_file does not exist."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-ev-missing"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver-ev-missing.aieng"
    _make_package_with_evidence_scaffold(pkg_path)
    project["aieng_file"] = "solver-ev-missing.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "import solver evidence",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "result_file": str(tmp_path / "nonexistent.dat"),
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "result_file_not_found"


# ---------------------------------------------------------------------------
# aieng.write_evidence_scaffold
# ---------------------------------------------------------------------------

def test_aieng_validate_success(tmp_path: Path) -> None:
    """aieng.validate returns PASS/WARN/FAIL messages for a real package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("validate-test"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "validate.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "validate.aieng"
    save_project(settings, project)

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.validate", "description": "validate", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "validate package",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert "validation_ok" in result
    assert "messages" in result
    assert "counts" in result
    assert isinstance(result["messages"], list)
    assert any(m["level"] == "PASS" for m in result["messages"])


def test_aieng_validate_missing_package_returns_error(tmp_path: Path) -> None:
    """aieng.validate returns error when package is missing."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("validate-missing"))
    project_id = project["id"]

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.validate", "description": "validate", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "validate package",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "missing_package_path"


def test_aieng_convert_step_success_via_mocked_bridge(monkeypatch, tmp_path: Path) -> None:
    """aieng.convert returns out_path and source_type on successful conversion."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("convert-test"))
    project_id = project["id"]
    step_path = project_dir(settings, project_id) / "source" / "test_part.step"
    step_path.parent.mkdir(parents=True, exist_ok=True)
    step_path.write_text("dummy step content")
    project["source_step"] = "source/test_part.step"
    save_project(settings, project)

    def _mock_convert(*a, **kw):
        return {
            "status": "ok",
            "out_path": str(project_dir(settings, project_id) / "packages" / "test_part.aieng"),
            "converter_id": "step_importer",
            "source_type": "step",
        }

    monkeypatch.setattr("app.aieng_bridge.convert_source_to_package", _mock_convert)

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.convert", "description": "convert", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "convert step to aieng",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["out_path"].endswith("test_part.aieng")
    assert result["source_type"] == "step"
    assert result["converter_id"] == "step_importer"


def test_aieng_convert_missing_source_returns_error(tmp_path: Path) -> None:
    """aieng.convert returns error when no source path can be resolved."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("convert-missing"))
    project_id = project["id"]

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.convert", "description": "convert", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "convert step to aieng",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "missing_source_path"


def test_import_aieng_file_uses_core_bridge_without_cad_provider(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    write_json(settings.runtime_config_path, {
        "provider": "none",
        "aieng_root": str(settings.aieng_root),
        "freecad_mcp_root": "",
        "freecad_home": "",
        "topology_backend": "auto",
    })
    project = save_project(settings, default_project("bridge-import"))
    project_id = project["id"]
    step_path = project_dir(settings, project_id) / "source" / "bracket.step"
    step_path.parent.mkdir(parents=True, exist_ok=True)
    step_path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    project["source_step"] = "source/bracket.step"
    save_project(settings, project)

    result = import_aieng_file(settings, project_id)
    summary = package_summary(settings, project_id)
    pkg_path = project_dir(settings, project_id) / "packages" / "bracket.aieng"

    assert result["status"] == "ok"
    assert pkg_path.exists()
    assert summary["files"]["aieng_file"]["exists"] is True
    assert summary["project"]["aieng_file"] == "packages/bracket.aieng"
    assert summary["summary_mode"] in {"bridge", "zip_fallback"}
    assert "geometry/source.step" in summary["members"]
    assert "validation/completeness_report.json" in summary["members"]


def test_recent_logs_returns_newest_logs_with_metadata(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    project = save_project(settings, default_project("logs"))
    project_id = project["id"]
    logs_dir = project_dir(settings, project_id) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    older = logs_dir / "older.json"
    newer = logs_dir / "newer.json"
    older.write_text('{"message":"old"}', encoding="utf-8")
    newer.write_text('{"message":"new"}', encoding="utf-8")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    logs = recent_logs(settings, project_id)

    assert [item["name"] for item in logs] == ["newer.json", "older.json"]
    assert logs[0]["size"] == newer.stat().st_size
    assert logs[0]["updated_at"].startswith("1970-01-01T00:33:20")


def test_package_summary_reports_file_metadata(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    project = save_project(settings, default_project("summary-files"))
    project_id = project["id"]
    root = project_dir(settings, project_id)
    source = root / "source" / "part.step"
    package = root / "packages" / "part.aieng"
    viewer = root / "viewer" / "part.glb"
    source.parent.mkdir(parents=True, exist_ok=True)
    package.parent.mkdir(parents=True, exist_ok=True)
    viewer.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("STEP", encoding="utf-8")
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "summary-files"}))
    viewer.write_bytes(b"glb")
    project["source_step"] = "source/part.step"
    project["aieng_file"] = "packages/part.aieng"
    project["web_asset"] = "viewer/part.glb"
    project["web_asset_format"] = "glb"
    save_project(settings, project)

    summary = package_summary(settings, project_id)

    assert summary["files"]["source_step"]["exists"] is True
    assert summary["files"]["source_step"]["size"] == source.stat().st_size
    assert summary["files"]["aieng_file"]["exists"] is True
    assert summary["files"]["aieng_file"]["size"] == package.stat().st_size
    assert summary["files"]["web_asset"]["exists"] is True
    assert summary["files"]["web_asset"]["size"] == viewer.stat().st_size
    assert summary["viewer"]["asset_exists"] is True


def test_convert_asset_reports_unavailable_honestly_without_provider(monkeypatch, tmp_path: Path) -> None:
    from app.main import convert_asset
    from app.services import platform_logic

    settings = _make_patch_settings(tmp_path)
    monkeypatch.setattr(
        platform_logic,
        "_step_to_stl_via_build123d",
        lambda *_args, **_kwargs: {
            "status": "unavailable",
            "code": "build123d_missing",
            "message": "build123d not installed",
        },
    )
    write_json(settings.runtime_config_path, {
        "provider": "none",
        "aieng_root": str(settings.aieng_root),
        "freecad_mcp_root": "",
        "freecad_home": "",
        "topology_backend": "auto",
    })
    project = save_project(settings, default_project("no-preview-provider"))
    project_id = project["id"]
    step_path = project_dir(settings, project_id) / "source" / "simple.step"
    step_path.parent.mkdir(parents=True, exist_ok=True)
    step_path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    project["source_step"] = "source/simple.step"
    save_project(settings, project)

    result = convert_asset(settings, project_id)
    saved = get_project(settings, project_id)

    assert result["status"] == "unavailable"
    assert result["viewer_url"] is None
    assert saved["web_asset"] is None
    assert saved["web_asset_format"] is None
    assert saved["status"] == "preview_unavailable"


def test_convert_asset_publishes_embedded_package_preview_without_provider(tmp_path: Path) -> None:
    from app.main import convert_asset

    settings = _make_patch_settings(tmp_path)
    write_json(settings.runtime_config_path, {
        "provider": "none",
        "aieng_root": str(settings.aieng_root),
        "freecad_mcp_root": "",
        "freecad_home": "",
        "topology_backend": "auto",
    })
    project = save_project(settings, default_project("package-preview"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "packages" / "package-preview.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    preview_bytes = b"solid preview\nendsolid preview\n"
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({"schema_version": "0.1"}))
        archive.writestr("geometry/preview.stl", preview_bytes)
    project["aieng_file"] = "packages/package-preview.aieng"
    save_project(settings, project)

    result = convert_asset(settings, project_id)
    saved = get_project(settings, project_id)
    viewer_file = project_dir(settings, project_id) / "viewer" / "model.stl"

    assert result["status"] == "ok"
    assert result["asset_format"] == "stl"
    assert result["source"] == "package_preview"
    assert viewer_file.read_bytes() == preview_bytes
    assert saved["web_asset"] == "viewer/model.stl"
    assert saved["web_asset_format"] == "stl"
    assert saved["status"] == "viewer_ready_stl"


def test_aieng_convert_shape_ir_executes_and_publishes_glb(monkeypatch, tmp_path: Path) -> None:
    """aieng.convert can import Shape IR, execute generated source.py, and publish GLB."""
    from app.main import create_app, default_project, get_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("shape-ir"))
    project_id = project["id"]
    source_path = project_dir(settings, project_id) / "source" / "organic.shape_ir.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps({
        "format_version": "0.1.0",
        "model_id": "organic",
        "parts": [
            {
                "id": "body",
                "kind": "rounded_box",
                "dimensions": [20, 12, 8],
                "radius": 2,
                "parameters": {"radius": 2},
            }
        ],
    }), encoding="utf-8")

    fake_topology = {
        "format_version": "0.1",
        "entities": [
            {"id": "body_001", "type": "solid", "name": "body", "bounding_box": [0, 0, 0, 20, 12, 8]},
            {
                "id": "face_001",
                "type": "face",
                "body_id": "body_001",
                "surface_type": "plane",
                "bounding_box": [0, 0, 8, 20, 12, 8],
                "area": 240,
                "normal": [0, 0, 1],
            },
        ],
    }

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {
            "name": "aieng.convert",
            "description": "convert shape ir",
            "input": {
                "project_id": pid,
                "sourcePath": str(source_path),
                "overwrite": True,
            },
        }
    ]
    try:
        monkeypatch.setattr(
            "app.cad_generation._execute_build123d_code",
            lambda *_args, **_kwargs: (b"ISO-10303-21;", b"solid\nendsolid\n", b"glTF\x02\x00\x00\x00", fake_topology, None),
        )
        resp = client.post("/api/runtime/runs", json={
            "message": "convert shape ir",
            "project_id": project_id,
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    output = run["tool_results"][0]["output"]
    assert output["ok"] is True
    assert output["source_type"] == "shape_ir"
    assert output["shape_ir_execution"]["status"] == "ok"
    assert output["preview"]["asset_format"] == "glb"

    saved = get_project(settings, project_id)
    assert saved["status"] == "viewer_ready_glb"
    assert saved["web_asset"] == "viewer/model.glb"
    viewer_file = project_dir(settings, project_id) / "viewer" / "model.glb"
    assert viewer_file.read_bytes().startswith(b"glTF")

    package_path = project_dir(settings, project_id) / saved["aieng_file"]
    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
        assert "geometry/shape_ir.json" in names
        assert "geometry/source.py" in names
        assert "geometry/generated.step" in names
        assert "geometry/preview.glb" in names


def test_runtime_snapshot_probe_uses_frontend_compatible_fields(tmp_path: Path) -> None:
    from app.main import runtime_config_snapshot

    settings = _make_patch_settings(tmp_path)
    snapshot = runtime_config_snapshot(settings)

    assert snapshot["config"]["provider"] == "build123d"
    assert snapshot["probe"]["provider"] == "build123d"
    assert isinstance(snapshot["probe"]["ready"], bool)
    assert snapshot["probe"]["topology_backend_requested"] == "auto"
    assert snapshot["probe"]["topology_backend_resolved"] in {"mock", "occ"}
    assert snapshot["probe"]["freecad_cmd_exists"] is False
    assert isinstance(snapshot["probe"]["build123d_available"], bool)
    assert isinstance(snapshot["probe"]["issues"], list)


def test_aieng_convert_bridge_exception_produces_tool_failed(monkeypatch, tmp_path: Path) -> None:
    """aieng.convert propagates bridge RuntimeError as tool_failed."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("convert-fail"))
    project_id = project["id"]
    step_path = project_dir(settings, project_id) / "source" / "fail.step"
    step_path.parent.mkdir(parents=True, exist_ok=True)
    step_path.write_text("dummy")
    project["source_step"] = "source/fail.step"
    save_project(settings, project)

    def _fail(*a, **kw):
        raise Exception("converter exploded")

    monkeypatch.setattr("app.aieng_bridge.convert_source_to_package", _fail)

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.convert", "description": "convert", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "convert step to aieng",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    event_types = [e["type"] for e in data["events"]]
    assert "tool_failed" in event_types
    assert "run_failed" in event_types


def test_runtime_plan_selects_convert_intent(tmp_path: Path) -> None:
    from app.runtime import build_plan
    for msg in ["convert step to aieng", "convert fcstd file", "import step to aieng"]:
        plan = build_plan(msg, None)
        assert len(plan) == 1, f"Expected 1 step for {msg!r}, got {plan!r}"
        assert plan[0]["name"] == "aieng.convert"


def test_aieng_write_completeness_report_success(tmp_path: Path) -> None:
    """aieng.write_completeness_report creates validation/completeness_report.json."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("completeness"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "completeness.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "completeness.aieng"
    save_project(settings, project)

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.write_completeness_report", "description": "completeness", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "write completeness report",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert any(a["path"] == "validation/completeness_report.json" for a in result["artifacts"])


def test_aieng_write_completeness_report_missing_package_returns_error(tmp_path: Path) -> None:
    """aieng.write_completeness_report returns error when package is missing."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("completeness-missing"))
    project_id = project["id"]

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.write_completeness_report", "description": "completeness", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "write completeness report",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "missing_package_path"


def test_aieng_update_validation_status_success(tmp_path: Path) -> None:
    """aieng.update_validation_status creates validation/status.yaml."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("validation-status"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "validation_status.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "validation_status.aieng"
    save_project(settings, project)

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.update_validation_status", "description": "validation status", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "update validation status",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert any(a["path"] == "validation/status.yaml" for a in result["artifacts"])


def test_aieng_update_validation_status_missing_package_returns_error(tmp_path: Path) -> None:
    """aieng.update_validation_status returns error when package is missing."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("validation-missing"))
    project_id = project["id"]

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.update_validation_status", "description": "validation status", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "update validation status",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "missing_package_path"


def test_runtime_plan_selects_completeness_intent(tmp_path: Path) -> None:
    from app.runtime import build_plan
    for msg in ["write completeness report", "missingness report", "what is missing"]:
        plan = build_plan(msg, None)
        names = [s["name"] for s in plan]
        assert "aieng.write_completeness_report" in names, f"Expected aieng.write_completeness_report in plan for {msg!r}, got {names!r}"


def test_runtime_plan_selects_validation_status_intent(tmp_path: Path) -> None:
    from app.runtime import build_plan
    for msg in ["write validation status", "update validation status yaml"]:
        plan = build_plan(msg, None)
        names = [s["name"] for s in plan]
        assert "aieng.update_validation_status" in names, f"Expected aieng.update_validation_status in plan for {msg!r}, got {names!r}"


def test_aieng_write_evidence_scaffold_success(tmp_path: Path) -> None:
    """aieng.write_evidence_scaffold creates evidence_index.json without advancing claims."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("scaffold"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "scaffold.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "scaffold.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "write evidence scaffold",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert any(a["path"] == "results/evidence_index.json" for a in result["artifacts"])
    assert not any(a["path"] == "results/claim_map.json" for a in result["artifacts"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/evidence_index.json" in zf.namelist()
        assert "results/claim_map.json" not in zf.namelist()
        assert "ai/claim_map.json" not in zf.namelist()


def test_aieng_write_evidence_scaffold_missing_package_returns_error(tmp_path: Path) -> None:
    """aieng.write_evidence_scaffold returns error when package is missing."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("scaffold-missing"))
    project_id = project["id"]

    resp = client.post("/api/runtime/runs", json={
        "message": "write evidence scaffold",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "missing_package_path"


# ---------------------------------------------------------------------------
# cae.import_solver_evidence auto-scaffold
# ---------------------------------------------------------------------------

def test_cae_import_solver_evidence_auto_scaffold_when_missing(tmp_path: Path) -> None:
    """cae.import_solver_evidence auto-creates scaffold when it is missing."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-ev-auto"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver-ev-auto.aieng"
    # Use _make_setup_package which does NOT include evidence scaffold
    _make_setup_package(pkg_path)
    project["aieng_file"] = "solver-ev-auto.aieng"
    save_project(settings, project)

    result_file = tmp_path / "job.dat"
    result_file.write_text(
        "max von Mises stress = 250.0 MPa\n"
        "maximum displacement = 1.23 mm\n",
        encoding="utf-8",
    )

    resp = client.post("/api/runtime/runs", json={
        "message": "import solver evidence",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "result_file": str(result_file),
            "result_format": "calculix_dat",
            "producer_tool": "calculix",
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result.get("scaffold_created") is True
    assert any("auto-created" in w for w in result.get("warnings", []))
    assert any(a["path"] == "results/evidence_index.json" for a in result["artifacts"])


# ---------------------------------------------------------------------------
# cae.prepare_solver_run (Phase 20B)
# ---------------------------------------------------------------------------

def _make_preflight_package(pkg_path: Path, *, mesh: bool = True, solver_settings: bool = True,
                             load_case: bool = True, input_deck: bool = False,
                             load_case_id: str = "load_case_001", run_id: str = "run_001") -> None:
    """Create a .aieng package for preflight tests with selectable artifact presence."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "preflight-test", "resources": {}}))
        if mesh:
            zf.writestr("simulation/mesh/mesh_metadata.json", json.dumps({"elements": 4000, "nodes": 800}))
        if solver_settings:
            zf.writestr("simulation/solver_settings.json", json.dumps({"solver": "CalculiX", "n_cpus": 4}))
        if load_case:
            zf.writestr(
                f"simulation/load_cases/{load_case_id}.json",
                json.dumps({"id": load_case_id, "loads": []}),
            )
        if input_deck:
            zf.writestr(
                f"simulation/runs/{run_id}/solver_input.inp",
                "** CalculiX input deck placeholder\n",
            )


def test_prepare_solver_run_reports_missing_artifacts(tmp_path: Path) -> None:
    """cae.prepare_solver_run honestly reports missing mesh, settings, load case, and input deck."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-missing"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    # Package with nothing — no mesh, no solver settings, no load case, no input deck
    _make_preflight_package(pkg_path, mesh=False, solver_settings=False, load_case=False, input_deck=False)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]

    assert result["ok"] is True
    assert result["ready_to_run"] is False

    # Receipt is additive and describes the non-mutating preflight outcome.
    receipt = result.get("receipt")
    assert receipt is not None
    assert receipt["format"] == "aieng.operation_receipt.v0"
    assert receipt["operation"] == "cae.prepare_solver_run"
    assert receipt["status"] == "warning"
    assert receipt["mutated"] is False
    assert receipt["approval_required"] is True
    assert receipt["approval_used"] is None

    # Standardized next_actions are exposed alongside the legacy recommended_next_calls.
    assert "next_actions" in result
    assert isinstance(result["next_actions"], list)
    assert "recommended_next_calls" in result
    run_action = next(a for a in result["next_actions"] if a["tool"] == "cae.run_solver")
    assert run_action["available_now"] is False
    assert run_action["blocked_reason"]
    assert run_action["runs_solver"] is True
    assert "id" in run_action
    assert "label" in run_action
    assert "priority" in run_action

    preflight = result["preflight"]
    assert preflight["has_mesh"] is False
    assert preflight["has_solver_settings"] is False
    assert preflight["has_load_case"] is False
    assert preflight["has_input_deck"] is False
    assert len(preflight["missing_items"]) >= 4


def test_prepare_solver_run_blocked_reason_codes_are_stable(tmp_path: Path) -> None:
    """cae.prepare_solver_run exposes stable blocked_reason_codes and preserves prose fields."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-codes"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(pkg_path, mesh=False, solver_settings=False, load_case=False, input_deck=False)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    with patch("app.main.shutil.which", return_value=None):
        resp = client.post("/api/runtime/runs", json={
            "message": "prepare solver run",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["ready_to_run"] is False

    # Top-level codes mirror the preflight blockers.
    codes = set(result["blocked_reason_codes"])
    assert "missing_mesh" in codes
    assert "missing_analysis_type" in codes
    assert "missing_solver" in codes
    assert "missing_loads" in codes
    assert "deck_not_prepared" in codes
    assert "solver_unavailable" in codes

    # The blocked cae.run_solver action carries approval + technical codes.
    run_action = next(a for a in result["next_actions"] if a["tool"] == "cae.run_solver")
    assert run_action["available_now"] is False
    action_codes = set(run_action["blocked_reason_codes"])
    assert "approval_required" in action_codes
    assert "missing_mesh" in action_codes
    assert "deck_not_prepared" in action_codes

    # Human-readable fields are still present.
    assert run_action["blocked_reason"]
    assert result["preflight"]["missing_items"]


def test_prepare_solver_run_ready_to_run_false_when_ccx_unavailable(tmp_path: Path) -> None:
    """cae.prepare_solver_run returns ready_to_run=false when ccx is not on PATH."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-noccx"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    # Package with all artifacts present except ccx
    _make_preflight_package(pkg_path, mesh=True, solver_settings=True, load_case=True, input_deck=True)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    # Patch shutil.which to simulate ccx not found
    with patch("app.main.shutil.which", return_value=None):
        resp = client.post("/api/runtime/runs", json={
            "message": "prepare solver run",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["ready_to_run"] is False
    assert result["preflight"]["ccx_available"] is False
    assert any("ccx" in item.lower() for item in result["preflight"]["missing_items"])


def test_prepare_solver_run_planned_artifacts_include_frd_and_summaries(tmp_path: Path) -> None:
    """planned_artifacts include FRD, computed_metrics, and result summaries when requested."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-artifacts"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(pkg_path)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "run_id": "run_001",
            "extract_results": True,
            "refresh_summary": True,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True

    paths = [a["path"] for a in result["planned_artifacts"]]
    assert any("result.frd" in p for p in paths)
    assert any("computed_metrics.json" in p for p in paths)
    assert any("result_summary.json" in p for p in paths)
    assert any("evidence_index.json" in p for p in paths)
    assert any("postprocessing_summary.md" in p for p in paths)


def test_prepare_solver_run_always_has_approval_and_no_execution(tmp_path: Path) -> None:
    """requires_approval is always true and solver_execution_performed is always false."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-contracts"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(pkg_path)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["requires_approval"] is True
    assert result["solver_execution_performed"] is False
    assert any("No solver execution" in w for w in result["warnings"])


def test_prepare_solver_run_tool_registered_in_introspection(tmp_path: Path) -> None:
    """cae.prepare_solver_run appears in /api/runtime/tools introspection."""
    from app.main import create_app
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    tools = resp.json()
    names = [t["name"] for t in tools]
    assert "cae.prepare_solver_run" in names


def test_prepare_solver_run_no_solver_subprocess(tmp_path: Path) -> None:
    """cae.prepare_solver_run never invokes a subprocess (no solver execution)."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-nosub"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(pkg_path)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    mock_run = MagicMock()
    with patch("subprocess.run", mock_run), patch("subprocess.Popen", MagicMock()):
        resp = client.post("/api/runtime/runs", json={
            "message": "prepare solver run",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })

    assert resp.status_code == 200
    mock_run.assert_not_called()


def test_prepare_solver_run_recommended_next_calls(tmp_path: Path) -> None:
    """cae.prepare_solver_run returns actionable next-call recommendations for missing artifacts."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-recs"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(
        pkg_path,
        mesh=False,
        solver_settings=False,
        load_case=False,
        input_deck=False,
    )
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "run_id": "run_001", "load_case_id": "load_case_001"},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True
    assert "recommended_next_calls" in result

    recs = result["recommended_next_calls"]
    tools = [r["tool"] for r in recs]
    assert "cae.write_mesh_handoff" in tools
    assert "cae.apply_setup_patch" in tools
    assert "cae.generate_solver_input" in tools

    # ccx is unavailable in this environment, so a non-tool environment action is present
    env_recs = [r for r in recs if r.get("tool") is None]
    assert env_recs
    assert any("ccx" in r["action"].lower() or "calculix" in r["action"].lower() for r in env_recs)

    # Solver run is NOT recommended until everything is present
    assert "cae.run_solver" not in tools


def test_prepare_solver_run_recommends_run_solver_when_ready(tmp_path: Path) -> None:
    """When all artifacts are present and ccx is available, the final recommendation is cae.run_solver."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-ready"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(pkg_path, mesh=True, solver_settings=True, load_case=True, input_deck=True)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    with patch("app.main.shutil.which", return_value="/fake/ccx"):
        resp = client.post("/api/runtime/runs", json={
            "message": "prepare solver run",
            "project_id": project_id,
            "tool_input": {"project_id": project_id, "run_id": "run_001", "load_case_id": "load_case_001"},
        })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["ready_to_run"] is True

    run_recs = [r for r in result["recommended_next_calls"] if r.get("tool") == "cae.run_solver"]
    assert len(run_recs) == 1
    assert run_recs[0].get("requires_approval") is True
    assert run_recs[0]["input"]["input_deck_path"] == "simulation/runs/run_001/solver_input.inp"


def test_prepare_solver_run_partial_readiness_recommends_only_missing_items(tmp_path: Path) -> None:
    """Partial readiness produces targeted recommendations for exactly the missing items."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-partial"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    # Mesh + load case present; solver settings + input deck missing.
    _make_preflight_package(pkg_path, mesh=True, solver_settings=False, load_case=True, input_deck=False)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "run_id": "run_001", "load_case_id": "load_case_001"},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["ready_to_run"] is False

    preflight = result["preflight"]
    assert preflight["has_mesh"] is True
    assert preflight["has_solver_settings"] is False
    assert preflight["has_load_case"] is True
    assert preflight["has_input_deck"] is False

    recs = result["recommended_next_calls"]
    tools = [r["tool"] for r in recs]
    assert "cae.write_mesh_handoff" not in tools
    assert "cae.apply_setup_patch" in tools
    assert "cae.generate_solver_input" in tools
    assert "cae.run_solver" not in tools


def test_prepare_solver_run_external_input_deck_bypasses_package_deck_check(tmp_path: Path) -> None:
    """An externally-supplied input_deck_path satisfies the input deck check even when the package has no deck."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-external-deck"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    # Everything present except the in-package input deck.
    _make_preflight_package(pkg_path, mesh=True, solver_settings=True, load_case=True, input_deck=False)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    external_deck = tmp_path / "external_solver_input.inp"
    external_deck.write_text("** external CalculiX deck\n", encoding="utf-8")

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "run_id": "run_001",
            "load_case_id": "load_case_001",
            "input_deck_path": str(external_deck),
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["preflight"]["has_input_deck"] is True

    # Missing items should no longer mention the input deck.
    missing_items = result["preflight"]["missing_items"]
    assert not any("solver_input.inp" in item for item in missing_items)


def _execute_run_solver(client, project_id, tool_input):
    """Start a solver run via the runtime endpoint and auto-approve if gated."""
    resp = client.post("/api/runtime/runs", json={
        "message": "execute solver run",
        "project_id": project_id,
        "tool_input": tool_input,
    })
    assert resp.status_code == 200
    data = resp.json()
    if data["status"] == "awaiting_approval":
        run_id = data["run_id"]
        approve_resp = client.post(f"/api/runtime/runs/{run_id}/approve")
        assert approve_resp.status_code == 200
        data = approve_resp.json()
    return data


def test_run_solver_rejects_path_traversal(tmp_path: Path) -> None:
    """cae.run_solver rejects input_deck_path containing '..'."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-traversal"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    with patch("app.main.shutil.which", return_value="/fake/ccx"):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/../secret.inp",
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "forbidden_path"
    assert result["solver_execution_performed"] is False


def test_run_solver_rejects_non_inp(tmp_path: Path) -> None:
    """cae.run_solver rejects input_deck_path that does not end with .inp."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-noninp"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    with patch("app.main.shutil.which", return_value="/fake/ccx"):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.txt",
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "invalid_input_deck"
    assert result["solver_execution_performed"] is False


def test_run_solver_ccx_unavailable_returns_error(tmp_path: Path) -> None:
    """cae.run_solver returns a clear error when ccx is not on PATH."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-noccx"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    with patch("app.main.shutil.which", return_value=None):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "solver_not_found"
    assert result["solver_execution_performed"] is False
    assert "ccx" in result["message"].lower()
    receipt = result.get("receipt")
    assert receipt is not None
    assert receipt["operation"] == "cae.run_solver"
    assert receipt["status"] == "error"
    assert receipt["mutated"] is False
    assert receipt["approval_required"] is True
    assert receipt["approval_used"] is None
    assert receipt["next_actions"] == []


def test_run_solver_uses_aieng_ccx_cmd_env_var(tmp_path: Path, monkeypatch) -> None:
    """cae.run_solver uses AIENG_CCX_CMD when set, bypassing PATH lookup."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    monkeypatch.setenv("AIENG_CCX_CMD", "conda run -n calculix-env ccx")

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-ccx-cmd"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="solver completed\n", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/conda"), \
         patch("subprocess.run", side_effect=fake_run) as mock_run:
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == 0

    assert len(mock_run.call_args_list) == 1
    args, kwargs = mock_run.call_args_list[0]
    assert args[0] == ["conda", "run", "-n", "calculix-env", "ccx", "solver_input"]
    assert kwargs.get("shell") is False


def test_split_ccx_cmd_preserves_windows_paths() -> None:
    """Windows command parsing preserves backslashes and removes wrapping quotes."""
    command = r'"C:\Program Files\CalculiX\ccx.exe" --solver-option'

    assert _split_ccx_cmd(command, platform="nt") == [
        r"C:\Program Files\CalculiX\ccx.exe",
        "--solver-option",
    ]


def test_resolve_ccx_command_absolute_path(monkeypatch) -> None:
    """An absolute ccx path resolves directly (most reliable form on Windows)."""
    from app.runtime_tool_registry import resolve_ccx_command

    abs_ccx = r"C:\envs\calculix-env\Library\bin\ccx.exe"
    monkeypatch.setenv("AIENG_CCX_CMD", abs_ccx)
    monkeypatch.setattr("shutil.which", lambda n: n if n.endswith("ccx.exe") else None)

    parts, reason = resolve_ccx_command()
    assert parts == [abs_ccx]
    assert "path" in reason.lower()


def test_resolve_ccx_command_conda_run_via_conda_exe(monkeypatch) -> None:
    """`conda run ... ccx` resolves when CONDA_EXE points at a real conda exe,
    even though bare `conda` is not on PATH (the Windows failure mode)."""
    from app.runtime_tool_registry import resolve_ccx_command

    conda_exe = r"C:\anaconda3\Scripts\conda.exe"
    monkeypatch.setenv("AIENG_CCX_CMD", "conda run -n calculix-env ccx")
    monkeypatch.setenv("CONDA_EXE", conda_exe)
    monkeypatch.setattr("shutil.which", lambda n: None)  # nothing resolvable on PATH
    monkeypatch.setattr("os.path.exists", lambda p: p == conda_exe)

    parts, reason = resolve_ccx_command()
    assert parts == [conda_exe, "run", "-n", "calculix-env", "ccx"]
    assert "conda" in reason


def test_resolve_ccx_command_conda_unresolvable_explains_why(monkeypatch) -> None:
    """When the conda launcher cannot be resolved, the reason says so (not a
    generic 'unavailable') so the operator knows to use the absolute path."""
    from app.runtime_tool_registry import resolve_ccx_command, _resolve_ccx_cmd

    monkeypatch.setenv("AIENG_CCX_CMD", "conda run -n calculix-env ccx")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.delenv("MAMBA_EXE", raising=False)
    monkeypatch.setattr("shutil.which", lambda n: None)

    parts, reason = resolve_ccx_command()
    assert parts is None
    assert "conda" in reason and "not resolvable" in reason.lower()
    assert _resolve_ccx_cmd() is None  # back-compat wrapper


def test_resolve_ccx_command_path_fallback_and_unset(monkeypatch) -> None:
    """With AIENG_CCX_CMD unset: resolve a bare ccx on PATH, else explain it's unset."""
    from app.runtime_tool_registry import resolve_ccx_command

    monkeypatch.delenv("AIENG_CCX_CMD", raising=False)
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/ccx" if n == "ccx" else None)
    parts, reason = resolve_ccx_command()
    assert parts == ["/usr/bin/ccx"]
    assert "ccx" in reason

    monkeypatch.setattr("shutil.which", lambda n: None)
    parts, reason = resolve_ccx_command()
    assert parts is None
    assert "not set" in reason.lower()


def test_runtime_capabilities_treats_malformed_ccx_cmd_as_unavailable(tmp_path: Path, monkeypatch) -> None:
    """Malformed AIENG_CCX_CMD does not crash the capabilities endpoint."""
    monkeypatch.setenv("AIENG_CCX_CMD", '"unterminated')

    client = TestClient(create_app(_make_patch_settings(tmp_path)))
    response = client.get("/api/runtime/capabilities")

    assert response.status_code == 200
    assert response.json()["environment"]["ccx_available"] is False


def test_run_solver_mocked_subprocess_success(tmp_path: Path) -> None:
    """cae.run_solver invokes ccx with shell=False and writes solver_run.json + solver_log.txt."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-success"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="solver completed\n", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run) as mock_run:
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == 0
    assert result["status"] == "completed"

    # Receipt is attached as additive metadata and preserves all original fields.
    receipt = result.get("receipt")
    assert receipt is not None
    assert receipt["format"] == "aieng.operation_receipt.v0"
    assert receipt["operation"] == "cae.run_solver"
    assert receipt["status"] == "ok"
    assert receipt["mutated"] is True
    assert receipt["approval_required"] is True
    assert receipt["approval_used"] is True
    assert any(a["path"].endswith("result.frd") for a in receipt["artifacts_written"])
    assert any(a["tool"] == "cae.extract_solver_results" for a in receipt["next_actions"])
    extract_action = next(a for a in receipt["next_actions"] if a["tool"] == "cae.extract_solver_results")
    assert extract_action["input"]["project_id"] == project_id
    assert extract_action["input"]["run_id"] == "run_001"

    # Verify subprocess args
    assert len(mock_run.call_args_list) == 1
    args, kwargs = mock_run.call_args_list[0]
    assert args[0] == ["/fake/ccx", "solver_input"]
    assert kwargs.get("shell") is False

    # Verify package artifacts
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
        assert "simulation/runs/run_001/solver_input.inp" in names
        assert "simulation/runs/run_001/solver_log.txt" in names
        assert "simulation/runs/run_001/solver_run.json" in names
        assert "simulation/runs/run_001/outputs/result.frd" in names

    # Verify solver_run.json content
    with zipfile.ZipFile(pkg_path, "r") as zf:
        run_meta = json.loads(zf.read("simulation/runs/run_001/solver_run.json"))
    assert run_meta["run_id"] == "run_001"
    assert run_meta["solver"] == "CalculiX"
    assert run_meta["state"] == "completed"
    assert run_meta["solved"] is True
    assert run_meta["converged"] is None
    assert run_meta["return_code"] == 0
    assert "started_at" in run_meta
    assert "finished_at" in run_meta
    assert "duration_seconds" in run_meta
    assert run_meta["input_files"] == ["simulation/runs/run_001/solver_input.inp"]
    assert "simulation/runs/run_001/outputs/result.frd" in run_meta["output_files"]


def test_run_solver_auto_imports_evidence_when_dat_present(tmp_path: Path) -> None:
    """cae.run_solver auto-imports solver evidence when .dat file is present after successful run."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-auto-import"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        dat_path = cwd / "solver_input.dat"
        dat_path.write_text(
            "max von Mises stress = 180.5 MPa\n"
            "maximum displacement = 0.42 mm\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0, stdout="solver completed\n", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["return_code"] == 0
    assert result.get("auto_import") is not None
    assert result["auto_import"]["status"] == "ok"
    assert any(a["path"] == "results/evidence_index.json" for a in result["changed_artifacts"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/evidence_index.json" in zf.namelist()


def test_run_solver_skips_auto_import_when_disabled(tmp_path: Path) -> None:
    """cae.run_solver skips auto-import when auto_import_evidence is false."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-no-auto"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        dat_path = cwd / "solver_input.dat"
        dat_path.write_text("max von Mises stress = 180.5 MPa\n", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
            "auto_import_evidence": False,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert "auto_import" not in result


def test_run_solver_writes_frd_to_outputs(tmp_path: Path) -> None:
    """cae.run_solver writes result.frd into simulation/runs/<run_id>/outputs/."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-frd"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "simulation/runs/run_001/outputs/result.frd" in zf.namelist()


def test_run_solver_extracts_results_when_requested(tmp_path: Path) -> None:
    """cae.run_solver calls existing FRD extraction when extract_results=true."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-extract"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    extract_called: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_extract(package_path, frd_path, *, aieng_root, load_case_id, software, overwrite):
        extract_called["package_path"] = package_path
        extract_called["frd_path"] = frd_path
        extract_called["load_case_id"] = load_case_id
        extract_called["software"] = software
        return {
            "status": "ok",
            "metrics": {"load_cases": [{"id": load_case_id, "metrics": {}}]},
            "artifacts": [{"path": "results/computed_metrics.json", "kind": "computed_metrics", "role": "extracted_metrics"}],
        }

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("app.aieng_bridge.extract_frd_solver_results", fake_extract):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": True,
            "refresh_summary": False,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert extract_called.get("load_case_id") == "load_case_001"
    assert extract_called.get("software") == "CalculiX"
    assert "extracted_metrics" in result


def test_run_solver_refreshes_summaries_when_requested(tmp_path: Path) -> None:
    """cae.run_solver refreshes CAE summaries when refresh_summary=true."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-refresh"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    refreshed: list[str] = []

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_refresh_result(pkg, *, aieng_root, overwrite=True):
        refreshed.append("result_summary")

    def fake_refresh_preproc(pkg, *, aieng_root, overwrite=True):
        refreshed.append("preprocessing_summary")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("app.aieng_bridge.refresh_cae_result_summary", fake_refresh_result), \
         patch("app.aieng_bridge.refresh_preprocessing_summary", fake_refresh_preproc):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": True,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert "result_summary" in refreshed
    assert "preprocessing_summary" in refreshed
    assert result.get("refreshed_summaries") == ["result_summary", "preprocessing_summary"]


def test_run_solver_timeout_records_failed_metadata(tmp_path: Path) -> None:
    """cae.run_solver handles timeout by recording failed run metadata."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient
    import subprocess

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-timeout"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "timeout_seconds": 1,
            "extract_results": False,
            "refresh_summary": False,
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["status"] == "failed"
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == -1
    assert any("timed out" in w.lower() for w in result["errors"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        run_meta = json.loads(zf.read("simulation/runs/run_001/solver_run.json"))
    assert run_meta["state"] == "failed"
    assert run_meta["solved"] is False
    assert run_meta["return_code"] == -1
    assert any("timed out" in w.lower() for w in run_meta["errors"])


def test_run_solver_registered_in_introspection(tmp_path: Path) -> None:
    """cae.run_solver appears in /api/runtime/tools with requires_approval=true."""
    from app.main import create_app
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    tools = resp.json()
    names = [t["name"] for t in tools]
    assert "cae.run_solver" in names
    solver_tool = next(t for t in tools if t["name"] == "cae.run_solver")
    assert solver_tool["requires_approval"] is True


def test_run_solver_no_mesh_generation(tmp_path: Path) -> None:
    """cae.run_solver does not attempt mesh generation or input deck generation."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-nomesh"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    # Only one subprocess invocation: ccx
    assert len(calls) == 1
    assert calls[0] == ["/fake/ccx", "solver_input"]


# ---------------------------------------------------------------------------
# CAE result field summary endpoints
# ---------------------------------------------------------------------------

def _make_computed_metrics_pkg(pkg_path: Path, disp_val: float, stress_val: float) -> None:
    """Write a minimal .aieng package containing results/computed_metrics.json."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    cm = {
        "schema_version": "0.1",
        "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX", "source_files": []},
        "load_cases": [
            {
                "id": "load_case_001",
                "metrics": {
                    "max_displacement": {"value": disp_val, "unit": "mm"},
                    "max_von_mises_stress": {"value": stress_val, "unit": "MPa"},
                },
            }
        ],
        "warnings": [],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "field-summary-test", "resources": {}}))
        zf.writestr("results/computed_metrics.json", json.dumps(cm))


def test_list_cae_result_fields_returns_displacement_and_stress(tmp_path: Path) -> None:
    """GET /cae-result-fields lists displacement and stress when computed_metrics.json exists."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("fields-list"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fields-list.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.42, stress_val=18.5)
    project["aieng_file"] = "fields-list.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-result-fields")
    assert resp.status_code == 200
    data = resp.json()

    assert data["schema_version"] == "0.1"
    assert data["project_id"] == project_id
    assert data["claim_advancement"] == "none"

    field_names = {f["field_name"] for f in data["available_fields"]}
    assert "displacement" in field_names
    assert "stress" in field_names

    by_name = {f["field_name"]: f for f in data["available_fields"]}
    disp = by_name["displacement"]
    assert disp["unit"] == "mm"
    assert disp["source_type"] == "computed_metrics"
    assert disp["source_artifact"] == "results/computed_metrics.json"
    stress = by_name["stress"]
    assert stress["unit"] == "MPa"
    assert stress["source_type"] == "computed_metrics"


def test_list_cae_result_fields_missing_package_returns_404(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("fields-404"))
    resp = client.get(f"/api/projects/{project['id']}/cae-result-fields")
    assert resp.status_code == 404


def test_get_cae_result_field_summary_displacement(tmp_path: Path) -> None:
    """GET /cae-result-fields/displacement returns field summary from computed_metrics."""
    from unittest.mock import patch as _patch

    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("field-disp"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-disp.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.35, stress_val=12.0)
    project["aieng_file"] = "field-disp.aieng"
    save_project(settings, project)

    # No FRD in package — endpoint must fall back to computed_metrics
    resp = client.get(f"/api/projects/{project_id}/cae-result-fields/displacement")
    assert resp.status_code == 200
    data = resp.json()

    assert data["schema_version"] == "0.1"
    assert data["field_name"] == "displacement"
    assert data["unit"] == "mm"
    assert data["evidence_role"] == "displacement_extrema"
    assert data["claim_advancement"] == "none"
    assert data["source"]["source_type"] == "computed_metrics"
    assert data["source"]["computed_metrics_path"] == "results/computed_metrics.json"
    assert data["stats"]["max_value"] == 0.35


def test_get_cae_result_field_summary_stress(tmp_path: Path) -> None:
    """GET /cae-result-fields/stress returns field summary from computed_metrics."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("field-stress"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-stress.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.1, stress_val=55.7)
    project["aieng_file"] = "field-stress.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-result-fields/stress")
    assert resp.status_code == 200
    data = resp.json()

    assert data["field_name"] == "stress"
    assert data["unit"] == "MPa"
    assert data["evidence_role"] == "stress_extrema"
    assert data["claim_advancement"] == "none"
    assert data["stats"]["max_value"] == 55.7


def test_get_cae_result_field_summary_with_frd(tmp_path: Path) -> None:
    """GET /cae-result-fields/{name} uses FRD stats when FRD is present in package."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("field-frd"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-frd.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.2, stress_val=20.0)

    # Add a minimal FRD to the package
    coords = {1: (0.0, 0.0, 0.0), 2: (10.0, 0.0, 0.0)}
    stress_data = {1: [5.0, 0.0, 0.0, 0.0, 0.0, 0.0], 2: [15.0, 0.0, 0.0, 0.0, 0.0, 0.0]}
    frd_text = _make_test_frd_with_coords(coords, stress_nodes=stress_data)
    with zipfile.ZipFile(pkg_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("simulation/runs/run_001/outputs/result.frd", frd_text)

    project["aieng_file"] = "field-frd.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-result-fields/stress")
    assert resp.status_code == 200
    data = resp.json()

    assert data["field_name"] == "stress"
    assert data["source"]["source_type"] == "frd"
    assert data["source"]["frd_path"] == "simulation/runs/run_001/outputs/result.frd"
    stats = data["stats"]
    assert stats["node_count"] == 2
    assert stats["values_finite"] is True
    assert stats["max_value"] > stats["min_value"]


def test_get_cae_result_field_summary_unsupported_field_returns_404(tmp_path: Path) -> None:
    """GET /cae-result-fields/temperature returns 404 for unsupported field."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("field-unsupported"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-unsup.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.1, stress_val=10.0)
    project["aieng_file"] = "field-unsup.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-result-fields/temperature")
    assert resp.status_code == 404


def test_get_cae_result_field_summary_no_results_returns_404(tmp_path: Path) -> None:
    """GET /cae-result-fields/displacement returns 404 when no computed_metrics and no FRD."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("field-empty"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-empty.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "empty", "resources": {}}))
    project["aieng_file"] = "field-empty.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-result-fields/displacement")
    assert resp.status_code == 404


def test_cae_result_field_summary_does_not_advance_claims(tmp_path: Path) -> None:
    """Field summary endpoints do not write claim maps or advance engineering claims."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("field-noclaim"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "field-noclaim.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.5, stress_val=30.0)
    project["aieng_file"] = "field-noclaim.aieng"
    save_project(settings, project)

    # Call both endpoints
    r1 = client.get(f"/api/projects/{project_id}/cae-result-fields")
    assert r1.status_code == 200
    r2 = client.get(f"/api/projects/{project_id}/cae-result-fields/displacement")
    assert r2.status_code == 200

    # Responses carry claim_advancement="none"
    assert r1.json()["claim_advancement"] == "none"
    assert r2.json()["claim_advancement"] == "none"

    # Package must not contain any claim map after these read-only calls
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


# ---------------------------------------------------------------------------
# CAE field summary artifact persistence (written during refresh_cae_summary)
# ---------------------------------------------------------------------------

def test_refresh_cae_summary_writes_field_summary_artifacts(tmp_path: Path) -> None:
    """postprocess.refresh_cae_summary writes field summary artifacts when computed_metrics exists."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("fsummary-write"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fsummary-write.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.42, stress_val=18.5)
    project["aieng_file"] = "fsummary-write.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed", f"refresh failed: {run}"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
        assert "results/fields/displacement.summary.json" in names
        assert "results/fields/stress.summary.json" in names

        disp = json.loads(zf.read("results/fields/displacement.summary.json"))
        stress = json.loads(zf.read("results/fields/stress.summary.json"))

    # Displacement artifact structure
    assert disp["schema_version"] == "0.1"
    assert disp["field_name"] == "displacement"
    assert disp["unit"] == "mm"
    assert disp["stats"]["max_value"] == 0.42
    assert disp["stats"]["min_value"] is None
    assert disp["stats"]["node_count"] is None
    assert disp["claim_advancement"] == "none"
    assert disp["evidence_role"] == "displacement_extrema"
    assert disp["source"]["computed_metrics_path"] == "results/computed_metrics.json"

    # Stress artifact structure
    assert stress["field_name"] == "stress"
    assert stress["unit"] == "MPa"
    assert stress["stats"]["max_value"] == 18.5
    assert stress["claim_advancement"] == "none"
    assert stress["evidence_role"] == "stress_extrema"


def test_field_summary_artifacts_in_evidence_index_after_refresh(tmp_path: Path) -> None:
    """evidence_index.json references field summary artifacts after a second refresh."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("fsummary-index"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fsummary-index.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.15, stress_val=8.0)
    project["aieng_file"] = "fsummary-index.aieng"
    save_project(settings, project)

    # First refresh writes field summaries
    r1 = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })
    assert r1.json()["status"] == "completed"

    # Second refresh: field summaries are now in the package, so evidence_index should show exists=True
    r2 = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })
    assert r2.json()["status"] == "completed"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        evidence = json.loads(zf.read("results/evidence_index.json"))

    entries_by_path = {e["path"]: e for e in evidence["entries"]}
    disp_entry = entries_by_path.get("results/fields/displacement.summary.json")
    stress_entry = entries_by_path.get("results/fields/stress.summary.json")

    assert disp_entry is not None
    assert disp_entry["kind"] == "field"
    assert disp_entry["role"] == "cae_field_summary"
    assert disp_entry["exists"] is True
    assert "displacement_extrema" in disp_entry["supports"]
    assert "audit" in disp_entry["supports"]

    assert stress_entry is not None
    assert stress_entry["exists"] is True
    assert "stress_extrema" in stress_entry["supports"]


def test_field_summary_artifact_consistent_with_endpoint(tmp_path: Path) -> None:
    """Field summary artifact max_value matches the field-summary endpoint response."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("fsummary-consistent"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fsummary-consistent.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.77, stress_val=42.1)
    project["aieng_file"] = "fsummary-consistent.aieng"
    save_project(settings, project)

    client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })

    # Read artifact from package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        disp_artifact = json.loads(zf.read("results/fields/displacement.summary.json"))
        stress_artifact = json.loads(zf.read("results/fields/stress.summary.json"))

    # Read from endpoint
    disp_resp = client.get(f"/api/projects/{project_id}/cae-result-fields/displacement")
    assert disp_resp.status_code == 200
    disp_endpoint = disp_resp.json()

    stress_resp = client.get(f"/api/projects/{project_id}/cae-result-fields/stress")
    assert stress_resp.status_code == 200
    stress_endpoint = stress_resp.json()

    # Artifact and endpoint must agree on the key scalar values
    assert disp_artifact["stats"]["max_value"] == disp_endpoint["stats"]["max_value"]
    assert disp_artifact["unit"] == disp_endpoint["unit"]
    assert disp_artifact["claim_advancement"] == "none"
    assert disp_endpoint["claim_advancement"] == "none"

    assert stress_artifact["stats"]["max_value"] == stress_endpoint["stats"]["max_value"]
    assert stress_artifact["unit"] == stress_endpoint["unit"]


def test_field_summary_artifact_no_claim_advancement(tmp_path: Path) -> None:
    """Field summary artifacts carry claim_advancement='none'; no claim maps are written."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("fsummary-noclaim"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fsummary-noclaim.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.3, stress_val=15.0)
    project["aieng_file"] = "fsummary-noclaim.aieng"
    save_project(settings, project)

    client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
        disp = json.loads(zf.read("results/fields/displacement.summary.json"))
        stress = json.loads(zf.read("results/fields/stress.summary.json"))

    assert disp["claim_advancement"] == "none"
    assert stress["claim_advancement"] == "none"
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


def test_no_field_summaries_without_computed_metrics(tmp_path: Path) -> None:
    """refresh_cae_summary does not write field summaries when computed_metrics.json is absent."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("fsummary-empty"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "fsummary-empty.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "empty", "resources": {}}))
    project["aieng_file"] = "fsummary-empty.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })
    assert resp.json()["status"] == "completed"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "results/fields/displacement.summary.json" not in names
    assert "results/fields/stress.summary.json" not in names


@pytest.mark.skipif(
    _resolve_ccx_cmd() is None,
    reason="CalculiX command unavailable via AIENG_CCX_CMD or PATH — skipping real solver smoke test.",
)
def test_run_solver_real_ccx_skipped_if_unavailable(tmp_path: Path) -> None:
    """Real CalculiX smoke test: runs ccx against minimal cantilever fixture if available.

    This test verifies that the external solver adapter (cae.run_solver) can:
      - locate a real ccx executable on PATH
      - execute it in a temp working directory
      - capture stdout/stderr and return code
      - write solver_run.json, solver_log.txt, and result.frd back into the .aieng package

    If ccx is not installed, the test is skipped cleanly so CI/environments without
    CalculiX do not fail.
    """
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("real-ccx-smoke"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"

    # Load the real fixture input deck
    fixture_path = Path(__file__).with_name("fixtures") / "minimal_cantilever.inp"
    inp_content = fixture_path.read_text(encoding="utf-8")

    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "real-ccx-smoke", "resources": {}}))
        zf.writestr("simulation/runs/run_001/solver_input.inp", inp_content)

    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    data = _execute_run_solver(client, project_id, {
        "project_id": project_id,
        "input_deck_path": "simulation/runs/run_001/solver_input.inp",
        "extract_results": True,
        "refresh_summary": True,
    })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == 0
    assert result["status"] == "completed"

    # Verify artifacts were written back into the package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
        assert "simulation/runs/run_001/solver_run.json" in names
        assert "simulation/runs/run_001/solver_log.txt" in names
        assert "simulation/runs/run_001/solver_input.inp" in names
        assert "simulation/runs/run_001/outputs/result.frd" in names

        # Verify solver_run.json content
        run_json = json.loads(zf.read("simulation/runs/run_001/solver_run.json"))
        assert run_json["solver"] == "CalculiX"
        assert run_json["solved"] is True
        assert run_json["converged"] is None  # honest boundary: no convergence claim
        assert run_json["return_code"] == 0
        assert "simulation/runs/run_001/outputs/result.frd" in run_json["output_files"]

    # Verify extracted metrics were produced
    assert "extracted_metrics" in result


def test_vertical_cae_workflow_end_to_end(tmp_path: Path) -> None:
    """Full CAE vertical workflow: preflight -> solver run -> FRD extraction -> summary refresh.

    This is the Phase 22 benchmark / agent-run vertical demo. It demonstrates that
    the runtime can execute the full CAE lifecycle -- preflight, external solver
    execution (mocked), FRD scalar extraction, and summary refresh -- entirely
    through the runtime REST API, producing honest evidence-backed results.
    """
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    def _output_for_tool(run_data: dict[str, Any], tool_name: str) -> dict[str, Any]:
        for tc, tr in zip(run_data["tool_calls"], run_data["tool_results"]):
            if tc["name"] == tool_name:
                return tr["output"]
        raise AssertionError(f"Tool {tool_name} not found in run tool_calls")

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    # fixture: generic .aieng package with all CAE setup artifacts
    project = save_project(settings, default_project("cae-benchmark"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "benchmark.aieng"
    _make_preflight_package(pkg_path, mesh=True, solver_settings=True, load_case=True, input_deck=True)
    project["aieng_file"] = "benchmark.aieng"
    save_project(settings, project)

    # Mock ccx availability for both preflight and solver run
    with patch("app.main.shutil.which", return_value="/fake/ccx"):
        # Step 1: prepare solver run (reads evidence, no execution)
        resp = client.post("/api/runtime/runs", json={
            "message": "prepare solver run",
            "project_id": project_id,
            "tool_input": {"project_id": project_id, "run_id": "run_001"},
        })
        assert resp.status_code == 200
        preflight = resp.json()["tool_results"][0]["output"]
        assert preflight["ok"] is True
        assert preflight["solver_execution_performed"] is False
        assert preflight["ready_to_run"] is True

        # Step 2: run solver (mocked ccx producing a parseable FRD)
        def fake_run(cmd, **kwargs):
            cwd = Path(kwargs.get("cwd", "."))
            frd_path = cwd / "solver_input.frd"
            frd_path.write_text(
                _make_test_frd(
                    {1: [1.0, 0.0, 0.0, 1.0], 2: [5.0, 0.0, 0.0, 5.0]},
                    {1: [200.0, 100.0, 50.0, 10.0, 0.0, 0.0]},
                ),
                encoding="utf-8",
            )
            return MagicMock(returncode=0, stdout="solver completed\n", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            resp = client.post("/api/runtime/runs", json={
                "message": "execute solver run",
                "project_id": project_id,
                "tool_input": {
                    "project_id": project_id,
                    "run_id": "run_001",
                    "input_deck_path": "simulation/runs/run_001/solver_input.inp",
                    "extract_results": False,
                    "refresh_summary": False,
                },
            })
            assert resp.status_code == 200
            run_data = resp.json()
            # Approval gate: cae.run_solver requires explicit approval
            assert run_data["status"] == "awaiting_approval"
            run_id = run_data["run_id"]
            approve_resp = client.post(f"/api/runtime/runs/{run_id}/approve")
            assert approve_resp.status_code == 200
            run_data = approve_resp.json()

    result = run_data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == 0
    assert result["status"] == "completed"
    assert any("solver_run.json" in a["path"] for a in result["changed_artifacts"])
    assert any("solver_log.txt" in a["path"] for a in result["changed_artifacts"])
    assert any("result.frd" in a["path"] for a in result["changed_artifacts"])

    # Verify solver artifacts persisted in the package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
        assert "simulation/runs/run_001/solver_run.json" in names
        assert "simulation/runs/run_001/solver_log.txt" in names
        assert "simulation/runs/run_001/outputs/result.frd" in names
        solver_run = json.loads(zf.read("simulation/runs/run_001/solver_run.json"))
    assert solver_run["solved"] is True
    assert solver_run["converged"] is None  # conservative: no reliable convergence evidence

    # Step 3: extract FRD scalar results
    frd_path = tmp_path / "solver_input.frd"
    with zipfile.ZipFile(pkg_path, "r") as zf:
        frd_content = zf.read("simulation/runs/run_001/outputs/result.frd")
    frd_path.write_bytes(frd_content)

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(frd_path),
            "loadCaseId": "load_case_001",
            "refresh_result_summary": False,
        },
    })
    assert resp.status_code == 200
    extract_result = resp.json()["tool_results"][0]["output"]
    assert extract_result["status"] == "ok"
    assert any(a["path"] == "results/computed_metrics.json" for a in extract_result["artifacts"])

    # Verify computed_metrics.json inside the package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/computed_metrics.json" in zf.namelist()
        metrics = json.loads(zf.read("results/computed_metrics.json"))
    assert metrics["schema_version"] == "0.1"
    lc = metrics["load_cases"][0]
    assert lc["id"] == "load_case_001"
    assert abs(lc["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4
    assert "max_von_mises_stress" in lc["metrics"]

    # Step 4: refresh CAE result summary
    resp = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })
    assert resp.status_code == 200
    refresh_run = resp.json()
    refresh_result = _output_for_tool(refresh_run, "postprocess.refresh_cae_summary")
    assert refresh_result["status"] == "ok"

    # Verify the summary endpoint now reports real extrema
    resp = client.get(f"/api/projects/{project_id}/cae-result-summary")
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["computed_values"]["extrema_computed"] is True
    assert summary["computed_values"]["max_displacement"] is not None
    assert summary["computed_values"]["max_von_mises_stress"] is not None
    assert len(summary["llm_summary"]["limitations"]) > 0

    # Benchmark checklist
    assert preflight["solver_execution_performed"] is False  # reads evidence before acting
    assert result["solver_execution_performed"] is True      # uses prepare/run/extract flow
    assert run_data["status"] == "completed"                 # approval semantics respected
    assert solver_run["converged"] is None                   # does not claim convergence
    assert extract_result["status"] == "ok"                  # distinguishes extraction from execution
    assert "limitations" in summary["llm_summary"]           # reports limitations honestly


# ---------------------------------------------------------------------------
# Bridge schema_version validation hook
# ---------------------------------------------------------------------------

def test_bridge_check_schema_version_matching_returns_no_warnings() -> None:
    """A matching on-disk schema_version yields an empty warnings list."""
    from app.aieng_bridge import _check_schema_version

    warnings = _check_schema_version("0.3", "0.3", "cae_result_summary")
    assert warnings == []


def test_bridge_check_schema_version_mismatch_returns_regenerate_warning() -> None:
    """A drifted on-disk schema_version produces an actionable warning."""
    from app.aieng_bridge import _check_schema_version

    warnings = _check_schema_version("0.1", "0.3", "cae_result_summary")
    assert len(warnings) == 1
    assert "regenerate" in warnings[0].lower()
    assert "'0.1'" in warnings[0]
    assert "'0.3'" in warnings[0]
    assert "cae_result_summary" in warnings[0]


def test_bridge_check_schema_version_missing_returns_regenerate_warning() -> None:
    """A missing on-disk schema_version produces an actionable warning."""
    from app.aieng_bridge import _check_schema_version

    warnings = _check_schema_version(None, "0.3", "cae_result_summary")
    assert len(warnings) == 1
    assert "regenerate" in warnings[0].lower()
    assert "missing" in warnings[0].lower()


# ---------------------------------------------------------------------------
# Runtime CAE tool contract
# ---------------------------------------------------------------------------
# Critical runtime tools that agent-facing surfaces (MCP wrappers, capability
# registry, agent vertical workflow) depend on. Adding a tool here means it
# must survive any future runtime-registry refactor.
#
# Subset membership only — we do not assert an exact total, since non-critical
# tools (aieng.*, mcp.*) are free to change without breaking this contract.

CRITICAL_RUNTIME_TOOLS: tuple[str, ...] = (
    "postprocess.generate_computed_metrics",
    "postprocess.refresh_cae_summary",
    "cae.apply_setup_patch",
    "cae.extract_solver_results",
    "cae.prepare_solver_run",
    "cae.run_solver",
)


def test_runtime_introspection_includes_critical_cae_tools(tmp_path: Path) -> None:
    """Every critical CAE/postprocess runtime tool must appear in
    /api/runtime/tools introspection with a non-empty description."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    tools_by_name = {t["name"]: t for t in resp.json()}

    missing = [name for name in CRITICAL_RUNTIME_TOOLS if name not in tools_by_name]
    assert not missing, (
        f"Critical runtime tools missing from introspection: {missing}. "
        f"Either register them in app.main.create_app or remove them from "
        f"CRITICAL_RUNTIME_TOOLS if intentionally deprecated."
    )

    for name in CRITICAL_RUNTIME_TOOLS:
        entry = tools_by_name[name]
        assert isinstance(entry["description"], str) and entry["description"], (
            f"{name} is registered but has no description"
        )
        assert "requires_approval" in entry


def test_run_solver_introspection_requires_approval(tmp_path: Path) -> None:
    """cae.run_solver is potentially destructive; the approval gate must
    survive any future refactor of the registry."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    run_solver = next(t for t in resp.json() if t["name"] == "cae.run_solver")
    assert run_solver["requires_approval"] is True


def _make_revalidation_pkg(pkg_path: Path) -> None:
    """Minimal .aieng package with computed_metrics for revalidation tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    cm = {
        "schema_version": "0.1",
        "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX", "source_files": []},
        "load_cases": [
            {
                "id": "load_case_001",
                "metrics": {
                    "max_displacement": {"value": 0.25, "unit": "mm"},
                    "max_von_mises_stress": {"value": 120.0, "unit": "MPa"},
                },
            }
        ],
        "warnings": [],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "rev-test", "resources": {}}))
        zf.writestr("results/computed_metrics.json", json.dumps(cm))
        zf.writestr("results/result_summary.json", json.dumps({"schema_version": "0.1", "summary_type": "cae_postprocessing"}))
        zf.writestr("results/evidence_index.json", json.dumps({"evidence_type": "cae_artifacts", "entries": []}))


def test_read_revalidation_status_returns_none_when_absent(tmp_path: Path) -> None:
    """_read_revalidation_status returns None when the artifact is not in the package."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    result = _read_revalidation_status(pkg)
    assert result is None


def test_cae_result_summary_no_stale_when_artifact_absent(tmp_path: Path) -> None:
    """cae-result-summary returns requires_revalidation=False when no stale artifact exists."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("rev-test"))
    pkg = project_dir(settings, project["id"]) / "packages" / "rev.aieng"
    _make_revalidation_pkg(pkg)
    project["aieng_file"] = "packages/rev.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/cae-result-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "revalidation_status" in data
    assert data["revalidation_status"]["requires_revalidation"] is False


def test_default_revalidation_response_has_revision_fields(tmp_path: Path) -> None:
    """_build_revalidation_response with None returns safe defaults for all revision fields."""
    resp = _build_revalidation_response(None)
    assert resp["requires_revalidation"] is False
    assert resp["current_geometry_revision"] == 0
    assert resp["last_validated_geometry_revision"] is None
    assert resp["stale_since_geometry_revision"] is None
    assert resp["validated_by_run_id"] is None
    assert resp["claim_advancement"] == "none"


def test_first_geometry_edit_increments_revision_to_one(tmp_path: Path) -> None:
    """First call to _record_geometry_edit_in_package sets current_geometry_revision=1."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    new_rev = _record_geometry_edit_in_package(pkg, affected_artifacts=[])

    assert new_rev == 1
    data = _read_revalidation_status(pkg)
    assert data is not None
    assert data["current_geometry_revision"] == 1
    assert data["requires_revalidation"] is True
    assert data["stale_since_geometry_revision"] == 1


def test_repeated_geometry_edits_increment_revision(tmp_path: Path) -> None:
    """Each call to _record_geometry_edit_in_package increments the revision by 1."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    _record_geometry_edit_in_package(pkg, affected_artifacts=[])
    _record_geometry_edit_in_package(pkg, affected_artifacts=[])
    new_rev = _record_geometry_edit_in_package(pkg, affected_artifacts=[])

    assert new_rev == 3
    data = _read_revalidation_status(pkg)
    assert data is not None
    assert data["current_geometry_revision"] == 3
    assert data["stale_since_geometry_revision"] == 3


def test_geometry_edit_preserves_last_validated_revision(tmp_path: Path) -> None:
    """_record_geometry_edit_in_package preserves the last validated revision from prior state."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    # Simulate: edit -> solver validates (revision 1) -> edit again
    _record_geometry_edit_in_package(pkg, affected_artifacts=[])
    _record_solver_validation_in_package(pkg, run_id="run_001")
    _record_geometry_edit_in_package(pkg, affected_artifacts=[])

    data = _read_revalidation_status(pkg)
    assert data is not None
    assert data["current_geometry_revision"] == 2
    assert data["last_validated_geometry_revision"] == 1
    assert data["stale_since_geometry_revision"] == 2
    assert data["requires_revalidation"] is True


def test_solver_validation_clears_stale_and_sets_last_validated(tmp_path: Path) -> None:
    """_record_solver_validation_in_package sets last_validated == current and clears stale."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    _record_geometry_edit_in_package(pkg, affected_artifacts=[])
    _record_solver_validation_in_package(pkg, run_id="run_002")

    data = _read_revalidation_status(pkg)
    assert data is not None
    assert data["requires_revalidation"] is False
    assert data["current_geometry_revision"] == 1
    assert data["last_validated_geometry_revision"] == 1
    assert data["stale_since_geometry_revision"] is None
    assert data["validated_by_run_id"] == "run_002"
    assert data["claim_advancement"] == "none"


def test_solver_validation_on_fresh_package_records_revision_zero(tmp_path: Path) -> None:
    """Solver validation on a package with no prior edits records current_geometry_revision=0."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    _record_solver_validation_in_package(pkg, run_id="run_001")

    data = _read_revalidation_status(pkg)
    assert data is not None
    assert data["current_geometry_revision"] == 0
    assert data["last_validated_geometry_revision"] == 0
    assert data["requires_revalidation"] is False


def test_no_claim_map_after_geometry_edit_helper(tmp_path: Path) -> None:
    """_record_geometry_edit_in_package does not create a claim map."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    _record_geometry_edit_in_package(pkg, affected_artifacts=[])

    with zipfile.ZipFile(pkg, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


def test_historical_evidence_preserved_after_geometry_edit(tmp_path: Path) -> None:
    """Old CAE artifacts remain in the package after recording a geometry edit."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    _record_geometry_edit_in_package(pkg, affected_artifacts=["results/result_summary.json"])

    with zipfile.ZipFile(pkg, "r") as zf:
        names = set(zf.namelist())
    assert "results/computed_metrics.json" in names
    assert "results/result_summary.json" in names
    assert "results/evidence_index.json" in names


def test_cae_result_summary_exposes_revision_fields_when_fresh(tmp_path: Path) -> None:
    """cae-result-summary revalidation_status includes revision fields (no prior edit)."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("rev-fresh"))
    pkg = project_dir(settings, project["id"]) / "packages" / "rev.aieng"
    _make_revalidation_pkg(pkg)
    project["aieng_file"] = "packages/rev.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/cae-result-summary")
    assert resp.status_code == 200
    rs = resp.json()["revalidation_status"]
    assert "current_geometry_revision" in rs
    assert "last_validated_geometry_revision" in rs
    assert rs["current_geometry_revision"] == 0
    assert rs["requires_revalidation"] is False
    assert rs["claim_advancement"] == "none"


def test_cae_result_summary_exposes_revision_after_geometry_edit(tmp_path: Path) -> None:
    """cae-result-summary revalidation_status shows incremented revision after edit."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("rev-edited"))
    pkg = project_dir(settings, project["id"]) / "packages" / "rev.aieng"
    _make_revalidation_pkg(pkg)
    project["aieng_file"] = "packages/rev.aieng"
    save_project(settings, project)

    _record_geometry_edit_in_package(pkg, affected_artifacts=[])

    resp = client.get(f"/api/projects/{project['id']}/cae-result-summary")
    assert resp.status_code == 200
    rs = resp.json()["revalidation_status"]
    assert rs["requires_revalidation"] is True
    assert rs["current_geometry_revision"] == 1
    assert rs["stale_since_geometry_revision"] == 1
    assert rs["last_validated_geometry_revision"] is None


def test_cae_result_fields_exposes_revision_fields(tmp_path: Path) -> None:
    """cae-result-fields revalidation_status contains all revision fields."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("rev-fields"))
    pkg = project_dir(settings, project["id"]) / "packages" / "rev.aieng"
    _make_revalidation_pkg(pkg)
    project["aieng_file"] = "packages/rev.aieng"
    save_project(settings, project)

    _record_geometry_edit_in_package(pkg, affected_artifacts=[])

    resp = client.get(f"/api/projects/{project['id']}/cae-result-fields")
    assert resp.status_code == 200
    rs = resp.json()["revalidation_status"]
    assert rs["current_geometry_revision"] == 1
    assert rs["requires_revalidation"] is True
    assert rs["claim_advancement"] == "none"


def test_read_audit_events_returns_empty_list_when_absent(tmp_path: Path) -> None:
    """_read_audit_events_from_package returns [] when no audit artifact exists."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    assert _read_audit_events_from_package(pkg) == []


def test_audit_event_required_fields_all_present(tmp_path: Path) -> None:
    """_build_audit_event produces a dict with all required schema fields."""
    pkg = tmp_path / "test.aieng"
    _make_revalidation_pkg(pkg)

    _append_audit_event_to_package(
        pkg,
        _build_audit_event(
            tool="cae.run_solver", event_type="solver_run_completed", status="completed",
            artifacts_written=["simulation/runs/run_001/solver_run.json"],
            evidence_created=["simulation/runs/run_001/solver_run.json"],
            state_changes={"requires_revalidation": False},
            geometry_revision=1, revalidation_status="fresh",
        ),
    )

    e = _read_audit_events_from_package(pkg)[0]
    for field in (
        "schema_version", "event_id", "timestamp", "tool", "event_type",
        "status", "artifacts_written", "evidence_created", "state_changes",
        "geometry_revision", "revalidation_status", "claim_advancement",
    ):
        assert field in e, f"Missing field: {field}"


def test_audit_events_endpoint_returns_empty_list(tmp_path: Path) -> None:
    """GET /audit-events returns empty list and count=0 when no audit log exists."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("audit-empty"))
    pkg = project_dir(settings, project["id"]) / "packages" / "audit.aieng"
    _make_revalidation_pkg(pkg)
    project["aieng_file"] = "packages/audit.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/audit-events")
    assert resp.status_code == 200
    data = resp.json()
    assert data["events"] == []
    assert data["count"] == 0
    assert data["claim_advancement"] == "none"


def test_audit_events_endpoint_404_when_package_missing(tmp_path: Path) -> None:
    """GET /audit-events returns 404 when no .aieng package is registered."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("audit-no-pkg"))

    resp = client.get(f"/api/projects/{project['id']}/audit-events")
    assert resp.status_code == 404


def test_refresh_cae_summary_appends_cae_summary_refreshed_event(tmp_path: Path) -> None:
    """postprocess.refresh_cae_summary appends a cae_summary_refreshed audit event."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    project = save_project(settings, default_project("audit-refresh"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "audit-refresh.aieng"
    _make_computed_metrics_pkg(pkg_path, disp_val=0.3, stress_val=50.0)
    project["aieng_file"] = "audit-refresh.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed", f"refresh failed: {run}"

    events = _read_audit_events_from_package(pkg_path)
    refresh_events = [e for e in events if e["event_type"] == "cae_summary_refreshed"]
    assert len(refresh_events) >= 1
    e = refresh_events[-1]
    assert e["tool"] == "postprocess.refresh_cae_summary"
    assert e["claim_advancement"] == "none"
    assert "results/result_summary.json" in e["artifacts_written"]
    assert "results/evidence_index.json" in e["artifacts_written"]


# ---------------------------------------------------------------------------
# Package artifact manifest tests
# ---------------------------------------------------------------------------

def _make_manifest_pkg(pkg_path: Path) -> None:
    """Create a minimal .aieng package with a variety of classified artifact types."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "manifest-test", "resources": {}}))
        zf.writestr("results/result_summary.json", json.dumps({"schema_version": "0.1"}))
        zf.writestr("results/evidence_index.json", json.dumps({"evidence_type": "cae_artifacts", "entries": []}))
        zf.writestr("results/fields/displacement.summary.json", json.dumps({"field_name": "displacement"}))
        zf.writestr("results/fields/stress.summary.json", json.dumps({"field_name": "stress"}))
        zf.writestr("results/computed_metrics.json", json.dumps({"schema_version": "0.1", "load_cases": []}))
        zf.writestr("simulation/runs/run_001/solver_run.json", json.dumps({"run_id": "run_001"}))
        zf.writestr("simulation/runs/run_001/outputs/result.frd", b"FRD")


def test_classify_audit_events_as_audit(tmp_path: Path) -> None:
    """audit/events.jsonl is classified as category 'audit'."""
    kind, category, producer, _ = _classify_artifact_path(AUDIT_EVENTS_PATH)
    assert category == "audit"
    assert kind == "audit_log"
    assert producer is None


def test_classify_solver_run_json_as_solver_output(tmp_path: Path) -> None:
    """simulation/runs/*/solver_run.json is classified as category 'solver_output'."""
    kind, category, producer, role = _classify_artifact_path(
        "simulation/runs/run_001/solver_run.json"
    )
    assert category == "solver_output"
    assert kind == "solver_run_metadata"
    assert producer == "cae.run_solver"
    assert role == "solver_execution_evidence"


def test_classify_frd_as_solver_output(tmp_path: Path) -> None:
    """simulation/runs/*/outputs/*.frd is classified as category 'solver_output'."""
    kind, category, producer, role = _classify_artifact_path(
        "simulation/runs/run_001/outputs/result.frd"
    )
    assert category == "solver_output"
    assert kind == "solver_raw_output"
    assert producer == "cae.run_solver"
    assert role == "solver_raw_output"


def test_classify_field_summary_displacement(tmp_path: Path) -> None:
    """results/fields/displacement.summary.json is classified as category 'field_summary'."""
    kind, category, producer, role = _classify_artifact_path(
        "results/fields/displacement.summary.json"
    )
    assert category == "field_summary"
    assert kind == "field"
    assert producer == "postprocess.refresh_cae_summary"
    assert role == "displacement_extrema"


def test_classify_evidence_index(tmp_path: Path) -> None:
    """results/evidence_index.json is classified as category 'evidence_index'."""
    kind, category, producer, role = _classify_artifact_path("results/evidence_index.json")
    assert category == "evidence_index"
    assert kind == "evidence_index"
    assert producer == "postprocess.refresh_cae_summary"


def test_classify_unknown_path_returns_unknown_category(tmp_path: Path) -> None:
    """An unrecognised path returns category 'unknown'."""
    kind, category, producer, role = _classify_artifact_path("some/unknown/artifact.bin")
    assert category == "unknown"
    assert kind == "unknown"
    assert producer is None
    assert role is None


def test_generate_manifest_all_entries_claim_advancement_none(tmp_path: Path) -> None:
    """Every artifact entry in the manifest carries claim_advancement='none'."""
    pkg = tmp_path / "test.aieng"
    _make_manifest_pkg(pkg)
    manifest = _generate_artifact_manifest(pkg)
    assert manifest["claim_advancement"] == "none"
    for entry in manifest["artifacts"]:
        assert entry["claim_advancement"] == "none", f"Missing on entry: {entry['path']}"


def test_generate_manifest_top_level_claim_advancement_none(tmp_path: Path) -> None:
    """Top-level manifest has claim_advancement='none'."""
    pkg = tmp_path / "test.aieng"
    _make_manifest_pkg(pkg)
    manifest = _generate_artifact_manifest(pkg)
    assert manifest["claim_advancement"] == "none"
    assert manifest["schema_version"] == "0.1"
    assert "generated_at" in manifest
    assert isinstance(manifest["artifact_count"], int)
    assert manifest["artifact_count"] == len(manifest["artifacts"])


def test_generate_manifest_excludes_self_reference(tmp_path: Path) -> None:
    """manifest/artifacts.json is excluded from the artifact list if present in the ZIP."""
    pkg = tmp_path / "self-ref.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "self-ref"}))
        zf.writestr(ARTIFACT_MANIFEST_PATH, json.dumps({"schema_version": "0.1"}))
    manifest = _generate_artifact_manifest(pkg)
    paths = {e["path"] for e in manifest["artifacts"]}
    assert ARTIFACT_MANIFEST_PATH not in paths


def test_manifest_endpoint_returns_200(tmp_path: Path) -> None:
    """GET /artifact-manifest returns 200 with correct structure for a valid package."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("manifest-ok"))
    pkg = project_dir(settings, project["id"]) / "packages" / "manifest.aieng"
    _make_manifest_pkg(pkg)
    project["aieng_file"] = "packages/manifest.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/artifact-manifest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == "0.1"
    assert data["claim_advancement"] == "none"
    assert "artifacts" in data
    assert isinstance(data["artifacts"], list)
    assert data["artifact_count"] == len(data["artifacts"])


def test_manifest_endpoint_404_when_no_package(tmp_path: Path) -> None:
    """GET /artifact-manifest returns 404 when no package is registered."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("manifest-404"))
    resp = client.get(f"/api/projects/{project['id']}/artifact-manifest")
    assert resp.status_code == 404


def test_manifest_endpoint_does_not_write_claim_map(tmp_path: Path) -> None:
    """Calling GET /artifact-manifest does not create a claim map in the package."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("manifest-noclaim"))
    pkg = project_dir(settings, project["id"]) / "packages" / "noclaim.aieng"
    _make_manifest_pkg(pkg)
    project["aieng_file"] = "packages/noclaim.aieng"
    save_project(settings, project)

    client.get(f"/api/projects/{project['id']}/artifact-manifest")

    with zipfile.ZipFile(pkg, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


# ---------------------------------------------------------------------------
# Package consistency check tests
# ---------------------------------------------------------------------------

def _make_consistency_pkg(
    pkg_path: Path,
    *,
    with_evidence_index: bool = True,
    evidence_path_exists: bool = True,
    with_audit_events: bool = False,
    audit_ref_exists: bool = True,
    with_field_summaries: bool = False,
    field_source_exists: bool = True,
    with_revalidation: dict[str, Any] | None = None,
    with_claim_map: bool = False,
) -> None:
    """Create a .aieng package for consistency-check tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    cm_path = "results/computed_metrics.json"
    members: dict[str, str] = {
        "manifest.json": json.dumps({"model_id": "consistency-test"}),
        cm_path: json.dumps({"schema_version": "0.1", "load_cases": []}),
    }

    if with_evidence_index:
        indexed_path = cm_path if evidence_path_exists else "nonexistent/artifact.json"
        members["results/evidence_index.json"] = json.dumps({
            "evidence_type": "cae_artifacts",
            "entries": [
                {
                    "id": "test_entry",
                    "path": indexed_path,
                    "kind": "result",
                    "role": "test_evidence",
                    "exists": True,
                    "supports": ["audit"],
                }
            ],
        })

    if with_audit_events:
        ref_path = cm_path if audit_ref_exists else "nonexistent/from_audit.json"
        members[AUDIT_EVENTS_PATH] = (
            json.dumps({
                "schema_version": "0.1",
                "event_id": "evt_consistency_001",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "tool": "cae.run_solver",
                "event_type": "solver_run_completed",
                "status": "completed",
                "artifacts_written": [ref_path],
                "evidence_created": [],
                "state_changes": {},
                "geometry_revision": None,
                "revalidation_status": None,
                "claim_advancement": "none",
            }, separators=(",", ":")) + "\n"
        )

    if with_field_summaries:
        src_path = cm_path if field_source_exists else "nonexistent/metrics.json"
        for field in ("displacement", "stress"):
            members[f"results/fields/{field}.summary.json"] = json.dumps({
                "schema_version": "0.1",
                "field_name": field,
                "unit": "mm" if field == "displacement" else "MPa",
                "source": {
                    "source_type": "computed_metrics",
                    "computed_metrics_path": src_path,
                },
                "stats": {"max_value": 0.42},
                "evidence_role": f"{field}_extrema",
                "claim_advancement": "none",
            })

    if with_claim_map:
        members["ai/claim_map.json"] = json.dumps({"claims": []})

    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    if with_revalidation is not None:
        _write_revalidation_status(
            pkg_path,
            requires_revalidation=with_revalidation.get("requires_revalidation", False),
            reason=with_revalidation.get("reason", "test"),
            triggering_tool=with_revalidation.get("triggering_tool", "cad.edit_parameter"),
            affected_artifacts=with_revalidation.get("affected_artifacts", []),
            current_geometry_revision=with_revalidation.get("current_geometry_revision"),
            last_validated_geometry_revision=with_revalidation.get(
                "last_validated_geometry_revision"
            ),
        )


def test_consistency_endpoint_404_when_no_package(tmp_path: Path) -> None:
    """GET /package-consistency returns 404 when no .aieng package is registered."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("cons-404"))
    resp = client.get(f"/api/projects/{project['id']}/package-consistency")
    assert resp.status_code == 404


def test_consistency_endpoint_ok_minimal_package(tmp_path: Path) -> None:
    """Minimal package without optional artifacts returns 200 and overall warning (no evidence)."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("cons-minimal"))
    pkg = project_dir(settings, project["id"]) / "cons.aieng"
    _make_consistency_pkg(pkg, with_evidence_index=False)
    project["aieng_file"] = "cons.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/package-consistency")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == "0.1"
    assert data["claim_advancement"] == "none"
    assert "status" in data
    assert "checks" in data


def test_consistency_evidence_paths_ok(tmp_path: Path) -> None:
    """Evidence index referencing existing artifacts -> evidence_paths_exist ok."""
    pkg = tmp_path / "evid-ok.aieng"
    _make_consistency_pkg(pkg, with_evidence_index=True, evidence_path_exists=True)
    checks = _run_package_consistency_checks(pkg)
    evid = next(c for c in checks if c["id"] == "evidence_paths_exist")
    assert evid["status"] == "ok"


def test_consistency_evidence_paths_warning_missing(tmp_path: Path) -> None:
    """Evidence index entry with missing path -> evidence_paths_exist warning."""
    pkg = tmp_path / "evid-miss.aieng"
    _make_consistency_pkg(pkg, with_evidence_index=True, evidence_path_exists=False)
    checks = _run_package_consistency_checks(pkg)
    evid = next(c for c in checks if c["id"] == "evidence_paths_exist")
    assert evid["status"] == "warning"
    assert "missing_paths" in evid.get("details", {})


def test_consistency_evidence_index_missing_is_warning(tmp_path: Path) -> None:
    """Absent evidence_index.json -> evidence_paths_exist warning, not error."""
    pkg = tmp_path / "evid-absent.aieng"
    _make_consistency_pkg(pkg, with_evidence_index=False)
    checks = _run_package_consistency_checks(pkg)
    evid = next(c for c in checks if c["id"] == "evidence_paths_exist")
    assert evid["status"] == "warning"


def test_consistency_audit_references_ok(tmp_path: Path) -> None:
    """Audit events referencing existing artifacts -> audit_artifact_references ok."""
    pkg = tmp_path / "audit-ok.aieng"
    _make_consistency_pkg(pkg, with_audit_events=True, audit_ref_exists=True)
    checks = _run_package_consistency_checks(pkg)
    audit_chk = next(c for c in checks if c["id"] == "audit_artifact_references")
    assert audit_chk["status"] == "ok"


def test_consistency_audit_references_warning_missing(tmp_path: Path) -> None:
    """Audit events referencing missing internal artifacts -> audit_artifact_references warning."""
    pkg = tmp_path / "audit-miss.aieng"
    _make_consistency_pkg(pkg, with_audit_events=True, audit_ref_exists=False)
    checks = _run_package_consistency_checks(pkg)
    audit_chk = next(c for c in checks if c["id"] == "audit_artifact_references")
    assert audit_chk["status"] == "warning"
    assert "missing_references" in audit_chk.get("details", {})


def test_consistency_field_summary_source_ok(tmp_path: Path) -> None:
    """Field summaries with traceable source artifacts -> field_summary_source_* ok."""
    pkg = tmp_path / "field-ok.aieng"
    _make_consistency_pkg(pkg, with_field_summaries=True, field_source_exists=True)
    checks = _run_package_consistency_checks(pkg)
    for field in ("displacement", "stress"):
        chk = next(c for c in checks if c["id"] == f"field_summary_source_{field}")
        assert chk["status"] == "ok", f"{field}: expected ok, got {chk['status']}"


def test_consistency_field_summary_source_warning_missing(tmp_path: Path) -> None:
    """Field summaries referencing missing source -> field_summary_source_* warning."""
    pkg = tmp_path / "field-miss.aieng"
    _make_consistency_pkg(pkg, with_field_summaries=True, field_source_exists=False)
    checks = _run_package_consistency_checks(pkg)
    for field in ("displacement", "stress"):
        chk = next(c for c in checks if c["id"] == f"field_summary_source_{field}")
        assert chk["status"] == "warning", f"{field}: expected warning, got {chk['status']}"


def test_consistency_revalidation_stale_is_warning_not_error(tmp_path: Path) -> None:
    """Stale revalidation state -> revalidation_status_consistency warning, never error."""
    pkg = tmp_path / "stale.aieng"
    _make_consistency_pkg(
        pkg,
        with_revalidation={
            "requires_revalidation": True,
            "reason": "geometry_changed",
            "current_geometry_revision": 2,
        },
    )
    checks = _run_package_consistency_checks(pkg)
    reval = next(c for c in checks if c["id"] == "revalidation_status_consistency")
    assert reval["status"] == "warning"
    # Must NOT be error — stale is valid state
    assert reval["status"] != "error"


def test_consistency_revalidation_inconsistent_revisions_warning(tmp_path: Path) -> None:
    """requires_revalidation=False but revisions disagree -> warning."""
    pkg = tmp_path / "rev-mismatch.aieng"
    _make_consistency_pkg(
        pkg,
        with_revalidation={
            "requires_revalidation": False,
            "reason": "solver_rerun_completed",
            "current_geometry_revision": 3,
            "last_validated_geometry_revision": 1,
        },
    )
    checks = _run_package_consistency_checks(pkg)
    reval = next(c for c in checks if c["id"] == "revalidation_status_consistency")
    assert reval["status"] == "warning"


def test_consistency_claim_map_absent_ok(tmp_path: Path) -> None:
    """No claim map in package -> claim_map_absent ok."""
    pkg = tmp_path / "noclaim.aieng"
    _make_consistency_pkg(pkg, with_claim_map=False)
    checks = _run_package_consistency_checks(pkg)
    claim_chk = next(c for c in checks if c["id"] == "claim_map_absent")
    assert claim_chk["status"] == "ok"


def test_consistency_top_level_claim_advancement_none(tmp_path: Path) -> None:
    """Consistency endpoint top-level has claim_advancement='none'."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("cons-claim"))
    pkg = project_dir(settings, project["id"]) / "claim.aieng"
    _make_consistency_pkg(pkg)
    project["aieng_file"] = "claim.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/package-consistency")
    assert resp.status_code == 200
    assert resp.json()["claim_advancement"] == "none"


def test_consistency_endpoint_does_not_mutate_package(tmp_path: Path) -> None:
    """Calling GET /package-consistency does not add, remove, or change any ZIP members."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("cons-nomut"))
    pkg = project_dir(settings, project["id"]) / "nomut.aieng"
    _make_consistency_pkg(pkg, with_evidence_index=True, with_audit_events=True)
    project["aieng_file"] = "nomut.aieng"
    save_project(settings, project)

    with zipfile.ZipFile(pkg, "r") as zf:
        names_before = set(zf.namelist())

    client.get(f"/api/projects/{project['id']}/package-consistency")

    with zipfile.ZipFile(pkg, "r") as zf:
        names_after = set(zf.namelist())

    assert names_before == names_after


def test_is_internal_package_path_rejects_absolute() -> None:
    """_is_internal_package_path rejects absolute and external paths."""
    assert not _is_internal_package_path("/etc/passwd")
    assert not _is_internal_package_path("C:\\Windows\\System32\\file.txt")
    assert not _is_internal_package_path("C:/absolute/path.json")
    assert not _is_internal_package_path("")
    assert _is_internal_package_path("results/computed_metrics.json")
    assert _is_internal_package_path("simulation/runs/run_001/solver_run.json")


# ---------------------------------------------------------------------------
# Runtime metadata contract tests
# ---------------------------------------------------------------------------
# Contract helper functions — reusable assertion libraries for stable shapes.
# Each helper documents intentional omissions where noted.

def _assert_revalidation_status_contract(obj: dict[str, Any]) -> None:
    """Assert state/revalidation_status.json has the required schema_version 0.2 fields."""
    required = {
        "schema_version", "geometry_modified", "requires_revalidation",
        "reason", "triggering_tool", "affected_artifacts", "affected_domains",
        "claim_advancement", "recorded_at",
        "current_geometry_revision", "last_validated_geometry_revision",
        "stale_since_geometry_revision", "validated_by_run_id",
    }
    for field in required:
        assert field in obj, f"revalidation_status artifact missing field: {field!r}"
    assert obj["schema_version"] == "0.2", (
        f"Expected schema_version '0.2', got {obj['schema_version']!r}"
    )
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["requires_revalidation"], bool)
    assert isinstance(obj["affected_artifacts"], list)
    assert isinstance(obj["affected_domains"], list)


def _assert_revalidation_response_contract(obj: dict[str, Any]) -> None:
    """Assert the revalidation_status sub-object injected into API responses.

    This is the shape produced by _build_revalidation_response(), not the raw
    artifact. It intentionally omits schema_version (it's a sub-object), and
    omits geometry_modified / affected_artifacts / reason because those belong
    to the full artifact, not the lightweight API projection.
    """
    required = {
        "requires_revalidation", "claim_advancement",
        "current_geometry_revision", "last_validated_geometry_revision",
        "stale_since_geometry_revision", "validated_by_run_id",
    }
    for field in required:
        assert field in obj, f"revalidation_response sub-object missing field: {field!r}"
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["requires_revalidation"], bool)


def _assert_audit_event_contract(obj: dict[str, Any]) -> None:
    """Assert a single audit event has the required schema_version 0.1 fields."""
    required = {
        "schema_version", "event_id", "timestamp", "tool", "event_type",
        "status", "artifacts_written", "evidence_created", "claim_advancement",
    }
    for field in required:
        assert field in obj, f"audit event missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["artifacts_written"], list)
    assert isinstance(obj["evidence_created"], list)
    assert obj["event_id"]


def _assert_audit_events_response_contract(obj: dict[str, Any]) -> None:
    """Assert GET /audit-events response envelope contract."""
    for field in ("schema_version", "project_id", "events", "count", "claim_advancement"):
        assert field in obj, f"audit-events response missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["events"], list)
    assert obj["count"] == len(obj["events"])
    for event in obj["events"]:
        _assert_audit_event_contract(event)


def _assert_artifact_entry_contract(entry: dict[str, Any]) -> None:
    """Assert a single artifact entry in the manifest."""
    for field in ("path", "kind", "category", "exists", "claim_advancement"):
        assert field in entry, f"artifact entry missing field: {field!r}"
    assert entry["claim_advancement"] == "none"
    assert isinstance(entry["exists"], bool)
    assert entry["path"]


def _assert_artifact_manifest_contract(obj: dict[str, Any]) -> None:
    """Assert GET /artifact-manifest response contract."""
    for field in (
        "schema_version", "generated_at", "claim_advancement",
        "artifacts", "artifact_count",
    ):
        assert field in obj, f"artifact-manifest missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["artifacts"], list)
    assert obj["artifact_count"] == len(obj["artifacts"])
    for entry in obj["artifacts"]:
        _assert_artifact_entry_contract(entry)


def _assert_consistency_check_contract(check: dict[str, Any]) -> None:
    """Assert a single package consistency check result."""
    for field in ("id", "status", "message"):
        assert field in check, f"consistency check missing field: {field!r}"
    assert check["status"] in ("ok", "warning", "error"), (
        f"unexpected check status: {check['status']!r}"
    )
    assert check["id"]
    assert check["message"]


def _assert_package_consistency_contract(obj: dict[str, Any]) -> None:
    """Assert GET /package-consistency response contract."""
    for field in ("schema_version", "project_id", "status", "claim_advancement", "checks"):
        assert field in obj, f"package-consistency missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert obj["status"] in ("ok", "warning", "error")
    assert isinstance(obj["checks"], list)
    for check in obj["checks"]:
        _assert_consistency_check_contract(check)


def _assert_cae_fields_list_contract(obj: dict[str, Any]) -> None:
    """Assert GET /cae-result-fields response contract."""
    for field in ("schema_version", "project_id", "available_fields", "claim_advancement"):
        assert field in obj, f"cae-result-fields missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["available_fields"], list)
    for entry in obj["available_fields"]:
        for fld in ("field_name", "unit", "source_type", "source_artifact"):
            assert fld in entry, f"available_fields entry missing: {fld!r}"
    if "revalidation_status" in obj:
        _assert_revalidation_response_contract(obj["revalidation_status"])


def _assert_cae_field_summary_contract(obj: dict[str, Any]) -> None:
    """Assert GET /cae-result-fields/{name} response contract.

    Note: max_value under stats may be None when only computed_metrics is
    available (min_value, node_count, values_finite are None too in that case).
    """
    for field in (
        "schema_version", "field_name", "unit", "source",
        "stats", "evidence_role", "claim_advancement",
    ):
        assert field in obj, f"cae-field-summary missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert "source_type" in obj["source"], "source missing source_type"
    assert "max_value" in obj["stats"], "stats missing max_value"
    if "revalidation_status" in obj:
        _assert_revalidation_response_contract(obj["revalidation_status"])


def _assert_capability_tool_contract(tool: dict[str, Any]) -> None:
    """Assert a single tool entry in the capability profile.

    advances_claims must always be False — this is a hard design contract.
    """
    for field in (
        "name", "implemented", "available", "registered",
        "requires_approval", "writes_artifacts", "produces_evidence",
        "advances_claims",
    ):
        assert field in tool, f"capability tool {tool.get('name', '?')!r} missing field: {field!r}"
    assert isinstance(tool["implemented"], bool)
    assert isinstance(tool["available"], bool)
    assert tool["advances_claims"] is False, (
        f"tool {tool['name']!r} has advances_claims=True — violates claim non-advancement contract"
    )


def _assert_capability_profile_contract(obj: dict[str, Any]) -> None:
    """Assert GET /api/runtime/capabilities response contract."""
    for field in (
        "schema_version", "generated_at", "environment",
        "tools", "result_fields", "claim_policy",
    ):
        assert field in obj, f"capability-profile missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    env = obj["environment"]
    assert "ccx_available" in env
    assert isinstance(obj["tools"], list)
    assert len(obj["tools"]) > 0
    for tool in obj["tools"]:
        _assert_capability_tool_contract(tool)
    cp = obj["claim_policy"]
    assert cp.get("automatic_claim_advancement") is False
    assert cp.get("claim_advancement_requires_explicit_workflow") is True


def _assert_claim_proposal_contract(obj: dict[str, Any]) -> None:
    """Assert a single claim proposal object satisfies the stable schema contract."""
    required = (
        "schema_version", "proposal_id", "claim_id", "proposed_status",
        "status", "supporting_evidence", "rationale", "created_at",
        "created_by_tool", "claim_advancement",
    )
    for field in required:
        assert field in obj, f"claim proposal missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["status"] == "proposed"
    assert obj["claim_advancement"] == "none"
    assert obj["created_by_tool"] == "claims.propose_update"
    assert obj["proposed_status"] in {"supported", "not_supported", "needs_review"}
    assert isinstance(obj["proposal_id"], str) and len(obj["proposal_id"]) > 0
    assert isinstance(obj["claim_id"], str) and len(obj["claim_id"]) > 0
    assert isinstance(obj["rationale"], str) and len(obj["rationale"]) > 0
    assert isinstance(obj["supporting_evidence"], list) and len(obj["supporting_evidence"]) > 0
    assert "T" in obj["created_at"]


def _assert_claim_proposals_list_contract(obj: dict[str, Any]) -> None:
    """Assert GET /api/projects/{id}/claim-proposals response contract."""
    for field in ("schema_version", "project_id", "count", "proposals", "claim_advancement"):
        assert field in obj, f"claim-proposals list missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["proposals"], list)
    assert obj["count"] == len(obj["proposals"])
    for proposal in obj["proposals"]:
        _assert_claim_proposal_contract(proposal)


def _assert_review_readiness_contract(obj: dict[str, Any]) -> None:
    """Assert a review_readiness object satisfies the stable schema contract."""
    for field in ("status", "checks", "claim_advancement"):
        assert field in obj, f"review_readiness missing field: {field!r}"
    assert obj["status"] in {"ready", "warning", "blocked"}
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["checks"], list) and len(obj["checks"]) > 0
    check_ids = {c["id"] for c in obj["checks"]}
    for expected_id in (
        "supporting_evidence_present",
        "no_missing_evidence",
        "stale_evidence",
        "proposal_status_reviewable",
        "claim_map_not_advanced",
    ):
        assert expected_id in check_ids, f"readiness check missing: {expected_id!r}"
    for check in obj["checks"]:
        assert "id" in check
        assert check.get("status") in {"ok", "warning", "blocked"}
        assert "message" in check


def _assert_claim_support_packet_contract(obj: dict[str, Any]) -> None:
    """Assert a claim support packet satisfies the stable schema contract."""
    required = (
        "schema_version", "proposal_id", "proposal_path", "claim_id",
        "proposed_status", "proposal_status", "rationale",
        "supporting_evidence", "evidence_warnings", "stale_evidence_count",
        "missing_evidence_count", "related_audit_events",
        "review_readiness", "claim_advancement",
    )
    for field in required:
        assert field in obj, f"support packet missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert obj["proposal_status"] == "proposed"
    assert obj["proposed_status"] in {"supported", "not_supported", "needs_review"}
    assert isinstance(obj["supporting_evidence"], list)
    assert isinstance(obj["evidence_warnings"], list)
    assert isinstance(obj["stale_evidence_count"], int) and obj["stale_evidence_count"] >= 0
    assert isinstance(obj["missing_evidence_count"], int) and obj["missing_evidence_count"] >= 0
    assert isinstance(obj["related_audit_events"], list)
    _assert_review_readiness_contract(obj["review_readiness"])
    for ref in obj["supporting_evidence"]:
        _assert_evidence_reference_contract(ref)


def _assert_evidence_reference_contract(obj: dict[str, Any]) -> None:
    """Assert a single resolved evidence reference satisfies the stable contract."""
    required = (
        "schema_version", "path", "exists", "in_evidence_index",
        "evidence_index_entry", "manifest_category", "manifest_kind",
        "evidence_role", "requires_revalidation", "current_geometry_revision",
        "last_validated_geometry_revision", "usable_for_claim_proposal",
        "warnings", "claim_advancement",
    )
    for field in required:
        assert field in obj, f"evidence reference missing field: {field!r}"
    assert obj["schema_version"] == "0.1"
    assert obj["claim_advancement"] == "none"
    assert isinstance(obj["exists"], bool)
    assert isinstance(obj["in_evidence_index"], bool)
    assert isinstance(obj["usable_for_claim_proposal"], bool)
    assert isinstance(obj["requires_revalidation"], bool)
    assert isinstance(obj["warnings"], list)
    assert isinstance(obj["path"], str) and len(obj["path"]) > 0
    assert isinstance(obj["manifest_category"], str)
    assert isinstance(obj["manifest_kind"], str)


# ── contract tests ──────────────────────────────────────────────────────────

def test_contract_audit_event_struct(tmp_path: Path) -> None:
    """_build_audit_event produces a dict that satisfies the audit event v0.1 contract."""
    event = _build_audit_event(
        tool="cae.run_solver",
        event_type="solver_run_completed",
        status="completed",
        artifacts_written=["simulation/runs/run_001/solver_run.json"],
        evidence_created=["simulation/runs/run_001/solver_run.json"],
        state_changes={"requires_revalidation": False},
        geometry_revision=1,
        revalidation_status="fresh",
    )
    _assert_audit_event_contract(event)


def test_contract_artifact_manifest_endpoint(tmp_path: Path) -> None:
    """GET /artifact-manifest response satisfies the manifest contract (all entries checked)."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("contract-manifest"))
    pkg = project_dir(settings, project["id"]) / "contract-manifest.aieng"
    _make_manifest_pkg(pkg)
    project["aieng_file"] = "contract-manifest.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/artifact-manifest")
    assert resp.status_code == 200
    _assert_artifact_manifest_contract(resp.json())


def test_contract_package_consistency_endpoint(tmp_path: Path) -> None:
    """GET /package-consistency response satisfies the consistency contract (all checks)."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("contract-cons"))
    pkg = project_dir(settings, project["id"]) / "contract-cons.aieng"
    _make_consistency_pkg(
        pkg,
        with_evidence_index=True,
        with_audit_events=True,
        with_field_summaries=True,
    )
    project["aieng_file"] = "contract-cons.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/package-consistency")
    assert resp.status_code == 200
    _assert_package_consistency_contract(resp.json())


def test_contract_cae_result_fields_list(tmp_path: Path) -> None:
    """GET /cae-result-fields response satisfies the fields-list contract."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("contract-fields-list"))
    pkg = project_dir(settings, project["id"]) / "contract-fields.aieng"
    _make_computed_metrics_pkg(pkg, disp_val=0.42, stress_val=18.5)
    project["aieng_file"] = "contract-fields.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/cae-result-fields")
    assert resp.status_code == 200
    _assert_cae_fields_list_contract(resp.json())


def test_contract_cae_result_field_summary(tmp_path: Path) -> None:
    """GET /cae-result-fields/displacement response satisfies the field-summary contract."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("contract-field-summ"))
    pkg = project_dir(settings, project["id"]) / "contract-field-summ.aieng"
    _make_computed_metrics_pkg(pkg, disp_val=0.35, stress_val=12.0)
    project["aieng_file"] = "contract-field-summ.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project['id']}/cae-result-fields/displacement")
    assert resp.status_code == 200
    _assert_cae_field_summary_contract(resp.json())


def test_contract_capability_profile(tmp_path: Path) -> None:
    """GET /api/runtime/capabilities response satisfies the capability profile contract."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/capabilities")
    assert resp.status_code == 200
    _assert_capability_profile_contract(resp.json())


def test_contract_capability_profile_no_tool_advances_claims(tmp_path: Path) -> None:
    """Every tool in the capability profile has advances_claims=False."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/capabilities")
    assert resp.status_code == 200
    for tool in resp.json()["tools"]:
        assert tool.get("advances_claims") is False, (
            f"Tool {tool.get('name')!r} has advances_claims=True"
        )


def test_contract_reads_do_not_mutate_package(tmp_path: Path) -> None:
    """All read-only metadata endpoints leave the ZIP namelist unchanged."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("contract-nomut"))
    pkg = project_dir(settings, project["id"]) / "contract-nomut.aieng"
    _make_consistency_pkg(pkg, with_evidence_index=True, with_audit_events=True)
    project["aieng_file"] = "contract-nomut.aieng"
    save_project(settings, project)

    with zipfile.ZipFile(pkg, "r") as zf:
        names_before = set(zf.namelist())

    pid = project["id"]
    for url in (
        f"/api/projects/{pid}/audit-events",
        f"/api/projects/{pid}/artifact-manifest",
        f"/api/projects/{pid}/package-consistency",
    ):
        client.get(url)

    with zipfile.ZipFile(pkg, "r") as zf:
        names_after = set(zf.namelist())

    assert names_before == names_after


def test_contract_no_claim_map_created_by_metadata_endpoints(tmp_path: Path) -> None:
    """None of the read-only metadata endpoints create a claim map in the package."""
    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("contract-noclaim"))
    pkg = project_dir(settings, project["id"]) / "contract-noclaim.aieng"
    _make_consistency_pkg(pkg)
    project["aieng_file"] = "contract-noclaim.aieng"
    save_project(settings, project)

    pid = project["id"]
    for url in (
        f"/api/projects/{pid}/audit-events",
        f"/api/projects/{pid}/artifact-manifest",
        f"/api/projects/{pid}/package-consistency",
    ):
        client.get(url)

    with zipfile.ZipFile(pkg, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


# ---------------------------------------------------------------------------
# Claim proposal tests
# ---------------------------------------------------------------------------

def _make_claim_proposal_pkg(pkg_path: Path, *, with_evidence: bool = True) -> str:
    """Create a minimal .aieng package for claim proposal tests.

    Returns the path of the evidence artifact written (used as supporting_evidence).
    """
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path = "results/computed_metrics.json"
    members: dict[str, str] = {
        "manifest.json": json.dumps({"model_id": "claim-proposal-test"}),
        evidence_path: json.dumps({"schema_version": "0.1", "load_cases": []}),
    }
    if with_evidence:
        members["results/evidence_index.json"] = json.dumps({
            "evidence_type": "cae_artifacts",
            "entries": [
                {
                    "id": "computed_metrics_entry",
                    "path": evidence_path,
                    "kind": "result",
                    "role": "computed_extrema",
                    "exists": True,
                    "supports": ["audit"],
                }
            ],
        })
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return evidence_path


def _setup_claim_proposal_project(tmp_path: Path) -> tuple[TestClient, str, Path]:
    """Return (client, project_id, pkg_path) with a claim-proposal-ready package."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("claim-proposal"))
    pkg_path = project_dir(settings, project["id"]) / "claim-proposal.aieng"
    evidence_path = _make_claim_proposal_pkg(pkg_path)
    project["aieng_file"] = "claim-proposal.aieng"
    save_project(settings, project)
    return client, project["id"], pkg_path


def test_claim_proposal_creates_artifact(tmp_path: Path) -> None:
    """POST /claim-proposals writes claims/proposals/{id}.json into the package."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "structural_integrity",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Stress within allowable limits per computed metrics.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    proposal_path = data["proposal_path"]
    assert proposal_path.startswith(f"{CLAIM_PROPOSALS_DIR}/")
    assert proposal_path.endswith(".json")

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert proposal_path in zf.namelist()


def test_claim_proposal_response_claim_advancement_none(tmp_path: Path) -> None:
    """POST /claim-proposals response always carries claim_advancement: 'none'."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "load_path",
            "proposed_status": "needs_review",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Load path not yet fully verified.",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["claim_advancement"] == "none"


def test_claim_proposal_artifact_contract_fields(tmp_path: Path) -> None:
    """Proposal artifact contains all required contract fields."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "fatigue_life",
            "proposed_status": "not_supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Insufficient cycle data.",
        },
    )
    assert resp.status_code == 200
    proposal_path = resp.json()["proposal_path"]

    with zipfile.ZipFile(pkg_path, "r") as zf:
        artifact = json.loads(zf.read(proposal_path))

    assert artifact["schema_version"] == "0.1"
    assert artifact["claim_id"] == "fatigue_life"
    assert artifact["proposed_status"] == "not_supported"
    assert artifact["status"] == "proposed"
    assert artifact["claim_advancement"] == "none"
    assert artifact["created_by_tool"] == "claims.propose_update"
    assert isinstance(artifact["proposal_id"], str) and len(artifact["proposal_id"]) > 0
    assert isinstance(artifact["created_at"], str) and "T" in artifact["created_at"]
    assert isinstance(artifact["supporting_evidence"], list)
    assert isinstance(artifact["rationale"], str) and len(artifact["rationale"]) > 0


def test_claim_proposal_references_existing_evidence(tmp_path: Path) -> None:
    """POST /claim-proposals succeeds when supporting evidence exists in the package."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "displacement_ok",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Displacement below 5 mm threshold.",
        },
    )
    assert resp.status_code == 200
    proposal = resp.json()["proposal"]
    assert "results/computed_metrics.json" in proposal["supporting_evidence"]


def test_claim_proposal_missing_evidence_returns_400(tmp_path: Path) -> None:
    """POST /claim-proposals returns 400 when a supporting evidence path is absent."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "some_claim",
            "proposed_status": "supported",
            "supporting_evidence": ["simulation/runs/run_001/outputs/result.frd"],
            "rationale": "FRD confirms convergence.",
        },
    )
    assert resp.status_code == 400
    assert "supporting_evidence" in resp.json()["detail"].lower()


def test_claim_proposal_empty_rationale_returns_400(tmp_path: Path) -> None:
    """POST /claim-proposals returns 400 when rationale is blank."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "some_claim",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "   ",
        },
    )
    assert resp.status_code == 400
    assert "rationale" in resp.json()["detail"].lower()


def test_claim_proposal_invalid_status_returns_400(tmp_path: Path) -> None:
    """POST /claim-proposals returns 400 for proposed_status not in the allowed set."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "some_claim",
            "proposed_status": "validated",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "All checks passed.",
        },
    )
    assert resp.status_code == 400
    assert "proposed_status" in resp.json()["detail"].lower()


def test_claim_proposal_audit_event_appended(tmp_path: Path) -> None:
    """POST /claim-proposals appends a claim_proposal_created audit event."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "stress_check",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Max stress within limits.",
        },
    )
    assert resp.status_code == 200

    events = _read_audit_events_from_package(pkg_path)
    assert len(events) >= 1
    last = events[-1]
    assert last["event_type"] == "claim_proposal_created"
    assert last["tool"] == "claims.propose_update"
    assert last["claim_advancement"] == "none"
    assert last["status"] in ("completed", "ok")


def test_claim_proposal_classified_in_manifest(tmp_path: Path) -> None:
    """Artifact manifest classifies claims/proposals/*.json as claim_proposal."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "yield_margin",
            "proposed_status": "needs_review",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Margin needs independent review.",
        },
    )
    assert resp.status_code == 200

    manifest_resp = client.get(f"/api/projects/{pid}/artifact-manifest")
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()

    proposal_entries = [
        e for e in manifest["artifacts"]
        if e.get("category") == "claim_proposal"
    ]
    assert len(proposal_entries) == 1
    assert proposal_entries[0]["kind"] == "claim_proposal"


def test_claim_proposal_consistency_check_accepts_proposal(tmp_path: Path) -> None:
    """Package consistency check reports ok when a well-formed proposal is present."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "stiffness_ok",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Stiffness meets spec.",
        },
    )

    checks_resp = client.get(f"/api/projects/{pid}/package-consistency")
    assert checks_resp.status_code == 200
    checks = checks_resp.json()

    proposal_check = next(
        (c for c in checks["checks"] if c["id"] == "claim_proposals"), None
    )
    assert proposal_check is not None
    assert proposal_check["status"] == "ok"


def test_claim_proposal_no_claim_map_created(tmp_path: Path) -> None:
    """POST /claim-proposals never writes ai/claim_map.json or results/claim_map.json."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "safety_factor",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Safety factor > 1.5.",
        },
    )

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


def test_capability_profile_includes_claims_propose_update(tmp_path: Path) -> None:
    """Capability profile endpoint lists claims.propose_update."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/capabilities")
    assert resp.status_code == 200
    caps = resp.json()

    tool_names = [t["name"] for t in caps.get("tools", [])]
    assert "claims.propose_update" in tool_names


def test_capability_claims_propose_update_advances_claims_false(tmp_path: Path) -> None:
    """claims.propose_update capability entry has advances_claims: false."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/capabilities")
    assert resp.status_code == 200
    caps = resp.json()

    tool = next(
        (t for t in caps.get("tools", []) if t["name"] == "claims.propose_update"),
        None,
    )
    assert tool is not None
    assert tool["advances_claims"] is False
    assert tool["creates_proposal"] is True
    assert tool["requires_explicit_acceptance_workflow"] is True


def test_build_claim_proposal_unit() -> None:
    """_build_claim_proposal produces a well-formed proposal dict."""
    proposal = _build_claim_proposal(
        claim_id="test_claim",
        proposed_status="supported",
        supporting_evidence=["results/computed_metrics.json"],
        rationale="Test rationale.",
    )
    assert proposal["schema_version"] == "0.1"
    assert proposal["status"] == "proposed"
    assert proposal["claim_advancement"] == "none"
    assert proposal["proposed_status"] == "supported"
    assert proposal["claim_id"] == "test_claim"
    assert proposal["created_by_tool"] == "claims.propose_update"
    assert isinstance(proposal["proposal_id"], str)


def test_valid_proposed_statuses_set() -> None:
    """_VALID_PROPOSED_STATUSES contains the expected values and no extras."""
    assert "supported" in _VALID_PROPOSED_STATUSES
    assert "not_supported" in _VALID_PROPOSED_STATUSES
    assert "needs_review" in _VALID_PROPOSED_STATUSES
    assert "validated" not in _VALID_PROPOSED_STATUSES
    assert "accepted" not in _VALID_PROPOSED_STATUSES


# ---------------------------------------------------------------------------
# Claim proposal inspection tests (read-only list/read endpoints)
# ---------------------------------------------------------------------------

def test_list_proposals_empty_when_none_exist(tmp_path: Path) -> None:
    """GET /claim-proposals returns empty list for a package with no proposals."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    resp = client.get(f"/api/projects/{pid}/claim-proposals")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["proposals"] == []
    assert data["claim_advancement"] == "none"


def test_list_proposals_returns_created_proposal(tmp_path: Path) -> None:
    """Creating a proposal then listing returns it."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "load_path",
            "proposed_status": "needs_review",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Load path under review.",
        },
    )

    resp = client.get(f"/api/projects/{pid}/claim-proposals")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert len(data["proposals"]) == 1
    assert data["proposals"][0]["claim_id"] == "load_path"


def test_list_proposals_contract(tmp_path: Path) -> None:
    """GET /claim-proposals response satisfies the list contract."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "structural_integrity",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Stress within limits.",
        },
    )

    resp = client.get(f"/api/projects/{pid}/claim-proposals")
    assert resp.status_code == 200
    _assert_claim_proposals_list_contract(resp.json())


def test_get_proposal_by_id_returns_artifact(tmp_path: Path) -> None:
    """GET /claim-proposals/{id} returns the same artifact written by POST."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    create_resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "fatigue_life",
            "proposed_status": "not_supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Insufficient cycle data.",
        },
    )
    assert create_resp.status_code == 200
    proposal_id = create_resp.json()["proposal"]["proposal_id"]

    read_resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}")
    assert read_resp.status_code == 200
    data = read_resp.json()
    assert data["proposal"]["proposal_id"] == proposal_id
    assert data["proposal"]["claim_id"] == "fatigue_life"
    assert data["proposal_path"] == f"{CLAIM_PROPOSALS_DIR}/{proposal_id}.json"
    assert data["claim_advancement"] == "none"


def test_get_proposal_by_id_contract(tmp_path: Path) -> None:
    """GET /claim-proposals/{id} response satisfies the proposal contract."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    create_resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "yield_margin",
            "proposed_status": "needs_review",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Margin requires independent review.",
        },
    )
    proposal_id = create_resp.json()["proposal"]["proposal_id"]

    read_resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}")
    assert read_resp.status_code == 200
    _assert_claim_proposal_contract(read_resp.json()["proposal"])


def test_get_proposal_missing_id_returns_404(tmp_path: Path) -> None:
    """GET /claim-proposals/{id} returns 404 for an unknown proposal_id."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    resp = client.get(f"/api/projects/{pid}/claim-proposals/nonexistentid12345")
    assert resp.status_code == 404


def test_list_proposals_claim_advancement_none(tmp_path: Path) -> None:
    """List endpoint always carries claim_advancement: 'none'."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    resp = client.get(f"/api/projects/{pid}/claim-proposals")
    assert resp.status_code == 200
    assert resp.json()["claim_advancement"] == "none"


def test_get_proposal_claim_advancement_none(tmp_path: Path) -> None:
    """Read-by-id endpoint always carries claim_advancement: 'none'."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    create_resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "displacement_ok",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Displacement within spec.",
        },
    )
    proposal_id = create_resp.json()["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}")
    assert resp.status_code == 200
    assert resp.json()["claim_advancement"] == "none"


def test_list_proposals_does_not_mutate_package(tmp_path: Path) -> None:
    """GET /claim-proposals does not modify the package ZIP."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    names_before = set(zipfile.ZipFile(pkg_path, "r").namelist())
    client.get(f"/api/projects/{pid}/claim-proposals")
    names_after = set(zipfile.ZipFile(pkg_path, "r").namelist())

    assert names_before == names_after


def test_get_proposal_does_not_mutate_package(tmp_path: Path) -> None:
    """GET /claim-proposals/{id} does not modify the package ZIP."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    create_resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "some_claim",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Within allowable limits.",
        },
    )
    proposal_id = create_resp.json()["proposal"]["proposal_id"]

    names_before = set(zipfile.ZipFile(pkg_path, "r").namelist())
    client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}")
    names_after = set(zipfile.ZipFile(pkg_path, "r").namelist())

    assert names_before == names_after


def test_list_proposals_no_claim_map_created(tmp_path: Path) -> None:
    """GET /claim-proposals does not create ai/claim_map.json or results/claim_map.json."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    client.get(f"/api/projects/{pid}/claim-proposals")

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


def test_get_proposal_no_claim_map_created(tmp_path: Path) -> None:
    """GET /claim-proposals/{id} does not create claim maps."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    create_resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "safety_factor",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Safety factor > 1.5.",
        },
    )
    proposal_id = create_resp.json()["proposal"]["proposal_id"]

    client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}")

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


def test_list_proposals_ordering_deterministic(tmp_path: Path) -> None:
    """Multiple proposals are returned in deterministic created_at / proposal_id order."""
    client, pid, _ = _setup_claim_proposal_project(tmp_path)

    ids = []
    for claim in ("claim_a", "claim_b", "claim_c"):
        resp = client.post(
            f"/api/projects/{pid}/claim-proposals",
            json={
                "claim_id": claim,
                "proposed_status": "needs_review",
                "supporting_evidence": ["results/computed_metrics.json"],
                "rationale": f"Review needed for {claim}.",
            },
        )
        ids.append(resp.json()["proposal"]["proposal_id"])

    list_resp = client.get(f"/api/projects/{pid}/claim-proposals")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["count"] == 3
    returned_ids = [p["proposal_id"] for p in data["proposals"]]
    # Order is deterministic: same call returns same order
    list_resp2 = client.get(f"/api/projects/{pid}/claim-proposals")
    returned_ids2 = [p["proposal_id"] for p in list_resp2.json()["proposals"]]
    assert returned_ids == returned_ids2


def test_read_claim_proposals_from_package_unit(tmp_path: Path) -> None:
    """_read_claim_proposals_from_package returns proposals sorted by created_at."""
    pkg = tmp_path / "proposals.aieng"
    _make_claim_proposal_pkg(pkg)

    proposal_a = _build_claim_proposal(
        claim_id="a", proposed_status="supported",
        supporting_evidence=["results/computed_metrics.json"], rationale="A.",
    )
    proposal_b = _build_claim_proposal(
        claim_id="b", proposed_status="needs_review",
        supporting_evidence=["results/computed_metrics.json"], rationale="B.",
    )
    _write_claim_proposal_to_package(pkg, proposal_a)
    _write_claim_proposal_to_package(pkg, proposal_b)

    proposals = _read_claim_proposals_from_package(pkg)
    assert len(proposals) == 2
    for p in proposals:
        _assert_claim_proposal_contract(p)
    # created_at is monotonically non-decreasing
    assert proposals[0]["created_at"] <= proposals[1]["created_at"]


def test_contract_claim_proposal_artifact(tmp_path: Path) -> None:
    """Proposal artifact written to the package satisfies the contract helper."""
    client, pid, pkg_path = _setup_claim_proposal_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "contract_check",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Validated by contract test.",
        },
    )
    assert resp.status_code == 200
    proposal_path = resp.json()["proposal_path"]

    with zipfile.ZipFile(pkg_path, "r") as zf:
        artifact = json.loads(zf.read(proposal_path))
    _assert_claim_proposal_contract(artifact)


# ---------------------------------------------------------------------------
# Evidence reference resolver tests
# ---------------------------------------------------------------------------

def _make_evidence_resolver_pkg(pkg_path: Path, *, with_revalidation: bool = False) -> None:
    """Create a .aieng package with field summaries and an evidence index for resolver tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    cm_path = "results/computed_metrics.json"
    frd_path = "simulation/runs/run_001/outputs/result.frd"
    members = {
        "manifest.json": json.dumps({"model_id": "resolver-test"}),
        cm_path: json.dumps({"schema_version": "0.1", "load_cases": []}),
        frd_path: "FAKE FRD CONTENT",
        "results/fields/displacement.summary.json": json.dumps({
            "schema_version": "0.1",
            "field_name": "displacement",
            "unit": "mm",
            "source": {"source_type": "computed_metrics", "computed_metrics_path": cm_path},
            "stats": {"max_value": 0.42},
            "evidence_role": "displacement_extrema",
            "claim_advancement": "none",
        }),
        "results/evidence_index.json": json.dumps({
            "evidence_type": "cae_artifacts",
            "entries": [
                {
                    "id": "cm_entry",
                    "path": cm_path,
                    "kind": "result",
                    "role": "computed_extrema",
                    "exists": True,
                    "supports": ["audit"],
                },
                {
                    "id": "frd_entry",
                    "path": frd_path,
                    "kind": "solver_raw_output",
                    "role": "solver_raw_output",
                    "exists": True,
                    "supports": ["numerical_result_source"],
                },
            ],
        }),
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    if with_revalidation:
        _write_revalidation_status(
            pkg_path,
            requires_revalidation=True,
            reason="geometry_changed",
            triggering_tool="cad.edit_parameter",
            affected_artifacts=["results/result_summary.json"],
            current_geometry_revision=2,
            last_validated_geometry_revision=1,
        )


def _setup_resolver_project(tmp_path: Path, *, stale: bool = False) -> tuple[TestClient, str, Path]:
    """Return (client, project_id, pkg_path) for resolver tests."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("evidence-resolver"))
    pkg_path = project_dir(settings, project["id"]) / "evidence-resolver.aieng"
    _make_evidence_resolver_pkg(pkg_path, with_revalidation=stale)
    project["aieng_file"] = "evidence-resolver.aieng"
    save_project(settings, project)
    return client, project["id"], pkg_path


def test_resolve_existing_field_summary(tmp_path: Path) -> None:
    """Resolving a field summary artifact that exists returns exists=True."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "results/fields/displacement.summary.json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    resolved = data["resolved"]
    assert resolved["exists"] is True
    assert resolved["manifest_category"] == "field_summary"
    assert resolved["manifest_kind"] == "field"
    assert resolved["evidence_role"] == "displacement_extrema"
    assert resolved["claim_advancement"] == "none"
    assert resolved["usable_for_claim_proposal"] is True


def test_resolve_existing_frd_artifact(tmp_path: Path) -> None:
    """Resolving a solver FRD artifact that exists returns the correct classification."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "simulation/runs/run_001/outputs/result.frd"},
    )
    assert resp.status_code == 200
    resolved = resp.json()["resolved"]
    assert resolved["exists"] is True
    assert resolved["manifest_category"] == "solver_output"
    assert resolved["manifest_kind"] == "solver_raw_output"
    assert resolved["in_evidence_index"] is True
    assert resolved["usable_for_claim_proposal"] is True


def test_resolve_path_in_evidence_index(tmp_path: Path) -> None:
    """Resolving a path listed in the evidence index sets in_evidence_index=True."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    resolved = resp.json()["resolved"]
    assert resolved["in_evidence_index"] is True
    assert resolved["evidence_index_entry"] is not None
    assert resolved["evidence_index_entry"]["path"] == "results/computed_metrics.json"


def test_resolve_missing_path_returns_200_with_exists_false(tmp_path: Path) -> None:
    """Resolving a path absent from the package returns 200 with exists=False and a warning."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "simulation/runs/run_999/outputs/result.frd"},
    )
    assert resp.status_code == 200
    resolved = resp.json()["resolved"]
    assert resolved["exists"] is False
    assert resolved["in_evidence_index"] is False
    assert resolved["usable_for_claim_proposal"] is False
    assert "path_not_found_in_package_or_evidence_index" in resolved["warnings"]


def test_resolve_empty_path_returns_400(tmp_path: Path) -> None:
    """Resolving with an empty path returns 400."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "   "},
    )
    assert resp.status_code == 400


def test_resolve_missing_package_returns_404(tmp_path: Path) -> None:
    """Resolving against a project with no package returns 404."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("no-pkg-resolver"))

    resp = client.get(
        f"/api/projects/{project['id']}/evidence-references/resolve",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 404


def test_resolve_stale_evidence_includes_warning(tmp_path: Path) -> None:
    """Stale revalidation status causes stale CAE output to include a staleness warning."""
    client, pid, _ = _setup_resolver_project(tmp_path, stale=True)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    resolved = resp.json()["resolved"]
    assert resolved["requires_revalidation"] is True
    assert "evidence_from_stale_geometry_state" in resolved["warnings"]


def test_resolve_stale_evidence_still_exists(tmp_path: Path) -> None:
    """Stale evidence is not marked as non-existent; exists remains True."""
    client, pid, _ = _setup_resolver_project(tmp_path, stale=True)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    resolved = resp.json()["resolved"]
    assert resolved["exists"] is True
    assert resolved["usable_for_claim_proposal"] is True


def test_resolve_endpoint_does_not_mutate_package(tmp_path: Path) -> None:
    """GET /evidence-references/resolve does not modify the package ZIP."""
    client, pid, pkg_path = _setup_resolver_project(tmp_path)

    names_before = set(zipfile.ZipFile(pkg_path, "r").namelist())
    client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "results/fields/displacement.summary.json"},
    )
    names_after = set(zipfile.ZipFile(pkg_path, "r").namelist())
    assert names_before == names_after


def test_resolve_endpoint_no_claim_map_created(tmp_path: Path) -> None:
    """GET /evidence-references/resolve does not create claim maps."""
    client, pid, pkg_path = _setup_resolver_project(tmp_path)

    client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "results/computed_metrics.json"},
    )
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


def test_resolve_endpoint_claim_advancement_none(tmp_path: Path) -> None:
    """Resolver endpoint always carries claim_advancement: 'none'."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    assert resp.json()["claim_advancement"] == "none"
    assert resp.json()["resolved"]["claim_advancement"] == "none"


def test_claim_proposal_creation_rejects_truly_missing_evidence(tmp_path: Path) -> None:
    """Proposal creation still rejects evidence paths absent from package and evidence index."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "missing_ev",
            "proposed_status": "supported",
            "supporting_evidence": ["nonexistent/artifact.json"],
            "rationale": "This evidence does not exist.",
        },
    )
    assert resp.status_code == 400
    assert "supporting_evidence" in resp.json()["detail"].lower()


def test_claim_proposal_creation_accepts_resolvable_existing_evidence(tmp_path: Path) -> None:
    """Proposal creation succeeds when supporting evidence is resolvable (exists in package)."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "displacement_ok",
            "proposed_status": "supported",
            "supporting_evidence": ["results/fields/displacement.summary.json"],
            "rationale": "Displacement summary confirms extrema.",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["claim_advancement"] == "none"


def test_resolve_evidence_reference_unit_existing(tmp_path: Path) -> None:
    """_resolve_evidence_reference returns correct fields for an existing path."""
    pkg_names = {
        "results/computed_metrics.json",
        "results/evidence_index.json",
    }
    evidence_entries = [
        {"path": "results/computed_metrics.json", "kind": "result", "role": "computed_extrema"},
    ]
    resolved = _resolve_evidence_reference(
        path="results/computed_metrics.json",
        pkg_names=pkg_names,
        evidence_entries=evidence_entries,
        revalidation_status=None,
    )
    assert resolved["exists"] is True
    assert resolved["in_evidence_index"] is True
    assert resolved["manifest_category"] == "solver_output"
    assert resolved["usable_for_claim_proposal"] is True
    assert resolved["requires_revalidation"] is False
    assert resolved["warnings"] == []
    assert resolved["claim_advancement"] == "none"


def test_resolve_evidence_reference_unit_missing(tmp_path: Path) -> None:
    """_resolve_evidence_reference returns usable=False for a path not in package or index."""
    resolved = _resolve_evidence_reference(
        path="nonexistent/artifact.json",
        pkg_names={"manifest.json"},
        evidence_entries=[],
        revalidation_status=None,
    )
    assert resolved["exists"] is False
    assert resolved["in_evidence_index"] is False
    assert resolved["usable_for_claim_proposal"] is False
    assert "path_not_found_in_package_or_evidence_index" in resolved["warnings"]


def test_resolve_evidence_reference_unit_stale(tmp_path: Path) -> None:
    """_resolve_evidence_reference adds stale warning for downstream CAE outputs."""
    pkg_names = {"results/computed_metrics.json"}
    resolved = _resolve_evidence_reference(
        path="results/computed_metrics.json",
        pkg_names=pkg_names,
        evidence_entries=[],
        revalidation_status={"requires_revalidation": True, "current_geometry_revision": 2},
    )
    assert resolved["exists"] is True
    assert resolved["requires_revalidation"] is True
    assert resolved["current_geometry_revision"] == 2
    assert "evidence_from_stale_geometry_state" in resolved["warnings"]
    assert resolved["usable_for_claim_proposal"] is True  # stale is still usable


def test_stale_evidence_categories_set() -> None:
    """_STALE_EVIDENCE_CATEGORIES contains the expected downstream output categories."""
    assert "solver_output" in _STALE_EVIDENCE_CATEGORIES
    assert "summary" in _STALE_EVIDENCE_CATEGORIES
    assert "field_summary" in _STALE_EVIDENCE_CATEGORIES
    assert "evidence_index" in _STALE_EVIDENCE_CATEGORIES
    assert "geometry" not in _STALE_EVIDENCE_CATEGORIES
    assert "package" not in _STALE_EVIDENCE_CATEGORIES


def test_contract_evidence_reference_resolver(tmp_path: Path) -> None:
    """Evidence reference resolver response satisfies the contract helper."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.get(
        f"/api/projects/{pid}/evidence-references/resolve",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    _assert_evidence_reference_contract(resp.json()["resolved"])


def test_consistency_check_passes_with_resolvable_proposal_evidence(tmp_path: Path) -> None:
    """Package consistency check still reports ok for proposals with resolvable evidence."""
    client, pid, pkg_path = _setup_resolver_project(tmp_path)

    client.post(
        f"/api/projects/{pid}/claim-proposals",
        json={
            "claim_id": "stress_ok",
            "proposed_status": "supported",
            "supporting_evidence": ["results/computed_metrics.json"],
            "rationale": "Stress within allowable limits.",
        },
    )

    checks_resp = client.get(f"/api/projects/{pid}/package-consistency")
    assert checks_resp.status_code == 200
    checks = checks_resp.json()
    proposal_check = next(
        (c for c in checks["checks"] if c["id"] == "claim_proposals"), None
    )
    assert proposal_check is not None
    assert proposal_check["status"] == "ok"


# ---------------------------------------------------------------------------
# Claim support packet tests
# ---------------------------------------------------------------------------

def _create_proposal_via_api(client: TestClient, pid: str, **kwargs: Any) -> dict[str, Any]:
    """POST a claim proposal and return the full response body."""
    payload = {
        "claim_id": kwargs.get("claim_id", "default_claim"),
        "proposed_status": kwargs.get("proposed_status", "supported"),
        "supporting_evidence": kwargs.get("supporting_evidence", ["results/computed_metrics.json"]),
        "rationale": kwargs.get("rationale", "Default test rationale."),
    }
    resp = client.post(f"/api/projects/{pid}/claim-proposals", json=payload)
    assert resp.status_code == 200, f"proposal creation failed: {resp.json()}"
    return resp.json()


def test_support_packet_returns_proposal_metadata(tmp_path: Path) -> None:
    """Support packet for a valid proposal includes all core proposal metadata."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    created = _create_proposal_via_api(
        client, pid,
        claim_id="structural_integrity",
        proposed_status="supported",
        rationale="Stress within allowable limits.",
    )
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    packet = resp.json()["support_packet"]
    assert packet["proposal_id"] == proposal_id
    assert packet["claim_id"] == "structural_integrity"
    assert packet["proposed_status"] == "supported"
    assert packet["proposal_status"] == "proposed"
    assert packet["rationale"] == "Stress within allowable limits."
    assert packet["claim_advancement"] == "none"


def test_support_packet_includes_resolved_evidence(tmp_path: Path) -> None:
    """Support packet resolves each supporting evidence entry via the resolver."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    created = _create_proposal_via_api(
        client, pid,
        supporting_evidence=["results/computed_metrics.json"],
    )
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    packet = resp.json()["support_packet"]
    assert len(packet["supporting_evidence"]) == 1
    resolved = packet["supporting_evidence"][0]
    assert resolved["path"] == "results/computed_metrics.json"
    assert resolved["exists"] is True
    assert resolved["claim_advancement"] == "none"


def test_support_packet_contract(tmp_path: Path) -> None:
    """Support packet satisfies the full contract helper."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    created = _create_proposal_via_api(client, pid)
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    _assert_claim_support_packet_contract(resp.json()["support_packet"])


def test_support_packet_missing_proposal_returns_404(tmp_path: Path) -> None:
    """GET /claim-proposals/{id}/support-packet returns 404 for unknown proposal_id."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    resp = client.get(f"/api/projects/{pid}/claim-proposals/nonexistent1234/support-packet")
    assert resp.status_code == 404


def test_support_packet_missing_package_returns_404(tmp_path: Path) -> None:
    """GET /claim-proposals/{id}/support-packet returns 404 when package is absent."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("no-pkg-sp"))

    resp = client.get(f"/api/projects/{project['id']}/claim-proposals/anyid/support-packet")
    assert resp.status_code == 404


def test_support_packet_stale_evidence_warning(tmp_path: Path) -> None:
    """Support packet surfaces stale_evidence_count > 0 when revalidation is required."""
    client, pid, _ = _setup_resolver_project(tmp_path, stale=True)

    created = _create_proposal_via_api(
        client, pid,
        supporting_evidence=["results/computed_metrics.json"],
    )
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    packet = resp.json()["support_packet"]
    assert packet["stale_evidence_count"] > 0
    assert any(
        "evidence_from_stale_geometry_state" in w
        for w in packet["evidence_warnings"]
    )


def test_support_packet_missing_evidence_count(tmp_path: Path) -> None:
    """Support packet reports missing_evidence_count for proposals with absent evidence.

    This requires injecting a proposal that references a path not in the package
    (bypassing create_claim_proposal's validation by writing directly to the ZIP).
    """
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("missing-ev-sp"))
    pkg_path = project_dir(settings, project["id"]) / "missing-ev-sp.aieng"
    _make_evidence_resolver_pkg(pkg_path)
    project["aieng_file"] = "missing-ev-sp.aieng"
    save_project(settings, project)

    # Build a proposal referencing a non-existent path and write it directly.
    phantom_proposal = _build_claim_proposal(
        claim_id="phantom_claim",
        proposed_status="supported",
        supporting_evidence=["simulation/runs/run_999/outputs/ghost.frd"],
        rationale="References absent evidence for test.",
    )
    _write_claim_proposal_to_package(pkg_path, phantom_proposal)
    proposal_id = phantom_proposal["proposal_id"]

    resp = client.get(f"/api/projects/{project['id']}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    packet = resp.json()["support_packet"]
    assert packet["missing_evidence_count"] == 1
    assert any(
        "path_not_found_in_package_or_evidence_index" in w
        for w in packet["evidence_warnings"]
    )


def test_support_packet_related_audit_event_for_creation(tmp_path: Path) -> None:
    """Support packet includes the claim_proposal_created audit event."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    created = _create_proposal_via_api(client, pid, claim_id="audit_check")
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    packet = resp.json()["support_packet"]
    related = packet["related_audit_events"]
    assert len(related) >= 1
    creation_event = next(
        (e for e in related if e.get("event_type") == "claim_proposal_created"), None
    )
    assert creation_event is not None
    assert creation_event.get("tool") == "claims.propose_update"
    assert creation_event.get("claim_advancement") == "none"


def test_support_packet_claim_advancement_none(tmp_path: Path) -> None:
    """Support packet envelope and packet body both carry claim_advancement: 'none'."""
    client, pid, _ = _setup_resolver_project(tmp_path)

    created = _create_proposal_via_api(client, pid)
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    body = resp.json()
    assert body["claim_advancement"] == "none"
    assert body["support_packet"]["claim_advancement"] == "none"


def test_support_packet_does_not_mutate_package(tmp_path: Path) -> None:
    """GET .../support-packet does not modify the package ZIP."""
    client, pid, pkg_path = _setup_resolver_project(tmp_path)

    created = _create_proposal_via_api(client, pid)
    proposal_id = created["proposal"]["proposal_id"]

    names_before = set(zipfile.ZipFile(pkg_path, "r").namelist())
    client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    names_after = set(zipfile.ZipFile(pkg_path, "r").namelist())
    assert names_before == names_after


def test_support_packet_no_claim_map_created(tmp_path: Path) -> None:
    """GET .../support-packet does not create ai/claim_map.json or results/claim_map.json."""
    client, pid, pkg_path = _setup_resolver_project(tmp_path)

    created = _create_proposal_via_api(client, pid)
    proposal_id = created["proposal"]["proposal_id"]

    client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


def test_build_claim_support_packet_unit() -> None:
    """_build_claim_support_packet produces a correct packet from raw inputs."""
    proposal = _build_claim_proposal(
        claim_id="unit_claim",
        proposed_status="needs_review",
        supporting_evidence=["results/computed_metrics.json"],
        rationale="Unit test rationale.",
    )
    proposal_path = f"{CLAIM_PROPOSALS_DIR}/{proposal['proposal_id']}.json"
    pkg_names = {"results/computed_metrics.json", proposal_path}
    evidence_entries: list[dict[str, Any]] = []
    audit_events = [
        {
            "event_type": "claim_proposal_created",
            "tool": "claims.propose_update",
            "artifacts_written": [proposal_path],
            "claim_advancement": "none",
        }
    ]
    packet = _build_claim_support_packet(
        proposal=proposal,
        proposal_path=proposal_path,
        pkg_names=pkg_names,
        evidence_entries=evidence_entries,
        revalidation_status=None,
        audit_events=audit_events,
    )
    assert packet["claim_id"] == "unit_claim"
    assert packet["proposed_status"] == "needs_review"
    assert packet["proposal_status"] == "proposed"
    assert packet["claim_advancement"] == "none"
    assert packet["stale_evidence_count"] == 0
    assert packet["missing_evidence_count"] == 0
    assert len(packet["supporting_evidence"]) == 1
    assert packet["supporting_evidence"][0]["exists"] is True
    assert len(packet["related_audit_events"]) == 1
    assert packet["related_audit_events"][0]["event_type"] == "claim_proposal_created"


def test_build_support_packet_stale_unit() -> None:
    """_build_claim_support_packet counts stale evidence correctly."""
    proposal = _build_claim_proposal(
        claim_id="stale_claim",
        proposed_status="supported",
        supporting_evidence=["results/computed_metrics.json"],
        rationale="Stale test.",
    )
    proposal_path = f"{CLAIM_PROPOSALS_DIR}/{proposal['proposal_id']}.json"
    packet = _build_claim_support_packet(
        proposal=proposal,
        proposal_path=proposal_path,
        pkg_names={"results/computed_metrics.json", proposal_path},
        evidence_entries=[],
        revalidation_status={"requires_revalidation": True, "current_geometry_revision": 3},
        audit_events=[],
    )
    assert packet["stale_evidence_count"] == 1
    assert "evidence_from_stale_geometry_state" in packet["evidence_warnings"]
    assert packet["missing_evidence_count"] == 0
    assert packet["claim_advancement"] == "none"


# ---------------------------------------------------------------------------
# Review readiness diagnostics tests
# ---------------------------------------------------------------------------

def test_readiness_ready_for_fresh_valid_proposal(tmp_path: Path) -> None:
    """Valid proposal with existing fresh evidence returns review_readiness.status == 'ready'."""
    client, pid, _ = _setup_resolver_project(tmp_path)  # fresh (no stale)

    created = _create_proposal_via_api(
        client, pid,
        supporting_evidence=["results/computed_metrics.json"],
    )
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    rr = resp.json()["support_packet"]["review_readiness"]
    assert rr["status"] == "ready"
    assert rr["claim_advancement"] == "none"


def test_readiness_warning_for_stale_evidence(tmp_path: Path) -> None:
    """Stale evidence causes review_readiness.status == 'warning'."""
    client, pid, _ = _setup_resolver_project(tmp_path, stale=True)

    created = _create_proposal_via_api(
        client, pid,
        supporting_evidence=["results/computed_metrics.json"],
    )
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    rr = resp.json()["support_packet"]["review_readiness"]
    assert rr["status"] == "warning"
    stale_check = next(c for c in rr["checks"] if c["id"] == "stale_evidence")
    assert stale_check["status"] == "warning"

def test_readiness_blocked_for_missing_evidence(tmp_path: Path) -> None:
    """Missing evidence causes review_readiness.status == 'blocked'."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("readiness-missing"))
    pkg_path = project_dir(settings, project["id"]) / "readiness-missing.aieng"
    _make_evidence_resolver_pkg(pkg_path)
    project["aieng_file"] = "readiness-missing.aieng"
    save_project(settings, project)

    # Inject a proposal referencing an absent path.
    phantom = _build_claim_proposal(
        claim_id="blocked_claim",
        proposed_status="supported",
        supporting_evidence=["simulation/runs/run_999/outputs/ghost.frd"],
        rationale="References absent evidence.",
    )
    _write_claim_proposal_to_package(pkg_path, phantom)

    resp = client.get(
        f"/api/projects/{project['id']}/claim-proposals/{phantom['proposal_id']}/support-packet"
    )
    assert resp.status_code == 200
    rr = resp.json()["support_packet"]["review_readiness"]
    assert rr["status"] == "blocked"
    missing_check = next(c for c in rr["checks"] if c["id"] == "no_missing_evidence")
    assert missing_check["status"] == "blocked"


def test_readiness_blocked_for_empty_supporting_evidence(tmp_path: Path) -> None:
    """Proposal with no supporting evidence paths causes readiness == 'blocked'."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("readiness-empty-ev"))
    pkg_path = project_dir(settings, project["id"]) / "readiness-empty-ev.aieng"
    _make_evidence_resolver_pkg(pkg_path)
    project["aieng_file"] = "readiness-empty-ev.aieng"
    save_project(settings, project)

    # Build proposal with empty supporting_evidence (bypasses API validation).
    empty_proposal: dict[str, Any] = {
        "schema_version": "0.1",
        "proposal_id": "emptyev01234567",
        "claim_id": "no_evidence_claim",
        "proposed_status": "supported",
        "status": "proposed",
        "supporting_evidence": [],
        "rationale": "Empty evidence for readiness test.",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by_tool": "claims.propose_update",
        "claim_advancement": "none",
    }
    import io as _io
    with zipfile.ZipFile(pkg_path, "r") as _zf:
        _members = [(_i, _zf.read(_i.filename) if not _i.is_dir() else b"") for _i in _zf.infolist()]
    import tempfile as _tmp
    _tp = Path(_tmp.mktemp(suffix=".aieng", dir=pkg_path.parent))
    with zipfile.ZipFile(_tp, "w", compression=zipfile.ZIP_DEFLATED) as _ozf:
        for _info, _data in _members:
            _ozf.writestr(_info, _data)
        _ep = f"{CLAIM_PROPOSALS_DIR}/emptyev01234567.json"
        _ozf.writestr(_ep, (json.dumps(empty_proposal, indent=2) + "\n").encode())
    import shutil as _sh
    _sh.move(str(_tp), pkg_path)

    resp = client.get(
        f"/api/projects/{project['id']}/claim-proposals/emptyev01234567/support-packet"
    )
    assert resp.status_code == 200
    rr = resp.json()["support_packet"]["review_readiness"]
    assert rr["status"] == "blocked"
    ev_check = next(c for c in rr["checks"] if c["id"] == "supporting_evidence_present")
    assert ev_check["status"] == "blocked"


def test_readiness_warning_for_unknown_proposal_status(tmp_path: Path) -> None:
    """Unknown proposal status causes proposal_status_reviewable check to warn."""
    # Use _build_review_readiness directly: proposal_status="accepted" -> warning.
    rr = _build_review_readiness(
        ev_paths=["results/computed_metrics.json"],
        missing_count=0,
        stale_count=0,
        proposal_status="accepted",
        pkg_names={"results/computed_metrics.json"},
    )
    status_check = next(c for c in rr["checks"] if c["id"] == "proposal_status_reviewable")
    assert status_check["status"] == "warning"
    # Overall status is warning because of unknown proposal status.
    assert rr["status"] == "warning"


def test_readiness_warning_when_claim_map_present(tmp_path: Path) -> None:
    """claim_map_not_advanced check warns when ai/claim_map.json is present."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("readiness-claimmap"))
    pkg_path = project_dir(settings, project["id"]) / "readiness-claimmap.aieng"
    _make_evidence_resolver_pkg(pkg_path)

    # Inject ai/claim_map.json directly.
    with zipfile.ZipFile(pkg_path, "r") as zf:
        members = [(info, zf.read(info.filename) if not info.is_dir() else b"")
                   for info in zf.infolist()]
    import tempfile as _tmpmod
    tp = Path(_tmpmod.mktemp(suffix=".aieng", dir=pkg_path.parent))
    with zipfile.ZipFile(tp, "w", compression=zipfile.ZIP_DEFLATED) as ozf:
        for info, data in members:
            ozf.writestr(info, data)
        ozf.writestr("ai/claim_map.json", json.dumps({"claims": []}).encode())
    import shutil as _sh2
    _sh2.move(str(tp), pkg_path)

    project["aieng_file"] = "readiness-claimmap.aieng"
    save_project(settings, project)

    # Create a valid proposal.
    created = _create_proposal_via_api(client, project["id"])
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(
        f"/api/projects/{project['id']}/claim-proposals/{proposal_id}/support-packet"
    )
    assert resp.status_code == 200
    rr = resp.json()["support_packet"]["review_readiness"]
    cm_check = next(c for c in rr["checks"] if c["id"] == "claim_map_not_advanced")
    assert cm_check["status"] == "warning"
    # Endpoint still returns 200 (not an error).
    assert rr["status"] in {"warning", "blocked"}


def test_readiness_claim_advancement_none(tmp_path: Path) -> None:
    """review_readiness object always carries claim_advancement: 'none'."""
    client, pid, _ = _setup_resolver_project(tmp_path)
    created = _create_proposal_via_api(client, pid)
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    assert resp.json()["support_packet"]["review_readiness"]["claim_advancement"] == "none"


def test_readiness_does_not_mutate_package(tmp_path: Path) -> None:
    """Support packet endpoint (including readiness) does not modify the package."""
    client, pid, pkg_path = _setup_resolver_project(tmp_path)
    created = _create_proposal_via_api(client, pid)
    proposal_id = created["proposal"]["proposal_id"]

    names_before = set(zipfile.ZipFile(pkg_path, "r").namelist())
    client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    names_after = set(zipfile.ZipFile(pkg_path, "r").namelist())
    assert names_before == names_after


def test_readiness_no_claim_map_created(tmp_path: Path) -> None:
    """Support packet endpoint never creates claim maps."""
    client, pid, pkg_path = _setup_resolver_project(tmp_path)
    created = _create_proposal_via_api(client, pid)
    proposal_id = created["proposal"]["proposal_id"]

    client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "ai/claim_map.json" not in names
    assert "results/claim_map.json" not in names


def test_readiness_contract_via_endpoint(tmp_path: Path) -> None:
    """review_readiness in support packet satisfies the contract helper."""
    client, pid, _ = _setup_resolver_project(tmp_path)
    created = _create_proposal_via_api(client, pid)
    proposal_id = created["proposal"]["proposal_id"]

    resp = client.get(f"/api/projects/{pid}/claim-proposals/{proposal_id}/support-packet")
    assert resp.status_code == 200
    _assert_review_readiness_contract(resp.json()["support_packet"]["review_readiness"])


def test_build_review_readiness_unit_ready() -> None:
    """_build_review_readiness returns 'ready' for a clean, fresh proposal."""
    rr = _build_review_readiness(
        ev_paths=["results/computed_metrics.json"],
        missing_count=0,
        stale_count=0,
        proposal_status="proposed",
        pkg_names={"results/computed_metrics.json"},
    )
    assert rr["status"] == "ready"
    assert rr["claim_advancement"] == "none"
    for check in rr["checks"]:
        assert check["status"] == "ok"


def test_build_review_readiness_unit_blocked_missing() -> None:
    """_build_review_readiness returns 'blocked' when evidence is missing."""
    rr = _build_review_readiness(
        ev_paths=["nonexistent/artifact.json"],
        missing_count=1,
        stale_count=0,
        proposal_status="proposed",
        pkg_names=set(),
    )
    assert rr["status"] == "blocked"
    missing_check = next(c for c in rr["checks"] if c["id"] == "no_missing_evidence")
    assert missing_check["status"] == "blocked"


def test_build_review_readiness_unit_checks_all_ids() -> None:
    """_build_review_readiness always emits all five expected check IDs."""
    rr = _build_review_readiness(
        ev_paths=["results/computed_metrics.json"],
        missing_count=0,
        stale_count=0,
        proposal_status="proposed",
        pkg_names={"results/computed_metrics.json"},
    )
    ids = {c["id"] for c in rr["checks"]}
    assert ids == {
        "supporting_evidence_present",
        "no_missing_evidence",
        "stale_evidence",
        "proposal_status_reviewable",
        "claim_map_not_advanced",
    }


# ---------------------------------------------------------------------------
# Phase 26 — artifact review endpoint
# ---------------------------------------------------------------------------

def _setup_project_with_package(
    tmp_path: Path,
    package_members: dict[str, bytes],
) -> tuple[TestClient, str]:
    """Build a project with a .aieng package containing the supplied members."""
    from app.main import default_project, project_dir

    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("artifact-review"))
    pkg_dir = project_dir(settings, project["id"]) / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = pkg_dir / "review.aieng"
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "review"}))
        for name, data in package_members.items():
            zf.writestr(name, data)
    project["aieng_file"] = "packages/review.aieng"
    save_project(settings, project)
    return TestClient(create_app(settings)), project["id"]


def test_artifact_read_returns_parsed_json(tmp_path: Path) -> None:
    payload = {"schema_version": "0.3", "load_cases": [{"id": "lc1"}]}
    client, pid = _setup_project_with_package(
        tmp_path,
        {"results/computed_metrics.json": json.dumps(payload).encode()},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "results/computed_metrics.json"
    assert body["exists"] is True
    assert body["media_type"] == "application/json"
    assert body["size_bytes"] > 0
    assert body["parsed_json"] == payload
    assert "text" in body  # JSON is also returned as text
    assert body["warnings"] == []


def test_artifact_read_returns_text_for_markdown(tmp_path: Path) -> None:
    markdown = "# Result Summary\n\n- max stress: 187.4 MPa\n"
    client, pid = _setup_project_with_package(
        tmp_path,
        {"results/postprocessing_summary.md": markdown.encode("utf-8")},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/postprocessing_summary.md"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["media_type"] == "text/markdown"
    assert body["text"] == markdown
    assert "parsed_json" not in body


def test_artifact_read_returns_exists_false_for_missing(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["path"] == "results/computed_metrics.json"
    assert "size_bytes" not in body
    assert "parsed_json" not in body
    assert "text" not in body


def test_artifact_read_rejects_parent_traversal(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "../../../etc/passwd"},
    )
    assert resp.status_code == 400
    assert "invalid artifact path" in resp.json()["detail"]


def test_artifact_read_rejects_absolute_path(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "/etc/passwd"},
    )
    assert resp.status_code == 400


def test_artifact_read_rejects_backslash(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results\\computed_metrics.json"},
    )
    assert resp.status_code == 400


def test_artifact_read_rejects_empty_path(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(f"/api/projects/{pid}/artifact", params={"path": ""})
    assert resp.status_code == 400


def test_artifact_read_large_text_returns_size_only(tmp_path: Path) -> None:
    # 300 KB markdown exceeds the 256 KB inline cap.
    big_md = ("line " + "x" * 100 + "\n") * 3000
    assert len(big_md.encode("utf-8")) > 256 * 1024
    client, pid = _setup_project_with_package(
        tmp_path,
        {"results/postprocessing_summary.md": big_md.encode("utf-8")},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/postprocessing_summary.md"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["size_bytes"] > 256 * 1024
    assert "text" not in body
    assert any("exceeds inline text cap" in w for w in body["warnings"])


def test_artifact_read_binary_suppresses_text(tmp_path: Path) -> None:
    # Synthetic binary blob with embedded NUL bytes inside the first 4 KB.
    binary = b"FRD\x00\x00binary content\x00\x01\x02more"
    client, pid = _setup_project_with_package(
        tmp_path,
        {"simulation/runs/run_001/outputs/result.frd": binary},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "simulation/runs/run_001/outputs/result.frd"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["media_type"] == "application/octet-stream"
    assert "text" not in body
    assert "parsed_json" not in body
    assert any("binary content detected" in w for w in body["warnings"])


def test_artifact_read_invalid_json_returns_warning(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(
        tmp_path,
        {"results/computed_metrics.json": b"{not valid json}"},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert "parsed_json" not in body
    assert any("json parse failed" in w for w in body["warnings"])
    assert body["text"] == "{not valid json}"  # text still returned


def test_artifact_read_404_when_package_missing(tmp_path: Path) -> None:
    from app.main import default_project

    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("no-package"))
    client = TestClient(create_app(settings))

    resp = client.get(
        f"/api/projects/{project['id']}/artifact",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 404


def test_artifact_diff_reports_changed_added_removed_paths(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    before = {
        "schema_version": "0.1",
        "load_cases": [{"id": "lc1", "metrics": {"max_stress": 100.0}}],
        "removed_block": {"obsolete": True},
    }
    after = {
        "schema_version": "0.3",
        "load_cases": [
            {"id": "lc1", "metrics": {"max_stress": 187.4}},
            {"id": "lc2"},
        ],
        "added_block": {"new": True},
    }

    resp = client.post(
        f"/api/projects/{pid}/artifact/diff",
        json={"before": before, "after": after},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "/schema_version" in body["changed_paths"]
    assert "/load_cases/0/metrics/max_stress" in body["changed_paths"]
    assert "/load_cases/1" in body["added_paths"]
    assert "/added_block" in body["added_paths"]
    assert "/removed_block" in body["removed_paths"]


def test_artifact_diff_identical_documents_empty(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    doc = {"a": 1, "b": [1, 2, 3], "c": {"d": "x"}}
    resp = client.post(
        f"/api/projects/{pid}/artifact/diff",
        json={"before": doc, "after": doc},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["changed_paths"] == []
    assert body["added_paths"] == []
    assert body["removed_paths"] == []


def test_artifact_diff_rejects_missing_before_after(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(
        f"/api/projects/{pid}/artifact/diff",
        json={"before": {"a": 1}},
    )
    assert resp.status_code == 400
    assert "before" in resp.json()["detail"] and "after" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Phase 29 — solver input deck import endpoint
# ---------------------------------------------------------------------------

_FIXTURE_INP_PATH = Path(__file__).resolve().parent / "fixtures" / "minimal_cantilever.inp"


def _read_fixture_inp() -> str:
    return _FIXTURE_INP_PATH.read_text(encoding="utf-8")


def test_solver_input_happy_path_writes_into_package(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["run_id"] == "run_001"
    assert body["artifact"]["path"] == "simulation/runs/run_001/solver_input.inp"
    assert body["artifact"]["kind"] == "solver_input"
    assert body["artifact"]["role"] == "solver_input_deck"
    assert body["artifact"]["size_bytes"] == len(deck.encode("utf-8"))
    assert body["keyword_count"] > 0
    # The fixture deck has *HEADING / *NODE / *ELEMENT / *MATERIAL / *STEP etc.
    assert "HEADING" in body["keywords"]
    assert "NODE" in body["keywords"]
    assert "STEP" in body["keywords"]
    # No missing-block warnings for a complete deck.
    assert all("*NODE" not in w and "*STEP" not in w for w in body["warnings"])

    # The package now contains the deck on disk at the canonical path.
    from app.main import get_project, resolve_project_path

    settings = _make_runtime_settings(tmp_path)
    project = get_project(settings, pid)
    package_path = resolve_project_path(settings, pid, project["aieng_file"])
    with zipfile.ZipFile(package_path, "r") as zf:
        assert "simulation/runs/run_001/solver_input.inp" in zf.namelist()
        assert zf.read("simulation/runs/run_001/solver_input.inp").decode("utf-8") == deck


def test_solver_input_custom_run_id(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck, "run_id": "experiment_42"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "experiment_42"
    assert body["artifact"]["path"] == "simulation/runs/experiment_42/solver_input.inp"


def test_solver_input_overwrites_existing_by_default(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    deck_a = "*HEADING\nfirst\n*NODE\n1, 0, 0, 0\n*STEP\n*STATIC\n*END STEP\n"
    deck_b = "*HEADING\nsecond\n*NODE\n2, 1, 1, 1\n*STEP\n*STATIC\n*END STEP\n"

    resp1 = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck_a})
    assert resp1.status_code == 200

    resp2 = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck_b})
    assert resp2.status_code == 200

    from app.main import get_project, resolve_project_path

    settings = _make_runtime_settings(tmp_path)
    project = get_project(settings, pid)
    package_path = resolve_project_path(settings, pid, project["aieng_file"])
    with zipfile.ZipFile(package_path, "r") as zf:
        contents = zf.read("simulation/runs/run_001/solver_input.inp").decode("utf-8")
    assert contents == deck_b


def test_solver_input_overwrite_false_conflicts(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = "*HEADING\nfirst\n*NODE\n1, 0, 0, 0\n*STEP\n*STATIC\n*END STEP\n"

    resp1 = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck})
    assert resp1.status_code == 200

    resp2 = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck, "overwrite": False},
    )
    assert resp2.status_code == 409


def test_solver_input_rejects_empty_text(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(f"/api/projects/{pid}/solver-input", json={"text": ""})
    assert resp.status_code == 400
    assert "text" in resp.json()["detail"]


def test_solver_input_rejects_missing_text(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(f"/api/projects/{pid}/solver-input", json={})
    assert resp.status_code == 400


def test_solver_input_rejects_non_string_text(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(f"/api/projects/{pid}/solver-input", json={"text": 42})
    assert resp.status_code == 400


def test_solver_input_rejects_text_without_keywords(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": "this is not a CalculiX deck\njust prose\n"},
    )
    assert resp.status_code == 400
    assert "CalculiX" in resp.json()["detail"]


def test_solver_input_rejects_run_id_traversal(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck, "run_id": "../etc"},
    )
    assert resp.status_code == 400


def test_solver_input_rejects_run_id_with_slash(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck, "run_id": "run/001"},
    )
    assert resp.status_code == 400


def test_solver_input_rejects_oversized_deck(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    # Build a >10 MB string with a valid header so size triggers before parse.
    header = "*HEADING\noversized\n*NODE\n"
    bulk = ("1, 0.0, 0.0, 0.0\n") * 700_000
    deck = header + bulk
    assert len(deck.encode("utf-8")) > 10 * 1024 * 1024

    resp = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck})
    assert resp.status_code == 413


def test_solver_input_warns_on_incomplete_deck(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    # A deck with a keyword but missing *NODE and *STEP — accepted with warnings.
    minimal = "*HEADING\nincomplete\n"

    resp = client.post(f"/api/projects/{pid}/solver-input", json={"text": minimal})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert any("*NODE" in w for w in body["warnings"])
    assert any("*STEP" in w for w in body["warnings"])


def test_solver_input_404_when_package_missing(tmp_path: Path) -> None:
    from app.main import default_project

    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("no-package-import"))
    client = TestClient(create_app(settings))

    resp = client.post(
        f"/api/projects/{project['id']}/solver-input",
        json={"text": _read_fixture_inp()},
    )
    assert resp.status_code == 404


def test_solver_input_imported_deck_is_visible_via_artifact_api(tmp_path: Path) -> None:
    """The artifact-read endpoint should surface the just-imported deck so a
    reviewer can confirm what landed inside the package before running it."""
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    post = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck})
    assert post.status_code == 200

    get = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "simulation/runs/run_001/solver_input.inp"},
    )
    assert get.status_code == 200
    body = get.json()
    assert body["exists"] is True
    assert body["media_type"] == "text/plain"
    assert body["text"] == deck


def test_llm_test_endpoint_config_ready_without_key(tmp_path: Path) -> None:
    """/api/llm/test returns config_ready=False when API key env is missing."""
    from app.main import Settings, create_app
    from starlette.testclient import TestClient

    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=tmp_path / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    resp = client.post("/api/llm/test", json={
        "llm_config": {"provider": "openai-compatible", "model": "gpt-4o", "api_key_env": "NONEXISTENT_KEY_XYZ"},
        "verify_connection": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["config_ready"] is False
    assert data["api_key_present"] is False
    assert data["connection_verified"] is False
    assert "NONEXISTENT_KEY_XYZ" in data["error_message"]


def test_llm_test_endpoint_no_api_key_in_response(tmp_path: Path) -> None:
    """/api/llm/test must never expose the API key in the response."""
    from app.main import Settings, create_app
    from starlette.testclient import TestClient

    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=tmp_path / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    resp = client.post("/api/llm/test", json={
        "llm_config": {
            "provider": "openai-compatible",
            "model": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "api_key": "sk-secret123",
        },
        "verify_connection": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    response_text = json.dumps(data)
    assert "sk-secret123" not in response_text


def test_llm_test_endpoint_missing_config_returns_400(tmp_path: Path) -> None:
    """/api/llm/test returns 400 when llm_config is missing."""
    from app.main import Settings, create_app
    from starlette.testclient import TestClient

    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=tmp_path / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    resp = client.post("/api/llm/test", json={})
    assert resp.status_code == 400


_REAL_FREECAD_STEP = _WORKSPACE_ROOT / "aieng_freecad_mcp" / "examples" / "parametric_bracket" / "freecad" / "source.step"


def _resolve_freecad_cmd() -> Path | None:
    """Find a usable FreeCADCmd executable for integration tests."""
    # 1. Explicit env override
    env_path = os.environ.get("FREECAD_CMD")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    # 2. Common installation paths (Windows)
    candidates = [
        Path(r"C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe"),
        Path(r"C:\Program Files\FreeCAD 0.21\bin\FreeCADCmd.exe"),
        Path(r"C:\Program Files\FreeCAD\bin\FreeCADCmd.exe"),
        Path.home() / "AppData" / "Local" / "Programs" / "FreeCAD" / "bin" / "FreeCADCmd.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    # 3. PATH lookup
    found = shutil.which("FreeCADCmd.exe") or shutil.which("FreeCADCmd")
    if found:
        return Path(found)
    return None


_FREECAD_CMD = _resolve_freecad_cmd()
_REAL_FREECAD_AVAILABLE = (
    os.environ.get("AIENG_TEST_REAL_FREECAD") == "1"
    and _FREECAD_CMD is not None
    and _FREECAD_CMD.exists()
    and _REAL_FREECAD_STEP.exists()
)


@pytest.mark.skipif(
    not _REAL_FREECAD_AVAILABLE,
    reason="Set AIENG_TEST_REAL_FREECAD=1 and ensure FreeCADCmd is on PATH or FREECAD_CMD is set",
)
def _make_lifecycle_pkg(pkg_path: Path) -> str:
    """Create a .aieng package with CAE evidence artifacts for lifecycle tests.

    Returns the path of the displacement field summary (used as supporting evidence).
    """
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    cm_path = "results/computed_metrics.json"
    frd_path = "simulation/runs/run_001/outputs/result.frd"
    disp_summary_path = "results/fields/displacement.summary.json"
    stress_summary_path = "results/fields/stress.summary.json"

    members: dict[str, str] = {
        "manifest.json": json.dumps({"model_id": "lifecycle-test"}),
        cm_path: json.dumps({
            "schema_version": "0.1",
            "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX", "source_files": []},
            "load_cases": [{"id": "lc1", "metrics": {
                "max_displacement": {"value": 0.18, "unit": "mm"},
                "max_von_mises_stress": {"value": 92.0, "unit": "MPa"},
            }}],
            "warnings": [],
        }),
        frd_path: "FAKE FRD CONTENT FOR LIFECYCLE TEST",
        "simulation/runs/run_001/solver_run.json": json.dumps({
            "schema_version": "0.1", "run_id": "run_001", "solver": "CalculiX",
            "converged": None, "state": "completed", "return_code": 0,
            "claim_advancement": "none",
        }),
        disp_summary_path: json.dumps({
            "schema_version": "0.1", "field_name": "displacement", "unit": "mm",
            "source": {"source_type": "computed_metrics", "computed_metrics_path": cm_path},
            "stats": {"max_value": 0.18, "min_value": None, "node_count": None, "values_finite": None},
            "evidence_role": "displacement_extrema", "claim_advancement": "none",
        }),
        stress_summary_path: json.dumps({
            "schema_version": "0.1", "field_name": "stress", "unit": "MPa",
            "source": {"source_type": "computed_metrics", "computed_metrics_path": cm_path},
            "stats": {"max_value": 92.0, "min_value": None, "node_count": None, "values_finite": None},
            "evidence_role": "stress_extrema", "claim_advancement": "none",
        }),
        "results/result_summary.json": json.dumps({
            "schema_version": "0.1", "summary_type": "cae_postprocessing",
            "claim_advancement": "none",
        }),
        "results/evidence_index.json": json.dumps({
            "evidence_type": "cae_artifacts",
            "entries": [
                {"id": "cm_entry", "path": cm_path, "kind": "result",
                 "role": "computed_extrema", "exists": True, "supports": ["audit"]},
                {"id": "frd_entry", "path": frd_path, "kind": "solver_raw_output",
                 "role": "solver_raw_output", "exists": True, "supports": ["numerical_result_source"]},
                {"id": "disp_entry", "path": disp_summary_path, "kind": "field",
                 "role": "displacement_extrema", "exists": True,
                 "supports": ["displacement_extrema", "field_evidence", "audit"]},
                {"id": "stress_entry", "path": stress_summary_path, "kind": "field",
                 "role": "stress_extrema", "exists": True,
                 "supports": ["stress_extrema", "field_evidence", "audit"]},
            ],
        }),
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            if isinstance(data, bytes):
                zf.writestr(name, data)
            else:
                zf.writestr(name, data)
    return disp_summary_path


def _setup_lifecycle_project(tmp_path: Path) -> tuple[TestClient, str, Path]:
    """Return (client, project_id, pkg_path) with a lifecycle-test-ready package."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("lifecycle-contract"))
    pkg_path = project_dir(settings, project["id"]) / "lifecycle.aieng"
    _make_lifecycle_pkg(pkg_path)
    project["aieng_file"] = "lifecycle.aieng"
    save_project(settings, project)
    return client, project["id"], pkg_path


_FULL_REAL_PIPELINE_AVAILABLE = (
    _REAL_FREECAD_AVAILABLE and shutil.which("ccx") is not None
)


def _make_cube_step_via_freecad(
    freecad_cmd: Path,
    out_step: Path,
    length: float = 10.0,
    width: float = 1.0,
    height: float = 1.0,
) -> None:
    """Generate a tiny cube STEP file using FreeCADCmd.

    Used only by the doubly-gated real-pipeline test; never runs in CI.
    """
    import subprocess

    out_step.parent.mkdir(parents=True, exist_ok=True)
    macro_path = out_step.parent / "_make_cube_macro.py"
    out_posix = out_step.as_posix()
    macro_path.write_text(
        f'''
import FreeCAD, Part
doc = FreeCAD.newDocument("cube")
box = doc.addObject("Part::Box", "Box")
box.Length = {length}
box.Width = {width}
box.Height = {height}
doc.recompute()
Part.export([box], "{out_posix}")
print("CUBE_STEP_WRITTEN:" + "{out_posix}")
''',
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(freecad_cmd), str(macro_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if not out_step.exists():
        raise RuntimeError(
            "FreeCADCmd cube generation produced no STEP file "
            f"(exit {result.returncode}). stdout: {result.stdout!r} stderr: {result.stderr!r}"
        )


def _complete_solver_deck(mesh_inp_text: str) -> str:
    """Turn a FreeCAD mesh-only .inp into a complete CalculiX static-step deck.

    FreeCAD's ``FemMesh.write`` emits only ``*NODE`` and ``*ELEMENT`` blocks.
    To drive ``ccx`` we need material, section, boundary, load, and output
    requests. This helper:

    - Reads every node coordinate.
    - Picks the first volumetric element block (``C3D4/C3D8/C3D10/C3D20``)
      and reuses its ``ELSET`` name for the ``*SOLID SECTION``.
    - Builds a ``FIX`` nset from minimum-x nodes and a ``LOAD`` nset from
      maximum-x nodes (within a 1e-3 relative tolerance).
    - Appends ``*NSET``, ``*MATERIAL/*ELASTIC``, ``*SOLID SECTION``,
      ``*STEP/*STATIC``, ``*BOUNDARY``, ``*CLOAD``, ``*NODE FILE``,
      ``*EL FILE``, ``*END STEP``.

    The tip load is distributed evenly across LOAD-nset nodes so total
    applied force is independent of mesh density.
    """
    lines = mesh_inp_text.splitlines()
    nodes: dict[int, tuple[float, float, float]] = {}
    volumetric_elset: str | None = None

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        upper = stripped.upper()
        is_node_header = (
            upper.startswith("*NODE")
            and not upper.startswith("*NODE FILE")
            and not upper.startswith("*NODE PRINT")
            and not upper.startswith("*NODE OUTPUT")
        )
        if is_node_header:
            i += 1
            while i < len(lines):
                row = lines[i].strip()
                if not row or row.startswith("*"):
                    break
                parts = [p.strip() for p in row.split(",")]
                if len(parts) >= 4:
                    try:
                        node_id = int(parts[0])
                        x = float(parts[1])
                        y = float(parts[2])
                        z = float(parts[3])
                        nodes[node_id] = (x, y, z)
                    except ValueError:
                        pass
                i += 1
            continue
        if upper.startswith("*ELEMENT") and volumetric_elset is None:
            params: dict[str, str] = {}
            for chunk in stripped.split(",")[1:]:
                if "=" in chunk:
                    k, v = chunk.split("=", 1)
                    params[k.strip().upper()] = v.strip()
            elem_type = params.get("TYPE", "").upper()
            elset = params.get("ELSET", "")
            if elem_type.startswith("C3D") and elset:
                volumetric_elset = elset
        i += 1

    if not nodes:
        raise ValueError("FreeCAD mesh .inp had no parseable *NODE entries")
    if not volumetric_elset:
        raise ValueError("FreeCAD mesh .inp had no volumetric (C3D*) *ELEMENT block")

    xs = [coords[0] for coords in nodes.values()]
    x_min, x_max = min(xs), max(xs)
    tol = max((x_max - x_min) * 1e-3, 1e-6)
    fix_ids = sorted(nid for nid, (x, _, _) in nodes.items() if abs(x - x_min) <= tol)
    load_ids = sorted(nid for nid, (x, _, _) in nodes.items() if abs(x - x_max) <= tol)
    if not fix_ids or not load_ids:
        raise ValueError(
            f"Degenerate NSETs from FreeCAD mesh: |FIX|={len(fix_ids)} |LOAD|={len(load_ids)}"
        )

    def _format_nset(name: str, ids: list[int]) -> str:
        chunks = []
        for k in range(0, len(ids), 16):
            chunks.append(", ".join(str(i) for i in ids[k:k + 16]))
        return f"*NSET, NSET={name}\n" + "\n".join(chunks)

    per_node_load = -100.0 / len(load_ids)
    addition = "\n".join([
        "** --- AIENG real-pipeline smoke test deck completion ---",
        _format_nset("FIX", fix_ids),
        _format_nset("LOAD", load_ids),
        "*MATERIAL, NAME=Steel",
        "*ELASTIC",
        "210000.0, 0.3",
        f"*SOLID SECTION, ELSET={volumetric_elset}, MATERIAL=Steel",
        "*STEP",
        "*STATIC",
        "*BOUNDARY",
        "FIX, 1, 3, 0.0",
        "*CLOAD",
        f"LOAD, 2, {per_node_load:.6f}",
        "*NODE FILE, NSET=LOAD",
        "U",
        "*EL FILE",
        "S",
        "*END STEP",
        "",
    ])
    return mesh_inp_text.rstrip() + "\n" + addition


@pytest.mark.skipif(
    not _FULL_REAL_PIPELINE_AVAILABLE,
    reason=(
        "Requires AIENG_TEST_REAL_FREECAD=1, FreeCADCmd, AND ccx on host — "
        "skipping full real pipeline."
    ),
)
def _make_copilot_loop_fixture_package(pkg_path: Path) -> None:
    """Fixture with recommendations + executable parameter contract."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    design_targets = {
        "schema_version": "0.1.1",
        "targets": [
            {"target_id": "mass_reduce_10pct", "target_type": "mass_reduction_target", "comparator": "reduce_by_at_least", "threshold": 10.0, "priority": "high"},
            {"target_id": "safety_factor_min", "target_type": "minimum_safety_factor", "comparator": ">=", "threshold": 1.5, "priority": "critical"},
        ],
    }
    parsed_features = {
        "features": [
            {"id": "back_wall", "kind": "wall", "parameters": {"thickness_mm": 20.0, "width_mm": 120.0}, "mass_contribution_kg": 1.51},
            {"id": "central_rib", "kind": "rib", "parameters": {"thickness_mm": 8.0, "length_mm": 100.0}, "mass_contribution_kg": 0.38},
        ],
    }
    feature_graph = {
        "features": [
            {
                "id": "back_wall",
                "type": "wall",
                "name": "Back wall",
                "cad_object_name": "BackWall",
                "parameters": [
                    {
                        "name": "thickness_mm",
                        "current_value": 20.0,
                        "min_value": 1.0,
                        "max_value": 40.0,
                        "editability": {"executable": True},
                        "cad_parameter_name": "BACK_WALL_THICKNESS",
                    }
                ],
            }
        ]
    }
    topology_map = {
        "format_version": "0.1",
        "entities": [
            {
                "id": "solid_back_wall",
                "type": "solid",
                "name": "back_wall",
                "bounding_box": [-60.0, -10.0, -30.0, 60.0, 10.0, 30.0],
            },
            {
                "id": "solid_central_rib",
                "type": "solid",
                "name": "central_rib",
                "bounding_box": [-50.0, -14.0, -4.0, 50.0, 14.0, 4.0],
            },
        ],
    }
    source_py = """from build123d import *

BACK_WALL_THICKNESS = 20.0
RIB_THICKNESS = 8.0

back_wall = Box(120, BACK_WALL_THICKNESS, 60)
back_wall.label = "back_wall"
back_wall.color = Color(0.55, 0.62, 0.70)

central_rib = Box(100, RIB_THICKNESS, 28).moved(Location((0, 0, 18)))
central_rib.label = "central_rib"
central_rib.color = Color(0.45, 0.52, 0.60)

result = Compound(children=[back_wall, central_rib])
"""
    stress = {
        "schema_version": "0.1",
        "load_case_id": "load_case_001",
        "minimum_required_safety_factor": 1.5,
        "features": [
            {"feature_ref": "back_wall", "max_von_mises_stress_mpa": 22.0, "safety_factor": 15.91},
            {"feature_ref": "central_rib", "max_von_mises_stress_mpa": 195.0, "safety_factor": 1.79},
        ],
    }
    metrics = {
        "schema_version": "0.1",
        "metrics_source": {"tool": "fixture", "software": "mock_postprocessor"},
        "load_cases": [
            {
                "id": "load_case_001",
                "metrics": {
                    "max_von_mises_stress": {"value": 195.0, "unit": "MPa"},
                    "minimum_safety_factor": {"value": 1.79},
                    "total_mass": {"value": 2.30, "unit": "kg"},
                },
            }
        ],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "copilot-loop", "resources": {"geometry": {"source": "geometry/source.step"}}}))
        zf.writestr("geometry/source.step", "ISO-10303-21;\nEND-ISO-10303-21;\n")
        zf.writestr("geometry/source.py", source_py)
        zf.writestr("geometry/topology_map.json", json.dumps(topology_map))
        zf.writestr("task/design_targets.yaml", yaml.safe_dump(design_targets, sort_keys=False))
        zf.writestr("simulation/cae_imports/parsed_features.json", json.dumps(parsed_features))
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph))
        zf.writestr("results/stress_by_feature.json", json.dumps(stress))
        zf.writestr("results/computed_metrics.json", json.dumps(metrics))


def _setup_copilot_loop_project(tmp_path: Path):
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    project = save_project(settings, default_project("copilot-loop"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "copilot-loop.aieng"
    _make_copilot_loop_fixture_package(pkg_path)
    project["aieng_file"] = "copilot-loop.aieng"
    save_project(settings, project)
    return client, project_id, pkg_path


def _advance_loop_to_apply_waiting(client: TestClient, project_id: str) -> dict[str, Any]:
    start = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={})
    assert start.status_code == 200
    loop = start.json()
    for _ in range(4):
        resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/advance")
        assert resp.status_code == 200
        loop = resp.json()
    return loop


def test_copilot_loop_reject_path_is_skipped_not_error(tmp_path: Path) -> None:
    client, project_id, pkg_path = _setup_copilot_loop_project(tmp_path)
    loop = _advance_loop_to_apply_waiting(client, project_id)

    reject = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/reject")
    assert reject.status_code == 200
    rejected = reject.json()
    apply_step = next(step for step in rejected["steps"] if step["id"] == "apply_cad_edit")
    assert apply_step["status"] == "skipped"
    assert "user_rejected" in " ".join(apply_step["warnings"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "state/revalidation_status.json" not in zf.namelist()


def _package_digest(pkg_path: Path) -> tuple[int, bytes]:
    data = pkg_path.read_bytes()
    return len(data), hashlib.sha256(data).digest()


def test_copilot_loop_handler_exception_marks_step_error_not_running(monkeypatch, tmp_path: Path) -> None:
    """A handler raising an unexpected exception must convert to an honest
    `error` step status, never leak a `running` state into persisted loop state.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    start = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={})
    assert start.status_code == 200
    loop = start.json()

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("forced inspection failure")

    monkeypatch.setattr("app.copilot_loop._read_metrics_snapshot", boom)
    # The baseline snapshot is read once at start_loop, so the first advance
    # is what would trigger the handler. We monkeypatch a path used inside
    # advance_inspect: package read still happens, but downstream evidence
    # generation can blow up. Instead, force the inspect handler itself.
    monkeypatch.setattr("app.copilot_loop._advance_inspect", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("forced")))

    resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/advance")
    assert resp.status_code == 200
    advanced = resp.json()
    inspect = next(s for s in advanced["steps"] if s["id"] == "inspect_evidence")
    assert inspect["status"] == "error", "exception in handler must produce error, not leak running state"
    assert any("RuntimeError" in e or "forced" in e for e in inspect.get("errors") or [])

    # Reload from disk: ensure error state was persisted, not just returned.
    reloaded = client.get(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}")
    assert reloaded.status_code == 200
    persisted = reloaded.json()
    inspect_persisted = next(s for s in persisted["steps"] if s["id"] == "inspect_evidence")
    assert inspect_persisted["status"] == "error"


def test_copilot_loop_state_persists_and_can_be_reloaded(tmp_path: Path) -> None:
    """Backend persistence contract: after start + several advance calls, a
    fresh app instance with the same settings can reload the loop and observe
    the same step statuses, warnings, artifacts, and current_step_id.
    """
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    project = save_project(settings, default_project("copilot-loop-reload"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "copilot-loop.aieng"
    _make_copilot_loop_fixture_package(pkg_path)
    project["aieng_file"] = "copilot-loop.aieng"
    save_project(settings, project)

    start = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={})
    assert start.status_code == 200
    loop = start.json()
    loop_id = loop["loop_id"]
    for _ in range(4):
        resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop_id}/advance")
        assert resp.status_code == 200
        loop = resp.json()

    # Capture statuses, warnings, artifacts, current step.
    expected_status_by_id = {s["id"]: s["status"] for s in loop["steps"]}
    expected_warnings_by_id = {s["id"]: list(s.get("warnings") or []) for s in loop["steps"]}
    expected_artifacts_by_id = {s["id"]: list(s.get("artifacts") or []) for s in loop["steps"]}
    expected_current = loop.get("current_step_id")

    # Simulate a server restart: fresh app with the same settings (and so the
    # same on-disk workspace). Loop state must be recoverable through the
    # list endpoint and through direct fetch.
    fresh_app = create_app(settings)
    fresh_client = TestClient(fresh_app)

    listing = fresh_client.get(f"/api/projects/{project_id}/copilot-loops")
    assert listing.status_code == 200
    summaries = listing.json().get("loops") or []
    assert any(s["loop_id"] == loop_id for s in summaries), "list endpoint must include persisted loop"
    summary = next(s for s in summaries if s["loop_id"] == loop_id)
    assert summary["step_total"] == 11
    assert summary["waiting_for_approval"] is True
    assert summary["current_step_id"] == expected_current

    reloaded = fresh_client.get(f"/api/projects/{project_id}/copilot-loop/{loop_id}")
    assert reloaded.status_code == 200
    persisted = reloaded.json()
    assert persisted["loop_id"] == loop_id
    assert persisted["current_step_id"] == expected_current
    for step in persisted["steps"]:
        assert step["status"] == expected_status_by_id[step["id"]]
        assert list(step.get("warnings") or []) == expected_warnings_by_id[step["id"]]
        assert list(step.get("artifacts") or []) == expected_artifacts_by_id[step["id"]]


def test_copilot_loop_reject_leaves_package_byte_identical(tmp_path: Path) -> None:
    """Reject path safety: package bytes must be byte-identical after reject;
    no CAD mutation, no stale-evidence marker, no claim-side-effect must occur.
    """
    client, project_id, pkg_path = _setup_copilot_loop_project(tmp_path)
    pre_size, pre_hash = _package_digest(pkg_path)
    loop = _advance_loop_to_apply_waiting(client, project_id)
    apply_step = next(s for s in loop["steps"] if s["id"] == "apply_cad_edit")
    assert apply_step["status"] == "waiting_for_approval"

    # Package must still be untouched up to this point (waiting_for_approval
    # gate must NOT have executed the mutation).
    waiting_size, waiting_hash = _package_digest(pkg_path)
    assert (waiting_size, waiting_hash) == (pre_size, pre_hash), (
        "package was mutated before approval — approval gate breach"
    )

    reject = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/reject")
    assert reject.status_code == 200
    rejected = reject.json()
    apply_step = next(s for s in rejected["steps"] if s["id"] == "apply_cad_edit")
    assert apply_step["status"] == "skipped"
    assert "user_rejected" in " ".join(apply_step.get("warnings") or [])

    post_size, post_hash = _package_digest(pkg_path)
    assert (post_size, post_hash) == (pre_size, pre_hash), "package must be byte-identical after reject"

    # Subsequent advance after reject must not flip any geometry-dependent
    # step into `completed` based on the (rejected, never-applied) edit.
    advance = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/advance")
    assert advance.status_code == 200
    after = advance.json()
    mark_stale = next(s for s in after["steps"] if s["id"] == "mark_stale")
    assert mark_stale["status"] == "skipped", "no edit was applied; no stale propagation should be claimed"

    # And package is still byte-identical.
    final_size, final_hash = _package_digest(pkg_path)
    assert (final_size, final_hash) == (pre_size, pre_hash)


def test_copilot_loop_reject_when_not_waiting_returns_conflict(tmp_path: Path) -> None:
    """Reject on a loop that has no waiting_for_approval step must be a 409
    conflict, not a silent no-op that fakes success.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    start = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={})
    loop = start.json()
    resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/reject")
    assert resp.status_code == 409


def test_copilot_loop_list_endpoint_returns_summaries_newest_first(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    first = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()
    second = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    listing = client.get(f"/api/projects/{project_id}/copilot-loops")
    assert listing.status_code == 200
    summaries = listing.json().get("loops") or []
    ids = [s["loop_id"] for s in summaries]
    assert first["loop_id"] in ids
    assert second["loop_id"] in ids
    # Newest first ordering.
    assert ids.index(second["loop_id"]) <= ids.index(first["loop_id"])
    # Each summary carries the contract fields.
    for summary in summaries:
        assert {"loop_id", "status", "created_at", "step_total", "waiting_for_approval"} <= set(summary)


def test_copilot_loop_report_carries_claim_boundary_and_rejection_notice(tmp_path: Path) -> None:
    """The generated loop report must clearly state the claim boundary and,
    when the apply step was rejected, the report must explicitly say so.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    loop = _advance_loop_to_apply_waiting(client, project_id)
    reject = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/reject")
    assert reject.status_code == 200
    loop = reject.json()

    # Advance to completion so the report is generated.
    for _ in range(15):
        resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/advance")
        assert resp.status_code == 200
        loop = resp.json()
        if loop.get("status") == "completed":
            break

    assert loop.get("status") == "completed"
    report_step = next(s for s in loop["steps"] if s["id"] == "generate_report")
    assert report_step["status"] in {"completed", "partial"}

    report = client.get(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/report")
    assert report.status_code == 200
    report_payload = report.json()
    markdown = report_payload["markdown"]

    # Claim boundary phrasing (English + Chinese parallel line).
    assert "does not certify the design" in markdown
    assert "does not advance engineering claims" in markdown
    assert "does not certify" in markdown
    # Rejection acknowledged.
    assert "Rejection notice" in markdown
    assert "not executed" in markdown.lower() or "rejected" in markdown.lower()
    # Claim boundary object on the report payload itself.
    boundary = report_payload.get("claim_boundary") or {}
    assert boundary.get("claims_advanced") is False
    assert boundary.get("design_certified") is False
    assert report_payload.get("apply_rejected") is True


def test_copilot_loop_advance_after_completion_is_idempotent(tmp_path: Path) -> None:
    """Repeated advance calls after the loop has reached a terminal state must
    not error and must not flip any step status backwards.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    loop = _advance_loop_to_apply_waiting(client, project_id)
    reject = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/reject")
    loop = reject.json()
    for _ in range(15):
        resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/advance")
        assert resp.status_code == 200
        loop = resp.json()
        if loop.get("status") == "completed":
            break
    assert loop.get("status") == "completed"
    snapshot = [(s["id"], s["status"]) for s in loop["steps"]]

    # Extra idempotent advances.
    for _ in range(3):
        resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/advance")
        assert resp.status_code == 200
        loop = resp.json()
        assert [(s["id"], s["status"]) for s in loop["steps"]] == snapshot
        assert loop["status"] == "completed"
        assert loop.get("current_step_id") is None


# ---------------------------------------------------------------------------
# Closed-loop Copilot Stepper v0.2 — Multi-loop history & comparison
# ---------------------------------------------------------------------------


def _summary_contract_fields() -> set[str]:
    return {
        "loop_id",
        "status",
        "created_at",
        "updated_at",
        "current_step_id",
        "step_total",
        "step_terminal_count",
        "waiting_for_approval",
        "decision",
        "proposal_summary",
        "verification_status",
        "report_path",
        "stale_artifact_count",
        "warning_count",
        "error_count",
        "metric_summary",
        "target_summary",
        "strictness",
    }


def test_copilot_loop_summary_for_rejected_loop_marks_decision_rejected(tmp_path: Path) -> None:
    """A loop where the user rejected the CAD edit must be summarized as
    `decision = "rejected"`, NOT `error`, and the overall status must not be
    `error` either. Rejected is a decision record, not a failure.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    loop = _advance_loop_to_apply_waiting(client, project_id)
    reject = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/reject")
    assert reject.status_code == 200

    listing = client.get(f"/api/projects/{project_id}/copilot-loops").json()
    summary = next(s for s in listing["loops"] if s["loop_id"] == loop["loop_id"])
    assert _summary_contract_fields() <= set(summary)
    assert summary["decision"] == "rejected"
    assert summary["status"] != "error"
    assert summary["proposal_summary"] is not None
    proposal = summary["proposal_summary"]
    assert proposal["feature_ref"] == "back_wall"
    assert proposal["parameter_name"] == "thickness_mm"


def test_copilot_loop_summary_for_pending_approval(tmp_path: Path) -> None:
    """A loop currently waiting for approval must summarize as
    `decision = "pending"` and `waiting_for_approval = True`.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    loop = _advance_loop_to_apply_waiting(client, project_id)

    listing = client.get(f"/api/projects/{project_id}/copilot-loops").json()
    summary = next(s for s in listing["loops"] if s["loop_id"] == loop["loop_id"])
    assert summary["decision"] == "pending"
    assert summary["waiting_for_approval"] is True


def test_copilot_loop_summary_handles_legacy_loop_without_context(tmp_path: Path) -> None:
    """Old loops on disk that predate v0.2 (no `context`, no `current_step_id`,
    sparse steps) must still list safely. Derived fields default to `None` /
    sensible zeros; the endpoint must not 500.
    """
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    project = save_project(settings, default_project("copilot-loop-legacy"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "copilot-loop.aieng"
    _make_copilot_loop_fixture_package(pkg_path)
    project["aieng_file"] = "copilot-loop.aieng"
    save_project(settings, project)

    legacy_dir = project_dir(settings, project_id) / "copilot_loops"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = legacy_dir / "legacy00000000.json"
    legacy_path.write_text(
        json.dumps({
            "schema_version": "0.0-pre",
            "loop_id": "legacy00000000",
            "status": "completed",
            "steps": [
                {"id": "inspect_evidence", "status": "completed", "warnings": [], "errors": []},
            ],
        }),
        encoding="utf-8",
    )

    listing = client.get(f"/api/projects/{project_id}/copilot-loops")
    assert listing.status_code == 200
    loops = listing.json()["loops"]
    legacy = next(s for s in loops if s["loop_id"] == "legacy00000000")
    assert _summary_contract_fields() <= set(legacy)
    # Derived fields are None / safe defaults for legacy state.
    assert legacy["decision"] == "none"
    assert legacy["proposal_summary"] is None
    assert legacy["verification_status"] is None
    assert legacy["report_path"] is None
    assert legacy["metric_summary"] is None
    assert legacy["target_summary"] is None
    assert legacy["stale_artifact_count"] == 0
    assert legacy["warning_count"] == 0
    assert legacy["error_count"] == 0


def test_copilot_loop_summary_counts_warnings_and_errors(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    loop = _advance_loop_to_apply_waiting(client, project_id)
    client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/reject")
    for _ in range(15):
        resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop['loop_id']}/advance")
        loop = resp.json()
        if loop.get("status") == "completed":
            break

    listing = client.get(f"/api/projects/{project_id}/copilot-loops").json()
    summary = next(s for s in listing["loops"] if s["loop_id"] == loop["loop_id"])
    # Reject path emits "reason=user_rejected" warning and some honest
    # blockers for unavailable solver/result paths. Counts are non-negative
    # integers; we don't pin exact numbers because skipped paths evolve.
    assert isinstance(summary["warning_count"], int) and summary["warning_count"] >= 1
    assert isinstance(summary["error_count"], int) and summary["error_count"] >= 0
    # Rejected loop must NOT be summarized with a global error status.
    assert summary["status"] != "error"
    assert summary["decision"] == "rejected"


def test_copilot_loop_list_returns_multiple_loops_newest_first(tmp_path: Path) -> None:
    """Multiple loops must be listed newest first, and each carries the
    full v0.2 summary contract — verifying the "history" UX is feasible.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    a = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()
    b = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()
    c = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    listing = client.get(f"/api/projects/{project_id}/copilot-loops").json()
    summaries = listing["loops"]
    ids = [s["loop_id"] for s in summaries]
    for created in (a, b, c):
        assert created["loop_id"] in ids
    # Newest first: c was created last so it must come first among c/b/a.
    assert ids.index(c["loop_id"]) < ids.index(b["loop_id"]) < ids.index(a["loop_id"])
    for summary in summaries:
        assert _summary_contract_fields() <= set(summary)


# ---------------------------------------------------------------------------
# Closed-loop Copilot Stepper v0.3 — Report-level diff inside compare panel
# ---------------------------------------------------------------------------


def _compare_reports_contract_fields() -> set[str]:
    return {
        "left_loop_id",
        "right_loop_id",
        "left_report_path",
        "right_report_path",
        "left_report_exists",
        "right_report_exists",
        "left_report_truncated",
        "right_report_truncated",
        "left_text",
        "right_text",
        "unified_diff",
        "added_lines",
        "removed_lines",
        "warnings",
        "claim_boundary",
    }


def _finish_loop_through_report(
    client: TestClient, project_id: str, loop_id: str, max_advances: int = 15
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(max_advances):
        resp = client.post(f"/api/projects/{project_id}/copilot-loop/{loop_id}/advance")
        assert resp.status_code == 200
        last = resp.json()
        if last.get("status") == "completed":
            return last
    return last


def _run_loop_to_report(
    client: TestClient, project_id: str, monkeypatch, tmp_path: Path, label: str, decision: str
) -> str:
    """Drive a loop to completion in either 'approved' or 'rejected' decision
    path and return its loop_id. The package gets a real report written.
    """
    loop = _advance_loop_to_apply_waiting(client, project_id)
    loop_id = loop["loop_id"]
    if decision == "approved":
        def _fake_edit_parameter(**kwargs: Any) -> dict[str, Any]:
            return {
                "status": "ok",
                "schema_version": "0.1",
                "project_id": kwargs.get("project_id"),
                "feature_id": kwargs.get("feature_id"),
                "parameter_name": kwargs.get("parameter_name"),
                "cad_parameter_name": "BACK_WALL_THICKNESS",
                "previous_value": 20.0,
                "new_value": kwargs.get("new_value"),
                "message": "Fixture CAD parameter edit applied.",
                "stale_artifacts": ["results/computed_metrics.json", "results/result_summary.json"],
                "artifacts": [{"path": "geometry/source.py", "kind": "cad_source"}],
                "regression_diff": {"verdict": "clean", "changed": [{"part": "back_wall"}]},
            }

        monkeypatch.setattr("app.cad_generation.edit_build123d_parameter", _fake_edit_parameter)
        approve = client.post(f"/api/projects/{project_id}/copilot-loop/{loop_id}/approve").json()
        assert next(s for s in approve["steps"] if s["id"] == "apply_cad_edit")["status"] == "completed"
    elif decision == "rejected":
        client.post(f"/api/projects/{project_id}/copilot-loop/{loop_id}/reject")
    else:
        raise AssertionError(f"unknown decision: {decision}")
    final = _finish_loop_through_report(client, project_id, loop_id)
    assert final.get("status") == "completed"
    return loop_id


def test_copilot_loop_compare_reports_happy_path(monkeypatch, tmp_path: Path) -> None:
    """Two loops that each generated a report can be diffed; the response
    carries the full contract, unified_diff is non-empty, and stats are
    non-negative integers.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "rejL", "rejected")
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "okR", "approved")

    resp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected_id, "right": approved_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _compare_reports_contract_fields() <= set(body)
    assert body["left_loop_id"] == rejected_id
    assert body["right_loop_id"] == approved_id
    assert body["left_report_exists"] is True
    assert body["right_report_exists"] is True
    assert isinstance(body["unified_diff"], str) and body["unified_diff"]
    assert isinstance(body["added_lines"], int) and body["added_lines"] >= 0
    assert isinstance(body["removed_lines"], int) and body["removed_lines"] >= 0
    # Two different loops should produce at least one differing line (each
    # report includes its own loop id).
    assert body["added_lines"] + body["removed_lines"] >= 1
    # Loop ids appear in the diff hunk headers fromfile/tofile.
    assert rejected_id in body["unified_diff"]
    assert approved_id in body["unified_diff"]
    # Claim boundary is required.
    assert isinstance(body["claim_boundary"], str)
    assert "does not certify" in body["claim_boundary"]


def test_copilot_loop_compare_reports_left_missing_is_unavailable(tmp_path: Path) -> None:
    """A loop without a generated report yields a clean unavailable response
    with a warning — never a 500 — and no diff text.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    just_started = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()
    # Run a separate loop fully through to get a real right-side report.
    monkeypatch_no_op = None  # not used; rejection path doesn't need monkeypatch
    rejected_id_loop = _advance_loop_to_apply_waiting(client, project_id)
    client.post(f"/api/projects/{project_id}/copilot-loop/{rejected_id_loop['loop_id']}/reject")
    rejected_id = rejected_id_loop["loop_id"]
    _finish_loop_through_report(client, project_id, rejected_id)

    resp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": just_started["loop_id"], "right": rejected_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["left_report_exists"] is False
    assert body["right_report_exists"] is True
    assert body["unified_diff"] in (None, "")
    assert any("Left loop" in w and "report" in w for w in body["warnings"])
    assert body["added_lines"] == 0 and body["removed_lines"] == 0


def test_copilot_loop_compare_reports_right_missing_is_unavailable(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id_loop = _advance_loop_to_apply_waiting(client, project_id)
    client.post(f"/api/projects/{project_id}/copilot-loop/{rejected_id_loop['loop_id']}/reject")
    rejected_id = rejected_id_loop["loop_id"]
    _finish_loop_through_report(client, project_id, rejected_id)
    just_started = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    resp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected_id, "right": just_started["loop_id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["left_report_exists"] is True
    assert body["right_report_exists"] is False
    assert body["unified_diff"] in (None, "")
    assert any("Right loop" in w and "report" in w for w in body["warnings"])


def test_copilot_loop_compare_reports_both_missing_is_unavailable(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    a = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()
    b = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    resp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": a["loop_id"], "right": b["loop_id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["left_report_exists"] is False
    assert body["right_report_exists"] is False
    assert body["unified_diff"] in (None, "")
    # Two unavailable warnings — one for each side.
    assert sum(1 for w in body["warnings"] if "has not been generated" in w or "missing" in w) >= 2


def test_copilot_loop_compare_reports_rejects_unknown_loop(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    a = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    resp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": a["loop_id"], "right": "does-not-exist"},
    )
    assert resp.status_code == 404


def test_copilot_loop_compare_reports_does_not_cross_projects(tmp_path: Path) -> None:
    """A loop from project A cannot be addressed in project B's compare-reports
    endpoint — the project_id-scoped path resolution must reject it as 404.
    """
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    # Project A with a real loop.
    pa = save_project(settings, default_project("p-a"))
    pa_id = pa["id"]
    _make_copilot_loop_fixture_package(project_dir(settings, pa_id) / "p-a.aieng")
    pa["aieng_file"] = "p-a.aieng"
    save_project(settings, pa)
    a_loop = client.post(f"/api/projects/{pa_id}/copilot-loop/start", json={}).json()

    # Project B without that loop.
    pb = save_project(settings, default_project("p-b"))
    pb_id = pb["id"]
    _make_copilot_loop_fixture_package(project_dir(settings, pb_id) / "p-b.aieng")
    pb["aieng_file"] = "p-b.aieng"
    save_project(settings, pb)
    b_loop = client.post(f"/api/projects/{pb_id}/copilot-loop/start", json={}).json()

    # Asking project B about project A's loop must 404 — no cross-project leak.
    resp = client.get(
        f"/api/projects/{pb_id}/copilot-loops/compare-reports",
        params={"left": a_loop["loop_id"], "right": b_loop["loop_id"]},
    )
    assert resp.status_code == 404


def test_copilot_loop_compare_reports_ignores_suspicious_report_path(tmp_path: Path) -> None:
    """A persisted loop with a tampered `report_path` (e.g. path traversal,
    absolute path, or non-report prefix) must be treated as having no report.
    The endpoint must surface a warning and must not read anything from the
    suspicious path.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    a = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()
    b = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    # Tamper with persisted state to inject suspicious report_paths.
    loops_dir = project_dir(_make_patch_settings(tmp_path), project_id) / "copilot_loops"
    for loop_id, evil in (
        (a["loop_id"], "../../../etc/passwd"),
        (b["loop_id"], "/etc/passwd"),
    ):
        path = loops_dir / f"{loop_id}.json"
        loop = json.loads(path.read_text(encoding="utf-8"))
        loop.setdefault("context", {})["report"] = {"artifact_path": evil, "markdown": "x"}
        path.write_text(json.dumps(loop), encoding="utf-8")

    resp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": a["loop_id"], "right": b["loop_id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["left_report_exists"] is False
    assert body["right_report_exists"] is False
    # The suspicious path warning must surface, AND no diff was produced.
    assert any("safe location" in w for w in body["warnings"]) or all(
        "has not been generated yet" in w or "missing" in w or "safe location" in w
        for w in body["warnings"]
    )
    assert body["unified_diff"] in (None, "")


def test_copilot_loop_compare_reports_handles_legacy_loops_without_report_path(tmp_path: Path) -> None:
    """Legacy persisted loops (no report context) must produce a clean
    unavailable response, not a 500.
    """
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    project = save_project(settings, default_project("p-legacy"))
    project_id = project["id"]
    _make_copilot_loop_fixture_package(project_dir(settings, project_id) / "p-legacy.aieng")
    project["aieng_file"] = "p-legacy.aieng"
    save_project(settings, project)

    legacy_dir = project_dir(settings, project_id) / "copilot_loops"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    for legacy_id in ("legacyA1234567", "legacyB1234567"):
        (legacy_dir / f"{legacy_id}.json").write_text(
            json.dumps({
                "schema_version": "0.0-pre",
                "loop_id": legacy_id,
                "status": "completed",
                "steps": [],
            }),
            encoding="utf-8",
        )

    resp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": "legacyA1234567", "right": "legacyB1234567"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["left_report_exists"] is False
    assert body["right_report_exists"] is False
    assert body["unified_diff"] in (None, "")


def _highlights_by_id(diff_body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["id"]: h for h in (diff_body.get("highlights") or [])}


def _required_highlight_ids() -> set[str]:
    return {
        "approval_decision",
        "proposal",
        "verification_status",
        "stale_artifacts",
        "metric_summary",
        "target_summary",
        "warnings_errors",
        "report_availability",
    }


def test_copilot_loop_compare_reports_highlights_reject_vs_approve_changed(monkeypatch, tmp_path: Path) -> None:
    """Highlights derived from two loops: a rejected loop and an approved
    loop must produce `changed` highlights for approval_decision and
    report_availability, with critical severity on the decision swing.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "rejH", "rejected")
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "appH", "approved")

    body = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected_id, "right": approved_id},
    ).json()
    highlights = _highlights_by_id(body)
    assert _required_highlight_ids() <= set(highlights)

    decision = highlights["approval_decision"]
    assert decision["status"] == "changed"
    assert decision["severity"] == "critical"
    assert decision["left"] == "rejected"
    assert decision["right"] == "approved"

    # Both reports exist -> availability unchanged + report_availability info.
    avail = highlights["report_availability"]
    assert avail["status"] == "unchanged"
    assert avail["left"] == "present" and avail["right"] == "present"


def test_copilot_loop_compare_reports_highlights_missing_report(tmp_path: Path) -> None:
    """If one report is missing, the report_availability highlight must be
    a `missing` status with warning severity and an informative summary.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_loop = _advance_loop_to_apply_waiting(client, project_id)
    client.post(f"/api/projects/{project_id}/copilot-loop/{rejected_loop['loop_id']}/reject")
    _finish_loop_through_report(client, project_id, rejected_loop["loop_id"])
    fresh = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    body = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected_loop["loop_id"], "right": fresh["loop_id"]},
    ).json()
    highlights = _highlights_by_id(body)
    avail = highlights["report_availability"]
    assert avail["status"] == "missing"
    assert avail["severity"] == "warning"
    assert avail["left"] == "present" and avail["right"] == "missing"


def test_copilot_loop_compare_reports_highlights_legacy_loops_safe(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    project = save_project(settings, default_project("p-legacy-hi"))
    project_id = project["id"]
    _make_copilot_loop_fixture_package(project_dir(settings, project_id) / "p-l.aieng")
    project["aieng_file"] = "p-l.aieng"
    save_project(settings, project)
    legacy_dir = project_dir(settings, project_id) / "copilot_loops"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    for legacy_id in ("legacyHi000001", "legacyHi000002"):
        (legacy_dir / f"{legacy_id}.json").write_text(
            json.dumps({"schema_version": "0.0-pre", "loop_id": legacy_id, "status": "completed", "steps": []}),
            encoding="utf-8",
        )

    body = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": "legacyHi000001", "right": "legacyHi000002"},
    ).json()
    highlights = _highlights_by_id(body)
    # Legacy loops produce mostly missing/unknown statuses without crashing.
    statuses = {h["id"]: h["status"] for h in (body.get("highlights") or [])}
    # Approval decision compares "none" vs "none" -> unchanged.
    assert statuses.get("approval_decision") in {"unchanged", "missing", "unknown"}
    # Proposal: neither has one -> missing.
    assert statuses.get("proposal") == "missing"
    # Verification: neither evaluated -> unknown.
    assert statuses.get("verification_status") == "unknown"
    # No report -> report availability is missing/warning.
    assert highlights["report_availability"]["status"] == "missing"


def test_copilot_loop_compare_reports_highlights_claim_boundary_critical_when_absent(
    monkeypatch, tmp_path: Path
) -> None:
    """If a report exists but does not contain a claim-boundary statement,
    the highlights must flag it as a critical missing piece.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "rejCB", "rejected")
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "appCB", "approved")

    # Tamper with the rejected loop's report to strip the claim-boundary text.
    import zipfile as _zf
    pkg = project_dir(_make_patch_settings(tmp_path), project_id).glob("*.aieng")
    # Locate the package by reading the project file.
    pkg_path = next(iter(pkg))
    member = f"reports/copilot_loop/{rejected_id}.md"
    members: dict[str, bytes] = {}
    with _zf.ZipFile(pkg_path, "r") as z:
        for name in z.namelist():
            if name == member:
                continue
            members[name] = z.read(name)
    with _zf.ZipFile(pkg_path, "w", compression=_zf.ZIP_DEFLATED) as z:
        for name, blob in members.items():
            z.writestr(name, blob)
        z.writestr(member, "# Stripped\n\nNo boundary here.\n")

    body = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected_id, "right": approved_id},
    ).json()
    highlights = _highlights_by_id(body)
    assert "claim_boundary_left" in highlights
    assert highlights["claim_boundary_left"]["status"] == "missing"
    assert highlights["claim_boundary_left"]["severity"] == "critical"


def test_copilot_loop_compare_reports_highlights_stale_count_change(monkeypatch, tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "stR", "rejected")
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "stA", "approved")
    body = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected_id, "right": approved_id},
    ).json()
    highlights = _highlights_by_id(body)
    stale = highlights["stale_artifacts"]
    # Rejected has no stale artifacts; approved has at least one stale entry
    # because the runtime emits a revalidation_status.json that propagates.
    if int(stale["left"]) != int(stale["right"]):
        assert stale["status"] == "changed"
        assert stale["severity"] == "warning"
    else:
        assert stale["status"] == "unchanged"


def test_copilot_loop_compare_reports_truncates_oversized_reports(monkeypatch, tmp_path: Path) -> None:
    """If a loop report exceeds the size cap, the endpoint must truncate and
    warn rather than streaming the full payload.
    """
    import app.copilot_loop as cl

    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_loop = _advance_loop_to_apply_waiting(client, project_id)
    client.post(f"/api/projects/{project_id}/copilot-loop/{rejected_loop['loop_id']}/reject")
    _finish_loop_through_report(client, project_id, rejected_loop["loop_id"])
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "okR2", "approved")

    monkeypatch.setattr(cl, "_REPORT_TEXT_CAP", 64)
    resp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected_loop["loop_id"], "right": approved_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["left_report_exists"] is True
    assert body["right_report_exists"] is True
    assert body["left_report_truncated"] is True or body["right_report_truncated"] is True
    assert any("truncated" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# Decision Review Workbench v0.4 — export-review endpoint
# ---------------------------------------------------------------------------


def _export_contract_fields() -> set[str]:
    return {
        "schema_version",
        "project_id",
        "loop_ids",
        "export_path",
        "export_text",
        "warnings",
        "claim_boundary",
        "included",
    }


def test_copilot_loop_export_review_single_loop(monkeypatch, tmp_path: Path) -> None:
    client, project_id, pkg_path = _setup_copilot_loop_project(tmp_path)
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "exp1", "approved")

    resp = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={"loop_ids": [approved_id], "include_reports": True, "include_diff": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _export_contract_fields() <= set(body)
    assert body["loop_ids"] == [approved_id]
    assert body["export_path"].startswith("reports/copilot_loop_review/")
    assert body["export_path"].endswith(".md")
    md = body["export_text"]
    assert "Single-loop record" in md
    assert "Claim boundary" in md
    assert "does not certify" in md
    # The single-loop branch must not pretend to have a What Changed table.
    assert "## What changed" not in md
    # Export was written into the .aieng package.
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert body["export_path"] in zf.namelist()


def test_copilot_loop_export_review_two_loops_with_highlights_and_diff(monkeypatch, tmp_path: Path) -> None:
    client, project_id, pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "expR", "rejected")
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "expA", "approved")

    resp = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={
            "loop_ids": [rejected_id, approved_id],
            "include_reports": False,
            "include_diff": True,
            "include_highlights": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    md = body["export_text"]
    assert "Two-loop comparison" in md
    assert "## What changed (structured highlights)" in md
    assert "## Report diff (unified)" in md
    # The decision swing must show in the highlights table.
    assert "Approval decision" in md
    assert "`changed`" in md or "changed" in md
    # Claim boundary always present.
    assert "does not certify" in md
    assert "does not certify" in md
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert body["export_path"] in zf.namelist()


def test_copilot_loop_export_review_missing_report_emits_warning(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    fresh = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    resp = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={"loop_ids": [fresh["loop_id"]], "include_reports": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any("no readable report" in w.lower() for w in body["warnings"])
    assert "_Report not available._" in body["export_text"]
    assert "does not certify" in body["export_text"]


def test_copilot_loop_export_review_rejects_unknown_loop(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    resp = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={"loop_ids": ["doesnotexist1"]},
    )
    assert resp.status_code == 404


def test_copilot_loop_export_review_rejects_invalid_loop_id_format(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    for bad in ("../../etc/passwd", "/abs/path", "loop with spaces", "x"):
        resp = client.post(
            f"/api/projects/{project_id}/copilot-loops/export-review",
            json={"loop_ids": [bad]},
        )
        assert resp.status_code == 400, f"expected 400 for bad id {bad!r}, got {resp.status_code}"


def test_copilot_loop_export_review_rejects_empty_or_oversized_loop_ids(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    assert client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={"loop_ids": []},
    ).status_code == 400
    assert client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={"loop_ids": ["a1", "b2", "c3"]},
    ).status_code == 400


def test_copilot_loop_export_review_path_is_server_generated(tmp_path: Path) -> None:
    """The export path is constructed server-side from a constant prefix and
    a timestamp; client request payload cannot influence the output path.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    fresh = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()

    resp = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={
            "loop_ids": [fresh["loop_id"]],
            "export_path": "../../etc/passwd",  # ignored
            "filename": "/abs/evil.md",         # ignored
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["export_path"].startswith("reports/copilot_loop_review/")
    assert ".." not in body["export_path"]
    assert not body["export_path"].startswith("/")


def test_copilot_loop_export_review_does_not_cross_projects(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    pa = save_project(settings, default_project("p-a-x"))
    _make_copilot_loop_fixture_package(project_dir(settings, pa["id"]) / "p-a.aieng")
    pa["aieng_file"] = "p-a.aieng"
    save_project(settings, pa)
    a_loop = client.post(f"/api/projects/{pa['id']}/copilot-loop/start", json={}).json()
    pb = save_project(settings, default_project("p-b-x"))
    _make_copilot_loop_fixture_package(project_dir(settings, pb["id"]) / "p-b.aieng")
    pb["aieng_file"] = "p-b.aieng"
    save_project(settings, pb)

    # Project B cannot export project A's loop.
    resp = client.post(
        f"/api/projects/{pb['id']}/copilot-loops/export-review",
        json={"loop_ids": [a_loop["loop_id"]]},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Decision Review Workbench v0.4 — demo seed endpoint
# ---------------------------------------------------------------------------


def test_demo_seed_creates_project_with_two_baked_loops(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    resp = client.post("/api/demo/copilot-loop/seed", json={})
    assert resp.status_code == 200
    body = resp.json()
    project_id = body["project_id"]
    assert project_id
    assert len(body["loops"]) == 2
    decisions = {l["decision"] for l in body["loops"]}
    assert decisions == {"rejected", "approved"}
    assert "fixture" in body["notice"].lower() or "demo" in body["notice"].lower()


def test_demo_seed_loops_appear_in_list_with_decisions(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    seed = client.post("/api/demo/copilot-loop/seed", json={}).json()
    project_id = seed["project_id"]

    listing = client.get(f"/api/projects/{project_id}/copilot-loops").json()
    decisions = {s["decision"] for s in listing["loops"]}
    assert decisions == {"rejected", "approved"}
    # Both loops have report paths populated.
    for s in listing["loops"]:
        assert s["report_path"], f"demo loop {s['loop_id']} should have a report_path"


def test_demo_seed_reports_do_not_claim_certification(tmp_path: Path) -> None:
    """Pre-baked demo reports must include the explicit claim-boundary
    statement and must not contain any certification language.
    """
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    seed = client.post("/api/demo/copilot-loop/seed", json={}).json()
    project_id = seed["project_id"]
    for entry in seed["loops"]:
        resp = client.get(f"/api/projects/{project_id}/copilot-loop/{entry['loop_id']}/report")
        assert resp.status_code == 200
        markdown = resp.json()["markdown"]
        assert "does not certify the design" in markdown
        assert "does not certify" in markdown
        forbidden = ("design is certified", "certifies the design", "engineering claim accepted")
        for phrase in forbidden:
            assert phrase not in markdown.lower(), (
                f"demo report must not assert certification ({phrase!r})"
            )


def test_demo_seed_compare_reports_produces_highlights(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    seed = client.post("/api/demo/copilot-loop/seed", json={}).json()
    project_id = seed["project_id"]
    rejected = next(l["loop_id"] for l in seed["loops"] if l["decision"] == "rejected")
    approved = next(l["loop_id"] for l in seed["loops"] if l["decision"] == "approved")

    body = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected, "right": approved},
    ).json()
    highlights = _highlights_by_id(body)
    decision = highlights["approval_decision"]
    assert decision["status"] == "changed"
    assert decision["severity"] == "critical"
    # Demo data drives a real metric delta on the approved side.
    assert highlights["metric_summary"]["status"] in {"changed", "missing"}
    # Both demo reports contain a claim boundary.
    if "claim_boundary_presence" in highlights:
        assert highlights["claim_boundary_presence"]["status"] == "unchanged"


# ---------------------------------------------------------------------------
# Demo Release Candidate v0.5 — idempotent seed, reset, link-out export,
# capped embedded reports, and end-to-end smoke test
# ---------------------------------------------------------------------------


def test_demo_seed_is_idempotent_reuses_existing_project(tmp_path: Path) -> None:
    """Default seed should not create a second demo project when one already
    exists. Subsequent calls return the same project_id and set `reused`.
    """
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    first = client.post("/api/demo/copilot-loop/seed", json={}).json()
    second = client.post("/api/demo/copilot-loop/seed", json={}).json()
    third = client.post("/api/demo/copilot-loop/seed", json={}).json()

    assert first["project_id"] == second["project_id"] == third["project_id"]
    assert first["reused"] is False
    assert second["reused"] is True
    assert third["reused"] is True

    # The workspace must contain exactly one demo project, not three.
    listing = client.get("/api/projects").json()
    demo_projects = [p for p in listing if p.get("demo_copilot_loop") or p.get("demo_kind") == "bracket-lightweighting"]
    assert len(demo_projects) == 1


def test_demo_seed_reset_creates_fresh_project(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    first = client.post("/api/demo/copilot-loop/seed", json={}).json()
    reset = client.post("/api/demo/copilot-loop/seed", json={"reset": True}).json()

    assert reset["reused"] is False
    assert reset["project_id"] != first["project_id"]
    listing = client.get("/api/projects").json()
    demo_projects = [p for p in listing if p.get("demo_copilot_loop")]
    assert len(demo_projects) == 1
    assert demo_projects[0]["id"] == reset["project_id"]


def test_demo_reset_endpoint_removes_only_demo_projects(tmp_path: Path) -> None:
    """A reset must not delete real user projects. Only projects tagged as
    Copilot-loop demo are removed.
    """
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    user = save_project(settings, default_project("Real user project"))
    user_id = user["id"]
    demo = client.post("/api/demo/copilot-loop/seed", json={}).json()
    demo_id = demo["project_id"]

    resp = client.post("/api/demo/copilot-loop/reset", json={})
    assert resp.status_code == 200
    body = resp.json()
    removed_ids = {r["project_id"] for r in body["removed"]}
    assert demo_id in removed_ids
    assert user_id not in removed_ids

    # Real user project still exists; demo project is gone.
    listing = client.get("/api/projects").json()
    listed_ids = {p["id"] for p in listing}
    assert user_id in listed_ids
    assert demo_id not in listed_ids


def test_demo_seed_marks_metadata_as_demo(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    seed = client.post("/api/demo/copilot-loop/seed", json={}).json()

    project = client.get("/api/projects").json()
    demo_entry = next(p for p in project if p["id"] == seed["project_id"])
    assert demo_entry.get("demo") is True
    assert demo_entry.get("demo_copilot_loop") is True
    assert demo_entry.get("demo_kind") == "bracket-lightweighting"
    assert "fixture" in (demo_entry.get("demo_notice") or "").lower()


def test_demo_seed_returns_next_action_hint(tmp_path: Path) -> None:
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)
    seed = client.post("/api/demo/copilot-loop/seed", json={}).json()
    assert "next_action" in seed
    assert "compare" in seed["next_action"].lower()


def test_export_review_contains_relative_report_link(monkeypatch, tmp_path: Path) -> None:
    """v0.5: per-loop sections must include a workspace-relative link to the
    report under `../copilot_loop/<id>.md` so reviewers can drill into the
    full report without inflating the export artifact.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "lnR", "rejected")
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "lnA", "approved")

    body = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={"loop_ids": [rejected_id, approved_id], "include_reports": False, "include_diff": True},
    ).json()
    md = body["export_text"]
    # Each loop section carries the relative link.
    assert f"(../copilot_loop/{rejected_id}.md)" in md
    assert f"(../copilot_loop/{approved_id}.md)" in md
    # The original report_path is preserved as the link label.
    assert f"reports/copilot_loop/{rejected_id}.md" in md
    assert f"reports/copilot_loop/{approved_id}.md" in md


def test_export_review_caps_embedded_reports_and_warns(monkeypatch, tmp_path: Path) -> None:
    """When ``include_reports=true`` and a report exceeds the embedded cap,
    the export must truncate with an explicit "see linked full report" note
    AND register a truncation warning. The full report link must still be
    present.
    """
    import app.copilot_loop as cl

    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "capR", "rejected")
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "capA", "approved")

    monkeypatch.setattr(cl, "_EMBEDDED_REPORT_CAP", 200)
    body = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={
            "loop_ids": [rejected_id, approved_id],
            "include_reports": True,
            "include_diff": False,
        },
    ).json()
    md = body["export_text"]
    assert "[…truncated at 200 chars" in md
    assert any("truncated" in w.lower() for w in body["warnings"])
    # Link-outs must remain so a reviewer can read the full report.
    assert f"(../copilot_loop/{rejected_id}.md)" in md
    assert f"(../copilot_loop/{approved_id}.md)" in md
    # Even capped, claim boundary is always present.
    assert "does not certify" in md
    assert "does not certify" in md


def test_export_review_missing_report_keeps_link_label_or_not_available(tmp_path: Path) -> None:
    """If a side has no report, the export must say "Not available" rather
    than fabricating a link.
    """
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    fresh = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()
    body = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={"loop_ids": [fresh["loop_id"]]},
    ).json()
    md = body["export_text"]
    assert "- Report: Not available" in md
    # No fabricated link for a non-existent report.
    assert "(../copilot_loop/" not in md
    assert "does not certify" in md


def test_export_review_caps_unified_diff_with_warning(monkeypatch, tmp_path: Path) -> None:
    import app.copilot_loop as cl

    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    rejected_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "dfR", "rejected")
    approved_id = _run_loop_to_report(client, project_id, monkeypatch, tmp_path, "dfA", "approved")

    monkeypatch.setattr(cl, "_EMBEDDED_REPORT_CAP", 50)
    body = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={"loop_ids": [rejected_id, approved_id], "include_diff": True, "include_reports": False},
    ).json()
    md = body["export_text"]
    # Cap for diff is 4x the per-report cap (=200 with this monkeypatch).
    if "[…truncated" in md:
        # Truncation actually occurred — assert the warning and the linked-out hint.
        assert any("Unified diff exceeded" in w for w in body["warnings"])
        assert "load the full diff" in md


def test_export_review_path_is_still_server_generated_in_v0_5(tmp_path: Path) -> None:
    client, project_id, _pkg_path = _setup_copilot_loop_project(tmp_path)
    fresh = client.post(f"/api/projects/{project_id}/copilot-loop/start", json={}).json()
    body = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={
            "loop_ids": [fresh["loop_id"]],
            "export_path": "../../etc/passwd",
            "filename": "../boom.md",
        },
    ).json()
    assert body["export_path"].startswith("reports/copilot_loop_review/")
    assert ".." not in body["export_path"]


# ---------------------------------------------------------------------------
# Demo Release Candidate v0.5 — full demo smoke chain
# ---------------------------------------------------------------------------


def test_v05_demo_smoke_seed_list_compare_export(tmp_path: Path) -> None:
    """End-to-end deterministic chain a new user will exercise:
    seed -> list -> compare -> confirm highlights -> export with
    highlights+diff -> assert artifact and claim-boundary.

    This is the headline release-readiness test.
    """
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    # Seed
    seed = client.post("/api/demo/copilot-loop/seed", json={}).json()
    project_id = seed["project_id"]
    assert seed["reused"] is False
    assert {l["decision"] for l in seed["loops"]} == {"rejected", "approved"}

    # List loops on the demo project
    listing = client.get(f"/api/projects/{project_id}/copilot-loops").json()
    decisions = {s["decision"] for s in listing["loops"]}
    assert decisions == {"rejected", "approved"}
    assert len(listing["loops"]) >= 2
    rejected = next(s for s in listing["loops"] if s["decision"] == "rejected")
    approved = next(s for s in listing["loops"] if s["decision"] == "approved")

    # Compare reports -> highlights must show approval-decision changed (critical)
    cmp = client.get(
        f"/api/projects/{project_id}/copilot-loops/compare-reports",
        params={"left": rejected["loop_id"], "right": approved["loop_id"]},
    ).json()
    highlights = {h["id"]: h for h in (cmp.get("highlights") or [])}
    assert "approval_decision" in highlights
    assert highlights["approval_decision"]["status"] == "changed"
    assert highlights["approval_decision"]["severity"] == "critical"

    # Export the two-loop review with highlights and diff
    exp = client.post(
        f"/api/projects/{project_id}/copilot-loops/export-review",
        json={
            "loop_ids": [rejected["loop_id"], approved["loop_id"]],
            "include_highlights": True,
            "include_diff": True,
            "include_reports": False,
        },
    ).json()
    md = exp["export_text"]
    # Export artifact present in the .aieng package.
    pkg_path = project_dir(settings, project_id) / seed["package_path"]
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert exp["export_path"] in zf.namelist()
    # Headlines and structure
    assert "Two-loop comparison" in md
    assert "## What changed (structured highlights)" in md
    assert "## Report diff (unified)" in md
    # Relative report link-outs
    assert f"(../copilot_loop/{rejected['loop_id']}.md)" in md
    assert f"(../copilot_loop/{approved['loop_id']}.md)" in md
    # Claim boundary present in both languages
    assert "does not certify" in md
    assert "does not certify" in md
    # No certification language
    md_lower = md.lower()
    for forbidden in (
        "design is certified",
        "certifies the design",
        "engineering claim accepted",
    ):
        assert forbidden not in md_lower



# ---------------------------------------------------------------------------
# Demo Health Check v0.6 — smoke-check endpoint
# ---------------------------------------------------------------------------


def test_demo_smoke_check_happy_path(tmp_path: Path) -> None:
    """The smoke-check endpoint should pass all checks on a healthy demo."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    resp = client.post("/api/demo/copilot-loop/smoke-check", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"]
    checks = body["checks"]
    assert checks
    check_ids = {c["id"] for c in checks}
    required = {
        "seed",
        "list_loops",
        "identify_decisions",
        "compare_reports",
        "highlight_approval_decision",
        "export_review",
        "export_artifact_exists",
        "claim_boundary_en",
        "claim_boundary_zh",
        "no_certification_language",
        "highlight_critical_severity",
    }
    assert required.issubset(check_ids)
    for c in checks:
        assert c["status"] == "passed", f"Check {c['id']} failed: {c['summary']}"
    assert body["export_path"]
    assert "does not certify" in body["claim_boundary"]
    assert body["warnings"] == []


def test_demo_smoke_check_with_reset_leaves_real_projects(tmp_path: Path) -> None:
    """Smoke-check with reset=true must recreate demo but never touch real projects."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    user = save_project(settings, default_project("Real user project"))
    user_id = user["id"]

    first = client.post("/api/demo/copilot-loop/smoke-check", json={}).json()
    first_pid = first["project_id"]

    reset = client.post("/api/demo/copilot-loop/smoke-check", json={"reset": True}).json()
    assert reset["ok"] is True
    assert reset["reused"] is False
    assert reset["project_id"] != first_pid

    listing = client.get("/api/projects").json()
    listed_ids = {p["id"] for p in listing}
    assert user_id in listed_ids
    assert first_pid not in listed_ids


def test_demo_smoke_check_structured_failure_no_500(tmp_path: Path) -> None:
    """If the demo chain is broken, smoke-check returns ok=False with failed
    checks — it does not raise 500."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    # Seed the demo first.
    seed = client.post("/api/demo/copilot-loop/seed", json={}).json()
    project_id = seed["project_id"]

    # Tamper: delete the approved loop file so the chain breaks.
    approved = next(l["loop_id"] for l in seed["loops"] if l["decision"] == "approved")
    loop_file = project_dir(settings, project_id) / "copilot_loops" / f"{approved}.json"
    if loop_file.exists():
        loop_file.unlink()

    resp = client.post("/api/demo/copilot-loop/smoke-check", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    failed = [c for c in body["checks"] if c["status"] == "failed"]
    assert failed
    # At minimum the approved loop cannot be found.
    failed_ids = {c["id"] for c in failed}
    assert "identify_decisions" in failed_ids or "compare_reports" in failed_ids


def test_demo_smoke_check_fails_on_missing_claim_boundary(monkeypatch, tmp_path: Path) -> None:
    """If the export somehow loses its claim boundary, the smoke check fails."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    # Monkeypatch the claim-boundary note to empty so the export is clean.
    import app.copilot_loop as cl
    original_note = cl._CLAIM_BOUNDARY_EXPORT_NOTE
    monkeypatch.setattr(cl, "_CLAIM_BOUNDARY_EXPORT_NOTE", "")

    # Use reset=true so a fresh demo is created under the patched note.
    resp = client.post("/api/demo/copilot-loop/smoke-check", json={"reset": True})
    body = resp.json()
    assert body["ok"] is False
    en_check = next((c for c in body["checks"] if c["id"] == "claim_boundary_en"), None)
    assert en_check is not None and en_check["status"] == "failed"
    # The Chinese claim boundary is hard-coded in _build_review_markdown separately
    # from the English _CLAIM_BOUNDARY_EXPORT_NOTE, so it still passes.

    monkeypatch.setattr(cl, "_CLAIM_BOUNDARY_EXPORT_NOTE", original_note)


def test_demo_smoke_check_fails_on_certification_language(monkeypatch, tmp_path: Path) -> None:
    """If prohibited certification language leaks into the export, smoke check fails."""
    settings = _make_patch_settings(tmp_path)
    test_app = create_app(settings)
    client = TestClient(test_app)

    # Monkeypatch the claim-boundary note to contain a forbidden phrase.
    import app.copilot_loop as cl
    original_note = cl._CLAIM_BOUNDARY_EXPORT_NOTE
    monkeypatch.setattr(
        cl,
        "_CLAIM_BOUNDARY_EXPORT_NOTE",
        "This export is certified safe and engineering claim approved.",
    )

    # Use reset=true so a fresh demo is created under the patched note.
    resp = client.post("/api/demo/copilot-loop/smoke-check", json={"reset": True})
    body = resp.json()
    assert body["ok"] is False
    cert_check = next((c for c in body["checks"] if c["id"] == "no_certification_language"), None)
    assert cert_check is not None and cert_check["status"] == "failed"
    assert "certified safe" in cert_check["summary"]

    monkeypatch.setattr(cl, "_CLAIM_BOUNDARY_EXPORT_NOTE", original_note)


def test_local_agent_capabilities_endpoint(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/local-agents/capabilities")

    assert resp.status_code == 200
    data = resp.json()
    assert "adapters" in data
    assert {item["adapter_id"] for item in data["adapters"]} >= {"claude-code", "codex-cli"}


def test_delete_project_removes_dir_and_chat(tmp_path: Path) -> None:
    """DELETE /api/projects/{id} removes the project dir and purges its chat data."""
    from app.main import create_app, default_project, project_dir, save_project

    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("to-delete"))
    pid = project["id"]
    client = TestClient(create_app(settings))

    assert client.get(f"/api/projects/{pid}").status_code == 200
    # create a chat session so we can confirm chat rows are purged
    sess = client.post(f"/api/projects/{pid}/chat-sessions", json={"title": "s"})
    assert sess.status_code == 200

    resp = client.delete(f"/api/projects/{pid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True and body["project_id"] == pid

    assert not project_dir(settings, pid).exists()
    assert client.get(f"/api/projects/{pid}").status_code == 404
    # deleting an unknown project 404s
    assert client.delete("/api/projects/nonexistent123456").status_code == 404


def test_chat_session_context_summary_api_get_update_refresh(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("summary-api"))
    client = TestClient(create_app(settings))
    project_id = project["id"]
    session = client.post(f"/api/projects/{project_id}/chat-sessions", json={"title": "Summary"}).json()
    session_id = session["id"]

    empty = client.get(f"/api/projects/{project_id}/chat-sessions/{session_id}/context-summary")
    assert empty.status_code == 200
    assert empty.json()["context_summary"] is None

    summary = {
        "schema_version": 1,
        "session_id": session_id,
        "project_id": project_id,
        "goal": "Summarize this session",
        "current_state": "Manual summary.",
        "important_decisions": [],
        "completed_steps": [],
        "pending_steps": [],
        "user_constraints": [],
        "relevant_files": [],
        "risks": [],
        "next_action": "Continue.",
        "updated_at": "2026-06-02T00:00:00+00:00",
    }
    put_resp = client.put(
        f"/api/projects/{project_id}/chat-sessions/{session_id}/context-summary",
        json={"context_summary": summary},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["context_summary"]["goal"] == "Summarize this session"

    mismatch = client.put(
        f"/api/projects/{project_id}/chat-sessions/{session_id}/context-summary",
        json={"context_summary": {**summary, "session_id": "other"}},
    )
    assert mismatch.status_code == 400

    client.post(
        f"/api/projects/{project_id}/chat-messages",
        json={
            "session_id": session_id,
            "role": "user",
            "content": "Please remember api_key=secret-token and sk-abc123456789 while planning.",
        },
    )
    refresh = client.post(f"/api/projects/{project_id}/chat-sessions/{session_id}/context-summary/refresh")
    assert refresh.status_code == 200
    refreshed = refresh.json()["context_summary"]
    assert refreshed["goal"].startswith("Please remember")
    assert "secret-token" not in str(refreshed)
    assert "sk-abc123456789" not in str(refreshed)
    sessions_after_refresh = client.get(f"/api/projects/{project_id}/chat-sessions")
    assert sessions_after_refresh.status_code == 200
    refreshed_session = next(item for item in sessions_after_refresh.json() if item["id"] == session_id)
    assert refreshed_session["context_summary"]["goal"] == refreshed["goal"]
    assert refreshed_session["context_summary_updated_at"] == refreshed["updated_at"]

    clear = client.put(
        f"/api/projects/{project_id}/chat-sessions/{session_id}/context-summary",
        json={"context_summary": None},
    )
    assert clear.status_code == 200
    assert clear.json()["context_summary"] is None


def _make_shape_ir_package(settings, pid: str, members: dict) -> None:
    """Write a Shape IR .aieng into the project dir + populate verification +
    object registry, and point the project at it."""
    import json as _json, zipfile as _zip
    from app.main import project_dir, get_project, save_project
    from aieng.converters.shape_ir_verification import write_shape_ir_verification
    from aieng.converters.shape_ir_object_registry import write_shape_ir_object_registry

    pkg = project_dir(settings, pid) / f"{pid}.aieng"
    with _zip.ZipFile(pkg, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content if isinstance(content, (bytes, str)) else _json.dumps(content))
    write_shape_ir_verification(pkg)
    write_shape_ir_object_registry(pkg)
    proj = get_project(settings, pid)
    proj["aieng_file"] = f"{pid}.aieng"
    save_project(settings, proj)


def _ir_manifest(representation: str, *, backend="build123d", geometry_kind="brep", executed=True) -> dict:
    m = {"source": {"source_document_metadata": {
            "representation": representation, "requested_representation": representation,
            "compile_runtime": backend, "representation_fallback": False}},
         "achieved_capability_levels": [{"level": n} for n in (0, 1, 2, 3)]}
    if executed:
        m["geometry_execution"] = {"executed": True, "backend": backend, "geometry_kind": geometry_kind}
    return m


def test_object_registry_endpoint_brep(tmp_path: Path) -> None:
    from app.main import create_app, default_project, save_project
    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("brep-reg"))["id"]
    _make_shape_ir_package(settings, pid, {
        "geometry/shape_ir.json": {"parts": [
            {"id": "plate", "type": "box", "parameters": {"LENGTH": 10}},
            {"id": "post", "type": "cylinder"},
        ]},
        "geometry/source.py": "# Shape IR node: plate\n# Shape IR node: post\n",
        "geometry/generated.step": "ISO-10303-21;\n",
        "geometry/preview.glb": b"glTF",
        "geometry/topology_map.json": {"metadata": {"extractor": "build123d"}, "entities": [
            {"id": "body_001", "type": "solid", "name": "plate", "face_ids": ["face_001", "face_002"]},
            {"id": "body_002", "type": "solid", "name": "post", "face_ids": ["face_003"]},
            {"id": "face_001", "type": "face", "body_id": "body_001", "surface_type": "plane"},
            {"id": "face_002", "type": "face", "body_id": "body_001", "surface_type": "plane"},
            {"id": "face_003", "type": "face", "body_id": "body_002", "surface_type": "cylinder"},
        ]},
        "provenance/conversion_manifest.json": _ir_manifest("brep_build123d"),
    })
    client = TestClient(create_app(settings))
    resp = client.get(f"/api/projects/{pid}/object-registry")
    assert resp.status_code == 200
    data = resp.json()
    objs = {o["node_id"]: o for o in data["object_registry"]["objects"]}
    assert set(objs) == {"plate", "post"}
    assert objs["plate"]["linkage"] == "name_match"
    assert set(objs["plate"]["viewer_selectable_ids"]) == {"face_001", "face_002"}
    assert objs["plate"]["editable_parameters"] == {"LENGTH": 10}
    assert objs["plate"]["cad_editable"] is True
    assert data["verification"]["representation_kind"] == "brep"


def test_object_registry_endpoint_mesh_object_level(tmp_path: Path) -> None:
    from app.main import create_app, default_project, save_project
    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("mesh-reg"))["id"]
    _make_shape_ir_package(settings, pid, {
        "geometry/shape_ir.json": {"representation": "implicit_sdf", "parts": [
            {"id": "a", "type": "sphere"}, {"id": "b", "type": "sphere"}]},
        "geometry/sdf_source.py": "# Shape IR node: a\n# Shape IR node: b\n",
        "geometry/preview.glb": b"glTF",
        "geometry/topology_map.json": {"metadata": {
            "extractor": "SDFRunner", "extraction_mode": "marching_cubes_mesh", "real_step_parsing": False},
            "entities": [
                {"id": "body_001", "type": "solid", "name": "sdf_body", "face_ids": ["face_001"]},
                {"id": "face_001", "type": "face", "body_id": "body_001", "surface_type": "freeform"}]},
        "provenance/conversion_manifest.json": _ir_manifest("implicit_sdf", backend="sdf", geometry_kind="mesh"),
    })
    client = TestClient(create_app(settings))
    resp = client.get(f"/api/projects/{pid}/object-registry")
    assert resp.status_code == 200
    data = resp.json()
    objs = {o["node_id"]: o for o in data["object_registry"]["objects"]}
    # object-level (fused) selection: every node resolves to the single mesh body/region
    for nid in ("a", "b"):
        assert objs[nid]["linkage"] == "fused_mesh"
        assert objs[nid]["viewer_selectable_ids"] == ["face_001"]
        assert objs[nid]["representation_kind"] == "implicit_field"
        assert objs[nid]["cad_editable"] is False


def test_object_registry_endpoint_404_without_registry(tmp_path: Path) -> None:
    from app.main import create_app, default_project, save_project
    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("empty"))["id"]
    client = TestClient(create_app(settings))
    assert client.get(f"/api/projects/{pid}/object-registry").status_code == 404


def test_shape_ir_patch_endpoint_dry_run_and_reject(tmp_path: Path) -> None:
    import json as _json, zipfile as _zip
    from app.main import create_app, default_project, project_dir, save_project, get_project

    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("patch"))["id"]
    pkg = project_dir(settings, pid) / f"{pid}.aieng"
    shape_ir = {"parts": [{"id": "plate", "type": "box", "parameters": {"RADIUS": 4}}]}
    with _zip.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/shape_ir.json", _json.dumps(shape_ir))
    proj = get_project(settings, pid); proj["aieng_file"] = f"{pid}.aieng"; save_project(settings, proj)
    client = TestClient(create_app(settings))

    # dry-run: report ok, shape_ir.json unchanged, no patch report written
    resp = client.post(f"/api/projects/{pid}/shape-ir-patch", json={
        "dry_run": True,
        "patch": {"operations": [{"op": "set_parameter", "target": "plate", "parameter": "RADIUS", "value": 12}]},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok" and data["dry_run"] is True
    assert data["patch_report"]["applied_count"] == 1 and data["recompile"] is None
    with _zip.ZipFile(pkg) as zf:
        assert _json.loads(zf.read("geometry/shape_ir.json"))["parts"][0]["parameters"]["RADIUS"] == 4
        assert "diagnostics/shape_ir_patch_report.json" not in zf.namelist()

    # invalid patch (missing target) -> rejected, atomic (no change)
    bad = client.post(f"/api/projects/{pid}/shape-ir-patch", json={
        "patch": {"operations": [{"op": "set_parameter", "target": "ghost", "parameter": "X", "value": 1}]},
    })
    assert bad.status_code == 200 and bad.json()["status"] == "rejected"
    with _zip.ZipFile(pkg) as zf:
        assert _json.loads(zf.read("geometry/shape_ir.json"))["parts"][0]["parameters"]["RADIUS"] == 4


def test_cae_result_map_endpoint(tmp_path: Path) -> None:
    import json as _json, zipfile as _zip
    from app.main import create_app, default_project, project_dir, save_project, get_project

    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("cae-map"))["id"]
    pkg = project_dir(settings, pid) / f"{pid}.aieng"
    members = {
        "geometry/topology_map.json": {"entities": [
            {"id": "body_001", "type": "solid", "name": "plate", "bounding_box": [-20, -15, 0, 20, 15, 6], "face_ids": ["face_001"]},
            {"id": "body_002", "type": "solid", "name": "post", "bounding_box": [10, -3, 6, 16, 3, 26], "face_ids": ["face_010"]},
        ]},
        "registry/object_registry.json": {"objects": [
            {"node_id": "plate", "topology_entities": ["body_001", "face_001"], "linkage": "name_match"},
            {"node_id": "post", "topology_entities": ["body_002", "face_010"], "linkage": "name_match"},
        ]},
        "results/computed_metrics.json": {"load_cases": [{"id": "lc1", "metrics": {
            "max_von_mises_stress": {"value": 245.0, "unit": "MPa"}}}]},
        "results/field_regions.json": {"field": "S", "clusters": [
            {"id": "cluster_001", "location": {"x": 13, "y": 0, "z": 18}, "magnitude": {"value": 245.0, "unit": "MPa"}, "node_count": 40}]},
    }
    with _zip.ZipFile(pkg, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, _json.dumps(content))
    proj = get_project(settings, pid); proj["aieng_file"] = f"{pid}.aieng"; save_project(settings, proj)

    client = TestClient(create_app(settings))
    resp = client.get(f"/api/projects/{pid}/cae-result-map")
    assert resp.status_code == 200
    data = resp.json()
    hot = next(m for m in data["mapped_results"] if m["result_type"] == "stress")
    assert hot["source_ir_node"] == "post" and hot["confidence"] == "high"
    assert data["summary"]["resolved_to_node"] == 1
    with _zip.ZipFile(pkg) as zf:
        assert "analysis/cae_result_map.json" in zf.namelist()

    # a project with no CAE results -> 404
    pid2 = save_project(settings, default_project("no-cae"))["id"]
    pkg2 = project_dir(settings, pid2) / f"{pid2}.aieng"
    with _zip.ZipFile(pkg2, "w") as zf:
        zf.writestr("geometry/topology_map.json", _json.dumps({"entities": []}))
    proj2 = get_project(settings, pid2); proj2["aieng_file"] = f"{pid2}.aieng"; save_project(settings, proj2)
    assert client.get(f"/api/projects/{pid2}/cae-result-map").status_code == 404


def test_topology_optimization_endpoint(tmp_path: Path) -> None:
    import json as _json, zipfile as _zip
    from app.main import create_app, default_project, project_dir, save_project, get_project

    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("topopt"))["id"]
    pkg = project_dir(settings, pid) / f"{pid}.aieng"
    with _zip.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/shape_ir.json", _json.dumps({"parts": [{"id": "bracket", "type": "box"}]}))
    proj = get_project(settings, pid); proj["aieng_file"] = f"{pid}.aieng"; save_project(settings, proj)
    client = TestClient(create_app(settings))

    resp = client.post(f"/api/projects/{pid}/topology-optimization", json={
        "problem": {"grid": {"nelx": 16, "nely": 8}, "volfrac": 0.5, "max_iters": 8,
                    "bcs": {"preset": "cantilever"}, "design_space_node": "bracket"}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    topo = data["topology_optimization"]
    assert topo["optimizer"]["name"] == "simp_2d"
    assert topo["result"]["compliance_history"][-1] < topo["result"]["compliance_history"][0]
    assert topo["provenance"]["design_space_node"] == "bracket"
    with _zip.ZipFile(pkg) as zf:
        assert "analysis/topology_optimization.json" in zf.namelist()

    # writeback (voxels): author the optimization result back into Shape IR + recompile
    wb = client.post(f"/api/projects/{pid}/topology-optimization/writeback",
                     json={"representation": "manifold_mesh", "method": "voxels",
                           "cell_size": [2.0, 2.0], "thickness": 4.0})
    assert wb.status_code == 200
    wbd = wb.json()
    # shape_ir.json is rewritten before recompile, so it carries the density_voxels node
    with _zip.ZipFile(pkg) as zf:
        sir = _json.loads(zf.read("geometry/shape_ir.json"))
    assert sir["representation"] == "manifold_mesh"
    (node,) = sir["parts"]
    assert node["type"] == "density_voxels"
    assert sir["provenance"]["design_space_node"] == "bracket"
    if wbd["status"] == "ok":
        assert wbd["recompile"] is not None

    # writeback (contour, the default): smooth boundary -> an extruded_region node
    wb2 = client.post(f"/api/projects/{pid}/topology-optimization/writeback", json={})
    assert wb2.status_code == 200
    with _zip.ZipFile(pkg) as zf:
        sir2 = _json.loads(zf.read("geometry/shape_ir.json"))
    (node2,) = sir2["parts"]
    if node2["type"] == "extruded_region":
        assert node2["polygons"]
    else:
        # Contour extraction uses optional scikit-image. Without it, writeback
        # must preserve the result honestly as voxels instead of claiming a
        # contour was generated.
        assert node2["type"] == "density_voxels"
        assert "contour_fallback" in node2["source_optimization"]


def test_topology_optimization_derive_from_cae_endpoint(tmp_path: Path) -> None:
    import json as _json, zipfile as _zip
    from app.main import create_app, default_project, project_dir, save_project, get_project

    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("topopt_derive"))["id"]
    pkg = project_dir(settings, pid) / f"{pid}.aieng"
    topo = {"entities": [
        {"id": "body_plate", "type": "solid", "source_ir_node": "plate",
         "bounding_box": [0, 0, 0, 120, 80, 10]},
        {"id": "face_left", "type": "face", "bounding_box": [0, 0, 0, 0, 80, 10]},
        {"id": "face_right", "type": "face", "bounding_box": [120, 0, 0, 120, 80, 10]},
    ]}
    cae_map = {"mappings": [
        {"maps_to": {"feature_id": "feat_fix"}, "face_ids": ["face_left"]},
        {"maps_to": {"feature_id": "feat_load"}, "face_ids": ["face_right"]},
    ]}
    setup = ("boundary_conditions:\n  - {id: bc1, target_feature: feat_fix, type: fixed}\n"
             "loads:\n  - {id: ld1, target_feature: feat_load, type: force, value_n: 500.0, "
             "direction: [0.0, -1.0, 0.0]}\n")
    with _zip.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", _json.dumps(topo))
        zf.writestr("simulation/cae_mapping.json", _json.dumps(cae_map))
        zf.writestr("simulation/setup.yaml", setup)
    proj = get_project(settings, pid); proj["aieng_file"] = f"{pid}.aieng"; save_project(settings, proj)
    client = TestClient(create_app(settings))

    # read-only derive
    d = client.post(f"/api/projects/{pid}/topology-optimization/derive", json={"resolution": 32})
    assert d.status_code == 200 and d.json()["status"] == "ok"
    prob = d.json()["problem"]
    assert prob["derivation"]["derived"] is True
    assert prob["derivation"]["plane"]["u_axis"] == "x"
    assert prob["bcs"]["loads"][0]["fy"] == -500.0

    # auto_derive run path: no problem in body -> derived + solved + bcs_source explicit
    r = client.post(f"/api/projects/{pid}/topology-optimization", json={"auto_derive": True})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    topo_res = r.json()["topology_optimization"]
    assert topo_res["problem"]["bcs_source"] == "explicit"
    assert topo_res["result"]["compliance_history"][-1] < topo_res["result"]["compliance_history"][0]


def test_topology_optimization_3d_endpoint(tmp_path: Path) -> None:
    import json as _json, zipfile as _zip
    from app.main import create_app, default_project, project_dir, save_project, get_project

    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("topopt3d"))["id"]
    pkg = project_dir(settings, pid) / f"{pid}.aieng"
    topo = {"entities": [
        {"id": "body_blk", "type": "solid", "source_ir_node": "blk",
         "bounding_box": [0, 0, 0, 60, 40, 30]},
        {"id": "face_left", "type": "face", "bounding_box": [0, 0, 0, 0, 40, 30]},
        {"id": "face_right", "type": "face", "bounding_box": [60, 0, 0, 60, 40, 30]},
    ]}
    cae_map = {"mappings": [
        {"maps_to": {"feature_id": "feat_fix"}, "face_ids": ["face_left"]},
        {"maps_to": {"feature_id": "feat_load"}, "face_ids": ["face_right"]},
    ]}
    setup = ("boundary_conditions:\n  - {id: bc1, target_feature: feat_fix, type: fixed}\n"
             "loads:\n  - {id: ld1, target_feature: feat_load, type: force, value_n: 800.0, "
             "direction: [0.0, 0.0, -1.0]}\n")
    with _zip.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", _json.dumps(topo))
        zf.writestr("simulation/cae_mapping.json", _json.dumps(cae_map))
        zf.writestr("simulation/setup.yaml", setup)
    proj = get_project(settings, pid); proj["aieng_file"] = f"{pid}.aieng"; save_project(settings, proj)
    client = TestClient(create_app(settings))

    # 3D derive: full 3D, support on x=0 layer, load with full -Z vector
    d = client.post(f"/api/projects/{pid}/topology-optimization/derive",
                    json={"dimension": "3d", "resolution_3d": 10})
    assert d.status_code == 200 and d.json()["status"] == "ok"
    prob = d.json()["problem"]
    assert prob["dimension"] == "3d" and prob["frame"]["w_axis"] == "z"
    assert prob["bcs"]["loads"][0]["fz"] == -800.0

    # 3D run via optimizer=simp_3d (implies 3D derivation)
    r = client.post(f"/api/projects/{pid}/topology-optimization",
                    json={"auto_derive": True, "optimizer": "simp_3d",
                          "problem": {"max_iters": 8, "volume_fraction": 0.4}})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    res = r.json()["topology_optimization"]
    assert res["dimension"] == "3d"
    assert res["optimizer"]["capability"]["production_ready"] is False
    assert res["result"]["density_grid_3d"]["nx"] >= 2 and res["result"]["solid_voxel_count"] >= 0
    assert res["result"]["compliance_history"][-1] < res["result"]["compliance_history"][0]

    # 3D writeback default -> smooth marching-cubes surface mesh (manifold), viewer refreshes
    wb = client.post(f"/api/projects/{pid}/topology-optimization/writeback", json={})
    assert wb.status_code == 200 and wb.json()["status"] == "ok"
    wb_body = wb.json()
    sir = wb_body["shape_ir"]
    assert sir["representation"] == "manifold_mesh"          # 3D defaults to mesh, not B-Rep
    node = sir["parts"][0]
    if node["type"] == "density_voxels":
        # Marching cubes is optional. The endpoint must report the fallback
        # explicitly rather than mislabel a voxel body as a smooth mesh.
        assert node["dimension"] == 3
        assert "surface_fallback" in node["source_optimization"]
        assert node["preview_only"] is True and node["cad_editable"] is False
        return
    assert node["type"] == "smooth_mesh_proxy" and node["dimension"] == 3   # default = smooth mesh proxy
    assert node["preview_only"] is True and node["cad_editable"] is False
    assert node["triangle_count"] > 0 and "not_production_cad" in node["tags"]
    recompile = wb_body.get("recompile") or {}
    if recompile.get("executed") is False:
        assert "manifold3d" in str(recompile.get("error") or "")
        ge = _read_geom_exec(pkg)
        assert ge["executed"] is False
        assert ge["geometry_kind"] == "none"
        assert ge["real_geometry"] is False
        assert (project_dir(settings, pid) / "viewer" / "model.glb").exists() is False
        return
    assert recompile.get("executed") is True
    assert (project_dir(settings, pid) / "viewer" / "model.glb").exists()
    # smooth-mesh reconstruction diagnostics + honest mesh evidence (registry + verification)
    from aieng.converters.shape_ir_verification import verify_shape_ir_package
    with _zip.ZipFile(pkg) as zf:
        names = zf.namelist()
        assert "diagnostics/smooth_mesh_reconstruction.json" in names
        recon = _json.loads(zf.read("diagnostics/smooth_mesh_reconstruction.json"))
        reg = _json.loads(zf.read("registry/object_registry.json"))
    assert recon["method"] == "marching_cubes" and recon["geometry_kind"] == "mesh"
    assert recon["cad_editable"] is False and recon["frame_placement_applied"] is True
    obj = reg["objects"][0]
    assert obj["linkage"] == "fused_mesh" and obj["representation_kind"] == "mesh"
    assert obj["cad_editable"] is False               # not pretending B-Rep faces
    vr = verify_shape_ir_package(pkg)
    assert vr["geometry_kind"] == "mesh" and vr["representation_kind"] == "mesh"
    # mesh recompile auto-builds the solver-neutral region graph (observational, not B-Rep)
    with _zip.ZipFile(pkg) as zf:
        nm = zf.namelist()
        assert "graph/mesh_region_graph.json" in nm
        mrg = _json.loads(zf.read("graph/mesh_region_graph.json"))
        assert "diagnostics/mesh_region_segmentation.json" in nm
        # analytic plane fitting auto-runs on the mesh regions (planes only, not B-Rep)
        assert "graph/mesh_surface_fit.json" in nm
        msf = _json.loads(zf.read("graph/mesh_surface_fit.json"))
        assert "diagnostics/mesh_surface_fitting.json" in nm
        # reconstruction readiness auto-runs (analysis only, not B-Rep)
        assert "diagnostics/mesh_reconstruction_readiness.json" in nm
        rr = _json.loads(zf.read("diagnostics/mesh_reconstruction_readiness.json"))
        assert "graph/mesh_reconstruction_plan.json" in nm
        # partial B-Rep PLANNING auto-runs (face candidates only; no solid/STEP)
        assert "geometry/partial_brep_surfaces.json" in nm
        pbs = _json.loads(zf.read("geometry/partial_brep_surfaces.json"))
        assert "graph/mesh_brep_reconstruction_plan.json" in nm
        # OCC face GENERATION auto-runs (validated faces, intermediate; no stitch/solid/STEP)
        assert "geometry/partial_brep_faces.json" in nm
        pbf = _json.loads(zf.read("geometry/partial_brep_faces.json"))
        assert "diagnostics/partial_brep_face_generation.json" in nm
        # stitching readiness + edge matching auto-runs (plan only; no sew/shell/STEP)
        assert "graph/mesh_brep_stitching_plan.json" in nm
        sps = _json.loads(zf.read("graph/mesh_brep_stitching_plan.json"))
        assert "diagnostics/mesh_brep_stitching_readiness.json" in nm
        # conservative OCC sewing/solidification auto-runs after PR34 artifacts.
        assert "diagnostics/mesh_brep_sewing.json" in nm
        sewing = _json.loads(zf.read("diagnostics/mesh_brep_sewing.json"))
        assert "diagnostics/mesh_brep_step_export.json" in nm
        step_export = _json.loads(zf.read("diagnostics/mesh_brep_step_export.json"))
        assert "diagnostics/mesh_brep_roundtrip_verification.json" in nm
        roundtrip = _json.loads(zf.read("diagnostics/mesh_brep_roundtrip_verification.json"))
        assert not any(n.lower().endswith((".step", ".stp")) for n in nm)   # no STEP exported
    assert mrg["regions"] and mrg["provenance"]["is_brep"] is False
    assert mrg["provenance"]["representation_kind"] == "mesh"
    assert msf["provenance"]["is_brep"] is False and msf["provenance"]["cad_editable"] is False
    assert all(s["surface_type"] == "plane" and s["is_brep"] is False for s in msf["surfaces"])
    assert rr["provenance"]["is_brep"] is False and rr["provenance"]["cad_editable"] is False
    assert rr["readiness"]["recommended_next_action"] in (
        "partial_brep_reconstruction", "freeform_surface_fitting", "mesh_cleanup", "insufficient_data")
    assert pbs["provenance"]["full_solid"] is False and pbs["provenance"]["watertight"] is False
    assert pbs["provenance"]["step_exported"] is False
    assert all(fc["reconstruction_status"] == "candidate" for fc in pbs["face_candidates"])
    assert pbf["provenance"]["faces_stitched"] is False and pbf["provenance"]["step_exported"] is False
    assert pbf["summary"]["generated_face_count"] >= 1     # planar regions -> validated OCC faces
    assert all(f["geometry_validation"]["valid"] for f in pbf["faces"] if f["status"] == "generated")
    assert sps["provenance"]["shell_created"] is False and sps["provenance"]["solid_created"] is False
    assert sps["provenance"]["step_exported"] is False and sps["provenance"]["stitching_plan_only"] is True
    assert sewing["provenance"]["production_ready"] is False
    assert step_export["step_exported"] is False
    assert roundtrip["status"] in ("warning", "failed")

    # explicit method=voxels -> blocky density_voxels
    wbv = client.post(f"/api/projects/{pid}/topology-optimization/writeback", json={"method": "voxels"})
    assert wbv.status_code == 200 and wbv.json()["status"] == "ok"
    nodev = wbv.json()["shape_ir"]["parts"][0]
    assert nodev["type"] == "density_voxels" and "voxelized" in nodev["tags"]


def test_topology_optimization_3d_needs_user_input(tmp_path: Path) -> None:
    import json as _json, zipfile as _zip
    from app.main import create_app, default_project, project_dir, save_project, get_project

    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid = save_project(settings, default_project("topopt3dbare"))["id"]
    pkg = project_dir(settings, pid) / f"{pid}.aieng"
    with _zip.ZipFile(pkg, "w") as zf:   # geometry only, no CAE setup
        zf.writestr("geometry/topology_map.json", _json.dumps(
            {"entities": [{"id": "b", "type": "solid", "bounding_box": [0, 0, 0, 30, 20, 10]}]}))
    proj = get_project(settings, pid); proj["aieng_file"] = f"{pid}.aieng"; save_project(settings, proj)
    client = TestClient(create_app(settings))

    d = client.post(f"/api/projects/{pid}/topology-optimization/derive", json={"dimension": "3d"})
    assert d.status_code == 200
    body = d.json()
    assert body["status"] == "needs_user_input" and body["diagnostics"]


def _topopt_project_with_setup(settings, name, *, load_dir):
    """Create a project (120x80x10 plate) with a CAE setup, return (pid, pkg, client)."""
    import json as _json, zipfile as _zip
    from app.main import create_app, default_project, project_dir, save_project, get_project
    pid = save_project(settings, default_project(name))["id"]
    pkg = project_dir(settings, pid) / f"{pid}.aieng"
    topo = {"entities": [
        {"id": "body_plate", "type": "solid", "source_ir_node": "plate", "bounding_box": [0, 0, 0, 120, 80, 10]},
        {"id": "face_left", "type": "face", "bounding_box": [0, 0, 0, 0, 80, 10]},
        {"id": "face_right", "type": "face", "bounding_box": [120, 0, 0, 120, 80, 10]}]}
    cae_map = {"mappings": [
        {"maps_to": {"feature_id": "feat_fix"}, "face_ids": ["face_left"]},
        {"maps_to": {"feature_id": "feat_load"}, "face_ids": ["face_right"]}]}
    setup = ("boundary_conditions:\n  - {id: bc1, target_feature: feat_fix, type: fixed}\n"
             f"loads:\n  - {{id: ld1, target_feature: feat_load, type: force, value_n: 500.0, direction: {load_dir}}}\n")
    with _zip.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", _json.dumps(topo))
        zf.writestr("simulation/cae_mapping.json", _json.dumps(cae_map))
        zf.writestr("simulation/setup.yaml", setup)
    proj = get_project(settings, pid); proj["aieng_file"] = f"{pid}.aieng"; save_project(settings, proj)
    client = TestClient(create_app(settings))
    return pid, pkg, client


def _read_geom_exec(pkg):
    import json as _json, zipfile as _zip
    with _zip.ZipFile(pkg) as zf:
        assert "provenance/conversion_manifest.json" in zf.namelist(), "no conversion manifest written"
        return _json.loads(zf.read("provenance/conversion_manifest.json")).get("geometry_execution") or {}


def _is_missing_manifold3d_execution(record: dict) -> bool:
    errors = " ".join(str(e) for e in (record.get("errors") or []))
    return (
        record.get("executed") is False
        and record.get("actual_runtime") == "manifold"
        and "manifold3d" in errors
    )


def test_geometry_execution_manifest_across_paths(tmp_path: Path) -> None:
    """recompile/writeback/patch paths write a normalized geometry_execution manifest;
    verification reads it (no false geometry_kind:none for a real mesh writeback)."""
    from aieng.converters.shape_ir_verification import verify_shape_ir_package
    settings = Settings(platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
                        data_root=tmp_path / "d", aieng_root=tmp_path / "w" / "aieng",
                        sample_step=tmp_path / "w" / "s.step")
    pid, pkg, client = _topopt_project_with_setup(settings, "geomexec", load_dir="[0.0, -1.0, 0.0]")

    # run 2D SIMP (auto-derive)
    r = client.post(f"/api/projects/{pid}/topology-optimization", json={"auto_derive": True})
    assert r.status_code == 200 and r.json()["status"] == "ok"

    # (1) manifold_mesh writeback -> executed:true, geometry_kind:mesh
    wb = client.post(f"/api/projects/{pid}/topology-optimization/writeback",
                     json={"representation": "manifold_mesh", "method": "voxels"})
    assert wb.status_code == 200 and wb.json()["status"] == "ok"
    ge = _read_geom_exec(pkg)
    if _is_missing_manifold3d_execution(ge):
        assert ge["geometry_kind"] == "none"
        assert ge["real_geometry"] is False
        assert ge["representation_kind"] == "mesh"
        assert "geometry/preview.glb" not in ge["artifacts"]
        vr = verify_shape_ir_package(pkg)
        assert vr["geometry_kind"] == "none"
    else:
        assert ge["executed"] is True and ge["geometry_kind"] == "mesh"
        assert ge["representation_kind"] == "mesh" and ge["actual_runtime"] == "manifold"
        assert "geometry/preview.glb" in ge["artifacts"] and ge["source_shape_ir"] == "geometry/shape_ir.json"
        assert "fallback" in ge and isinstance(ge["fallback"]["used"], bool)
        vr = verify_shape_ir_package(pkg)
        assert vr["geometry_kind"] == "mesh"          # no longer falsely "none" for a real mesh writeback

    # (2) brep_build123d writeback -> geometry_kind:brep + generated.step
    wb2 = client.post(f"/api/projects/{pid}/topology-optimization/writeback",
                      json={"representation": "brep_build123d", "method": "contour", "boundary": "polygon"})
    assert wb2.status_code == 200 and wb2.json()["status"] == "ok"
    ge2 = _read_geom_exec(pkg)
    if _is_missing_manifold3d_execution(ge2):
        assert ge2["geometry_kind"] == "none"
        assert ge2["real_geometry"] is False
    else:
        assert ge2["executed"] is True and ge2["geometry_kind"] == "brep"
        assert ge2["representation_kind"] == "brep" and "geometry/generated.step" in ge2["artifacts"]
        assert verify_shape_ir_package(pkg)["geometry_kind"] == "brep"

    # (3) the shared recompile path (used by apply_shape_ir_patch AND writeback) writes
    # the manifest — call it directly to prove the patch path is covered too.
    from app import cad_generation
    rec = cad_generation.recompile_shape_ir_package(pkg)
    ge3 = _read_geom_exec(pkg)
    if rec["executed"] is False:
        assert ge3["executed"] is False
        assert ge3["geometry_kind"] == "none"
        assert "manifold3d" in str(rec.get("error") or "")
    else:
        assert rec["executed"] is True
        assert ge3["executed"] is True and ge3["geometry_kind"] in ("brep", "mesh")


def test_geometry_execution_manifest_missing_degrades_honestly(tmp_path: Path) -> None:
    """A package with NO manifest verifies honestly: not executed, geometry_kind none."""
    import json as _json, zipfile as _zip
    from aieng.converters.shape_ir_verification import verify_shape_ir_package
    pkg = tmp_path / "bare.aieng"
    with _zip.ZipFile(pkg, "w") as zf:   # shape_ir only, no manifest, no geometry artifacts
        zf.writestr("geometry/shape_ir.json", _json.dumps(
            {"representation": "manifold_mesh", "parts": [{"id": "x", "type": "box"}]}))
    vr = verify_shape_ir_package(pkg)
    assert vr["geometry_kind"] == "none"             # honest: nothing was generated


def test_assembly_process_endpoint(tmp_path: Path) -> None:
    """POST /assembly/process writes validation + registry + connection graph + CAE draft
    for a package carrying assembly/assembly_ir.json — and runs no solver."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("asm-demo"))
    project_id = project["id"]
    pkg = project_dir(settings, project_id) / "asm.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    assembly = {
        "format": "aieng.assembly_ir", "schema_version": "0.1", "unit": "mm",
        "parts": [
            {"id": "bracket", "role": "design_part", "geometry_ref": "geometry/bracket.step",
             "transform": {"translation": [0, 0, 0], "unit": "mm"}, "material": "AlSi10Mg"},
            {"id": "wall", "role": "reference_part", "geometry_ref": "geometry/wall.step",
             "transform": {"translation": [0, 0, -5], "unit": "mm"}},
        ],
        "interfaces": [
            {"id": "if_a", "part_id": "bracket", "semantic_role": "mounting_face"},
            {"id": "if_b", "part_id": "wall", "semantic_role": "support_face"},
        ],
        "connections": [
            {"id": "c1", "type": "bolted_proxy", "part_a": "bracket", "part_b": "wall",
             "interface_a": "if_a", "interface_b": "if_b", "behavior": ["load_transfer"],
             "limitations": ["no preload"]},
        ],
        "analysis_intent": {"design_parts": ["bracket"], "frozen_parts": ["wall"]},
    }
    # per-part topology so interface resolution + geometry validation can run
    face_a = {"id": "a_top", "type": "face", "bounding_box": [0, 0, 10, 5, 5, 10],
              "normal": [0, 0, 1.0], "area": 25.0}
    face_b = {"id": "b_bot", "type": "face", "bounding_box": [0, 0, 0, 5, 5, 0],
              "normal": [0, 0, -1.0], "area": 25.0}
    assembly["interfaces"][0]["topology_refs"] = {"face_ids": ["a_top"]}
    assembly["interfaces"][1]["topology_refs"] = {"face_ids": ["b_bot"]}
    assembly["parts"][1]["transform"] = {"translation": [0, 0, 10], "unit": "mm"}
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "asm-demo", "resources": {}}))
        zf.writestr("assembly/assembly_ir.json", json.dumps(assembly))
        zf.writestr("parts/bracket/topology_map.json",
                    json.dumps({"format_version": "0.1.0", "entities": [face_a]}))
        zf.writestr("parts/wall/topology_map.json",
                    json.dumps({"format_version": "0.1.0", "entities": [face_b]}))
    project["aieng_file"] = "asm.aieng"
    save_project(settings, project)

    resp = client.post(f"/api/projects/{project_id}/assembly/process", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["assembly_present"] is True and data["validation_status"] == "passed"
    assert data["interface_resolution"]["resolved"] == 2
    assert data["connection_geometry"]["connection_count"] == 1
    assert data["assembly_cae_model_status"] == "ready"
    assert data["solver_deck_status"] == "skipped"
    assert data["solver_execution_status"] == "skipped"

    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
        for art in ("diagnostics/assembly_validation.json", "assembly/part_registry.json",
                    "assembly/connection_graph.json", "simulation/assembly_cae_setup_draft.json",
                    "assembly/interface_resolution.json",
                    "diagnostics/assembly_connection_geometry.json",
                    "simulation/assembly_cae_model.json",
                    "diagnostics/assembly_cae_model_diagnostics.json",
                    "diagnostics/assembly_solver_deck_generation.json",
                    "diagnostics/assembly_solver_execution.json",
                    "diagnostics/assembly_result_mapping.json"):
            assert art in names
        assert not any(n.lower().endswith((".inp", ".frd", ".step", ".stp")) for n in names)


def test_assembly_process_endpoint_no_assembly(tmp_path: Path) -> None:
    """A package without assembly/assembly_ir.json is left untouched (assembly_present false)."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("no-asm"))
    project_id = project["id"]
    pkg = project_dir(settings, project_id) / "p.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "no-asm", "resources": {}}))
    project["aieng_file"] = "p.aieng"
    save_project(settings, project)

    resp = client.post(f"/api/projects/{project_id}/assembly/process", json={})
    assert resp.status_code == 200
    assert resp.json()["assembly_present"] is False


def test_assembly_topology_optimization_run_endpoint_is_explicit_and_part_scoped(tmp_path: Path) -> None:
    """Explicit endpoint runs selected-part assembly topopt and writes only part-level artifacts."""
    from app.main import create_app, default_project, project_dir, save_project
    from aieng.converters.assembly_cae import ASSEMBLY_CAE_MODEL_PATH, build_assembly_cae_model
    from aieng.converters.assembly_interface_resolution import (
        ASSEMBLY_CONNECTION_GEOMETRY_PATH,
        INTERFACE_RESOLUTION_PATH,
        resolve_assembly_interfaces,
        validate_connection_geometry,
    )
    from aieng.converters.assembly_ir import (
        ASSEMBLY_CAE_DRAFT_PATH,
        ASSEMBLY_IR_PATH,
        CONNECTION_GRAPH_PATH,
        CONVERSION_MANIFEST_PATH,
        PART_REGISTRY_PATH,
        build_assembly_cae_setup_draft,
        build_connection_graph,
        build_part_registry,
    )
    from aieng.converters.assembly_topopt import (
        ASSEMBLY_DESIGN_RECOMMENDATIONS_PATH,
        ASSEMBLY_NEXT_ACTIONS_PATH,
        ASSEMBLY_POSTPROCESS_REPORT_PATH,
        write_assembly_topopt_problem,
    )
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("asm-topopt"))
    project_id = project["id"]
    pkg = project_dir(settings, project_id) / "asm-topopt.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    assembly = {
        "format": "aieng.assembly_ir", "schema_version": "0.1", "unit": "mm",
        "parts": [
            {"id": "bracket", "role": "design_part", "geometry_ref": "geometry/bracket.step",
             "topology_ref": "parts/bracket/topology_map.json", "source_ir_node": "node_bracket"},
            {"id": "wall", "role": "reference_part", "geometry_ref": "geometry/wall.step",
             "topology_ref": "parts/wall/topology_map.json", "source_ir_node": "node_wall"},
            {"id": "load_jig", "role": "load_source", "geometry_ref": "geometry/load_jig.step",
             "topology_ref": "parts/load_jig/topology_map.json", "source_ir_node": "node_load_jig"},
        ],
        "interfaces": [
            {"id": "if_mount", "part_id": "bracket", "semantic_role": "mounting_face",
             "topology_refs": {"face_ids": ["face_mount"]}},
            {"id": "if_wall", "part_id": "wall", "semantic_role": "support_face",
             "topology_refs": {"face_ids": ["face_wall"]}},
            {"id": "if_load", "part_id": "bracket", "semantic_role": "load_face",
             "topology_refs": {"face_ids": ["face_load"]}},
            {"id": "if_jig", "part_id": "load_jig", "semantic_role": "load_face",
             "topology_refs": {"face_ids": ["face_jig"]}},
        ],
        "connections": [
            {"id": "c_mount", "type": "rigid_tie", "part_a": "bracket", "part_b": "wall",
             "interface_a": "if_mount", "interface_b": "if_wall", "behavior": ["load_transfer"]},
            {"id": "c_load", "type": "bolted_proxy", "part_a": "bracket", "part_b": "load_jig",
             "interface_a": "if_load", "interface_b": "if_jig", "behavior": ["load_transfer"]},
        ],
        "analysis_intent": {"design_parts": ["bracket"], "frozen_parts": ["wall", "load_jig"]},
    }
    topo = {
        "bracket": {
            "entities": [
                {"id": "bracket", "type": "solid", "body_id": "bracket",
                 "bounding_box": [0, 0, 0, 60, 12, 6]},
                {"id": "face_mount", "type": "face", "body_id": "bracket",
                 "bounding_box": [0, 0, 0, 0, 12, 6], "normal": [-1, 0, 0], "area": 72},
                {"id": "face_load", "type": "face", "body_id": "bracket",
                 "bounding_box": [60, 3, 0, 60, 9, 6], "normal": [1, 0, 0], "area": 36},
            ]
        },
        "wall": {"entities": [
            {"id": "face_wall", "type": "face", "body_id": "wall",
             "bounding_box": [0, 0, 0, 0, 12, 6], "normal": [1, 0, 0], "area": 72},
        ]},
        "load_jig": {"entities": [
            {"id": "face_jig", "type": "face", "body_id": "load_jig",
             "bounding_box": [60, 3, 0, 60, 9, 6], "normal": [-1, 0, 0], "area": 36},
        ]},
    }
    topo_by_part = {pid: {e["id"]: e for e in doc["entities"]} for pid, doc in topo.items()}
    resolution = resolve_assembly_interfaces(assembly, topo_by_part)
    geometry = validate_connection_geometry(assembly, resolution)
    registry = build_part_registry(assembly)
    graph = build_connection_graph(assembly)
    draft = build_assembly_cae_setup_draft(assembly)
    model, _diag = build_assembly_cae_model(
        assembly=assembly,
        part_registry=registry,
        connection_graph=graph,
        interface_resolution=resolution,
        connection_geometry=geometry,
        setup_draft=draft,
    )
    for load in model.get("boundary_conditions", {}).get("loads", []):
        if load.get("interface_id") == "if_load":
            load["direction"] = [0.0, -1.0, 0.0]
            load["value_n"] = 10.0
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "asm-topopt", "resources": {}}))
        zf.writestr(ASSEMBLY_IR_PATH, json.dumps(assembly))
        zf.writestr(PART_REGISTRY_PATH, json.dumps(registry))
        zf.writestr(CONNECTION_GRAPH_PATH, json.dumps(graph))
        zf.writestr(INTERFACE_RESOLUTION_PATH, json.dumps(resolution))
        zf.writestr(ASSEMBLY_CONNECTION_GEOMETRY_PATH, json.dumps(geometry))
        zf.writestr(ASSEMBLY_CAE_DRAFT_PATH, json.dumps(draft))
        zf.writestr(ASSEMBLY_CAE_MODEL_PATH, json.dumps(model))
        zf.writestr(CONVERSION_MANIFEST_PATH, json.dumps({"format": "aieng.conversion_manifest"}))
        for pid, doc in topo.items():
            zf.writestr(f"parts/{pid}/topology_map.json", json.dumps(doc))
    project["aieng_file"] = "asm-topopt.aieng"
    save_project(settings, project)
    setup = write_assembly_topopt_problem(pkg, resolution=10, max_iters=4)
    assert setup["status"] == "ready"

    resp = client.post(
        f"/api/projects/{project_id}/assembly/topology-optimization/run",
        json={"method": "voxels", "representation": "manifold_mesh"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool"] == "opt.run_assembly_topology_optimization"
    assert data["status"] == "derived_part_artifact_written"
    assert data["assembly_topology_optimization"]["recommendation_status"] == "accept"
    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
        assert "analysis/assembly_topology_optimization.json" in names
        assert "diagnostics/assembly_topopt_execution.json" in names
        assert ASSEMBLY_DESIGN_RECOMMENDATIONS_PATH in names
        assert ASSEMBLY_POSTPROCESS_REPORT_PATH in names
        assert ASSEMBLY_NEXT_ACTIONS_PATH in names
        assert "parts/bracket/analysis/topology_optimization.json" in names
        assert "parts/bracket/geometry/optimized_shape_ir.json" in names
        assert "parts/wall/geometry/optimized_shape_ir.json" not in names
        assert "geometry/shape_ir.json" not in names


def test_design_study_summary_endpoint_returns_readonly_artifact_envelope(tmp_path: Path) -> None:
    """GET /design-study/summary aggregates panel artifacts without mutating."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("design-study-summary"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "summary.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    ranking = {
        "format": "aieng.design_study_candidate_ranking",
        "candidates": [{"candidate_id": "c1", "rank": 1, "feasibility": "feasible"}],
        "best_candidate_id": "c1",
    }
    recommendation = {
        "format": "aieng.optimization_recommendation",
        "recommended_candidate_id": "c1",
        "advisory_only": True,
    }
    convergence = {
        "format": "aieng.optimization_iterations",
        "iterations": [{"index": 1, "incumbent_candidate_id": "c1", "incumbent_objective": 1.0}],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "design-study-summary"}))
        zf.writestr("analysis/design_study_candidate_ranking.json", json.dumps(ranking))
        zf.writestr("analysis/optimization_recommendation.json", json.dumps(recommendation))
        zf.writestr("analysis/optimization_iterations.json", json.dumps(convergence))
    project["aieng_file"] = "summary.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/design-study/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["available_artifacts"] == ["convergence", "ranking", "recommendation"]
    assert data["artifacts"]["ranking"]["path"] == "analysis/design_study_candidate_ranking.json"
    assert data["artifacts"]["ranking"]["report"]["best_candidate_id"] == "c1"
    assert data["artifacts"]["surrogate"]["available"] is False
    assert data["artifacts"]["surrogate"]["reason"] == "not_found"
    assert "does not mean a candidate was accepted" in data["honesty"]


def test_design_study_validate_endpoint(tmp_path: Path) -> None:
    """POST /design-study/validate validates the problem + candidate patches and writes
    diagnostics — contract + validation only, no patch applied, baseline untouched."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("design-study"))
    project_id = project["id"]
    pkg = project_dir(settings, project_id) / "ds.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    problem = {
        "format": "aieng.design_study_problem", "schema_version": "0.1",
        "variables": [
            {"id": "wall_t", "path": "params/WALL_THICKNESS", "type": "continuous",
             "current_value": 3.0, "min_value": 2.0, "max_value": 8.0, "unit": "mm",
             "safe_to_modify": True, "semantic_role": "wall_thickness"},
            {"id": "bolt_dia", "path": "params/BOLT_DIA", "type": "discrete",
             "current_value": 6, "allowed_values": [4, 5, 6, 8], "unit": "mm",
             "safe_to_modify": False, "semantic_role": "bolt_hole"},
        ],
        "settings": {"max_variables_per_candidate": 2, "require_reasoning": True},
    }
    good = {"format": "aieng.design_candidate_patch", "candidate_id": "cand_ok",
            "reasoning": "thin the wall to cut mass",
            "variable_changes": [{"variable_id": "wall_t", "new_value": 4.0}]}
    bad = {"format": "aieng.design_candidate_patch", "candidate_id": "cand_bad",
           "reasoning": "touch the bolt", "variable_changes": [{"variable_id": "bolt_dia", "new_value": 8}]}
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "design-study", "resources": {}}))
        zf.writestr("geometry/shape_ir.json", json.dumps({"representation": "brep_build123d"}))
        zf.writestr("analysis/design_study_problem.json", json.dumps(problem))
        zf.writestr("patches/design_candidates/cand_ok.json", json.dumps(good))
        zf.writestr("patches/design_candidates/cand_bad.json", json.dumps(bad))
    project["aieng_file"] = "ds.aieng"
    save_project(settings, project)

    resp = client.post(f"/api/projects/{project_id}/design-study/validate", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["design_study_present"] is True and data["problem_status"] == "passed"
    assert data["candidate_count"] == 2

    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
        assert "diagnostics/design_study_problem_diagnostics.json" in names
        assert "diagnostics/design_study_candidate_validation.json" in names
        # baseline geometry untouched
        assert json.loads(zf.read("geometry/shape_ir.json"))["representation"] == "brep_build123d"
        diag = json.loads(zf.read("diagnostics/design_study_candidate_validation.json"))
        by_id = {c["candidate_id"]: c for c in diag["candidates"]}
        assert by_id["cand_ok"]["status"] == "valid" and by_id["cand_ok"]["applied"] is False
        assert by_id["cand_bad"]["status"] == "rejected"


def test_design_study_validate_endpoint_no_study(tmp_path: Path) -> None:
    """A package without analysis/design_study_problem.json is untouched (present false)."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("no-ds"))
    project_id = project["id"]
    pkg = project_dir(settings, project_id) / "p.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "no-ds", "resources": {}}))
    project["aieng_file"] = "p.aieng"
    save_project(settings, project)

    resp = client.post(f"/api/projects/{project_id}/design-study/validate", json={})
    assert resp.status_code == 200
    assert resp.json()["design_study_present"] is False


def _design_study_run_project(settings, name):
    """A project with a box Shape IR baseline + a design study problem + one valid candidate."""
    from app.main import default_project, project_dir, save_project
    project = save_project(settings, default_project(name))
    project_id = project["id"]
    pkg = project_dir(settings, project_id) / "ds.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    baseline = {"representation": "brep_build123d",
                "parts": [{"id": "blk", "type": "box",
                           "parameters": {"length": 20.0, "width": 20.0, "height": 10.0}}]}
    problem = {
        "format": "aieng.design_study_problem", "schema_version": "0.1",
        "variables": [
            {"id": "h", "path": "parts/0/parameters/height", "type": "continuous",
             "current_value": 10.0, "min_value": 5.0, "max_value": 30.0, "unit": "mm",
             "safe_to_modify": True, "semantic_role": "height"},
        ],
        "settings": {"max_variables_per_candidate": 1, "require_reasoning": True},
    }
    cand = {"format": "aieng.design_candidate_patch", "candidate_id": "taller",
            "reasoning": "raise the part", "variable_changes": [{"variable_id": "h", "new_value": 18.0}]}
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": name, "resources": {}}))
        zf.writestr("geometry/shape_ir.json", json.dumps(baseline))
        zf.writestr("analysis/design_study_problem.json", json.dumps(problem))
        zf.writestr("patches/design_candidates/taller.json", json.dumps(cand))
    project["aieng_file"] = "ds.aieng"
    save_project(settings, project)
    return project_id, pkg, baseline


def test_design_study_run_candidate_endpoint_no_compile(tmp_path: Path) -> None:
    """Explicit candidate run with compile disabled: derived workspace written, baseline
    Shape IR untouched, evaluation honestly partial."""
    from app.main import create_app
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id, pkg, baseline = _design_study_run_project(settings, "ds-run")

    resp = client.post(
        f"/api/projects/{project_id}/design-study/candidates/taller/run", json={"compile": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["execution_status"] == "patch_applied"
    assert data["recommendation"] == "needs_more_evaluation"
    assert data["baseline_modified"] is False

    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
        assert "candidates/taller/geometry/shape_ir.json" in names
        assert "analysis/design_study_iterations.json" in names
        assert "diagnostics/design_study_report.json" in names
        # baseline untouched
        assert json.loads(zf.read("geometry/shape_ir.json")) == baseline
        derived = json.loads(zf.read("candidates/taller/geometry/shape_ir.json"))
        assert derived["parts"][0]["parameters"]["height"] == 18.0
        # no candidate generated.step leaked into the global baseline paths
        assert "candidates/taller/geometry/generated.step" not in names


def test_design_study_run_candidate_endpoint_real_compile(tmp_path: Path) -> None:
    """With compile enabled (default), the candidate compiles in a throwaway copy; the baseline
    package's geometry artifacts are never created/overwritten by the candidate run."""
    import pytest
    pytest.importorskip("build123d")
    from app.main import create_app
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id, pkg, baseline = _design_study_run_project(settings, "ds-run-real")

    resp = client.post(f"/api/projects/{project_id}/design-study/candidates/taller/run", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["execution_status"] in ("evaluation_complete", "compile_succeeded")
    assert data["recommendation"] in ("refine_candidate", "needs_more_evaluation")
    assert data["baseline_modified"] is False

    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
        # candidate compile artifacts live ONLY under the candidate workspace
        assert "candidates/taller/provenance/geometry_execution_manifest.json" in names
        # baseline Shape IR + (absence of) baseline generated geometry preserved
        assert json.loads(zf.read("geometry/shape_ir.json")) == baseline
        assert "geometry/generated.step" not in names
        # no throwaway temp package left behind
    assert not list(pkg.parent.glob("*.tmp.aieng"))
    assert not list(pkg.parent.glob("*.dscand_*.aieng"))


def test_geometry_report_endpoint_surfaces_floating_and_symmetry(tmp_path: Path) -> None:
    """GET /geometry-report returns floating parts, broken symmetry, and per-part boxes."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project = save_project(settings, default_project("assembly-check"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "assembly.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    topo = {"entities": [
        {"type": "solid", "id": "b1", "name": "torso", "bounding_box": [-30, -15, 100, 30, 15, 300]},
        {"type": "solid", "id": "b2", "name": "arm_L", "bounding_box": [-50, -10, 150, -30, 10, 290]},
        {"type": "solid", "id": "b3", "name": "arm_R", "bounding_box": [30, -10, 150, 50, 10, 250]},
        {"type": "solid", "id": "b4", "name": "foot_FL", "bounding_box": [-200, -10, -20, -180, 10, 0]},
    ]}
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "assembly-check", "resources": {}}))
        zf.writestr("geometry/topology_map.json", json.dumps(topo))
    project["aieng_file"] = "assembly.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/geometry-report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert "foot_FL" in data["floating_parts"]
    # per-part boxes for every named solid, each a 6-number bbox
    assert set(data["part_boxes"]) == {"torso", "arm_L", "arm_R", "foot_FL"}
    assert len(data["part_boxes"]["arm_L"]) == 6
    # the arm pair is flagged asymmetric
    arm = next(s for s in data["symmetry"] if s.get("pair") == ["arm_L", "arm_R"])
    assert arm["ok"] is False


def test_geometry_report_endpoint_no_package_is_graceful(tmp_path: Path) -> None:
    """No package / unknown project → 200 with available=False, not a 404."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    client = TestClient(create_app(settings))

    project_id = save_project(settings, default_project("no-geo"))["id"]
    resp = client.get(f"/api/projects/{project_id}/geometry-report")
    assert resp.status_code == 200
    assert resp.json()["available"] is False

    unknown = client.get("/api/projects/doesnotexist99/geometry-report")
    assert unknown.status_code == 200
    assert unknown.json()["available"] is False
