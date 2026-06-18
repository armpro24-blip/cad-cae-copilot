"""Project creation, engineering workflow, simulation, and contextual-chat routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from ..legacy_app_symbols import sync_main_symbols
from ..logging_utils import log_exception

LOGGER = logging.getLogger("app.app_factory")


def _sync_main_symbols() -> None:
    sync_main_symbols(globals())


def register_project_workflow_routes(
    app: FastAPI,
    *,
    active_settings: Any,
    db_path: Any,
    app_context: Any,
    tool_handlers: Any,
) -> None:
    _sync_main_symbols()
    _add_chat_message_and_publish = app_context.add_chat_message_and_publish
    _delete_project_everywhere = app_context.delete_project_everywhere
    _load_project_feature_parameters = app_context.load_project_feature_parameters
    _load_project_simulation_setup = app_context.load_project_simulation_setup
    _resolve_api_key = app_context.resolve_api_key
    _tool_aieng_apply_shape_ir_patch = tool_handlers.apply_shape_ir_patch
    _tool_opt_derive_problem_from_cae = tool_handlers.derive_topology_optimization_problem
    _tool_opt_run_topology_optimization = tool_handlers.run_topology_optimization
    _tool_opt_writeback_to_shape_ir = tool_handlers.writeback_topology_optimization
    _tool_opt_topology_to_sizing = tool_handlers.topology_to_sizing
    _tool_opt_run_assembly_topology_optimization = tool_handlers.run_assembly_topology_optimization

    @app.get("/api/adapters/structural/preflight")
    def structural_adapter_preflight() -> dict[str, Any]:
        """Read-only structural CAD/CAE adapter readiness check.

        Returns a capability manifest plus an honest environment preflight
        for the existing Gmsh / CalculiX structural path. Never executes
        mesh or solver tools; never mutates any project or package.
        """
        from .. import structural_adapter

        return structural_adapter.preflight_structural_adapter(active_settings)

    @app.post("/api/projects/{project_id}/structural/prepare-preview")
    def structural_prepare_preview(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Read-only structural solver-run preflight for one project.

        Reuses the structural adapter semantics but remains strictly non-
        executing: no mesh generation, no solver execution, no FRD parsing, and
        no package mutation.
        """
        from .. import structural_adapter

        return structural_adapter.prepare_structural_run_preview(
            active_settings,
            project_id,
            payload or {},
        )

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        items = [normalize_project(read_json(path, {})) for path in active_settings.projects_root.glob("*/metadata.json")]
        return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)

    @app.post("/api/projects")
    def create_project(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        data = payload or {}
        name = str(data.get("name") or "Untitled project").strip() or "Untitled project"
        return save_project(active_settings, default_project(name))

    @app.post("/api/projects/sample")
    def create_sample_project() -> dict[str, Any]:
        project = save_project(active_settings, default_project("SFA-5.41 sample"))
        if active_settings.sample_step.exists():
            target = project_dir(active_settings, project["id"]) / "source" / active_settings.sample_step.name
            shutil.copy2(active_settings.sample_step, target)
            project["source_step"] = project_relpath(active_settings, project["id"], target)
            project["status"] = "sample_ready"
            project["last_error"] = None
        else:
            project["status"] = "sample_missing"
            project["last_error"] = f"Sample STEP not found: {active_settings.sample_step}"
        return save_project(active_settings, project)

    @app.post("/api/projects/{project_id}/upload")
    async def upload(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        filename = SAFE_NAME.sub("_", file.filename or "upload.bin")
        suffix = Path(filename).suffix.lower()
        if suffix not in STEP_EXTENSIONS | {AIENG_EXT}:
            raise HTTPException(status_code=400, detail="only STEP/.aieng uploads are supported")
        folder = "packages" if suffix == AIENG_EXT else "source"
        destination = project_dir(active_settings, project_id) / folder / filename
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        relpath = project_relpath(active_settings, project_id, destination)
        if folder == "packages":
            project["aieng_file"] = relpath
            project["status"] = "package_uploaded"
        else:
            project["source_step"] = relpath
            project["status"] = "step_uploaded"
        project["last_error"] = None
        return save_project(active_settings, project)

    @app.get("/api/projects/{project_id}")
    def get_project_summary(project_id: str) -> dict[str, Any]:
        return package_summary(active_settings, project_id)

    @app.delete("/api/projects/{project_id}")
    def delete_project_endpoint(project_id: str) -> dict[str, Any]:
        """Delete a project: its directory (.aieng package, metadata, viewer,
        logs) and all its chat sessions/messages (kept in a separate sqlite db).
        Idempotent-ish: 404s only if the project metadata doesn't exist."""
        return _delete_project_everywhere(project_id)

    @app.get("/api/projects/{project_id}/agent-context")
    def get_project_agent_context(project_id: str) -> dict[str, Any]:
        """Read-only CAD/CAE semantic context for connected AI agents.

        This is the mainline agent-facing context package: it aggregates
        existing CAD observation, CAE setup/result summaries, targets, metrics,
        target comparisons, and loop history without running CAD/CAE tools or
        mutating project artifacts.
        """
        from .. import agent_context

        return agent_context.build_agent_context(active_settings, project_id)

    @app.post("/api/projects/{project_id}/engineering-action-plan")
    def engineering_action_plan_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Return a typed, read-only action candidate for a chat prompt.

        This endpoint does not execute CAD/CAE tools and does not mutate the
        package. It centralizes the first-pass intent/action decision so the
        chat UI can avoid brittle frontend-only keyword ordering.
        """
        from .. import engineering_action_plan

        p = payload or {}
        return engineering_action_plan.build_engineering_action_plan(
            settings=active_settings,
            project_id=project_id,
            message=str(p.get("message") or ""),
        )

    @app.post("/api/projects/{project_id}/brep-graph/build")
    def build_brep_graph_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Build symbolic B-Rep graph, entity pointer index, and digest.

        Derived from geometry/topology_map.json only; no CAD kernel, LLM, mesh,
        or solver is executed. By default writes graph/brep_graph.json,
        graph/entity_index.json, and ai/brep_digest.md into the package.
        """
        from .. import brep_graph

        return brep_graph.build_brep_graph_for_project(
            active_settings, project_id, payload or {}
        )

    @app.get("/api/projects/{project_id}/brep-graph")
    def get_brep_graph_endpoint(project_id: str) -> dict[str, Any]:
        """Read symbolic B-Rep graph artifacts from a project package."""
        from .. import brep_graph

        return brep_graph.get_brep_graph_for_project(active_settings, project_id)

    @app.get("/api/projects/{project_id}/object-registry")
    def get_object_registry_endpoint(project_id: str) -> dict[str, Any]:
        """Shape IR object registry (+ verification) — source of truth for mapping
        Shape IR nodes <-> viewer-selectable entities. Returns
        {object_registry, verification}. 404 if the project has no package or no
        registry (i.e. not a Shape IR package)."""
        import zipfile as _zipfile
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        registry: dict[str, Any] | None = None
        verification: dict[str, Any] | None = None
        try:
            with _zipfile.ZipFile(package_path, "r") as zf:
                names = set(zf.namelist())
                if "registry/object_registry.json" in names:
                    registry = json.loads(zf.read("registry/object_registry.json").decode("utf-8"))
                if "diagnostics/shape_ir_verification.json" in names:
                    verification = json.loads(zf.read("diagnostics/shape_ir_verification.json").decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"failed to read registry: {exc}") from exc
        if registry is None:
            raise HTTPException(status_code=404, detail="object registry not found (not a Shape IR package?)")
        return {"object_registry": registry, "verification": verification}

    @app.get("/api/projects/{project_id}/editable-parameters")
    def get_editable_parameters_endpoint(project_id: str) -> dict[str, Any]:
        """Editable-parameter listing for the Editable Parameters panel (read-only).

        Reuses the same package feature-graph read as the /modify slot binding and
        the cad.list_editable_parameters tool (single source). Returns
        {parameters, summary}; an empty listing (no feature graph / no editable
        constants) is a valid 200 state the panel renders as "nothing editable yet"
        — not a 404."""
        from ..agent_autopilot.parameter_binding import summarize_parameter_index

        index = _load_project_feature_parameters(project_id) or []
        parameters = [{k: v for k, v in entry.items() if k != "search_tokens"} for entry in index]
        return {"parameters": parameters, "summary": summarize_parameter_index(index)}

    @app.get("/api/projects/{project_id}/critique")
    def get_critique_endpoint(project_id: str) -> dict[str, Any]:
        """Deterministic engineering critique for the Critique panel (read-only).

        Runs the same cad.critique audit (min wall, hole sizes, floating parts, …)
        and returns its findings + severity summary. Best-effort: any failure /
        no-geometry project resolves to an empty findings set the panel hides."""
        from .. import cad_generation as _cg

        empty = {"status": "ok", "findings": [], "summary": {"by_severity": {"high": 0, "medium": 0, "low": 0}}}
        try:
            result = _cg.critique(active_settings, project_id, {})
        except Exception:
            log_exception(
                LOGGER,
                "CAD critique endpoint failed; returning empty findings.",
                subsystem="app_factory.project_critique",
                context={"project_id": project_id},
            )
            return empty
        if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
            # No package / no geometry yet (critique returns an error shape) — the
            # panel treats an empty findings set as "nothing to show".
            return empty
        return result

    @app.get("/api/projects/{project_id}/simulation-readiness")
    def get_simulation_readiness_endpoint(project_id: str) -> dict[str, Any]:
        """Deterministic simulation-readiness report for the CAE readiness panel.

        Classifies the six core inputs (analysis_type / material / loads /
        constraints / mesh / solver) as present / missing / defaultable / unknown,
        reusing the same builder as /simulate. Read-only; runs no solver. Reads the
        direct CAE setup artifact (preferred) and the agent-context cae block.
        Best-effort: failures resolve to a not_found report the panel can hide."""
        from ..agent_autopilot.simulation_readiness import build_simulation_readiness_report

        cae_block: dict[str, Any] | None = None
        try:
            from ..agent_context import build_agent_context

            context = build_agent_context(active_settings, project_id)
            if isinstance(context, dict) and isinstance(context.get("cae"), dict):
                cae_block = context["cae"]
        except Exception:
            log_exception(
                LOGGER,
                "Failed to read CAE block for simulation readiness endpoint.",
                subsystem="app_factory.simulation_readiness.context",
                context={"project_id": project_id},
            )
            cae_block = None
        try:
            setup_artifact = _load_project_simulation_setup(project_id)
        except Exception:
            log_exception(
                LOGGER,
                "Failed to read direct simulation setup artifact for readiness endpoint.",
                subsystem="app_factory.simulation_readiness.setup_artifact",
                context={"project_id": project_id},
            )
            setup_artifact = None
        return build_simulation_readiness_report(cae_block, setup_artifact=setup_artifact)

    def _load_latest_json_artifact(project_id: str, artifact_path: str) -> dict[str, Any]:
        import json as _json
        import zipfile as _zipfile
        from ..project_io import get_project, resolve_project_path

        try:
            project = get_project(active_settings, project_id)
        except Exception:
            return {"available": False, "reason": "project_not_found"}
        pkg_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if pkg_path is None or not pkg_path.exists():
            return {"available": False, "reason": "no_package"}
        try:
            with _zipfile.ZipFile(pkg_path, "r") as zf:
                if artifact_path not in zf.namelist():
                    return {"available": False, "reason": "not_found"}
                data = _json.loads(zf.read(artifact_path).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": "read_failed", "error": str(exc)}
        return {"available": True, "report": data}

    @app.get("/api/projects/{project_id}/sizing-sweep-report")
    def get_sizing_sweep_report_endpoint(project_id: str) -> dict[str, Any]:
        """Latest sizing-sweep report for the workbench panel (read-only).

        Returns the persisted ``analysis/sizing_sweep_report.json`` if present;
        ``available=false`` when the project has no package or no report yet.
        """
        return _load_latest_json_artifact(project_id, "analysis/sizing_sweep_report.json")

    @app.get("/api/projects/{project_id}/mesh-convergence-report")
    def get_mesh_convergence_report_endpoint(project_id: str) -> dict[str, Any]:
        """Latest mesh-convergence report for the workbench panel (read-only).

        Returns the persisted ``analysis/mesh_convergence_report.json`` if present;
        ``available=false`` when the project has no package or no report yet.
        """
        return _load_latest_json_artifact(project_id, "analysis/mesh_convergence_report.json")

    @app.post("/api/projects/{project_id}/shape-ir-patch")
    def apply_shape_ir_patch_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Apply a Shape IR patch. Body: {patch: {...}, dry_run?: bool}."""
        p = payload or {}
        return _tool_aieng_apply_shape_ir_patch(
            {"project_id": project_id, "patch": p.get("patch"), "dry_run": bool(p.get("dry_run", False))},
            {},
        )

    @app.get("/api/projects/{project_id}/geometry-report")
    def get_geometry_report_endpoint(project_id: str) -> dict[str, Any]:
        """Geometry assembly-check report for the viewer overlay (read-only).

        Reuses cad_generation._compute_geometry_report (the same structural
        signals cad.design_review folds in) and adds a compact per-named-part
        bounding-box map (model frame, mm) so the frontend can draw floating /
        broken-symmetry boxes that line up with the rendered model. Empty or
        missing geometry is a valid 200 the viewer renders as "no assembly
        issues" — not a 404."""
        import json as _json
        import zipfile as _zipfile

        from ..cad_generation import _compute_geometry_report
        from ..project_io import get_project, resolve_project_path

        try:
            project = get_project(active_settings, project_id)
        except Exception:
            return {"available": False, "reason": "project_not_found"}
        pkg_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if pkg_path is None or not pkg_path.exists():
            return {"available": False, "reason": "no_package"}
        try:
            with _zipfile.ZipFile(pkg_path, "r") as zf:
                if "geometry/topology_map.json" not in zf.namelist():
                    return {"available": False, "reason": "no_topology"}
                topo = _json.loads(zf.read("geometry/topology_map.json").decode("utf-8"))
        except Exception:
            return {"available": False, "reason": "read_failed"}

        report = _compute_geometry_report(topo)
        part_boxes: dict[str, list[float]] = {}
        for ent in topo.get("entities", []):
            if ent.get("type") != "solid":
                continue
            name = ent.get("name")
            bbox = ent.get("bounding_box")
            if name and isinstance(bbox, list) and len(bbox) >= 6:
                part_boxes[str(name)] = [float(v) for v in bbox[:6]]
        return {
            "available": bool(report.get("available")),
            "units": report.get("units", "mm"),
            "floating_parts": report.get("floating_parts", []),
            "symmetry": report.get("symmetry", []),
            "gaps": report.get("gaps", []),
            "part_boxes": part_boxes,
        }

    @app.get("/api/projects/{project_id}/cae-setup-overlay")
    def get_cae_setup_overlay_endpoint(project_id: str) -> dict[str, Any]:
        """CAE setup visualization data for the 3D viewer (read-only).

        Resolves loads, constraints, and their target faces from the project's
        CAE setup artifacts and topology map. Returns face centroids/normals/bboxes
        in model-frame mm plus a stale-reference list so the viewer can flag
        unresolved faces honestly.
        """
        import json as _json
        import zipfile as _zipfile

        from ..agent_autopilot.simulation_readiness import load_simulation_setup
        from ..project_io import get_project, resolve_project_path, validate_cae_topology_references

        try:
            project = get_project(active_settings, project_id)
        except Exception:
            return {"available": False, "reason": "project_not_found"}
        pkg_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if pkg_path is None or not pkg_path.exists():
            return {"available": False, "reason": "no_package"}

        try:
            with _zipfile.ZipFile(pkg_path, "r") as zf:
                names = set(zf.namelist())
                topo = None
                if "geometry/topology_map.json" in names:
                    topo = _json.loads(zf.read("geometry/topology_map.json").decode("utf-8"))
                setup = load_simulation_setup(lambda name: zf.read(name).decode("utf-8") if name in names else None)
                mapping = None
                if "simulation/cae_mapping.json" in names:
                    mapping = _json.loads(zf.read("simulation/cae_mapping.json").decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": "read_failed", "error": str(exc)}

        if not setup or not isinstance(setup.get("data"), dict):
            return {"available": False, "reason": "no_setup"}

        validation = validate_cae_topology_references(pkg_path)
        missing_face_ids = set(validation.get("missing_face_ids") or [])
        stale_refs = validation.get("stale_references") or []

        face_index: dict[str, dict[str, Any]] = {}
        if isinstance(topo, dict):
            for ent in topo.get("entities", []) or []:
                if isinstance(ent, dict) and ent.get("type") == "face" and isinstance(ent.get("id"), str):
                    face_index[ent["id"]] = ent

        def _face_info(face_id: str) -> dict[str, Any] | None:
            f = face_index.get(face_id)
            if not isinstance(f, dict):
                return None
            return {
                "face_id": face_id,
                "center_mm": f.get("center"),
                "normal": f.get("normal"),
                "bounding_box_mm": f.get("bounding_box"),
                "surface_type": f.get("surface_type"),
                "stale": face_id in missing_face_ids,
            }

        def _collect_entities(items: list[Any], kind: str) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                face_ids = item.get("target_face_ids") or []
                faces = []
                for fid in face_ids:
                    info = _face_info(fid)
                    if info is not None:
                        faces.append(info)
                out.append({
                    "id": item.get("id"),
                    "type": item.get("type", kind),
                    "target_feature": item.get("target_feature"),
                    "target_pointers": item.get("target_pointers") or [],
                    "face_ids": face_ids,
                    "faces": faces,
                    "value_n": item.get("value_n") if kind == "load" else None,
                    "direction": item.get("direction") if kind == "load" else None,
                    "magnitude_n": item.get("value_n") if kind == "load" else None,
                })
            return out

        setup_data = setup["data"]
        return {
            "available": True,
            "project_id": project_id,
            "units": "mm",
            "setup_source": setup.get("setup_source"),
            "loads": _collect_entities(setup_data.get("loads"), "load"),
            "constraints": _collect_entities(setup_data.get("boundary_conditions"), "constraint"),
            "cae_mapping": mapping,
            "stale_references": stale_refs,
            "topology_hash_status": validation.get("hash_status"),
            "topology_stale": bool(validation.get("cae_mapping_stale")),
        }

    @app.get("/api/projects/{project_id}/edit-diff")
    def get_edit_diff_endpoint(project_id: str) -> dict[str, Any]:
        """The most recent edit's diff for the viewer (#226), read-only.

        Re-surfaces the `regression_diff` (topology drift) + `critique_diff`
        (manufacturability) verdicts persisted to `state/last_edit_diff.json` on
        each CAD mutation — the trust signal that otherwise only lives in the
        mutation tool's response (which the web viewer never sees). Missing /
        never-edited geometry is a valid 200 with `available: false`, not a 404."""
        import json as _json
        import zipfile as _zipfile

        from ..cad_generation import _LAST_EDIT_DIFF_MEMBER
        from ..project_io import get_project, resolve_project_path

        try:
            project = get_project(active_settings, project_id)
        except Exception:
            return {"available": False, "reason": "project_not_found"}
        pkg_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if pkg_path is None or not pkg_path.exists():
            return {"available": False, "reason": "no_package"}
        try:
            with _zipfile.ZipFile(pkg_path, "r") as zf:
                if _LAST_EDIT_DIFF_MEMBER not in zf.namelist():
                    return {"available": False, "reason": "no_edit_yet"}
                payload = _json.loads(zf.read(_LAST_EDIT_DIFF_MEMBER).decode("utf-8"))
        except Exception:
            return {"available": False, "reason": "read_failed"}
        return {
            "available": True,
            "tool": payload.get("tool"),
            "regression_diff": payload.get("regression_diff"),
            "critique_diff": payload.get("critique_diff"),
            "geometry_verification": payload.get("geometry_verification"),
        }

    @app.get("/api/projects/{project_id}/cae-result-map")
    def get_cae_result_map_endpoint(project_id: str) -> dict[str, Any]:
        """Build (and persist) the CAE -> Shape IR result map for a project."""
        import zipfile as _zipfile
        from aieng.converters.cae_result_map import build_cae_result_map_for_package, write_cae_result_map
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        with _zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            cae_sources = (
                "analysis/computed_metrics.json", "analysis/field_regions.json",
                "results/computed_metrics.json", "results/field_regions.json",
            )
            if not any(m in names for m in cae_sources):
                raise HTTPException(status_code=404, detail="no CAE results to map (run the solver + extract first)")
        try:
            return write_cae_result_map(package_path)
        except Exception:  # noqa: BLE001 - fall back to a non-persisted build
            log_exception(
                LOGGER,
                "Failed to persist CAE result map; falling back to non-persisted response.",
                subsystem="app_factory.cae_result_map.persist",
                context={"project_id": project_id},
            )
            return build_cae_result_map_for_package(package_path)

    @app.post("/api/projects/{project_id}/topology-optimization")
    def run_topology_optimization_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Run topology optimization. Body: {problem?, auto_derive?, optimizer?}."""
        p = payload or {}
        return _tool_opt_run_topology_optimization(
            {"project_id": project_id, "problem": p.get("problem"),
             "auto_derive": p.get("auto_derive"), "optimizer": p.get("optimizer")},
            {},
        )

    @app.post("/api/projects/{project_id}/topology-optimization/derive")
    def derive_topology_optimization_problem_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Derive a topopt problem from the project's CAE setup + geometry (read-only).
        Body: {resolution?, volfrac?, penalty?, rmin?, max_iters?}."""
        return _tool_opt_derive_problem_from_cae({"project_id": project_id, **(payload or {})}, {})

    @app.post("/api/projects/{project_id}/topology-optimization/writeback")
    def writeback_topology_optimization_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Author the topology-optimization result back into Shape IR + recompile.
        Body: {representation?, cell_size?, thickness?, origin?, node_id?}."""
        p = payload or {}
        return _tool_opt_writeback_to_shape_ir({"project_id": project_id, **p}, {})

    @app.post("/api/projects/{project_id}/topology-optimization/sizing")
    def topology_to_sizing_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Bridge a 2D contour topology writeback to a sizing study.
        Body: {}. Writes the full optimization-study envelope and a decision-log
        chain-linkage entry. Refuses 3D / voxel inputs with needs_user_input."""
        return _tool_opt_topology_to_sizing({"project_id": project_id, **(payload or {})}, {})

    @app.post("/api/projects/{project_id}/assembly/topology-optimization/run")
    def run_assembly_topology_optimization_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Explicitly run assembly-aware topopt for one selected design part.
        Body: {optimizer?, writeback?, method?, representation?, boundary?}.
        Does not run as part of /assembly/process."""
        p = payload or {}
        return _tool_opt_run_assembly_topology_optimization({"project_id": project_id, **p}, {})

    @app.post("/api/projects/{project_id}/assembly/process")
    def process_assembly_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Best-effort Assembly IR v0 processing: if the package carries
        assembly/assembly_ir.json, (re)write its validation + part registry +
        connection graph + solver-neutral CAE setup draft. No solver is run; no
        single-part geometry is touched. Returns {assembly_present: false} when the
        package has no assembly artifact."""
        from aieng.converters.assembly_ir import process_assembly_package
        from aieng.converters.assembly_interface_resolution import (
            resolve_and_validate_assembly_geometry,
        )
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        base = process_assembly_package(package_path)
        result = {"project_id": project_id, **base}
        if base.get("assembly_present"):
            geo = resolve_and_validate_assembly_geometry(package_path)
            result["interface_resolution"] = geo.get("resolution_summary")
            result["connection_geometry"] = geo.get("geometry_summary")
            result["assembly_cae_model_status"] = geo.get("assembly_cae_model_status")
            result["solver_deck_status"] = geo.get("solver_deck_status")
            result["solver_execution_status"] = geo.get("solver_execution_status")
            result["assembly_result_mapping_status"] = geo.get("assembly_result_mapping_status")
        return result

    @app.post("/api/projects/{project_id}/design-study/validate")
    def validate_design_study_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Best-effort design study v0 validation: if the package carries
        analysis/design_study_problem.json, validate the problem + any candidate
        patches under patches/design_candidates/ and write the diagnostics. Contract
        + validation ONLY — no patch is applied, no geometry recompiled, no CAE run,
        no optimization executed. Returns {design_study_present: false} when absent."""
        from aieng.converters.design_study import process_design_study_package
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return {"project_id": project_id, **process_design_study_package(package_path)}

    @app.post("/api/projects/{project_id}/design-study/candidates/{candidate_id}/run")
    def run_design_study_candidate_endpoint(
        project_id: str,
        candidate_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """EXPLICITLY execute ONE validated design-study candidate into a derived workspace
        (candidates/<id>/...). Applies the patch to a DERIVED Shape IR only; the baseline is
        never overwritten and no candidate is auto-promoted. Compiles the candidate in a
        throwaway copy when ``compile`` is enabled (default true); records the iteration in
        analysis/design_study_iterations.json + diagnostics/design_study_report.json. No
        optimizer/search/loop and no CAE are run."""
        from aieng.converters.design_study_execution import execute_design_study_candidate
        from ..cad_generation import make_candidate_recompiler
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        do_compile = (payload or {}).get("compile", True)
        recompiler = make_candidate_recompiler(package_path) if do_compile else None
        return {"project_id": project_id,
                **execute_design_study_candidate(package_path, candidate_id, recompiler=recompiler)}

    @app.post("/api/projects/{project_id}/design-study/run-candidates")
    def run_design_study_candidates_batch_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """EXPLICITLY execute a fixed set of design-study candidates, each into its own
        derived workspace (candidates/<cid>/...). Discovers the candidate set from the
        package (optimization_study/variables candidate_ids + candidate patches on disk),
        or runs an explicit ``candidate_ids`` list. Each patch is applied to a DERIVED
        Shape IR only; the baseline is never overwritten and no candidate is auto-promoted.
        Compiles each candidate in a throwaway copy when ``compile`` is enabled (default
        true). A failed candidate is recorded cleanly and the batch CONTINUES. No
        optimizer/search/loop and no CAE are run.

        Body (all optional):
          candidate_ids (list[str]): explicit ids to run (default: all discovered)
          compile (bool): recompile each candidate (default true)
          max_candidates (int): cap executed this call; remainder reported as skipped
        """
        from aieng.converters.design_study_batch import run_design_study_batch
        from ..cad_generation import make_candidate_recompiler
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        do_compile = data.get("compile", True)
        recompiler = make_candidate_recompiler(package_path) if do_compile else None
        return {
            "project_id": project_id,
            **run_design_study_batch(
                package_path,
                candidate_ids=data.get("candidate_ids"),
                recompiler=recompiler,
                max_candidates=data.get("max_candidates"),
            ),
        }

    @app.post("/api/projects/{project_id}/design-study/evaluate-candidates")
    def evaluate_design_study_candidates_batch_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Evaluate a set of executed design-study candidates from candidate-local evidence.

        For each candidate, normalizes mass / volume / max_stress / max_deflection /
        min_safety_factor, evaluates declared constraints, and classifies feasibility.
        Missing CAE metrics are recorded honestly as unknown — never fabricated. With
        ``cae`` true, first derives each candidate's CAE setup and normalizes any CAE
        evidence (solver execution stays disabled by default). Writes
        candidates/<cid>/analysis/evaluation.json per candidate. Does NOT accept/promote
        a candidate, run an optimizer loop, or modify the baseline.

        Body (all optional):
          candidate_ids (list[str]): explicit ids (default: all executed)
          cae (bool): run candidate-local CAE evaluation first (default false)
          mode (str): CAE evaluation mode (when cae=true)
          allow_solver_execution (bool): permit solver (best-effort/skipped in v0)
          max_candidates (int): cap evaluated this call; remainder reported as skipped
        """
        from aieng.converters.design_study_batch import run_design_study_evaluation_batch
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        cae_options: dict[str, Any] = {}
        if data.get("cae"):
            if data.get("mode") is not None:
                cae_options["mode"] = data.get("mode")
            if data.get("allow_solver_execution") is not None:
                cae_options["allow_solver_execution"] = bool(data.get("allow_solver_execution"))
        return {
            "project_id": project_id,
            **run_design_study_evaluation_batch(
                package_path,
                candidate_ids=data.get("candidate_ids"),
                cae=bool(data.get("cae", False)),
                cae_options=cae_options or None,
                max_candidates=data.get("max_candidates"),
            ),
        }

    @app.post("/api/projects/{project_id}/design-study/candidates/{candidate_id}/evaluate")
    def evaluate_design_study_candidate_endpoint(
        project_id: str,
        candidate_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Evaluate ONE candidate from candidate-local solver-neutral/static evidence.

        Writes ``candidates/<id>/analysis/evaluation.json`` and
        ``candidates/<id>/diagnostics/evaluation_report.json``. This is
        backend-only post-processing: it does NOT run CAE, does NOT recompile
        geometry, does NOT apply/promote candidates, and does NOT overwrite
        baseline geometry.
        """
        from aieng.converters.design_study_evaluation import evaluate_design_study_candidate
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return {"project_id": project_id, **evaluate_design_study_candidate(package_path, candidate_id)}

    @app.post("/api/projects/{project_id}/design-study/rank")
    def rank_design_study_candidates_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Rank already-executed design-study candidates.

        Reads analysis/design_study_iterations.json + per-candidate evaluation artifacts,
        classifies feasibility, scores against the problem objective and constraints,
        and writes analysis/design_study_candidate_ranking.json +
        diagnostics/design_study_scoring_report.json.

        Does NOT execute new candidates, does NOT recompile geometry, does NOT run CAE,
        and does NOT modify baseline geometry. Ranking is advisory only."""
        from aieng.converters.design_study_ranking import rank_design_study_candidates
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return {"project_id": project_id, **rank_design_study_candidates(package_path)}

    @app.post("/api/projects/{project_id}/design-study/recommendation")
    def explain_design_study_recommendation_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Explain the candidate ranking as an advisory, reason-coded recommendation.

        Reads analysis/design_study_candidate_ranking.json (run the rank endpoint
        first) and writes analysis/optimization_recommendation.json: why the top
        candidate is recommended (objective delta + metrics), or why none is, plus
        explicit caveats for missing CAE metrics / low confidence. Advisory only and
        human-approval-gated for acceptance — does NOT accept/promote a candidate, run
        CAE, or modify the baseline."""
        from aieng.converters.optimization_recommendation import explain_recommendation
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return {"project_id": project_id, **explain_recommendation(package_path)}

    @app.post("/api/projects/{project_id}/design-study/report")
    def build_optimization_report_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Aggregate the optimization study into a single reproducible summary report.

        Reads existing package artifacts (problem, variables/objectives/constraints,
        all candidates + metrics, ranking, failed candidates, recommendation, acceptance,
        decision log) and writes diagnostics/optimization_report.json. Read-only with
        respect to engineering state: does NOT execute/evaluate/rank/accept candidates,
        run CAE, or modify the baseline."""
        from aieng.converters.optimization_report import build_optimization_report
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return {"project_id": project_id, **build_optimization_report(package_path)}

    @app.post("/api/projects/{project_id}/design-study/propose-next")
    def propose_next_candidates_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Propose the next batch of candidates by local refinement.

        Reads the ranking incumbent + optimization variables and samples within a
        shrinking trust region (default), runs an SLSQP local step, or uses a Bayesian
        surrogate.  Falls back to whole-domain LHS when no feasible incumbent.  Deterministic given a seed. Does
        NOT run/evaluate/accept candidates, run CAE, or modify the baseline.

        Body (all optional):
          algorithm (str): "trust_region" | "slsqp" | "bayesian" | "genetic" (default "trust_region")
          count (int), shrink (float 0-1), seed (int)."""
        from aieng.converters.optimization_proposer import propose_next_candidates
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        algorithm = str(data.get("algorithm") or "trust_region")
        if algorithm not in {"trust_region", "slsqp", "bayesian", "genetic"}:
            raise HTTPException(
                status_code=422,
                detail="algorithm must be one of: trust_region, slsqp, bayesian, genetic",
            )
        return {
            "project_id": project_id,
            **propose_next_candidates(
                package_path,
                count=int(data.get("count", 4)),
                shrink=float(data.get("shrink", 0.5)),
                seed=int(data.get("seed", 0)),
                algorithm=algorithm,
            ),
        }

    @app.post("/api/projects/{project_id}/design-study/check-convergence")
    def check_convergence_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Record the current iteration's incumbent and return a convergence verdict.

        Snapshots the ranking incumbent into analysis/optimization_iterations.json and
        evaluates the deterministic, direction-aware stopping criteria. Advisory only —
        it tells the agent whether to stop; it never accepts a candidate, runs CAE, or
        modifies the baseline.

        Body (all optional): evaluations_total (int), failures_this_round (int),
        had_success (bool), proposer_exhausted (bool)."""
        from aieng.converters.optimization_convergence import record_iteration_and_check
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        return {
            "project_id": project_id,
            **record_iteration_and_check(
                package_path,
                evaluations_total=data.get("evaluations_total"),
                failures_this_round=int(data.get("failures_this_round", 0)),
                had_success=bool(data.get("had_success", True)),
                proposer_exhausted=bool(data.get("proposer_exhausted", False)),
            ),
        }

    @app.post("/api/projects/{project_id}/design-study/select-optimizer")
    def select_optimizer_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Deterministically select an optimizer for the design study.

        Reads ``analysis/optimization_variables.json`` and
        ``analysis/optimization_study.json``, applies the selection policy, appends
        one reason-coded entry to ``analysis/optimization_decision_log.json``, and
        returns the chosen optimizer. Body (optional): optimizer (str) to override.

        No search runs inside the call; the baseline is never modified.
        """
        from aieng.converters.optimizer_selector import select_optimizer
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        return {
            "project_id": project_id,
            **select_optimizer(
                package_path,
                user_selected=data.get("optimizer") if data.get("optimizer") else None,
            ),
        }

    @app.post("/api/projects/{project_id}/design-study/hints")
    def build_design_study_candidate_hints_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Build advisory candidate proposal hints from existing design-study evidence.

        Explicit backend-only decision support. Writes
        ``analysis/design_study_candidate_hints.json`` and
        ``diagnostics/design_study_candidate_hints_report.json``. It does NOT
        generate candidate patches, execute candidates, run CAE, rank/accept
        candidates, mutate geometry, or overwrite baseline artifacts.
        """
        from aieng.converters.design_study_hints import build_design_study_candidate_hints
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        return {
            "project_id": project_id,
            **build_design_study_candidate_hints(package_path, max_hints=int(data.get("max_hints", 10))),
        }

    @app.post("/api/projects/{project_id}/design-study/candidates/{candidate_id}/accept")
    def accept_design_study_candidate_endpoint(
        project_id: str,
        candidate_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Explicitly accept ONE ranked design-study candidate into a derived accepted workspace.

        Copies the candidate's derived artifacts into ``accepted/<candidate_id>/`` and writes
        ``analysis/design_study_acceptance.json`` + ``diagnostics/design_study_acceptance_report.json``.

        The candidate must be eligible (feasible, not failed/unknown, and safe_to_accept or
        best_candidate_id unless override_unsafe is explicitly set in the payload).

        Does NOT overwrite baseline geometry. Does NOT auto-promote. Production approval is
        NOT claimed."""
        from aieng.converters.design_study_acceptance import accept_design_study_candidate
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        return {
            "project_id": project_id,
            **accept_design_study_candidate(
                package_path,
                candidate_id,
                accepted_by=data.get("accepted_by", "agent"),
                reasoning=data.get("reasoning"),
                override_unsafe=bool(data.get("override_unsafe", False)),
            ),
        }

    @app.post("/api/projects/{project_id}/design-study/sample")
    def sample_design_study_candidates_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Generate candidate parameter sets from optimization variables.

        Reads ``analysis/optimization_variables.json`` and optionally
        ``analysis/optimization_study.json`` from the project's .aieng package,
        runs the requested sampler (grid / random / latin_hypercube), and writes
        candidate patches to ``patches/design_candidates/<cid>.json``.

        Body (all optional, overrides study config when provided):
          algorithm (str): grid | random | latin_hypercube | lhs
          count (int): number of candidates (for random/LHS)
          seed (int): random seed for reproducibility
          max_candidates (int): hard cap (default 50)
          overwrite (bool): overwrite existing candidates (default false)

        Does NOT execute candidates, recompile geometry, run CAE, or modify baseline.
        """
        from aieng.converters.optimization_sampler import sample_candidates_package
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        return {
            "project_id": project_id,
            **sample_candidates_package(
                package_path,
                algorithm=data.get("algorithm"),
                count=data.get("count"),
                seed=data.get("seed"),
                max_candidates=data.get("max_candidates"),
                overwrite=bool(data.get("overwrite", False)),
            ),
        }

    @app.post("/api/projects/{project_id}/design-study/candidates/{candidate_id}/cae-evaluate")
    def cae_evaluate_design_study_candidate_endpoint(
        project_id: str,
        candidate_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Explicitly request CAE evaluation for ONE design-study candidate.

        Derives candidate-local CAE setup from the baseline, normalizes existing
        candidate-local neutral metrics into ``candidates/<id>/analysis/evaluation.json``,
        and optionally refreshes ranking. Solver execution is disabled by default.

        Writes:
          - ``candidates/<id>/analysis/cae_evaluation_request.json``
          - ``candidates/<id>/diagnostics/cae_evaluation_request.json``
          - ``candidates/<id>/simulation/setup.yaml`` (copied from baseline)
          - ``candidates/<id>/simulation/cae_mapping.json`` (copied from baseline)
          - ``candidates/<id>/analysis/evaluation.json`` (refreshed)
          - ``candidates/<id>/diagnostics/evaluation_report.json`` (refreshed)

        Does NOT overwrite baseline geometry or baseline CAE artifacts. Does NOT
        auto-accept or auto-promote candidates. Does NOT run unbounded iterations.
        """
        from aieng.converters.design_study_cae_evaluation import (
            request_design_study_candidate_cae_evaluation,
        )
        from ..project_io import get_project, resolve_project_path

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        data = payload or {}
        allow_solver = bool(data.get("allow_solver_execution", False))
        # When solver execution is explicitly requested, inject the real candidate
        # solver (compile candidate geometry + Gmsh + CalculiX). It degrades honestly
        # if tools are unavailable / topology is stale. Default stays solver-free.
        solver_fn = None
        if allow_solver:
            from ..candidate_solver import solve_candidate_geometry

            def solver_fn(pkg, cid):  # noqa: ANN001
                return solve_candidate_geometry(
                    pkg, cid,
                    timeout=int(data.get("timeout", 180)),
                    mesh_size_mm=data.get("mesh_size_mm"),
                )

        return {
            "project_id": project_id,
            **request_design_study_candidate_cae_evaluation(
                package_path,
                candidate_id,
                mode=data.get("mode", "prepare_only"),
                allow_solver_execution=allow_solver,
                allow_solver_deck_generation=bool(data.get("allow_solver_deck_generation", True)),
                allow_ranking_refresh=bool(data.get("allow_ranking_refresh", False)),
                requested_by=data.get("requested_by", "agent"),
                load_case_ids=data.get("load_case_ids"),
                constraints_to_evaluate=data.get("constraints_to_evaluate"),
                solver_fn=solver_fn,
            ),
        }

    @app.post("/api/projects/{project_id}/brep/pick-face")
    def pick_face_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Pick the closest B-Rep face to a 3D point from the viewer.

        Body: { x: float, y: float, z: float }
        Returns the best-matching face pointer, surface type, center, normal,
        and a human-readable label. Returns 404 if no B-Rep graph is available.
        """
        from .. import brep_graph
        from ..project_io import get_project, resolve_project_path

        data = payload or {}
        px = float(data.get("x", 0))
        py = float(data.get("y", 0))
        pz = float(data.get("z", 0))

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        result = brep_graph.pick_face_at_point(package_path, px, py, pz)
        if result is None:
            raise HTTPException(status_code=404, detail="No B-Rep face graph available")
        return {
            "project_id": project_id,
            "pick_point": {"x": px, "y": py, "z": pz},
            **result,
        }

    @app.post("/api/projects/{project_id}/ai-preprocessing")
    def ai_preprocessing_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """AI-driven FEA preprocessing setup generator.

        Reads geometry from the project's .aieng package, calls Claude to decide
        material, boundary conditions, loads, and mesh strategy, then writes
        simulation/setup.yaml and simulation/cae_mapping.json into the package.

        Body:
          task_description (str, required): natural-language description of the
            load case and support conditions, e.g. "Bracket bolted at 4 corner
            holes, 500 N downward load at the end face."
          material_hint (str, optional): preferred material name or description.
          mesh_hint (str, optional): "coarse", "medium", or "fine".
          write_files (bool, optional): write artifacts to package (default true).
            Pass false to get a dry-run preview without mutating the package.
        """
        from .. import ai_preprocessing

        data = payload or {}
        resolved_key = _resolve_api_key(data)
        if resolved_key:
            data = {**data, "api_key": resolved_key}
        if isinstance(data.get("llm_config"), dict):
            data = {**data, "llm_config": agent_engine.sanitize_llm_config(data.get("llm_config"))}
        return ai_preprocessing.run_ai_preprocessing(
            active_settings, project_id, data
        )

    @app.post("/api/projects/{project_id}/run-simulation")
    def run_simulation_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Mesh with Gmsh + solve with CalculiX from AI preprocessing output.

        Requires confirmed=true in the request body — this runs external processes.
        Returns gracefully if Gmsh or CalculiX are not installed.

        Body:
          confirmed (bool, required): must be true to execute.
          timeout_s (int, optional): CalculiX timeout in seconds (default 180).

        Prerequisites: the package must contain simulation/setup.yaml
        (from ai-preprocessing) and geometry/generated.step (from generate-cad).
        """
        from .. import simulation_runner

        return simulation_runner.run_simulation(
            active_settings, project_id, payload or {}
        )

    @app.get("/api/simulation/tools")
    def get_simulation_tools() -> dict[str, Any]:
        """Check whether Gmsh and CalculiX are available on this host."""
        from .. import simulation_runner

        return simulation_runner.check_simulation_tools()

    @app.post("/api/projects/{project_id}/run-simulation-stream")
    def run_simulation_stream_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ):
        """Mesh + solve with streaming SSE progress events.

        Yields server-sent events: checking_tools → meshing → building_nsets →
        solving → parsing → done (with full result) | error.
        Requires confirmed=true in the request body.
        """
        from fastapi.responses import StreamingResponse
        from .. import simulation_runner

        def generate():
            yield from simulation_runner.run_simulation_stream(
                active_settings, project_id, payload or {}
            )

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/projects/{project_id}/bom")
    def bom_endpoint(project_id: str) -> dict[str, Any]:
        """Return the project's Bill of Materials in the frontend ``BOMData`` shape.

        Backed by the shared ``generate_bom`` recognizer; the BOM panel renders
        this and exports CSV/JSON client-side. 404 when the project has no
        ``.aieng`` package.
        """
        from datetime import datetime, timezone
        from .. import standards_bridge

        result = standards_bridge.generate_bom(active_settings, project_id, None)
        if result.get("status") != "ok":
            status = 404 if result.get("code") == "missing_package" else 400
            raise HTTPException(status_code=status, detail=result.get("message", "BOM generation failed"))
        generated_at = datetime.now(timezone.utc).isoformat()
        return standards_bridge.to_bom_frontend_payload(result, project_id, generated_at)

    @app.get("/api/projects/{project_id}/mesh-preview")
    def mesh_preview_endpoint(project_id: str) -> dict[str, Any]:
        """Return surface wireframe + element stats for the project's FE mesh.

        Reads ``simulation/mesh.inp`` from the .aieng package (written by the
        solver runner). Returns ``{available: false}`` when no mesh exists so the
        viewer can degrade cleanly.
        """
        from ..project_io import get_project, resolve_project_path
        from .. import simulation_runner

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        return simulation_runner.get_mesh_preview(package_path)

    @app.get("/api/projects/{project_id}/mesh-diagnostics")
    def mesh_diagnostics_endpoint(project_id: str) -> dict[str, Any]:
        """Return an element-quality verdict for the project's FE mesh (#279).

        Reads ``simulation/mesh.inp`` and reports degenerate / sliver / high-aspect
        tetrahedra with an ok/warning/fail verdict. ``{available: false}`` when no
        mesh exists. Honest boundary: heuristic tet quality, not a Jacobian measure.
        """
        from ..project_io import get_project, resolve_project_path
        from .. import simulation_runner

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        return simulation_runner.get_mesh_quality_diagnostics(package_path)

    @app.post("/api/projects/{project_id}/chat-set-target")
    def chat_set_target_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Parse a natural-language message and upsert a design target into design_targets.yaml.

        Body: { message: str }
        Example: {"message": "set max stress to 250 MPa"}
        """
        from .. import design_target_chat

        p = payload or {}
        return design_target_chat.add_target_from_chat(
            settings=active_settings,
            project_id=project_id,
            text=str(p.get("message") or ""),
        )

    @app.post("/api/projects/{project_id}/contextual-chat")
    def contextual_chat_endpoint(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Context-aware engineering chat grounded in the current project state.

        Injects geometry summary, simulation results, verdict, and design targets
        into the system prompt so the LLM can answer engineering questions accurately.
        Body: { message: str, history?: [{role, content}], api_key?: str }
        """
        from .. import contextual_chat, db

        p = payload or {}
        message = str(p.get("message") or "").strip()
        session_id = str(p.get("session_id") or "").strip() or None
        if session_id is None:
            session_id = db.ensure_default_chat_session(db_path, project_id)["id"]
        if message:
            _add_chat_message_and_publish(
                project_id=project_id,
                session_id=session_id,
                role="user",
                content=message,
            )
        result = contextual_chat.chat_with_context(
            settings=active_settings,
            project_id=project_id,
            message=message,
            history=list(p.get("history") or []),
            api_key=_resolve_api_key(p),
        )
        reply = result.get("reply", "")
        if reply:
            _add_chat_message_and_publish(
                project_id=project_id,
                session_id=session_id,
                role="assistant",
                content=reply,
            )
        return result
