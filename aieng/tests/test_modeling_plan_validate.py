from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from aieng.modeling_plan.validate import (
    PlanValidationMessage,
    PlanValidationReport,
    validate_modeling_plan,
    validate_modeling_plan_file,
)


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "modeling_plan.schema.json"


def _make_plan(**overrides) -> dict:
    """Return a minimal valid modeling plan."""
    plan = {
        "plan_id": "plan_001",
        "plan_schema_version": "0.1.0",
        "intent": {"original_text": "create a 120x80x10 plate"},
        "units": {"length": "mm", "angle": "deg"},
        "steps": [
            {
                "step_id": "step_001",
                "operation": "create_box",
                "creates": "base_plate",
                "parameters": {
                    "length": 120.0,
                    "width": 80.0,
                    "height": 10.0,
                    "name": "base_plate",
                },
            }
        ],
    }
    plan.update(overrides)
    return plan


class TestSchemaSelfValidation:
    """Ensure the schema file itself is valid Draft 2020-12."""

    def test_schema_is_valid_draft_2020_12(self) -> None:
        schema_path = _schema_path()
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


class TestValidPlans:
    def test_valid_minimal_plan(self) -> None:
        plan = _make_plan()
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert report.ok
        assert any("Schema validation passed" in m.text for m in report.messages)

    def test_valid_plan_with_cut(self) -> None:
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "base_plate",
                    "parameters": {
                        "length": 120.0,
                        "width": 80.0,
                        "height": 10.0,
                        "name": "base_plate",
                    },
                },
                {
                    "step_id": "step_002",
                    "operation": "create_cylindrical_cut",
                    "creates": "hole_01",
                    "target": "step_001",
                    "parameters": {
                        "radius": 5.0,
                        "depth": 10.0,
                        "position": [10.0, 20.0, 0.0],
                        "name": "hole_01",
                    },
                },
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert report.ok, report.render()

    def test_origin_mode_omitted_defaults_to_corner(self) -> None:
        plan = _make_plan()
        # origin_mode is not present; schema allows omission
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert report.ok

    def test_confidence_certain(self) -> None:
        plan = _make_plan()
        plan["steps"][0]["confidence"] = "certain"
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert report.ok

    def test_confidence_inferred(self) -> None:
        plan = _make_plan()
        plan["steps"][0]["confidence"] = "inferred"
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert report.ok

    def test_valid_plan_with_checks(self) -> None:
        plan = _make_plan(
            checks=[
                {
                    "check_type": "bounding_box",
                    "parameters": {
                        "expected_size": [120.0, 80.0, 10.0],
                        "tolerance": 0.5,
                        "origin_mode": "corner",
                    },
                },
                {
                    "check_type": "operation_count",
                    "parameters": {
                        "by_operation": {"create_box": 1},
                    },
                },
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert report.ok


class TestSchemaInvalidations:
    def test_missing_required_plan_id(self) -> None:
        plan = _make_plan()
        del plan["plan_id"]
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("plan_id" in m.text for m in report.messages)

    def test_missing_intent_original_text(self) -> None:
        plan = _make_plan(intent={})
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("original_text" in m.text for m in report.messages)

    def test_empty_intent_original_text(self) -> None:
        plan = _make_plan(intent={"original_text": ""})
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok

    def test_family_operation_create_plate(self) -> None:
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_plate",
                    "creates": "plate",
                    "parameters": {"length": 100, "width": 50, "thickness": 5},
                }
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("create_plate" in m.text for m in report.messages)

    def test_target_in_parameters(self) -> None:
        """target must live at the step level, not inside parameters."""
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "base",
                    "parameters": {
                        "length": 100.0,
                        "width": 50.0,
                        "height": 5.0,
                        "target": "step_000",  # illegal here
                    },
                }
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("target" in m.text.lower() or "additionalProperties" in m.text for m in report.messages)

    def test_invalid_confidence_number(self) -> None:
        plan = _make_plan()
        plan["steps"][0]["confidence"] = 0.9
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok

    def test_invalid_confidence_string(self) -> None:
        plan = _make_plan()
        plan["steps"][0]["confidence"] = "high"
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok


class TestLogicValidations:
    def test_duplicate_step_id(self) -> None:
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "body_a",
                    "parameters": {"length": 10, "width": 10, "height": 10, "name": "a"},
                },
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "body_b",
                    "parameters": {"length": 20, "width": 20, "height": 20, "name": "b"},
                },
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("Duplicate step_id" in m.text for m in report.messages)

    def test_duplicate_creates(self) -> None:
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "body",
                    "parameters": {"length": 10, "width": 10, "height": 10, "name": "a"},
                },
                {
                    "step_id": "step_002",
                    "operation": "create_box",
                    "creates": "body",
                    "parameters": {"length": 20, "width": 20, "height": 20, "name": "b"},
                },
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("Duplicate creates" in m.text for m in report.messages)

    def test_unresolved_target(self) -> None:
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_cylindrical_cut",
                    "creates": "hole",
                    "target": "step_999",
                    "parameters": {
                        "radius": 5.0,
                        "depth": 10.0,
                        "position": [0.0, 0.0, 0.0],
                        "name": "hole",
                    },
                }
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("Target 'step_999' does not resolve" in m.text for m in report.messages)

    def test_missing_target(self) -> None:
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "base",
                    "parameters": {"length": 100, "width": 50, "height": 5, "name": "base"},
                },
                {
                    "step_id": "step_002",
                    "operation": "create_cylindrical_cut",
                    "creates": "hole",
                    # missing "target"
                    "parameters": {
                        "radius": 5.0,
                        "depth": 10.0,
                        "position": [0.0, 0.0, 0.0],
                        "name": "hole",
                    },
                },
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("Missing 'target'" in m.text for m in report.messages)

    def test_missing_box_parameters(self) -> None:
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "base",
                    "parameters": {
                        "width": 50.0,
                        "height": 5.0,
                        # missing length
                    },
                }
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("length" in m.text for m in report.messages)

    def test_missing_cut_parameters(self) -> None:
        plan = _make_plan(
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "base",
                    "parameters": {"length": 100, "width": 50, "height": 5, "name": "base"},
                },
                {
                    "step_id": "step_002",
                    "operation": "create_cylindrical_cut",
                    "creates": "hole",
                    "target": "step_001",
                    "parameters": {
                        "radius": 5.0,
                        # missing depth and position
                    },
                },
            ]
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert not report.ok
        assert any("depth" in m.text or "position" in m.text for m in report.messages)

    def test_missing_assumption_ref_warns(self) -> None:
        plan = _make_plan(
            assumptions=[
                {"id": "assumption_001", "text": "Material is steel"},
            ],
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "base",
                    "parameters": {"length": 100, "width": 50, "height": 5, "name": "base"},
                    "assumption_refs": ["assumption_001", "assumption_999"],
                }
            ],
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert report.ok  # WARN does not fail the report
        assert report.has_warnings
        assert any("assumption_999" in m.text and m.level == "WARN" for m in report.messages)

    def test_valid_assumption_ref_passes(self) -> None:
        plan = _make_plan(
            assumptions=[
                {"id": "assumption_001", "text": "Material is steel"},
            ],
            steps=[
                {
                    "step_id": "step_001",
                    "operation": "create_box",
                    "creates": "base",
                    "parameters": {"length": 100, "width": 50, "height": 5, "name": "base"},
                    "assumption_refs": ["assumption_001"],
                }
            ],
        )
        report = validate_modeling_plan(plan, schema_path=_schema_path())
        assert report.ok
        assert not report.has_warnings


class TestPlanValidationReport:
    def test_ok_property(self) -> None:
        report = PlanValidationReport(
            messages=(
                PlanValidationMessage("PASS", "Schema ok"),
                PlanValidationMessage("PASS", "Logic ok"),
            )
        )
        assert report.ok

    def test_not_ok_when_fail_present(self) -> None:
        report = PlanValidationReport(
            messages=(
                PlanValidationMessage("PASS", "Schema ok"),
                PlanValidationMessage("FAIL", "Bad step"),
            )
        )
        assert not report.ok

    def test_ok_with_warnings(self) -> None:
        report = PlanValidationReport(
            messages=(
                PlanValidationMessage("PASS", "Schema ok"),
                PlanValidationMessage("WARN", "Something odd"),
            )
        )
        assert report.ok
        assert report.has_warnings

    def test_render(self) -> None:
        report = PlanValidationReport(
            messages=(
                PlanValidationMessage("PASS", "Schema ok"),
                PlanValidationMessage("FAIL", "Bad step", "step_001"),
            )
        )
        rendered = report.render()
        assert "PASS Schema ok" in rendered
        assert "FAIL [step_001] Bad step" in rendered


class TestFileInterface:
    def test_validate_modeling_plan_file(self, tmp_path: Path) -> None:
        plan = _make_plan()
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        report = validate_modeling_plan_file(plan_file, schema_path=_schema_path())
        assert report.ok
