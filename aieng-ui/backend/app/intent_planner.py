"""Natural-language Intent Planner (v0.35.1).

Translates a plain-language engineering request into a structured AIENG
*IntentPlan* — a reviewable preview only. The planner never executes a
tool; execution is the caller's responsibility and goes through the
existing approval-gated runtime (`POST /api/runtime/runs`).

Heuristic-only in v1. Designed so an LLM planner can replace
``plan_from_request`` later without changing the schema or the consumer.

Safety contract:
  * read-only; planner never mutates the .aieng package or runs CAD/CAE.
  * every proposed action references a registered runtime tool.
  * every action carries an explicit ``mode`` and ``requires_approval`` flag.
  * Solver requests are expanded into a reviewable CAE workflow: context check,
    preflight / deck preparation, explicit solver approval, and postprocess
    parsing. ``cae.run_solver`` is never marked auto-approved.
  * "Unsupported" engineering requests (drone arm, generic free-form CAD)
    return an honest missing_information list, not a fake template match.
  * claim_advancement is always ``"none"``.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from . import action_selector
from .agent_engine import _infer_template_request


ActionMode = Literal["read_only", "metadata_write", "mutation", "expensive"]

from .honesty import INTENT_PLANNER_CLAIM_BOUNDARY as CLAIM_BOUNDARY

SCHEMA_VERSION = "0.1"

CLAIM_ADVANCEMENT: Literal["none"] = "none"

# ── domain classifier ────────────────────────────────────────────────────────

_STRUCTURAL_TOKENS = (
    "beam", "bracket", "plate", "cantilever", "tension", "compression",
    "stress", "displacement", "deflection", "stiffness", "fatigue",
    "static", "linear elastic", "load", "force",
    "arm", "strong enough", "strength", "robust", "stiff",
)
_CFD_TOKENS = (
    "cfd", "fluid", "flow", "openfoam", "turbulence", "navier",
    "velocity field", "pressure drop",
)
_RUN_TOKENS = (
    "run", "execute", "solve", "simulate",
)

# CAD-action detection (v0.38)
_CAD_ACTION_VERBS = (
    "create", "draw", "model", "make", "build", "generate",
)
_CAD_ACTION_NOUNS = (
    "box", "bracket", "cantilever", "beam", "plate", "frame",
    "quadcopter", "drone", "arm", "geometry", "cad", "shape",
    "part", "component",
)
_CAD_ACTION_TOKENS = _CAD_ACTION_VERBS + _CAD_ACTION_NOUNS

KNOWN_TEMPLATE_IDS = ("cantilever_beam", "plate_with_hole")


def _classify_domain(message: str, template_id: str | None) -> str:
    text = message.lower()
    if template_id in KNOWN_TEMPLATE_IDS:
        return "structural_static_linear"
    if any(token in text for token in _CFD_TOKENS):
        return "cfd_unsupported"
    if any(token in text for token in _STRUCTURAL_TOKENS):
        return "structural_unspecified"
    return "unclassified"


def _wants_solver_now(message: str) -> bool:
    text = message.lower()
    return any(token in text for token in _RUN_TOKENS) and any(
        token in text
        for token in ("solver", "simulation", "simulate", "ccx", "calculix", "fem")
    )


def _wants_mesh_now(message: str) -> bool:
    text = message.lower()
    return any(token in text for token in ("mesh", "generate mesh", "mesher", "gmsh", "netgen"))


# ── constraint extraction ────────────────────────────────────────────────────


def _extract_constraints(params: dict[str, Any], template_id: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if "material" in params:
        out.append({
            "kind": "material",
            "value": params["material"],
            "source": "natural_language",
        })
    for geom_key, axis in (("length_mm", "length"), ("width_mm", "width"),
                           ("height_mm", "height"), ("thickness_mm", "thickness"),
                           ("hole_diameter_mm", "hole_diameter")):
        if geom_key in params:
            out.append({
                "kind": "geometry",
                "axis": axis,
                "value_mm": params[geom_key],
                "source": "natural_language",
            })
    for load_key in ("tip_load_N", "tensile_load_N"):
        if load_key in params:
            out.append({
                "kind": "load",
                "label": load_key,
                "value_N": params[load_key],
                "source": "natural_language",
            })
    if "allowable_stress_MPa" in params:
        out.append({
            "kind": "design_target",
            "metric": "max_von_mises_stress",
            "operator": "<=",
            "value": params["allowable_stress_MPa"],
            "unit": "MPa",
            "source": "natural_language",
        })
    if "max_displacement_mm" in params:
        out.append({
            "kind": "design_target",
            "metric": "max_displacement",
            "operator": "<=",
            "value": params["max_displacement_mm"],
            "unit": "mm",
            "source": "natural_language",
        })
    if template_id:
        out.append({
            "kind": "template_match",
            "template_id": template_id,
            "source": "heuristic",
        })
    return out


def _missing_for_structural_pilot(template_id: str | None, params: dict[str, Any]) -> list[str]:
    if template_id is None:
        return [
            "template_match (no supported template inferred from the request)",
            "material",
            "primary_dimensions",
            "load_magnitude_or_direction",
            "boundary_conditions",
            "design_targets",
        ]
    missing: list[str] = []
    if "material" not in params:
        missing.append("material")
    if template_id == "cantilever_beam":
        for key in ("length_mm", "width_mm", "height_mm", "tip_load_N"):
            if key not in params:
                missing.append(key)
    elif template_id == "plate_with_hole":
        for key in ("length_mm", "width_mm", "thickness_mm",
                    "hole_diameter_mm", "tensile_load_N"):
            if key not in params:
                missing.append(key)
    return missing


def _assumptions_for(template_id: str | None, params: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if template_id is None:
        return out
    if "material" not in params:
        default_material = (
            "aluminum_6061_t6" if template_id == "cantilever_beam" else "steel_s235"
        )
        out.append(
            f"Material not provided; the template's default ({default_material}) will be used for preview."
        )
    out.append("Lengths interpreted in millimetres; forces in newtons; stresses in MPa.")
    if template_id == "cantilever_beam":
        out.append("Single transverse tip load case; linear elastic static analysis only.")
    elif template_id == "plate_with_hole":
        out.append("Uniaxial tension across the long axis; classical stress-concentration regime.")
    return out


# ── mode classification ──────────────────────────────────────────────────────

_READ_ONLY_TOOLS = frozenset({
    "aieng.agent_context",
    "aieng.inspect_package",
    "aieng.read_audit_log",
    "aieng.validate",
    "aieng.refresh_semantics",
    "engineering_template.preview",
    "cae.prepare_solver_run",
    "postprocess.refresh_cae_summary",
})

_EXPENSIVE_TOOLS = frozenset({
    "cae.run_solver",
})

_MUTATION_TOOLS = frozenset()


def _classify_mode(
    tool_name: str,
    capability: dict[str, Any] | None,
    requires_approval: bool,
) -> ActionMode:
    if tool_name in _EXPENSIVE_TOOLS:
        return "expensive"
    if tool_name in _MUTATION_TOOLS:
        return "mutation"
    if tool_name in _READ_ONLY_TOOLS:
        return "read_only"
    if capability is not None:
        if capability.get("mutates_cad"):
            return "mutation"
        if capability.get("may_update_claim_map"):
            return "mutation"
        if capability.get("mutates_package"):
            return "metadata_write"
    if requires_approval:
        # Approval-gated and not in the explicit lists above: treat as
        # metadata_write by default (template.save_draft / adopt_targets /
        # generate_cad_fixture all land here).
        return "metadata_write"
    return "read_only"


# ── action templates ─────────────────────────────────────────────────────────


def _action(
    *,
    label: str,
    description: str,
    tool_name: str,
    tool_args: dict[str, Any],
    runtime_tools: list[dict[str, Any]],
    capabilities_by_name: dict[str, dict[str, Any]],
    expected_artifacts: list[str] | None = None,
    stale_impacts: list[str] | None = None,
    risk_notes: list[str] | None = None,
    workflow_phase: str | None = None,
) -> dict[str, Any] | None:
    tool_info = next((t for t in runtime_tools if t.get("name") == tool_name), None)
    if tool_info is None:
        return None
    requires_approval = bool(tool_info.get("requires_approval"))
    capability = capabilities_by_name.get(tool_name)
    mode = _classify_mode(tool_name, capability, requires_approval)
    action = {
        "id": f"action_{uuid.uuid4().hex[:8]}",
        "label": label,
        "description": description,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "mode": mode,
        "requires_approval": requires_approval,
        "expected_artifacts": list(expected_artifacts or []),
        "stale_impacts": list(stale_impacts or []),
        "risk_notes": list(risk_notes or []),
    }
    if workflow_phase:
        action["workflow_phase"] = workflow_phase
    return action


def _template_actions(
    *,
    project_id: str | None,
    template_id: str,
    params: dict[str, Any],
    runtime_tools: list[dict[str, Any]],
    capabilities_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    base = {"project_id": project_id} if project_id else {}
    template_args = {**base, "template_id": template_id, "parameters": params}
    candidates: list[dict[str, Any] | None] = []
    if project_id:
        candidates.append(_action(
            label="Read agent CAD/CAE context",
            description="Read the compact agent-facing CAD/CAE context before proposing changes.",
            tool_name="aieng.agent_context",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
        ))
        candidates.append(_action(
            label="Inspect current .aieng package",
            description="Read-only inspection of the project package context before proposing changes.",
            tool_name="aieng.inspect_package",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
        ))
    candidates.append(_action(
        label=f"Preview template draft ({template_id})",
        description=(
            "Render the controlled engineering template as a reviewable draft. "
            "No package write; no CAD/solver execution."
        ),
        tool_name="engineering_template.preview",
        tool_args=template_args,
        runtime_tools=runtime_tools,
        capabilities_by_name=capabilities_by_name,
        expected_artifacts=[
            "cad_script_preview (inline)",
            "fea_setup_draft (inline)",
            "design_target_suggestions (inline)",
        ],
        risk_notes=[
            "Preview only; the script is inert text and the FEA setup is structured JSON, "
            "not a runnable solver deck.",
        ],
    ))
    if project_id:
        candidates.append(_action(
            label="Save template draft into the package",
            description=(
                "Write the four template draft artifacts (manifest, CAD script preview, "
                "FEA setup, design target suggestions) into the .aieng package."
            ),
            tool_name="engineering_template.save_draft",
            tool_args=template_args,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            expected_artifacts=[
                "task/engineering_setup_draft.json",
                "task/cad_template_preview.py",
                "task/fea_setup_draft.json",
                "task/design_targets_suggestions.yaml",
            ],
            risk_notes=[
                "Approval-gated. Never overwrites task/design_targets.yaml.",
            ],
        ))
        candidates.append(_action(
            label="Adopt suggested design targets",
            description=(
                "Merge the template's suggested targets into task/design_targets.yaml. "
                "Existing targets with matching IDs are skipped unless overwrite is requested."
            ),
            tool_name="engineering_template.adopt_targets",
            tool_args={**base, "template_id": template_id},
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            expected_artifacts=["task/design_targets.yaml"],
            stale_impacts=[
                "results/target_comparison_summary (recomputed on next read)",
            ],
            risk_notes=[
                "Approval-gated. Adoption is review metadata only; does not certify the design.",
            ],
        ))
        candidates.append(_action(
            label="Generate deterministic CAD fixture metadata",
            description=(
                "Write geometry/template_cad_fixture.json describing the controlled template "
                "geometry. No real CAD file is created; downstream evidence is marked stale."
            ),
            tool_name="engineering_template.generate_cad_fixture",
            tool_args={**template_args, "approved": True},
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            expected_artifacts=[
                "geometry/template_cad_fixture.json",
                "validation/revalidation_status.json",
            ],
            stale_impacts=[
                "simulation/mesh/* (revalidation required)",
                "simulation/runs/*/* (revalidation required)",
                "results/* (revalidation required)",
            ],
            risk_notes=[
                "Approval-gated. Writes geometry metadata only; does not run Gmsh/CalculiX.",
            ],
        ))
    return [a for a in candidates if a is not None]


def _safe_inspection_actions(
    *,
    project_id: str | None,
    runtime_tools: list[dict[str, Any]],
    capabilities_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not project_id:
        return []
    base = {"project_id": project_id}
    candidates = [
        _action(
            label="Read agent CAD/CAE context",
            description=(
                "Read the compact agent-facing context: CAD observation, CAE setup/results, "
                "targets, metrics, target comparison, and next action hints."
            ),
            tool_name="aieng.agent_context",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
        ),
        _action(
            label="Inspect current .aieng package",
            description="Read-only summary of the package's current semantic and CAE state.",
            tool_name="aieng.inspect_package",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
        ),
        _action(
            label="Read audit log",
            description="List the most recent audit events for this project.",
            tool_name="aieng.read_audit_log",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
        ),
    ]
    return [a for a in candidates if a is not None]


def _solver_preflight_action(
    *,
    project_id: str | None,
    runtime_tools: list[dict[str, Any]],
    capabilities_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not project_id:
        return None
    return _action(
        label="Run structural solver preflight (readiness check only)",
        description=(
            "Inspect the package for mesh, solver settings, load case, and input deck. "
            "Reports readiness gaps; does not execute the solver."
        ),
        tool_name="cae.prepare_solver_run",
        tool_args={"project_id": project_id},
        runtime_tools=runtime_tools,
        capabilities_by_name=capabilities_by_name,
        expected_artifacts=["preflight readiness report (inline, no package write)"],
        risk_notes=[
            "Solver execution remains approval-gated and should follow this preflight.",
        ],
        workflow_phase="check",
    )


def _solver_workflow_actions(
    *,
    project_id: str | None,
    runtime_tools: list[dict[str, Any]],
    capabilities_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not project_id:
        return []
    base = {"project_id": project_id}
    candidates: list[dict[str, Any] | None] = [
        _action(
            label="Read CAD/CAE context for simulation",
            description="Read geometry, CAE setup, selected faces, targets, and existing result state before planning solver work.",
            tool_name="aieng.agent_context",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            workflow_phase="check",
        ),
        _action(
            label="Inspect current package before simulation",
            description="Read-only package inspection to confirm existing artifacts and stale evidence before solver preparation.",
            tool_name="aieng.inspect_package",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            workflow_phase="check",
        ),
        _solver_preflight_action(
            project_id=project_id,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
        ),
        _action(
            label="Generate solver input deck if needed",
            description=(
                "Generate or refresh the CalculiX input deck from existing CAE setup artifacts. "
                "If material, load, or boundary-condition data is missing, patch setup before this step."
            ),
            tool_name="cae.generate_solver_input",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            expected_artifacts=["simulation/runs/*/solver_input.inp"],
            risk_notes=[
                "Requires an existing CAE setup. Use cae.apply_setup_patch first when material, BC, or load data is incomplete.",
            ],
            workflow_phase="preprocess",
        ),
        _action(
            label="Run structural solver",
            description="Execute CalculiX on the prepared input deck. This is the only external solver execution step.",
            tool_name="cae.run_solver",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            expected_artifacts=[
                "simulation/runs/*/solver_run.json",
                "simulation/runs/*/solver_log.txt",
                "simulation/runs/*/outputs/result.frd",
            ],
            risk_notes=[
                "Expensive external execution. Requires explicit approval in every approval mode.",
                "Run cae.prepare_solver_run before approval so readiness gaps are visible.",
            ],
            workflow_phase="approval_execute",
        ),
        _action(
            label="Extract solver metrics",
            description="Parse CalculiX FRD output into computed metrics for downstream review.",
            tool_name="cae.extract_solver_results",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            expected_artifacts=["analysis/computed_metrics.json"],
            workflow_phase="parse",
        ),
        _action(
            label="Extract high-field regions",
            description="Cluster high-stress or displacement regions so the result can be tied back to geometry.",
            tool_name="cae.extract_field_regions",
            tool_args={**base, "field": "stress"},
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            expected_artifacts=["results/field_regions.json"],
            workflow_phase="parse",
        ),
        _action(
            label="Refresh CAE result summary",
            description="Regenerate the human-readable CAE summary and result field metadata after parsing.",
            tool_name="postprocess.refresh_cae_summary",
            tool_args=base,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
            expected_artifacts=["results/result_summary.json", "results/evidence_index.json"],
            workflow_phase="parse",
        ),
    ]
    return [action for action in candidates if action is not None]


# ── plan assembly ────────────────────────────────────────────────────────────


def _task_summary(message: str, domain: str, template_id: str | None) -> str:
    if template_id == "cantilever_beam":
        return "Structural sizing study for a cantilever beam (linear elastic static)."
    if template_id == "plate_with_hole":
        return "Stress-concentration study for a plate with a central hole (uniaxial tension)."
    if domain == "structural_unspecified":
        return "Structural request without a matching controlled template — no supported pilot path."
    if domain == "cfd_unsupported":
        return "Fluid-flow / CFD request — out of MVP scope; AIENG does not run CFD."
    snippet = message.strip().splitlines()[0][:120] if message.strip() else "(empty request)"
    return f"Unclassified engineering request: \"{snippet}\""


def _evidence_scope(domain: str, template_id: str | None) -> list[str]:
    if template_id:
        return [
            "Controlled engineering template draft (inline preview + optional package write).",
            "Existing design targets and audit log when the project is loaded.",
            "No CAD geometry, mesh, or solver evidence is produced by the planner.",
        ]
    if domain == "structural_unspecified":
        return [
            "No structural evidence available: the request does not match a supported template.",
            "Safe inspection only.",
        ]
    if domain == "cfd_unsupported":
        return [
            "No CFD evidence is produced by AIENG. CFD execution is deferred (see roadmap v0.41+).",
        ]
    return [
        "No engineering evidence inferred from this request.",
        "Safe inspection only.",
    ]


def _capabilities_by_name(capabilities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cap in capabilities:
        name = cap.get("name")
        if isinstance(name, str) and name not in out:
            out[name] = cap
    return out


def _compact_agent_context(agent_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(agent_context, dict):
        return {}
    cad = agent_context.get("cad") if isinstance(agent_context.get("cad"), dict) else {}
    cae = agent_context.get("cae") if isinstance(agent_context.get("cae"), dict) else {}
    targets = (
        agent_context.get("design_targets")
        if isinstance(agent_context.get("design_targets"), dict)
        else {}
    )
    metrics = (
        agent_context.get("computed_metrics")
        if isinstance(agent_context.get("computed_metrics"), dict)
        else {}
    )
    comparison = (
        agent_context.get("target_comparison")
        if isinstance(agent_context.get("target_comparison"), dict)
        else {}
    )
    return {
        "agent_brief": agent_context.get("agent_brief") or {},
        "cad": {
            "status": cad.get("status"),
            "geometry_evidence_level": cad.get("geometry_evidence_level"),
            "known_geometry": cad.get("known_geometry") or {},
            "missing_information": cad.get("missing_information") or [],
        },
        "cae": {
            "present": cae.get("present"),
            "results_available": cae.get("results_available"),
            "available_fields": cae.get("available_fields") or [],
            "has_fea_setup_draft": bool(cae.get("fea_setup_draft")),
        },
        "design_targets": {"count": targets.get("count") or 0},
        "computed_metrics": {
            "metrics_count": metrics.get("metrics_count") or 0,
            "load_case_count": metrics.get("load_case_count") or 0,
        },
        "target_comparison": {
            "summary": comparison.get("summary") or {},
            "failed_count": len(comparison.get("failed_targets") or []),
            "unknown_count": len(comparison.get("unknown_targets") or []),
        },
        "available_actions": agent_context.get("available_actions") or [],
        "warnings": agent_context.get("warnings") or [],
    }


def plan_from_request(
    *,
    message: str,
    project_id: str | None,
    runtime_tools: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    structural_preflight: dict[str, Any] | None = None,
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Heuristic planner. Pure function; never executes a tool.

    Parameters
    ----------
    message:
        The raw natural-language request from the user.
    project_id:
        The active project, when one is loaded. ``None`` is honest — actions
        that need a package are still listed in the plan but every proposed
        action with ``project_id`` set will be omitted; the warning list
        explains why.
    runtime_tools:
        Output of ``runtime.registered_tools_info()``. Used to confirm that
        every proposed action references a tool that exists *now*.
    capabilities:
        Output of ``agent_workbench.list_capabilities``. Used to pull
        ``mutates_*`` / ``side_effects`` metadata into ``mode`` classification.
    structural_preflight:
        Optional output of ``structural_adapter.prepare_structural_run_preview``.
        When the user asks to "run the solver", the planner uses this to
        report readiness gaps and keep solver execution behind explicit
        approval.
    agent_context:
        Optional output of ``agent_context.build_agent_context``. Included in
        the plan as the compact CAD/CAE state the connected agent should use
        before selecting actions.
    """
    message = message or ""
    template_id, params = _infer_template_request(message)
    domain = _classify_domain(message, template_id)
    capabilities_by_name = _capabilities_by_name(capabilities)
    warnings: list[str] = []
    if not project_id:
        warnings.append(
            "No project_id was provided; the plan is informational and cannot execute "
            "package-writing actions."
        )

    actions: list[dict[str, Any]] = []
    missing: list[str] = []
    refusals: list[dict[str, Any]] = []
    wants_solver_now = _wants_solver_now(message)

    # Branch 1: "run the solver now" — expand into the full CAE workflow.
    if wants_solver_now:
        if structural_preflight is not None:
            preflight = (
                structural_preflight.get("preflight")
                if isinstance(structural_preflight.get("preflight"), dict)
                else structural_preflight
            )
            for item in preflight.get("missing_items") or []:
                missing.append(f"solver_run_readiness:{item}")
            if not preflight.get("ready_to_run", False):
                warnings.append(
                    "Structural solver preflight reports the run is not ready. "
                    "You may still approve the solver run, but it is likely to fail or produce incomplete results."
                )
        else:
            missing.append("structural_preflight_unavailable (no project loaded)")
            warnings.append(
                "No preflight data available. Solver execution is offered but may fail."
            )
        actions.extend(_solver_workflow_actions(
            project_id=project_id,
            runtime_tools=runtime_tools,
            capabilities_by_name=capabilities_by_name,
        ))

    # Branch 2: template match — pilot path.
    elif template_id is not None:
        missing.extend(_missing_for_structural_pilot(template_id, params))
        actions.extend(
            _template_actions(
                project_id=project_id,
                template_id=template_id,
                params=params,
                runtime_tools=runtime_tools,
                capabilities_by_name=capabilities_by_name,
            )
        )

    # Branch 3: no template match. Honest missing information + safe actions only.
    else:
        missing.extend(_missing_for_structural_pilot(template_id, params))
        if domain == "cfd_unsupported":
            warnings.append(
                "CFD / fluid-flow requests are out of MVP scope. AIENG does not run CFD."
            )
            refusals.append({
                "tool_name": None,
                "reason": "CFD execution is deferred to roadmap items v0.41+ (radar / preflight / viewer).",
            })
        else:
            warnings.append(
                "No controlled template matched the request. The planner proposes safe "
                "inspection only; no engineering claim is being made."
            )
        actions.extend(
            _safe_inspection_actions(
                project_id=project_id,
                runtime_tools=runtime_tools,
                capabilities_by_name=capabilities_by_name,
            )
        )

    required_approvals = [
        action["id"] for action in actions if action.get("requires_approval")
    ]
    plan_id = "plan_" + uuid.uuid4().hex[:10]

    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "planner_mode": "heuristic",
        "message": message,
        "project_id": project_id,
        "task_summary": _task_summary(message, domain, template_id),
        "inferred_engineering_domain": domain,
        "inferred_template_id": template_id,
        "extracted_constraints": _extract_constraints(params, template_id),
        "extracted_parameters": params,
        "agent_context": _compact_agent_context(agent_context),
        "action_selection": action_selector.select_actions_for_intent(
            message=message,
            available_actions=(
                agent_context.get("available_actions")
                if isinstance(agent_context, dict) and isinstance(agent_context.get("available_actions"), list)
                else []
            ),
        ),
        "missing_information": missing,
        "assumptions": _assumptions_for(template_id, params),
        "actions": actions,
        "required_approvals": required_approvals,
        "evidence_scope": _evidence_scope(domain, template_id),
        "refusals": refusals,
        "warnings": warnings,
        "claim_advancement": CLAIM_ADVANCEMENT,
        "claim_boundary": CLAIM_BOUNDARY,
    }


def find_action(plan: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    """Locate an action in a plan by id. Pure helper for the execute endpoint."""
    if not isinstance(plan, dict):
        return None
    for action in plan.get("actions") or []:
        if isinstance(action, dict) and action.get("id") == action_id:
            return action
    return None


__all__ = [
    "ActionMode",
    "CLAIM_ADVANCEMENT",
    "CLAIM_BOUNDARY",
    "SCHEMA_VERSION",
    "find_action",
    "plan_from_request",
]
