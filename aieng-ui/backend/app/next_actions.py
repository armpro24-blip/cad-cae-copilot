"""Pure advisory next_actions schema normalizer.

Standardizes action-like payloads from operation receipts, CAE preflight, and
other readiness outputs into a single shape for MCP / VS Code extension clients.
This module performs no package I/O and executes no tools.

The standardized item is a descriptive hint only: callers must keep the
underlying tool-specific recommendation fields authoritative and must not
automatically execute actions without user approval.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import runtime as _runtime

# Tools that execute an external solver.
_SOLVER_TOOLS: frozenset[str] = frozenset({"cae.run_solver"})

# Tools that advance an engineering claim.
_CLAIM_ADVANCING_TOOLS: frozenset[str] = frozenset({"opt.accept_candidate"})


def _tool_metadata(tool_name: str) -> dict[str, Any] | None:
    """Return runtime registry metadata for a tool, if available."""
    try:
        return _runtime.registered_tool_metadata(tool_name)
    except Exception:  # noqa: BLE001
        return None


def _tool_label(tool_name: str) -> str:
    """Human-readable label derived from a tool name.

    >>> _tool_label("cad.execute_build123d")
    'Execute Build123d'
    >>> _tool_label("")
    'Action'
    """
    if not tool_name:
        return "Action"
    name = tool_name.split(".")[-1] if "." in tool_name else tool_name
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = name.replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in name.split())


def _action_id(tool_name: str, input_dict: dict[str, Any]) -> str:
    """Stable, deterministic id from tool name and input."""
    material = json.dumps(
        {"tool": tool_name, "input": input_dict},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    prefix = tool_name.replace(".", "_") if tool_name else "action"
    return f"{prefix}_{digest}"


def _safety_flags(tool_name: str) -> dict[str, bool]:
    """Infer safety flags for a tool.

    ``requires_approval`` comes from runtime registry metadata when available.
    ``mutates_package`` uses tool-name heuristics because the registry's
    ``read_only`` default is based only on the approval flag and can miss
    non-approval-gated mutating tools such as ``cae.generate_solver_input``.
    """
    meta = _tool_metadata(tool_name)
    if meta is not None:
        requires_approval = bool(meta.get("requires_approval", False))
    else:
        requires_approval = tool_name in _SOLVER_TOOLS or tool_name in _CLAIM_ADVANCING_TOOLS
    mutates_package = (
        tool_name.startswith(("cad.execute", "cad.edit_", "cad.replace_", "cad.remove_"))
        or tool_name.startswith(("cae.apply_setup_patch", "cae.run_", "cae.generate_solver_input"))
        or tool_name.startswith("opt.")
        or tool_name in _SOLVER_TOOLS
    )
    return {
        "requires_approval": requires_approval,
        "mutates_package": mutates_package,
        "runs_solver": tool_name in _SOLVER_TOOLS,
        "advances_claim": tool_name in _CLAIM_ADVANCING_TOOLS,
    }


def normalize_next_action(
    raw: Any,
    *,
    source: str = "unknown",
    available_now: bool = True,
    blocked_reason: str | None = None,
    priority: str = "medium",
) -> dict[str, Any]:
    """Convert an action-like payload into a standardized ``next_actions`` item.

    Recognized legacy fields:
      - ``tool``, ``input``, ``reason``, ``action``
      - ``id``, ``label``, ``priority``, ``available_now``, ``blocked_reason``
      - ``requires_approval``, ``mutates_package``, ``runs_solver``,
        ``advances_claim`` (truthy values are preserved as conservative
        overrides; falsey values never downgrade inferred safety flags)

    Items with no executable ``tool`` are marked as blocked so clients do not
    accidentally render them as runnable actions.
    """
    if not isinstance(raw, dict):
        raw = {}
    tool_name = raw.get("tool") if isinstance(raw.get("tool"), str) else ""
    input_dict = raw.get("input") if isinstance(raw.get("input"), dict) else {}
    reason = str(raw.get("reason") or raw.get("action") or "")
    label = str(raw.get("label") or _tool_label(tool_name))

    raw_priority = raw.get("priority")
    if isinstance(raw_priority, str) and raw_priority in {"high", "medium", "low"}:
        priority = raw_priority

    raw_available = raw.get("available_now")
    if isinstance(raw_available, bool):
        available_now = raw_available

    raw_blocked = raw.get("blocked_reason")
    if isinstance(raw_blocked, str):
        blocked_reason = raw_blocked

    if not tool_name:
        available_now = False
        if not blocked_reason:
            blocked_reason = reason or "Action cannot be executed automatically."

    safety = _safety_flags(tool_name)
    safety = {
        "requires_approval": safety["requires_approval"] or raw.get("requires_approval") is True,
        "mutates_package": safety["mutates_package"] or raw.get("mutates_package") is True,
        "runs_solver": safety["runs_solver"] or raw.get("runs_solver") is True,
        "advances_claim": safety["advances_claim"] or raw.get("advances_claim") is True,
    }
    return {
        "id": raw.get("id") if isinstance(raw.get("id"), str) else _action_id(tool_name, input_dict),
        "label": label,
        "priority": priority,
        "source": source,
        "tool": tool_name,
        "input": input_dict,
        "reason": reason,
        "available_now": available_now,
        "blocked_reason": blocked_reason,
        **safety,
    }


def normalize_next_actions(
    actions: Any,
    *,
    source: str = "unknown",
    default_available_now: bool = True,
    blocked_reason: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize a list of action-like payloads into standardized items.

    Non-list inputs are treated as empty. The first item is assigned ``high``
    priority unless it already declares one; remaining items default to
    ``medium``.
    """
    if not isinstance(actions, list):
        return []
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(actions):
        action_priority = "high" if i == 0 else "medium"
        action = normalize_next_action(
            raw,
            source=source,
            available_now=default_available_now,
            blocked_reason=blocked_reason,
            priority=action_priority,
        )
        out.append(action)
    return out


def build_next_action(
    tool: str,
    input_dict: dict[str, Any],
    reason: str,
    *,
    source: str = "unknown",
    label: str | None = None,
    priority: str = "medium",
    available_now: bool = True,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    """Build a standardized next_action item from explicit fields.

    ``input_dict`` is copied before serialization so callers cannot mutate the
    produced item's input payload afterwards.
    """
    safety = _safety_flags(tool)
    return {
        "id": _action_id(tool, input_dict),
        "label": label or _tool_label(tool),
        "priority": priority,
        "source": source,
        "tool": tool,
        "input": dict(input_dict),
        "reason": reason,
        "available_now": available_now,
        "blocked_reason": blocked_reason,
        **safety,
    }
