"""Tests for the advisory next_actions schema normalizer."""

from __future__ import annotations

from typing import Any

import pytest

from app.next_actions import (
    build_next_action,
    normalize_next_action,
    normalize_next_actions,
)


def test_normalize_next_action_read_only_action() -> None:
    action = normalize_next_action(
        {"tool": "cad.critique", "input": {"project_id": "p1"}, "reason": "Review geometry."},
        source="receipt",
    )
    assert action["tool"] == "cad.critique"
    assert action["input"] == {"project_id": "p1"}
    assert action["reason"] == "Review geometry."
    assert action["source"] == "receipt"
    assert action["requires_approval"] is False
    assert action["mutates_package"] is False
    assert action["runs_solver"] is False
    assert action["advances_claim"] is False
    assert action["available_now"] is True
    assert action["blocked_reason"] is None
    assert action["label"] == "Critique"
    assert action["priority"] == "medium"
    assert action["id"].startswith("cad_critique_")


def test_normalize_next_action_approval_gated_mutating_action() -> None:
    action = normalize_next_action(
        {"tool": "cad.execute_build123d", "input": {"project_id": "p1", "code": "..."}, "reason": "Build."},
        source="receipt",
    )
    assert action["requires_approval"] is False
    assert action["mutates_package"] is True
    assert action["runs_solver"] is False
    assert action["advances_claim"] is False


def test_normalize_next_action_solver_running_action() -> None:
    action = normalize_next_action(
        {
            "tool": "cae.run_solver",
            "input": {"project_id": "p1", "input_deck_path": "simulation/runs/run_001/solver_input.inp"},
            "reason": "Run solver.",
        },
        source="preflight",
    )
    assert action["requires_approval"] is True
    assert action["mutates_package"] is True
    assert action["runs_solver"] is True
    assert action["advances_claim"] is False
    assert action["label"] == "Run Solver"


def test_normalize_next_action_blocked_action() -> None:
    action = normalize_next_action(
        {
            "tool": "cae.run_solver",
            "input": {"project_id": "p1"},
            "reason": "Run the solver once ready.",
            "available_now": False,
            "blocked_reason": "Missing mesh and solver settings.",
            "priority": "high",
        },
        source="preflight",
    )
    assert action["available_now"] is False
    assert action["blocked_reason"] == "Missing mesh and solver settings."
    assert action["priority"] == "high"
    assert action["runs_solver"] is True


def test_normalize_next_action_handles_tool_none_as_blocked() -> None:
    action = normalize_next_action(
        {"action": "Install CalculiX and ensure ccx is on PATH.", "reason": "Solver not found."},
        source="preflight",
    )
    assert action["tool"] == ""
    assert action["available_now"] is False
    assert action["blocked_reason"] == "Solver not found."


def test_normalize_next_action_converts_legacy_shape() -> None:
    legacy = {
        "tool": "cae.generate_solver_input",
        "input": {"project_id": "p1", "run_id": "run_001"},
        "reason": "Generate input deck.",
        "requires_approval": True,
    }
    action = normalize_next_action(legacy, source="legacy")
    assert action["tool"] == "cae.generate_solver_input"
    assert action["input"] == {"project_id": "p1", "run_id": "run_001"}
    assert action["reason"] == "Generate input deck."
    assert action["source"] == "legacy"
    assert action["requires_approval"] is True
    assert action["mutates_package"] is True
    assert action["id"].startswith("cae_generate_solver_input_")


def test_normalize_next_action_preserves_truthy_legacy_safety_flags() -> None:
    action = normalize_next_action(
        {
            "tool": "external.custom_solver",
            "input": {"project_id": "p1"},
            "reason": "Run through an external gated workflow.",
            "requires_approval": True,
            "mutates_package": True,
            "runs_solver": True,
            "advances_claim": True,
        },
        source="legacy",
    )
    assert action["requires_approval"] is True
    assert action["mutates_package"] is True
    assert action["runs_solver"] is True
    assert action["advances_claim"] is True


def test_normalize_next_action_false_flags_do_not_downgrade_inferred_safety() -> None:
    action = normalize_next_action(
        {
            "tool": "cae.run_solver",
            "input": {"project_id": "p1", "input_deck_path": "simulation/runs/run_001/solver_input.inp"},
            "reason": "Run solver.",
            "requires_approval": False,
            "mutates_package": False,
            "runs_solver": False,
        },
        source="legacy",
    )
    assert action["requires_approval"] is True
    assert action["mutates_package"] is True
    assert action["runs_solver"] is True


def test_normalize_next_actions_assigns_priority_by_position() -> None:
    actions = normalize_next_actions(
        [
            {"tool": "cad.critique", "input": {}, "reason": "First."},
            {"tool": "cad.edit_parameter", "input": {}, "reason": "Second."},
        ],
        source="receipt",
    )
    assert actions[0]["priority"] == "high"
    assert actions[1]["priority"] == "medium"


def test_normalize_next_actions_ignores_non_list_input() -> None:
    assert normalize_next_actions("not a list", source="receipt") == []
    assert normalize_next_actions({"tool": "cad.critique"}, source="receipt") == []


def test_build_next_action_explicit_fields() -> None:
    action = build_next_action(
        "opt.accept_candidate",
        {"project_id": "p1", "candidate_id": "c1"},
        "Accept the best candidate.",
        source="ranking",
        label="Accept Candidate",
        priority="high",
        available_now=False,
        blocked_reason="Pending human review.",
    )
    assert action["tool"] == "opt.accept_candidate"
    assert action["label"] == "Accept Candidate"
    assert action["priority"] == "high"
    assert action["source"] == "ranking"
    assert action["requires_approval"] is True
    assert action["mutates_package"] is True
    assert action["advances_claim"] is True
    assert action["available_now"] is False
    assert action["blocked_reason"] == "Pending human review."


def test_build_next_action_copies_input() -> None:
    inp: dict[str, Any] = {"project_id": "p1"}
    action = build_next_action("cad.critique", inp, "Review.")
    assert action["input"] is not inp
    assert action["input"] == inp
