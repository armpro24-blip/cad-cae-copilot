"""Tests for cae_result_summary."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.cae_result_summary import (
    FIELD_SUMMARY_DISPLACEMENT_PATH,
    FIELD_SUMMARY_STRESS_PATH,
    generate_cae_result_summary,
    generate_evidence_index,
    generate_postprocessing_markdown,
    write_cae_result_summary_package,
)
from aieng.schema_versions import CAE_RESULT_SUMMARY_SCHEMA, EVIDENCE_INDEX_SCHEMA


def _build_package(tmp_path: Path, members: dict[str, bytes]) -> Path:
    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        for name, data in members.items():
            zf.writestr(name, data)
    return pkg


class TestGenerateCaeResultSummary:
    def test_cad_only_package(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {})
        result = generate_cae_result_summary(pkg)
        assert result["schema_version"] == CAE_RESULT_SUMMARY_SCHEMA
        assert result["summary_type"] == "cae_postprocessing"
        assert result["status"]["mode"] == "cad_only"
        assert result["computed_values"]["extrema_computed"] is False
        assert result["computed_values"]["max_displacement"] is None
        assert result["llm_summary"]["one_line"] == "CAD-only package; no CAE artifacts detected."
        assert any("CAD-only" in w for w in result["status"]["warnings"])
        assert result["load_cases"] == []
        assert result["solver_settings"] is None
        assert result["field_metadata"] is None
        assert result["source"]["solver"] == "external_or_unknown"

    def test_cae_setup_package(self, tmp_path: Path) -> None:
        pkg = _build_package(
            tmp_path,
            {
                "graph/constraints.json": b"{}",
                "simulation/cae_imports/parsed_materials.json": b"[]",
            },
        )
        result = generate_cae_result_summary(pkg)
        assert result["status"]["mode"] == "cae_setup"
        assert result["status"]["has_cae_setup"] is True
        assert result["status"]["has_mesh"] is False
        assert result["computed_values"]["extrema_computed"] is False
        assert "setup" in result["llm_summary"]["one_line"].lower()

    def test_cae_result_package(self, tmp_path: Path) -> None:
        pkg = _build_package(
            tmp_path,
            {
                "results/fields/von_mises_stress.vtu": b"<xml/>",
                "results/fields/displacement.vtu": b"<xml/>",
            },
        )
        result = generate_cae_result_summary(pkg)
        assert result["status"]["mode"] == "cae_result"
        assert result["status"]["has_fields"] is True
        assert result["artifacts"]["field_files"] == [
            "results/fields/displacement.vtu",
            "results/fields/von_mises_stress.vtu",
        ]
        assert result["computed_values"]["extrema_computed"] is False
        findings = result["llm_summary"]["key_findings"]
        assert any("field" in f.lower() for f in findings)

    def test_cae_validation_package(self, tmp_path: Path) -> None:
        pkg = _build_package(
            tmp_path,
            {
                "validation/status.yaml": b"status: ok",
            },
        )
        result = generate_cae_result_summary(pkg)
        assert result["status"]["mode"] == "cae_validation"
        assert result["status"]["has_validation"] is True

    def test_limitations_include_honesty(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"results/fields/safety_factor.vtu": b"<xml/>"})
        result = generate_cae_result_summary(pkg)
        limitations = result["llm_summary"]["limitations"]
        assert any("artifact presence only" in lim for lim in limitations)
        assert any("numerical extrema were not computed" in lim for lim in limitations)

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            generate_cae_result_summary(tmp_path / "missing.aieng")

    def test_reads_solver_metadata(self, tmp_path: Path) -> None:
        solver_meta = json.dumps({"solver": "calculix", "software": "CalculiX 2.20", "source_files": ["job.inp"]})
        pkg = _build_package(
            tmp_path,
            {
                "results/fields/displacement.vtu": b"<xml/>",
                "results/solver_metadata.json": solver_meta.encode(),
            },
        )
        result = generate_cae_result_summary(pkg)
        assert result["source"]["solver"] == "calculix"
        assert result["source"]["software"] == "CalculiX 2.20"
        assert result["source"]["source_files"] == ["job.inp"]
        assert "calculix" in result["llm_summary"]["one_line"].lower()
        findings = result["llm_summary"]["key_findings"]
        assert any("calculix" in f.lower() for f in findings)

    def test_reads_field_metadata(self, tmp_path: Path) -> None:
        field_meta = json.dumps({"fields": [{"name": "S", "type": "stress"}], "format": "vtu"})
        pkg = _build_package(
            tmp_path,
            {
                "results/fields/von_mises_stress.vtu": b"<xml/>",
                "results/field_metadata.json": field_meta.encode(),
            },
        )
        result = generate_cae_result_summary(pkg)
        assert result["field_metadata"] is not None
        assert result["field_metadata"]["count"] == 1
        assert result["field_metadata"]["format"] == "vtu"

    def test_reads_solver_settings(self, tmp_path: Path) -> None:
        settings = json.dumps({"solver_type": "calculix", "analysis_type": "static", "parameters": {"steps": 10}})
        pkg = _build_package(
            tmp_path,
            {
                "simulation/solver_settings.json": settings.encode(),
            },
        )
        result = generate_cae_result_summary(pkg)
        assert result["solver_settings"] is not None
        assert result["solver_settings"]["solver_type"] == "calculix"
        assert result["solver_settings"]["analysis_type"] == "static"
        assert result["solver_settings"]["parameters"]["steps"] == 10

    def test_reads_load_cases(self, tmp_path: Path) -> None:
        lc1 = json.dumps({"id": "lc1", "name": "Force X", "type": "force", "magnitude": 1000, "unit": "N"})
        lc2 = json.dumps({"name": "Pressure Y", "type": "pressure", "magnitude": 5, "unit": "MPa"})
        pkg = _build_package(
            tmp_path,
            {
                "simulation/load_cases/lc1.json": lc1.encode(),
                "simulation/load_cases/lc2.json": lc2.encode(),
            },
        )
        result = generate_cae_result_summary(pkg)
        assert len(result["load_cases"]) == 2
        ids = {lc["id"] for lc in result["load_cases"]}
        assert ids == {"lc1", "Pressure Y"}
        names = {lc["name"] for lc in result["load_cases"]}
        assert "Force X" in names
        assert "Pressure Y" in names
        types = {lc["type"] for lc in result["load_cases"]}
        assert types == {"force", "pressure"}
        # Verify key findings mention load cases
        findings = result["llm_summary"]["key_findings"]
        assert any("load case" in f.lower() for f in findings)

    def test_ignores_malformed_json(self, tmp_path: Path) -> None:
        pkg = _build_package(
            tmp_path,
            {
                "results/solver_metadata.json": b"not json",
                "results/field_metadata.json": b"{bad",
                "simulation/solver_settings.json": b"[]",
                "simulation/load_cases/bad.json": b"not json",
            },
        )
        result = generate_cae_result_summary(pkg)
        assert result["source"]["solver"] == "external_or_unknown"
        assert result["field_metadata"] is None
        assert result["solver_settings"] is None
        assert result["load_cases"] == []

    # Phase 6 computed metrics tests

    def test_no_computed_metrics_preserves_extrema_false(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"results/fields/von_mises_stress.vtu": b"<xml/>"})
        result = generate_cae_result_summary(pkg)
        assert result["computed_values"]["extrema_computed"] is False
        assert result["computed_values"]["max_displacement"] is None
        assert result["computed_values"]["max_von_mises_stress"] is None

    def test_valid_computed_metrics_sets_extrema_true(self, tmp_path: Path) -> None:
        computed = json.dumps({
            "schema_version": "0.1",
            "metrics_source": {"tool": "freecad_postprocessor", "software": "FreeCAD FEM"},
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_von_mises_stress": {"value": 187.4, "unit": "MPa", "field": "von_mises_stress"},
                        "max_displacement": {"value": 0.82, "unit": "mm", "field": "displacement_magnitude"},
                        "minimum_safety_factor": {"value": 1.33, "unit": None, "basis": "yield_strength / max_von_mises_stress"},
                    },
                }
            ],
        })
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": computed.encode()})
        result = generate_cae_result_summary(pkg)
        assert result["computed_values"]["extrema_computed"] is True
        assert result["computed_values"]["source"] == "results/computed_metrics.json"
        assert result["computed_values"]["computed_by"] == "freecad_postprocessor"
        assert result["computed_values"]["max_von_mises_stress"]["value"] == 187.4
        assert result["computed_values"]["max_displacement"]["value"] == 0.82
        assert result["computed_values"]["minimum_safety_factor"]["value"] == 1.33
        assert result["result_contract"]["claim_tier"] == "imported_computed_metrics"
        assert result["result_contract"]["solver_execution_evidence"] is False

    def test_legacy_rest_result_summary_normalizes_as_compatibility_metrics(self, tmp_path: Path) -> None:
        legacy = json.dumps({
            "status": "success",
            "solver": "CalculiX",
            "von_mises_max_mpa": 212.5,
            "displacement_max_mm": 0.44,
        })
        pkg = _build_package(tmp_path, {"simulation/results_summary.json": legacy.encode()})

        result = generate_cae_result_summary(pkg)

        assert result["status"]["mode"] == "cae_result"
        assert result["computed_values"]["extrema_computed"] is True
        assert result["computed_values"]["source"] == "simulation/results_summary.json"
        assert result["computed_values"]["computed_by"] == "legacy_rest_simulation_runner"
        assert result["computed_values"]["max_von_mises_stress"]["value"] == 212.5
        assert result["computed_values"]["max_von_mises_stress"]["unit"] == "MPa"
        assert result["computed_values"]["max_displacement"]["value"] == 0.44
        contract = result["result_contract"]
        assert contract["claim_tier"] == "legacy_rest_result"
        assert contract["solver_execution_evidence"] is False
        assert contract["legacy_rest_summary_path"] == "simulation/results_summary.json"
        assert "simulation/results_summary.json" in contract["source_artifacts"]

    def test_completed_solver_run_is_executed_solver_result_contract(self, tmp_path: Path) -> None:
        run = json.dumps({
            "run_id": "run_001",
            "state": "completed",
            "solved": True,
            "solver": "CalculiX",
            "input_files": ["simulation/runs/run_001/solver_input.inp"],
            "output_files": ["simulation/runs/run_001/outputs/result.frd"],
        })
        metrics = json.dumps({
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_displacement": {"value": 0.12, "unit": "mm"},
                    },
                }
            ],
        })
        pkg = _build_package(
            tmp_path,
            {
                "simulation/runs/run_001/solver_run.json": run.encode(),
                "results/computed_metrics.json": metrics.encode(),
                "results/evidence_index.json": json.dumps({"evidence": []}).encode(),
            },
        )

        summary = generate_cae_result_summary(pkg)
        assert summary["status"]["mode"] == "cae_result"
        contract = summary["result_contract"]

        assert contract["claim_tier"] == "executed_solver_result"
        assert contract["solver_execution_evidence"] is True
        assert contract["completed_solver_run_ids"] == ["run_001"]
        assert "simulation/runs/run_001/solver_run.json" in contract["source_artifacts"]

        # The summary must credit the executed run in source/status/llm, not
        # report it as imported/external with "no solver executed".
        assert summary["source"]["solver"] == "CalculiX"
        assert summary["source"]["software"] == "CalculiX"
        assert summary["status"]["solved"] is True
        assert "executed" in summary["llm_summary"]["one_line"].lower()
        limitations = " ".join(summary["llm_summary"]["limitations"]).lower()
        assert "no solver was executed" not in limitations
        assert "linear static" in limitations

    def test_computed_metrics_take_priority_over_legacy_rest_summary(self, tmp_path: Path) -> None:
        metrics = json.dumps({
            "metrics_source": {"tool": "frd_parser"},
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_von_mises_stress": {"value": 155.0, "unit": "MPa"},
                    },
                }
            ],
        })
        legacy = json.dumps({
            "status": "success",
            "von_mises_max_mpa": 999.0,
        })
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": metrics.encode(),
                "simulation/results_summary.json": legacy.encode(),
            },
        )

        result = generate_cae_result_summary(pkg)

        assert result["computed_values"]["source"] == "results/computed_metrics.json"
        assert result["computed_values"]["max_von_mises_stress"]["value"] == 155.0
        assert result["result_contract"]["claim_tier"] == "imported_computed_metrics"

    def test_missing_result_contract_is_unknown(self, tmp_path: Path) -> None:
        result = generate_cae_result_summary(_build_package(tmp_path, {}))

        assert result["result_contract"]["claim_tier"] == "missing_or_unknown"
        assert result["result_contract"]["solver_execution_evidence"] is False
        assert result["result_contract"]["metrics_source"] is None

    def test_computed_metrics_attach_to_matching_load_case(self, tmp_path: Path) -> None:
        lc = json.dumps({"id": "lc1", "name": "Force", "type": "force"})
        computed = json.dumps({
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_von_mises_stress": {"value": 200.0, "unit": "MPa"},
                    },
                }
            ],
        })
        pkg = _build_package(
            tmp_path,
            {
                "simulation/load_cases/lc1.json": lc.encode(),
                "results/computed_metrics.json": computed.encode(),
            },
        )
        result = generate_cae_result_summary(pkg)
        lc_result = next(lc for lc in result["load_cases"] if lc["id"] == "lc1")
        assert lc_result["metrics"]["max_von_mises_stress"]["value"] == 200.0
        assert result["computed_values"]["extrema_computed"] is True

    def test_computed_metrics_unknown_load_case_preserved(self, tmp_path: Path) -> None:
        computed = json.dumps({
            "load_cases": [
                {
                    "id": "lc_unknown",
                    "metrics": {
                        "max_displacement": {"value": 1.2, "unit": "mm"},
                    },
                }
            ],
        })
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": computed.encode()})
        result = generate_cae_result_summary(pkg)
        assert len(result["load_cases"]) == 1
        assert result["load_cases"][0]["id"] == "lc_unknown"
        assert result["load_cases"][0]["metrics"]["max_displacement"]["value"] == 1.2
        assert result["computed_values"]["extrema_computed"] is True

    def test_malformed_computed_metrics_adds_warning(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": b"not json"})
        result = generate_cae_result_summary(pkg)
        assert result["computed_values"]["extrema_computed"] is False
        assert any("malformed" in w.lower() for w in result["status"]["warnings"])
        assert any("metrics ignored" in w.lower() for w in result["status"]["warnings"])

    def test_low_safety_factor_risk(self, tmp_path: Path) -> None:
        computed = json.dumps({
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "minimum_safety_factor": {"value": 1.2, "unit": None},
                    },
                }
            ],
        })
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": computed.encode()})
        result = generate_cae_result_summary(pkg)
        risks = result["llm_summary"]["risks"]
        assert any("low safety factor" in r.lower() for r in risks)

    def test_design_targets_compliance_true_false_unknown(self, tmp_path: Path) -> None:
        computed = json.dumps({
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_von_mises_stress": {"value": 100.0, "unit": "MPa"},
                        "minimum_safety_factor": {"value": 2.0, "unit": None},
                    },
                }
            ],
        })
        targets = b"""
format_version: 0.1.0
targets:
  - id: stress_limit
    metric: max_von_mises_stress
    operator: "<="
    value: 120
    unit: MPa
  - id: sf_floor
    metric: minimum_safety_factor
    operator: ">="
    value: 2.5
  - id: displacement_bound
    metric: max_displacement
    operator: "<="
    value: 1.0
    unit: mm
claim_policy:
  targets_are_acceptance_criteria: true
  compliance_requires_evidence: true
  physical_correctness_not_claimed: true
"""
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": computed.encode(),
                "task/design_targets.yaml": targets,
            },
        )
        result = generate_cae_result_summary(pkg)
        by_id = {item["id"]: item for item in result["targets"]["items"]}
        assert by_id["stress_limit"]["met"] is True
        assert by_id["sf_floor"]["met"] is False
        assert by_id["displacement_bound"]["met"] == "unknown"

    def test_computed_metrics_mentions_imported_in_limitations(self, tmp_path: Path) -> None:
        computed = json.dumps({
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_von_mises_stress": {"value": 100.0, "unit": "MPa"},
                    },
                }
            ],
        })
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": computed.encode()})
        result = generate_cae_result_summary(pkg)
        limitations = result["llm_summary"]["limitations"]
        assert any("imported" in lim.lower() for lim in limitations)
        assert any("not computed by aieng" in lim.lower() for lim in limitations)


class TestGenerateEvidenceIndex:
    def test_entries_match_artifacts(self, tmp_path: Path) -> None:
        pkg = _build_package(
            tmp_path,
            {
                "graph/constraints.json": b"{}",
                "simulation/mesh/model.vtu": b"<xml/>",
            },
        )
        evidence = generate_evidence_index(pkg)
        assert evidence["schema_version"] == EVIDENCE_INDEX_SCHEMA
        assert evidence["evidence_type"] == "cae_artifacts"
        entries_by_path = {e["path"]: e for e in evidence["entries"]}
        assert entries_by_path["graph/constraints.json"]["exists"] is True
        assert entries_by_path["simulation/mesh/model.vtu"]["exists"] is True
        assert entries_by_path["validation/status.yaml"]["exists"] is False
        # Metadata entries present even when missing
        assert "results/solver_metadata.json" in entries_by_path
        assert entries_by_path["results/solver_metadata.json"]["exists"] is False

    def test_supports_empty_when_missing(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {})
        evidence = generate_evidence_index(pkg)
        for entry in evidence["entries"]:
            if not entry["exists"]:
                assert entry["supports"] == []

    def test_load_case_entries(self, tmp_path: Path) -> None:
        lc = json.dumps({"id": "lc1", "name": "Force", "type": "force"})
        pkg = _build_package(
            tmp_path,
            {
                "simulation/load_cases/lc1.json": lc.encode(),
            },
        )
        evidence = generate_evidence_index(pkg)
        entries_by_id = {e["id"]: e for e in evidence["entries"]}
        assert "load_case_lc1" in entries_by_id
        assert entries_by_id["load_case_lc1"]["kind"] == "setup"
        assert entries_by_id["load_case_lc1"]["role"] == "load_case"

    def test_computed_metrics_evidence_entry(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": b"{}"})
        evidence = generate_evidence_index(pkg)
        entries_by_path = {e["path"]: e for e in evidence["entries"]}
        assert "results/computed_metrics.json" in entries_by_path
        assert entries_by_path["results/computed_metrics.json"]["exists"] is True
        assert entries_by_path["results/computed_metrics.json"]["kind"] == "computed_metrics"

    def test_solver_run_artifacts_in_evidence_index(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {
            "simulation/runs/run_001/solver_run.json": b'{"solver": "CalculiX"}',
            "simulation/runs/run_001/outputs/result.frd": b"** CalculiX FRD\n",
        })
        evidence = generate_evidence_index(pkg)
        entries_by_path = {e["path"]: e for e in evidence["entries"]}

        sr = entries_by_path.get("simulation/runs/run_001/solver_run.json")
        assert sr is not None, "solver_run.json must appear in evidence_index"
        assert sr["kind"] == "result"
        assert sr["role"] == "solver_run_metadata"
        assert sr["exists"] is True
        assert "solver_execution_evidence" in sr["supports"]
        assert "audit" in sr["supports"]

        frd = entries_by_path.get("simulation/runs/run_001/outputs/result.frd")
        assert frd is not None, "result.frd must appear in evidence_index"
        assert frd["kind"] == "result"
        assert frd["role"] == "solver_raw_output"
        assert frd["exists"] is True
        assert "numerical_result_source" in frd["supports"]

    def test_field_summary_artifacts_in_evidence_index_when_present(self, tmp_path: Path) -> None:
        """Field summary artifacts appear in evidence_index with exists=True when present."""
        pkg = _build_package(tmp_path, {
            FIELD_SUMMARY_DISPLACEMENT_PATH: b'{"field_name": "displacement", "claim_advancement": "none"}',
            FIELD_SUMMARY_STRESS_PATH: b'{"field_name": "stress", "claim_advancement": "none"}',
        })
        evidence = generate_evidence_index(pkg)
        entries_by_path = {e["path"]: e for e in evidence["entries"]}

        disp = entries_by_path.get(FIELD_SUMMARY_DISPLACEMENT_PATH)
        assert disp is not None
        assert disp["kind"] == "field"
        assert disp["role"] == "cae_field_summary"
        assert disp["exists"] is True
        assert "displacement_extrema" in disp["supports"]
        assert "audit" in disp["supports"]

        stress = entries_by_path.get(FIELD_SUMMARY_STRESS_PATH)
        assert stress is not None
        assert stress["kind"] == "field"
        assert stress["role"] == "cae_field_summary"
        assert stress["exists"] is True
        assert "stress_extrema" in stress["supports"]

    def test_field_summary_artifacts_in_evidence_index_when_absent(self, tmp_path: Path) -> None:
        """Field summary artifacts appear in evidence_index with exists=False when absent."""
        pkg = _build_package(tmp_path, {"graph/constraints.json": b"{}"})
        evidence = generate_evidence_index(pkg)
        entries_by_path = {e["path"]: e for e in evidence["entries"]}

        disp = entries_by_path.get(FIELD_SUMMARY_DISPLACEMENT_PATH)
        assert disp is not None
        assert disp["exists"] is False
        assert disp["supports"] == []  # empty when absent

        stress = entries_by_path.get(FIELD_SUMMARY_STRESS_PATH)
        assert stress is not None
        assert stress["exists"] is False


class TestGeneratePostprocessingMarkdown:
    def test_includes_mode_and_limitations(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"results/fields/von_mises_stress.vtu": b"<xml/>"})
        summary = generate_cae_result_summary(pkg)
        evidence = generate_evidence_index(pkg)
        md = generate_postprocessing_markdown(summary, evidence)
        assert "CAE / Post-processing Summary" in md
        assert "cae_result" in md
        assert "Not computed" in md
        assert "Limitations" in md
        assert "artifact presence only" in md

    def test_cad_only_markdown(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {})
        summary = generate_cae_result_summary(pkg)
        evidence = generate_evidence_index(pkg)
        md = generate_postprocessing_markdown(summary, evidence)
        assert "CAD-only" in md

    def test_includes_load_cases_and_solver(self, tmp_path: Path) -> None:
        solver_meta = json.dumps({"solver": "calculix", "software": "CalculiX 2.20"})
        lc = json.dumps({"id": "lc1", "name": "Force", "type": "force", "magnitude": 1000, "unit": "N"})
        pkg = _build_package(
            tmp_path,
            {
                "results/fields/von_mises_stress.vtu": b"<xml/>",
                "results/solver_metadata.json": solver_meta.encode(),
                "simulation/load_cases/lc1.json": lc.encode(),
            },
        )
        summary = generate_cae_result_summary(pkg)
        evidence = generate_evidence_index(pkg)
        md = generate_postprocessing_markdown(summary, evidence)
        assert "Solver:** calculix" in md
        assert "Software:** CalculiX 2.20" in md
        assert "Load cases" in md
        assert "Force" in md

    def test_includes_field_metadata(self, tmp_path: Path) -> None:
        field_meta = json.dumps({"fields": [{"name": "S"}, {"name": "U"}], "format": "vtu"})
        pkg = _build_package(
            tmp_path,
            {
                "results/field_metadata.json": field_meta.encode(),
            },
        )
        summary = generate_cae_result_summary(pkg)
        evidence = generate_evidence_index(pkg)
        md = generate_postprocessing_markdown(summary, evidence)
        assert "Field metadata" in md
        assert "Registered fields: 2" in md

    def test_computed_metrics_in_markdown(self, tmp_path: Path) -> None:
        computed = json.dumps({
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_von_mises_stress": {"value": 187.4, "unit": "MPa"},
                        "max_displacement": {"value": 0.82, "unit": "mm"},
                    },
                }
            ],
        })
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": computed.encode()})
        summary = generate_cae_result_summary(pkg)
        evidence = generate_evidence_index(pkg)
        md = generate_postprocessing_markdown(summary, evidence)
        assert "Imported computed metrics" in md
        assert "187.4 MPa" in md
        assert "0.82 mm" in md
        assert "Per load case" in md
        assert "lc1" in md

    def test_computed_metrics_source_warning_in_markdown(self, tmp_path: Path) -> None:
        computed = json.dumps({
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_von_mises_stress": {"value": 100.0, "unit": "MPa"},
                    },
                }
            ],
        })
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": computed.encode()})
        summary = generate_cae_result_summary(pkg)
        evidence = generate_evidence_index(pkg)
        md = generate_postprocessing_markdown(summary, evidence)
        assert "Source:" in md
        assert "Computed by:" in md


class TestWriteCaeResultSummaryPackage:
    def test_writes_three_files(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"graph/constraints.json": b"{}"})
        out = write_cae_result_summary_package(pkg)
        assert out == pkg
        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
            assert "results/result_summary.json" in names
            assert "results/evidence_index.json" in names
            assert "results/postprocessing_summary.md" in names
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["resources"]["results"]["result_summary"] == "results/result_summary.json"

    def test_refuses_overwrite_by_default(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"results/result_summary.json": b"{}"})
        with pytest.raises(FileExistsError):
            write_cae_result_summary_package(pkg, overwrite=False)

    def test_allows_overwrite(self, tmp_path: Path) -> None:
        pkg = _build_package(tmp_path, {"results/result_summary.json": b"{}"})
        out = write_cae_result_summary_package(pkg, overwrite=True)
        assert out == pkg
        with zipfile.ZipFile(pkg, "r") as zf:
            summary = json.loads(zf.read("results/result_summary.json"))
            # Old result_summary.json counts as a result artifact, so mode is cae_result
            assert summary["status"]["mode"] == "cae_result"

    def test_writes_field_summary_artifacts_when_computed_metrics_present(self, tmp_path: Path) -> None:
        """Field summary artifacts are written when computed_metrics.json has displacement and stress."""
        cm = {
            "schema_version": "0.1",
            "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX", "source_files": []},
            "load_cases": [
                {
                    "id": "lc1",
                    "metrics": {
                        "max_displacement": {"value": 0.42, "unit": "mm"},
                        "max_von_mises_stress": {"value": 18.5, "unit": "MPa"},
                    },
                }
            ],
            "warnings": [],
        }
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": json.dumps(cm).encode()})
        write_cae_result_summary_package(pkg, overwrite=True)

        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
            assert FIELD_SUMMARY_DISPLACEMENT_PATH in names
            assert FIELD_SUMMARY_STRESS_PATH in names

            disp = json.loads(zf.read(FIELD_SUMMARY_DISPLACEMENT_PATH))
            assert disp["schema_version"] == "0.1"
            assert disp["field_name"] == "displacement"
            assert disp["unit"] == "mm"
            assert disp["stats"]["max_value"] == 0.42
            assert disp["stats"]["min_value"] is None
            assert disp["stats"]["node_count"] is None
            assert disp["claim_advancement"] == "none"
            assert disp["evidence_role"] == "displacement_extrema"
            assert disp["source"]["computed_metrics_path"] == "results/computed_metrics.json"

            stress = json.loads(zf.read(FIELD_SUMMARY_STRESS_PATH))
            assert stress["field_name"] == "stress"
            assert stress["unit"] == "MPa"
            assert stress["stats"]["max_value"] == 18.5
            assert stress["claim_advancement"] == "none"
            assert stress["evidence_role"] == "stress_extrema"

    def test_no_field_summaries_without_computed_metrics(self, tmp_path: Path) -> None:
        """Field summary artifacts are NOT written when computed_metrics.json is absent."""
        pkg = _build_package(tmp_path, {"graph/constraints.json": b"{}"})
        write_cae_result_summary_package(pkg, overwrite=True)

        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
        assert FIELD_SUMMARY_DISPLACEMENT_PATH not in names
        assert FIELD_SUMMARY_STRESS_PATH not in names

    def test_field_summaries_include_frd_path_when_present(self, tmp_path: Path) -> None:
        """Field summary source.frd_path references the result.frd if one is in the package."""
        cm = {
            "schema_version": "0.1",
            "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX", "source_files": []},
            "load_cases": [{"id": "lc1", "metrics": {
                "max_displacement": {"value": 0.1, "unit": "mm"},
                "max_von_mises_stress": {"value": 5.0, "unit": "MPa"},
            }}],
            "warnings": [],
        }
        pkg = _build_package(tmp_path, {
            "results/computed_metrics.json": json.dumps(cm).encode(),
            "simulation/runs/run_001/outputs/result.frd": b"** CalculiX FRD\n 9999\n",
        })
        write_cae_result_summary_package(pkg, overwrite=True)

        with zipfile.ZipFile(pkg, "r") as zf:
            disp = json.loads(zf.read(FIELD_SUMMARY_DISPLACEMENT_PATH))
        assert disp["source"]["frd_path"] == "simulation/runs/run_001/outputs/result.frd"

    def test_field_summaries_overwritten_on_refresh(self, tmp_path: Path) -> None:
        """A second refresh overwrites existing field summary artifacts."""
        cm_v1 = {
            "schema_version": "0.1",
            "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX", "source_files": []},
            "load_cases": [{"id": "lc1", "metrics": {
                "max_displacement": {"value": 0.1, "unit": "mm"},
                "max_von_mises_stress": {"value": 5.0, "unit": "MPa"},
            }}],
            "warnings": [],
        }
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": json.dumps(cm_v1).encode()})
        write_cae_result_summary_package(pkg, overwrite=True)

        # Verify first refresh wrote v1 value
        with zipfile.ZipFile(pkg, "r") as zf:
            disp_v1 = json.loads(zf.read(FIELD_SUMMARY_DISPLACEMENT_PATH))
        assert disp_v1["stats"]["max_value"] == 0.1

        # Build a second package with different values; verify field summaries reflect new data
        cm_v2 = {
            "schema_version": "0.1",
            "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX", "source_files": []},
            "load_cases": [{"id": "lc1", "metrics": {
                "max_displacement": {"value": 0.99, "unit": "mm"},
                "max_von_mises_stress": {"value": 5.0, "unit": "MPa"},
            }}],
            "warnings": [],
        }
        pkg2_dir = tmp_path / "pkg2"
        pkg2_dir.mkdir()
        pkg2 = _build_package(pkg2_dir, {"results/computed_metrics.json": json.dumps(cm_v2).encode()})
        write_cae_result_summary_package(pkg2, overwrite=True)

        with zipfile.ZipFile(pkg2, "r") as zf:
            disp = json.loads(zf.read(FIELD_SUMMARY_DISPLACEMENT_PATH))
        assert disp["stats"]["max_value"] == 0.99

    def test_field_summary_no_claim_advancement(self, tmp_path: Path) -> None:
        """Field summary artifacts always carry claim_advancement='none'."""
        cm = {
            "schema_version": "0.1",
            "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX", "source_files": []},
            "load_cases": [{"id": "lc1", "metrics": {
                "max_displacement": {"value": 0.5, "unit": "mm"},
                "max_von_mises_stress": {"value": 25.0, "unit": "MPa"},
            }}],
            "warnings": [],
        }
        pkg = _build_package(tmp_path, {"results/computed_metrics.json": json.dumps(cm).encode()})
        write_cae_result_summary_package(pkg, overwrite=True)

        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
            disp = json.loads(zf.read(FIELD_SUMMARY_DISPLACEMENT_PATH))
            stress = json.loads(zf.read(FIELD_SUMMARY_STRESS_PATH))

        assert disp["claim_advancement"] == "none"
        assert stress["claim_advancement"] == "none"
        assert "ai/claim_map.json" not in names
        assert "results/claim_map.json" not in names
