from __future__ import annotations

import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app import aieng_bridge


def sync_main_symbols() -> None:
    """Refresh globals from app.main before executing legacy-compatible helpers."""
    from app import main as api

    local_helpers = {
        "sync_main_symbols",
        "runtime_status",
        "compact_chat_output",
        "find_patch_json",
        "build_chat_plan",
        "import_aieng_file",
        "validate_aieng_file",
        "convert_asset",
        "recent_logs",
        "package_summary",
        "mcp_check",
        "parse_patch",
        "prepare_patch_execution",
        "execute_chat_step",
        "chat_orchestrator",
        "_resolve_frd_in_package",
        "_extract_frd_field_data",
    }
    updates: dict[str, Any] = {}
    for name, value in vars(api).items():
        if name.startswith("__") and name.endswith("__"):
            continue
        if name in local_helpers:
            is_main_wrapper = (
                callable(value)
                and getattr(value, "__module__", None) == "app.main"
                and getattr(value, "__name__", None) == name
            )
            if is_main_wrapper:
                continue
        updates[name] = value
    globals().update(updates)


def runtime_status(settings: Settings) -> dict[str, Any]:
    return runtime_config_snapshot(settings)


def compact_chat_output(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    if tool == "project.summary":
        return {
            "status": "ok",
            "member_count": result.get("package", {}).get("member_count"),
            "feature_count": result.get("derived", {}).get("feature_graph", {}).get("count"),
            "topology_count": result.get("derived", {}).get("topology", {}).get("count"),
            "validation_ok": result.get("validation", {}).get("report_ok"),
            "viewer_url": result.get("viewer_url"),
        }
    if tool == "aieng.import":
        return {
            "status": result.get("status"),
            "aieng_file": result.get("aieng_file"),
            "topology_backend": result.get("topology_backend"),
            "generated_resources": result.get("generated_resources", []),
            "validation_ok": result.get("validation", {}).get("ok"),
        }
    if tool == "viewer.convert":
        return {
            "status": result.get("status"),
            "asset_format": result.get("asset_format"),
            "viewer_url": result.get("viewer_url"),
        }
    if tool == "aieng.validate":
        return {
            "ok": result.get("ok"),
            "counts": result.get("counts"),
        }
    if tool == "mcp.check":
        guard = result.get("guard", {})
        return {
            "allowed": guard.get("allowed"),
            "mode": guard.get("mode"),
            "warnings": guard.get("warnings"),
            "reasons": guard.get("reasons"),
        }
    if tool == "mcp.parse_patch":
        return {
            "supported_operation_count": result.get("supported_operation_count"),
            "unsupported_operation_count": result.get("unsupported_operation_count"),
            "warnings": result.get("plan", {}).get("warnings", []),
        }
    if tool == "mcp.prepare_execution":
        return {
            "status": result.get("status"),
            "preflight_status": result.get("preflight", {}).get("status"),
            "step_count": len(result.get("preflight", {}).get("steps", [])),
            "warnings": result.get("preflight", {}).get("warnings", []),
            "errors": result.get("preflight", {}).get("errors", []),
        }
    return result


def find_patch_json(explicit_patch: Any, message: str) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(explicit_patch, dict):
        return explicit_patch, None
    if isinstance(explicit_patch, str) and explicit_patch.strip():
        try:
            return json.loads(explicit_patch), None
        except json.JSONDecodeError as exc:
            return None, f"patch_json is not valid JSON: {exc}"
    block_match = re.search(r"```json\\s*(\\{.*?\\})\\s*```", message, flags=re.DOTALL)
    if block_match:
        try:
            return json.loads(block_match.group(1)), None
        except json.JSONDecodeError as exc:
            return None, f"embedded patch JSON is invalid: {exc}"
    return None, None


def build_chat_plan(project: dict[str, Any], message: str, patch_json: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text = message.lower()
    wants_summary = (
        not message.strip()
        or any(token in text for token in ["summary", "manifest", "feature", "topology", "semantic", "package"])
    )
    wants_import = "aieng" in text or ("import" in text and "step" in text) or ("package" in text and not project.get("aieng_file"))
    wants_convert = any(token in text for token in ["preview", "viewer", "glb", "stl", "convert", "render", "view"])
    wants_validate = any(token in text for token in ["validate", "validation"])
    wants_whitelist = any(token in text for token in ["whitelist", "guard", "safe", "allowed", "policy", "check"])
    wants_patch = patch_json is not None or "patch" in text
    wants_prepare = wants_patch and any(token in text for token in ["prepare", "execute", "apply", "audit", "run", "preflight"])

    steps: list[dict[str, Any]] = []
    if wants_summary:
        steps.append(
            {
                "id": "summary",
                "title": "Refresh project summary",
                "tool": "project.summary",
                "status": "planned",
                "safe": True,
                "reason": "Load the latest package, topology, validation, and viewer state.",
            }
        )
    if wants_import and not project.get("aieng_file"):
        steps.append(
            {
                "id": "import",
                "title": "Import STEP into .aieng",
                "tool": "aieng.import",
                "status": "planned",
                "safe": True,
                "reason": "Create a semantic engineering package from the current STEP source.",
            }
        )
    if wants_convert:
        steps.append(
            {
                "id": "convert",
                "title": "Build web preview asset",
                "tool": "viewer.convert",
                "status": "planned",
                "safe": True,
                "reason": "Generate an inspectable web asset, preferring GLB with fallback when needed.",
            }
        )
    if wants_validate:
        steps.append(
            {
                "id": "validate",
                "title": "Validate current package",
                "tool": "aieng.validate",
                "status": "planned",
                "safe": True,
                "reason": "Run .aieng package validation and collect pass/warn/fail messages.",
            }
        )
    if wants_whitelist or wants_prepare:
        operation = "cad_set_parameter" if wants_patch else "cad_export_step"
        target_feature_id = None
        if patch_json:
            operations = patch_json.get("operations", [])
            if isinstance(operations, list) and operations:
                first = operations[0]
                if isinstance(first, dict):
                    target_feature_id = first.get("target_feature_id") or first.get("feature_id")
        steps.append(
            {
                "id": "mcp-check",
                "title": "Check MCP guardrails",
                "tool": "mcp.check",
                "status": "planned",
                "safe": True,
                "inputs": {
                    "operation": operation,
                    "target_feature_id": target_feature_id,
                    "is_modification": wants_patch,
                },
                "reason": "Inspect whitelist, package context, and protected-region guard behavior.",
            }
        )
    if wants_patch:
        steps.append(
            {
                "id": "parse-patch",
                "title": "Parse patch proposal",
                "tool": "mcp.parse_patch",
                "status": "planned",
                "safe": True,
                "reason": "Validate supported and unsupported patch operations without executing them.",
            }
        )
    if wants_prepare:
        steps.append(
            {
                "id": "prepare-execution",
                "title": "Prepare patch execution",
                "tool": "mcp.prepare_execution",
                "status": "planned",
                "safe": True,
                "reason": "Run a dry-run preflight for auditable execution readiness.",
            }
        )
    if not steps:
        steps.append(
            {
                "id": "summary",
                "title": "Refresh project summary",
                "tool": "project.summary",
                "status": "planned",
                "safe": True,
                "reason": "No explicit action detected, so load the current project state.",
            }
        )
    return steps, {
        "wants_summary": wants_summary,
        "wants_import": wants_import,
        "wants_convert": wants_convert,
        "wants_validate": wants_validate,
        "wants_whitelist": wants_whitelist,
        "wants_patch": wants_patch,
        "wants_prepare": wants_prepare,
    }


def import_aieng_file(settings: Settings, project_id: str) -> dict[str, Any]:
    project = get_project(settings, project_id)
    source = ensure_step_source(settings, project_id, project)
    out_path = project_dir(settings, project_id) / "packages" / f"{source.stem}{AIENG_EXT}"
    runtime_config = resolve_runtime_config(settings)
    import_result = aieng_bridge.import_step_to_aieng(
        source,
        out_path,
        aieng_root=settings.aieng_root,
        overwrite=True,
    )
    enrich_result = aieng_bridge.enrich_imported_package(
        out_path,
        aieng_root=settings.aieng_root,
        topology_backend=runtime_config["topology_backend"],
    )
    validation_result = aieng_bridge.validate_package(out_path, aieng_root=settings.aieng_root)
    project["aieng_file"] = project_relpath(settings, project_id, out_path)
    project["last_validation_ok"] = validation_result.get("ok")
    project["status"] = "validated" if validation_result.get("ok") else "validation_failed"
    validation_error = None if validation_result.get("ok") else "package validation reported failures"
    enrich_warnings = enrich_result.get("warnings") or []
    project["last_error"] = validation_error or (str(enrich_warnings[0]) if enrich_warnings else None)
    save_project(settings, project)
    return {
        "status": import_result["status"],
        "aieng_file": project["aieng_file"],
        "package_size": enrich_result.get("package_size", import_result.get("package_size")),
        "topology_backend": enrich_result.get("topology_backend"),
        "generated_resources": enrich_result.get("generated_resources", []),
        "warnings": enrich_result.get("warnings", []),
        "validation": validation_result,
    }


def validate_aieng_file(settings: Settings, project_id: str) -> dict[str, Any]:
    project = get_project(settings, project_id)
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if package_path is None or not package_path.exists():
        raise HTTPException(status_code=400, detail=".aieng package not found")
    result = aieng_bridge.validate_package(package_path, aieng_root=settings.aieng_root)
    project["last_validation_ok"] = result.get("ok")
    project["status"] = "validated" if result.get("ok") else "validation_failed"
    project["last_error"] = None if result.get("ok") else "package validation reported failures"
    save_project(settings, project)
    return result


def _step_to_stl_via_build123d(step_path: Path, stl_path: Path) -> dict[str, Any]:
    """Read a STEP file with build123d and export it to STL.

    Handles both pre-0.9 and 0.9+ build123d APIs so the fallback works across
    installed versions. Returns a dict shaped like the provider response so
    ``convert_asset`` can consume it uniformly.
    """
    try:
        import build123d as b123d
    except Exception as exc:
        return {"status": "unavailable", "code": "build123d_missing", "message": f"build123d not installed: {exc}"}

    try:
        # build123d 0.9+ moved import_step to a module-level free function.
        fn = getattr(b123d, "import_step", None)
        if fn is None:
            # Pre-0.9: class method on Shape
            fn = getattr(getattr(b123d, "Shape", None), "import_step", None)
        if fn is None:
            return {"status": "error", "code": "import_step_missing", "message": "build123d has no import_step API"}
        shape = fn(str(step_path))

        stl_path.parent.mkdir(parents=True, exist_ok=True)

        # STL export: prefer module-level free function (0.9+), fall back to instance method.
        export_fn = getattr(b123d, "export_stl", None)
        if export_fn is not None:
            export_fn(shape, str(stl_path))
        else:
            method = getattr(shape, "export_stl", None)
            if method is not None:
                method(str(stl_path))
            else:
                return {"status": "error", "code": "export_stl_missing", "message": "build123d has no export_stl API"}

        return {
            "status": "ok",
            "provider": "build123d",
            "object_count": 1,
            "stl_path": str(stl_path),
        }
    except Exception as exc:
        return {"status": "error", "code": "build123d_step_export_failed", "message": f"{type(exc).__name__}: {exc}"}


def _publish_package_preview_asset(
    settings: Settings,
    project_id: str,
    project: dict[str, Any],
    metadata_path: Path | None = None,
) -> dict[str, Any] | None:
    """Publish an embedded package preview to the viewer directory if present."""
    import zipfile

    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if package_path is None or not package_path.exists():
        return None

    candidates = (
        ("geometry/preview.glb", "glb"),
        ("preview.glb", "glb"),
        ("viewer/model.glb", "glb"),
        ("geometry/preview.stl", "stl"),
        ("preview.stl", "stl"),
        ("viewer/model.stl", "stl"),
    )
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            names = set(archive.namelist())
            selected = next(((member, fmt) for member, fmt in candidates if member in names), None)
            if selected is None:
                return None
            member, asset_format = selected
            data = archive.read(member)
    except Exception as exc:
        return {
            "status": "error",
            "asset_path": None,
            "asset_format": None,
            "viewer_url": None,
            "message": f"Failed to read embedded preview asset: {type(exc).__name__}: {exc}",
        }

    viewer_root = project_dir(settings, project_id) / "viewer"
    viewer_root.mkdir(parents=True, exist_ok=True)
    asset_path = viewer_root / f"model.{asset_format}"
    asset_path.write_bytes(data)
    rel_asset = project_relpath(settings, project_id, asset_path)

    source_step = resolve_project_path(settings, project_id, project.get("source_step"))
    preview_info = {
        "source_step": project_relpath(settings, project_id, source_step) if source_step and source_step.exists() else None,
        "selected_asset": rel_asset,
        "selected_format": asset_format,
        "preview": {
            "status": "ok",
            "provider": "package_preview",
            "member": member,
            "package": project_relpath(settings, project_id, package_path),
        },
        "glb_attempt": None,
    }
    if metadata_path is not None:
        write_json(metadata_path, preview_info)

    project["web_asset"] = rel_asset
    project["web_asset_format"] = asset_format
    project["preview_info"] = preview_info
    project["status"] = f"viewer_ready_{asset_format}"
    project["last_error"] = None
    save_project(settings, project)
    return {
        "status": "ok",
        "asset_path": rel_asset,
        "asset_format": asset_format,
        "viewer_url": f"/assets/projects/{project_id}/{rel_asset}",
        "preview_info": preview_info,
        "source": "package_preview",
    }


def convert_asset(settings: Settings, project_id: str) -> dict[str, Any]:
    project = get_project(settings, project_id)
    viewer_root = project_dir(settings, project_id) / "viewer"
    stl_path = viewer_root / "model.stl"
    glb_path = viewer_root / "model.glb"
    metadata_path = viewer_root / "preview.json"

    embedded_preview = _publish_package_preview_asset(settings, project_id, project, metadata_path)
    if embedded_preview is not None:
        return embedded_preview

    source = ensure_step_source(settings, project_id, project)

    _, _, provider = resolve_provider_bundle(settings)
    preview_result = provider.export_step_preview_to_stl(step_path=source, stl_path=stl_path)
    preview_status = str(preview_result.get("status") or "error")

    # Fallback to build123d/OCP when the CAD provider is unavailable (e.g. FreeCAD
    # is not installed). build123d can read STEP and export STL directly.
    if preview_status == "unavailable":
        fallback = _step_to_stl_via_build123d(source, stl_path)
        if fallback.get("status") == "ok" and stl_path.exists():
            preview_result = fallback
            preview_status = "ok"
        else:
            preview_result = fallback
            preview_status = str(fallback.get("status") or "unavailable")

    if preview_status != "ok" or not stl_path.exists():
        failure_status = "unavailable" if preview_status == "unavailable" else "error"
        preview_info = {
            "source_step": project_relpath(settings, project_id, source),
            "selected_asset": None,
            "selected_format": None,
            "preview": preview_result,
            "glb_attempt": None,
        }
        write_json(metadata_path, preview_info)
        project["web_asset"] = None
        project["web_asset_format"] = None
        project["preview_info"] = preview_info
        project["status"] = "preview_unavailable" if failure_status == "unavailable" else "preview_failed"
        project["last_error"] = str(preview_result.get("message") or "preview generation failed")
        save_project(settings, project)
        return {
            "status": failure_status,
            "asset_path": None,
            "asset_format": None,
            "viewer_url": None,
            "preview_info": preview_info,
            "message": project["last_error"],
        }
    glb_attempt = convert_stl_to_glb(stl_path, glb_path)

    if glb_attempt.get("ok"):
        asset_path = glb_path
        asset_format = "glb"
    else:
        asset_path = stl_path
        asset_format = "stl"

    preview_info = {
        "source_step": project_relpath(settings, project_id, source),
        "selected_asset": project_relpath(settings, project_id, asset_path),
        "selected_format": asset_format,
        "preview": preview_result,
        "glb_attempt": glb_attempt,
    }
    write_json(metadata_path, preview_info)

    project["web_asset"] = project_relpath(settings, project_id, asset_path)
    project["web_asset_format"] = asset_format
    project["preview_info"] = preview_info
    project["status"] = f"viewer_ready_{asset_format}"
    project["last_error"] = None if asset_format == "glb" else glb_attempt.get("error")
    save_project(settings, project)
    return {
        "status": "ok",
        "asset_path": project["web_asset"],
        "asset_format": asset_format,
        "viewer_url": f"/assets/projects/{project_id}/{project['web_asset']}",
        "preview_info": preview_info,
    }


def recent_logs(settings: Settings, project_id: str, limit: int = 8) -> list[dict[str, Any]]:
    logs_root = project_dir(settings, project_id) / "logs"
    items: list[dict[str, Any]] = []
    log_entries = [(path.stat(), path) for path in logs_root.glob("*.json")]
    for stat_result, path in sorted(log_entries, key=lambda item: item[0].st_mtime, reverse=True)[:limit]:
        items.append(
            {
                "name": path.name,
                "path": project_relpath(settings, project_id, path),
                "url": f"/assets/projects/{project_id}/{project_relpath(settings, project_id, path)}",
                "size": stat_result.st_size,
                "updated_at": datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return items


def _file_exists_and_size(path: Path | None) -> tuple[bool, int | None]:
    if path is None:
        return False, None
    try:
        stat_result = path.stat()
    except OSError:
        return False, None
    return True, stat_result.st_size


def package_summary(settings: Settings, project_id: str) -> dict[str, Any]:
    project = get_project(settings, project_id)
    source_path = resolve_project_path(settings, project_id, project.get("source_step"))
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    viewer_path = resolve_project_path(settings, project_id, project.get("web_asset"))
    viewer_metadata_path = project_dir(settings, project_id) / "viewer" / "preview.json"
    source_exists, source_size = _file_exists_and_size(source_path)
    package_exists, package_size = _file_exists_and_size(package_path)
    viewer_exists, viewer_size = _file_exists_and_size(viewer_path)

    summary: dict[str, Any] = {
        "project": project,
        "files": {
            "source_step": {
                "path": project.get("source_step"),
                "exists": source_exists,
                "size": source_size,
            },
            "aieng_file": {
                "path": project.get("aieng_file"),
                "exists": package_exists,
                "size": package_size,
            },
            "web_asset": {
                "path": project.get("web_asset"),
                "exists": viewer_exists,
                "size": viewer_size,
            },
        },
        "viewer": {
            "asset_format": project.get("web_asset_format"),
            "asset_path": project.get("web_asset"),
            "asset_exists": viewer_exists,
            "metadata": read_json(viewer_metadata_path, None),
        },
        "viewer_url": f"/assets/projects/{project_id}/{project['web_asset']}" if project.get("web_asset") else None,
        "package": {
            "path": project.get("aieng_file"),
            "member_count": 0,
        },
        "members": [],
        "manifest": None,
        "feature_graph": None,
        "topology": None,
        "interfaces": None,
        "constraints": None,
        "task_spec": None,
        "external_tool_requirements": None,
        "claim_map": None,
        "evidence_index": None,
        "tool_trace": None,
        "completeness_report": None,
        "evidence_report": None,
        "cae": {
            "present": False,
            "constraints_count": 0,
            "constraint_types": {},
            "materials_count": 0,
            "boundary_conditions_count": 0,
            "loads_count": 0,
            "evidence_count": 0,
            "result_evidence_count": 0,
            "results_available": False,
            "available_fields": [],
            "simulation_targets": [],
            "protected_regions": [],
            "materials": [],
            "boundary_conditions": [],
            "loads": [],
            "evidence": [],
            "mapping": None,
            "solver_status": {},
            "solver_fields": [],
        },
        "validation": {
            "report_ok": None,
            "messages": [],
            "counts": {},
            "status": None,
        },
        "ai_summary": None,
        "derived": {},
        "summary_error": None,
        "summary_mode": "none",
        "integration": runtime_status(settings),
        "recent_logs": recent_logs(settings, project_id),
    }
    if package_path and package_exists:
        try:
            _, _, provider = resolve_provider_bundle(settings)
            result = provider.package_summary_snapshot(package_path=package_path)
            if isinstance(result, dict) and result.get("status") == "unavailable":
                raise RuntimeError(str(result.get("message") or "package summary unavailable"))
            summary["members"] = result.get("members", [])
            summary["package"]["member_count"] = result.get("member_count", 0)
            summary["manifest"] = result.get("manifest")
            summary["feature_graph"] = result.get("feature_graph")
            summary["topology"] = result.get("topology")
            summary["interfaces"] = result.get("interfaces")
            summary["constraints"] = result.get("constraints")
            summary["task_spec"] = result.get("task_spec")
            summary["external_tool_requirements"] = result.get("external_tool_requirements")
            summary["claim_map"] = result.get("claim_map")
            summary["evidence_index"] = result.get("evidence_index")
            summary["tool_trace"] = result.get("tool_trace")
            summary["completeness_report"] = result.get("completeness_report")
            summary["evidence_report"] = result.get("evidence_report")
            summary["cae"] = result.get("cae") or summarize_cae_payload(
                constraints=result.get("constraints"),
                parsed_materials=result.get("parsed_materials"),
                parsed_boundary_conditions=result.get("parsed_boundary_conditions"),
                parsed_loads=result.get("parsed_loads"),
                cae_mapping=result.get("cae_mapping"),
                evidence_index=result.get("evidence_index"),
                validation_status=result.get("validation_status"),
            )
            summary["validation"] = {
                "report_ok": result.get("validation_report", {}).get("ok"),
                "messages": result.get("validation_report", {}).get("messages", []),
                "counts": result.get("validation_report", {}).get("counts", {}),
                "status": result.get("validation_status"),
            }
            summary["ai_summary"] = result.get("ai_summary")
            summary["derived"] = result.get("derived", {})
            summary["summary_mode"] = "bridge"
        except Exception as exc:
            summary["summary_error"] = f"{type(exc).__name__}: {exc}"
            try:
                fallback = package_summary_fallback(package_path)
            except Exception as fallback_exc:
                summary["validation"] = {
                    "report_ok": project.get("last_validation_ok"),
                    "messages": [
                        {
                            "level": "WARN",
                            "text": "package_summary failed and fallback package inspection was unavailable",
                        },
                        {"level": "WARN", "text": f"{type(fallback_exc).__name__}: {fallback_exc}"},
                    ],
                    "counts": {"WARN": 2},
                    "status": "degraded",
                }
                summary["summary_error"] = (
                    f"{summary['summary_error']} | fallback failed: {type(fallback_exc).__name__}: {fallback_exc}"
                )
                summary["summary_mode"] = "error_fallback"
            else:
                summary["members"] = fallback["members"]
                summary["package"]["member_count"] = fallback["member_count"]
                summary["manifest"] = fallback["manifest"]
                summary["feature_graph"] = fallback["feature_graph"]
                summary["topology"] = fallback["topology"]
                summary["interfaces"] = fallback["interfaces"]
                summary["constraints"] = fallback["constraints"]
                summary["task_spec"] = fallback["task_spec"]
                summary["external_tool_requirements"] = fallback["external_tool_requirements"]
                summary["claim_map"] = fallback["claim_map"]
                summary["evidence_index"] = fallback["evidence_index"]
                summary["tool_trace"] = fallback["tool_trace"]
                summary["completeness_report"] = fallback["completeness_report"]
                summary["evidence_report"] = fallback["evidence_report"]
                summary["cae"] = fallback["cae"]
                summary["ai_summary"] = fallback["ai_summary"]
                summary["derived"] = fallback["derived"]
                summary["validation"] = {
                    "report_ok": project.get("last_validation_ok"),
                    "messages": [
                        {
                            "level": "WARN",
                            "text": "package_summary degraded to zip fallback because optional package resources are missing",
                        },
                        *[
                            {"level": "WARN", "text": f"{member_name} missing"}
                            for member_name in fallback["warnings"]
                        ],
                    ],
                    "counts": {"WARN": len(fallback["warnings"]) + 1},
                    "status": "degraded",
                }
                summary["summary_mode"] = "zip_fallback"

    _cae = summary.get("cae")
    if isinstance(_cae, dict):
        # Default metadata for every selectable CAE result field. These are
        # overridden by real FRD extrema when a result file is present.
        _field_defaults: dict[str, dict[str, Any]] = {
            "von_mises": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa"},
            "stress": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa"},
            "sxx": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "syy": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "szz": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "sxy": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "sxz": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "syz": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "s1": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "s2": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "s3": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa"},
            "tresca": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa"},
            "max_shear": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa"},
            "disp_magnitude": {"min_value": 0.0, "max_value": 5.0, "unit": "mm"},
            "displacement": {"min_value": 0.0, "max_value": 5.0, "unit": "mm"},
            "ux": {"min_value": -5.0, "max_value": 5.0, "unit": "mm"},
            "uy": {"min_value": -5.0, "max_value": 5.0, "unit": "mm"},
            "uz": {"min_value": -5.0, "max_value": 5.0, "unit": "mm"},
            "safety_factor": {"min_value": 0.0, "max_value": 10.0, "unit": ""},
        }
        # Legacy aliases share the same FRD data as their canonical names.
        # Keep them in defaults for fallback metadata, but only extract each
        # physical field once to avoid redundant FRD parsing.
        _FIELD_NAME_ALIASES: dict[str, str] = {
            "stress": "von_mises",
            "displacement": "disp_magnitude",
        }

        def _canonical_field_name(name: str) -> str:
            return _FIELD_NAME_ALIASES.get(name, name)

        _SELECTABLE_FIELD_NAMES: tuple[str, ...] = tuple(
            name for name in _field_defaults if name not in _FIELD_NAME_ALIASES
        )

        # Check whether a real FRD exists so solver_fields can advertise the
        # correct format upfront.
        _has_frd = False
        if package_path and package_path.exists():
            _has_frd = _resolve_frd_in_package(package_path) is not None

        _available_fields = list(_cae.get("available_fields") or [])
        _real_field_cache: dict[str, dict[str, Any]] = {}
        if _has_frd and package_path and package_path.exists():
            for candidate in _SELECTABLE_FIELD_NAMES:
                try:
                    real_field = _extract_frd_field_data(package_path, candidate, settings.aieng_root)
                except Exception:
                    real_field = None
                if real_field is not None:
                    _real_field_cache[candidate] = real_field
                    if candidate not in _available_fields:
                        _available_fields.append(candidate)
        _cae["available_fields"] = _available_fields
        if _has_frd:
            _cae["present"] = True
            _cae["results_available"] = True

        _solver_fields: list[dict[str, Any]] = []
        for f in _available_fields:
            _meta = _field_defaults.get(f, {"min_value": 0.0, "max_value": 1.0, "unit": ""})
            _field_entry: dict[str, Any] = {
                "field_name": f,
                "descriptor_url": f"/api/projects/{project_id}/fields/{f}",
                **_meta,
                "format": "vertex_json" if _has_frd else "vertex_synthetic",
                "available": True,
            }
            # If FRD is present, try to fetch real extrema so the frontend
            # legend is accurate before the first descriptor fetch. Aliases
            # reuse the canonical field's cached extraction.
            if _has_frd:
                try:
                    canonical = _canonical_field_name(f)
                    _real = _real_field_cache.get(canonical)
                    if _real is None and package_path and package_path.exists():
                        _real = _extract_frd_field_data(package_path, canonical, settings.aieng_root)
                        if _real is not None:
                            _real_field_cache[canonical] = _real
                    if _real is not None:
                        _field_entry["min_value"] = _real["min_value"]
                        _field_entry["max_value"] = _real["max_value"]
                        _field_entry["unit"] = _real["unit"]
                except Exception:
                    pass
            _solver_fields.append(_field_entry)
        _cae["solver_fields"] = _solver_fields
        if package_path and package_path.exists():
            _artifact_detection = _detect_cae_artifacts(settings, package_path)
            if _artifact_detection is not None:
                _cae["artifact_detection"] = _artifact_detection
            _result_summary = _generate_cae_result_summary(settings, package_path)
            if _result_summary is not None:
                _cae["result_summary"] = _result_summary
            _preprocessing_summary = _generate_cae_preprocessing_summary(settings, package_path)
            if _preprocessing_summary is not None:
                _cae["preprocessing_summary"] = _preprocessing_summary
            _simulation_run_summary = _generate_cae_simulation_run_summary(settings, package_path)
            if _simulation_run_summary is not None:
                _cae["simulation_run_summary"] = _simulation_run_summary

    return summary


def mcp_check(settings: Settings, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = get_project(settings, project_id)
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    _, _, provider = resolve_provider_bundle(settings)
    result = provider.check_mcp_operation(
        package_path=str(package_path) if package_path and package_path.exists() else None,
        payload=payload,
        whitelisted_tools=TOOLS_ALLOWED,
    )
    result["project_id"] = project_id
    result["package_path"] = project.get("aieng_file")
    return result


def parse_patch(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    _, _, provider = resolve_provider_bundle(settings)
    return provider.parse_patch_proposal(patch_json=payload.get("patch_json") or {})


def prepare_patch_execution(settings: Settings, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = get_project(settings, project_id)
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    _, _, provider = resolve_provider_bundle(settings)
    result = provider.prepare_patch_preflight(
        package_path=str(package_path) if package_path and package_path.exists() else None,
        payload=payload,
    )
    result["project_id"] = project_id
    return result


def execute_chat_step(
    settings: Settings,
    project_id: str,
    step: dict[str, Any],
    patch_json: dict[str, Any] | None,
) -> dict[str, Any]:
    tool = step["tool"]
    if tool == "project.summary":
        return package_summary(settings, project_id)
    if tool == "aieng.import":
        return import_aieng_file(settings, project_id)
    if tool == "viewer.convert":
        return convert_asset(settings, project_id)
    if tool == "aieng.validate":
        return validate_aieng_file(settings, project_id)
    if tool == "mcp.check":
        return mcp_check(settings, project_id, step.get("inputs", {}))
    if tool == "mcp.parse_patch":
        return parse_patch(settings, {"patch_json": patch_json or {}})
    if tool == "mcp.prepare_execution":
        return prepare_patch_execution(settings, project_id, {"patch_json": patch_json or {}})
    return {"status": "unsupported"}


def chat_orchestrator(settings: Settings, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = get_project(settings, project_id)
    message = str(payload.get("message") or "").strip()
    execute = bool(payload.get("execute", False))
    patch_json, patch_error = find_patch_json(payload.get("patch_json"), message)
    plan, intent = build_chat_plan(project, message, patch_json)
    errors: list[str] = []

    if patch_error:
        errors.append(patch_error)

    if execute and not patch_error:
        for step in plan:
            try:
                output = execute_chat_step(settings, project_id, step, patch_json)
                step["output"] = compact_chat_output(step["tool"], output)
                step["status"] = "done"
            except Exception as exc:
                step["status"] = "failed"
                step["error"] = f"{type(exc).__name__}: {exc}"
                errors.append(step["error"])
                break

    executed_steps = [step for step in plan if step.get("status") == "done"]
    if execute and not errors:
        reply = f"Executed {len(executed_steps)} safe step(s) and refreshed the project state."
    elif execute and errors:
        reply = f"Stopped after {len(executed_steps)} safe step(s) because a later step failed."
    else:
        reply = f"Built a guarded plan with {len(plan)} step(s)."

    audit_payload = {
        "kind": "chat",
        "project_id": project_id,
        "message": message,
        "intent": intent,
        "execute": execute,
        "patch_json": patch_json,
        "plan": plan,
        "errors": errors,
        "created_at": now_iso(),
    }
    audit_meta = write_audit_log(settings, project_id, "chat", audit_payload)
    project["last_chat_audit"] = audit_meta["audit_path"]
    save_project(settings, project)
    return {
        "reply": reply,
        "intent": intent,
        "plan": plan,
        "executed": execute,
        "errors": errors,
        "audit_id": audit_meta["audit_id"],
        "audit_log_url": audit_meta["audit_url"],
        "patch_json": patch_json,
    }


def _resolve_frd_in_package(package_path: Path) -> str | None:
    """Find the newest result.frd inside a .aieng package."""
    if not package_path.exists():
        return None
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            candidates = [
                name for name in zf.namelist()
                if name.endswith("/outputs/result.frd")
            ]
            if not candidates:
                return None
            # Pick the lexicographically last run (run_002 > run_001)
            return sorted(candidates)[-1]
    except zipfile.BadZipFile:
        return None


def _resolve_material_yield(package_path: Path) -> float | None:
    """Best-effort material yield strength (MPa) for the safety-factor field.

    Reads the CAE setup material: an explicit ``yield_strength_mpa`` / ``yield_mpa``
    wins; otherwise the material name is looked up against the known-materials table.
    Returns None if it cannot be determined (the field is then reported unavailable).
    """
    import yaml

    from ..post_processing import _lookup_yield_strength

    setup: dict[str, Any] | None = None
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            for member in ("simulation/setup.yaml", "simulation/setup.yml", "cae/setup.yaml"):
                try:
                    setup = yaml.safe_load(zf.read(member).decode("utf-8"))
                    break
                except KeyError:
                    continue
                except Exception:
                    continue
    except Exception:
        return None
    if not isinstance(setup, dict):
        return None

    # Find a material name + optional explicit yield.
    name: str | None = setup.get("material_name")
    mat_obj: Any = None
    materials = setup.get("materials")
    if isinstance(materials, dict) and materials:
        first_name, first_obj = next(iter(materials.items()))
        name = name or str(first_name)
        mat_obj = first_obj
    elif isinstance(setup.get("material"), dict):
        mat_obj = setup["material"]
        name = name or mat_obj.get("name")

    if isinstance(mat_obj, dict):
        for key in ("yield_strength_mpa", "yield_mpa", "yield_strength"):
            val = mat_obj.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                return float(val)

    return _lookup_yield_strength(name) if name else None


def _frd_step_index_from_load_case_id(load_case_id: str | None, max_steps: int) -> int:
    """Map a load-case id to a 0-based FRD step index.

    Supports conventions like ``load_case_001`` (→ 0), ``mode_1`` (→ 0),
    ``mode_01`` (→ 0), and ``buckling_001`` (→ 0).  Unknown or missing ids
    default to step 0.  Out-of-range indices are clamped to the last step.
    """
    if not load_case_id:
        return 0
    match = re.search(r"(\d+)\s*$", load_case_id)
    if not match:
        return 0
    try:
        index = int(match.group(1)) - 1
    except ValueError:
        return 0
    if max_steps <= 0:
        return 0
    return max(0, min(index, max_steps - 1))


def _extract_frd_field_data(
    package_path: Path,
    field_name: str,
    aieng_root: Path,
    load_case_id: str | None = None,
) -> dict[str, Any] | None:
    """Extract per-node scalar values and coordinates from an FRD inside a package.

    Args:
        package_path: Path to the .aieng package.
        field_name: CAE field name to extract.
        aieng_root: Root of the aieng repository checkout.
        load_case_id: Optional load-case / mode identifier used to select the
            corresponding FRD step. Defaults to the first step.

    Returns a dict with ``values``, ``node_coords``, ``min_value``,
    ``max_value``, ``unit``, ``warnings`` — or ``None`` if no usable FRD.
    """
    frd_entry = _resolve_frd_in_package(package_path)
    if frd_entry is None:
        return None

    # Extract FRD to temp file for parsing
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            frd_bytes = zf.read(str(frd_entry))
    except (KeyError, zipfile.BadZipFile):
        return None

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".frd", delete=False) as fh:
        fh.write(frd_bytes)
        temp_frd = Path(fh.name)

    try:
        aieng_src = aieng_root / "src"
        injected = False
        if str(aieng_src) not in sys.path:
            sys.path.insert(0, str(aieng_src))
            injected = True

        from aieng.simulation.frd_result_extractor import parse_frd_steps
        from aieng.simulation.field_region_extractor import _extract_node_coords_from_frd
        from aieng.simulation.field_derivation import (
            FIELD_CATALOG,
            canonical_field_name,
            derive_displacement_value,
            derive_stress_value,
        )

        fields = parse_frd_steps(temp_frd)
        coords = _extract_node_coords_from_frd(temp_frd)
        if not coords:
            return None

        warnings: list[str] = []
        values: dict[int, float] = {}

        name = canonical_field_name(field_name)
        meta = FIELD_CATALOG.get(name)
        if meta is None:
            warnings.append(f"Field '{field_name}' is not supported for FRD extraction.")
            return None
        unit = meta["unit"]

        # CalculiX names the stress field "STRESS"; some exports use "S".
        stress_steps = fields.get("S") or fields.get("STRESS") or []

        step_index = _frd_step_index_from_load_case_id(
            load_case_id,
            max(len(stress_steps), len(fields.get("DISP", [])), 1),
        )

        if meta["source"] == "stress":
            s_steps = stress_steps
            if not s_steps:
                warnings.append("S (stress tensor) field not found in FRD.")
                return None
            s_field = s_steps[step_index] if step_index < len(s_steps) else s_steps[-1]
            yield_strength: float | None = None
            if name == "safety_factor":
                yield_strength = _resolve_material_yield(package_path)
                if yield_strength is None:
                    warnings.append(
                        "Material yield strength unknown — safety-factor field unavailable."
                    )
                    return None
            for nid, vals in s_field["node_data"].items():
                if nid not in coords:
                    continue
                v = derive_stress_value(name, tuple(vals[:6]), yield_strength=yield_strength)
                if v is not None:
                    values[nid] = v
        else:  # displacement
            disp_steps = fields.get("DISP", [])
            if not disp_steps:
                warnings.append("DISP field not found in FRD.")
                return None
            disp = disp_steps[step_index] if step_index < len(disp_steps) else disp_steps[-1]
            components = disp["components"]
            idx = {c: next((i for i, cc in enumerate(components) if cc == c), None) for c in ("D1", "D2", "D3")}

            def _comp(vals: list[Any], key: str) -> float | None:
                i = idx[key]
                if i is not None and i < len(vals) and vals[i] is not None:
                    return float(vals[i])
                return None

            vectors: dict[int, tuple[float, float, float]] = {}
            for nid, vals in disp["node_data"].items():
                if nid not in coords:
                    continue
                d1 = _comp(vals, "D1")
                d2 = _comp(vals, "D2")
                d3 = _comp(vals, "D3")
                v = derive_displacement_value(name, d1, d2, d3)
                if v is not None:
                    values[nid] = v
                if d1 is not None and d2 is not None and d3 is not None:
                    vectors[nid] = (d1, d2, d3)

        if not values:
            warnings.append(f"No valid '{field_name}' values could be extracted from FRD.")
            return None

        # Sort by node_id for stable ordering
        sorted_ids = sorted(values.keys())
        value_list = [values[nid] for nid in sorted_ids]
        coord_list = [list(coords[nid]) for nid in sorted_ids]
        min_val = min(value_list)
        max_val = max(value_list)

        result: dict[str, Any] = {
            "values": value_list,
            "node_coords": coord_list,
            "min_value": min_val,
            "max_value": max_val,
            "unit": unit,
            "warnings": warnings,
        }
        if meta["source"] == "displacement":
            vector_list = [
                list(vectors.get(nid, (0.0, 0.0, 0.0))) for nid in sorted_ids
            ]
            result["vectors"] = vector_list
        return result
    except Exception:
        return None
    finally:
        try:
            temp_frd.unlink(missing_ok=True)
        except OSError:
            pass
        if injected:
            try:
                sys.path.remove(str(aieng_src))
            except ValueError:
                pass
