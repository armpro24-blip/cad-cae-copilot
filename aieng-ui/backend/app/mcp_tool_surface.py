"""Curated MCP tool surface for clients with a small active-tool budget.

Some MCP clients (e.g. Cursor) advertise a limited number of active tools and
struggle when presented with the full ~90-tool workbench surface. The compact
surface is an opt-in subset that keeps the tools an agent needs for common
CAD → CAE workflows while staying comfortably under those limits.

The full surface remains the default; compact mode is enabled explicitly via
``--compact-tool-surface`` or ``AIENG_MCP_COMPACT_SURFACE=1`` so Claude Code
and other full-surface clients are unaffected.
"""

from __future__ import annotations

import os

# Curated subset of high-frequency agent-facing tools. Keep this list under ~35
# items to stay within Cursor-style ~40-tool caps while preserving the core
# onboarding → CAD → CAE → results workflow.
ESSENTIAL_MCP_TOOLS: frozenset[str] = frozenset({
    # Onboarding + context
    "aieng.agent_readme",
    "aieng.guide",
    "aieng.list_projects",
    "aieng.agent_context",
    "aieng.inspect_package",
    "aieng.convert",

    # AI-driven FEA preprocessing
    "ai_preprocessing.run_ai_preprocessing",

    # CAD authoring / inspection
    "cad.confirm_modeling_plan",
    "cad.plan_build123d_skill",
    "cad.execute_build123d",
    "cad.get_source",
    "cad.list_editable_parameters",
    "cad.critique",
    "cad.design_review",
    "cad.diagnose",
    "cad.edit_parameter",
    "cad.author_brief",
    "cad.get_brief",
    "cad.set_reference_image",
    "cad.search_reference_image",

    # CAE setup / solve / results
    "cae.apply_setup_patch",
    "cae.generate_mesh",
    "cae.prepare_solver_run",
    "cae.generate_solver_input",
    "cae.run_solver",
    "cae.run_simulation_pipeline",
    "cae.extract_solver_results",
    "cae.extract_field_regions",
    "cae.mesh_convergence",
    "postprocess.refresh_cae_summary",

    # Materials + standards lookup
    "list_materials",
    "get_material_details",
    "compare_materials",
})


def compact_surface_enabled() -> bool:
    """Return True when the compact tool surface should be exposed."""
    return os.environ.get("AIENG_MCP_COMPACT_SURFACE") == "1"


def is_essential_mcp_tool(tool_name: str) -> bool:
    """Return True if ``tool_name`` belongs to the compact surface."""
    return tool_name in ESSENTIAL_MCP_TOOLS
