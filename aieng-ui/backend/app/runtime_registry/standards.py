"""standards runtime tool registrations.

Extracted from runtime_tool_registry.py to keep domain logic focused.
"""

from __future__ import annotations

import logging
from typing import Any

from ..legacy_app_symbols import sync_main_symbols

LOGGER = logging.getLogger("app.app_factory")


def register_standards_tools(rt: Any, active_settings: Any, app_context: Any, _schema: Any) -> dict[str, Any]:
    """Register standards runtime tools."""
    sync_main_symbols(globals())
    _delete_project_everywhere = app_context.delete_project_everywhere
    _load_project_feature_parameters = app_context.load_project_feature_parameters

    def _tool_inspect_mcp_capabilities(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        desired = str(inp.get("desired_outcome") or inp.get("message") or "").strip().lower()
        caps = agent_workbench.list_capabilities(active_settings)
        if desired:
            tokens = [part for part in re.split(r"\W+", desired) if part]
            caps = [
                cap for cap in caps
                if any(
                    token in str(cap.get("name") or "").lower()
                    or token in str(cap.get("purpose") or "").lower()
                    or token in str(cap.get("category") or "").lower()
                    for token in tokens
                )
            ] or caps
        return {
            "status": "success",
            "operation": "aieng_inspect_capabilities",
            "desired_outcome": inp.get("desired_outcome") or "",
            "capabilities": caps[:80],
            "registered_runtime_tool_count": len(rt.registered_tool_names()),
            "claim_policy": {
                "claims_advanced": False,
                "requires_explicit_update_claim": True,
            },
        }

    # ── materials tools ─────────────────────────────────────────────────────────

    def _tool_list_materials(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import materials_bridge as _mb
        return _mb.list_materials(
            category=str(inp.get("category") or "").strip() or None,
            query=str(inp.get("query") or "").strip() or None,
        )

    rt.register_tool(
        "list_materials",
        _tool_list_materials,
        read_only=True,
        description="List all available engineering materials with properties. Optional filter by category or search query.",
        input_schema=_schema("list_materials"),
    )

    def _tool_get_material_details(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import materials_bridge as _mb
        return _mb.get_material_details(str(inp.get("material_name") or "").strip())

    rt.register_tool(
        "get_material_details",
        _tool_get_material_details,
        read_only=True,
        description="Return full properties for a specific material including E, nu, density, yield strength, ultimate strength, thermal expansion.",
        input_schema=_schema("get_material_details"),
    )

    def _tool_compare_materials(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import materials_bridge as _mb
        raw = inp.get("material_names")
        names = list(raw) if isinstance(raw, (list, tuple)) else []
        return _mb.compare_materials(names)

    rt.register_tool(
        "compare_materials",
        _tool_compare_materials,
        read_only=True,
        description="Compare properties of two or more materials side by side with normalized scores.",
        input_schema=_schema("compare_materials"),
    )

    # ── standard parts tools ────────────────────────────────────────────────────

    def _tool_list_standard_parts(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import standards_bridge as _sb
        return _sb.list_standard_parts(
            category=str(inp.get("category") or "").strip() or None,
        )

    rt.register_tool(
        "list_standard_parts",
        _tool_list_standard_parts,
        read_only=True,
        description="List available standard part categories and types (fasteners, bearings, shafts, profiles, holes).",
        input_schema=_schema("list_standard_parts"),
    )

    def _tool_get_standard_part_specs(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import standards_bridge as _sb
        return _sb.get_standard_part_specs(
            part_type=str(inp.get("part_type") or "").strip(),
            preset_name=str(inp.get("preset_name") or "").strip() or None,
        )

    rt.register_tool(
        "get_standard_part_specs",
        _tool_get_standard_part_specs,
        read_only=True,
        description="Return Shape IR spec and available presets for a standard part type.",
        input_schema=_schema("get_standard_part_specs"),
    )

    def _tool_insert_standard_part(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import standards_bridge as _sb
        return _sb.insert_standard_part(
            active_settings=active_settings,
            project_id=str(inp.get("project_id") or "").strip() or None,
            package_path=str(inp.get("package_path") or "").strip() or None,
            part_type=str(inp.get("part_type") or "").strip(),
            parameters=inp.get("parameters") if isinstance(inp.get("parameters"), dict) else {},
            position=inp.get("position") if isinstance(inp.get("position"), list) else None,
            orientation=inp.get("orientation") if isinstance(inp.get("orientation"), list) else None,
            part_name=str(inp.get("part_name") or "").strip() or None,
            preset_name=str(inp.get("preset_name") or "").strip() or None,
        )

    rt.register_tool(
        "insert_standard_part",
        _tool_insert_standard_part,
        requires_approval=True,
        read_only=False,
        destructive=False,
        description="[APPROVAL REQUIRED] Insert a standard part (fastener, bearing, profile, etc.) into the current project as Shape IR. Recompiles the package on success.",
        input_schema=_schema("insert_standard_part"),
    )

    def _tool_set_part_material(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import standards_bridge as _sb
        return _sb.set_part_material(
            active_settings=active_settings,
            project_id=str(inp.get("project_id") or "").strip() or None,
            package_path=str(inp.get("package_path") or "").strip() or None,
            part_name=str(inp.get("part_name") or "").strip(),
            material_name=str(inp.get("material_name") or "").strip(),
            override_properties=inp.get("override_properties") if isinstance(inp.get("override_properties"), dict) else None,
        )

    rt.register_tool(
        "set_part_material",
        _tool_set_part_material,
        requires_approval=True,
        read_only=False,
        destructive=False,
        description="[APPROVAL REQUIRED] Assign a material to a named part in the current project. Updates graph/feature_graph.json.",
        input_schema=_schema("set_part_material"),
    )

    def _tool_generate_bom(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import standards_bridge as _sb
        return _sb.generate_bom(
            active_settings=active_settings,
            project_id=str(inp.get("project_id") or "").strip() or None,
            package_path=str(inp.get("package_path") or "").strip() or None,
            fmt=str(inp.get("format") or "").strip() or None,
        )

    rt.register_tool(
        "generate_bom",
        _tool_generate_bom,
        read_only=True,
        description="Generate a Bill of Materials from the current project parts, including standard parts and their quantities.",
        input_schema=_schema("generate_bom"),
    )

    return {}
