from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


PermissionLevel = Literal[
    "auto_read",
    "auto_preview",
    "auto_write_safe",
    "approval_mutation",
    "explicit_confirm",
    "blocked",
]
ApprovalMode = Literal["balanced", "strict", "manual"]


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: PermissionLevel
    allowed: bool
    requires_approval: bool = False
    explanation: str


AUTO_READ_TOOLS = {
    "aieng.agent_readme",
    "aieng.guide",
    "aieng.list_projects",
    "aieng.agent_context",
    "aieng.inspect_package",
    "aieng.read_audit_log",
    "aieng.validate",
    "aieng.write_completeness_report",
    "cad.get_source",
    "cad.get_named_part_bbox",
    "cad.plan_build123d_skill",
    "cad.critique",
    "cad.design_review",
    "cad.list_snapshots",
}

AUTO_PREVIEW_TOOLS = {
    "cae.prepare_solver_run",
}

AUTO_WRITE_SAFE_TOOLS = {
    "aieng.create_project",
    "cae.apply_setup_patch",
    "cae.generate_solver_input",
    "cae.write_mesh_handoff",
    "cae.import_solver_evidence",
    "cae.extract_solver_results",
    "cae.extract_field_regions",
    "postprocess.generate_computed_metrics",
    "postprocess.refresh_cae_summary",
    "aieng.update_validation_status",
}

APPROVAL_MUTATION_TOOLS = {
    "cad.execute_build123d",
    "cad.edit_parameter",
    "cad.restore_snapshot",
    "aieng.convert",
    "aieng.generate_preview",
    "aieng.refresh_semantics",
    "aieng.write_evidence_scaffold",
}

EXPLICIT_CONFIRM_TOOLS = {
    "cae.run_solver",
}

NO_PROJECT_REQUIRED = {
    "aieng.agent_readme",
    "aieng.create_project",
    "aieng.guide",
    "aieng.list_projects",
}


def classify_known_tool(tool_name: str) -> PermissionLevel:
    if tool_name in AUTO_READ_TOOLS:
        return "auto_read"
    if tool_name in AUTO_PREVIEW_TOOLS:
        return "auto_preview"
    if tool_name in AUTO_WRITE_SAFE_TOOLS:
        return "auto_write_safe"
    if tool_name in APPROVAL_MUTATION_TOOLS:
        return "approval_mutation"
    if tool_name in EXPLICIT_CONFIRM_TOOLS:
        return "explicit_confirm"
    return "blocked"


def evaluate_tool_call(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    active_project_id: str | None,
    registered_tools: list[dict[str, Any]],
    mode: str = "autopilot",
    approval_mode: ApprovalMode = "balanced",
) -> PolicyDecision:
    registry = {str(tool.get("name")) for tool in registered_tools if tool.get("name")}
    if tool_name not in registry:
        return PolicyDecision(
            level="blocked",
            allowed=False,
            explanation=f"Tool is not registered in the Workbench runtime: {tool_name}",
        )
    if tool_name.startswith(("shell.", "file.", "fs.", "terminal.")):
        return PolicyDecision(
            level="blocked",
            allowed=False,
            explanation="Raw shell and filesystem actions are blocked by Local Agent Autopilot.",
        )
    level = classify_known_tool(tool_name)
    if level == "blocked":
        return PolicyDecision(
            level="blocked",
            allowed=False,
            explanation=f"Tool is registered but not allowlisted for Autopilot policy: {tool_name}",
        )
    if active_project_id and tool_name not in NO_PROJECT_REQUIRED:
        requested_project = tool_input.get("project_id") or tool_input.get("projectId")
        if requested_project != active_project_id:
            return PolicyDecision(
                level="blocked",
                allowed=False,
                explanation=(
                    f"Tool call project_id must match active project {active_project_id}; "
                    f"got {requested_project!r}."
                ),
            )
    if mode == "assist" and level not in {"auto_read", "auto_preview"}:
        return PolicyDecision(
            level=level,
            allowed=False,
            explanation="Assist mode can observe and preview only; execution is disabled.",
        )
    if approval_mode == "manual":
        return PolicyDecision(
            level=level,
            allowed=True,
            requires_approval=True,
            explanation=f"Manual approval mode requires approval before running {tool_name}.",
        )
    if approval_mode == "strict" and level == "auto_write_safe":
        return PolicyDecision(
            level=level,
            allowed=True,
            requires_approval=True,
            explanation=f"Strict approval mode requires approval before safe-write tool {tool_name}.",
        )
    if level in {"approval_mutation", "explicit_confirm"}:
        copy = "Requires explicit solver execution confirmation." if level == "explicit_confirm" else (
            "Requires approval before mutating CAD/package geometry."
        )
        return PolicyDecision(level=level, allowed=True, requires_approval=True, explanation=copy)
    return PolicyDecision(
        level=level,
        allowed=True,
        requires_approval=False,
        explanation=f"Tool is allowed to run automatically as {level}.",
    )
