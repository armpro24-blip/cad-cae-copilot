"""Tests for the self-contained engineering report export (#372)."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, default_project, project_dir, save_project

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


def _make_project(settings: Settings) -> tuple[str, Path]:
    project = save_project(settings, default_project("engineering-report"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "report.aieng"
    project["aieng_file"] = "report.aieng"
    save_project(settings, project)
    return project_id, pkg_path


def _write_report_package(pkg_path: Path) -> None:
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    feature_graph = {
        "format_version": "0.1.0",
        "features": [
            {
                "id": "feat_bracket",
                "type": "solid",
                "name": "machined_bracket",
                "canonical_type": "bracket",
                "material": "Aluminium 6061-T6",
                "parameters": {},
            },
            {
                "id": "feat_bolt_1",
                "type": "standard_part",
                "name": "mounting_bolt_M6",
                "canonical_type": "screw",
                "designation": "M6-1",
                "source_library": "bd_warehouse",
                "parameters": {},
            },
        ],
    }
    targets = {
        "schema_version": "0.1",
        "targets": [
            {
                "target_id": "stress_pass",
                "label": "Stress below yield",
                "metric": "max_von_mises_stress",
                "operator": "<=",
                "value": 200,
                "unit": "MPa",
            }
        ],
    }
    metrics = {
        "schema_version": "0.1",
        "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX"},
        "load_cases": [
            {
                "load_case_id": "lc1",
                "metrics": {
                    "max_von_mises_stress": {"value": 87.4, "unit": "MPa"},
                    "max_displacement": {"value": 0.42, "unit": "mm"},
                },
            }
        ],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "report-test", "resources": {}}))
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph))
        zf.writestr(
            "geometry/topology_map.json",
            json.dumps({
                "format_version": "0.1",
                "entities": [
                    {"id": "body_001", "type": "solid", "name": "machined_bracket"},
                    {"id": "face_001", "type": "face"},
                ],
            }),
        )
        zf.writestr("geometry/generated.step", "")
        zf.writestr("task/design_targets.yaml", yaml.safe_dump(targets, sort_keys=False))
        zf.writestr("results/computed_metrics.json", json.dumps(metrics))
        zf.writestr("simulation/runs/run_001/solver_input.inp", "*HEADING\nreport fixture\n")
        zf.writestr("simulation/runs/run_001/solver_run.json", json.dumps({
            "solver": "CalculiX",
            "status": "completed",
            "solved": True,
            "return_code": 0,
            "started_at": "2026-06-25T10:01:00Z",
            "finished_at": "2026-06-25T10:01:03Z",
        }))
        zf.writestr("simulation/runs/run_001/result.frd", "")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_engineering_report_endpoint_returns_self_contained_html_read_only(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id, pkg_path = _make_project(settings)
    _write_report_package(pkg_path)
    before = _digest(pkg_path)

    response = client.get(f"/api/projects/{project_id}/report")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert _digest(pkg_path) == before
    html = response.text
    assert "<!doctype html>" in html
    assert "Engineering Report" in html
    assert "Honesty Boundary" in html
    assert "does not certify design safety" in html
    assert "Credibility Stamp" in html
    assert "Provenance" in html
    assert "<table>" in html
    assert "status-chip" in html
    assert "<pre>" not in html
    assert "max_von_mises_stress" in html
    assert "87.4" in html
    assert "simulation/runs/run_001/solver_input.inp" in html
    assert "simulation/runs/run_001/result.frd" in html
    assert "CalculiX" in html
    assert "Return code" in html
    assert "mounting_bolt_M6" in html
    assert "claim advancement" in html.lower()


def _frd(disp: float, stress: float) -> str:
    def value(v: float) -> str:
        return f"{v:12.5E}"

    def node(nid: int, values: list[float]) -> str:
        return "    -1" + f"{nid:12d}" + "".join(value(v) for v in values)

    return "\n".join([
        "    1C                                                                         1",
        "    1UCUT.......................                                                2",
        "    -4  DISP        4    1",
        "    -5  D1          1    2    1    0",
        "    -5  D2          1    2    2    0",
        "    -5  D3          1    2    3    0",
        "    -5  ALL         1    2    0    1",
        node(1, [disp, 0.0, 0.0, disp]),
        "    -3",
        "    -4  S           6    1",
        "    -5  SXX         1    4    1    1",
        "    -5  SYY         1    4    2    1",
        "    -5  SZZ         1    4    3    1",
        "    -5  SXY         1    4    4    1",
        "    -5  SXZ         1    4    5    1",
        "    -5  SYZ         1    4    6    1",
        node(1, [stress, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "    -3",
        " 9999",
    ]) + "\n"


def _add_second_run(pkg_path: Path, *, second_run_completed: bool = True) -> None:
    """Give the fixture the two runs a before/after actually needs.

    run_001 already has its `solver_run.json` from `_write_report_package`; only
    the second run's record is written here, so its completion can be varied.
    """
    with zipfile.ZipFile(pkg_path, "a") as zf:
        for run_id, disp, stress, revision in (
            ("run_001", 2.4, 140.0, 0),
            ("run_002", 0.3, 34.0, 1),
        ):
            zf.writestr(f"simulation/runs/{run_id}/outputs/result.frd", _frd(disp, stress))
            zf.writestr(f"simulation/runs/{run_id}/deck_provenance.json", json.dumps(
                {"run_id": run_id, "geometry_revision": revision}
            ))
        zf.writestr("simulation/runs/run_002/solver_run.json", json.dumps({
            "run_id": "run_002",
            "solver": "CalculiX",
            "state": "completed" if second_run_completed else "failed",
            "solved": second_run_completed,
            "analysis_type": "static",
        }))


def test_report_carries_the_before_after_comparison(tmp_path: Path) -> None:
    """The deliverable the promised task ends with, in the artifact a user is handed.

    Before this the report could only show where the design ENDED UP:
    `results/computed_metrics.json` is one fixed path, so the re-solve's
    extraction had already replaced the baseline's numbers. The section
    re-derives each column from its own run's FRD.
    """
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id, pkg_path = _make_project(settings)
    _write_report_package(pkg_path)
    _add_second_run(pkg_path)
    before = _digest(pkg_path)

    response = client.get(f"/api/projects/{project_id}/report")

    assert response.status_code == 200, response.text
    assert _digest(pkg_path) == before, "the report must not write into the package"
    html = response.text
    assert "Design Change (Before / After)" in html
    assert "geometry revision 0" in html and "geometry revision 1" in html
    assert "-87.5%" in html, "the displacement change should be reported as a percentage"
    assert "simulation/runs/run_001/outputs/result.frd" in html
    assert "simulation/runs/run_002/outputs/result.frd" in html


def test_a_caveat_on_the_comparison_reaches_the_report_warnings(tmp_path: Path) -> None:
    """The delta is only worth as much as the runs behind it.

    Here the second solve did not finish. The numbers still parse out of the
    FRD it left behind, so the table renders — and rendering only the table
    would drop exactly the caveat that decides whether the change means
    anything. The comparison's own warnings join the report's.
    """
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id, pkg_path = _make_project(settings)
    _write_report_package(pkg_path)
    _add_second_run(pkg_path, second_run_completed=False)

    html = client.get(f"/api/projects/{project_id}/report").text

    assert "Design Change (Before / After)" in html
    assert "run_002 did not complete" in html


def test_report_with_one_run_says_there_is_nothing_to_compare(tmp_path: Path) -> None:
    """An empty section would read as "nothing changed"; the refusal is the answer."""
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id, pkg_path = _make_project(settings)
    _write_report_package(pkg_path)

    html = client.get(f"/api/projects/{project_id}/report").text

    assert "Design Change (Before / After)" in html
    assert "needs two runs with results" in html


def test_report_generate_runtime_tool_returns_html_without_claim_advancement(tmp_path: Path) -> None:
    from app import runtime as _rt

    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id, pkg_path = _make_project(settings)
    _write_report_package(pkg_path)

    original_build = _rt.build_plan
    _rt.build_plan = lambda _msg, pid: [
        {"name": "report.generate", "description": "engineering report", "input": {"project_id": pid}}
    ]
    try:
        response = client.post(
            "/api/runtime/runs",
            json={
                "message": "generate engineering report",
                "project_id": project_id,
                "tool_input": {"project_id": project_id},
            },
        )
    finally:
        _rt.build_plan = original_build

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "completed"
    output = run["tool_results"][0]["output"]
    assert output["ok"] is True
    assert output["tool"] == "report.generate"
    assert output["claim_advancement"] == "none"
    assert output["credibility_summary"]["result_evidence"] == "present"
    assert "Engineering Report" in output["html"]


def test_report_generate_tool_is_registered() -> None:
    from app.runtime import registered_tools_info

    tools = {tool["name"]: tool for tool in registered_tools_info()}
    assert "report.generate" in tools
    assert "Read-only" in tools["report.generate"]["description"]
