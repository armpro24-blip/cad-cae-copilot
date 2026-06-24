"""cae runtime tool registrations.

Extracted from runtime_tool_registry.py to keep domain logic focused.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import blocked_reason_codes as _blocked_reason_codes
from .. import next_actions as _next_actions
from .. import operation_receipt as _receipt
from ..legacy_app_symbols import sync_main_symbols

LOGGER = logging.getLogger("app.app_factory")


def register_cae_tools(rt: Any, active_settings: Any, app_context: Any, _schema: Any) -> dict[str, Any]:
    """Register cae runtime tools."""
    sync_main_symbols(globals())
    from ..runtime_tool_registry import _resolve_ccx_cmd
    from ..project_io import validate_cae_topology_references

    _delete_project_everywhere = app_context.delete_project_everywhere
    _load_project_feature_parameters = app_context.load_project_feature_parameters

    def _normalize_cae_setup_patches(inp: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        raw_patches = inp.get("patches")
        if raw_patches is not None:
            if isinstance(raw_patches, dict):
                raw_patches = [raw_patches]
            if not isinstance(raw_patches, list):
                return [], "Input field 'patches' must be a list of patch objects."
            patches = list(raw_patches)
        else:
            legacy_patch = inp.get("patch")
            if legacy_patch is None:
                return [], None
            if isinstance(legacy_patch, list):
                patches = list(legacy_patch)
            elif isinstance(legacy_patch, dict):
                if (
                    "path" in legacy_patch
                    or "action_type" in legacy_patch
                    or "operation" in legacy_patch
                ):
                    patches = [dict(legacy_patch)]
                else:
                    patches = []
                    for op_key, op_value in legacy_patch.items():
                        if not isinstance(op_value, dict):
                            return [], (
                                "Legacy input field 'patch' must contain patch objects keyed by "
                                "operation name."
                            )
                        op = dict(op_value)
                        op.setdefault("action_type", str(op_key).split(":", 1)[0])
                        patches.append(op)
            else:
                return [], "Input field 'patch' must be a patch object or list of patch objects."

        normalized: list[dict[str, Any]] = []
        for patch in patches:
            if not isinstance(patch, dict):
                return [], "Each CAE setup patch must be an object."
            op = dict(patch)
            action = op.get("action_type") or op.get("operation")
            if (
                action in {"replace_json", "merge_object", "append_array_item"}
                and "value" not in op
                and "content" in op
            ):
                op["value"] = op.pop("content")
            normalized.append(op)
        return normalized, None

    def _tool_cae_apply_setup_patch(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        # Guard: reject claims_advanced requests
        if inp.get("claims_advanced"):
            return {
                "status": "error",
                "code": "unsupported_operation",
                "message": "claims_advanced=true is not supported in this version.",
            }

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
                "code": "missing_package_path",
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

        patches, patch_error = _normalize_cae_setup_patches(inp)
        if patch_error:
            return {
                "status": "error",
                "code": "invalid_patch_input",
                "message": patch_error,
            }
        if not patches:
            return {
                "status": "error",
                "code": "no_patches",
                "message": "No patches provided.",
            }

        # Validate all patches before applying any
        for i, patch in enumerate(patches):
            path = patch.get("path", "")
            action = patch.get("action_type") or patch.get("operation") or ""
            if not _is_allowed_patch_path(path):
                return {
                    "status": "error",
                    "code": "forbidden_path",
                    "message": (
                        f"Patch {i}: path {path!r} is not in the allowed patch locations. "
                        "Only simulation/cae_imports/, simulation/load_cases/, "
                        "simulation/solver_settings.json, simulation/cae_mapping.json, "
                        "and graph/constraints.json are writable."
                    ),
                }
            if action not in _SUPPORTED_PATCH_OPERATIONS:
                return {
                    "status": "error",
                    "code": "unsupported_operation",
                    "message": (
                        f"Patch {i}: action_type {action!r} is not supported. "
                        f"Supported: {sorted(_SUPPORTED_PATCH_OPERATIONS)}"
                    ),
                }

        try:
            changed_paths, apply_warnings, artifact_diffs = _apply_patches_to_package(
                _Path(package_path), patches
            )
        except ValueError as exc:
            return {"status": "error", "code": "patch_error", "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "code": "patch_error", "message": f"Patch failed: {exc}"}

        refreshed_artifacts: list[dict[str, Any]] = []
        refresh_warnings: list[str] = []

        do_refresh = bool(inp.get("refresh_preprocessing_summary", True))
        if do_refresh:
            try:
                refresh_result = aieng_bridge.refresh_preprocessing_summary(
                    package_path,
                    aieng_root=active_settings.aieng_root,
                    overwrite=True,
                )
                refreshed_artifacts.extend(refresh_result.get("artifacts", []))
            except Exception as exc:
                refresh_warnings.append(
                    f"preprocessing_summary_refresh_failed: {exc}. "
                    "Refresh manually via postprocess.refresh_cae_summary."
                )

        refreshed_paths = [a["path"] for a in refreshed_artifacts]
        stale_artifacts = _compute_stale_artifacts(changed_paths, refreshed_paths)
        all_warnings = apply_warnings + refresh_warnings

        return {
            "status": "ok",
            "changed_artifacts": [
                {"path": p, "kind": "cae_setup_patch", "role": "patched_setup_artifact"}
                for p in changed_paths
            ],
            "refreshed_artifacts": refreshed_artifacts,
            "stale_artifacts": stale_artifacts,
            "artifact_diffs": artifact_diffs,
            "warnings": all_warnings,
        }

    def _tool_cae_extract_solver_results(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        frd_path: str | None = inp.get("frdPath") or inp.get("frd_path")

        if not package_path and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path = str(pkg)

        if not package_path:
            return {
                "status": "error",
                "code": "missing_package_path",
                "message": (
                    "No package path provided and no project_id could be resolved. "
                    "Pass packagePath or a project_id with an .aieng file."
                ),
            }

        if not frd_path:
            return {
                "status": "error",
                "code": "missing_frd_path",
                "message": "No frdPath provided. Pass the path to the CalculiX .frd result file.",
            }

        if not _Path(package_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path}",
            }

        if not _Path(frd_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"FRD file not found: {frd_path}",
            }

        load_case_id: str = inp.get("loadCaseId") or inp.get("load_case_id") or "load_case_001"
        software: str = inp.get("software") or "CalculiX"
        overwrite: bool = bool(inp.get("overwrite", True))

        try:
            result = aieng_bridge.extract_frd_solver_results(
                package_path,
                frd_path,
                aieng_root=active_settings.aieng_root,
                load_case_id=load_case_id,
                software=software,
                overwrite=overwrite,
            )
        except Exception as exc:
            return {"status": "error", "code": "extraction_error", "message": str(exc)}

        # Optionally refresh the result summary so the UI reflects real numbers
        refresh_warnings: list[str] = []
        if inp.get("refresh_result_summary", True):
            try:
                aieng_bridge.refresh_cae_result_summary(
                    package_path,
                    aieng_root=active_settings.aieng_root,
                    overwrite=True,
                )
            except Exception as exc:
                refresh_warnings.append(
                    f"result_summary_refresh_failed: {exc}. "
                    "Refresh manually via postprocess.refresh_cae_summary."
                )

        if refresh_warnings:
            result.setdefault("warnings", []).extend(refresh_warnings)

        return result

    def _tool_cae_extract_field_regions(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        frd_path: str | None = inp.get("frdPath") or inp.get("frd_path")
        field: str = inp.get("field") or "S"
        metric: str = inp.get("metric") or "von_mises"
        max_clusters: int = int(inp.get("maxClusters") or inp.get("max_clusters") or 3)
        threshold_percentile: float = float(
            inp.get("thresholdPercentile") or inp.get("threshold_percentile") or 90.0
        )
        overwrite: bool = bool(inp.get("overwrite", False))
        refresh_field_summary: bool = bool(inp.get("refreshFieldSummary", inp.get("refresh_field_summary", True)))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        if not frd_path:
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "missing_frd_path",
                "message": "No frdPath provided. Pass the path to the CalculiX .frd result file.",
            }

        if not _Path(package_path_str).exists():
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        if not _Path(frd_path).exists():
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "file_not_found",
                "message": f"FRD file not found: {frd_path}",
            }

        try:
            result = aieng_bridge.extract_field_regions(
                package_path_str,
                frd_path,
                aieng_root=active_settings.aieng_root,
                field=field,
                metric=metric,
                max_clusters=max_clusters,
                threshold_percentile=threshold_percentile,
                overwrite=overwrite,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "extraction_error",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        field_summary_status = "not_requested"
        refreshed_artifacts: list[dict[str, Any]] = []
        warnings = list(result.get("warnings", []))
        if refresh_field_summary:
            try:
                summary_result = aieng_bridge.write_field_summary(
                    package_path_str,
                    aieng_root=active_settings.aieng_root,
                    overwrite=True,
                )
                refreshed_artifacts = summary_result.get("artifacts", [])
                field_summary_status = summary_result.get("status", "ok")
                if field_summary_status == "skipped":
                    warnings.append(
                        f"Field summary skipped: {summary_result.get('reason', 'aieng.cae_field_summary unavailable')}"
                    )
            except Exception as exc:
                field_summary_status = "error"
                warnings.append(
                    f"Field regions were extracted, but field summary refresh failed: {type(exc).__name__}: {exc}"
                )

        # Refresh the CAE -> Shape IR result map so hotspots stay tied to nodes.
        cae_result_map_status = "ok"
        try:
            from aieng.converters.cae_result_map import write_cae_result_map
            write_cae_result_map(package_path_str)
        except Exception as exc:  # noqa: BLE001
            cae_result_map_status = "error"
            warnings.append(f"cae_result_map refresh failed: {type(exc).__name__}: {exc}")

        return {
            "ok": True,
            "tool": "cae.extract_field_regions",
            "status": "completed",
            "package_path": package_path_str,
            "out_path": result.get("out_path"),
            "cluster_count": result.get("cluster_count", 0),
            "cae_result_map_refreshed": cae_result_map_status,
            "clusters": result.get("clusters", []),
            "warnings": warnings,
            "artifacts": [
                {
                    "path": result.get("out_path", ""),
                    "kind": "field_regions",
                    "role": "high_magnitude_spatial_clusters",
                }
            ],
            "refreshed_artifacts": refreshed_artifacts,
            "field_summary_status": field_summary_status,
        }

    def _recommended_next_calls(
        project_id: str,
        run_id: str,
        load_case_id: str,
        preflight: dict[str, Any],
        ready_to_run: bool,
    ) -> list[dict[str, Any]]:
        """Build actionable next-call recommendations for external agents."""
        recs: list[dict[str, Any]] = []
        if not preflight["has_mesh"]:
            recs.append({
                "tool": "cae.write_mesh_handoff",
                "input": {"project_id": project_id, "handoff_id": "mesh_handoff_001"},
                "reason": (
                    "No mesh files found in package. Write a mesh handoff contract "
                    "or import a meshed CalculiX deck before generating solver input."
                ),
            })
        if not preflight["has_solver_settings"]:
            recs.append({
                "tool": "cae.apply_setup_patch",
                "input": {
                    "project_id": project_id,
                    "patches": [{
                        "path": "simulation/solver_settings.json",
                        "action_type": "create_file",
                        "content": {"solver": "CalculiX", "analysis_type": "linear_static"},
                    }],
                },
                "reason": (
                    "Missing solver settings. Create simulation/solver_settings.json "
                    "with solver target and analysis_type."
                ),
            })
        if not preflight["has_load_case"]:
            recs.append({
                "tool": "cae.apply_setup_patch",
                "input": {
                    "project_id": project_id,
                    "patches": [{
                        "path": f"simulation/load_cases/{load_case_id}.json",
                        "action_type": "create_file",
                        "content": {
                            "id": load_case_id,
                            "loads": [{
                                "id": "load_001",
                                "type": "force",
                                "target": "REPLACE_WITH_NSET_OR_FACE_POINTER",
                                "dof": 2,
                                "value": 500.0,
                            }],
                        },
                    }],
                },
                "reason": f"Missing load case file simulation/load_cases/{load_case_id}.json.",
            })
        if not preflight["has_input_deck"]:
            recs.append({
                "tool": "cae.generate_solver_input",
                "input": {"project_id": project_id, "run_id": run_id, "overwrite": True},
                "reason": (
                    "Missing solver input deck; generate it once mesh, solver settings, "
                    "material, boundary conditions and loads are present."
                ),
            })
        if not preflight["ccx_available"]:
            recs.append({
                "tool": None,
                "action": "Install CalculiX and ensure ccx is on PATH, or set AIENG_CCX_CMD.",
                "reason": "CalculiX command not found.",
            })
        if ready_to_run:
            recs.append({
                "tool": "cae.run_solver",
                "input": {
                    "project_id": project_id,
                    "run_id": run_id,
                    "load_case_id": load_case_id,
                    "input_deck_path": f"simulation/runs/{run_id}/solver_input.inp",
                },
                "reason": "All preflight checks passed.",
                "requires_approval": True,
            })
        return recs

    def _tool_cae_prepare_solver_run(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        import zipfile as _zipfile

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        run_id: str = inp.get("runId") or inp.get("run_id") or "run_001"
        solver: str = inp.get("solver") or "CalculiX"
        load_case_id: str = inp.get("loadCaseId") or inp.get("load_case_id") or "load_case_001"
        input_deck_path_str: str | None = inp.get("inputDeckPath") or inp.get("input_deck_path")
        extract_results: bool = bool(inp.get("extractResults", inp.get("extract_results", True)))
        refresh_summary: bool = bool(inp.get("refreshSummary", inp.get("refresh_summary", True)))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.prepare_solver_run",
                "status": "error",
                "code": "missing_package_path",
                "message": (
                    "No package path provided and no project_id could be resolved. "
                    "Pass packagePath or a project_id with an .aieng file."
                ),
            }

        package_path = Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.prepare_solver_run",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            with _zipfile.ZipFile(package_path, "r") as zf:
                names = set(zf.namelist())
        except Exception as exc:
            return {
                "ok": False,
                "tool": "cae.prepare_solver_run",
                "status": "error",
                "code": "package_read_error",
                "message": f"Failed to read package: {exc}",
            }

        has_mesh = any(n.startswith("simulation/mesh/") for n in names)
        has_solver_settings = "simulation/solver_settings.json" in names
        has_load_case = f"simulation/load_cases/{load_case_id}.json" in names

        if input_deck_path_str:
            has_input_deck = Path(input_deck_path_str).exists()
        else:
            has_input_deck = f"simulation/runs/{run_id}/solver_input.inp" in names

        # Check ccx availability without executing it
        ccx_available = _resolve_ccx_cmd() is not None

        # Validate that face-scoped CAE references still match the current topology.
        topology_validation = validate_cae_topology_references(package_path)
        topology_refs_ok = topology_validation["valid"]

        missing_items: list[str] = []
        if not has_mesh:
            missing_items.append("simulation/mesh/ (no mesh files found in package)")
        if not has_solver_settings:
            missing_items.append("simulation/solver_settings.json")
        if not has_load_case:
            missing_items.append(f"simulation/load_cases/{load_case_id}.json")
        if not has_input_deck:
            deck_hint = f" (or external: {input_deck_path_str})" if input_deck_path_str else ""
            missing_items.append(f"simulation/runs/{run_id}/solver_input.inp{deck_hint}")
        if not ccx_available:
            missing_items.append(
                "CalculiX command unavailable (set AIENG_CCX_CMD or ensure ccx is discoverable on PATH)."
            )
        if not topology_refs_ok:
            missing_items.append(
                "stale_topology_references: CAE face references do not match current geometry."
            )

        ready_to_run = len(missing_items) == 0 and topology_refs_ok

        run_prefix = f"simulation/runs/{run_id}"
        planned_artifacts: list[dict[str, str]] = [
            {"path": f"{run_prefix}/solver_run.json", "kind": "solver_run_record", "role": "run_metadata"},
            {"path": f"{run_prefix}/solver_log.txt", "kind": "solver_log", "role": "solver_stdout"},
            {"path": f"{run_prefix}/outputs/result.frd", "kind": "frd_result", "role": "primary_result"},
        ]
        if extract_results:
            planned_artifacts.append(
                {"path": "results/computed_metrics.json", "kind": "computed_metrics", "role": "extracted_metrics"}
            )
        if refresh_summary:
            planned_artifacts.extend([
                {"path": "results/result_summary.json", "kind": "result_summary", "role": "postprocessing_summary"},
                {"path": "results/evidence_index.json", "kind": "evidence_index", "role": "evidence_index"},
                {"path": "results/postprocessing_summary.md", "kind": "markdown_report", "role": "human_readable_summary"},
            ])

        warnings: list[str] = [
            "No solver execution was performed.",
            "This is a preflight plan only. Solver execution requires external CalculiX setup.",
        ]
        warnings.extend(topology_validation.get("warnings") or [])
        if not ready_to_run:
            warnings.append(f"Run is not ready: {len(missing_items)} item(s) missing.")

        preflight = {
            "has_mesh": has_mesh,
            "has_solver_settings": has_solver_settings,
            "has_load_case": has_load_case,
            "has_input_deck": has_input_deck,
            "ccx_available": ccx_available,
            "missing_items": missing_items,
            "topology_references_valid": topology_refs_ok,
            "topology_hash_current": topology_validation.get("topology_hash_current"),
            "topology_hash_expected": topology_validation.get("topology_hash_expected"),
            "hash_status": topology_validation.get("hash_status"),
            "cae_mapping_stale": topology_validation.get("cae_mapping_stale"),
            "stale_topology_references": topology_validation.get("stale_references", []),
        }

        recommendations = _recommended_next_calls(
            project_id or "",
            run_id,
            load_case_id,
            {
                "has_mesh": has_mesh,
                "has_solver_settings": has_solver_settings,
                "has_load_case": has_load_case,
                "has_input_deck": has_input_deck,
                "ccx_available": ccx_available,
            },
            ready_to_run,
        )
        if not topology_refs_ok:
            recommendations.insert(0, {
                "tool": "ai_preprocessing.run_ai_preprocessing",
                "input": {"project_id": project_id or "", "task_description": "Refresh CAE face references after geometry change"},
                "reason": (
                    "CAE face references are stale relative to the current topology. "
                    "Re-run AI preprocessing to rebind loads/BCs, or use cae.apply_setup_patch "
                    "to update face IDs manually."
                ),
            })

        preflight["blocked_reason_codes"] = _blocked_reason_codes.codes_for_preflight(preflight)

        next_actions_raw: list[dict[str, Any]] = list(recommendations)
        if not ready_to_run:
            next_actions_raw.insert(
                0,
                {
                    "tool": "cae.run_solver",
                    "input": {
                        "project_id": project_id or "",
                        "run_id": run_id,
                        "load_case_id": load_case_id,
                        "input_deck_path": f"simulation/runs/{run_id}/solver_input.inp",
                    },
                    "reason": "Run the solver once all preflight checks pass.",
                    "available_now": False,
                    "blocked_reason": "Run is not ready: missing required inputs or stale topology references.",
                    "blocked_reason_codes": _blocked_reason_codes.codes_for_run_solver_action(preflight),
                    "priority": "high",
                },
            )
        next_actions = _next_actions.normalize_next_actions(
            next_actions_raw, source="cae.prepare_solver_run"
        )

        result = {
            "ok": True,
            "tool": "cae.prepare_solver_run",
            "ready_to_run": ready_to_run,
            "solver": solver,
            "run_id": run_id,
            "load_case_id": load_case_id,
            "requires_approval": True,
            "solver_execution_performed": False,
            "preflight": preflight,
            "planned_artifacts": planned_artifacts,
            "warnings": warnings,
            "blocked_reason_codes": preflight["blocked_reason_codes"],
            "recommended_next_calls": recommendations,
            "next_actions": next_actions,
        }
        return _receipt.receipt_from_prepare_solver_run(result)

    def _tool_cae_generate_solver_input(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        run_id: str = inp.get("runId") or inp.get("run_id") or "run_001"
        overwrite: bool = bool(inp.get("overwrite", False))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.generate_solver_input",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.generate_solver_input",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        # Close the decoupled solve loop: if the package has a Gmsh mesh
        # (simulation/mesh.inp, e.g. from cae.generate_mesh) but no imported
        # source solver deck, synthesize one (mesh + *SOLID SECTION + named NSETs)
        # so the deck generator can bind loads/BCs. An imported deck always wins.
        source_deck_synthesis: dict[str, Any] | None = None
        try:
            from .. import simulation_runner

            source_deck_synthesis = simulation_runner.ensure_source_deck_from_mesh(package_path)
        except Exception as exc:  # noqa: BLE001 — best-effort; deck gen reports the real gap
            source_deck_synthesis = {"created": False, "status": "error", "message": str(exc)}

        try:
            result = aieng_bridge.generate_solver_input(
                package_path,
                aieng_root=active_settings.aieng_root,
                run_id=run_id,
                overwrite=overwrite,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "tool": "cae.generate_solver_input",
                "status": "error",
                "code": "missing_setup",
                "message": str(exc),
                "missing_items": getattr(exc, "missing_items", []),
                "source_deck_synthesis": source_deck_synthesis,
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "cae.generate_solver_input",
                "status": "error",
                "code": "generation_failed",
                "message": str(exc),
                "source_deck_synthesis": source_deck_synthesis,
            }

        return {
            "ok": True,
            "tool": "cae.generate_solver_input",
            "status": "completed",
            "package_path": str(package_path),
            "out_path": result.get("out_path"),
            "warnings": result.get("warnings", []),
            "source_deck_synthesis": source_deck_synthesis,
            "artifacts": [
                {
                    "path": result.get("out_path", ""),
                    "kind": "solver_input_deck",
                    "role": "calculix_linear_static_input",
                }
            ],
        }

    def _tool_cae_run_solver(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        import time as _time
        import subprocess as _subprocess
        import tempfile as _tempfile
        import zipfile as _zipfile
        from pathlib import Path as _Path
        from .. import aieng_bridge

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        run_id: str = inp.get("runId") or inp.get("run_id") or "run_001"
        solver: str = inp.get("solver") or "CalculiX"
        load_case_id: str = inp.get("loadCaseId") or inp.get("load_case_id") or "load_case_001"
        input_deck_path_str: str | None = inp.get("inputDeckPath") or inp.get("input_deck_path")
        extract_results: bool = bool(inp.get("extractResults", inp.get("extract_results", True)))
        refresh_summary: bool = bool(inp.get("refreshSummary", inp.get("refresh_summary", True)))
        overwrite: bool = bool(inp.get("overwrite", True))
        timeout_seconds: int = int(inp.get("timeout_seconds", inp.get("timeoutSeconds", 120)))
        auto_import_evidence: bool = bool(inp.get("autoImportEvidence", inp.get("auto_import_evidence", True)))

        def _with_run_solver_receipt(result: dict[str, Any]) -> dict[str, Any]:
            result.setdefault("project_id", project_id)
            result.setdefault("run_id", run_id)
            return _receipt.receipt_from_run_solver(result)

        # Resolve package path
        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return _with_run_solver_receipt({
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
                "solver_execution_performed": False,
            })

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return _with_run_solver_receipt({
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
                "solver_execution_performed": False,
            })

        # Validate input_deck_path
        if not input_deck_path_str:
            return _with_run_solver_receipt({
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "missing_input_deck",
                "message": "No input_deck_path provided. Pass the path to the CalculiX .inp file inside the package.",
                "solver_execution_performed": False,
            })

        # Reject absolute paths and path traversal
        normalized = input_deck_path_str.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            return _with_run_solver_receipt({
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "forbidden_path",
                "message": "input_deck_path must be a relative path inside the package and must not contain '..' or start with a separator.",
                "solver_execution_performed": False,
            })

        if not input_deck_path_str.lower().endswith(".inp"):
            return _with_run_solver_receipt({
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "invalid_input_deck",
                "message": "input_deck_path must end with .inp",
                "solver_execution_performed": False,
            })

        # Verify input deck exists in package
        try:
            with _zipfile.ZipFile(package_path, "r") as zf:
                names = set(zf.namelist())
                if input_deck_path_str not in names:
                    return _with_run_solver_receipt({
                        "ok": False,
                        "tool": "cae.run_solver",
                        "status": "error",
                        "code": "input_deck_not_found",
                        "message": f"Input deck not found in package: {input_deck_path_str}",
                        "solver_execution_performed": False,
                    })
                inp_data = zf.read(input_deck_path_str)
        except Exception as exc:
            return _with_run_solver_receipt({
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "package_read_error",
                "message": f"Failed to read package: {exc}",
                "solver_execution_performed": False,
            })

        # Refuse to run if the face-scoped CAE references are stale.
        topology_validation = validate_cae_topology_references(package_path)
        if not topology_validation["valid"]:
            return _with_run_solver_receipt({
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "stale_topology_references",
                "message": (
                    "CAE face references do not match the current topology. "
                    "Re-run AI preprocessing to refresh face references, or update "
                    "simulation/cae_mapping.json manually via cae.apply_setup_patch."
                ),
                "topology_validation": topology_validation,
                "solver_execution_performed": False,
            })

        # Locate ccx (respects AIENG_CCX_CMD for cross-env launches)
        ccx_parts = _resolve_ccx_cmd()
        if not ccx_parts:
            return _with_run_solver_receipt({
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "solver_not_found",
                "message": (
                    "CalculiX command unavailable; set a valid AIENG_CCX_CMD "
                    "or ensure ccx is discoverable on PATH."
                ),
                "solver_execution_performed": False,
            })

        # Run solver in a temp directory
        started_at = datetime.now(timezone.utc).isoformat()
        start_ts = _time.monotonic()
        temp_dir = _tempfile.mkdtemp(prefix="aieng_solver_")
        work_dir = _Path(temp_dir)
        changed_artifacts: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        frd_path: _Path | None = None
        return_code: int | None = None
        stdout = ""
        stderr = ""

        try:
            stem = _Path(input_deck_path_str).stem
            local_inp = work_dir / f"{stem}.inp"
            local_inp.write_bytes(inp_data)

            try:
                proc = _subprocess.run(
                    ccx_parts + [stem],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    shell=False,
                )
                return_code = proc.returncode
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
            except _subprocess.TimeoutExpired as exc:
                return_code = -1
                stdout = exc.stdout.decode() if exc.stdout else ""
                stderr = exc.stderr.decode() if exc.stderr else ""
                errors.append(f"Solver timed out after {timeout_seconds} seconds.")
                warnings.append("Solver execution was terminated due to timeout.")
            except Exception as exc:
                return _with_run_solver_receipt({
                    "ok": False,
                    "tool": "cae.run_solver",
                    "status": "error",
                    "code": "solver_subprocess_error",
                    "message": f"Failed to run solver subprocess: {exc}",
                    "solver_execution_performed": False,
                })

            finished_at = datetime.now(timezone.utc).isoformat()
            duration_seconds = round(_time.monotonic() - start_ts, 3)

            # Write solver log
            log_path = work_dir / "solver_log.txt"
            log_path.write_text(
                f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n=== RETURN CODE ===\n{return_code}\n",
                encoding="utf-8",
            )

            solved = return_code == 0
            # Conservative: don't claim convergence without reliable evidence
            converged = None

            # Locate generated FRD in temp working directory
            result_frd = work_dir / f"{stem}.frd"
            if result_frd.exists():
                frd_path = result_frd

            # Detect analysis type from the deck so eigenvalue (modal/buckling)
            # results are routed to the .dat extractor, not the FRD extractor.
            _deck_upper = inp_data.decode("utf-8", errors="replace").upper()
            if "*FREQUENCY" in _deck_upper:
                analysis_type = "modal"
            elif "*BUCKLE" in _deck_upper:
                analysis_type = "buckling"
            else:
                analysis_type = "static"
            result_dat = work_dir / f"{stem}.dat"
            dat_path = result_dat if result_dat.exists() else None

            # Build solver_run.json
            solver_run = {
                "run_id": run_id,
                "solver": solver,
                "state": "completed" if solved else "failed",
                "solved": solved,
                "converged": converged,
                "return_code": return_code,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_seconds": duration_seconds,
                "input_files": [input_deck_path_str],
                "output_files": [],
                "log_file": f"simulation/runs/{run_id}/solver_log.txt",
                "warnings": warnings,
                "errors": errors,
            }
            if frd_path:
                solver_run["output_files"].append(f"simulation/runs/{run_id}/outputs/result.frd")
            if dat_path:
                solver_run["output_files"].append(f"simulation/runs/{run_id}/outputs/result.dat")
            solver_run["analysis_type"] = analysis_type

            # Write artifacts back into package
            run_prefix = f"simulation/runs/{run_id}"

            def _write_safe(artifact_path: str, source: _Path) -> None:
                try:
                    art = write_artifact_to_package(
                        package_path, artifact_path, source, overwrite=overwrite
                    )
                    changed_artifacts.append(art)
                except FileExistsError:
                    warnings.append(f"{artifact_path} already exists and overwrite=False")
                except Exception as exc:
                    warnings.append(f"Failed to write {artifact_path}: {exc}")

            _write_safe(f"{run_prefix}/solver_input.inp", local_inp)
            _write_safe(f"{run_prefix}/solver_log.txt", log_path)

            run_json_path = work_dir / "solver_run.json"
            run_json_path.write_text(json.dumps(solver_run, indent=2), encoding="utf-8")
            _write_safe(f"{run_prefix}/solver_run.json", run_json_path)

            if frd_path:
                _write_safe(f"{run_prefix}/outputs/result.frd", frd_path)
            if dat_path:
                _write_safe(f"{run_prefix}/outputs/result.dat", dat_path)

            # Extract results if requested — route by analysis type: modal/buckling
            # read the .dat (eigenfrequencies / buckling factors), static reads FRD.
            extracted_metrics: dict[str, Any] | None = None
            if extract_results and analysis_type in ("modal", "buckling") and dat_path:
                try:
                    ext_result = aieng_bridge.extract_dat_solver_results(
                        str(package_path),
                        str(dat_path),
                        analysis_type,
                        aieng_root=active_settings.aieng_root,
                        load_case_id=load_case_id,
                        software=solver,
                        overwrite=overwrite,
                    )
                    extracted_metrics = ext_result.get("metrics")
                    changed_artifacts.extend(ext_result.get("artifacts", []))
                except Exception as exc:
                    warnings.append(f"DAT extraction failed: {exc}")
            elif extract_results and frd_path:
                try:
                    ext_result = aieng_bridge.extract_frd_solver_results(
                        str(package_path),
                        str(frd_path),
                        aieng_root=active_settings.aieng_root,
                        load_case_id=load_case_id,
                        software=solver,
                        overwrite=overwrite,
                    )
                    extracted_metrics = ext_result.get("metrics")
                    changed_artifacts.extend(ext_result.get("artifacts", []))
                except Exception as exc:
                    warnings.append(f"FRD extraction failed: {exc}")

            # Auto-import solver evidence (.dat) if solver succeeded and file exists
            auto_import_result: dict[str, Any] | None = None
            if auto_import_evidence and solved:
                dat_path = work_dir / f"{stem}.dat"
                if dat_path.exists():
                    # Ensure evidence scaffold exists before importing
                    try:
                        with _zipfile.ZipFile(package_path, "r") as zf:
                            has_scaffold = "results/evidence_index.json" in zf.namelist()
                    except Exception:
                        log_exception(
                            LOGGER,
                            "Failed to probe existing solver evidence scaffold in package.",
                            subsystem="app_factory.cae_solver.scaffold_probe",
                            context={"project_id": project_id, "run_id": run_id},
                        )
                        has_scaffold = False
                    if not has_scaffold:
                        try:
                            aieng_bridge.write_evidence_scaffold(
                                package_path,
                                aieng_root=active_settings.aieng_root,
                                overwrite=False,
                                include_claim_map=True,
                            )
                        except Exception as exc:
                            warnings.append(f"Auto-scaffold for evidence import failed: {exc}")
                    try:
                        import_result = aieng_bridge.import_solver_evidence(
                            package_path,
                            dat_path,
                            aieng_root=active_settings.aieng_root,
                            result_format="calculix_dat",
                            producer_tool="calculix",
                            claim_support=["claim_solver_result_001"],
                        )
                        auto_import_result = {
                            "status": "ok",
                            "evidence_id": import_result.get("evidence_id"),
                            "artifacts": import_result.get("artifacts", []),
                        }
                        changed_artifacts.extend(import_result.get("artifacts", []))
                    except Exception as exc:
                        warnings.append(f"Auto-import of solver evidence failed: {exc}")
                        auto_import_result = {"status": "error", "message": str(exc)}

            # Refresh summaries if requested
            refreshed_summaries: list[str] = []
            if refresh_summary:
                try:
                    aieng_bridge.refresh_cae_result_summary(
                        str(package_path),
                        aieng_root=active_settings.aieng_root,
                        overwrite=True,
                    )
                    refreshed_summaries.append("result_summary")
                except Exception as exc:
                    warnings.append(f"CAE result summary refresh failed: {exc}")

                try:
                    aieng_bridge.refresh_preprocessing_summary(
                        str(package_path),
                        aieng_root=active_settings.aieng_root,
                        overwrite=True,
                    )
                    refreshed_summaries.append("preprocessing_summary")
                except Exception as exc:
                    warnings.append(f"Preprocessing summary refresh failed: {exc}")

            # Clear geometry-edit stale state when solver run succeeds — fresh
            # results now exist for the current geometry.
            if solved:
                try:
                    _record_solver_validation_in_package(package_path, run_id=run_id)
                except Exception as _exc:
                    warnings.append(f"Could not update revalidation status: {_exc}")

                try:
                    _rev = _read_revalidation_status(package_path) or {}
                    _solver_artifacts = list(changed_artifacts) + [REVALIDATION_STATUS_PATH]
                    _evidence = [
                        a for a in changed_artifacts
                        if a.endswith("solver_run.json") or a.endswith(".frd")
                    ]
                    _append_audit_event_to_package(
                        package_path,
                        _build_audit_event(
                            tool="cae.run_solver",
                            event_type="solver_run_completed",
                            status="completed",
                            artifacts_written=_solver_artifacts,
                            evidence_created=_evidence,
                            state_changes={
                                "requires_revalidation": False,
                                "last_validated_geometry_revision": _rev.get(
                                    "last_validated_geometry_revision"
                                ),
                                "current_geometry_revision": _rev.get("current_geometry_revision"),
                            },
                            geometry_revision=_rev.get("current_geometry_revision"),
                            revalidation_status="fresh",
                        ),
                    )
                except Exception as _exc:
                    warnings.append(f"Could not write audit event: {_exc}")

            result: dict[str, Any] = {
                "ok": True,
                "tool": "cae.run_solver",
                "status": "completed" if solved else "failed",
                "solver_execution_performed": True,
                "return_code": return_code,
                "project_id": project_id,
                "run_id": run_id,
                "changed_artifacts": changed_artifacts,
                "warnings": warnings,
                "errors": errors,
            }
            if extracted_metrics is not None:
                result["extracted_metrics"] = extracted_metrics
            if refreshed_summaries:
                result["refreshed_summaries"] = refreshed_summaries
            if auto_import_result is not None:
                result["auto_import"] = auto_import_result
            return _with_run_solver_receipt(result)

        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to clean temporary solver run directory.",
                    subsystem="app_factory.cae_solver.cleanup_tempdir",
                    context={"run_id": run_id},
                )

    def _tool_cae_generate_mesh(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path as _Path
        from .. import simulation_runner

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        mesh_size_raw = inp.get("mesh_size_mm", inp.get("meshSizeMm"))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.generate_mesh",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.generate_mesh",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        mesh_size_mm: float | None = None
        if mesh_size_raw is not None:
            try:
                mesh_size_mm = float(mesh_size_raw)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "tool": "cae.generate_mesh",
                    "status": "error",
                    "code": "bad_input",
                    "message": f"mesh_size_mm must be a number, got {mesh_size_raw!r}.",
                }

        result = simulation_runner.generate_mesh_for_package(
            package_path, mesh_size_mm=mesh_size_mm
        )
        status = result.get("status")
        ok = status == "success"
        artifacts = [
            {"path": p, "kind": "fe_mesh", "role": "calculix_mesh"}
            for p in result.get("written_artifacts", [])
        ]
        return {
            "ok": ok,
            "tool": "cae.generate_mesh",
            "status": "completed" if ok else status,
            "package_path": str(package_path),
            "node_count": result.get("node_count"),
            "element_count": result.get("element_count"),
            "element_type": result.get("element_type"),
            "target_size_mm": result.get("target_size_mm"),
            "quality_verdict": result.get("quality_verdict"),
            "quality": result.get("quality"),
            "code": result.get("code"),
            "message": result.get("message"),
            "missing_tools": result.get("missing_tools"),
            "artifacts": artifacts,
        }

    def _tool_cae_write_mesh_handoff(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        overwrite: bool = bool(inp.get("overwrite", False))
        handoff_id: str = inp.get("handoffId") or inp.get("handoff_id") or "mesh_handoff_001"

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.write_mesh_handoff",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.write_mesh_handoff",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.write_mesh_handoff(
                package_path,
                aieng_root=active_settings.aieng_root,
                overwrite=overwrite,
                handoff_id=handoff_id,
            )
        except FileNotFoundError as exc:
            return {
                "ok": False,
                "tool": "cae.write_mesh_handoff",
                "status": "error",
                "code": "topology_missing",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "cae.write_mesh_handoff",
                "status": "error",
                "code": "handoff_write_failed",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "cae.write_mesh_handoff",
            "status": "completed",
            "package_path": str(package_path),
            "handoff_id": handoff_id,
            "artifacts": result.get("artifacts", []),
        }

    def _tool_cae_import_solver_evidence(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from .. import aieng_bridge
        from pathlib import Path as _Path
        import zipfile as _zipfile

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        result_file: str | None = inp.get("resultFile") or inp.get("result_file")
        result_format: str = inp.get("resultFormat") or inp.get("result_format") or "calculix_dat"
        producer_tool: str = inp.get("producerTool") or inp.get("producer_tool") or "calculix"
        claim_support: list[str] = inp.get("claimSupport") or inp.get("claim_support") or ["claim_solver_result_001"]
        verification_status: str = inp.get("verificationStatus") or inp.get("verification_status") or "unverified"
        evidence_id: str | None = inp.get("evidenceId") or inp.get("evidence_id")
        auto_scaffold: bool = bool(inp.get("autoScaffold", inp.get("auto_scaffold", True)))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        if not result_file:
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "missing_result_file",
                "message": "No result file provided. Pass resultFile.",
            }

        result_path = _Path(result_file)
        if not result_path.exists():
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "result_file_not_found",
                "message": f"Result file not found: {result_file}",
            }

        # Check if evidence scaffold is present; auto-create if requested
        scaffold_created = False
        if auto_scaffold:
            try:
                with _zipfile.ZipFile(package_path, "r") as zf:
                    has_scaffold = "results/evidence_index.json" in zf.namelist()
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to probe solver evidence scaffold before import.",
                    subsystem="app_factory.import_solver_evidence.scaffold_probe",
                    context={"project_id": project_id, "package_path": package_path},
                )
                has_scaffold = False
            if not has_scaffold:
                try:
                    aieng_bridge.write_evidence_scaffold(
                        package_path,
                        aieng_root=active_settings.aieng_root,
                        overwrite=False,
                        include_claim_map=True,
                    )
                    scaffold_created = True
                except Exception:
                    log_exception(
                        LOGGER,
                        "Failed to create solver evidence scaffold before import.",
                        subsystem="app_factory.import_solver_evidence.scaffold_create",
                        context={"project_id": project_id, "package_path": package_path},
                    )

        try:
            result = aieng_bridge.import_solver_evidence(
                package_path,
                result_path,
                aieng_root=active_settings.aieng_root,
                result_format=result_format,
                producer_tool=producer_tool,
                claim_support=claim_support,
                verification_status=verification_status,
                evidence_id=evidence_id,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "import_validation_failed",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "import_failed",
                "message": str(exc),
            }

        out = {
            "ok": True,
            "tool": "cae.import_solver_evidence",
            "status": "completed",
            "package_path": str(package_path),
            "evidence_id": result.get("evidence_id"),
            "artifacts": result.get("artifacts", []),
            "summary": result.get("summary", {}),
        }
        if scaffold_created:
            out["scaffold_created"] = True
            out.setdefault("warnings", []).append(
                "Evidence scaffold was auto-created because results/evidence_index.json was missing. "
                "No claim status was advanced."
            )
        return out


    from ..runtime_tool_schemas import get_schema as _schema

    # ── agent onboarding tools ────────────────────────────────────────────────

    def _tool_cae_map_results(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Map CAE results back to topology entities / object_registry / source_ir_node;
        write analysis/cae_result_map.json. Read-only analysis (no solver)."""
        from aieng.converters.cae_result_map import write_cae_result_map
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result_map = write_cae_result_map(pkg)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "map_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": "ok", "tool": "cae.map_results", "cae_result_map": result_map}

    rt.register_tool(
        "cae.map_results",
        _tool_cae_map_results,
        description=(
            "Map CAE results (stress/displacement clusters + scalar extrema) back to "
            "topology entities, object_registry objects, and source_ir_node where "
            "resolvable. Writes analysis/cae_result_map.json; reports unmapped regions "
            "honestly. Read-only analysis (no solver/mesher)."
        ),
        input_schema=_schema("cae.map_results"),
    )

    rt.register_tool(
        "cae.apply_setup_patch",
        _tool_cae_apply_setup_patch,
        description=(
            "Apply a controlled patch to CAE setup artifacts inside a .aieng package. "
            "Accepts a preferred 'patches' array plus legacy 'patch' compatibility. "
            "Supports create_file, replace_json, merge_object, append_array_item. "
            "Use this to assemble a minimal linear_static setup: solver settings, "
            "materials, boundary conditions, and loads. Writes only to allowed setup paths; "
            "rejects results/ and path traversal."
        ),
        input_schema=_schema("cae.apply_setup_patch"),
    )
    rt.register_tool(
        "cae.extract_solver_results",
        _tool_cae_extract_solver_results,
        description=(
            "Parse a CalculiX FRD result file and write computed_metrics.json "
            "(max displacement, max von Mises stress) into a .aieng package. "
            "Extracts real numerical extrema from per-node field data."
        ),
        input_schema=_schema("cae.extract_solver_results"),
    )
    rt.register_tool(
        "cae.extract_field_regions",
        _tool_cae_extract_field_regions,
        description=(
            "Extract high-magnitude spatial clusters from a CalculiX FRD result file. "
            "Partitions nodal stress or displacement fields into ≤ N clusters, "
            "reporting centroid, peak magnitude, and node count per cluster. "
            "Writes results/field_regions.json into the .aieng package."
        ),
        input_schema=_schema("cae.extract_field_regions"),
    )
    rt.register_tool(
        "cae.prepare_solver_run",
        _tool_cae_prepare_solver_run,
        description=(
            "Inspect a .aieng package and return a reviewable solver run preflight plan. "
            "Checks for mesh, solver settings, load case, and input deck presence. "
            "No solver is executed. The response includes recommended_next_calls so the "
            "agent knows exactly which cae.* tool to invoke next. Call this before cae.run_solver."
        ),
        input_schema=_schema("cae.prepare_solver_run"),
    )
    rt.register_tool(
        "cae.generate_solver_input",
        _tool_cae_generate_solver_input,
        description=(
            "Generate a runnable CalculiX solver input deck from existing .aieng setup artifacts. "
            "Preserves mesh from a previously imported source deck and assembles materials, BCs, loads, and step. "
            "Supports linear static only. Refuses with explicit missing_items if mesh or setup is absent. "
            "Typical input: {project_id, run_id: 'run_001', overwrite: true}."
        ),
        input_schema=_schema("cae.generate_solver_input"),
    )
    rt.register_tool(
        "cae.run_solver",
        _tool_cae_run_solver,
        requires_approval=True,
        description=(
            "[APPROVAL REQUIRED] Execute an external CalculiX solver run on an existing input deck. "
            "Copies the .inp into a temp directory, runs ccx with a timeout, "
            "captures stdout/stderr, and writes solver_run.json, solver_log.txt, "
            "and result.frd back into the .aieng package. "
            "Always call cae.prepare_solver_run first to verify the input deck is ready. "
            "After completion call cae.extract_solver_results to parse the FRD output. "
            "Typical input: {project_id, run_id, load_case_id, input_deck_path: 'simulation/runs/run_001/solver_input.inp'}."
        ),
        input_schema=_schema("cae.run_solver"),
    )
    rt.register_tool(
        "cae.generate_mesh",
        _tool_cae_generate_mesh,
        description=(
            "Mesh the project's STEP geometry in-process with Gmsh and persist the FE mesh "
            "(simulation/mesh.inp + simulation/mesh/mesh_metadata.json) into the .aieng package, "
            "satisfying the has_mesh preflight and lighting up mesh preview/quality/convergence. "
            "Produces ONLY the mesh — it does not bind loads/BCs, assemble a solver deck, or run a "
            "solver. Degrades honestly when Gmsh is unavailable or the package has no STEP. "
            "Typical input: {project_id, mesh_size_mm: 2.5}."
        ),
        input_schema=_schema("cae.generate_mesh"),
    )
    rt.register_tool(
        "cae.write_mesh_handoff",
        _tool_cae_write_mesh_handoff,
        description=(
            "Write a mesh handoff contract (simulation/mesh_handoff_contract.json) into a .aieng package. "
            "Reads topology_map.json and simulation/setup.yaml to produce a structured handoff spec "
            "for external Gmsh execution. Does not run a mesher. "
            "Typical input: {project_id, handoff_id: 'mesh_handoff_001', overwrite: false}."
        ),
        input_schema=_schema("cae.write_mesh_handoff"),
    )
    rt.register_tool(
        "cae.import_solver_evidence",
        _tool_cae_import_solver_evidence,
        description=(
            "Import an external solver result file as evidence into a .aieng package. "
            "Scans the result file for known numeric observations (max von Mises, max displacement, etc.) "
            "and appends them to results/evidence_index.json. Does not auto-advance claim status."
        ),
        input_schema=_schema("cae.import_solver_evidence"),
    )

    def _tool_cae_mesh_convergence(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Solve the project's current static geometry at several mesh sizes and report,
        per metric, the apparent order of convergence, the Richardson-extrapolated
        mesh-independent value, and the Grid Convergence Index (ASME V&V-20) with a
        converged / not-converged verdict. Read-only on the package; runs one solve per
        mesh size."""
        from ..mesh_convergence_runner import run_mesh_convergence

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        sizes = inp.get("mesh_sizes")
        if not isinstance(sizes, list) or not sizes:
            return {"status": "error", "code": "bad_input",
                    "message": "mesh_sizes must be a non-empty array of element sizes (mm)"}
        try:
            return run_mesh_convergence(
                active_settings,
                pid,
                mesh_sizes=sizes,
                metrics=inp.get("metrics"),
                safety_factor=float(inp.get("safety_factor", 1.25)),
                converged_gci_percent=float(inp.get("converged_gci_percent", 5.0)),
                timeout=int(inp.get("timeout", 180)),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "convergence_failed", "message": f"{type(exc).__name__}: {exc}"}

    rt.register_tool(
        "cae.mesh_convergence",
        _tool_cae_mesh_convergence,
        description=(
            "[APPROVAL REQUIRED] Mesh-convergence study — answers 'can I trust this "
            "stress/deflection, or is it mesh noise?'. Solves the project's CURRENT static "
            "geometry at each requested mesh size (3+ progressively finer sizes recommended) "
            "and reports, per metric, the apparent order of convergence, the "
            "Richardson-extrapolated mesh-independent value, and the Grid Convergence Index "
            "(ASME V&V-20 discretization uncertainty) with a converged / not-converged "
            "verdict + asymptotic-range check. Read-only on the package (mutates nothing); "
            "runs ONE solver execution per mesh size, so it is approval-gated as one "
            "operation. The GCI is discretization uncertainty for these metrics on this "
            "geometry only — not model validity or certification."
        ),
        input_schema=_schema("cae.mesh_convergence"),
        requires_approval=True,
        read_only=False,
    )

    return {}
