"""Tests for cae.run_simulation_pipeline."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.app_factory import create_app
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


def _make_project(settings: Settings, name: str) -> tuple[str, Path]:
    from app.main import default_project, project_dir, save_project

    project = save_project(settings, default_project(name))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "model.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1"}))
        zf.writestr("geometry/topology_map.json", json.dumps({"format_version": "0.1", "entities": []}))
        zf.writestr("graph/feature_graph.json", json.dumps({"features": []}))
        zf.writestr("geometry/generated.step", "ISO-10303-21;\nEND-ISO-10303-21;\n")
    project["aieng_file"] = "model.aieng"
    save_project(settings, project)
    return project_id, pkg_path


def _fake_invoke_tool(
    real_invoke: Any,
    ok: bool = True,
    solver_executed: bool = True,
    mesh_ok: bool = True,
    deck_ok: bool = True,
) -> Any:
    """Return a mock runtime.invoke_tool that succeeds for each pipeline stage.

    The outer ``cae.run_simulation_pipeline`` call is forwarded to the real
    handler so the HTTP endpoint test exercises the real pipeline logic; only
    the sub-tool calls inside the pipeline are mocked.
    """

    def _invoke(tool: str, inp: dict[str, Any]) -> dict[str, Any]:
        if tool == "cae.run_simulation_pipeline":
            return real_invoke(tool, inp)
        if tool == "ai_preprocessing.run_ai_preprocessing":
            return {
                "status": "ok",
                "written_artifacts": ["simulation/setup.yaml", "simulation/cae_mapping.json"],
                "all_warnings": [],
            }
        if tool == "cae.generate_mesh":
            return {
                "ok": mesh_ok,
                "status": "completed" if mesh_ok else "error",
                "message": "" if mesh_ok else "gmsh unavailable",
                "element_count": 1234,
            }
        if tool == "cae.generate_solver_input":
            return {
                "ok": deck_ok,
                "status": "completed" if deck_ok else "error",
                "message": "" if deck_ok else "deck generation failed",
            }
        if tool == "cae.run_solver":
            return {
                "ok": ok and solver_executed,
                "status": "completed" if ok else "failed",
                "solver_execution_performed": solver_executed,
                "message": "" if ok else "solver failed",
                "return_code": 0 if ok else 1,
            }
        raise KeyError(f"unexpected tool in pipeline: {tool}")

    return _invoke


def test_pipeline_runs_all_stages_when_setup_provided(tmp_path: Path) -> None:
    """cae.run_simulation_pipeline invokes preprocessing, mesh, deck, and solver in order."""
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project_id, _pkg_path = _make_project(settings, "pipeline-full")

    from app import runtime as _rt

    real_invoke = _rt.invoke_tool
    with patch("app.runtime.invoke_tool", side_effect=_fake_invoke_tool(real_invoke)):
        resp = client.post("/api/agent/invoke-tool", json={
            "tool": "cae.run_simulation_pipeline",
            "input": {
                "project_id": project_id,
                "task_description": "Bracket fixed at bolt holes, 500 N downward on top face",
                "mesh_size_mm": 2.5,
                "run_id": "pipeline_run_001",
            },
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["ok"] is True
    assert data["solver_execution_performed"] is True
    phases = data["phase_results"]
    assert "ai_preprocessing" in phases
    assert "generate_mesh" in phases
    assert "generate_solver_input" in phases
    assert "run_solver" in phases
    assert phases["generate_mesh"]["element_count"] == 1234


def test_pipeline_skips_mesh_when_existing_mesh_and_no_size_override(tmp_path: Path) -> None:
    """If a mesh exists and no mesh_size_mm is given, the pipeline skips mesh generation."""
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project_id, pkg_path = _make_project(settings, "pipeline-existing-mesh")
    # Add an existing mesh to the package.
    _append_to_package(pkg_path, "simulation/mesh/mesh.inp", "*NODE\n1,0,0,0\n")

    invoked_tools: list[str] = []

    from app import runtime as _rt

    real_invoke = _rt.invoke_tool

    def _tracking_invoke(tool: str, _inp: dict[str, Any]) -> dict[str, Any]:
        invoked_tools.append(tool)
        return _fake_invoke_tool(real_invoke)(tool, _inp)

    with patch("app.runtime.invoke_tool", side_effect=_tracking_invoke):
        resp = client.post("/api/agent/invoke-tool", json={
            "tool": "cae.run_simulation_pipeline",
            "input": {
                "project_id": project_id,
                "run_id": "pipeline_run_001",
            },
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "cae.generate_mesh" not in invoked_tools
    assert "cae.generate_solver_input" in invoked_tools
    assert "cae.run_solver" in invoked_tools


def test_pipeline_stops_on_mesh_failure(tmp_path: Path) -> None:
    """A failure in an early stage prevents later stages from running."""
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project_id, _pkg_path = _make_project(settings, "pipeline-mesh-fail")

    invoked_tools: list[str] = []

    from app import runtime as _rt

    real_invoke = _rt.invoke_tool

    def _failing_invoke(tool: str, _inp: dict[str, Any]) -> dict[str, Any]:
        invoked_tools.append(tool)
        fake = _fake_invoke_tool(real_invoke, mesh_ok=False)
        return fake(tool, _inp)

    with patch("app.runtime.invoke_tool", side_effect=_failing_invoke):
        resp = client.post("/api/agent/invoke-tool", json={
            "tool": "cae.run_simulation_pipeline",
            "input": {
                "project_id": project_id,
                "task_description": "Bracket fixed at bolt holes, 500 N downward on top face",
                "mesh_size_mm": 2.5,
            },
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert data["code"] == "mesh_failed"
    assert "cae.run_solver" not in invoked_tools


def _append_to_package(pkg_path: Path, member: str, content: str) -> None:
    """Append a member to an existing zip package."""
    import shutil

    tmp = pkg_path.with_suffix(".tmp")
    with zipfile.ZipFile(pkg_path, "r") as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr(member, content)
    shutil.move(str(tmp), str(pkg_path))
