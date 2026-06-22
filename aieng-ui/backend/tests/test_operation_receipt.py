"""Tests for the operation receipt builder and tool-specific receipt helpers."""

from __future__ import annotations

from typing import Any

import pytest

from app.operation_receipt import (
    attach_receipt,
    build_receipt,
    receipt_from_edit_parameter,
    receipt_from_execute_build123d,
    receipt_from_prepare_solver_run,
    receipt_from_run_solver,
)


def test_build_receipt_has_expected_schema() -> None:
    receipt = build_receipt(
        operation="cad.test",
        status="ok",
        mutated=True,
        approval_required=False,
        approval_used=None,
        artifacts_written=[{"path": "a.step", "kind": "geometry", "role": "artifact"}],
        artifacts_read=[{"path": "b.inp", "kind": "solver_input", "role": "artifact"}],
        evidence_created=[{"path": "c.json", "kind": "metrics", "role": "evidence"}],
        stale_artifacts=[{"path": "d.json", "kind": "summary", "role": "stale"}],
        warnings=["one"],
        summary="A test receipt.",
        next_actions=[
            {
                "tool": "cad.critique",
                "input": {"project_id": "p1"},
                "reason": "Check it.",
            }
        ],
    )
    assert receipt["format"] == "aieng.operation_receipt.v0"
    assert receipt["operation"] == "cad.test"
    assert receipt["status"] == "ok"
    assert receipt["mutated"] is True
    assert receipt["approval_required"] is False
    assert receipt["approval_used"] is None
    assert receipt["artifacts_written"] == [{"path": "a.step", "kind": "geometry", "role": "artifact"}]
    assert receipt["artifacts_read"] == [{"path": "b.inp", "kind": "solver_input", "role": "artifact"}]
    assert receipt["evidence_created"] == [{"path": "c.json", "kind": "metrics", "role": "evidence"}]
    assert receipt["stale_artifacts"] == [{"path": "d.json", "kind": "summary", "role": "stale"}]
    assert receipt["warnings"] == ["one"]
    assert receipt["summary"] == "A test receipt."
    assert len(receipt["next_actions"]) == 1
    action = receipt["next_actions"][0]
    assert action["tool"] == "cad.critique"
    assert action["requires_approval"] is False
    assert action["mutates_package"] is False
    assert action["runs_solver"] is False
    assert action["advances_claim"] is False


def test_build_receipt_solver_action_has_safety_flags() -> None:
    receipt = build_receipt(
        operation="cae.prepare_solver_run",
        status="ok",
        mutated=False,
        next_actions=[{"tool": "cae.run_solver", "input": {}, "reason": "Run it."}],
    )
    action = receipt["next_actions"][0]
    assert action["requires_approval"] is True
    assert action["mutates_package"] is True
    assert action["runs_solver"] is True
    assert action["advances_claim"] is False


def test_build_receipt_next_actions_use_standardized_schema() -> None:
    receipt = build_receipt(
        operation="cad.test",
        status="ok",
        mutated=True,
        next_actions=[{"tool": "cad.critique", "input": {"project_id": "p1"}, "reason": "Check it."}],
    )
    action = receipt["next_actions"][0]
    assert set(action.keys()) >= {
        "id", "label", "priority", "source", "tool", "input", "reason",
        "requires_approval", "mutates_package", "runs_solver", "advances_claim",
        "available_now", "blocked_reason",
    }
    assert action["source"] == "receipt"
    assert action["label"] == "Critique"
    assert action["priority"] == "high"
    assert action["available_now"] is True
    assert action["blocked_reason"] is None


def test_build_receipt_claim_advancing_action_flagged() -> None:
    receipt = build_receipt(
        operation="opt.rank_candidates",
        status="ok",
        mutated=False,
        next_actions=[{"tool": "opt.accept_candidate", "input": {}, "reason": "Accept."}],
    )
    action = receipt["next_actions"][0]
    assert action["requires_approval"] is True
    assert action["advances_claim"] is True


def test_attach_receipt_adds_receipt_to_result() -> None:
    result: dict[str, Any] = {"status": "ok", "project_id": "p1"}
    out = attach_receipt(result, operation="cad.test", status="ok", mutated=True)
    assert out is result
    assert "receipt" in result
    assert result["receipt"]["operation"] == "cad.test"
    # Original fields are preserved.
    assert result["status"] == "ok"
    assert result["project_id"] == "p1"


def test_attach_receipt_isolation_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A receipt assembly failure must not mutate the underlying result status."""
    result: dict[str, Any] = {"status": "ok"}

    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.operation_receipt.build_receipt", _boom)
    out = attach_receipt(result, operation="cad.test", status="ok", mutated=True)
    assert out is result
    assert "receipt" not in result
    assert result["warnings"] == ["receipt_assembly_failed: boom"]
    assert result["status"] == "ok"


def test_attach_receipt_skips_non_dict_result() -> None:
    assert attach_receipt("not a dict", operation="cad.test", status="ok", mutated=True) == "not a dict"


@pytest.mark.parametrize("bad_artifacts", [{"path": "x"}, "not-a-list", 123, object()])
def test_receipt_from_execute_build123d_tolerates_non_list_artifact_payload(bad_artifacts: Any) -> None:
    """Non-list artifact payloads must not crash receipt assembly."""
    result = receipt_from_execute_build123d(
        {
            "status": "ok",
            "project_id": "p1",
            "written_artifacts": bad_artifacts,
            "warnings": [],
        }
    )
    receipt = result["receipt"]
    assert receipt["status"] == "ok"
    assert receipt["artifacts_written"] == []


def test_receipt_from_execute_build123d_success() -> None:
    result = receipt_from_execute_build123d(
        {
            "status": "ok",
            "project_id": "p1",
            "written_artifacts": ["geometry/generated.step", "geometry/preview.glb"],
            "warnings": [],
        }
    )
    receipt = result["receipt"]
    assert receipt["operation"] == "cad.execute_build123d"
    assert receipt["status"] == "ok"
    assert receipt["mutated"] is True
    assert receipt["approval_required"] is False
    assert receipt["approval_used"] is None
    paths = [a["path"] for a in receipt["artifacts_written"]]
    assert "geometry/generated.step" in paths
    assert "geometry/preview.glb" in paths
    assert any(a["tool"] == "cad.critique" for a in receipt["next_actions"])


def test_receipt_from_execute_build123d_failure() -> None:
    result = receipt_from_execute_build123d(
        {"status": "error", "message": "missing code", "project_id": "p1"}
    )
    receipt = result["receipt"]
    assert receipt["status"] == "error"
    assert receipt["mutated"] is False
    assert "missing code" in receipt["summary"]
    assert receipt["next_actions"] == []


def test_receipt_from_edit_parameter_success() -> None:
    result = receipt_from_edit_parameter(
        {
            "status": "ok",
            "project_id": "p1",
            "feature_id": "f1",
            "parameter_name": "WALL_THICKNESS",
            "cad_parameter_name": "WALL_THICKNESS",
            "written_artifacts": ["geometry/source.py"],
            "warnings": ["ignored"],
        }
    )
    receipt = result["receipt"]
    assert receipt["operation"] == "cad.edit_parameter"
    assert receipt["status"] == "ok"
    assert receipt["mutated"] is True
    assert "WALL_THICKNESS" in receipt["summary"]
    assert receipt["warnings"] == ["ignored"]
    assert any(a["tool"] == "cad.critique" for a in receipt["next_actions"])


def test_receipt_from_prepare_solver_run_not_ready() -> None:
    result = receipt_from_prepare_solver_run(
        {
            "ok": True,
            "tool": "cae.prepare_solver_run",
            "ready_to_run": False,
            "requires_approval": True,
            "planned_artifacts": [{"path": "simulation/runs/run_001/outputs/result.frd", "kind": "frd_result", "role": "primary_result"}],
            "warnings": ["No solver execution was performed."],
            "recommended_next_calls": [
                {
                    "tool": "cae.run_solver",
                    "input": {"project_id": "p1"},
                    "reason": "Run the solver.",
                }
            ],
        }
    )
    receipt = result["receipt"]
    assert receipt["operation"] == "cae.prepare_solver_run"
    assert receipt["status"] == "warning"
    assert receipt["mutated"] is False
    assert receipt["approval_required"] is True
    assert receipt["approval_used"] is None
    assert any(a["path"].endswith("result.frd") for a in receipt["artifacts_read"])
    assert len(receipt["next_actions"]) == 1
    action = receipt["next_actions"][0]
    assert action["tool"] == "cae.run_solver"
    assert action["requires_approval"] is True
    assert action["runs_solver"] is True


def test_receipt_from_run_solver_success() -> None:
    result = receipt_from_run_solver(
        {
            "ok": True,
            "tool": "cae.run_solver",
            "status": "completed",
            "solver_execution_performed": True,
            "run_id": "run_001",
            "project_id": "p1",
            "changed_artifacts": [
                {"path": "simulation/runs/run_001/outputs/result.frd", "kind": "frd_result", "role": "primary_result"}
            ],
            "warnings": [],
            "errors": [],
        }
    )
    receipt = result["receipt"]
    assert receipt["operation"] == "cae.run_solver"
    assert receipt["status"] == "ok"
    assert receipt["mutated"] is True
    assert receipt["approval_required"] is True
    assert receipt["approval_used"] is True
    paths = [a["path"] for a in receipt["artifacts_written"]]
    assert any("result.frd" in p for p in paths)
    assert any(a["tool"] == "cae.extract_solver_results" for a in receipt["next_actions"])


def test_receipt_from_run_solver_nonzero_exit_is_error_without_extract_next_action() -> None:
    result = receipt_from_run_solver(
        {
            "ok": True,
            "tool": "cae.run_solver",
            "status": "failed",
            "solver_execution_performed": True,
            "return_code": 2,
            "run_id": "run_001",
            "project_id": "p1",
            "changed_artifacts": [
                {"path": "simulation/runs/run_001/solver_log.txt", "kind": "solver_log", "role": "diagnostic"}
            ],
            "warnings": [],
            "errors": ["ccx exited nonzero"],
        }
    )
    receipt = result["receipt"]
    assert receipt["operation"] == "cae.run_solver"
    assert receipt["status"] == "error"
    assert receipt["mutated"] is True
    assert receipt["approval_used"] is True
    assert "return_code=2" in receipt["summary"]
    assert "solver_error: ccx exited nonzero" in receipt["warnings"]
    assert receipt["next_actions"] == []


def test_receipt_from_run_solver_failure_preserves_message() -> None:
    result = receipt_from_run_solver(
        {
            "ok": False,
            "tool": "cae.run_solver",
            "status": "error",
            "message": "Solver not found",
            "solver_execution_performed": False,
            "warnings": [],
            "errors": [],
        }
    )
    receipt = result["receipt"]
    assert receipt["status"] == "error"
    assert receipt["mutated"] is False
    assert receipt["approval_required"] is True
    assert "Solver not found" in receipt["summary"]
    assert receipt["approval_used"] is None


def test_receipt_from_run_solver_includes_errors_as_warnings() -> None:
    result = receipt_from_run_solver(
        {
            "ok": True,
            "tool": "cae.run_solver",
            "status": "completed",
            "solver_execution_performed": True,
            "changed_artifacts": [],
            "warnings": [],
            "errors": ["non-fatal issue"],
        }
    )
    receipt = result["receipt"]
    assert "solver_error: non-fatal issue" in receipt["warnings"]
