"""opt runtime tool registrations.

Extracted from runtime_tool_registry.py to keep domain logic focused.
"""

from __future__ import annotations

import json
import logging
from typing import Any
import zipfile

from ..legacy_app_symbols import sync_main_symbols

LOGGER = logging.getLogger("app.app_factory")

# Mesh payload fields an agent cannot act on inline. Every one of them has a
# scalar counterpart alongside it (`vertex_count`, `triangle_count`, `bbox`), and
# the full node is written to geometry/shape_ir.json regardless.
_BULK_SHAPE_IR_FIELDS = ("vertices", "faces", "triangles", "density", "density_3d",
                         "density_voxels", "boundary", "boundary_points", "polygon")


def _summarize_shape_ir_payload(payload: Any) -> Any:
    """The Shape IR writeback payload with its bulk arrays replaced by their size.

    Measured on a 3D writeback: the response was 30,118 chars (~7.5k tokens),
    27,544 of them raw vertex and face arrays — beside the `vertex_count`,
    `triangle_count`, `bbox` and honesty tags that say the same thing in forty.
    """
    if not isinstance(payload, dict):
        return payload
    parts = payload.get("parts")
    if not isinstance(parts, list):
        # An unexpected shape is still the caller's data — summarizing must never
        # be the thing that loses it.
        return payload

    summarized: list[Any] = []
    for part in parts:
        if not isinstance(part, dict):
            summarized.append(part)
            continue
        kept = {k: v for k, v in part.items() if k not in _BULK_SHAPE_IR_FIELDS}
        omitted = {k: len(part[k]) for k in _BULK_SHAPE_IR_FIELDS
                   if isinstance(part.get(k), list)}
        if omitted:
            kept["omitted_arrays"] = omitted
            kept["omitted_arrays_note"] = (
                "sizes only — the full geometry is in geometry/shape_ir.json")
        summarized.append(kept)
    return {**payload, "parts": summarized}


def register_opt_tools(rt: Any, active_settings: Any, app_context: Any, _schema: Any) -> dict[str, Any]:
    """Register opt runtime tools."""
    sync_main_symbols(globals())
    _delete_project_everywhere = app_context.delete_project_everywhere
    _load_project_feature_parameters = app_context.load_project_feature_parameters

    def _tool_opt_derive_problem_from_cae(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Derive a 2D topology-optimization problem (grid + supports + loads + design
        space) from a project's CAE setup + geometry. Read-only (no mutation)."""
        from aieng.converters.topology_optimization import derive_topopt_problem_from_package
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        kw: dict[str, Any] = {"dimension": str(inp.get("dimension") or "2d").lower()}
        for k in ("resolution", "resolution_3d", "max_iters"):
            if inp.get(k) is not None:
                kw[k] = int(inp[k])
        for k in ("volfrac", "penalty", "rmin"):
            if inp.get(k) is not None:
                kw[k] = float(inp[k])
        if inp.get("design_space_node"):
            kw["design_space_node"] = str(inp["design_space_node"])
        try:
            problem = derive_topopt_problem_from_package(pkg, **kw)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "derive_failed", "message": f"{type(exc).__name__}: {exc}"}
        # 3D derivation may honestly decline to guess BCs.
        if problem.get("status") == "needs_user_input":
            return {"status": "needs_user_input", "tool": "opt.derive_problem_from_cae",
                    "problem": problem,
                    # `reason` carries the explanation; `diagnostics` is often
                    # empty, so returning only that reported a refusal with no
                    # stated cause.
                    "reason": problem.get("reason"),
                    "recommendation": problem.get("recommendation"),
                    "diagnostics": problem.get("diagnostics"),
                    # The way out of a refusal the default design space caused.
                    "design_space_candidates": problem.get("design_space_candidates")}
        return {"status": "ok", "tool": "opt.derive_problem_from_cae", "problem": problem,
                "derivation": problem.get("derivation")}

    def _tool_opt_run_topology_optimization(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Run topology optimization (built-in 2D SIMP) on a project's design space;
        write analysis/topology_optimization.json. No external solver. If auto_derive
        is set (or no problem is given), the problem is derived from the project's CAE
        setup + geometry first."""
        from aieng.converters.topology_optimization import (
            TopologyProblemRefused,
            derive_topopt_problem_from_package,
            write_topology_optimization,
        )
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        problem = inp.get("problem") if isinstance(inp.get("problem"), dict) else None
        auto_derive = bool(inp.get("auto_derive")) or problem is None
        dimension = str(inp.get("dimension") or "2d").lower()
        # An explicit 3D optimizer implies 3D derivation.
        if str(inp.get("optimizer") or "").lower() == "simp_3d":
            dimension = "3d"
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        optimizer = str(inp.get("optimizer") or ("simp_3d" if dimension == "3d" else "simp_2d"))
        try:
            if auto_derive:
                derive_kw: dict[str, Any] = {"dimension": dimension}
                # The selector must be resolved BEFORE deriving: `problem` is
                # merged in afterwards, so a design_space_node passed there would
                # arrive too late to choose the bounds and the caller would
                # silently get the default largest solid instead.
                selector = inp.get("design_space_node") or (
                    problem.get("design_space_node") if isinstance(problem, dict) else None)
                if selector:
                    derive_kw["design_space_node"] = str(selector)
                derived = derive_topopt_problem_from_package(pkg, **derive_kw)
                # The derivation may honestly decline to guess BCs — surface it, don't solve.
                if derived.get("status") == "needs_user_input":
                    return {"status": "needs_user_input", "tool": "opt.run_topology_optimization",
                            "problem": derived,
                            "reason": derived.get("reason"),
                            "recommendation": derived.get("recommendation"),
                            "diagnostics": derived.get("diagnostics"),
                            "design_space_candidates": derived.get("design_space_candidates")}
                if isinstance(problem, dict):  # caller overrides (volfrac, grid, ...) win
                    derived.update({k: v for k, v in problem.items() if k != "bcs"})
                    if isinstance(problem.get("bcs"), dict):
                        derived.setdefault("bcs", {}).update(problem["bcs"])
                problem = derived
            result = write_topology_optimization(pkg, problem, optimizer=optimizer)
        except TopologyProblemRefused as exc:
            # A caller-supplied problem that is really a derivation refusal. The
            # auto_derive branch above already surfaces this; a passed-in problem
            # went straight to the optimizer, which substituted a preset.
            # Same top-level fields as the auto_derive refusal above: a caller
            # should not have to parse prose, or reach into `problem`, just
            # because it supplied the problem itself.
            refused = problem or {}
            return {"status": "needs_user_input", "tool": "opt.run_topology_optimization",
                    "code": "problem_refused", "message": f"{exc}",
                    "problem": problem,
                    "reason": refused.get("reason"),
                    "recommendation": refused.get("recommendation"),
                    "diagnostics": refused.get("diagnostics"),
                    "design_space_candidates": refused.get("design_space_candidates")}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "optimization_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": "ok", "tool": "opt.run_topology_optimization", "topology_optimization": result}

    rt.register_tool(
        "opt.run_topology_optimization",
        _tool_opt_run_topology_optimization,
        description=(
            "Run topology optimization on a project's design space using the built-in "
            "self-contained 2D SIMP (compliance minimization, pure numpy — no external "
            "solver). Writes analysis/topology_optimization.json (optimizer provenance, "
            "objective history, achieved volume fraction, density grid, honest 2D/coarse "
            "limitations). Optimizer is pluggable; optimizer=precomputed accepts a density grid. "
            "Set auto_derive=true (or omit problem) to derive supports/loads/design-space from "
            "the project's CAE setup + geometry instead of a preset."
        ),
        input_schema=_schema("opt.run_topology_optimization"),
    )

    rt.register_tool(
        "opt.derive_problem_from_cae",
        _tool_opt_derive_problem_from_cae,
        description=(
            "Derive a 2D topology-optimization problem (grid + supports + loads + design "
            "space) from a project's CAE setup (simulation/setup.yaml supports/loads) + "
            "geometry (topology_map faces + design-space bbox). Read-only — returns the "
            "problem + a 'derivation' block (projection plane, frame, source BC links, "
            "warnings). 3D supports/loads are projected onto the plane of the two largest "
            "design-space dimensions; out-of-plane components are dropped (plane-stress). "
            "Inspect this, then pass it to opt.run_topology_optimization."
        ),
        input_schema=_schema("opt.derive_problem_from_cae"),
    )

    def _tool_opt_writeback_to_shape_ir(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Author a topology-optimization result back into the project's Shape IR
        (one density_voxels node) and recompile through runtime routing — so the
        optimized body meshes/views and gets verification + object_registry."""
        from aieng.converters.topology_optimization import write_shape_ir_from_topology_optimization
        from .. import cad_generation as _cad_generation
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}

        # Detect a 3D result up front — it drives both the representation and method
        # defaults (3D is a mesh preview, not a B-Rep / contour body).
        _dim3 = False
        try:
            import zipfile as _zfd
            with _zfd.ZipFile(pkg, "r") as _z:
                if "analysis/topology_optimization.json" in _z.namelist():
                    _topo = json.loads(_z.read("analysis/topology_optimization.json"))
                    _dim3 = _topo.get("dimension") == "3d" or "density_grid_3d" in (_topo.get("result") or {})
        except Exception:
            log_exception(
                LOGGER,
                "Failed to inspect topology optimization artifact; defaulting to 2D writeback mode.",
                subsystem="app_factory.topology_writeback.dimension_detect",
                context={"project_id": pid},
            )
            _dim3 = False
        # Default to B-Rep for 2D (analytic faces an engineer picks / exports to STEP;
        # manifold_mesh is the robust fallback, auto-used if the B-Rep build fails).
        # 3D results default to manifold_mesh — a B-Rep Compound of hundreds of voxel
        # boxes is heavy and beside the point; 3D is an explicitly meshed preview.
        representation = str(inp.get("representation") or ("manifold_mesh" if _dim3 else "brep_build123d"))
        # The optimized body is placed in the design space's derivation frame;
        # cell_size/thickness/origin are honored only if the caller overrides.
        cs = inp.get("cell_size")
        cell_size = (float(cs[0]), float(cs[1] if len(cs) > 1 else cs[0])) if cs else None
        thickness = inp.get("thickness")
        org = inp.get("origin")
        origin = (float(org[0]), float(org[1]), float(org[2])) if org else None
        # 2D default: contour; 3D default: surface (smooth marching-cubes proxy).
        method = str(inp.get("method") or ("surface" if _dim3 else "contour")).lower()
        boundary = str(inp.get("boundary") or "spline").lower()
        timeout = int(inp.get("timeout") or 120)

        def _writeback(rep: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
            pay = write_shape_ir_from_topology_optimization(
                pkg, representation=rep, cell_size=cell_size,
                thickness=(float(thickness) if thickness is not None else None),
                origin=origin,
                node_id=(str(inp["node_id"]) if inp.get("node_id") else None),
                use_frame=bool(inp.get("use_frame", True)),
                method=method, boundary=boundary,
            )
            rc = _cad_generation.recompile_shape_ir_package(pkg, timeout=timeout)
            return pay, rc

        try:
            payload, recompile = _writeback(representation)
            # Safety net: if a non-mesh representation didn't actually execute, retry
            # once as a watertight mesh so the writeback still yields a viewable body.
            if representation != "manifold_mesh" and not (recompile or {}).get("executed", False):
                payload, recompile = _writeback("manifold_mesh")
                payload.setdefault("provenance", {})["representation_fallback"] = (
                    f"{representation} did not execute; fell back to manifold_mesh")
        except FileNotFoundError as exc:
            return {"status": "error", "code": "no_topology_optimization", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            # B-Rep build raised — fall back to mesh once before giving up.
            if representation != "manifold_mesh":
                try:
                    payload, recompile = _writeback("manifold_mesh")
                    payload.setdefault("provenance", {})["representation_fallback"] = (
                        f"{representation} failed ({type(exc).__name__}); fell back to manifold_mesh")
                except Exception as exc2:  # noqa: BLE001
                    return {"status": "error", "code": "writeback_failed",
                            "message": f"{type(exc2).__name__}: {exc2}"}
            else:
                return {"status": "error", "code": "writeback_failed", "message": f"{type(exc).__name__}: {exc}"}

        # Publish the recompiled preview to viewer/model.* + set web_asset so the UI
        # viewer actually shows the optimized body (the frontend's default viewer URL
        # resolves to /assets/projects/{id}/{web_asset}; recompile only refreshes the
        # in-package preview, not the on-disk viewer asset). Mirrors cad.execute_build123d.
        try:
            import zipfile as _zf
            glb_bytes = stl_bytes = None
            with _zf.ZipFile(pkg, "r") as zf:
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
                "Failed to persist project metadata after topology optimization writeback.",
                subsystem="app_factory.topology_writeback.project_update",
                context={"project_id": pid},
            )

        # This tool REPLACES the project's geometry with the optimized body, so
        # every downstream CAE artifact is stale — exactly what cad.edit_parameter
        # records. Without it `state/revalidation_status.json` kept saying
        # `geometry_modified: false` from the previous solver run, agent_context
        # showed no EDIT IMPACT, and the solver was blocked only because the old
        # face ids happened to vanish: safety by accident, not by rule.
        geometry_revision = None
        try:
            from ..project_io import _record_geometry_edit_in_package

            geometry_revision = _record_geometry_edit_in_package(
                pkg, triggering_tool="opt.writeback_to_shape_ir", affected_artifacts=[
                "results/computed_metrics.json",
                "results/result_summary.json",
                "simulation/mesh/mesh_metadata.json",
                "simulation/cae_mapping.json",
                "cae/*",
                "analysis/field_regions.json",
            ])
        except Exception:
            log_exception(
                LOGGER,
                "Failed to record the geometry edit after topology optimization writeback.",
                subsystem="app_factory.topology_writeback.revalidation",
                context={"project_id": pid},
            )

        return {
            "status": "ok",
            "tool": "opt.writeback_to_shape_ir",
            # A summary, not the mesh: the full node is in geometry/shape_ir.json.
            # Returning it inline cost ~7.5k tokens per call, 91% of it vertex and
            # face arrays an agent cannot act on — next to the counts and bbox that
            # say the same thing.
            "shape_ir": _summarize_shape_ir_payload(payload),
            "recompile": recompile,
            "geometry_revision": geometry_revision,
            "downstream_evidence": "stale — geometry was replaced by the optimized body",
        }

    rt.register_tool(
        "opt.writeback_to_shape_ir",
        _tool_opt_writeback_to_shape_ir,
        description=(
            "Author the project's topology-optimization result (analysis/"
            "topology_optimization.json) back into geometry/shape_ir.json as one "
            "density_voxels node, then recompile through runtime routing. The optimized "
            "density field becomes re-compilable, viewable geometry (default representation "
            "manifold_mesh -> watertight voxel mesh; brep_build123d also supported) with "
            "topology + verification + object_registry, linked to its design_space_node. "
            "Run opt.run_topology_optimization first."
        ),
        input_schema=_schema("opt.writeback_to_shape_ir"),
    )

    def _tool_opt_topology_to_sizing(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Bridge a 2D contour topology writeback to a sizing study.

        Verifies the project has a 2D topology result and contour writeback,
        auto-parameterizes the recovered extruded_region, and writes the full
        optimization-study envelope (problem, variables, objectives, constraints,
        study, decision-log) with chain-linkage provenance. Refuses 3D / voxel
        inputs honestly. Baseline geometry is never modified.
        """
        from aieng.converters.topology_to_sizing import topology_to_sizing
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = topology_to_sizing(pkg)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "topology_to_sizing_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status"), "tool": "opt.topology_to_sizing", **result}

    rt.register_tool(
        "opt.topology_to_sizing",
        _tool_opt_topology_to_sizing,
        description=(
            "Bridge a 2D contour topology writeback to a sizing study. Verifies "
            "analysis/topology_optimization.json and geometry/shape_ir.json, "
            "auto-parameterizes the recovered extruded_region thickness, and writes "
            "analysis/design_study_problem.json, analysis/optimization_variables.json, "
            "analysis/optimization_objectives.json, analysis/optimization_constraints.json, "
            "analysis/optimization_study.json, and a chain-linkage entry in "
            "analysis/optimization_decision_log.json. Refuses 3D / voxel inputs with "
            "needs_user_input. Run opt.writeback_to_shape_ir (method=contour) first."
        ),
        input_schema=_schema("opt.topology_to_sizing"),
    )

    def _tool_opt_design_study_summary(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Read-only design-study artifact envelope for external agents."""
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))

        artifact_paths = {
            "ranking": "analysis/design_study_candidate_ranking.json",
            "recommendation": "analysis/optimization_recommendation.json",
            "report": "diagnostics/optimization_report.json",
            "surrogate": "analysis/design_study_surrogate_proposals.json",
            "convergence": "analysis/optimization_iterations.json",
        }

        def _artifact(path: str) -> dict[str, Any]:
            if pkg is None or not pkg.exists():
                return {"path": path, "available": False, "reason": "package_not_found"}
            try:
                with zipfile.ZipFile(pkg, "r") as zf:
                    if path not in zf.namelist():
                        return {"path": path, "available": False, "reason": "missing"}
                    raw = json.loads(zf.read(path).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - summary should degrade per artifact
                return {
                    "path": path,
                    "available": False,
                    "reason": "read_failed",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            return {"path": path, "available": True, "report": raw}

        artifacts = {key: _artifact(path) for key, path in artifact_paths.items()}
        available = {key: value for key, value in artifacts.items() if value.get("available")}
        return {
            "status": "ok",
            "tool": "opt.design_study_summary",
            "project_id": pid,
            "available": bool(available),
            "artifacts": artifacts,
            "available_artifacts": sorted(available),
            "claim_advancement": "none",
            "honesty": (
                "Read-only artifact envelope. Presence of ranking/recommendation "
                "does not mean a candidate was accepted or the baseline was changed."
            ),
        }

    rt.register_tool(
        "opt.design_study_summary",
        _tool_opt_design_study_summary,
        description=(
            "Read-only design-study artifact envelope for external agents. Reports "
            "the availability of ranking, recommendation, optimization report, "
            "surrogate proposal, and convergence artifacts with per-artifact reasons. "
            "Does NOT validate, execute, evaluate, rank, accept, run CAE, or modify "
            "the baseline; artifact presence does not mean a candidate was accepted."
        ),
        input_schema=_schema("opt.design_study_summary"),
        requires_approval=False,
        read_only=True,
    )

    def _tool_opt_run_assembly_topology_optimization(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Explicit assembly-aware topopt execution for one selected design part.
        Uses the assembly topopt setup artifacts and writes selected-part derived
        artifacts only; no package-level geometry overwrite."""
        from aieng.converters.assembly_topopt import run_assembly_topology_optimization
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = run_assembly_topology_optimization(
                pkg,
                optimizer=(str(inp["optimizer"]) if inp.get("optimizer") else None),
                writeback=bool(inp.get("writeback", True)),
                method=(str(inp["method"]) if inp.get("method") else None),
                representation=(str(inp["representation"]) if inp.get("representation") else None),
                boundary=str(inp.get("boundary") or "spline"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "assembly_topopt_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {
            "status": result.get("status"),
            "tool": "opt.run_assembly_topology_optimization",
            "assembly_topology_optimization": result,
        }

    rt.register_tool(
        "opt.run_assembly_topology_optimization",
        _tool_opt_run_assembly_topology_optimization,
        description=(
            "Explicitly run assembly-aware topology optimization for one selected design "
            "part. Consumes assembly_topopt_problem + topology_optimization_problem, calls "
            "the existing optimizer, writes assembly diagnostics/provenance and selected-part "
            "derived artifacts, and never overwrites reference/frozen parts or package-level geometry."
        ),
        input_schema=_schema("opt.run_assembly_topology_optimization"),
    )

    def _tool_opt_propose_candidates(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Generate candidate parameter sets from optimization variables.
        Reads analysis/optimization_variables.json (and optionally
        optimization_study.json) from the project's .aieng package, runs
        the requested sampler, and writes candidate patches to
        patches/design_candidates/<cid>.json. Does NOT execute candidates,
        recompile geometry, run CAE, or modify the baseline."""
        from aieng.converters.optimization_sampler import sample_candidates_package
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = sample_candidates_package(
                pkg,
                algorithm=inp.get("algorithm"),
                count=inp.get("count"),
                seed=inp.get("seed"),
                max_candidates=inp.get("max_candidates"),
                overwrite=bool(inp.get("overwrite", False)),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "sampling_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {
            "status": result.get("status", "error"),
            "tool": "opt.propose_candidates",
            "sampler_result": result,
        }

    rt.register_tool(
        "opt.propose_candidates",
        _tool_opt_propose_candidates,
        description=(
            "Generate candidate parameter sets from optimization variables. "
            "Reads analysis/optimization_variables.json (required) and optionally "
            "analysis/optimization_study.json from the project's .aieng package, "
            "runs the requested sampler (grid / random / latin_hypercube), and "
            "writes candidate patches to patches/design_candidates/<cid>.json "
            "in the format consumed by the candidate executor/evaluator/ranker. "
            "Does NOT execute candidates, recompile geometry, run CAE, or modify "
            "the baseline. The study's candidate_ids are updated to include the "
            "new candidates. Deterministic given a seed."
        ),
        input_schema=_schema("opt.propose_candidates"),
        # Package-mutating but baseline-safe (writes only derived candidate
        # patches). Kept inside the modeling-plan boundary like
        # cad.execute_build123d / opt.writeback_to_shape_ir rather than carrying
        # a per-call approval gate; the hard gate lives at acceptance.
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_run_candidates(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Execute proposed design-study candidates into derived workspaces.
        Discovers candidates (or runs an explicit candidate_ids list) and runs
        each through the single-shot executor, applying its patch to a DERIVED
        copy of the baseline Shape IR in candidates/<cid>/. Optionally recompiles
        each candidate in a throwaway copy (compile=true, default). Failures are
        recorded per-candidate and the batch continues. Does NOT run CAE, accept
        a candidate, or modify the baseline."""
        from aieng.converters.design_study_batch import run_design_study_batch
        from ..cad_generation import make_candidate_recompiler
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        do_compile = bool(inp.get("compile", True))
        recompiler = make_candidate_recompiler(pkg) if do_compile else None
        try:
            result = run_design_study_batch(
                pkg,
                candidate_ids=inp.get("candidate_ids"),
                recompiler=recompiler,
                max_candidates=inp.get("max_candidates"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "batch_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.run_candidates", "batch_result": result}

    rt.register_tool(
        "opt.run_candidates",
        _tool_opt_run_candidates,
        description=(
            "Execute proposed design-study candidates into isolated derived "
            "workspaces (candidates/<cid>/). Discovers the candidate set from the "
            "package (optimization_study/variables candidate_ids + any candidate "
            "patches on disk), or runs an explicit candidate_ids list, applying "
            "each patch to a DERIVED copy of the baseline Shape IR. With "
            "compile=true (default) each candidate is recompiled in a throwaway "
            "copy; a failed candidate is recorded cleanly and the batch CONTINUES. "
            "Records each iteration in analysis/design_study_iterations.json. Does "
            "NOT run CAE, accept/promote any candidate, or modify the baseline."
        ),
        input_schema=_schema("opt.run_candidates"),
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_evaluate_candidates(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Evaluate executed design-study candidates from candidate-local evidence.
        For each candidate, normalizes mass / volume / max_stress / max_deflection /
        min_safety_factor, evaluates declared constraints, and classifies feasibility.
        Missing CAE metrics are recorded honestly as unknown — never fabricated. With
        cae=true, first derives each candidate's CAE setup and normalizes CAE evidence
        (solver stays disabled by default). Does NOT accept/promote a candidate or
        modify the baseline. Feeds opt.rank_candidates."""
        from aieng.converters.design_study_batch import run_design_study_evaluation_batch
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        cae_options: dict[str, Any] = {}
        if inp.get("cae"):
            if inp.get("mode") is not None:
                cae_options["mode"] = inp.get("mode")
            if inp.get("allow_solver_execution") is not None:
                cae_options["allow_solver_execution"] = bool(inp.get("allow_solver_execution"))
        try:
            result = run_design_study_evaluation_batch(
                pkg,
                candidate_ids=inp.get("candidate_ids"),
                cae=bool(inp.get("cae", False)),
                cae_options=cae_options or None,
                max_candidates=inp.get("max_candidates"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "evaluation_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.evaluate_candidates", "batch_result": result}

    rt.register_tool(
        "opt.evaluate_candidates",
        _tool_opt_evaluate_candidates,
        description=(
            "Evaluate executed design-study candidates from candidate-local "
            "evidence (candidates/<cid>/). Normalizes mass / volume / max_stress / "
            "max_deflection / min_safety_factor, evaluates declared constraints, and "
            "classifies each candidate feasible / infeasible / unknown. MISSING CAE "
            "metrics are recorded honestly as unknown — never fabricated. With "
            "cae=true, first derives each candidate's CAE setup and normalizes any "
            "CAE evidence (solver execution stays disabled by default). Writes "
            "candidates/<cid>/analysis/evaluation.json. Does NOT accept/promote any "
            "candidate or modify the baseline; feeds opt.rank_candidates."
        ),
        input_schema=_schema("opt.evaluate_candidates"),
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_rank_candidates(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Rank evaluated design-study candidates (advisory).
        Reads per-candidate evaluations, classifies feasibility, scores against the
        problem objective + constraints, and writes
        analysis/design_study_candidate_ranking.json +
        diagnostics/design_study_scoring_report.json. Selects best_candidate_id only
        when feasible, improving, and high-confidence. Does NOT accept/promote a
        candidate, run CAE, or modify the baseline."""
        from aieng.converters.design_study_ranking import rank_design_study_candidates
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = rank_design_study_candidates(pkg)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "ranking_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.rank_candidates", "ranking_result": result}

    rt.register_tool(
        "opt.rank_candidates",
        _tool_opt_rank_candidates,
        description=(
            "Rank evaluated design-study candidates (advisory). Reads per-candidate "
            "evaluations, classifies feasibility (feasible / infeasible / unknown / "
            "failed), scores against the problem objective + constraints, and writes "
            "analysis/design_study_candidate_ranking.json + the scoring report. "
            "best_candidate_id is selected only when a candidate is feasible, improves "
            "the objective, and is high-confidence; otherwise it is null and "
            "safe_to_accept is false. Does NOT accept/promote a candidate, run CAE, or "
            "modify the baseline."
        ),
        input_schema=_schema("opt.rank_candidates"),
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_explain_recommendation(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Explain the candidate ranking as an advisory, reason-coded recommendation.
        Reads the existing ranking + scoring report and composes a human-readable
        recommendation (why the top candidate, citing metrics + objective delta; why
        none, when applicable; caveats for missing metrics / low confidence), written
        to analysis/optimization_recommendation.json. Advisory only — does NOT accept a
        candidate, run CAE, or modify the baseline. Run opt.rank_candidates first."""
        from aieng.converters.optimization_recommendation import explain_recommendation
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = explain_recommendation(pkg)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "explain_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.explain_recommendation", "recommendation": result}

    rt.register_tool(
        "opt.explain_recommendation",
        _tool_opt_explain_recommendation,
        description=(
            "Explain a candidate ranking as an advisory, reason-coded recommendation. "
            "Reads analysis/design_study_candidate_ranking.json (run opt.rank_candidates "
            "first) and composes a human-readable recommendation: why the top candidate "
            "is recommended (objective delta + metrics), or why none is, plus explicit "
            "caveats for missing CAE metrics / low confidence. Writes "
            "analysis/optimization_recommendation.json. Advisory only and human-approval "
            "gated for acceptance — does NOT accept/promote a candidate, run CAE, or "
            "modify the baseline."
        ),
        input_schema=_schema("opt.explain_recommendation"),
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_accept_candidate(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Accept ONE ranked design-study candidate into a derived accepted workspace.
        Copies the candidate's derived artifacts into accepted/<cid>/ and writes
        analysis/design_study_acceptance.json. The candidate must be eligible
        (feasible, and best_candidate_id + safe_to_accept unless override_unsafe).
        Does NOT overwrite baseline geometry, does NOT auto-promote, and does NOT
        claim production approval. APPROVAL REQUIRED."""
        from aieng.converters.design_study_acceptance import accept_design_study_candidate
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        cid = str(inp.get("candidate_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        if not cid:
            return {"status": "error", "code": "bad_input", "message": "candidate_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = accept_design_study_candidate(
                pkg,
                cid,
                accepted_by=inp.get("accepted_by", "agent"),
                reasoning=inp.get("reasoning"),
                override_unsafe=bool(inp.get("override_unsafe", False)),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "acceptance_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.accept_candidate", "acceptance_result": result}

    rt.register_tool(
        "opt.accept_candidate",
        _tool_opt_accept_candidate,
        description=(
            "[APPROVAL REQUIRED] Accept ONE ranked design-study candidate into a "
            "derived accepted workspace (accepted/<cid>/). The candidate must be "
            "eligible: feasible (not failed/infeasible/unknown), and the "
            "best_candidate_id with safe_to_accept=true — otherwise override_unsafe "
            "must be set explicitly. Copies the candidate's derived patch / Shape IR / "
            "evaluation into accepted/<cid>/ and writes "
            "analysis/design_study_acceptance.json + the acceptance report. Does NOT "
            "overwrite baseline geometry, does NOT auto-promote into the baseline, and "
            "does NOT claim production certification — acceptance is an advisory, "
            "human-approved derived design artifact only."
        ),
        input_schema=_schema("opt.accept_candidate"),
        requires_approval=True,
    )

    def _tool_opt_write_report(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Aggregate the optimization study into a single summary report.
        Reads existing package artifacts (problem, variables/objectives/constraints,
        candidates + metrics, ranking, failed candidates, recommendation, acceptance,
        decision log) and writes diagnostics/optimization_report.json. Read-only with
        respect to engineering state: does NOT execute/evaluate/rank/accept candidates,
        run CAE, or modify the baseline. The report is reconstructable from artifacts."""
        from aieng.converters.optimization_report import build_optimization_report
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = build_optimization_report(pkg)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "report_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.write_report", "report_result": result}

    rt.register_tool(
        "opt.write_report",
        _tool_opt_write_report,
        description=(
            "Aggregate the optimization study into a single summary report. Reads the "
            "existing package artifacts (problem definition, variables / objectives / "
            "constraints, all candidates + metrics, ranking, failed candidates, the "
            "advisory recommendation, acceptance state, and decision log) and writes "
            "diagnostics/optimization_report.json. Read-only with respect to "
            "engineering state — does NOT execute / evaluate / rank / accept "
            "candidates, run CAE, or modify the baseline. The report is "
            "reconstructable purely from on-disk artifacts."
        ),
        input_schema=_schema("opt.write_report"),
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_propose_next(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Propose the next batch of candidates by local refinement around the incumbent.
        Reads the ranking incumbent + optimization variables and samples within a
        trust region that shrinks each iteration; falls back to whole-domain LHS when
        there is no feasible incumbent. Writes candidate patches in the existing
        format. Deterministic given a seed. Does NOT run/evaluate/accept candidates,
        run CAE, or modify the baseline."""
        from aieng.converters.optimization_proposer import propose_next_candidates
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = propose_next_candidates(
                pkg,
                count=int(inp.get("count", 4)),
                shrink=float(inp.get("shrink", 0.5)),
                seed=int(inp.get("seed", 0)),
                algorithm=inp.get("algorithm"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "propose_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.propose_next", "propose_result": result}

    rt.register_tool(
        "opt.propose_next",
        _tool_opt_propose_next,
        description=(
            "Propose the next batch of design-study candidates by trust-region local "
            "refinement around the current ranking incumbent (radius shrinks each "
            "iteration); falls back to whole-domain Latin hypercube sampling when there "
            "is no feasible incumbent. Writes candidate patches to "
            "patches/design_candidates/<cid>.json in the format the executor consumes. "
            "Deterministic given a seed. Does NOT run/evaluate/accept candidates, run "
            "CAE, or modify the baseline — pair with opt.run_candidates + "
            "opt.evaluate_candidates + opt.rank_candidates + opt.check_convergence."
        ),
        input_schema=_schema("opt.propose_next"),
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_select_optimizer(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Deterministically select an optimizer and log the reason-coded decision."""
        from aieng.converters.optimizer_selector import select_optimizer
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = select_optimizer(
                pkg,
                user_selected=inp.get("optimizer") if inp.get("optimizer") else None,
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "select_optimizer_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.select_optimizer", "select_result": result}

    rt.register_tool(
        "opt.select_optimizer",
        _tool_opt_select_optimizer,
        description=(
            "Deterministically select an optimizer for the design study and append a "
            "reason-coded decision to analysis/optimization_decision_log.json. Chooses "
            "trust_region (default), slsqp, bayesian, or genetic based on variable types, "
            "count, and CAE-availability. Honors explicit optimizer override. No search "
            "runs inside the call; pair with opt.propose_next or the phase-appropriate "
            "candidate generator."
        ),
        input_schema=_schema("opt.select_optimizer"),
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_check_convergence(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Record the current iteration's incumbent and return a convergence verdict.
        Snapshots the ranking incumbent into analysis/optimization_iterations.json and
        evaluates the deterministic stopping criteria (objective-delta stagnation,
        budget, no-feasible-progress, consecutive failures). Advisory only — tells the
        agent whether to stop; never accepts a candidate, runs CAE, or modifies the
        baseline. Run opt.rank_candidates first."""
        from aieng.converters.optimization_convergence import record_iteration_and_check
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}
        try:
            result = record_iteration_and_check(
                pkg,
                evaluations_total=inp.get("evaluations_total"),
                failures_this_round=int(inp.get("failures_this_round", 0)),
                had_success=bool(inp.get("had_success", True)),
                proposer_exhausted=bool(inp.get("proposer_exhausted", False)),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "convergence_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": result.get("status", "error"), "tool": "opt.check_convergence", "convergence_result": result}

    rt.register_tool(
        "opt.check_convergence",
        _tool_opt_check_convergence,
        description=(
            "Record the current iteration's incumbent into "
            "analysis/optimization_iterations.json and return a deterministic, advisory "
            "convergence verdict (continue / converged / stop_budget / stop_no_feasible "
            "/ stop_failures / stop_proposer_exhausted). The objective-delta check is "
            "direction-aware (reads the objective sense). Advisory only — it tells the "
            "agent whether to stop; it never accepts a candidate, runs CAE, or modifies "
            "the baseline. Acceptance stays the only hard gate (opt.accept_candidate)."
        ),
        input_schema=_schema("opt.check_convergence"),
        requires_approval=False,
        read_only=False,
    )

    def _tool_opt_sizing_sweep(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Sweep ONE editable dimension across explicit values or a {min,max,steps/step}
        range, solving EACH variant with the real static solver, and rank by objective
        subject to a stress/displacement constraint. Baseline is never modified unless
        apply_winner=true, in which case the winning value is applied through the
        audited cad.edit_parameter path. Runs N solver executions."""
        from ..sizing_sweep_runner import run_sizing_sweep

        pid = str(inp.get("project_id") or "").strip()
        feature_id = str(inp.get("featureId") or "").strip()
        parameter_name = str(inp.get("parameterName") or "").strip()
        if not (pid and feature_id and parameter_name):
            return {"status": "error", "code": "bad_input",
                    "message": "project_id, featureId and parameterName are required"}
        values = inp.get("values")
        range_spec = inp.get("range") if isinstance(inp.get("range"), dict) else None
        if values is None and range_spec is None:
            return {"status": "error", "code": "bad_input",
                    "message": "provide either values or range"}
        try:
            return run_sizing_sweep(
                active_settings,
                pid,
                feature_id=feature_id,
                parameter_name=parameter_name,
                values=values,
                range=range_spec,
                objective=str(inp.get("objective") or "min_mass"),
                stress_limit=inp.get("stress_limit"),
                safety_factor=float(inp.get("safety_factor", 1.0)),
                displacement_limit=inp.get("displacement_limit"),
                mesh_size_mm=inp.get("mesh_size_mm"),
                timeout=int(inp.get("timeout", 180)),
                density=inp.get("density"),
                apply_winner=bool(inp.get("apply_winner", False)),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "sweep_failed", "message": f"{type(exc).__name__}: {exc}"}

    rt.register_tool(
        "opt.sizing_sweep",
        _tool_opt_sizing_sweep,
        description=(
            "[APPROVAL REQUIRED] Parametric sizing sweep that CLOSES the optimize→verify "
            "loop on static FEA: vary ONE editable dimension (an UPPER_SNAKE_CASE constant, "
            "see cad.list_editable_parameters) across explicit values OR a {min,max,steps/step} "
            "range, solve EACH variant with the real static solver (Gmsh + CalculiX), and rank "
            "by objective (min_mass / min_displacement / min_stress) subject to a stress "
            "(yield / safety-factor) + optional displacement constraint. Range values are "
            "clamped to the parameter's declared min/max. The baseline is NEVER modified unless "
            "apply_winner=true, in which case the winning value is applied through the audited "
            "cad.edit_parameter path and the regression_diff is reported. A variant that fails "
            "to build or solve is reported honestly (solver_executed=false) and never recommended. "
            "Runs N solver executions, so it is approval-gated as one operation."
        ),
        input_schema=_schema("opt.sizing_sweep"),
        requires_approval=True,
        read_only=False,
    )

    def _tool_opt_doe_sizing_study(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """Multi-parameter DOE sizing study: jointly vary 2+ editable parameters,
        solve each design point with the real static solver, and rank by objective +
        constraints. Budget-capped; baseline never mutated."""
        from ..doe_sizing_runner import run_doe_sizing_study

        pid = str(inp.get("project_id") or "").strip()
        parameters = inp.get("parameters")
        if not pid:
            return {"status": "error", "code": "bad_input", "message": "project_id is required"}
        if not isinstance(parameters, list) or len(parameters) < 2:
            return {"status": "error", "code": "bad_input",
                    "message": "parameters must be a list with at least 2 entries"}
        try:
            return run_doe_sizing_study(
                active_settings,
                pid,
                parameters=parameters,
                method=str(inp.get("method") or "full_factorial"),
                budget=int(inp.get("budget", 64)),
                objective=str(inp.get("objective") or "min_mass"),
                stress_limit=inp.get("stress_limit"),
                safety_factor=float(inp.get("safety_factor", 1.0)),
                displacement_limit=inp.get("displacement_limit"),
                mesh_size_mm=inp.get("mesh_size_mm"),
                timeout=int(inp.get("timeout", 180)),
                density=inp.get("density"),
                seed=inp.get("seed"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "doe_study_failed", "message": f"{type(exc).__name__}: {exc}"}

    rt.register_tool(
        "opt.doe_sizing_study",
        _tool_opt_doe_sizing_study,
        description=(
            "[APPROVAL REQUIRED] Multi-parameter DOE sizing study: jointly vary 2+ editable "
            "parameters (each by explicit values or a {min,max,steps/step} range), generate a "
            "full-factorial or Latin-hypercube design within the budget cap, solve EACH design "
            "point with the real static solver (Gmsh + CalculiX), and rank by objective subject "
            "to stress/displacement constraints. The baseline is never modified; the recommended "
            "design point is applied through approval-gated cad.edit_parameter. Budget is capped "
            "at 64 solver executions."
        ),
        input_schema=_schema("opt.doe_sizing_study"),
        requires_approval=True,
        read_only=False,
    )

    def _tool_opt_cae_evaluate_candidate(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        """CAE-evaluate ONE design-study candidate with the real static solver. When
        allow_solver_execution is true, compiles the candidate geometry on a throwaway
        copy and solves it (Gmsh + CalculiX), writing candidate-local computed_metrics +
        an evaluation whose honesty.solver_executed reflects reality. Baseline never
        modified; no candidate accepted/promoted."""
        from aieng.converters.design_study_cae_evaluation import (
            request_design_study_candidate_cae_evaluation,
        )
        from ..project_io import get_project, resolve_project_path

        pid = str(inp.get("project_id") or "").strip()
        cid = str(inp.get("candidate_id") or "").strip()
        if not pid or not cid:
            return {"status": "error", "code": "bad_input", "message": "project_id and candidate_id are required"}
        project = get_project(active_settings, pid)
        pkg = resolve_project_path(active_settings, pid, project.get("aieng_file"))
        if pkg is None or not pkg.exists():
            return {"status": "error", "code": "no_package", "message": ".aieng package not found"}

        allow_solver = bool(inp.get("allow_solver_execution", False))
        solver_fn = None
        if allow_solver:
            from ..candidate_solver import solve_candidate_geometry

            def solver_fn(p, c):  # noqa: ANN001
                return solve_candidate_geometry(
                    p, c, timeout=int(inp.get("timeout", 180)), mesh_size_mm=inp.get("mesh_size_mm")
                )
        try:
            result = request_design_study_candidate_cae_evaluation(
                pkg,
                cid,
                mode="run_if_available" if allow_solver else "normalize_existing",
                allow_solver_execution=allow_solver,
                allow_ranking_refresh=bool(inp.get("allow_ranking_refresh", False)),
                requested_by="agent",
                solver_fn=solver_fn,
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "code": "cae_evaluate_failed", "message": f"{type(exc).__name__}: {exc}"}
        return {"tool": "opt.cae_evaluate_candidate", **result}

    rt.register_tool(
        "opt.cae_evaluate_candidate",
        _tool_opt_cae_evaluate_candidate,
        description=(
            "[APPROVAL REQUIRED] CAE-evaluate ONE design-study candidate with the REAL static "
            "solver — the MCP path that CLOSES the candidate optimize→verify loop. Derives "
            "candidate-local CAE setup from the baseline; when allow_solver_execution=true it "
            "compiles the candidate geometry on a throwaway copy and runs Gmsh + CalculiX, "
            "writing candidate-local computed_metrics and an evaluation whose "
            "honesty.solver_executed reflects reality (true only on a real solve). The "
            "baseline is NEVER modified and no candidate is accepted or promoted. Degrades "
            "honestly — never a fake success — when Gmsh/CalculiX are unavailable or the "
            "candidate's faces no longer match the baseline CAE mapping (stale topology). "
            "With allow_solver_execution=false it only normalizes existing candidate metrics. "
            "Run opt.run_candidates first; rank with opt.rank_candidates after."
        ),
        input_schema=_schema("opt.cae_evaluate_candidate"),
        requires_approval=True,
        read_only=False,
    )

    return {
        "derive_topology_optimization_problem": _tool_opt_derive_problem_from_cae,
        "run_topology_optimization": _tool_opt_run_topology_optimization,
        "writeback_topology_optimization": _tool_opt_writeback_to_shape_ir,
        "topology_to_sizing": _tool_opt_topology_to_sizing,
        "run_assembly_topology_optimization": _tool_opt_run_assembly_topology_optimization,
        "sizing_sweep": _tool_opt_sizing_sweep,
        "cae_evaluate_candidate": _tool_opt_cae_evaluate_candidate,
    }
