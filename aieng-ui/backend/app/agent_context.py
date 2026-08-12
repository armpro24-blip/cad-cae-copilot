"""Agent-facing CAD/CAE context package.

This module builds the compact semantic state that a connected AI agent needs
before deciding the next CAD/CAE action. It is intentionally read-only: no CAD
tools, meshers, solvers, package writes, or claim advancement are performed.
"""

from __future__ import annotations

import zipfile
from typing import Any, Callable

from fastapi import HTTPException

from . import action_selector
from . import cad_observation, copilot_loop
from . import computed_metrics as computed_metrics_module
from . import design_targets as design_targets_module
from . import target_comparison as target_comparison_module
from .config import Settings, now_iso
from .cae_payload_profile import compact_cae_block, profile_payload
from .package_inspection import (
    PackageReadCache,
    _detect_cae_artifacts,
    _generate_cae_preprocessing_summary,
    _generate_cae_result_summary,
    _generate_cae_simulation_run_summary,
    package_summary_fallback,
    read_package_json,
)
from .project_io import get_project, resolve_project_path

SCHEMA_VERSION = "0.1"

CLAIM_BOUNDARY = (
    "Agent context is a read-only CAD/CAE understanding package. It may guide "
    "next engineering actions, but it does not execute tools, mutate CAD/CAE "
    "artifacts, or certify engineering correctness."
)


def build_agent_context(
    settings: Settings,
    project_id: str,
    *,
    package_reader: PackageReadCache | None = None,
) -> dict[str, Any]:
    """Build a compact CAD/CAE semantic context for connected AI agents."""

    project = get_project(settings, project_id)
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    package_exists = bool(package_path and package_path.exists())
    warnings: list[str] = []
    package_error: str | None = None

    owns_reader = package_reader is None and package_exists and package_path is not None
    reader = package_reader
    if owns_reader and package_path is not None:
        try:
            reader = PackageReadCache(package_path)
        except Exception as exc:  # pragma: no cover - exercised by corrupt packages
            package_error = f"{type(exc).__name__}: {exc}"
            warnings.append(f"Could not inspect package zip members: {package_error}")
            reader = None
    try:
        fallback: dict[str, Any] | None = None
        if package_exists and package_path is not None and reader is not None:
            try:
                fallback = package_summary_fallback(package_path, archive=reader)
            except Exception as exc:  # pragma: no cover - exercised by corrupt packages
                package_error = f"{type(exc).__name__}: {exc}"
                warnings.append(f"Could not inspect package zip members: {package_error}")
        elif package_exists and package_path is not None and package_error is None:
            try:
                fallback = package_summary_fallback(package_path)
            except Exception as exc:  # pragma: no cover - exercised by corrupt packages
                package_error = f"{type(exc).__name__}: {exc}"
                warnings.append(f"Could not inspect package zip members: {package_error}")
        else:
            warnings.append("Project has no readable .aieng package.")

        cad = cad_observation.observe_cad_state(settings, project_id, package_reader=reader)
        design_targets = _safe_call(
            lambda: design_targets_module.get_design_targets(settings, project_id),
            warnings,
            "design_targets",
            default={"ok": False, "targets": [], "document": None, "warnings": []},
        )
        computed_metrics = _safe_call(
            lambda: computed_metrics_module.get_computed_metrics(settings, project_id),
            warnings,
            "computed_metrics",
            default={"ok": False, "document": None, "metrics_count": 0, "load_case_count": 0},
        )
        comparison = _safe_call(
            lambda: target_comparison_module.compare_package_targets(settings, project_id),
            warnings,
            "target_comparison",
            default=None,
        )

        cae_summaries = _build_cae_summaries(
            settings,
            package_path if package_exists else None,
            warnings,
            package_reader=reader,
        )
        loops = _safe_call(
            lambda: copilot_loop.list_loops(settings, project_id),
            warnings,
            "copilot_loops",
            default={"loops": []},
        )
        raw_members = fallback.get("members", []) if isinstance(fallback, dict) else []

        context = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "project_id": project_id,
            "purpose": (
                "Give a connected AI agent enough CAD/CAE state to understand the part, "
                "physics, evidence gaps, target failures, and useful next actions."
            ),
            "project": _project_block(project),
            "package": {
                "path": project.get("aieng_file"),
                "exists": package_exists,
                "member_count": len(raw_members),
                "summary_error": package_error,
            },
            "cad": cad,
            "brep_graph": _brep_graph_block(package_path if package_exists else None, package_reader=reader),
            "assembly": _assembly_block(package_path if package_exists else None, package_reader=reader),
            "cae": _cae_block(fallback, cae_summaries),
            "design_targets": _design_targets_block(design_targets),
            "computed_metrics": _computed_metrics_block(computed_metrics),
            "target_comparison": _target_comparison_block(comparison),
            "loop_history": {
                "count": len(loops.get("loops") or []) if isinstance(loops, dict) else 0,
                "recent": (loops.get("loops") or [])[:5] if isinstance(loops, dict) else [],
            },
            "agent_brief": {},
            "available_actions": action_selector.annotate_available_actions(
                _available_actions(cad, computed_metrics, comparison)
            ),
            "warnings": _dedupe_keep_order(warnings),
            "claim_advancement": "none",
            "claim_boundary": CLAIM_BOUNDARY,
        }
        refused = [
            c for c in (context["assembly"].get("attention") or [])
            if c.get("geometry_status") == "invalid"
        ]
        for conn in refused:
            context["warnings"].append(
                f"assembly connection '{conn.get('connection_id')}' is geometrically invalid "
                f"({', '.join(conn.get('reasons') or [])}) and is NOT solver-enabled."
            )
        ifaces = context["assembly"].get("interfaces") or {}
        if ifaces.get("safe_for_solver") is False:
            context["warnings"].append(
                "assembly interface(s) resolve to an empty node set: "
                f"{', '.join(ifaces.get('blocking') or []) or 'unnamed'} — "
                "the assembly is not safe to solve."
            )
        context["agent_brief"] = _agent_brief(context)
        return context
    finally:
        if owns_reader and reader is not None:
            reader.close()


def _safe_call(
    func: Callable[[], Any],
    warnings: list[str],
    label: str,
    *,
    default: Any,
) -> Any:
    try:
        return func()
    except HTTPException as exc:
        warnings.append(f"{label} unavailable: HTTP {exc.status_code}: {exc.detail}")
        return default
    except Exception as exc:
        warnings.append(f"{label} unavailable: {type(exc).__name__}: {exc}")
        return default


def _build_cae_summaries(
    settings: Settings,
    package_path: Any,
    warnings: list[str],
    *,
    package_reader: PackageReadCache | None = None,
) -> dict[str, Any]:
    if package_path is None:
        return {
            "artifact_detection": None,
            "preprocessing": None,
            "simulation_run": None,
            "result": None,
        }
    summaries = {
        "artifact_detection": _detect_cae_artifacts(settings, package_path),
        "preprocessing": _generate_cae_preprocessing_summary(settings, package_path),
        "simulation_run": _generate_cae_simulation_run_summary(settings, package_path),
        "result": _generate_cae_result_summary(settings, package_path),
        "fea_setup_draft": _read_package_member(package_path, "task/fea_setup_draft.json", package_reader=package_reader),
        "template_fixture": _read_package_member(package_path, "geometry/template_cad_fixture.json", package_reader=package_reader),
    }
    if summaries["artifact_detection"] is None:
        warnings.append("CAE artifact detector unavailable or no artifact-detection summary could be generated.")
    return summaries


def _read_package_member(
    package_path: Any,
    member: str,
    *,
    package_reader: PackageReadCache | None = None,
) -> Any:
    try:
        if package_reader is not None:
            return read_package_json(package_reader, member)
        with zipfile.ZipFile(package_path, "r") as zf:
            return read_package_json(zf, member)
    except Exception:
        return None


def _brep_graph_block(
    package_path: Any,
    *,
    package_reader: PackageReadCache | None = None,
) -> dict[str, Any]:
    if package_path is None:
        return {"present": False}
    try:
        from . import brep_graph

        graph = _read_package_member(package_path, brep_graph.BREP_GRAPH_MEMBER, package_reader=package_reader)
        index = _read_package_member(package_path, brep_graph.ENTITY_INDEX_MEMBER, package_reader=package_reader)
        digest = brep_graph.load_or_build_digest(package_path, max_items=16)
        faces = ((graph or {}).get("entities") or {}).get("faces") or []
        edges = ((graph or {}).get("entities") or {}).get("edges") or []
        groups = (graph or {}).get("selection_groups") or []
        return {
            "present": bool(graph),
            "face_count": len(faces),
            "edge_count": len(edges),
            "selection_group_count": len(groups),
            "pointer_syntax": (graph or {}).get("pointer_syntax") or {"face": "@face:<face_id>", "edge": "@edge:<edge_id>"},
            "digest": digest,
            "entity_index_sample": dict(list((index or {}).items())[:12]) if isinstance(index, dict) else {},
        }
    except Exception as exc:
        return {"present": False, "error": f"{type(exc).__name__}: {exc}"}


def _assembly_block(
    package_path: Any,
    *,
    package_reader: PackageReadCache | None = None,
) -> dict[str, Any]:
    """Whether this project is an assembly, and which joints are blocking (#496).

    Measured on the dogfood gearbox: after three parts, four interfaces and three
    mates were authored, `agent_context` reported nothing about any of it — an
    agent reading its session context could not tell an assembly from a single
    part, let alone that one connection was refused. Deliberately compact: counts,
    a status tally, and only the connections that are NOT plausible.
    """
    if package_path is None:
        return {"present": False}
    try:
        assembly = _read_package_member(
            package_path, "assembly/assembly_ir.json", package_reader=package_reader
        )
        if not isinstance(assembly, dict) or not assembly.get("parts"):
            return {"present": False}
        geometry = _read_package_member(
            package_path,
            "diagnostics/assembly_connection_geometry.json",
            package_reader=package_reader,
        )
        connections = (geometry or {}).get("connections") or []
        tally: dict[str, int] = {}
        blocking: list[dict[str, Any]] = []
        for conn in connections:
            status = conn.get("geometry_status") or "unknown"
            tally[status] = tally.get(status, 0) + 1
            if status != "plausible":
                blocking.append({
                    "connection_id": conn.get("connection_id"),
                    "type": conn.get("type"),
                    "geometry_status": status,
                    "reasons": conn.get("reasons") or [],
                })
        draft = _read_package_member(
            package_path,
            "simulation/assembly_cae_setup_draft.json",
            package_reader=package_reader,
        )
        # Interface coverage is only worth reporting now that a warning means a
        # defect rather than a geometry (#497) — before, every correct interface
        # warned, so forwarding it would have been pure noise.
        mesh_diag = _read_package_member(
            package_path,
            "diagnostics/assembly_mesh_interface_diagnostics.json",
            package_reader=package_reader,
        )
        interfaces = {
            "safe_for_solver": (mesh_diag or {}).get("safe_for_solver"),
            "summary": (mesh_diag or {}).get("summary") or {},
            "blocking": (mesh_diag or {}).get("blocking_interfaces") or [],
        } if mesh_diag else None
        return {
            "present": True,
            "part_count": len(assembly.get("parts") or []),
            "interface_count": len(assembly.get("interfaces") or []),
            "connection_count": len(assembly.get("connections") or []),
            "connection_status": tally,
            "attention": blocking[:8],
            "cae_draft_status": (draft or {}).get("status"),
            "needs_user_input": ((draft or {}).get("needs_user_input") or [])[:8],
            "interfaces": interfaces,
            "honesty": {
                "contact_physics_modeled": False,
                "bolt_preload_modeled": bool(
                    ((geometry or {}).get("provenance") or {}).get("bolt_preload_modeled")
                ),
                "note": "Assembly connections are simplified proxies, not nonlinear contact.",
            },
        }
    except Exception as exc:
        return {"present": False, "error": f"{type(exc).__name__}: {exc}"}


def _project_block(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": project.get("id"),
        "name": project.get("name"),
        "status": project.get("status"),
        "source_step": project.get("source_step"),
        "aieng_file": project.get("aieng_file"),
        "web_asset": project.get("web_asset"),
    }


def _cae_block(
    fallback: dict[str, Any] | None,
    summaries: dict[str, Any],
) -> dict[str, Any]:
    cae = fallback.get("cae") if isinstance(fallback, dict) else None
    if not isinstance(cae, dict):
        cae = {}
    block = {
        "present": bool(cae.get("present")),
        "materials": cae.get("materials") or [],
        "loads": cae.get("loads") or [],
        "boundary_conditions": cae.get("boundary_conditions") or [],
        "constraints_count": cae.get("constraints_count") or 0,
        "materials_count": cae.get("materials_count") or 0,
        "loads_count": cae.get("loads_count") or 0,
        "boundary_conditions_count": cae.get("boundary_conditions_count") or 0,
        "results_available": bool(cae.get("results_available")),
        "available_fields": cae.get("available_fields") or [],
        "solver_status": cae.get("solver_status") or {},
        "mapping": cae.get("mapping"),
        "artifact_detection": summaries.get("artifact_detection"),
        "preprocessing_summary": _compact_summary(summaries.get("preprocessing")),
        "simulation_run_summary": _compact_summary(summaries.get("simulation_run")),
        "result_summary": _compact_summary(summaries.get("result")),
        "fea_setup_draft": summaries.get("fea_setup_draft"),
        "template_fixture": summaries.get("template_fixture"),
    }
    # B4: profile and compact the CAE block if it grows unexpectedly large.
    block = compact_cae_block(block, label="agent_context.cae_block")
    return block


def _compact_summary(summary: Any) -> Any:
    if not isinstance(summary, dict):
        return summary
    keep = (
        "status",
        "summary",
        "computed_values",
        "design_target_comparisons",
        "recommended_next_actions",
        "limitations",
        "warnings",
        "errors",
        "artifacts",
        "runs",
    )
    out = {k: summary.get(k) for k in keep if k in summary}
    return out or summary


def _design_targets_block(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        response = {}
    targets = [t for t in response.get("targets") or [] if isinstance(t, dict)]
    return {
        "ok": bool(response.get("ok")),
        "artifact_path": response.get("artifact_path"),
        "count": len(targets),
        "targets": targets,
        "warnings": response.get("warnings") or [],
    }


def _computed_metrics_block(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        response = {}
    doc = response.get("document") if isinstance(response.get("document"), dict) else {}
    return {
        "ok": bool(response.get("ok")),
        "artifact_path": response.get("artifact_path"),
        "metrics_count": response.get("metrics_count") or 0,
        "load_case_count": response.get("load_case_count") or 0,
        "global_metrics": doc.get("global_metrics") or {},
        "load_cases": doc.get("load_cases") or [],
        "target_mapping": response.get("target_mapping") or [],
        "warnings": response.get("warnings") or [],
        "errors": response.get("errors") or [],
    }


def _target_comparison_block(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {
            "available": False,
            "summary": {"total": 0, "pass": 0, "fail": 0, "unknown": 0},
            "items": [],
            "failed_targets": [],
            "unknown_targets": [],
            "warnings": ["Target comparison unavailable."],
        }
    # `target_comparison._response` emits the block under "comparison" (singular)
    # AND flattens summary/items to the top level. Reading only "comparisons"
    # (plural) silently matched nothing, so this block reported
    # `available: false` with no warning for EVERY project — the design-target
    # verdict never reached the surface agents actually read (measured
    # 2026-08-11: the core comparator returned pass/unknown correctly while
    # agent_context showed nothing). Accept every shape it can arrive in.
    comparisons: dict[str, Any] = {}
    for key in ("comparison", "comparisons"):
        value = response.get(key)
        if isinstance(value, dict) and (value.get("items") or value.get("summary")):
            comparisons = value
            break
    items = [i for i in comparisons.get("items") or [] if isinstance(i, dict)]
    if not items:
        items = [i for i in response.get("items") or [] if isinstance(i, dict)]
        if items and not comparisons:
            comparisons = {"summary": response.get("summary") or {}, "items": items}
    failed = [i for i in items if i.get("status") == "fail"]
    unknown = [i for i in items if i.get("status") not in {"pass", "fail"}]
    return {
        "available": bool(items),
        "summary": comparisons.get("summary") or {},
        "items": items,
        "failed_targets": failed,
        "unknown_targets": unknown,
        "warnings": response.get("warnings") or [],
    }


def _available_actions(
    cad: dict[str, Any],
    computed_metrics: Any,
    comparison: Any,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for rec in cad.get("next_recommended_actions") or []:
        if not isinstance(rec, dict):
            continue
        reference = rec.get("reference")
        actions.append({
            "id": rec.get("kind") or reference or "cad_next_step",
            "label": rec.get("label") or "CAD next step",
            "tool_hint": reference,
            "rationale": rec.get("rationale"),
            "requires_approval": _requires_approval(str(reference or "")),
        })

    if not isinstance(computed_metrics, dict) or not computed_metrics.get("document"):
        actions.append({
            "id": "import_computed_metrics",
            "label": "Import or extract computed metrics from solver results.",
            "tool_hint": "cae.extract_solver_results",
            "rationale": "No computed metrics are available for target comparison.",
            "requires_approval": False,
        })
    if not isinstance(comparison, dict) or not (comparison.get("comparisons") or {}).get("items"):
        actions.append({
            "id": "compare_targets",
            "label": "Compare design targets once metrics are available.",
            "tool_hint": "aieng.inspect_package",
            "rationale": "Re-inspect the package to read pass/fail/unknown target status once "
                         "computed metrics exist; that status guides the next engineering move.",
            "requires_approval": False,
        })
    return _dedupe_actions(actions)


def _requires_approval(tool_hint: str) -> bool:
    return tool_hint in {
        "cae.run_solver",
    }


def _agent_brief(context: dict[str, Any]) -> dict[str, Any]:
    cad = context.get("cad") if isinstance(context.get("cad"), dict) else {}
    cae = context.get("cae") if isinstance(context.get("cae"), dict) else {}
    targets = context.get("design_targets") if isinstance(context.get("design_targets"), dict) else {}
    comparison = context.get("target_comparison") if isinstance(context.get("target_comparison"), dict) else {}
    assembly = context.get("assembly") if isinstance(context.get("assembly"), dict) else {}

    failed_count = len(comparison.get("failed_targets") or [])
    unknown_count = len(comparison.get("unknown_targets") or [])
    focus: list[str] = []
    invalid_joints = [
        c for c in (assembly.get("attention") or [])
        if c.get("geometry_status") == "invalid"
    ]
    if invalid_joints:
        focus.append(
            "fix the refused assembly connection(s): "
            + ", ".join(str(c.get("connection_id")) for c in invalid_joints[:3])
        )
    if cad.get("geometry_evidence_level") in {"none", "metadata"}:
        focus.append("obtain real CAD geometry or live CAD snapshot")
    if not cae.get("materials") and not cae.get("fea_setup_draft"):
        focus.append("define material and FEA setup")
    if not targets.get("count"):
        focus.append("define design targets")
    if failed_count:
        focus.append("propose CAD/CAE changes for failed targets")
    elif unknown_count:
        focus.append("resolve unknown target comparisons")
    if not focus:
        focus.append("inspect current evidence and select the next engineering action")

    return {
        "part_summary": _part_summary(context),
        "physics_summary": _physics_summary(cae),
        "target_status_summary": {
            "target_count": targets.get("count") or 0,
            "failed_count": failed_count,
            "unknown_count": unknown_count,
            "summary": comparison.get("summary") or {},
        },
        "next_decision_focus": focus,
    }


def _part_summary(context: dict[str, Any]) -> str:
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    cad = context.get("cad") if isinstance(context.get("cad"), dict) else {}
    geometry = cad.get("known_geometry") if isinstance(cad.get("known_geometry"), dict) else {}
    assembly = context.get("assembly") if isinstance(context.get("assembly"), dict) else {}
    name = project.get("name") or project.get("id") or "project"
    kind = geometry.get("geometry_kind") or geometry.get("primitive")
    evidence = cad.get("geometry_evidence_level") or "unknown"
    if kind:
        base = f"{name}: {kind} geometry with {evidence} evidence."
    else:
        base = f"{name}: CAD geometry evidence level is {evidence}."
    if assembly.get("present"):
        base += (
            f" Assembly IR: {assembly.get('part_count')} part(s), "
            f"{assembly.get('connection_count')} connection(s)."
        )
    return base


def _physics_summary(cae: dict[str, Any]) -> str:
    materials = len(cae.get("materials") or [])
    loads = len(cae.get("loads") or [])
    bcs = len(cae.get("boundary_conditions") or [])
    if cae.get("fea_setup_draft"):
        return "FEA setup draft is available for agent interpretation."
    if materials or loads or bcs:
        return f"CAE setup has {materials} material(s), {loads} load(s), and {bcs} boundary condition(s)."
    return "CAE physics setup is not yet explicit."


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for action in actions:
        key = str(action.get("id") or action.get("tool_hint") or action.get("label"))
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out


__all__ = ["CLAIM_BOUNDARY", "SCHEMA_VERSION", "build_agent_context"]
