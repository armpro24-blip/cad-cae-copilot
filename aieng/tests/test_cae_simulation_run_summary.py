"""Tests for cae_simulation_run_summary."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.cae_simulation_run_summary import (
    generate_simulation_run_summary,
    generate_simulation_run_markdown,
    write_simulation_run_summary_package,
)
from aieng.schema_versions import CAE_SIMULATION_RUN_SUMMARY_SCHEMA


def _build_package(tmp_path: Path, members: dict[str, bytes]) -> Path:
    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        for name, data in members.items():
            zf.writestr(name, data)
    return pkg


class TestGenerateSimulationRunSummary:
    def test_no_runs(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {})
        result = generate_simulation_run_summary(pkg)
        assert result["schema_version"] == CAE_SIMULATION_RUN_SUMMARY_SCHEMA
        assert result["summary_type"] == "cae_simulation_run"
        assert result["status"]["has_simulation_runs"] is False
        assert result["status"]["run_count"] == 0
        assert result["status"]["latest_run_id"] is None
        assert result["llm_summary"]["one_line"].startswith("No simulation runs")

    def test_single_completed_converged_run(self, tmp_path: Path) -> None:
        run = json.dumps({
            "run_id": "run_001",
            "solver": "CalculiX",
            "software": "FreeCAD FEM",
            "analysis_type": "static",
            "status": {"state": "completed", "solved": True, "converged": True, "warnings": [], "errors": []},
            "input_files": ["simulation/runs/run_001/solver_input.inp"],
            "output_files": ["results/raw/job.frd"],
        })
        pkg = _build_package(tmp_path, {
            "simulation/runs/run_001/solver_run.json": run.encode(),
            "simulation/runs/run_001/solver_log.txt": b"completed",
        })
        result = generate_simulation_run_summary(pkg)
        assert result["status"]["has_simulation_runs"] is True
        assert result["status"]["run_count"] == 1
        assert result["status"]["latest_run_id"] == "run_001"
        assert result["status"]["has_completed_run"] is True
        assert result["status"]["has_converged_run"] is True
        assert result["status"]["has_failed_run"] is False
        run_entry = result["runs"][0]
        assert run_entry["solver"] == "CalculiX"
        assert run_entry["software"] == "FreeCAD FEM"
        assert run_entry["analysis_type"] == "static"
        assert run_entry["state"] == "completed"
        assert run_entry["solved"] is True
        assert run_entry["converged"] is True
        assert run_entry["log_file"] == "simulation/runs/run_001/solver_log.txt"

    def test_completed_run_top_level_fields(self, tmp_path: Path) -> None:
        """Regression: cae.run_solver writes state/solved/converged at the TOP
        LEVEL of solver_run.json (not nested under a `status` block). The
        normalizer must read them there, otherwise a real executed solve is
        reported state="unknown"/solved=None and has_completed_run stays false —
        making a genuine run look unverified in the UI."""
        run = json.dumps({
            "run_id": "run_001",
            "solver": "CalculiX",
            "analysis_type": "static",
            "state": "completed",      # top-level, as cae.run_solver writes it
            "solved": True,
            "converged": None,
            "return_code": 0,
            "warnings": [],
            "errors": [],
            "input_files": ["simulation/runs/run_001/solver_input.inp"],
            "output_files": ["simulation/runs/run_001/outputs/result.frd"],
        })
        pkg = _build_package(tmp_path, {
            "simulation/runs/run_001/solver_run.json": run.encode(),
        })
        result = generate_simulation_run_summary(pkg)
        assert result["status"]["has_completed_run"] is True
        run_entry = result["runs"][0]
        assert run_entry["state"] == "completed"
        assert run_entry["solved"] is True
        assert run_entry["solver"] == "CalculiX"

    def test_failed_run(self, tmp_path: Path) -> None:
        run = json.dumps({
            "run_id": "run_002",
            "solver": "CalculiX",
            "status": {"state": "failed", "solved": False, "converged": False, "warnings": [], "errors": ["solver crashed"]},
        })
        pkg = _build_package(tmp_path, {"simulation/runs/run_002/solver_run.json": run.encode()})
        result = generate_simulation_run_summary(pkg)
        assert result["status"]["has_failed_run"] is True
        assert "Failed or errored" in result["llm_summary"]["risks"][0]
        assert "Review failed run logs" in result["llm_summary"]["recommended_next_actions"][0]

    def test_multiple_runs_latest_selected(self, tmp_path: Path) -> None:
        run1 = json.dumps({
            "run_id": "run_001",
            "solver": "CalculiX",
            "status": {"state": "completed", "solved": True, "converged": True},
            "finished_at": "2026-01-01T10:00:00Z",
        })
        run2 = json.dumps({
            "run_id": "run_002",
            "solver": "CalculiX",
            "status": {"state": "completed", "solved": True, "converged": True},
            "finished_at": "2026-01-02T10:00:00Z",
        })
        pkg = _build_package(tmp_path, {
            "simulation/runs/run_001/solver_run.json": run1.encode(),
            "simulation/runs/run_002/solver_run.json": run2.encode(),
        })
        result = generate_simulation_run_summary(pkg)
        assert result["status"]["run_count"] == 2
        assert result["status"]["latest_run_id"] == "run_002"

    def test_malformed_json_adds_warning(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"simulation/runs/run_001/solver_run.json": b"not json"})
        result = generate_simulation_run_summary(pkg)
        warnings = result["status"]["warnings"]
        assert any("malformed" in w for w in warnings)
        assert result["status"]["run_count"] == 0

    def test_legacy_solver_run(self, tmp_path: Path) -> None:
        run = json.dumps({
            "solver": "CalculiX",
            "software": "FreeCAD FEM",
            "status": {"state": "completed", "solved": True, "converged": True},
        })
        pkg = _build_package(tmp_path, {"simulation/solver_run.json": run.encode()})
        result = generate_simulation_run_summary(pkg)
        assert result["status"]["run_count"] == 1
        assert result["runs"][0]["run_id"] == "legacy"

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            generate_simulation_run_summary(tmp_path / "missing.aieng")


class TestGenerateSimulationRunMarkdown:
    def test_includes_runs(self, tmp_path: Path) -> None:
        run = json.dumps({
            "run_id": "run_001",
            "solver": "CalculiX",
            "software": "FreeCAD FEM",
            "analysis_type": "static",
            "status": {"state": "completed", "solved": True, "converged": True},
        })
        pkg = _build_package(tmp_path, {"simulation/runs/run_001/solver_run.json": run.encode()})
        summary = generate_simulation_run_summary(pkg)
        md = generate_simulation_run_markdown(summary)
        assert "# CAE Simulation Run Summary" in md
        assert "## Runs" in md
        assert "run_001" in md
        assert "CalculiX" in md
        assert "FreeCAD FEM" in md
        assert "## Limitations" in md

    def test_no_runs_shows_none(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {})
        summary = generate_simulation_run_summary(pkg)
        md = generate_simulation_run_markdown(summary)
        assert "Runs recorded:** no" in md
        assert "Latest run:** none" in md


class TestWriteSimulationRunSummaryPackage:
    def test_writes_both_files(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {})
        write_simulation_run_summary_package(pkg, overwrite=True)
        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
            assert "simulation/simulation_run_summary.json" in names
            assert "simulation/simulation_run_summary.md" in names
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["resources"]["simulation"]["simulation_run_summary"] == "simulation/simulation_run_summary.json"

    def test_preserves_unrelated_entries(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"other.txt": b"keep me"})
        write_simulation_run_summary_package(pkg, overwrite=True)
        with zipfile.ZipFile(pkg, "r") as zf:
            assert "other.txt" in zf.namelist()
            assert zf.read("other.txt") == b"keep me"

    def test_refuses_overwrite_by_default(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"simulation/simulation_run_summary.json": b"old"})
        with pytest.raises(FileExistsError):
            write_simulation_run_summary_package(pkg, overwrite=False)

    def test_allows_overwrite(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"simulation/simulation_run_summary.json": b"old"})
        write_simulation_run_summary_package(pkg, overwrite=True)
        with zipfile.ZipFile(pkg, "r") as zf:
            data = zf.read("simulation/simulation_run_summary.json").decode()
            assert "old" not in data
            assert "schema_version" in data

    def test_no_duplicate_zip_entries(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"simulation/simulation_run_summary.json": b"old"})
        write_simulation_run_summary_package(pkg, overwrite=True)
        with zipfile.ZipFile(pkg, "r") as zf:
            names = zf.namelist()
            assert names.count("simulation/simulation_run_summary.json") == 1
            assert names.count("simulation/simulation_run_summary.md") == 1
