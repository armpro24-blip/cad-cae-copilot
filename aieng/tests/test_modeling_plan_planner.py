from __future__ import annotations

from aieng.modeling_plan.planner import RuleBasedModelingPlanner
from aieng.modeling_plan.validate import validate_modeling_plan


def _schema_path() -> None:
    from pathlib import Path
    return Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "modeling_plan.schema.json"


class TestRuleBasedModelingPlanner:
    def test_plate_with_four_holes_emits_box_and_cuts(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 rectangular plate with 4 mounting holes")

        ops = [s["operation"] for s in plan["steps"]]
        assert ops.count("create_box") == 1
        assert ops.count("create_cylindrical_cut") == 4
        assert "create_plate" not in ops
        assert "create_bracket" not in ops

        # Validate against schema
        report = validate_modeling_plan(plan)
        assert report.ok, report.render()

    def test_missing_dimensions_create_assumptions(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a rectangular plate with 4 mounting holes")

        box_step = plan["steps"][0]
        assert box_step["operation"] == "create_box"
        # Should use defaults
        assert box_step["parameters"]["length"] == 100.0

        # Must have at least one assumption requiring confirmation
        requiring = [a for a in plan["assumptions"] if a.get("requires_user_confirmation")]
        assert len(requiring) >= 1

        report = validate_modeling_plan(plan)
        assert report.ok, report.render()

    def test_default_units_mm_assumption(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 plate")

        assert plan["units"]["length"] == "mm"

        # Should record an assumption about default unit
        unit_assumptions = [a for a in plan["assumptions"] if "mm" in a["text"]]
        assert len(unit_assumptions) >= 1

        report = validate_modeling_plan(plan)
        assert report.ok, report.render()

    def test_explicit_units_are_respected(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 cm plate")
        assert plan["units"]["length"] == "cm"

        report = validate_modeling_plan(plan)
        assert report.ok, report.render()

    def test_target_references_prior_box_step(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 plate with 4 holes")

        cuts = [s for s in plan["steps"] if s["operation"] == "create_cylindrical_cut"]
        assert len(cuts) == 4
        for cut in cuts:
            assert cut["target"] == "step_001"

        report = validate_modeling_plan(plan)
        assert report.ok, report.render()

    def test_plan_has_operation_count_check(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 plate with 4 holes")

        checks = plan.get("checks", [])
        op_count_checks = [c for c in checks if c["check_type"] == "operation_count"]
        assert len(op_count_checks) == 1

        by_op = op_count_checks[0]["parameters"]["by_operation"]
        assert by_op.get("create_box") == 1
        assert by_op.get("create_cylindrical_cut") == 4

        report = validate_modeling_plan(plan)
        assert report.ok, report.render()

    def test_no_holes_no_cuts(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 solid block")

        ops = [s["operation"] for s in plan["steps"]]
        assert ops == ["create_box"]

        report = validate_modeling_plan(plan)
        assert report.ok, report.render()

    def test_by_dimensions_parsing(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a plate 120 by 80 by 10 mm")

        box = plan["steps"][0]
        assert box["parameters"]["length"] == 120.0
        assert box["parameters"]["width"] == 80.0
        assert box["parameters"]["height"] == 10.0

        report = validate_modeling_plan(plan)
        assert report.ok, report.render()

    def test_hole_depth_exceeds_height(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 plate with 4 holes")

        cuts = [s for s in plan["steps"] if s["operation"] == "create_cylindrical_cut"]
        for cut in cuts:
            assert cut["parameters"]["depth"] > plan["steps"][0]["parameters"]["height"]

    def test_plan_has_intent_and_units(self) -> None:
        planner = RuleBasedModelingPlanner()
        intent = "create a 120x80x10 plate with 4 holes"
        plan = planner.plan(intent)

        assert plan["intent"]["original_text"] == intent
        assert plan["intent"]["interpreted_goal"]
        assert plan["units"]["length"] == "mm"
        assert plan["units"]["angle"] == "deg"

    def test_confidence_levels_present(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 plate with 4 holes")

        for step in plan["steps"]:
            assert step["confidence"] in {"certain", "inferred", "guessed"}

    def test_number_word_hole_count(self) -> None:
        planner = RuleBasedModelingPlanner()
        plan = planner.plan("create a 120x80x10 plate with four holes")

        cuts = [s for s in plan["steps"] if s["operation"] == "create_cylindrical_cut"]
        assert len(cuts) == 4

        report = validate_modeling_plan(plan)
        assert report.ok, report.render()
