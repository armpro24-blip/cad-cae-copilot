"""aieng runtime tool registrations.

Extracted from runtime_tool_registry.py to keep domain logic focused.
"""

from __future__ import annotations

import logging
from typing import Any

from ..legacy_app_symbols import sync_main_symbols

LOGGER = logging.getLogger("app.app_factory")


def register_aieng_tools(rt: Any, active_settings: Any, app_context: Any, _schema: Any) -> dict[str, Any]:
    """Register aieng runtime tools."""
    sync_main_symbols(globals())
    _delete_project_everywhere = app_context.delete_project_everywhere
    _load_project_feature_parameters = app_context.load_project_feature_parameters

    def _tool_inspect_package(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for aieng.inspect_package")
        return package_summary(active_settings, pid)

    def _tool_agent_context(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import agent_context

        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for aieng.agent_context")
        return agent_context.build_agent_context(active_settings, str(pid))

    def _tool_refresh_semantics(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for aieng.refresh_semantics")
        return validate_aieng_file(active_settings, pid)

    def _tool_generate_preview(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for aieng.generate_preview")
        # convert_asset first publishes embedded package previews (GLB/STL), then
        # falls back to STEP conversion when the package has no viewer asset.
        return convert_asset(active_settings, pid)

    def _tool_read_audit_log(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        logs = recent_logs(active_settings, pid) if pid else []
        return {"project_id": pid, "recent_logs": logs}

    def _tool_recent_activity(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Recent CAD build/activity events for a project (#227).

        Headless build feedback: a CLI/IDE agent sees build progress + iteration
        errors without the web viewer's SSE connection. Reads the backend's
        bounded in-memory ring buffer (current process; live, not persisted).
        """
        from .. import agent_activity

        pid = inp.get("project_id")
        try:
            limit = int(inp.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        raw_since = inp.get("since_ts")
        try:
            since_ts = float(raw_since) if raw_since is not None else None
        except (TypeError, ValueError):
            since_ts = None
        events = agent_activity.recent(pid, limit=limit, since_ts=since_ts)
        latest_ts = events[-1].get("ts") if events else since_ts
        return {
            "project_id": pid,
            "events": events,
            "count": len(events),
            "latest_ts": latest_ts,  # pass back as since_ts to poll for newer events
            "note": (
                "Recent in-memory build/activity events from the current backend "
                "process (bounded ring buffer) — live feedback, not a persisted history."
            ),
        }

    def _tool_refresh_cae_summary(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")

        if not package_path and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path = str(pkg)

        if not package_path:
            return {
                "status": "error",
                "code": "missing_cae_summary_package_path",
                "message": (
                    "No package path provided and no project_id could be resolved. "
                    "Pass packagePath or a project_id with an .aieng file."
                ),
            }

        if not _Path(package_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path}",
            }

        overwrite = bool(inp.get("overwrite", True))
        result = aieng_bridge.refresh_cae_result_summary(
            package_path,
            aieng_root=active_settings.aieng_root,
            overwrite=overwrite,
        )

        if result.get("status") == "ok":
            try:
                _pkg = _Path(package_path)
                _written = [a["path"] for a in result.get("artifacts", [])]
                _evidence = [
                    a["path"] for a in result.get("artifacts", [])
                    if a.get("kind") in ("cae_result_summary", "evidence_index", "field")
                ]
                _append_audit_event_to_package(
                    _pkg,
                    _build_audit_event(
                        tool="postprocess.refresh_cae_summary",
                        event_type="cae_summary_refreshed",
                        status="completed",
                        artifacts_written=_written,
                        evidence_created=_evidence,
                        state_changes={},
                        geometry_revision=None,
                        revalidation_status=None,
                    ),
                )
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to write computed-metrics audit artifact.",
                    subsystem="app_factory.audit.computed_metrics",
                    context={"project_id": project_id},
                )

        return result

    def _tool_generate_computed_metrics(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import computed_metrics as _cm
        from pathlib import Path as _Path

        project_id: str | None = inp.get("project_id")
        input_path: str | None = inp.get("inputPath") or inp.get("input_path")

        if not project_id:
            return {"status": "error", "code": "missing_project_id", "message": "project_id is required."}
        if not input_path:
            return {"status": "error", "code": "missing_input_path", "message": "inputPath is required."}

        p = _Path(input_path)
        if not p.exists():
            return {"status": "error", "code": "file_not_found", "message": f"Input file not found: {input_path}"}

        text = p.read_text(encoding="utf-8")
        fmt = "csv" if input_path.lower().endswith(".csv") else "json"
        payload: dict[str, Any] = {"format": fmt, "text": text}
        if inp.get("loadCaseId"):
            payload["load_case_id"] = inp["loadCaseId"]
        if inp.get("software"):
            payload["software"] = inp["software"]

        try:
            result = _cm.save_computed_metrics(active_settings, project_id, payload)
        except Exception as exc:
            return {"status": "error", "code": "save_failed", "message": str(exc)}

        return {**result, "status": "ok"}

    def _tool_mcp_check(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for mcp.check")
        return mcp_check(active_settings, pid, inp)

    def _tool_mcp_parse_patch(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        patch_json = inp.get("patch_json")
        if not isinstance(patch_json, dict):
            raise ValueError("patch_json is required for mcp.parse_patch")
        return parse_patch(active_settings, {"patch_json": patch_json})

    def _tool_mcp_prepare_execution(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for mcp.prepare_execution")
        patch_json = inp.get("patch_json")
        if not isinstance(patch_json, dict):
            raise ValueError("patch_json is required for mcp.prepare_execution")
        return prepare_patch_execution(active_settings, pid, inp)

    def _tool_aieng_validate(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "aieng.validate",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "aieng.validate",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.validate_package(
                package_path,
                aieng_root=active_settings.aieng_root,
            )
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.validate",
                "status": "error",
                "code": "validation_failed",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "aieng.validate",
            "status": "completed",
            "package_path": str(package_path),
            "validation_ok": result.get("ok"),
            "messages": result.get("messages", []),
            "counts": result.get("counts", {}),
        }

    def _tool_aieng_convert(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        source_path_str: str | None = inp.get("sourcePath") or inp.get("source_path")
        out_path_str: str | None = inp.get("outPath") or inp.get("out_path")
        project_id: str | None = inp.get("project_id")
        converter_id: str | None = inp.get("converterId") or inp.get("converter_id")
        overwrite: bool = bool(inp.get("overwrite", False))
        runtime_mode: str = inp.get("runtimeMode") or inp.get("runtime_mode") or "auto"
        model_id: str | None = inp.get("modelId") or inp.get("model_id")
        execute_shape_ir: bool = bool(inp.get("executeShapeIr", inp.get("execute_shape_ir", True)))

        # Resolve source_path from project.source_step if not provided
        if not source_path_str and project_id:
            proj = get_project(active_settings, project_id)
            src = resolve_project_path(active_settings, project_id, proj.get("source_step"))
            if src is not None and src.exists():
                source_path_str = str(src)

        if not source_path_str:
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "missing_source_path",
                "message": "No source path provided and no project source_step could be resolved.",
            }

        source_path = _Path(source_path_str)
        if not source_path.exists():
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "source_not_found",
                "message": f"Source file not found: {source_path_str}",
            }

        # Resolve out_path: default to project packages dir
        if not out_path_str and project_id:
            proj_name = _Path(source_path_str).stem
            out_path_str = str(project_dir(active_settings, project_id) / "packages" / f"{proj_name}.aieng")

        if not out_path_str:
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "missing_out_path",
                "message": "No output path provided and could not infer one from project.",
            }

        out_path = _Path(out_path_str)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            result = aieng_bridge.convert_source_to_package(
                source_path,
                out_path,
                aieng_root=active_settings.aieng_root,
                model_id=model_id,
                converter_id=converter_id,
                overwrite=overwrite,
                runtime_mode=runtime_mode,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "conversion_failed",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        shape_ir_execution: dict[str, Any] | None = None

        class _ShapeIRRepresentationHandled(Exception):
            """Internal: a non-build123d representation was already handled (run or
            skipped) — break out before the default build123d execution path,
            without falling into the generic error handler."""

        if result.get("source_type") == "shape_ir" and execute_shape_ir:
            try:
                import zipfile as _zipfile
                from .. import cad_generation as _cad_generation
                from aieng.converters.shape_ir import (
                    representation_runtime as _shape_ir_runtime,
                    shape_ir_representation as _shape_ir_rep,
                )

                with _zipfile.ZipFile(out_path, "r") as _archive:
                    names = set(_archive.namelist())
                    _ir_payload = (
                        json.loads(_archive.read("geometry/shape_ir.json").decode("utf-8"))
                        if "geometry/shape_ir.json" in names else {}
                    )
                    representation = _shape_ir_rep(_ir_payload) if isinstance(_ir_payload, dict) else "brep_build123d"
                    runtime = _shape_ir_runtime(representation)
                    # build123d-runtime representations (brep_build123d, nurbs_brep)
                    # all execute the generated geometry/source.py through the
                    # build123d runner — exact STEP/B-Rep with per-face topology.
                    if runtime == "build123d":
                        if "geometry/source.py" not in names:
                            raise RuntimeError("Shape IR package did not contain geometry/source.py")
                        source_code = _archive.read("geometry/source.py").decode("utf-8")

                # Mesh backends (SDF / Manifold): run the field/CSG, write mesh-only
                # artifacts, and reconcile provenance (geometry_kind=mesh). Shared path.
                _mesh_backends = {
                    "implicit_sdf": ("geometry/sdf_source.py", _cad_generation._execute_sdf_code, "sdf"),
                    "manifold_mesh": ("geometry/manifold_source.py", _cad_generation._execute_manifold_code, "manifold"),
                }
                if representation in _mesh_backends:
                    src_member, runner_fn, backend_name = _mesh_backends[representation]
                    if src_member not in names:
                        raise RuntimeError(f"{representation} package did not contain {src_member}")
                    with _zipfile.ZipFile(out_path, "r") as _archive:
                        mesh_code = _archive.read(src_member).decode("utf-8")
                    stl_bytes, glb_bytes, mesh_topo = runner_fn(mesh_code, timeout=int(inp.get("timeout") or 120))
                    mesh_fg = _cad_generation._mesh_feature_graph(mesh_topo)
                    _cad_generation._write_mesh_artifacts(out_path, stl_bytes, glb_bytes, mesh_topo, mesh_fg)
                    _cad_generation.reconcile_shape_ir_provenance(
                        out_path, mesh_topo, mesh_fg,
                        representation=representation, backend=backend_name, geometry_kind="mesh",
                    )
                    mesh_named = _cad_generation._named_parts_from_feature_graph(mesh_fg)
                    shape_ir_execution = {
                        "status": "ok",
                        "representation": representation,
                        "backend": backend_name,
                        "geometry_kind": "mesh",
                        "written_artifacts": [
                            "geometry/preview.stl",
                            "geometry/topology_map.json",
                            "graph/feature_graph.json",
                        ] + (["geometry/preview.glb"] if glb_bytes else []),
                        "named_parts": mesh_named,
                        "part_count": len(mesh_named),
                        "geometry_report": _cad_generation._compute_geometry_report(mesh_topo),
                    }
                    raise _ShapeIRRepresentationHandled()

                if runtime != "build123d":
                    # Representations whose runner isn't wired yet (future targets).
                    shape_ir_execution = {
                        "status": "skipped",
                        "code": "runtime_not_wired",
                        "representation": representation,
                        "message": (
                            f"Shape IR uses the '{representation}' representation; its compiled "
                            "source was emitted but no matching runner is wired yet, so no executed "
                            "geometry/preview was produced."
                        ),
                    }
                    raise _ShapeIRRepresentationHandled()

                step_bytes, stl_bytes, glb_bytes, topo, mesh_meta = _cad_generation._execute_build123d_code(
                    source_code,
                    timeout=int(inp.get("timeout") or 60),
                )
                feature_graph = _cad_generation._topology_to_feature_graph(
                    topo,
                    source_code=source_code,
                    model_kind=str(inp.get("model_kind") or "organic"),
                )
                _cad_generation._write_cad_artifacts(
                    out_path,
                    step_bytes=step_bytes,
                    stl_bytes=stl_bytes,
                    topology_map=topo,
                    feature_graph=feature_graph,
                    generated_code=source_code,
                    glb_bytes=glb_bytes,
                )
                # The projected topology/feature were just replaced with REAL
                # executed geometry; refresh object_registry + stamp provenance so
                # they don't dangle against the converter's pre-execution slug ids.
                _cad_generation.reconcile_shape_ir_provenance(
                    out_path, topo, feature_graph,
                    representation=representation, backend="build123d", geometry_kind="brep",
                )
                named_parts = _cad_generation._named_parts_from_feature_graph(feature_graph)
                shape_ir_execution = {
                    "status": "ok",
                    "representation": representation,
                    "written_artifacts": [
                        "geometry/generated.step",
                        "geometry/preview.stl",
                        "geometry/topology_map.json",
                        "graph/feature_graph.json",
                        "geometry/source.py",
                    ] + (["geometry/preview.glb"] if glb_bytes else []),
                    "named_parts": named_parts,
                    "part_count": len(named_parts),
                    "geometry_report": _cad_generation._compute_geometry_report(topo),
                    "mesh_meta_available": mesh_meta is not None,
                }
            except _ShapeIRRepresentationHandled:
                pass  # shape_ir_execution already holds the ok/skipped status
            except Exception as exc:  # noqa: BLE001 - return structured tool error, don't throw
                shape_ir_execution = {
                    "status": "error",
                    "code": "shape_ir_execution_failed",
                    "message": f"{type(exc).__name__}: {exc}",
                }

        # Unified Shape IR verification: audit the (now final) package and write
        # diagnostics/shape_ir_verification.json. Best-effort — never fails convert.
        shape_ir_verification: dict[str, Any] | None = None
        shape_ir_object_registry: dict[str, Any] | None = None
        if result.get("source_type") == "shape_ir":
            try:
                from aieng.converters.shape_ir_verification import write_shape_ir_verification
                shape_ir_verification = write_shape_ir_verification(out_path)
            except Exception as exc:  # noqa: BLE001
                shape_ir_verification = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
            # Object registry (depends on verification): links Shape IR nodes to
            # topology/mesh/viewer entities + editable parameters.
            try:
                from aieng.converters.shape_ir_object_registry import write_shape_ir_object_registry
                shape_ir_object_registry = write_shape_ir_object_registry(out_path)
            except Exception as exc:  # noqa: BLE001
                shape_ir_object_registry = {"error": f"{type(exc).__name__}: {exc}"}

        preview_result: dict[str, Any] | None = None

        # Update project aieng_file if project_id is available
        if project_id:
            try:
                proj = get_project(active_settings, project_id)
                rel_out = project_relpath(active_settings, project_id, out_path)
                proj["aieng_file"] = rel_out
                proj["status"] = "converted"
                save_project(active_settings, proj)
                if shape_ir_execution and shape_ir_execution.get("status") == "ok":
                    preview_result = convert_asset(active_settings, project_id)
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to persist converted project metadata; keeping tool result.",
                    subsystem="app_factory.convert_asset.project_update",
                    context={"project_id": project_id},
                )

        return {
            "ok": True,
            "tool": "aieng.convert",
            "status": "completed",
            "out_path": result.get("out_path"),
            "source_type": result.get("source_type"),
            "converter_id": result.get("converter_id"),
            "shape_ir_execution": shape_ir_execution,
            "shape_ir_verification": shape_ir_verification,
            "shape_ir_object_registry": shape_ir_object_registry,
            "preview": preview_result,
        }

    def _tool_aieng_write_completeness_report(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        overwrite: bool = bool(inp.get("overwrite", False))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "aieng.write_completeness_report",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "aieng.write_completeness_report",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.write_completeness_report(
                package_path,
                aieng_root=active_settings.aieng_root,
                overwrite=overwrite,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "aieng.write_completeness_report",
                "status": "error",
                "code": "write_failed",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.write_completeness_report",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "aieng.write_completeness_report",
            "status": "completed",
            "package_path": str(package_path),
            "artifacts": result.get("artifacts", []),
        }

    def _tool_aieng_update_validation_status(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        overwrite: bool = bool(inp.get("overwrite", False))
        extra_status: dict[str, Any] | None = inp.get("extraStatus") or inp.get("extra_status")

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "aieng.update_validation_status",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "aieng.update_validation_status",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.update_validation_status(
                package_path,
                aieng_root=active_settings.aieng_root,
                overwrite=overwrite,
                extra_status=extra_status,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "aieng.update_validation_status",
                "status": "error",
                "code": "update_failed",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.update_validation_status",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "aieng.update_validation_status",
            "status": "completed",
            "package_path": str(package_path),
            "artifacts": result.get("artifacts", []),
        }

    def _tool_aieng_write_evidence_scaffold(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        overwrite: bool = bool(inp.get("overwrite", False))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "aieng.write_evidence_scaffold",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "aieng.write_evidence_scaffold",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.write_evidence_scaffold(
                package_path,
                aieng_root=active_settings.aieng_root,
                overwrite=overwrite,
            )
        except FileExistsError as exc:
            return {
                "ok": False,
                "tool": "aieng.write_evidence_scaffold",
                "status": "error",
                "code": "scaffold_exists",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.write_evidence_scaffold",
                "status": "error",
                "code": "scaffold_write_failed",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "aieng.write_evidence_scaffold",
            "status": "completed",
            "package_path": str(package_path),
            "claims_advanced": False,
            "artifacts": result.get("artifacts", []),
        }

    def _tool_aieng_list_projects(_inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """List all .aieng projects the workbench knows about.

        Broken projects (missing or unreadable metadata) are filtered out so the
        agent never receives a project_id that would later return 404.
        """
        projects: list[dict[str, Any]] = []
        for path in active_settings.projects_root.glob("*/metadata.json"):
            metadata = read_json(path, None)
            if metadata is None:
                continue  # unreadable / broken metadata
            if not isinstance(metadata, dict):
                continue
            if not metadata.get("id"):
                continue  # missing project_id — would cause 404 downstream
            projects.append(normalize_project(metadata))
        projects.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
        return {"projects": projects, "count": len(projects)}

    rt.register_tool(
        "aieng.list_projects",
        _tool_aieng_list_projects,
        description=(
            "List all projects available in this workbench instance. Returns id, "
            "name, status, last-modified, and (for agent-built geometry) named_parts "
            "+ part_count for each project. Call this first if you don't know which "
            "project_id to use; use aieng.find_projects_by_part to locate a project "
            "by a part label."
        ),
        input_schema=_schema("aieng.list_projects"),
    )

    def _tool_aieng_create_project(_inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Create a new empty project."""
        from ..project_io import default_project, save_project

        name = str(_inp.get("name") or "").strip() or "Untitled project"
        project = save_project(active_settings, default_project(name))
        return {
            "id": project["id"],
            "name": project["name"],
            "status": project.get("status", "empty"),
            "created_at": project.get("created_at"),
            "message": f"Project '{project['name']}' created successfully.",
        }

    rt.register_tool(
        "aieng.create_project",
        _tool_aieng_create_project,
        description=(
            "Create a new empty workbench project. Returns the project's id, name, "
            "and status. Use this when the user wants to start CAD modeling from "
            "scratch and no suitable existing project is available. The returned "
            "id can be passed directly to geometry-mutation tools such as "
            "cad.execute_build123d."
        ),
        input_schema=_schema("aieng.create_project"),
    )

    def _tool_aieng_find_projects_by_part(_inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Find projects whose geometry contains a named part matching the query.

        Scans each project's metadata ``named_parts`` (cheap; populated on every
        agent build); for older projects without that field it falls back to
        reading the package's feature graph. Substring, case-insensitive.
        """
        from ..cad_generation import _named_parts_from_package
        from ..project_io import resolve_project_path

        query = str(_inp.get("query") or "").strip().lower()
        if not query:
            return {"query": "", "matches": [], "count": 0}
        matches: list[dict[str, Any]] = []
        for path in active_settings.projects_root.glob("*/metadata.json"):
            proj = normalize_project(read_json(path, {}))
            parts = proj.get("named_parts")
            if not isinstance(parts, list):
                parts = []
                pkg_path = resolve_project_path(active_settings, proj["id"], proj.get("aieng_file"))
                if pkg_path and pkg_path.exists():
                    parts = _named_parts_from_package(pkg_path)
            hits = [str(p) for p in parts if query in str(p).lower()]
            if hits:
                matches.append({
                    "id": proj["id"],
                    "name": proj["name"],
                    "status": proj.get("status"),
                    "matched_parts": hits,
                    "part_count": len(parts),
                })
        matches.sort(key=lambda m: (-len(m["matched_parts"]), m["name"]))
        return {"query": query, "matches": matches, "count": len(matches)}

    rt.register_tool(
        "aieng.find_projects_by_part",
        _tool_aieng_find_projects_by_part,
        description=(
            "Find projects whose geometry contains a named part matching the query "
            "(case-insensitive substring on part labels). Use this to locate a model "
            "by content, e.g. find which project holds the 'optimus' parts."
        ),
        input_schema=_schema("aieng.find_projects_by_part"),
    )

    def _tool_aieng_delete_project(_inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Permanently delete a project: its directory + chat sessions/messages."""
        pid = str(_inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "message": "project_id is required"}
        try:
            result = _delete_project_everywhere(pid)
        except HTTPException:
            return {"status": "error", "code": "not_found", "message": f"project not found: {pid}"}
        return {"status": "ok", **result}

    rt.register_tool(
        "aieng.delete_project",
        _tool_aieng_delete_project,
        description=(
            "[APPROVAL REQUIRED] Permanently delete a project — its .aieng package, "
            "metadata, viewer assets, and all chat sessions/messages. Irreversible. "
            "Confirm with the user before calling."
        ),
        input_schema=_schema("aieng.delete_project"),
        requires_approval=True,
    )

    def _tool_aieng_apply_shape_ir_patch(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Apply a surgical patch to a project's Shape IR (atomic + validated),
        then recompile through runtime routing. dry_run validates + reports only."""
        import zipfile as _zipfile
        from aieng.converters import shape_ir_patch as _patch
        from .. import cad_generation as _cad_generation
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        patch = inp.get("patch") if isinstance(inp.get("patch"), dict) else None
        dry_run = bool(inp.get("dry_run", False))
        if not pid or patch is None:
            return {"status": "error", "code": "bad_input", "message": "project_id and patch are required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            with _zipfile.ZipFile(pkg, "r") as zf:
                if "geometry/shape_ir.json" not in zf.namelist():
                    return {"status": "error", "code": "not_shape_ir",
                            "message": "project has no geometry/shape_ir.json"}
                payload = json.loads(zf.read("geometry/shape_ir.json").decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "read_failed", "message": f"{type(exc).__name__}: {exc}"}

        result = _patch.apply_shape_ir_patch(payload, patch, dry_run=dry_run)
        report = _patch.build_patch_report(patch, result)
        recompile: dict[str, Any] | None = None

        if result["ok"] and not dry_run:
            # Commit the patched Shape IR, then re-run the full pipeline.
            _cad_generation._replace_member(
                pkg, "geometry/shape_ir.json",
                (json.dumps(result["new_payload"], indent=2, sort_keys=True) + "\n").encode(),
            )
            recompile = _cad_generation.recompile_shape_ir_package(pkg, timeout=int(inp.get("timeout") or 120))
            report["recompile"] = recompile
            _patch.write_patch_report(pkg, report)  # persist only when committed
            # Publish the recompiled preview to viewer/model.* so the UI viewer shows
            # the patched geometry (recompile only refreshes the in-package preview, not
            # the on-disk viewer asset the frontend defaults to). Mirrors cad.execute.
            try:
                glb_bytes = stl_bytes = None
                with _zipfile.ZipFile(pkg, "r") as zf:
                    names = zf.namelist()
                    if "geometry/preview.glb" in names:
                        glb_bytes = zf.read("geometry/preview.glb")
                    if "geometry/preview.stl" in names:
                        stl_bytes = zf.read("geometry/preview.stl")
                proj = get_project(active_settings, pid)
                if glb_bytes or stl_bytes:
                    proj["status"] = "viewer_ready_glb" if glb_bytes else "viewer_ready_stl"
                    _cad_generation._publish_preview_to_viewer(active_settings, pid, proj, glb_bytes, stl_bytes)
                proj["updated_at"] = now_iso()
                save_project(active_settings, proj)
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to persist project metadata after Shape IR patch.",
                    subsystem="app_factory.shape_ir_patch.project_update",
                    context={"project_id": pid},
                )

        return {
            "status": "ok" if result["ok"] else "rejected",
            "dry_run": dry_run,
            "patch_report": report,
            "recompile": recompile,
        }

    rt.register_tool(
        "aieng.apply_shape_ir_patch",
        _tool_aieng_apply_shape_ir_patch,
        description=(
            "[APPROVAL REQUIRED] Apply a surgical patch to a project's Shape IR "
            "(set_parameter / move_control_point / add_node / remove_node / replace_node / "
            "connect / disconnect / change_representation_backend). Atomic + validated: invalid "
            "patches are rejected without overwriting. On success the package is recompiled through "
            "runtime routing and verification/object-registry are refreshed. Use dry_run to preview."
        ),
        input_schema=_schema("aieng.apply_shape_ir_patch"),
        requires_approval=True,
    )

    def _tool_aieng_agent_readme(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Return compact onboarding by default, with a full-guide compatibility mode."""
        from .. import agent_guides

        if str(inp.get("detail") or "quickstart").lower() == "full":
            result = agent_guides.full_result()
        else:
            result = agent_guides.quickstart_result()
        # Registry identity so an agent can tell if this long-lived MCP session
        # is serving a stale tool set (#29) — compare against GET /api/health.
        result["registry"] = rt.registry_identity()
        return result

    rt.register_tool(
        "aieng.agent_readme",
        _tool_aieng_agent_readme,
        description=(
            "Return compact operational onboarding. Read this once at the start of a session, "
            "then use aieng.guide only for task-specific detail. detail=full preserves access "
            "to the canonical complete AGENTS.md."
        ),
        input_schema=_schema("aieng.agent_readme"),
    )

    def _tool_aieng_guide(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Return one detailed guide topic extracted from canonical AGENTS.md."""
        from .. import agent_guides

        return agent_guides.guide_result(str(inp.get("topic") or ""))

    rt.register_tool(
        "aieng.guide",
        _tool_aieng_guide,
        description=(
            "Return task-specific detail extracted from the canonical AGENTS.md without "
            "loading the full guide. Topics include cad, cae, pointers, tools, workflows, "
            "package, fallback, frontend, approvals, operators, and full."
        ),
        input_schema=_schema("aieng.guide"),
    )

    rt.register_tool(
        "aieng.inspect_package",
        _tool_inspect_package,
        description=(
            "Inspect a .aieng package and return the full project semantic summary "
            "(geometry, CAE setup, results, verdict, design targets). "
            "Call this first when starting work on a project to understand its current state."
        ),
        input_schema=_schema("aieng.inspect_package"),
    )
    rt.register_tool(
        "aieng.agent_context",
        _tool_agent_context,
        description=(
            "Return the compact agent-facing CAD/CAE context: geometry with @face/@feature pointers, "
            "stale-artifact warnings (EDIT IMPACT), CAE setup summary, results, design targets, "
            "and suggested next steps. "
            "Call this before every project-level action — it gives you the pointer IDs needed "
            "to construct valid cad.* and cae.* tool calls."
        ),
        input_schema=_schema("aieng.agent_context"),
    )
    rt.register_tool(
        "aieng.refresh_semantics",
        _tool_refresh_semantics,
        description=(
            "Re-validate the package and refresh semantic state (face labels, feature graph, "
            "stale-artifact flags). Call this after any geometry edit to clear EDIT IMPACT warnings "
            "before re-running the CAE pipeline."
        ),
        input_schema=_schema("aieng.refresh_semantics"),
    )
    rt.register_tool(
        "aieng.generate_preview",
        _tool_generate_preview,
        description=(
            "Regenerate the 3-D web preview asset (GLB preferred, STL fallback) from the current STEP file. "
            "Call this after cad.execute_build123d to update the viewer in the React UI."
        ),
        input_schema=_schema("aieng.generate_preview"),
    )
    rt.register_tool(
        "aieng.read_audit_log",
        _tool_read_audit_log,
        description="Return the most recent audit log entries for this project",
        input_schema=_schema("aieng.read_audit_log"),
    )
    rt.register_tool(
        "aieng.recent_activity",
        _tool_recent_activity,
        description=(
            "Return recent CAD build/activity events for a project (paginated by "
            "limit / since_ts) — headless build feedback + iteration errors without "
            "the web viewer. Poll with since_ts=latest_ts for new events."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project to filter events for."},
                "limit": {"type": "integer", "description": "Max events (most recent); default 50, cap 500."},
                "since_ts": {"type": "number", "description": "Return only events with ts > since_ts (poll for new)."},
            },
        },
    )
    rt.register_tool(
        "aieng.write_completeness_report",
        _tool_aieng_write_completeness_report,
        description=(
            "Write a completeness/missingness report (validation/completeness_report.json) into a .aieng package. "
            "Assesses 19+ categories: geometry, topology, features, constraints, simulation setup, evidence, etc."
        ),
        input_schema=_schema("aieng.write_completeness_report"),
    )
    rt.register_tool(
        "aieng.update_validation_status",
        _tool_aieng_update_validation_status,
        description=(
            "Update validation status (validation/status.yaml) inside a .aieng package. "
            "Records geometry, topology, feature, solver/mesh, and CAE import status with explicit claim policy."
        ),
        input_schema=_schema("aieng.update_validation_status"),
    )
    rt.register_tool(
        "aieng.write_evidence_scaffold",
        _tool_aieng_write_evidence_scaffold,
        description=(
            "Write results/evidence_index.json scaffold into a .aieng package. "
            "Required before importing external solver or mesh evidence; does not create or advance claim maps."
        ),
    )
    rt.register_tool(
        "aieng.validate",
        _tool_aieng_validate,
        description=(
            "Validate a .aieng package against AIENG schemas and rules. "
            "Returns PASS/WARN/FAIL messages and an overall validation_ok boolean."
        ),
        input_schema=_schema("aieng.validate"),
    )
    rt.register_tool(
        "aieng.convert",
        _tool_aieng_convert,
        description=(
            "Convert a CAD/Shape source file (.step/.stp/.FCStd/.shape.json/.shape_ir.json) "
            "to a .aieng package. Shape IR sources also generate build123d source.py and, "
            "by default, execute it to publish STEP/STL/GLB viewer artifacts. "
            "Automatically updates project aieng_file on success."
        ),
        input_schema=_schema("aieng.convert"),
    )
    rt.register_tool(
        "postprocess.generate_computed_metrics",
        _tool_generate_computed_metrics,
        description=(
            "Import computed metrics from a CSV or JSON file (inputPath) into a .aieng package. "
            "Writes results/computed_metrics.json back into the package."
        ),
        input_schema=_schema("postprocess.generate_computed_metrics"),
    )
    rt.register_tool(
        "postprocess.refresh_cae_summary",
        _tool_refresh_cae_summary,
        description="Regenerate CAE result summary, evidence index, and markdown inside the .aieng package",
        input_schema=_schema("postprocess.refresh_cae_summary"),
    )
    rt.register_tool(
        "mcp.check",
        _tool_mcp_check,
        description="Check MCP guardrails, capability gaps, and operation policy for this project",
    )
    rt.register_tool(
        "mcp.parse_patch",
        _tool_mcp_parse_patch,
        description="Parse an .aieng patch proposal without executing it",
    )
    rt.register_tool(
        "mcp.prepare_execution",
        _tool_mcp_prepare_execution,
        description="Dry-run an .aieng patch proposal and return preflight side effects",
    )
    return {
        "apply_shape_ir_patch": _tool_aieng_apply_shape_ir_patch,
    }
