"""Tests for structured parametric edit proposals (#432)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings

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


def _make_project(settings: Settings, name: str) -> str:
    from app.main import default_project, save_project

    project = save_project(settings, default_project(name))
    return project["id"]


def _build_bracket(settings: Settings, project_id: str) -> dict[str, Any]:
    from app.cad_generation import execute_build123d_code

    code = (
        "from build123d import *\n"
        "PLATE_LENGTH = 120\n"
        "PLATE_WIDTH = 80\n"
        "PLATE_THICKNESS = 8\n"
        "HOLE_RADIUS = 5\n"
        "HOLE_COUNT = 4\n"
        "with BuildPart() as bp:\n"
        "    Box(PLATE_LENGTH, PLATE_WIDTH, PLATE_THICKNESS)\n"
        "    with PolarLocations(35, HOLE_COUNT):\n"
        "        Hole(radius=HOLE_RADIUS, depth=PLATE_THICKNESS + 1)\n"
        "body = bp.part\n"
        "body.label = 'base_plate'\n"
        "result = Compound(children=[body])\n"
    )
    result = execute_build123d_code(settings, project_id, {"code": code, "thumbnail": False})
    assert result["status"] == "ok", result
    return result


def _find_parameter(feature_graph: dict[str, Any], cad_name: str) -> tuple[str, str]:
    for feature in feature_graph.get("features", []):
        for param_name, param in (feature.get("parameters") or {}).items():
            if isinstance(param, dict) and param.get("cad_parameter_name") == cad_name:
                return feature["id"], param_name
    raise AssertionError(f"parameter {cad_name} not found")


def test_propose_parametric_edit_returns_structured_proposal(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code
    from app.parametric_edit_proposal import propose_parametric_edit

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "proposal-test")
    result = execute_build123d_code(
        settings,
        pid,
        {
            "code": (
                "from build123d import *\n"
                "BODY_LENGTH = 120\n"
                "body = Box(BODY_LENGTH, 80, 8); body.label = 'base_plate'\n"
                "result = Compound(children=[body])\n"
            ),
            "thumbnail": False,
        },
    )
    assert result["status"] == "ok"
    feature_id, parameter_name = _find_parameter(result["feature_graph"], "BODY_LENGTH")

    proposal = propose_parametric_edit(
        settings, pid, feature_id=feature_id, parameter_name=parameter_name, new_value=200
    )

    assert proposal["status"] == "ok"
    assert proposal["proposal_id"].startswith("pep_")
    assert proposal["approval_required"] is True
    assert proposal["target"]["feature_id"] == feature_id
    assert proposal["target"]["parameter_name"] == parameter_name
    assert proposal["target"]["cad_parameter_name"] == "BODY_LENGTH"
    assert proposal["target"]["pointer"] == f"@feature:{feature_id}"
    assert proposal["change"]["old_value"] == 120
    assert proposal["change"]["new_value"] == 200
    assert proposal["change"]["unit"] == "mm"
    assert proposal["scope"] == "local"
    assert proposal["scope_risk"] is None
    assert isinstance(proposal["risks"]["protected_features"], list)
    assert isinstance(proposal["risks"]["design_target_impacts"], list)
    # Preview should include geometry diff information.
    assert proposal["preview"]["status"] == "ok"
    assert proposal["preview"]["regression_diff"]["verdict"] in ("clean", "topology_changed")
    # Expected impact is honest — no fabricated solver evidence.
    assert proposal["expected_impact"]["stress"]["status"] == "unknown"
    mass_note = proposal["expected_impact"]["mass"]["note"].lower()
    assert "recompute" in mass_note or "proxy" in mass_note
    # Package must not be mutated by the proposal alone.
    from app.project_io import get_project, resolve_project_path

    pkg_path = resolve_project_path(settings, pid, get_project(settings, pid).get("aieng_file"))
    with zipfile.ZipFile(pkg_path, "r") as zf:
        source = zf.read("geometry/source.py").decode("utf-8")
    assert "BODY_LENGTH = 120" in source
    assert "BODY_LENGTH = 200" not in source


def test_propose_parametric_edit_detects_protected_feature(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.parametric_edit_proposal import propose_parametric_edit

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "protected-test")
    result = _build_bracket(settings, pid)
    feature_id, parameter_name = _find_parameter(result["feature_graph"], "HOLE_RADIUS")

    proposal = propose_parametric_edit(
        settings, pid, feature_id=feature_id, parameter_name=parameter_name, new_value=8
    )

    assert proposal["status"] == "ok"
    risks = proposal["risks"]["protected_features"]
    assert any("hole" in r.get("message", "").lower() for r in risks)


def test_propose_parametric_edit_global_scope_requires_confirmation(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.parametric_edit_proposal import propose_parametric_edit

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "global-test")
    code = (
        "from build123d import *\n"
        "GEAR_IN_DIA = 30\n"
        "GEAR_OUT_DIA = 50\n"
        "GEAR_WIDTH = 8\n"
        "gi = Cylinder(GEAR_IN_DIA / 2, GEAR_WIDTH); gi.label = 'gear_input'\n"
        "go = Cylinder(GEAR_OUT_DIA / 2, GEAR_WIDTH); go.label = 'gear_output'\n"
        "result = Compound(children=[gi, go])\n"
    )
    from app.cad_generation import execute_build123d_code

    result = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert result["status"] == "ok"
    global_feat = next(f for f in result["feature_graph"]["features"] if f.get("type") == "global_params")
    gear_width_param_name = next(
        name
        for name, p in global_feat["parameters"].items()
        if isinstance(p, dict) and p.get("cad_parameter_name") == "GEAR_WIDTH"
    )

    proposal = propose_parametric_edit(
        settings, pid, feature_id=global_feat["id"], parameter_name=gear_width_param_name, new_value=10
    )

    assert proposal["status"] == "ok"
    assert proposal["scope"] == "global"
    assert proposal["scope_risk"]["scope"] == "global"
    assert proposal["next_action"]["input"]["confirmScopeRisk"] is True


def test_edit_parameter_records_pre_edit_snapshot_and_stale_evidence(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code
    from app.project_io import _read_revalidation_status, get_project, resolve_project_path

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "snapshot-stale-test")
    result = execute_build123d_code(
        settings,
        pid,
        {
            "code": (
                "from build123d import *\n"
                "BODY_LENGTH = 120\n"
                "body = Box(BODY_LENGTH, 80, 8); body.label = 'base_plate'\n"
                "result = Compound(children=[body])\n"
            ),
            "thumbnail": False,
        },
    )
    assert result["status"] == "ok"
    feature_id, parameter_name = _find_parameter(result["feature_graph"], "BODY_LENGTH")

    pkg_path = resolve_project_path(settings, pid, get_project(settings, pid).get("aieng_file"))

    # Simulate a prior solver validation so we can observe staleness.
    from app.project_io import _record_solver_validation_in_package

    _record_solver_validation_in_package(pkg_path, run_id="run_prior")
    prior_status = _read_revalidation_status(pkg_path)
    assert prior_status is not None
    assert prior_status.get("requires_revalidation") is False

    edited = edit_build123d_parameter(
        settings,
        pid,
        feature_id=feature_id,
        parameter_name=parameter_name,
        new_value=200,
        thumbnail=False,
    )
    assert edited["status"] == "ok"
    assert edited.get("snapshot_id", "").startswith("snap_")
    assert edited.get("proposal_id") is None

    # Downstream CAE evidence is marked stale with structured audit fields.
    status = _read_revalidation_status(pkg_path)
    assert status is not None
    assert status["requires_revalidation"] is True
    assert status["geometry_modified"] is True
    assert status["triggering_tool"] == "cad.edit_parameter"
    assert "results/computed_metrics.json" in status["affected_artifacts"]
    assert status["current_geometry_revision"] is not None
    assert status["stale_since_geometry_revision"] is not None

    # Audit log records the accepted edit.
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "audit_log.jsonl" in zf.namelist()
        log_lines = zf.read("audit_log.jsonl").decode("utf-8").strip().splitlines()
    audit_entries = [json.loads(line) for line in log_lines]
    edit_entries = [e for e in audit_entries if e.get("tool") == "cad.edit_parameter"]
    assert len(edit_entries) >= 1
    assert edit_entries[-1]["action"] == "accepted_parametric_edit"
    assert edit_entries[-1]["previous_value"] == 120
    assert edit_entries[-1]["new_value"] == 200
    assert edit_entries[-1]["snapshot_id"] == edited["snapshot_id"]


def test_edit_parameter_with_proposal_id_links_to_proposal(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code
    from app.parametric_edit_proposal import propose_parametric_edit, save_parametric_edit_proposal

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "proposal-link-test")
    result = execute_build123d_code(
        settings,
        pid,
        {
            "code": (
                "from build123d import *\n"
                "BODY_LENGTH = 120\n"
                "body = Box(BODY_LENGTH, 80, 8); body.label = 'base_plate'\n"
                "result = Compound(children=[body])\n"
            ),
            "thumbnail": False,
        },
    )
    assert result["status"] == "ok"
    feature_id, parameter_name = _find_parameter(result["feature_graph"], "BODY_LENGTH")
    proposal = propose_parametric_edit(
        settings,
        pid,
        feature_id=feature_id,
        parameter_name=parameter_name,
        new_value=200,
    )
    assert proposal["status"] == "ok"
    proposal = save_parametric_edit_proposal(settings, pid, proposal)

    edited = edit_build123d_parameter(
        settings,
        pid,
        feature_id=feature_id,
        parameter_name=parameter_name,
        new_value=200,
        thumbnail=False,
        proposal_id=proposal["proposal_id"],
    )
    assert edited["status"] == "ok"
    assert edited["proposal_id"] == proposal["proposal_id"]


def test_stale_proposal_is_rejected_without_mutating_package(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code
    from app.parametric_edit_proposal import propose_parametric_edit, save_parametric_edit_proposal
    from app.project_io import get_project, resolve_project_path

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "stale-proposal-test")
    result = execute_build123d_code(
        settings,
        pid,
        {
            "code": (
                "from build123d import *\n"
                "BODY_LENGTH = 120\n"
                "body = Box(BODY_LENGTH, 80, 8); body.label = 'base_plate'\n"
                "result = Compound(children=[body])\n"
            ),
            "thumbnail": False,
        },
    )
    assert result["status"] == "ok"
    feature_id, parameter_name = _find_parameter(result["feature_graph"], "BODY_LENGTH")

    proposal = propose_parametric_edit(
        settings,
        pid,
        feature_id=feature_id,
        parameter_name=parameter_name,
        new_value=200,
    )
    assert proposal["status"] == "ok"
    proposal = save_parametric_edit_proposal(settings, pid, proposal)

    intervening_edit = edit_build123d_parameter(
        settings,
        pid,
        feature_id=feature_id,
        parameter_name=parameter_name,
        new_value=150,
        thumbnail=False,
    )
    assert intervening_edit["status"] == "ok"

    stale = edit_build123d_parameter(
        settings,
        pid,
        feature_id=feature_id,
        parameter_name=parameter_name,
        new_value=200,
        thumbnail=False,
        proposal_id=proposal["proposal_id"],
    )
    assert stale["status"] == "error"
    assert stale["code"] == "stale_parametric_edit_proposal"
    assert stale["proposal_old_value"] == 120
    assert stale["current_value"] == 150

    pkg_path = resolve_project_path(settings, pid, get_project(settings, pid).get("aieng_file"))
    with zipfile.ZipFile(pkg_path, "r") as zf:
        source_after = zf.read("geometry/source.py").decode("utf-8")
    assert "BODY_LENGTH = 150" in source_after
    assert "BODY_LENGTH = 200" not in source_after


def test_rejected_edit_preserves_package_state(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code
    from app.project_io import get_project, resolve_project_path

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "rejected-test")
    result = execute_build123d_code(
        settings,
        pid,
        {
            "code": (
                "from build123d import *\n"
                "BODY_LENGTH = 120\n"
                "body = Box(BODY_LENGTH, 80, 8); body.label = 'base_plate'\n"
                "result = Compound(children=[body])\n"
            ),
            "thumbnail": False,
        },
    )
    assert result["status"] == "ok"
    feature_id, parameter_name = _find_parameter(result["feature_graph"], "BODY_LENGTH")

    pkg_path = resolve_project_path(settings, pid, get_project(settings, pid).get("aieng_file"))
    with zipfile.ZipFile(pkg_path, "r") as zf:
        source_before = zf.read("geometry/source.py").decode("utf-8")

    rejected = edit_build123d_parameter(
        settings,
        pid,
        feature_id=feature_id,
        parameter_name=parameter_name,
        new_value=0,  # 0-length box is geometrically invalid
        thumbnail=False,
    )
    assert rejected["status"] == "error"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        source_after = zf.read("geometry/source.py").decode("utf-8")
    assert source_after == source_before
