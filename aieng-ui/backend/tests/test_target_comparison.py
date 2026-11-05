"""Target comparison endpoint and integration tests."""

from __future__ import annotations

import pytest

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from app.main import Settings, create_app, default_project, project_dir, save_project

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


def _make_package(
    pkg_path: Path,
    *,
    targets: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    feature_graph: bool = False,
) -> None:
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        if targets is not None:
            zf.writestr(
                "task/design_targets.yaml",
                yaml.safe_dump({"schema_version": "0.1", "targets": targets}, sort_keys=False),
            )
        if metrics is not None:
            zf.writestr("results/computed_metrics.json", json.dumps(metrics))
        if feature_graph:
            zf.writestr("graph/feature_graph.json", json.dumps({"features": []}))


def _client_project(
    tmp_path: Path,
    *,
    targets: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    feature_graph: bool = False,
) -> tuple[TestClient, str, Path]:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("target-comparison"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "test.aieng"
    _make_package(pkg_path, targets=targets, metrics=metrics, feature_graph=feature_graph)
    project["aieng_file"] = "test.aieng"
    save_project(settings, project)
    return client, project_id, pkg_path


def _metrics(stress: float = 187.4, displacement: float = 0.82) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "load_cases": [
            {
                "load_case_id": "lc1",
                "metrics": {
                    "max_von_mises_stress": {"value": stress, "unit": "MPa"},
                    "max_displacement": {"value": displacement, "unit": "mm"},
                },
            }
        ],
    }


def test_target_comparison_endpoint_reports_pass_fail_unknown_and_missing_metric(tmp_path: Path) -> None:
    targets = [
        {"target_id": "stress_pass", "label": "Stress pass", "metric": "max_von_mises_stress", "operator": "<=", "value": 200, "unit": "MPa"},
        {"target_id": "disp_fail", "label": "Displacement fail", "metric": "max_displacement", "operator": "<=", "value": 0.5, "unit": "mm"},
        {"target_id": "range_unknown", "label": "Range", "target_type": "maximum_displacement", "comparator": "within_range", "unit": "mm"},
        {"target_id": "missing_metric", "label": "Safety", "metric": "minimum_safety_factor", "operator": ">=", "value": 1.5},
    ]
    client, project_id, pkg = _client_project(tmp_path, targets=targets, metrics=_metrics())

    before = hashlib.sha256(pkg.read_bytes()).hexdigest()
    resp = client.get(f"/api/projects/{project_id}/target-comparison")
    after = hashlib.sha256(pkg.read_bytes()).hexdigest()

    assert resp.status_code == 200, resp.text
    assert before == after
    body = resp.json()
    statuses = {item["target_id"]: item["status"] for item in body["items"]}
    reasons = {item["target_id"]: item.get("reason_code") for item in body["items"]}
    assert statuses["stress_pass"] == "pass"
    assert statuses["disp_fail"] == "fail"
    assert statuses["range_unknown"] == "unknown"
    assert statuses["missing_metric"] == "unknown"
    assert reasons["missing_metric"] == "missing_metric"
    assert body["summary"]["pass"] == 1
    assert body["summary"]["fail"] == 1
    assert body["summary"]["unknown"] == 2
    assert "does not certify" in body["claim_boundary"]


def test_target_comparison_endpoint_marks_ambiguous_load_case_metric_unknown(tmp_path: Path) -> None:
    targets = [
        {"target_id": "ambiguous", "label": "Stress", "metric": "max_von_mises_stress", "operator": "<=", "value": 200},
    ]
    metrics = {
        "schema_version": "0.1",
        "load_cases": [
            {"load_case_id": "lc1", "metrics": {"max_von_mises_stress": {"value": 100}}},
            {"load_case_id": "lc2", "metrics": {"max_von_mises_stress": {"value": 120}}},
        ],
    }
    client, project_id, _pkg = _client_project(tmp_path, targets=targets, metrics=metrics)

    body = client.get(f"/api/projects/{project_id}/target-comparison").json()

    assert body["items"][0]["status"] == "unknown"
    assert body["items"][0]["reason_code"] == "ambiguous"
    assert body["summary"]["unknown"] == 1


def test_target_comparison_evaluates_global_metrics_imported_by_ui(tmp_path: Path) -> None:
    targets = [
        {"target_id": "mass_pass", "label": "Mass", "metric": "mass", "operator": "<=", "value": 2.0, "unit": "kg"},
        {"target_id": "stress_range", "label": "Stress", "metric": "max_von_mises_stress", "operator": "within_range", "threshold_min": 100, "threshold_max": 200, "value": 200, "unit": "MPa"},
    ]
    metrics = {
        "schema_version": "0.1",
        "global_metrics": {
            "mass": {"value": 1.24, "unit": "kg"},
            "max_von_mises_stress": {"value": 187.4, "unit": "MPa"},
        },
    }
    client, project_id, _pkg = _client_project(tmp_path, targets=targets, metrics=metrics)

    body = client.get(f"/api/projects/{project_id}/target-comparison").json()
    statuses = {item["target_id"]: item["status"] for item in body["items"]}

    assert statuses == {"mass_pass": "pass", "stress_range": "pass"}
    assert body["summary"]["pass"] == 2


def test_save_design_targets_accepts_core_within_range_operator(tmp_path: Path) -> None:
    client, project_id, _pkg = _client_project(tmp_path)
    payload = [
        {
            "target_id": "range",
            "label": "Stress range",
            "metric": "max_von_mises_stress",
            "operator": "within_range",
            "threshold_min": 100,
            "threshold_max": 200,
            "value": 200,
        }
    ]

    resp = client.put(f"/api/projects/{project_id}/design-targets", json=payload)

    assert resp.status_code == 200, resp.text
    assert resp.json()["targets"][0]["operator"] == "within_range"


def test_project_health_surfaces_target_comparison_summary(tmp_path: Path) -> None:
    targets = [
        {"target_id": "stress", "label": "Stress", "metric": "max_von_mises_stress", "operator": "<=", "value": 200},
    ]
    client, project_id, _pkg = _client_project(tmp_path, targets=targets, metrics=_metrics(), feature_graph=True)

    body = client.get(f"/api/projects/{project_id}/health-check").json()
    check = next(c for c in body["checks"] if c["id"] == "target_comparison")

    assert check["status"] == "passed"
    assert "1 pass" in check["summary"]
    assert any("stress: pass" in detail for detail in check["details"])


def test_copilot_report_and_export_include_target_comparison_block() -> None:
    from app import copilot_loop

    comparison = {
        "present": True,
        "summary": {"total": 1, "pass": 1, "fail": 0, "unknown": 0, "not_evaluated": 0},
        "items": [
            {
                "target_id": "stress",
                "status": "pass",
                "reason_code": "passed_threshold",
                "actual": {"value": 187.4, "unit": "MPa"},
                "expected": {"comparator": "<=", "threshold": 200},
            }
        ],
    }
    loop = {
        "loop_id": "loop_001",
        "project_id": "proj",
        "package_path": "test.aieng",
        "strictness": "default",
        "status": "completed",
        "steps": [],
        "context": {"after": {"design_target_comparisons": comparison}},
    }

    report = copilot_loop._build_loop_report(loop)
    assert "## Design target comparison" in report["markdown"]
    assert "`stress`" in report["markdown"]

    summary = copilot_loop._build_loop_summary(loop)
    export = copilot_loop._build_review_markdown(
        project_id="proj",
        loops=[loop],
        summaries=[summary],
        include_reports=False,
        include_diff=False,
        include_highlights=True,
        diff_payload=None,
        report_texts=[None],
        warnings_collected=[],
    )
    assert "### Design target comparison" in export
    assert "`passed_threshold`" in export


def test_acceptance_verdict_lists_only_what_did_not_pass(monkeypatch) -> None:
    """The verdict cae.extract_solver_results returns: summary + the failures only."""
    from app import target_comparison

    monkeypatch.setattr(target_comparison, "compare_package_targets", lambda _s, _p: {
        "summary": {"total": 2, "pass": 1, "fail": 1, "unknown": 0, "not_evaluated": 0},
        "items": [
            {"target_type": "max_von_mises_stress", "status": "pass",
             "expected": {"value": 100}, "actual": {"value": 8.6}},
            {"target_type": "max_displacement", "status": "fail", "comparator": "<=",
             "expected": {"value": 0.001}, "actual": {"value": 0.0024}, "notes": "dropped"},
        ],
    })
    verdict = target_comparison.acceptance_verdict(None, "p1")

    assert verdict["summary"]["fail"] == 1
    assert verdict["not_passed"] == [{
        "target_type": "max_displacement", "comparator": "<=",
        "expected": {"value": 0.001}, "actual": {"value": 0.0024}, "status": "fail",
    }]


@pytest.mark.parametrize("comparison", [
    {"summary": {"total": 0}, "items": []},
    RuntimeError("package unreadable"),
])
def test_acceptance_verdict_is_absent_without_criteria_or_on_error(monkeypatch, comparison) -> None:
    """No criteria, or a failed comparison, must leave the extraction response unchanged."""
    from app import target_comparison

    def _compare(_s, _p):
        if isinstance(comparison, Exception):
            raise comparison
        return comparison

    monkeypatch.setattr(target_comparison, "compare_package_targets", _compare)
    assert target_comparison.acceptance_verdict(None, "p1") is None
