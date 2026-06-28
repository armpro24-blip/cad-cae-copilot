"""Tests for CAE calibration/benchmark comparison packs (#433)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.app_factory import create_app
from app.cae_calibration import (
    CALIBRATION_CASES,
    assess_calibration,
    compare_to_benchmark,
    get_calibration_case,
    list_calibration_cases,
)
from app.config import Settings
from app.main import default_project, project_dir, save_project


def test_list_calibration_cases_returns_metadata() -> None:
    cases = list_calibration_cases()
    assert len(cases) == len(CALIBRATION_CASES)
    ids = {c["id"] for c in cases}
    assert "tension_rod" in ids
    assert "cantilever_end_load" in ids
    for case in cases:
        assert "title" in case
        assert "description" in case
        assert "gated_metrics" in case


def test_get_calibration_case_unknown_returns_none() -> None:
    assert get_calibration_case("no_such_case") is None


def test_tension_rod_passes_within_tolerance() -> None:
    computed = {
        "max_displacement": 0.0048,  # reference ~0.00476
        "max_von_mises_stress": 10.2,
    }
    result = compare_to_benchmark(computed, "tension_rod")

    assert result["status"] == "passed"
    assert result["case_id"] == "tension_rod"
    assert result["gated_passed"] is True
    assert any(r["metric"] == "max_displacement" and r["status"] == "passed" for r in result["metric_results"])


def test_tension_rod_fails_when_gating_metric_deviates() -> None:
    computed = {
        "max_displacement": 0.0060,  # >10% deviation
        "max_von_mises_stress": 10.0,
    }
    result = compare_to_benchmark(computed, "tension_rod")

    assert result["status"] == "failed"
    assert result["gated_passed"] is False


def test_missing_metric_lowers_to_warning() -> None:
    computed = {"max_von_mises_stress": 10.0}
    result = compare_to_benchmark(computed, "tension_rod")

    assert result["status"] == "warning"
    assert any(r["metric"] == "max_displacement" and r["status"] == "missing" for r in result["metric_results"])


def test_cantilever_end_load_passes() -> None:
    computed = {
        "max_displacement": 0.024,
        "max_von_mises_stress": 14.5,
    }
    result = compare_to_benchmark(computed, "cantilever_end_load")

    assert result["status"] == "passed"


def test_compare_unknown_case_returns_error() -> None:
    result = compare_to_benchmark({}, "unknown")
    assert result["status"] == "error"


def test_assess_calibration_auto_matches_best_case() -> None:
    computed = {
        "max_displacement": 0.00476,
        "max_von_mises_stress": 10.0,
    }
    result = assess_calibration(computed)

    assert result["status"] == "passed"
    assert result["case_id"] == "tension_rod"


def test_non_gating_metric_deviation_warns_not_fails() -> None:
    computed = {
        "max_displacement": 0.00476,  # gated metric passes
        "max_von_mises_stress": 13.0,  # non-gated metric fails (>10% deviation)
    }
    result = compare_to_benchmark(computed, "tension_rod")

    assert result["status"] == "warning"
    assert result["gated_passed"] is True
    assert any(
        r["metric"] == "max_von_mises_stress" and r["status"] == "failed" and r["gate"] is False
        for r in result["metric_results"]
    )


def test_assess_calibration_unknown_when_no_match() -> None:
    result = assess_calibration({"some_other_metric": 1.0})
    assert result["status"] == "unknown"


def test_assess_calibration_competing_cases_selects_unambiguous_best(monkeypatch: pytest.MonkeyPatch) -> None:
    """When multiple cases share metric names, auto-match picks the closer fit."""
    competing_cases = dict(CALIBRATION_CASES)
    competing_cases["tension_rod_loose"] = {
        "title": "Tension rod (loose)",
        "description": "Overlapping tolerance bands for auto-match testing.",
        "analysis_type": "static",
        "material": "Steel",
        "geometry": {"length_mm": 100.0, "cross_section_mm2": 100.0},
        "loading": {"force_n": 1000.0, "direction": "+X"},
        "references": {
            "max_displacement": {
                "value": 0.0050,
                "unit": "mm",
                "tolerance_percent": 10.0,
                "gate": True,
            },
            "max_von_mises_stress": {
                "value": 11.0,
                "unit": "MPa",
                "tolerance_percent": 10.0,
                "gate": False,
            },
        },
        "limitations": ["Test-only competing case."],
    }
    monkeypatch.setattr("app.cae_calibration.CALIBRATION_CASES", competing_cases)

    computed = {
        "max_displacement": 0.00476,  # closer to tension_rod reference
        "max_von_mises_stress": 10.0,
    }
    result = assess_calibration(computed)

    assert result["status"] == "passed"
    assert result["case_id"] == "tension_rod"


def _make_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=Path(__file__).resolve().parents[3] / "aieng",
        sample_step=workspace / "sample.step",
    )


def _write_metrics_package(pkg: Path, metrics: dict[str, object]) -> None:
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "calibration-test"}))
        zf.writestr("results/computed_metrics.json", json.dumps(metrics))


def test_calibration_endpoints_list_and_compare(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project = save_project(settings, default_project("calibration-test"))
    project_id = project["id"]
    pkg = project_dir(settings, project_id) / "p.aieng"
    project["aieng_file"] = "p.aieng"
    save_project(settings, project)
    _write_metrics_package(
        pkg,
        {
            "global_metrics": {
                "max_displacement": {"value": 0.00476, "unit": "mm"},
                "max_von_mises_stress": {"value": 10.0, "unit": "MPa"},
            }
        },
    )

    resp = client.get(f"/api/projects/{project_id}/calibration-cases")
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == project_id
    ids = {c["id"] for c in body["cases"]}
    assert "tension_rod" in ids
    assert "cantilever_end_load" in ids

    # Auto-match when no caseId is provided.
    resp = client.post(f"/api/projects/{project_id}/calibration", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["comparison"]["case_id"] == "tension_rod"
    assert body["comparison"]["status"] == "passed"

    # Explicit case selection.
    resp = client.post(
        f"/api/projects/{project_id}/calibration",
        json={"caseId": "tension_rod"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["comparison"]["case_id"] == "tension_rod"
