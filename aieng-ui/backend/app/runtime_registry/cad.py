"""cad runtime tool registrations.

Extracted from runtime_tool_registry.py to keep domain logic focused.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import operation_receipt as _receipt
from ..legacy_app_symbols import sync_main_symbols

LOGGER = logging.getLogger("app.app_factory")


def register_cad_tools(rt: Any, active_settings: Any, app_context: Any, _schema: Any) -> dict[str, Any]:
    """Register cad runtime tools."""
    sync_main_symbols(globals())
    _delete_project_everywhere = app_context.delete_project_everywhere
    _load_project_feature_parameters = app_context.load_project_feature_parameters

    def _tool_cad_confirm_modeling_plan(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "plan_confirmed": True,
            "project_id": str(inp.get("project_id") or ""),
            "summary": str(inp.get("summary") or ""),
            "steps": list(inp.get("steps") or []),
            "assumptions": list(inp.get("assumptions") or []),
            "scope": str(inp.get("scope") or ""),
            "message": "Modeling plan confirmed in the agent client. Continue within the approved scope.",
        }

    rt.register_tool(
        "cad.confirm_modeling_plan",
        _tool_cad_confirm_modeling_plan,
        requires_approval=True,
        input_schema=_schema("cad.confirm_modeling_plan"),
        description=(
            "[APPROVAL REQUIRED] Present a proposed CAD modeling plan in the connecting agent's "
            "native confirmation UI. This authorization tool does not write files or execute CAD. "
            "Call it after preparing the plan instead of ending the conversation or asking for a "
            "plain-text reply. If the user approves, it returns immediately and the agent should "
            "continue in the same task with ordinary CAD build/edit tools. If the user denies it, "
            "do not mutate CAD. A materially changed scope requires another plan confirmation."
        ),
    )

    def _tool_cad_plan_build123d_skill(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_skill_planner as _planner

        return _planner.plan_build123d_skill(inp)

    rt.register_tool(
        "cad.plan_build123d_skill",
        _tool_cad_plan_build123d_skill,
        input_schema=_schema("cad.plan_build123d_skill"),
        description=(
            "Read-only CAD skill planner. Use this before cad.execute_build123d for common "
            "create-new parts — mechanical (flange, mounting plate, L-bracket, enclosure, "
            "bushing) and organic starters (aircraft, vehicle/car, wheel, built from the "
            "fuselage_profile/naca_airfoil/wheel/rounded_box primitives). It interprets the "
            "request, records assumptions, and returns a parameterized build123d execute_input "
            "(UPPER_SNAKE_CASE constants, named parts) for the agent to review and then pass to "
            "cad.execute_build123d after the modeling plan is explicitly confirmed. It does not mutate the "
            "package and does not bypass Autopilot."
        ),
    )

    def _record_cad_snapshot(result: dict[str, Any], project_id: Any, tool_name: str) -> None:
        # Best-effort undo timeline: snapshot the package after a successful CAD
        # mutation so cad.restore_snapshot can roll back. Never affects the tool.
        if isinstance(result, dict) and result.get("status") == "ok" and project_id:
            from .. import snapshots as _snap

            _snap.record_snapshot(active_settings, str(project_id), tool_name)

    def _tool_cad_execute_build123d(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        result = _cg.execute_build123d_code(active_settings, project_id, inp)
        _record_cad_snapshot(result, project_id, "cad.execute_build123d")
        return _receipt.receipt_from_execute_build123d(result)

    rt.register_tool(
        "cad.execute_build123d",
        _tool_cad_execute_build123d,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.execute_build123d"),
        description=(
            "Execute caller-supplied build123d Python code under an explicitly approved modeling plan. "
            "The agent writes the full build123d script and this tool runs it in a sandboxed subprocess — "
            "no LLM API key needed. "
            "Code contract: bind the final model to a variable named `result`; omit all export calls "
            "(the runner adds export_step/export_stl/export_gltf automatically). "
            "Name parts by setting `.label` on shapes and combining with `Compound(children=[...])` — "
            "labels become named parts in topology_map/feature_graph you can reference later. "
            "Color parts by setting `.color = Color(r, g, b)` (RGB 0..1) — colors render in both "
            "the agent thumbnail AND the GLB the UI viewer displays. "
            "The runner also accepts legacy `Compound([...])` and preserves child labels. "
            "Use mode='append' to build incrementally: the previous model is exposed as `previous_result` "
            "and your code adds to it (still reassigning `result`). "
            "Returns a 2x2 contact-sheet image (front/side/top/iso views) so you can visually verify "
            "alignment from multiple angles — inspect all four views, alignment problems hide in iso. "
            "Also returns named_parts (all named parts now in the model), parts_added (what this step "
            "introduced), mode, and used_base — so you get text-side feedback even if the image isn't rendered. "
            "For iterative loops, pass response_detail='compact' to return a one-line geometry summary "
            "and suppress the thumbnail unless thumbnail=true. Identical source re-runs may return cache_hit=true "
            "without re-running build123d. "
            "Writes source.py, generated.step, preview.stl/.glb, topology_map.json, and feature_graph.json "
            "into the .aieng package; sets project status to viewer_ready_glb."
        ),
    )

    def _tool_cad_get_source(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.read_cad_source(active_settings, project_id)

    rt.register_tool(
        "cad.get_source",
        _tool_cad_get_source,
        input_schema=_schema("cad.get_source"),
        description=(
            "Read-only: return the project's accumulated build123d source code plus a "
            "state summary {source, named_parts, has_base}. Call this before cad.execute_build123d "
            "to decide replace vs append, see which named parts already exist, and avoid "
            "re-adding prior logic. has_base=true means append mode is available."
        ),
    )

    def _tool_cad_list_editable_parameters(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from ..agent_autopilot.parameter_binding import summarize_parameter_index

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        # Reuse the same package read the /modify slot binding uses (single source).
        index = _load_project_feature_parameters(str(project_id))
        if index is None:
            return {
                "status": "ok",
                "project_id": project_id,
                "parameters": [],
                "summary": {"total": 0, "by_scope": {"local": 0, "global": 0, "unscoped": 0}},
                "message": (
                    "No editable-parameter index available — the project has no feature "
                    "graph yet. Build CAD (cad.execute_build123d) with dimensions declared "
                    "as UPPER_SNAKE_CASE constants to make them editable."
                ),
            }
        # Drop the internal search_tokens; keep the user/agent-facing fields.
        parameters = [{k: v for k, v in entry.items() if k != "search_tokens"} for entry in index]
        summary = summarize_parameter_index(index)
        message = (
            f"{summary['total']} editable parameter(s): "
            f"{summary['by_scope']['local']} local, {summary['by_scope']['global']} global "
            f"(shared — edits ripple), {summary['by_scope']['unscoped']} unscoped."
            if summary["total"]
            else (
                "No editable parameters found. Declare dimensions as UPPER_SNAKE_CASE "
                "constants in the build123d source so cad.edit_parameter can target them."
            )
        )
        return {
            "status": "ok",
            "project_id": project_id,
            "parameters": parameters,
            "summary": summary,
            "message": message,
        }

    rt.register_tool(
        "cad.list_editable_parameters",
        _tool_cad_list_editable_parameters,
        input_schema=_schema("cad.list_editable_parameters"),
        description=(
            "Read-only: list the CAD parameters that can be edited fast and deterministically "
            "via cad.edit_parameter (the 'point' half of point-and-shoot editing). Reads the "
            "project's feature graph and returns, per parameter, its featureId / parameterName / "
            "editable constant (cad_parameter_name) / current value / min-max range, plus a "
            "`scope`: 'local' (one named part — the safe local edit), 'global' (a shared "
            "constant — editing ripples across parts) or 'unscoped'. Use this to answer 'what "
            "can I change here?' and to pick a precise cad.edit_parameter target before editing. "
            "Does not modify the package and is never approval-gated."
        ),
    )

    def _tool_cad_critique(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.critique(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.critique",
        _tool_cad_critique,
        input_schema=_schema("cad.critique"),
        description=(
            "Run a deterministic engineering critique of the project geometry. Walks the "
            "feature graph + topology bounding boxes and checks them against process-aware "
            "manufacturing rule packs: cnc (default 3mm wall / 2mm corner / standard drills), "
            "sheet_metal (2mm / 0.5mm / standard drills), fdm (1.2mm / 1mm / no standard-hole "
            "check), sla (0.8mm / 0.4mm / no standard-hole check). Each finding reports the "
            "rule pack and thresholds used. Also detects floating components and missing "
            "mounting interfaces on plate-like parts. Returns structured findings (severity, "
            "category, rule, affected feature, observation, suggested fix) plus a "
            "fail_first_objections list of the top blocking issues. Call after "
            "cad.execute_build123d for engineering parts (brackets, housings, fixtures) "
            "to catch manufacturability problems before user review or FEA setup. "
            "Read-only — does not modify the package."
        ),
    )

    def _tool_cad_design_review(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.design_review(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.design_review",
        _tool_cad_design_review,
        input_schema=_schema("cad.design_review"),
        description=(
            "Read-only self-review that synthesizes the deterministic critique, structural "
            "geometry signals, and editable parameters into ONE prioritized, actionable list "
            "so you can self-correct before presenting a result — not just fix what the user "
            "points out. On top of cad.critique it adds the left/right symmetry checks critique "
            "lacks (broken / missing mirror pairs from the geometry report) and, for each "
            "fixable finding, binds the concrete cad.edit_parameter target (featureId / "
            "parameterName / current value / allowed range) you would edit. Returns a merged "
            "verdict, a severity-ranked `actions` list (findings with a fast parameter fix), and "
            "a recommendation. Changes NOTHING — applying a fix still goes through the "
            "approved modeling-plan cad.edit_parameter / cad.execute_build123d path. "
            "response_detail='compact' returns actions + summary only; 'full' (default) also "
            "returns every finding. Call after building/editing an engineering part."
        ),
    )

    def _tool_cad_tolerance_stackup(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from ..tolerance_analysis import analyze_tolerance_stackup

        contributors = inp.get("contributors")
        if not isinstance(contributors, list) or not contributors:
            return {
                "status": "error",
                "code": "bad_input",
                "message": "contributors must be a non-empty list of dimension/tolerance entries.",
            }
        confidence = inp.get("confidence_level", 0.95)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.95
        return analyze_tolerance_stackup(contributors, confidence_level=confidence)

    rt.register_tool(
        "cad.tolerance_stackup",
        _tool_cad_tolerance_stackup,
        read_only=True,
        input_schema=_schema("cad.tolerance_stackup"),
        description=(
            "Read-only 1D tolerance stack-up analysis. Pass an ordered list of contributors "
            "(each with name, nominal, plus, minus, and optional distribution) and get back "
            "worst-case arithmetic min/max, statistical RSS sigma and confidence-band min/max, "
            "the controlling contributors for each method, and explicit honesty notes about "
            "independence and the +/- 3-sigma assumption. No solver, no geometry mutation."
        ),
    )

    def _tool_cad_validate_subpart(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        return _cg.validate_subpart(active_settings, inp)

    rt.register_tool(
        "cad.validate_subpart",
        _tool_cad_validate_subpart,
        read_only=True,
        input_schema=_schema("cad.validate_subpart"),
        description=(
            "Read-only: execute a build123d fragment in an isolated subprocess (no package "
            "write, no project mutation) and report whether it builds into a usable solid — "
            "build success or the exact error, a non-empty-solid check, solid/face counts, "
            "per-part + total volume/area, and the union bounding box. Use it to verify a "
            "sub-structure (a sketch->solid, a boolean, one sub-assembly) BEFORE committing it "
            "via cad.execute_build123d or cad.replace_part, instead of one-shotting a whole "
            "complex model. 'valid' means it builds into a non-empty solid — NOT a "
            "manifold/watertight or manufacturability guarantee."
        ),
    )

    def _tool_cad_validate_targets(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.validate_targets(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.validate_targets",
        _tool_cad_validate_targets,
        read_only=True,
        input_schema=_schema("cad.validate_targets"),
        description=(
            "Read-only deterministic geometry target validator: verify a build's exact "
            "geometric promises (named parts present, feature present, part count, overall/part "
            "bbox size + center within tolerance, no floating parts, no deep bbox overlap, plus "
            "exact B-Rep checks: no_interference, coaxial_within, faces_flush_within, clearance_within) "
            "against its topology + feature graph. Each target returns pass / fail / unknown with measured "
            "vs expected — catching plausible-looking but mispositioned or over-modeled results. "
            "Exact B-Rep checks run in a sandboxed subprocess on the STEP geometry when available. "
            "Bbox-level, not a GD&T solver; mutates nothing."
        ),
    )

    def _tool_cad_author_brief(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.author_brief(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.author_brief",
        _tool_cad_author_brief,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.author_brief"),
        description=(
            "Author the pre-code CAD brief + validation-target list: declare units, model type, "
            "parts, and key dimensions BEFORE building. Stored as a project sidecar; auto-derives "
            "validation_targets that cad.validate_targets checks the built model against — the "
            "plan→build→verify loop. Planning artifact only; no geometry, no guarantee."
        ),
    )

    def _tool_cad_get_brief(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.get_brief(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.get_brief",
        _tool_cad_get_brief,
        read_only=True,
        input_schema=_schema("cad.get_brief"),
        description="Read-only: return the project's authored CAD brief (units, parts, validation_targets), or not_found.",
    )

    def _tool_cad_diagnose(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.diagnose(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.diagnose",
        _tool_cad_diagnose,
        read_only=True,
        input_schema=_schema("cad.diagnose"),
        description=(
            "Read-only diagnostic snapshot + repair verdict: composes design_review (critique + "
            "symmetry + modeling fidelity + fix targets), structural geometry checks, and the CAD "
            "brief's validation_targets into one snapshot with risk triggers, a ready / "
            "needs_repair verdict, and prioritized repair_actions. Repair-loop contract: for a "
            "high-risk build, fix every blocking issue and re-diagnose until 'ready' before "
            "presenting the result. Mutates nothing."
        ),
    )

    def _tool_cad_define_part(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.define_assembly_part(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.define_part",
        _tool_cad_define_part,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.define_part"),
        description=(
            "Author one part into the project's Assembly IR v0 (assembly/assembly_ir.json), "
            "initialising it if absent and refreshing the derived part registry / connection "
            "graph / CAE setup draft. Link it to a named CAD part via geometry_ref (verified "
            "against the model topology and reported honestly). Decompose a multi-part model "
            "into assembly parts, then connect them with cad.define_mate. Representation + "
            "validation only — no contact physics, no bolt preload, no solver is implied."
        ),
    )

    def _tool_cad_define_mate(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.define_assembly_mate(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.define_mate",
        _tool_cad_define_mate,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.define_mate"),
        description=(
            "Author one connection (mate) between two already-defined assembly parts "
            "(rigid_tie / bonded / bolted_proxy / welded_proxy / contact_proxy / spring_proxy). "
            "Refuses dangling connections (both parts must exist — call cad.define_part first) and "
            "never records a proxy connection without honest limitations. v0 proxy boundary: "
            "positioning + simplified load transfer only; no contact mechanics, no bolt preload, no solver."
        ),
    )

    def _tool_cad_define_interface(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.define_assembly_interface(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.define_interface",
        _tool_cad_define_interface,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.define_interface"),
        description=(
            "Author one interface binding an assembly part to specific B-Rep entities (@face:* from "
            "the brep graph / agent_context). This makes a mate geometric: once both parts carry "
            "interfaces, a cad.define_mate referencing interface_a/interface_b is resolved to world "
            "coordinates and gets a geometry_status (plausible / warning / invalid). Face refs are "
            "verified against the model topology and reported honestly (face_ids_known), never "
            "fabricated. Geometry validation only — no contact physics, no preload, no solver."
        ),
    )

    def _tool_cad_set_reference_image(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.set_reference_image(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.set_reference_image",
        _tool_cad_set_reference_image,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.set_reference_image"),
        description=(
            "Attach a reference image (real-world photo, drawing, or render) to a project so "
            "subsequent cad.execute_build123d thumbnails include it in a right-hand column for "
            "side-by-side comparison. Pass image_url (HTTP/HTTPS) or image_path (local file). "
            "The image is downscaled to 800x800 max and stored as geometry/reference.png in the "
            ".aieng package — set once, used by every future build. Use this when the user names "
            "a real product/character/vehicle and supplies a picture, or when you want the agent "
            "to calibrate proportions against an actual reference instead of memory."
        ),
    )

    def _tool_cad_search_reference_image(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.search_reference_image(active_settings, str(project_id), inp)

    rt.register_tool(
        "cad.search_reference_image",
        _tool_cad_search_reference_image,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.search_reference_image"),
        description=(
            "Search Wikimedia Commons for a reference image matching a free-text query "
            "(e.g. 'Boeing 747 side view') and attach the best raster match to the project "
            "as its reference image — a convenience wrapper around cad.set_reference_image "
            "for when the user names a real product/character/vehicle but supplies no picture. "
            "Returns the matched page_url so the source and its license can be verified. "
            "Degrades gracefully: status='no_results' means proceed without a reference. "
            "Like cad.set_reference_image, the image is stored as geometry/reference.png and "
            "every future cad.execute_build123d thumbnail tiles it for side-by-side calibration."
        ),
    )

    def _tool_cad_get_named_part_bbox(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        part_name = inp.get("part_name")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        if not part_name:
            return {"status": "error", "code": "missing_part_name", "message": "part_name is required."}
        return _cg.get_named_part_bbox(active_settings, str(project_id), str(part_name))

    rt.register_tool(
        "cad.get_named_part_bbox",
        _tool_cad_get_named_part_bbox,
        input_schema=_schema("cad.get_named_part_bbox"),
        description=(
            "Read-only: look up a named part by its exact topology_map label and return "
            "its bounding_box plus derived center point. Useful for grounded follow-up "
            "instructions like moving or resizing one named component."
        ),
    )

    def _tool_cad_insert_fasteners(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _cg.insert_fasteners(active_settings, str(project_id), dict(inp))

    rt.register_tool(
        "cad.insert_fasteners",
        _tool_cad_insert_fasteners,
        requires_approval=True,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.insert_fasteners"),
        description=(
            "Insert semantic standard-part fasteners for selected hole features. "
            "Matches hole diameter to the metric clearance-hole catalog, chooses screw length "
            "from the mating stack thickness, and appends screw/nut standard_part features plus "
            "a fastener_insertion_report to the .aieng package. Pass explicit hole_feature_ids "
            "or set auto_select_holes=true to populate every feature that carries hole_metadata. "
            "Does not rebuild CAD geometry or claim B-Rep fastener solids exist — it records "
            "semantic placement for downstream BOM and assembly CAE."
        ),
    )

    def _tool_cad_refine(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        if not str(inp.get("feedback") or "").strip():
            return {"status": "error", "code": "missing_feedback", "message": "feedback is required."}
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return {
                "status": "error",
                "message": "ANTHROPIC_API_KEY not configured; cad.refine requires LLM access",
            }
        try:
            return _cg.refine_cad_generation(active_settings, str(project_id), dict(inp))
        except HTTPException as exc:
            return {"status": "error", "message": str(exc.detail)}
        except Exception as exc:
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}

    rt.register_tool(
        "cad.refine",
        _tool_cad_refine,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.refine"),
        description=(
            "Refine the existing build123d model within an explicitly approved modeling plan. "
            "Reads geometry/source.py, asks Claude to edit the code, re-executes it, and writes updated "
            "geometry/topology/preview artifacts back into the .aieng package."
        ),
    )

    def _tool_cad_edit_parameter(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg
        result = _cg.edit_build123d_parameter(
            settings=active_settings,
            project_id=str(inp.get("project_id") or ""),
            feature_id=str(inp.get("featureId") or ""),
            parameter_name=str(inp.get("parameterName") or ""),
            new_value=inp.get("newValue"),
            timeout=int(inp.get("timeout", 120)),
            response_detail=str(inp.get("response_detail") or "full"),
            thumbnail=inp.get("thumbnail") if isinstance(inp.get("thumbnail"), bool) else None,
            confirm_scope_risk=bool(inp.get("confirmScopeRisk")),
        )
        _record_cad_snapshot(result, inp.get("project_id"), "cad.edit_parameter")
        return _receipt.receipt_from_edit_parameter(result)

    rt.register_tool(
        "cad.edit_parameter",
        _tool_cad_edit_parameter,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.edit_parameter"),
        description=(
            "Apply a parametric edit to a CAD model feature. "
            "The encompassing modeling plan must already be explicitly approved. "
            "Performs a fast deterministic text replacement in geometry/source.py "
            "(no LLM round-trip) and re-executes build123d so the change is immediate. "
            "The feature graph must carry editable parameters (UPPER_SNAKE_CASE constants)."
        ),
    )

    def _tool_cad_remove_part(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg
        result = _cg.remove_build123d_part(
            settings=active_settings,
            project_id=str(inp.get("project_id") or ""),
            label=str(inp.get("label") or ""),
            timeout=int(inp.get("timeout", 120)),
            response_detail=str(inp.get("response_detail") or "full"),
            thumbnail=inp.get("thumbnail") if isinstance(inp.get("thumbnail"), bool) else None,
        )
        _record_cad_snapshot(result, inp.get("project_id"), "cad.remove_part")
        return result

    rt.register_tool(
        "cad.remove_part",
        _tool_cad_remove_part,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.remove_part"),
        description=(
            "Remove a named part from the model by its build123d label. "
            "Appends a filter step to geometry/source.py (keeping the script "
            "self-consistent) and re-executes — no LLM. Returns a regression_diff "
            "confirming only that part was dropped. The encompassing modeling plan must already be approved."
        ),
    )

    def _tool_cad_replace_part(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import cad_generation as _cg
        result = _cg.replace_build123d_part(
            settings=active_settings,
            project_id=str(inp.get("project_id") or ""),
            label=str(inp.get("label") or ""),
            code=str(inp.get("code") or ""),
            timeout=int(inp.get("timeout", 120)),
            response_detail=str(inp.get("response_detail") or "full"),
            thumbnail=inp.get("thumbnail") if isinstance(inp.get("thumbnail"), bool) else None,
        )
        _record_cad_snapshot(result, inp.get("project_id"), "cad.replace_part")
        return result

    rt.register_tool(
        "cad.replace_part",
        _tool_cad_replace_part,
        read_only=False,
        destructive=False,
        input_schema=_schema("cad.replace_part"),
        description=(
            "Replace a named part by its build123d label with caller-supplied "
            "build123d code (the code must reassign `result` to the new part and "
            "set result.label). Drops the old part, combines the new one in, and "
            "re-executes — no LLM. Lets the agent refine one part without "
            "resubmitting the whole model. Returns a regression_diff. The encompassing modeling plan must already be approved."
        ),
    )

    def _tool_cad_list_snapshots(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import snapshots as _snap

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        limit = inp.get("limit")
        return _snap.list_snapshots(active_settings, str(project_id), int(limit) if limit else 20)

    rt.register_tool(
        "cad.list_snapshots",
        _tool_cad_list_snapshots,
        input_schema=_schema("cad.list_snapshots"),
        description=(
            "Read-only: list the recent CAD snapshots (undo timeline). A snapshot is "
            "recorded automatically after every successful cad.execute_build123d / "
            "edit_parameter / replace_part / remove_part. Returns tiny metadata only "
            "(snapshot_id, created_at, tool_name, part_count, named_parts) — never "
            "package bytes. Pair with cad.restore_snapshot to roll back."
        ),
    )

    def _tool_cad_restore_snapshot(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import snapshots as _snap

        project_id = inp.get("project_id")
        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        return _snap.restore_snapshot(active_settings, str(project_id), str(inp.get("snapshot_id") or ""))

    rt.register_tool(
        "cad.restore_snapshot",
        _tool_cad_restore_snapshot,
        requires_approval=True,
        input_schema=_schema("cad.restore_snapshot"),
        description=(
            "[APPROVAL REQUIRED] Roll the project back to an earlier CAD snapshot by "
            "snapshot_id (from cad.list_snapshots). Replaces the current .aieng package "
            "with the snapshot and republishes the viewer preview, clearing stale-artifact "
            "flags. Use to undo an unwanted edit. Irreversible from the agent's side "
            "(the current state is not auto-snapshotted before restore), so confirm first."
        ),
    )


    return {}
