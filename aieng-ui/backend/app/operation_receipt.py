"""Best-effort, descriptive receipt builder for MCP/runtime tool responses.

A receipt is additive metadata that summarizes what an operation did, what it
wrote or refreshed, and what a client can safely do next. It is **not** a second
source of truth: the underlying tool-specific response fields remain
authoritative. Receipt assembly failures are swallowed and surfaced as warnings
so they never turn a successful operation into an error.
"""

from __future__ import annotations

import logging
from typing import Any

from . import next_actions as _next_actions

LOGGER = logging.getLogger("app.operation_receipt")

RECEIPT_FORMAT = "aieng.operation_receipt.v0"


def _normalize_artifact(artifact: Any, kind: str = "artifact") -> dict[str, Any]:
    """Normalize a written artifact entry into a receipt artifact dict."""
    if isinstance(artifact, dict):
        return {
            "path": str(artifact.get("path") or ""),
            "kind": str(artifact.get("kind") or kind),
            "role": str(artifact.get("role") or ""),
        }
    if isinstance(artifact, str):
        return {"path": artifact, "kind": kind, "role": ""}
    return {"path": str(artifact), "kind": kind, "role": ""}


def _normalize_warnings(result: dict[str, Any]) -> list[str]:
    """Extract a flat list of warning strings from a result dict.

    Non-list warning payloads are treated as empty so a malformed upstream
    field cannot break receipt assembly.
    """
    warnings = result.get("warnings")
    if isinstance(warnings, list):
        return [str(w) for w in warnings if w is not None]
    return []


def _as_list(value: Any) -> list[Any]:
    """Coerce an expected list value into a list, ignoring other shapes.

    Dicts and strings are intentionally treated as non-list values so they
    are not silently iterated key-by-key or character-by-character.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def build_receipt(
    *,
    operation: str,
    status: str,
    mutated: bool,
    approval_required: bool = False,
    approval_used: bool | None = None,
    artifacts_written: list[dict[str, Any]] | None = None,
    artifacts_read: list[dict[str, Any]] | None = None,
    evidence_created: list[dict[str, Any]] | None = None,
    stale_artifacts: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    summary: str = "",
    next_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a standardized operation receipt.

    All fields are descriptive metadata. Callers must keep the underlying tool
    response fields authoritative and must not derive engineering claims solely
    from the receipt.
    """
    normalized_next_actions = _next_actions.normalize_next_actions(
        next_actions, source="receipt"
    )

    return {
        "format": RECEIPT_FORMAT,
        "operation": str(operation),
        "status": str(status),
        "mutated": bool(mutated),
        "approval_required": bool(approval_required),
        "approval_used": None if approval_used is None else bool(approval_used),
        "artifacts_written": list(artifacts_written or []),
        "artifacts_read": list(artifacts_read or []),
        "evidence_created": list(evidence_created or []),
        "stale_artifacts": list(stale_artifacts or []),
        "warnings": list(warnings or []),
        "summary": str(summary),
        "next_actions": normalized_next_actions,
    }


def attach_receipt(result: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any] | None:
    """Best-effort attach a receipt to a tool result dict.

    If ``result`` is not a dict, or receipt assembly fails, the original result
    is returned unchanged (with a logged warning). This guarantees that a receipt
    bug can never turn a successful CAD/CAE operation into a failure.
    """
    if not isinstance(result, dict):
        return result
    try:
        result["receipt"] = build_receipt(**kwargs)
    except Exception as exc:  # noqa: BLE001
        message = f"receipt_assembly_failed: {exc}"
        LOGGER.warning(message)
        existing_warnings = result.get("warnings")
        if isinstance(existing_warnings, list):
            try:
                existing_warnings.append(message)
            except Exception:  # noqa: BLE001
                result["warnings"] = [message]
        else:
            result["warnings"] = [message]
    return result


def receipt_from_execute_build123d(
    result: dict[str, Any], operation: str = "cad.execute_build123d"
) -> dict[str, Any]:
    """Build and attach a receipt for a ``cad.execute_build123d`` result."""
    if not isinstance(result, dict):
        return result
    status = "ok" if result.get("status") == "ok" else "error"
    artifacts_written = [
        _normalize_artifact(p, kind="cad_geometry")
        for p in _as_list(result.get("written_artifacts"))
    ]
    warnings = _normalize_warnings(result)
    summary = (
        f"CAD build completed ({len(artifacts_written)} artifact(s) written)."
        if status == "ok"
        else f"CAD build failed: {result.get('message', 'unknown error')}"
    )
    next_actions: list[dict[str, Any]] = []
    if status == "ok":
        next_actions.append(
            {
                "tool": "cad.critique",
                "input": {"project_id": result.get("project_id")},
                "reason": "Review the new geometry for manufacturability issues.",
            }
        )
    return attach_receipt(
        result,
        operation=operation,
        status=status,
        mutated=(status == "ok"),
        approval_required=False,
        approval_used=None,
        artifacts_written=artifacts_written,
        warnings=warnings,
        summary=summary,
        next_actions=next_actions,
    )


def receipt_from_edit_parameter(result: dict[str, Any]) -> dict[str, Any]:
    """Build and attach a receipt for a ``cad.edit_parameter`` result."""
    if not isinstance(result, dict):
        return result
    status = "ok" if result.get("status") == "ok" else "error"
    artifacts_written = [
        _normalize_artifact(p, kind="cad_geometry")
        for p in _as_list(result.get("written_artifacts"))
    ]
    warnings = _normalize_warnings(result)
    param_name = result.get("cad_parameter_name") or result.get("parameter_name") or "parameter"
    summary = (
        f"Parametric edit applied to {param_name}."
        if status == "ok"
        else f"Parametric edit failed: {result.get('message', 'unknown error')}"
    )
    next_actions: list[dict[str, Any]] = []
    if status == "ok":
        next_actions.append(
            {
                "tool": "cad.critique",
                "input": {"project_id": result.get("project_id")},
                "reason": "Verify the edit did not introduce manufacturability regressions.",
            }
        )
    return attach_receipt(
        result,
        operation="cad.edit_parameter",
        status=status,
        mutated=(status == "ok"),
        approval_required=False,
        approval_used=None,
        artifacts_written=artifacts_written,
        warnings=warnings,
        summary=summary,
        next_actions=next_actions,
    )


def receipt_from_prepare_solver_run(result: dict[str, Any]) -> dict[str, Any]:
    """Build and attach a receipt for a ``cae.prepare_solver_run`` result."""
    if not isinstance(result, dict):
        return result
    ready = bool(result.get("ready_to_run"))
    status = "ok" if ready else "warning"
    warnings = _normalize_warnings(result)
    planned = [_normalize_artifact(a, kind="planned_solver_output") for a in _as_list(result.get("planned_artifacts"))]
    stale = [_normalize_artifact(a, kind="stale") for a in _as_list(result.get("stale_artifacts"))]
    if isinstance(result.get("next_actions"), list):
        next_actions = result["next_actions"]
    else:
        next_actions = [
            {
                "tool": rec.get("tool", ""),
                "input": rec.get("input") if isinstance(rec.get("input"), dict) else {},
                "reason": rec.get("reason", ""),
            }
            for rec in _as_list(result.get("recommended_next_calls"))
            if isinstance(rec, dict)
        ]
    summary = f"Solver preflight complete; ready_to_run={ready}."
    return attach_receipt(
        result,
        operation="cae.prepare_solver_run",
        status=status,
        mutated=False,
        approval_required=bool(result.get("requires_approval")),
        approval_used=None,
        artifacts_read=planned,
        stale_artifacts=stale,
        warnings=warnings,
        summary=summary,
        next_actions=next_actions,
    )


def receipt_from_run_solver(result: dict[str, Any]) -> dict[str, Any]:
    """Build and attach a receipt for a ``cae.run_solver`` result."""
    if not isinstance(result, dict):
        return result
    ok = bool(result.get("ok"))
    tool_status = str(result.get("status") or "")
    solver_executed = bool(result.get("solver_execution_performed"))
    solver_succeeded = ok and tool_status == "completed"
    status = "ok" if solver_succeeded else "error"
    artifacts_written = [
        _normalize_artifact(a, kind="solver_output")
        for a in _as_list(result.get("changed_artifacts"))
    ]
    evidence_created = [
        _normalize_artifact(a, kind="evidence")
        for a in _as_list(result.get("evidence_imported"))
    ]
    warnings = _normalize_warnings(result)
    errors = result.get("errors")
    if isinstance(errors, list):
        for err in errors:
            if err is not None:
                warnings.append(f"solver_error: {err}")
    next_actions: list[dict[str, Any]] = []
    if solver_succeeded and solver_executed:
        next_actions.append(
            {
                "tool": "cae.extract_solver_results",
                "input": {"project_id": result.get("project_id"), "run_id": result.get("run_id")},
                "reason": "Extract computed metrics from the solver result file.",
            }
        )
    if solver_succeeded:
        summary = f"Solver run completed (executed={solver_executed})."
    elif solver_executed and tool_status == "failed":
        summary = f"Solver run failed with return_code={result.get('return_code', 'unknown')}."
    else:
        summary = f"Solver run failed: {result.get('message', 'unknown error')}"
    return attach_receipt(
        result,
        operation="cae.run_solver",
        status=status,
        mutated=solver_executed,
        approval_required=True,
        approval_used=solver_executed if solver_executed else None,
        artifacts_written=artifacts_written,
        evidence_created=evidence_created,
        warnings=warnings,
        summary=summary,
        next_actions=next_actions,
    )
