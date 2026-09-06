"""Runtime tool registration helpers.

Extracted from app_factory.py to keep the factory focused on FastAPI route
assembly and reduce its responsibility for integration closure details.
"""

from __future__ import annotations

from typing import Any

from . import runtime
from .config import Settings


def register_engineering_template_tools(rt: Any, settings: Settings) -> None:
    """Register engineering_template.* runtime tools."""

    def _engineering_template_tool(operation: str) -> runtime.ToolHandler:
        def _handler(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
            from . import engineering_templates

            project_id = inp.get("project_id") or _ctx.get("project_id")
            template_id = inp.get("template_id")
            if not template_id:
                return {
                    "ok": False,
                    "tool": f"engineering_template.{operation}",
                    "status": "error",
                    "errors": [{
                        "code": "missing_template_id",
                        "message": "template_id is required.",
                    }],
                }
            payload: dict[str, Any] = {}
            if isinstance(inp.get("parameters"), dict):
                payload["parameters"] = inp["parameters"]
            if isinstance(inp.get("suggestions"), list):
                payload["suggestions"] = inp["suggestions"]
            if isinstance(inp.get("target_ids"), list):
                payload["target_ids"] = inp["target_ids"]
            if "overwrite_existing" in inp:
                payload["overwrite_existing"] = bool(inp["overwrite_existing"])
            if "approved" in inp:
                payload["approved"] = bool(inp["approved"])
            elif operation == "generate_cad_fixture":
                # Approval is granted by the runtime's approval gate, not
                # by the underlying module; pass it through so the module
                # does not re-prompt.
                payload["approved"] = True

            if operation == "preview":
                if not project_id:
                    return {
                        "ok": False,
                        "tool": "engineering_template.preview",
                        "status": "error",
                        "errors": [{"code": "missing_project_id", "message": "project_id is required."}],
                    }
                return engineering_templates.preview_template(
                    settings, str(project_id), str(template_id), payload,
                )
            if operation == "save_draft":
                if not project_id:
                    return {
                        "ok": False,
                        "tool": "engineering_template.save_draft",
                        "status": "error",
                        "errors": [{"code": "missing_project_id", "message": "project_id is required."}],
                    }
                return engineering_templates.save_template_draft(
                    settings, str(project_id), str(template_id), payload,
                )
            if operation == "adopt_targets":
                if not project_id:
                    return {
                        "ok": False,
                        "tool": "engineering_template.adopt_targets",
                        "status": "error",
                        "errors": [{"code": "missing_project_id", "message": "project_id is required."}],
                    }
                return engineering_templates.adopt_template_target_suggestions(
                    settings, str(project_id), str(template_id), payload,
                )
            if operation == "generate_cad_fixture":
                if not project_id:
                    return {
                        "ok": False,
                        "tool": "engineering_template.generate_cad_fixture",
                        "status": "error",
                        "errors": [{"code": "missing_project_id", "message": "project_id is required."}],
                    }
                return engineering_templates.generate_template_cad_fixture(
                    settings, str(project_id), str(template_id), payload,
                )
            return {
                "ok": False,
                "tool": f"engineering_template.{operation}",
                "status": "error",
                "errors": [{"code": "unknown_operation", "message": operation}],
            }

        return _handler

    # Every one of these reads `template_id` and refuses without it, plus four
    # optional payload fields — none of which appeared in the tool's advertised
    # interface, because all four registered with no `input_schema` and were
    # therefore served the permissive `{"type": "object"}` fallback. A handler
    # that refuses correctly is no help if the agent cannot see what to send.
    def _template_schema(*, targets: bool = False, overwrite: bool = False) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "project_id": {"type": "string"},
            "template_id": {
                "type": "string",
                "description": "The engineering template to apply. Required.",
            },
            "parameters": {
                "type": "object",
                "description": "Template parameter overrides.",
            },
            "suggestions": {
                "type": "array",
                "description": "Design-target suggestions carried with the template.",
            },
        }
        if targets:
            properties["target_ids"] = {
                "type": "array",
                "description": "Restrict the operation to these design-target ids.",
            }
        if overwrite:
            properties["overwrite_existing"] = {
                "type": "boolean",
                "description": (
                    "Replace design targets whose ids already exist "
                    "(default false — existing ids are preserved)."
                ),
            }
        return {
            "type": "object",
            "required": ["project_id", "template_id"],
            "properties": properties,
            "additionalProperties": True,
        }

    rt.register_tool(
        "engineering_template.preview",
        _engineering_template_tool("preview"),
        description=(
            "Read-only preview of a controlled engineering template (CAD script preview, "
            "FEA setup draft, design target suggestions). No package write."
        ),
        input_schema=_template_schema(),
    )
    rt.register_tool(
        "engineering_template.save_draft",
        _engineering_template_tool("save_draft"),
        requires_approval=True,
        description=(
            "Approval-gated write of the four engineering template draft artifacts "
            "(manifest, CAD script preview, FEA setup, design target suggestions) into "
            "the .aieng package. Never overwrites task/design_targets.yaml."
        ),
        input_schema=_template_schema(),
    )
    rt.register_tool(
        "engineering_template.adopt_targets",
        _engineering_template_tool("adopt_targets"),
        requires_approval=True,
        description=(
            "Approval-gated merge of template design-target suggestions into "
            "task/design_targets.yaml. Existing target IDs are preserved unless "
            "overwrite_existing=true is supplied."
        ),
        input_schema=_template_schema(targets=True, overwrite=True),
    )
    rt.register_tool(
        "engineering_template.generate_cad_fixture",
        _engineering_template_tool("generate_cad_fixture"),
        requires_approval=True,
        description=(
            "Approval-gated deterministic CAD fixture write. Produces "
            "geometry/template_cad_fixture.json plus the standard stale revalidation "
            "marker; never runs Gmsh/CalculiX."
        ),
        input_schema=_template_schema(),
    )
