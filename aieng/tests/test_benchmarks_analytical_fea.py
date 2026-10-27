"""Tests for the analytical FEA benchmark corpus + scorer harness (issue #257)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aieng.benchmarks.analytical_fea import (
    SCORECARD_FORMAT,
    SCORECARD_VERSION,
    build_fixture_packages,
    ensure_packages,
    load_corpus,
    load_reference,
    score_case,
    score_corpus,
    write_scorecard,
)
from aieng.nafems_verification import REFERENCE_CASES, _find_ccx


DATASET_ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "datasets" / "analytical_fea"


# ---------------------------------------------------------------------------
# Dataset integrity
# ---------------------------------------------------------------------------


def test_corpus_and_reference_files_exist() -> None:
    corpus = load_corpus(DATASET_ROOT)
    assert corpus["corpus_id"] == "analytical_fea"
    assert len(corpus["cases"]) == len(REFERENCE_CASES)
    for entry in corpus["cases"]:
        ref = load_reference(entry["case_id"], DATASET_ROOT)
        assert ref["case_id"] == entry["case_id"]
        assert ref["analysis_type"] == entry["analysis_type"]
        assert ref["metrics"]


def test_references_match_nafems_reference_cases() -> None:
    """The JSON references are derived from the canonical REFERENCE_CASES table."""
    for case_id, expected in REFERENCE_CASES.items():
        ref = load_reference(case_id, DATASET_ROOT)
        assert ref["description"] == expected["description"]
        assert ref["analysis_type"] == expected.get("analysis_type", "static")
        assert set(ref["metrics"]) == set(expected["metrics"])
        for metric_name, metric in expected["metrics"].items():
            # Every field, not a chosen few: comparing only value/unit/tolerance
            # let `gate` drift, so the CLI gated on peak stresses the table
            # declares informational (tension rod: 21.9 vs 10 MPa).
            got = dict(ref["metrics"][metric_name])
            assert got.pop("value") == pytest.approx(metric["value"])
            assert got == {k: v for k, v in metric.items() if k != "value"}


# ---------------------------------------------------------------------------
# Fixture package builder
# ---------------------------------------------------------------------------


def test_build_fixture_packages_creates_all_cases(tmp_path: Path) -> None:
    paths = build_fixture_packages(tmp_path, DATASET_ROOT)
    assert set(paths) == set(REFERENCE_CASES)
    for path in paths.values():
        assert path.exists()
        assert path.suffix == ".aieng"


def test_ensure_packages_reuses_existing_packages(tmp_path: Path) -> None:
    build_fixture_packages(tmp_path, DATASET_ROOT)
    pkg_dir = ensure_packages(tmp_path, DATASET_ROOT)
    assert pkg_dir == tmp_path
    assert len(list(pkg_dir.glob("*.aieng"))) == len(REFERENCE_CASES)


def test_ensure_packages_builds_missing_packages(tmp_path: Path) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    # Build only one package manually.
    build_fixture_packages(partial, DATASET_ROOT)
    for extra in list(partial.glob("*.aieng"))[1:]:
        extra.unlink()

    pkg_dir = ensure_packages(partial, DATASET_ROOT)
    assert len(list(pkg_dir.glob("*.aieng"))) == len(REFERENCE_CASES)


# ---------------------------------------------------------------------------
# Scorer unit tests (no solver required)
# ---------------------------------------------------------------------------


def _fake_runner(
    computed_metrics: dict[str, Any],
    *,
    status: str = "ok",
    analysis_type: str = "static",
) -> Any:
    def _run(package_path: Path, run_id: str = "") -> dict[str, Any]:
        return {
            "status": status,
            "analysis_type": analysis_type,
            "computed_metrics": computed_metrics,
        }

    return _run


def test_score_case_passes_within_tolerance(tmp_path: Path) -> None:
    ref = load_reference("tension_rod", DATASET_ROOT)
    computed = {
        "load_cases": [
            {
                "id": "run_001",
                "metrics": {
                    "max_displacement": {
                        "value": ref["metrics"]["max_displacement"]["value"] * 0.98,
                        "unit": "mm",
                    },
                    "max_von_mises_stress": {
                        "value": ref["metrics"]["max_von_mises_stress"]["value"] * 1.02,
                        "unit": "MPa",
                    },
                },
            }
        ]
    }
    result = score_case(
        "tension_rod",
        tmp_path / "x.aieng",
        ref,
        run_id="run_001",
        runner=_fake_runner(computed),
    )
    assert result["status"] == "ok"
    assert result["verdict"] == "pass"
    for m in result["metrics"]:
        assert m["verdict"] == "pass"


def test_score_case_fails_outside_tolerance(tmp_path: Path) -> None:
    ref = load_reference("tension_rod", DATASET_ROOT)
    computed = {
        "load_cases": [
            {
                "id": "run_001",
                "metrics": {
                    "max_displacement": {
                        "value": ref["metrics"]["max_displacement"]["value"] * 1.50,
                        "unit": "mm",
                    },
                    "max_von_mises_stress": {
                        "value": ref["metrics"]["max_von_mises_stress"]["value"],
                        "unit": "MPa",
                    },
                },
            }
        ]
    }
    result = score_case(
        "tension_rod",
        tmp_path / "x.aieng",
        ref,
        run_id="run_001",
        runner=_fake_runner(computed),
    )
    assert result["verdict"] == "fail"
    disp = next(m for m in result["metrics"] if m["metric"] == "max_displacement")
    stress = next(m for m in result["metrics"] if m["metric"] == "max_von_mises_stress")
    assert disp["verdict"] == "fail"
    assert stress["verdict"] == "pass"


def test_score_case_skips_when_solver_unavailable(tmp_path: Path) -> None:
    ref = load_reference("tension_rod", DATASET_ROOT)

    def _skip_runner(package_path: Path, run_id: str = "") -> dict[str, Any]:
        return {
            "status": "skipped",
            "missing_tools": ["ccx"],
            "message": "no solver",
        }

    result = score_case("tension_rod", tmp_path / "x.aieng", ref, runner=_skip_runner)
    assert result["status"] == "skipped"
    assert result["verdict"] == "skipped"


def test_score_corpus_shape_with_mock_runner(tmp_path: Path) -> None:
    build_fixture_packages(tmp_path, DATASET_ROOT)

    def _passing_runner(package_path: Path, run_id: str = "") -> dict[str, Any]:
        case_id = package_path.stem
        ref = load_reference(case_id, DATASET_ROOT)
        metrics: dict[str, Any] = {}
        for name, spec in ref["metrics"].items():
            metrics[name] = {"value": spec["value"], "unit": spec.get("unit", "")}
        return {
            "status": "ok",
            "analysis_type": ref.get("analysis_type", "static"),
            "computed_metrics": {"load_cases": [{"id": run_id, "metrics": metrics}]},
        }

    scorecard = score_corpus(
        tmp_path,
        dataset_root=DATASET_ROOT,
        run_id="mock_run_001",
        runner=_passing_runner,
    )
    assert scorecard["format"] == SCORECARD_FORMAT
    assert scorecard["format_version"] == SCORECARD_VERSION
    assert scorecard["corpus"] == "analytical_fea"
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["total"] == len(REFERENCE_CASES)
    assert scorecard["summary"]["passed"] == len(REFERENCE_CASES)
    assert scorecard["summary"]["failed"] == 0
    assert "honesty" in scorecard
    assert scorecard["honesty"]["certified"] is False


def test_score_corpus_status_is_failed_when_any_case_fails(tmp_path: Path) -> None:
    build_fixture_packages(tmp_path, DATASET_ROOT)

    def _bad_runner(package_path: Path, run_id: str = "") -> dict[str, Any]:
        # Every metric at twice its own case's reference, so each case fails on
        # its gating metric rather than by coincidence with another case's values.
        ref = load_reference(package_path.stem, DATASET_ROOT)
        metrics = {
            name: {"value": spec["value"] * 2.0, "unit": spec.get("unit", "")}
            for name, spec in ref["metrics"].items()
        }
        return {
            "status": "ok",
            "analysis_type": ref.get("analysis_type", "static"),
            "computed_metrics": {"load_cases": [{"id": run_id, "metrics": metrics}]},
        }

    scorecard = score_corpus(
        tmp_path,
        dataset_root=DATASET_ROOT,
        run_id="mock_run_002",
        runner=_bad_runner,
    )
    assert scorecard["status"] == "failed"
    assert scorecard["summary"]["failed"] == len(REFERENCE_CASES)


def test_write_scorecard_roundtrips(tmp_path: Path) -> None:
    scorecard = {"format": SCORECARD_FORMAT, "summary": {"total": 1}}
    path = write_scorecard(scorecard, tmp_path / "sc.json")
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == scorecard


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_module_smoke(tmp_path: Path) -> None:
    from aieng.benchmarks import analytical_fea

    out = tmp_path / "scorecard.json"
    rc = analytical_fea.main(
        [
            "--packages-dir",
            str(tmp_path / "packages"),
            "--dataset-root",
            str(DATASET_ROOT),
            "--run-id",
            "cli_smoke",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format"] == SCORECARD_FORMAT
    assert data["run_id"] == "cli_smoke"


@pytest.mark.skipif(_find_ccx() is None, reason="CalculiX (ccx) not available")
def test_cli_real_ccx_corpus_passes(tmp_path: Path) -> None:
    """With a real solver every case must SOLVE and pass its gating metrics.

    The smoke test above also passes without ccx (every case is ``skipped`` and
    the CLI exits 0), so on its own it never exercised the scored path; this is
    the one the real-ccx gate runs.
    """
    from aieng.benchmarks import analytical_fea

    out = tmp_path / "scorecard.json"
    rc = analytical_fea.main(
        ["--packages-dir", str(tmp_path / "packages"), "--dataset-root", str(DATASET_ROOT),
         "--run-id", "real_ccx", "--out", str(out)]
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    # Every canonical case, not "whatever corpus.json lists": an empty or partial
    # corpus would otherwise pass, and -k real_ccx skips the integrity tests.
    assert sorted(c["case_id"] for c in data["cases"]) == sorted(REFERENCE_CASES)
    failing = [c["case_id"] for c in data["cases"] if c["verdict"] != "pass"]
    assert rc == 0 and data["status"] == "passed", failing
    assert all(c["status"] == "ok" for c in data["cases"])
