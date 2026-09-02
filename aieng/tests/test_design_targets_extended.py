"""Tests for Phase 35 extended design target schema and comparison surface.

These tests validate schema evolution only; comparison behavior is covered in
test_cae_result_summary.py.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import yaml

from aieng.cae_result_summary import generate_cae_result_summary
from aieng.validate import validate_package


def _build_package(tmp_path: Path, members: dict[str, bytes]) -> Path:
    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        for name, data in members.items():
            zf.writestr(name, data)
    return pkg


class TestModernFormatCompatibility:
    """Modern 0.1.1 field names (target_id/target_type/comparator/threshold)
    must be readable by the existing result summary generator."""

    def test_modern_fields_map_to_legacy_comparison(self, tmp_path: Path) -> None:
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
format_version: "0.1.1"
targets:
  - target_id: stress_limit
    target_type: maximum_von_mises_stress
    description: Allowable stress
    comparator: "<="
    threshold: 120
    unit: MPa
    priority: high
  - target_id: sf_floor
    target_type: minimum_safety_factor
    description: Safety floor
    comparator: ">="
    threshold: 2.5
    priority: critical
  - target_id: disp_bound
    target_type: maximum_displacement
    description: Displacement limit
    comparator: "<="
    threshold: 1.0
    unit: mm
    priority: medium
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
        assert by_id["disp_bound"]["met"] == "unknown"

    def test_objective_priority_returns_unknown(self, tmp_path: Path) -> None:
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
        targets = b"""
format_version: "0.1.1"
targets:
  - target_id: obj_priority
    target_type: objective_priority
    description: Safety over mass
    comparator: priority
    priority: critical
    objective_order:
      - safety_floor
      - mass_reduce
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
        # objective_priority has no numeric mapping yet → unknown
        assert by_id["obj_priority"]["met"] == "unknown"


class TestDesignTargetComparisonSchema:
    """The standalone design_target_comparison.schema.json must validate sample blocks."""

    def test_comparison_block_validates(self, tmp_path: Path) -> None:
        from pathlib import Path
        import jsonschema

        schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "design_target_comparison.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        sample = {
            "present": True,
            "target_set_id": "ts_001",
            "evaluated_at": "2026-05-17T12:00:00Z",
            "summary": {"total": 3, "pass": 2, "fail": 1, "unknown": 0, "not_evaluated": 0},
            "items": [
                {
                    "target_id": "stress_limit",
                    "target_type": "maximum_von_mises_stress",
                    "expected": {"comparator": "<=", "threshold": 350.0},
                    "actual": {"value": 298.0, "unit": "MPa", "source_artifact": "results/computed_metrics.json"},
                    "comparator": "<=",
                    "status": "pass",
                    "evidence_refs": ["results/computed_metrics.json"],
                    "source_artifacts": ["results/computed_metrics.json"],
                    "notes": "Within limit",
                },
                {
                    "target_id": "disp_limit",
                    "target_type": "maximum_displacement",
                    "expected": {"comparator": "<=", "threshold": 0.25},
                    "actual": {"value": 0.31, "unit": "mm", "source_artifact": "results/computed_metrics.json"},
                    "comparator": "<=",
                    "status": "fail",
                    "notes": "Exceeded by 0.06 mm",
                },
                {
                    "target_id": "preserve_holes",
                    "target_type": "preserved_interface",
                    "expected": {"comparator": "preserve"},
                    "actual": {"value": None},
                    "comparator": "preserve",
                    "status": "unknown",
                    "notes": "No diff evidence",
                },
            ],
        }
        jsonschema.validate(instance=sample, schema=schema)

    def test_invalid_status_rejected(self, tmp_path: Path) -> None:
        from pathlib import Path
        import jsonschema

        schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "design_target_comparison.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        bad = {
            "present": True,
            "summary": {"total": 1, "pass": 0, "fail": 0, "unknown": 0, "not_evaluated": 0},
            "items": [
                {
                    "target_id": "x",
                    "target_type": "maximum_von_mises_stress",
                    "expected": {"comparator": "<="},
                    "actual": {"value": 100},
                    "comparator": "<=",
                    "status": "maybe",  # invalid
                }
            ],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=bad, schema=schema)


# ---------------------------------------------------------------------------
# Phase 35 PR 2 — design_target_comparisons block emission
# ---------------------------------------------------------------------------

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "aieng"
    / "schemas"
    / "design_target_comparison.schema.json"
)


def _validate_against_block_schema(block: dict) -> None:
    """Validate a design_target_comparisons block against its standalone schema."""
    import jsonschema

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=block, schema=schema)


def _build_targets_yaml(targets: list[dict]) -> bytes:
    """Render a design_targets.yaml body with the canonical claim policy."""
    doc = {
        "format_version": "0.1.1",
        "target_set_id": "ts_phase35_pr2",
        "targets": targets,
        "claim_policy": {
            "targets_are_acceptance_criteria": True,
            "compliance_requires_evidence": True,
            "physical_correctness_not_claimed": True,
        },
    }
    return yaml.safe_dump(doc).encode("utf-8")


def _computed_metrics_json(metrics: dict) -> bytes:
    """Render results/computed_metrics.json with a single load case."""
    return json.dumps({"load_cases": [{"id": "lc1", "metrics": metrics}]}).encode("utf-8")


class TestDesignTargetComparisonEmission:
    """Phase 35 PR 2: result_summary.json carries a design_target_comparisons block."""

    def test_legacy_targets_still_produce_old_flat_targets_block(self, tmp_path: Path) -> None:
        """The pre-existing result['targets'] block must continue to work for
        legacy-format targets so consumers that depend on it are not broken."""
        targets_yaml = b"""
format_version: 0.1.0
targets:
  - id: stress_limit
    metric: max_von_mises_stress
    operator: "<="
    value: 350.0
    unit: MPa
claim_policy:
  targets_are_acceptance_criteria: true
  compliance_requires_evidence: true
  physical_correctness_not_claimed: true
"""
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"max_von_mises_stress": {"value": 300.0, "unit": "MPa"}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        result = generate_cae_result_summary(pkg)
        assert result["targets"]["present"] is True
        by_id = {item["id"]: item for item in result["targets"]["items"]}
        assert by_id["stress_limit"]["met"] is True

    def test_modern_targets_produce_design_target_comparisons_block(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "stress_limit",
                    "target_type": "maximum_von_mises_stress",
                    "description": "Allowable stress",
                    "comparator": "<=",
                    "threshold": 350.0,
                    "unit": "MPa",
                    "priority": "high",
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"max_von_mises_stress": {"value": 298.0, "unit": "MPa"}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        result = generate_cae_result_summary(pkg)
        block = result["design_target_comparisons"]
        assert block["present"] is True
        assert block["target_set_id"] == "ts_phase35_pr2"
        assert block["summary"]["total"] == 1
        assert block["summary"]["pass"] == 1
        item = block["items"][0]
        assert item["status"] == "pass"
        assert item["actual"]["value"] == 298.0
        assert "results/computed_metrics.json" in item["source_artifacts"]
        _validate_against_block_schema(block)

    def test_pass_comparison(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "sf_floor",
                    "target_type": "minimum_safety_factor",
                    "description": "Safety floor",
                    "comparator": ">=",
                    "threshold": 1.5,
                    "priority": "critical",
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"minimum_safety_factor": {"value": 2.0, "unit": None}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        assert block["items"][0]["status"] == "pass"
        assert block["summary"]["pass"] == 1
        assert block["summary"]["fail"] == 0

    def test_fail_comparison(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "sf_floor",
                    "target_type": "minimum_safety_factor",
                    "description": "Safety floor",
                    "comparator": ">=",
                    "threshold": 2.5,
                    "priority": "critical",
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"minimum_safety_factor": {"value": 2.0, "unit": None}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        assert block["items"][0]["status"] == "fail"
        assert block["summary"]["fail"] == 1

    def test_unknown_when_computed_metric_missing_from_existing_artifact(
        self, tmp_path: Path
    ) -> None:
        """computed_metrics.json exists but max_displacement is not present."""
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "disp_bound",
                    "target_type": "maximum_displacement",
                    "description": "Displacement limit",
                    "comparator": "<=",
                    "threshold": 1.0,
                    "unit": "mm",
                    "priority": "medium",
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"max_von_mises_stress": {"value": 100.0, "unit": "MPa"}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        item = block["items"][0]
        assert item["status"] == "unknown"
        assert "results/computed_metrics.json" in item["source_artifacts"]
        assert block["summary"]["unknown"] == 1

    def test_not_evaluated_when_computed_metrics_artifact_missing(
        self, tmp_path: Path
    ) -> None:
        """No results/computed_metrics.json at all → not_evaluated, not unknown."""
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "stress_limit",
                    "target_type": "maximum_von_mises_stress",
                    "description": "Allowable stress",
                    "comparator": "<=",
                    "threshold": 350.0,
                    "unit": "MPa",
                    "priority": "high",
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {"task/design_targets.yaml": targets_yaml},
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        item = block["items"][0]
        assert item["status"] == "not_evaluated"
        assert "source_artifacts" not in item or "results/computed_metrics.json" not in item.get("source_artifacts", [])
        assert block["summary"]["not_evaluated"] == 1

    def test_within_range_pass(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "mass_window",
                    "target_type": "absolute_mass_target",
                    "description": "Mass within window",
                    "comparator": "within_range",
                    "threshold_min": 1.0,
                    "threshold_max": 2.0,
                    "unit": "kg",
                    "priority": "medium",
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"total_mass": {"value": 1.5, "unit": "kg"}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        assert block["items"][0]["status"] == "pass"

    def test_within_range_fail(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "mass_window",
                    "target_type": "absolute_mass_target",
                    "description": "Mass within window",
                    "comparator": "within_range",
                    "threshold_min": 1.0,
                    "threshold_max": 2.0,
                    "unit": "kg",
                    "priority": "medium",
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"total_mass": {"value": 2.5, "unit": "kg"}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        assert block["items"][0]["status"] == "fail"

    def test_objective_priority_returns_not_evaluated(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "obj_priority",
                    "target_type": "objective_priority",
                    "description": "Safety over mass",
                    "comparator": "priority",
                    "priority": "critical",
                    "objective_order": ["safety_floor", "mass_reduce"],
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"max_von_mises_stress": {"value": 100.0, "unit": "MPa"}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        item = block["items"][0]
        assert item["status"] == "not_evaluated"
        assert item["expected"]["objective_order"] == ["safety_floor", "mass_reduce"]

    def test_preserved_interface_unknown_when_feature_graph_present(
        self, tmp_path: Path
    ) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "preserve_holes",
                    "target_type": "preserved_interface",
                    "description": "Holes preserved",
                    "comparator": "preserve",
                    "priority": "critical",
                    "protected_features": [
                        {"feature_id": "hole_001", "feature_type": "circular_hole"}
                    ],
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "graph/feature_graph.json": b'{"nodes": []}',
                "task/design_targets.yaml": targets_yaml,
            },
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        item = block["items"][0]
        assert item["status"] == "unknown"
        assert "graph/feature_graph.json" in item["source_artifacts"]

    def test_preserved_interface_not_evaluated_when_no_feature_graph(
        self, tmp_path: Path
    ) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "preserve_holes",
                    "target_type": "preserved_interface",
                    "description": "Holes preserved",
                    "comparator": "preserve",
                    "priority": "critical",
                    "protected_features": [
                        {"feature_id": "hole_001", "feature_type": "circular_hole"}
                    ],
                }
            ]
        )
        pkg = _build_package(tmp_path, {"task/design_targets.yaml": targets_yaml})
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        item = block["items"][0]
        assert item["status"] == "not_evaluated"

    def test_evidence_index_unchanged_after_comparison(self, tmp_path: Path) -> None:
        """The comparison block must not write to or mutate evidence_index.json."""
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "stress_limit",
                    "target_type": "maximum_von_mises_stress",
                    "description": "Allowable stress",
                    "comparator": "<=",
                    "threshold": 350.0,
                    "unit": "MPa",
                    "priority": "high",
                }
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {"max_von_mises_stress": {"value": 298.0, "unit": "MPa"}}
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        with zipfile.ZipFile(pkg, "r") as zf:
            before_names = set(zf.namelist())
        _ = generate_cae_result_summary(pkg)
        with zipfile.ZipFile(pkg, "r") as zf:
            after_names = set(zf.namelist())
        # No new evidence or claim resources should be created
        new_files = after_names - before_names
        assert not any("evidence" in f or "claim" in f for f in new_files)

    def test_emitted_block_validates_against_schema(self, tmp_path: Path) -> None:
        """The emitted design_target_comparisons block must conform to
        schemas/design_target_comparison.schema.json across all four statuses."""
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "pass_one",
                    "target_type": "maximum_von_mises_stress",
                    "description": "Stress pass",
                    "comparator": "<=",
                    "threshold": 350.0,
                    "unit": "MPa",
                    "priority": "high",
                },
                {
                    "target_id": "fail_one",
                    "target_type": "minimum_safety_factor",
                    "description": "SF fail",
                    "comparator": ">=",
                    "threshold": 2.5,
                    "priority": "critical",
                },
                {
                    "target_id": "unknown_one",
                    "target_type": "maximum_displacement",
                    "description": "Disp not in metrics",
                    "comparator": "<=",
                    "threshold": 1.0,
                    "unit": "mm",
                    "priority": "medium",
                },
                {
                    "target_id": "not_eval_one",
                    "target_type": "objective_priority",
                    "description": "Priority policy",
                    "comparator": "priority",
                    "priority": "critical",
                    "objective_order": ["a", "b"],
                },
            ]
        )
        pkg = _build_package(
            tmp_path,
            {
                "results/computed_metrics.json": _computed_metrics_json(
                    {
                        "max_von_mises_stress": {"value": 298.0, "unit": "MPa"},
                        "minimum_safety_factor": {"value": 2.0, "unit": None},
                    }
                ),
                "task/design_targets.yaml": targets_yaml,
            },
        )
        block = generate_cae_result_summary(pkg)["design_target_comparisons"]
        _validate_against_block_schema(block)
        assert block["summary"]["pass"] == 1
        assert block["summary"]["fail"] == 1
        assert block["summary"]["unknown"] == 1
        assert block["summary"]["not_evaluated"] == 1


# ---------------------------------------------------------------------------
# Phase 35 PR 2 — validate.py cross-field semantic checks
# ---------------------------------------------------------------------------


class TestDesignTargetSemanticValidation:
    """validate.py must reject malformed cross-field combinations."""

    def _empty_pkg_with_targets(self, tmp_path: Path, targets_yaml: bytes) -> Path:
        return _build_package(tmp_path, {"task/design_targets.yaml": targets_yaml})

    def _fails(self, pkg: Path) -> list[str]:
        from aieng.validate import Level, validate_package

        report = validate_package(pkg)
        return [m.text for m in report.messages if m.level is Level.FAIL]

    def test_within_range_missing_thresholds_rejected(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "bad_range",
                    "target_type": "absolute_mass_target",
                    "description": "Bad range",
                    "comparator": "within_range",
                    "priority": "medium",
                }
            ]
        )
        pkg = self._empty_pkg_with_targets(tmp_path, targets_yaml)
        fails = self._fails(pkg)
        assert any("threshold_min" in f for f in fails)
        assert any("threshold_max" in f for f in fails)

    def test_quantitative_comparator_requires_threshold(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "no_threshold",
                    "target_type": "maximum_von_mises_stress",
                    "description": "Missing threshold",
                    "comparator": "<=",
                    "priority": "high",
                }
            ]
        )
        pkg = self._empty_pkg_with_targets(tmp_path, targets_yaml)
        fails = self._fails(pkg)
        assert any("requires threshold" in f for f in fails)

    def test_preserve_requires_protected_entry(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "preserve_nothing",
                    "target_type": "preserved_interface",
                    "description": "Preserve without entries",
                    "comparator": "preserve",
                    "priority": "critical",
                }
            ]
        )
        pkg = self._empty_pkg_with_targets(tmp_path, targets_yaml)
        fails = self._fails(pkg)
        assert any("protected_features or protected_interfaces" in f for f in fails)

    def test_priority_requires_objective_order(self, tmp_path: Path) -> None:
        targets_yaml = _build_targets_yaml(
            [
                {
                    "target_id": "priority_no_order",
                    "target_type": "objective_priority",
                    "description": "Priority without order",
                    "comparator": "priority",
                    "priority": "critical",
                }
            ]
        )
        pkg = self._empty_pkg_with_targets(tmp_path, targets_yaml)
        fails = self._fails(pkg)
        assert any("non-empty objective_order" in f for f in fails)
