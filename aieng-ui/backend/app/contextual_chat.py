"""Context-aware engineering chat: injects current project state into every conversation turn.

Reads geometry, FEA setup, simulation results, verdict, and design targets from the
.aieng package and prepends them as a structured system context block so the LLM
can answer engineering questions grounded in the actual project state.

No external tool calls — reads from the package ZIP, calls Claude API, returns text.
"""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Any

import yaml
from aieng.simulation.cae_mapping_writer import mapping_target_id
from fastapi import HTTPException


# ── Package I/O ───────────────────────────────────────────────────────────────

def _read_member(package_path: Path, member: str) -> bytes | None:
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            return zf.read(member)
    except Exception:
        pass
    return None


# ── Pointer formatting helpers ────────────────────────────────────────────────

def _format_pointer_block(
    parts: list[str],
    item: dict[str, Any],
    label: str,
    entity_index: dict[str, Any],
    cae_mapping: dict[str, Any],
) -> None:
    """Append a formatted pointer line for a BC or Load, with validation badges."""
    item_id = item.get("id", "?")
    pointers = item.get("target_pointers") or []
    face_ids = item.get("target_face_ids") or []

    if not pointers and not face_ids:
        return

    # Build mapping string: @face:xxx → face_003, face_004
    mapping_parts: list[str] = []
    warnings: list[str] = []
    for ptr in pointers:
        ptr_str = str(ptr)
        if ptr_str.startswith("@") and ":" in ptr_str:
            entity_id = ptr_str.split(":", 1)[1]
            entry = entity_index.get(entity_id)
            if entry is None:
                warnings.append(f"{ptr_str} NOT FOUND in B-Rep entity index — needs manual confirmation")
                mapping_parts.append(f"{ptr_str} → ⚠️ unknown")
            else:
                kind = entry.get("kind", "?")
                if kind == "group":
                    members = entry.get("members") or []
                    mapping_parts.append(f"{ptr_str} → {', '.join(members)}")
                else:
                    mapping_parts.append(f"{ptr_str} → {entity_id}")
        else:
            mapping_parts.append(ptr_str)

    # Check cae_mapping confidence for this item
    for mapping in cae_mapping.get("mappings", []):
        maps_to = mapping.get("maps_to") or {}
        if mapping_target_id(mapping) == item.get("target_feature"):
            conf = mapping.get("confidence", "")
            src = maps_to.get("selection_source", "")
            if conf == "ai_generated" or src == "ai_generated":
                warnings.append("AI-generated selection — verify in CAD before solving")
            break

    # Also check if the mapping file itself is stale
    if cae_mapping.get("stale"):
        warnings.append("CAE mapping is STALE — re-run AI preprocessing after CAD changes")

    line = f"  {label} {item_id}: " + ("; ".join(mapping_parts) if mapping_parts else "(no pointers)")
    parts.append(line)
    for w in warnings:
        parts.append(f"    ⚠️  {w}")


def _append_canonical_result_summary(parts: list[str], summary: dict[str, Any]) -> bool:
    """Append normalized CAE result context from results/result_summary.json.

    Returns True when a canonical summary was usable. The legacy REST summary
    formatter remains as a compatibility fallback for old packages.
    """
    computed = summary.get("computed_values") or {}
    contract = summary.get("result_contract") or {}
    if not isinstance(computed, dict) or not isinstance(contract, dict):
        return False

    if computed.get("extrema_computed"):
        tier = str(contract.get("claim_tier") or "imported_computed_metrics")
        if contract.get("solver_execution_evidence") is True:
            label = "SOLVER RESULT"
        elif tier == "legacy_rest_result":
            label = "LEGACY REST RESULT"
        else:
            label = "IMPORTED METRICS"

        sim_line = f"Simulation: {label}"
        vm = computed.get("max_von_mises_stress")
        if isinstance(vm, dict) and vm.get("value") is not None:
            sim_line += f" — sigma_max={float(vm['value']):.1f} {vm.get('unit') or 'MPa'}"
        ux = computed.get("max_displacement")
        if isinstance(ux, dict) and ux.get("value") is not None:
            sim_line += f", u_max={float(ux['value']):.3f} {ux.get('unit') or 'mm'}"
        parts.append(sim_line)

        source = computed.get("source") or contract.get("metrics_source")
        if source:
            parts.append(f"  Source: {source}; claim_tier={tier}")
        if not contract.get("solver_execution_evidence"):
            parts.append("  Note: scalar metrics do not by themselves prove AIENG solver execution.")
        return True

    status = summary.get("status") or {}
    if isinstance(status, dict) and status.get("mode") in {"cae_setup", "cad_only"}:
        parts.append("Simulation: not yet run")
        return True
    return False


# ── Context block builder ─────────────────────────────────────────────────────

def build_context_block(package_path: Path | None) -> str:
    """Read current project state and format it as a structured LLM context block."""
    if not package_path or not package_path.exists():
        return "No project package loaded."

    parts: list[str] = []

    # Geometry summary
    topo_raw = _read_member(package_path, "geometry/topology_map.json")
    if topo_raw:
        try:
            topo = json.loads(topo_raw)
            entities = topo.get("entities") or topo.get("faces") or []
            faces = [e for e in entities if isinstance(e, dict) and e.get("type") == "face"]
            bboxes = [e["bounding_box"] for e in faces if len(e.get("bounding_box") or []) == 6]
            if bboxes:
                xspan = max(b[3] for b in bboxes) - min(b[0] for b in bboxes)
                yspan = max(b[4] for b in bboxes) - min(b[1] for b in bboxes)
                zspan = max(b[5] for b in bboxes) - min(b[2] for b in bboxes)
                parts.append(f"Geometry: {len(faces)} faces, envelope {xspan:.1f}×{yspan:.1f}×{zspan:.1f} mm")
            else:
                parts.append(f"Geometry: {len(faces)} faces")
        except Exception:
            parts.append("Geometry: topology available (parse error)")

    # B-Rep pointer digest for precise face/edge references.
    try:
        from . import brep_graph

        digest = brep_graph.load_or_build_digest(package_path, max_items=18)
        if digest:
            parts.append("\n" + digest)
    except Exception:
        pass

    # FEA setup (material, BCs, loads, mesh) + pointer mapping validation
    setup_raw = _read_member(package_path, "simulation/setup.yaml")
    cae_mapping_raw = _read_member(package_path, "simulation/cae_mapping.json")
    entity_index: dict[str, Any] = {}
    try:
        from . import brep_graph

        ei_raw = _read_member(package_path, brep_graph.ENTITY_INDEX_MEMBER)
        if ei_raw:
            entity_index = json.loads(ei_raw)
    except Exception:
        pass

    if setup_raw:
        try:
            setup = yaml.safe_load(setup_raw)
            material = setup.get("material_name") or setup.get("material") or "unknown"
            bcs = setup.get("boundary_conditions") or []
            loads = setup.get("loads") or []
            bc_count = len(bcs)
            load_count = len(loads)
            mesh_size = (setup.get("mesh") or {}).get("target_size_mm", "?")
            loads_info = ""
            for ld in loads[:3]:
                v = ld.get("value_n")
                if v is not None:
                    loads_info += f" [{v} N]"
            parts.append(
                f"FEA Setup: material={material}, {bc_count} BC(s), {load_count} load(s){loads_info}, mesh={mesh_size} mm"
            )

            # Display pointer mappings with validation
            cae_mapping: dict[str, Any] = {}
            if cae_mapping_raw:
                try:
                    cae_mapping = json.loads(cae_mapping_raw)
                except Exception:
                    pass

            for bc in bcs:
                _format_pointer_block(parts, bc, "BC", entity_index, cae_mapping)
            for ld in loads:
                _format_pointer_block(parts, ld, "Load", entity_index, cae_mapping)

            # If there are no BCs/Loads but the mapping itself is stale, warn anyway
            if cae_mapping.get("stale") and not bcs and not loads:
                parts.append("  ⚠️  CAE mapping is STALE — re-run AI preprocessing after CAD changes")
        except Exception:
            parts.append("FEA Setup: setup.yaml present (parse error)")

    # Simulation results + verdict. Prefer the normalized package-level summary;
    # fall back to the legacy REST summary for older packages.
    canonical_results_raw = _read_member(package_path, "results/result_summary.json")
    canonical_used = False
    if canonical_results_raw:
        try:
            canonical = json.loads(canonical_results_raw)
            if isinstance(canonical, dict):
                canonical_used = _append_canonical_result_summary(parts, canonical)
        except Exception:
            parts.append("Simulation: result summary present (parse error)")

    results_raw = None if canonical_used else _read_member(package_path, "simulation/results_summary.json")
    if results_raw:
        try:
            results = json.loads(results_raw)
            status = results.get("status", "unknown")
            if status == "success":
                vm = results.get("von_mises_max_mpa")
                ux = results.get("displacement_max_mm")
                nc = results.get("node_count")
                ms = results.get("mesh_size_mm")
                sim_line = "Simulation: COMPLETED"
                if vm is not None:
                    sim_line += f" — σ_max={vm:.1f} MPa"
                if ux is not None:
                    sim_line += f", u_max={ux:.3f} mm"
                if nc:
                    sim_line += f", {nc:,} nodes"
                if ms:
                    sim_line += f", mesh {ms} mm"
                parts.append(sim_line)

                verdict = results.get("verdict") or {}
                overall = verdict.get("overall", "no_targets")
                if overall not in ("no_targets", ""):
                    v_line = f"Verdict: {overall.upper()} ({verdict.get('pass_count', 0)} passed, {verdict.get('fail_count', 0)} failed)"
                    parts.append(v_line)
                    for item in (verdict.get("items") or []):
                        label = item.get("label") or item.get("metric") or item.get("target_id", "?")
                        actual = item.get("actual_value")
                        thresh = item.get("threshold")
                        op = item.get("operator", "<=")
                        unit = item.get("unit") or ""
                        s = item.get("status", "unknown")
                        if actual is not None and thresh is not None:
                            parts.append(
                                f"  - {label}: {s.upper()} "
                                f"(actual {actual:.2f}{unit} vs limit {op} {thresh}{unit})"
                            )
                        else:
                            parts.append(f"  - {label}: {s.upper()}")
            elif status == "tools_unavailable":
                parts.append("Simulation: solver tools not installed (Gmsh/CalculiX)")
            elif status == "solver_error":
                parts.append(f"Simulation: SOLVER ERROR (returncode={results.get('returncode', '?')})")
            else:
                parts.append("Simulation: not yet run")
        except Exception:
            parts.append("Simulation: results present (parse error)")
    elif not canonical_used:
        parts.append("Simulation: not yet run")

    # Design targets
    targets_raw = _read_member(package_path, "task/design_targets.yaml")
    if targets_raw:
        try:
            doc = yaml.safe_load(targets_raw)
            targets = (doc.get("targets") or []) if isinstance(doc, dict) else []
            if targets:
                parts.append(f"Design Targets ({len(targets)} defined):")
                for t in targets[:8]:
                    label = t.get("label") or t.get("metric") or t.get("target_id", "?")
                    op = t.get("operator") or t.get("comparator", "?")
                    val = t.get("value") if t.get("value") is not None else t.get("threshold")
                    unit = t.get("unit") or ""
                    parts.append(f"  - {label}: {op} {val}{unit}")
            else:
                parts.append("Design Targets: none defined")
        except Exception:
            parts.append("Design Targets: file present (parse error)")
    else:
        parts.append("Design Targets: none defined — add task/design_targets.yaml to enable pass/fail assessment")

    return "\n".join(parts)


_SYSTEM_PROMPT = """\
You are an engineering assistant embedded in a CAD/FEA workbench. You help engineers \
interpret simulation results, understand why analyses pass or fail, and identify \
practical design improvements.

CURRENT PROJECT STATE:
{context_block}

Guidelines:
- Be concise and specific. Reference actual numbers from the project state above.
- If the simulation failed a target, focus on actionable changes (geometry dimensions, \
material substitution, load redistribution, mesh refinement).
- If asked to run something, tell the user what to type in chat:
    "generate <description>" → generate new CAD geometry
    "set up FEA for <task>" → AI preprocessing (material + BCs + loads)
    "run simulation" → Gmsh mesh + CalculiX solve
    "change material to <name>" → re-preprocess with new material + re-simulate
    "refine mesh to <N> mm" → re-simulate with finer mesh
- Do not invent numbers. Only reference values from the project state.
- Answer in the same language the user writes in.

CAD MODELING (build123d):
- Color(r, g, b) takes exactly 3 arguments (0..1). No alpha parameter.
- Cylinder(radius, height) takes exactly 2 arguments.
- Use .moved(Location(...)) to position shapes; .move() does not exist on Shape.
- Polygon(...) returns a Wire. To extrude: extrude(Face(Polygon(...)), amount=...).
- Final line MUST be: result = <shape>. Do NOT call export_step/export_stl/export_gltf.
- Pre-injected helpers (prefer over raw BuildSketch/BuildPart): lofted_stack, rounded_box, capsule, tapered_cylinder, swept_tube, revolved_profile, organic_blend.
- Organic forms (characters/vehicles): use loft/revolve/sweep + mirror + fillet. Avoid Box stacking for exterior curves.
- Mechanical parts: use canonical labels (base_plate, mounting_hole, rib, boss, flange) and honor min wall >= 3mm.\
"""


# ── Main entry ────────────────────────────────────────────────────────────────

def chat_with_context(
    settings: Any,
    project_id: str,
    message: str,
    history: list[dict[str, str]],
    api_key: str | None = None,
) -> dict[str, Any]:
    """Single-turn LLM response grounded in the current project state.

    history: list of {"role": "user"|"assistant", "content": str} (most recent last).
    Returns: {"reply": str, "context_used": bool, "project_id": str}
    """
    import anthropic

    from .copilot_loop import _resolve_package

    # Resolve package (graceful — no package is OK, context will say so)
    package_path: Path | None = None
    try:
        package_path = _resolve_package(settings, project_id)
    except HTTPException:
        pass

    context_block = build_context_block(package_path)
    system_prompt = _SYSTEM_PROMPT.format(context_block=context_block)

    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not set — cannot call Claude for contextual chat",
        )

    # Build message list — last 20 turns to stay within context budget
    messages: list[dict[str, str]] = []
    for h in history[-20:]:
        role = h.get("role", "")
        content = str(h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    resolved_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    resolved_base_url = os.environ.get("ANTHROPIC_BASE_URL")
    client = anthropic.Anthropic(
        api_key=resolved_key,
        **({"base_url": resolved_base_url} if resolved_base_url else {}),
    )
    response = client.messages.create(
        model=resolved_model,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )

    reply = response.content[0].text if response.content else "(no reply)"
    context_used = context_block != "No project package loaded."

    return {
        "reply": reply,
        "context_used": context_used,
        "project_id": project_id,
    }
