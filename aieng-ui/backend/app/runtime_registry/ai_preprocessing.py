"""AI preprocessing runtime tool registrations.

Exposes the AI-driven FEA setup generator as a callable MCP/runtime tool so
that `cae.prepare_solver_run` can recommend a concrete recovery action when
CAE face references are stale after a CAD edit.
"""

from __future__ import annotations

import logging
from typing import Any

from ..legacy_app_symbols import sync_main_symbols

LOGGER = logging.getLogger("app.app_factory")


def register_ai_preprocessing_tools(rt: Any, active_settings: Any, app_context: Any, _schema: Any) -> dict[str, Any]:
    """Register ai_preprocessing runtime tools."""
    sync_main_symbols(globals())

    _resolve_api_key = app_context.resolve_api_key

    def _tool_ai_preprocessing_run(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Run AI-driven FEA preprocessing for a project."""
        from .. import ai_preprocessing as _ai_preprocessing

        project_id = str(inp.get("project_id") or "").strip()
        if not project_id:
            return {
                "status": "error",
                "code": "missing_project_id",
                "message": "project_id is required.",
            }

        task_description = str(inp.get("task_description") or "").strip()
        if not task_description:
            return {
                "status": "error",
                "code": "missing_task_description",
                "message": "task_description is required.",
            }

        payload: dict[str, Any] = {"task_description": task_description}

        # Forward optional hints so the agent can steer material/mesh.
        material_hint = str(inp.get("material_hint") or "").strip()
        if material_hint:
            payload["material_hint"] = material_hint
        mesh_hint = str(inp.get("mesh_hint") or "").strip()
        if mesh_hint:
            payload["mesh_hint"] = mesh_hint

        # Forward write toggles; default to mutating the package.
        payload["write_files"] = bool(inp.get("write_files", True))
        payload["write_brep_graph"] = bool(inp.get("write_brep_graph", True))
        payload["use_brep_graph"] = bool(inp.get("use_brep_graph", True))

        # Resolve API key from input, then persisted settings.
        api_key = _resolve_api_key(inp)
        if isinstance(api_key, str) and api_key:
            payload["api_key"] = api_key

        llm_config = inp.get("llm_config")
        if isinstance(llm_config, dict):
            payload["llm_config"] = llm_config

        try:
            result = _ai_preprocessing.run_ai_preprocessing(active_settings, project_id, payload)
        except Exception as exc:  # noqa: BLE001
            from ..logging_utils import log_exception

            log_exception(
                LOGGER,
                "ai_preprocessing.run_ai_preprocessing failed",
                subsystem="runtime.ai_preprocessing",
                context={"project_id": project_id},
            )
            return {
                "status": "error",
                "code": "ai_preprocessing_failed",
                "message": f"{type(exc).__name__}: {exc}",
            }

        # Preserve the existing result shape while adding a consistent status field.
        result["status"] = "ok"
        result["tool"] = "ai_preprocessing.run_ai_preprocessing"
        return result

    rt.register_tool(
        "ai_preprocessing.run_ai_preprocessing",
        _tool_ai_preprocessing_run,
        read_only=False,
        destructive=False,
        input_schema=_schema("ai_preprocessing.run_ai_preprocessing"),
        description=(
            "AI-driven FEA preprocessing setup generator. Reads geometry from the project's "
            ".aieng package, calls Claude to decide material, boundary conditions, loads, and "
            "mesh strategy, then writes simulation/setup.yaml and simulation/cae_mapping.json "
            "into the package atomically. Use this to recover from stale CAE topology references "
            "after a CAD edit, or to set up a first analysis. Pass a clear task_description "
            "describing the load case and supports, e.g. 'Bracket bolted at 4 corner holes, "
            "500 N downward load at the end face.'"
        ),
    )

    return {}
