"""Project targets, metrics, templates, reviews, and copilot-loop routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Body, FastAPI

from ..legacy_app_symbols import sync_main_symbols

LOGGER = logging.getLogger("app.app_factory")


def _sync_main_symbols() -> None:
    sync_main_symbols(globals())


def register_project_analysis_routes(app: FastAPI, *, active_settings: Any) -> None:
    _sync_main_symbols()

    @app.get("/api/projects/{project_id}/design-targets")
    def get_project_design_targets(project_id: str) -> dict[str, Any]:
        """Read design targets from the project's .aieng package."""
        from .. import design_targets

        return design_targets.get_design_targets(active_settings, project_id)

    @app.put("/api/projects/{project_id}/design-targets")
    def put_project_design_targets(project_id: str, payload: Any = Body(...)) -> dict[str, Any]:
        """Save design targets into the project's .aieng package.

        Validates the payload and writes only the design-target artifact.
        Does not run solvers, edit CAD, or advance claims.
        """
        from .. import design_targets

        return design_targets.save_design_targets(active_settings, project_id, payload)

    @app.get("/api/projects/{project_id}/computed-metrics")
    def get_project_computed_metrics(project_id: str) -> dict[str, Any]:
        """Read computed metrics from the project's .aieng package.

        Read-only: does not run solvers, refresh summaries, or advance claims.
        """
        from .. import computed_metrics

        return computed_metrics.get_computed_metrics(active_settings, project_id)

    @app.get("/api/projects/{project_id}/target-comparison")
    def get_project_target_comparison(project_id: str) -> dict[str, Any]:
        """Read-only design-target comparison using the aieng core comparator.

        Does not run solvers, mutate the package, edit CAD, or advance claims.
        """
        from .. import target_comparison

        return target_comparison.compare_package_targets(active_settings, project_id)

    @app.get("/api/projects/{project_id}/calibration-cases")
    def list_project_calibration_cases(project_id: str) -> dict[str, Any]:
        """List available CAE calibration/benchmark cases for comparison.

        Read-only: does not run solvers, mutate the package, or advance claims.
        """
        from .. import cae_calibration

        return {"project_id": project_id, "cases": cae_calibration.list_calibration_cases()}

    @app.post("/api/projects/{project_id}/calibration")
    def run_project_calibration(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Compare the project's computed metrics against a calibration case.

        If ``caseId`` is omitted, the comparator attempts a conservative auto-match
        against available metrics. Read-only: does not run solvers or mutate the
        package.
        """
        from .. import cae_calibration, computed_metrics

        data = payload or {}
        metrics = computed_metrics.get_computed_metrics(active_settings, project_id)
        if not metrics.get("ok"):
            return {
                "status": "error",
                "project_id": project_id,
                "message": "computed metrics not available",
            }

        doc = metrics.get("document") or {}
        computed = doc.get("global_metrics") or {}
        raw_case_id = data.get("caseId") or data.get("case_id")
        case_id = str(raw_case_id) if raw_case_id else None
        result = cae_calibration.assess_calibration(computed, case_id=case_id)
        return {"status": "ok", "project_id": project_id, "comparison": result}

    @app.get("/api/engineering-templates")
    def list_engineering_templates_endpoint() -> dict[str, Any]:
        """List available parametric CAD + FEA setup templates (v0.34).

        Templates are static, controlled, and deterministic. Listing reads
        no project state and performs no execution.
        """
        from .. import engineering_templates

        return engineering_templates.list_engineering_templates()

    @app.get("/api/engineering-templates/{template_id}")
    def get_engineering_template_endpoint(template_id: str) -> dict[str, Any]:
        """Return the full schema (parameters, materials, safety note, claim boundary)."""
        from .. import engineering_templates

        return engineering_templates.get_engineering_template(template_id)

    @app.post("/api/projects/{project_id}/engineering-templates/{template_id}/preview")
    def post_engineering_template_preview(
        project_id: str, template_id: str, payload: Any = Body(default=None)
    ) -> dict[str, Any]:
        """Read-only preview of the CAD script + FEA setup draft + target suggestions.

        Never writes to the project package, never runs CAD/mesh/solver tools,
        never advances claims. Invalid parameters return a structured 200 response
        with ``errors[]`` rather than 4xx so the UI can render the validation map.
        """
        from .. import engineering_templates

        body = payload if isinstance(payload, dict) else {}
        return engineering_templates.preview_template(active_settings, project_id, template_id, body)

    @app.post("/api/projects/{project_id}/engineering-templates/{template_id}/save-draft")
    def post_engineering_template_save_draft(
        project_id: str, template_id: str, payload: Any = Body(default=None)
    ) -> dict[str, Any]:
        """Explicit user save. Writes only ``task/engineering_setup_draft.json``,
        ``task/cad_template_preview.py``, ``task/fea_setup_draft.json``, and
        ``task/design_targets_suggestions.yaml`` into the package.

        Never touches CAD geometry, simulation/, results/, or existing
        ``task/design_targets.yaml``. Never runs an external tool.
        """
        from .. import engineering_templates

        body = payload if isinstance(payload, dict) else {}
        return engineering_templates.save_template_draft(active_settings, project_id, template_id, body)

    @app.post("/api/projects/{project_id}/engineering-templates/{template_id}/adopt-targets")
    def post_engineering_template_adopt_targets(
        project_id: str, template_id: str, payload: Any = Body(default=None)
    ) -> dict[str, Any]:
        """Explicitly adopt template target suggestions into design targets.

        Writes only ``task/design_targets.yaml`` and never runs CAD, mesh,
        solver, postprocessing, or claim updates.
        """
        from .. import engineering_templates

        body = payload if isinstance(payload, dict) else {}
        return engineering_templates.adopt_template_target_suggestions(
            active_settings, project_id, template_id, body
        )

    @app.post("/api/projects/{project_id}/engineering-templates/{template_id}/generate-cad-fixture")
    def post_engineering_template_generate_cad_fixture(
        project_id: str, template_id: str, payload: Any = Body(default=None)
    ) -> dict[str, Any]:
        """Explicitly write a deterministic template CAD fixture.

        Requires approval in the payload. Writes geometry metadata plus stale
        revalidation state only; never runs CAD, mesh, solver, or claim updates.
        """
        from .. import engineering_templates

        body = payload if isinstance(payload, dict) else {}
        return engineering_templates.generate_template_cad_fixture(
            active_settings, project_id, template_id, body
        )

    @app.get("/api/projects/{project_id}/review-support-packet/preview")
    def get_review_support_packet_preview(project_id: str) -> dict[str, Any]:
        """Build and return an Engineering Review Support Packet without writing it.

        Read-only aggregation of existing project evidence. Does not run CAD,
        meshers, or solvers. Does not edit the .aieng package. Does not
        advance engineering claims.
        """
        from .. import review_support_packet

        return review_support_packet.preview_review_support_packet(active_settings, project_id)

    @app.get("/api/projects/{project_id}/report")
    def get_project_engineering_report(project_id: str) -> Any:
        """Return a self-contained, print-friendly Engineering Report HTML page.

        Read-only aggregation of existing project/package evidence. Does not
        run CAD, meshers, solvers, post-processing tools, or write artifacts.
        """
        from fastapi.responses import HTMLResponse

        from .. import engineering_report

        result = engineering_report.generate_engineering_report(active_settings, project_id)
        return HTMLResponse(
            content=result["html"],
            media_type="text/html",
            headers={
                "Content-Disposition": f'inline; filename="engineering-report-{project_id}.html"',
            },
        )

    @app.post("/api/projects/{project_id}/review-support-packet/export")
    def post_review_support_packet_export(
        project_id: str, payload: Any = Body(default=None)
    ) -> dict[str, Any]:
        """Export the Engineering Review Support Packet into the project package.

        Writes only ``reports/review_support/{packet_id}.md`` and ``.json``.
        Does not run CAD, meshers, or solvers; does not edit any other artifact;
        does not advance engineering claims. ``preview_markdown`` is included by
        default so the UI can render it without a second round-trip.
        """
        from .. import review_support_packet

        body = payload if isinstance(payload, dict) else {}
        return review_support_packet.export_review_support_packet(
            active_settings, project_id, body
        )

    @app.post("/api/projects/{project_id}/computed-metrics/preview")
    def preview_project_computed_metrics(project_id: str, payload: Any = Body(...)) -> dict[str, Any]:
        """Parse and validate a computed-metrics import without writing it."""
        from .. import computed_metrics

        return computed_metrics.preview_computed_metrics(active_settings, project_id, payload)

    @app.put("/api/projects/{project_id}/computed-metrics")
    def put_project_computed_metrics(project_id: str, payload: Any = Body(...)) -> dict[str, Any]:
        """Save computed metrics into the project's .aieng package.

        Explicit user import only. Writes only results/computed_metrics.json;
        does not run a solver, edit CAD, generate mesh, refresh claims, or
        certify engineering safety.
        """
        from .. import computed_metrics

        return computed_metrics.save_computed_metrics(active_settings, project_id, payload)

    @app.get("/api/projects/{project_id}/cae-artifacts")
    def get_project_cae_artifacts(project_id: str) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _detect_cae_artifacts(active_settings, package_path)
        if result is None:
            raise HTTPException(status_code=503, detail="aieng detector unavailable")
        return result

    @app.get("/api/projects/{project_id}/cae-result-summary")
    def get_project_cae_result_summary(project_id: str) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _generate_cae_result_summary(active_settings, package_path)
        if result is None:
            raise HTTPException(status_code=503, detail="aieng summarizer unavailable")
        result["revalidation_status"] = _build_revalidation_response(
            _read_revalidation_status(package_path)
        )
        return result

    @app.get("/api/projects/{project_id}/cae-preprocessing-summary")
    def get_project_cae_preprocessing_summary(project_id: str) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _generate_cae_preprocessing_summary(active_settings, package_path)
        if result is None:
            raise HTTPException(status_code=503, detail="aieng preprocessing summarizer unavailable")
        return result

    @app.get("/api/projects/{project_id}/cae-simulation-run-summary")
    def get_project_cae_simulation_run_summary(project_id: str) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _generate_cae_simulation_run_summary(active_settings, package_path)
        if result is None:
            raise HTTPException(status_code=503, detail="aieng simulation run summarizer unavailable")
        return result

    @app.get("/api/projects/{project_id}/cad-recommendations")
    def get_project_cad_recommendations(
        project_id: str,
        strictness: str = "default",
    ) -> dict[str, Any]:
        """Phase 39 MVP: ranked CAD modification proposals + verification verdicts.

        Read-only. Runs the Phase 36 recommender and the Phase 37
        verification gate on the project's .aieng package and returns
        a combined payload for the UI panel. Does not mutate the
        package, does not execute CAD/CAE operations, does not advance
        claims.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _generate_cad_recommendations_with_verification(
            active_settings, package_path, strictness=strictness
        )
        if result is None:
            raise HTTPException(status_code=503, detail="aieng recommender/verifier unavailable")
        return result

    @app.get("/api/projects/{project_id}/cae-review-report")
    def get_project_cae_review_report(project_id: str) -> dict[str, Any]:
        """Generate a read-only, evidence-backed CAE review report.

        This endpoint synthesizes existing lifecycle summaries. It never
        executes a solver, mutates the package, or advances claims.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        preprocessing = _generate_cae_preprocessing_summary(active_settings, package_path)
        simulation_run = _generate_cae_simulation_run_summary(active_settings, package_path)
        result = _generate_cae_result_summary(active_settings, package_path)
        if preprocessing is None and simulation_run is None and result is None:
            raise HTTPException(status_code=503, detail="aieng CAE summarizers unavailable")
        revalidation = _build_revalidation_response(_read_revalidation_status(package_path))
        return build_cae_review_report(
            package_path=package_path,
            project_id=project_id,
            preprocessing_summary=preprocessing,
            simulation_run_summary=simulation_run,
            result_summary=result,
            revalidation_status=revalidation,
        )

    @app.post("/api/projects/{project_id}/copilot-loop/start")
    def start_project_copilot_loop(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Create a persisted Closed-loop Copilot Stepper state.

        The loop is a thin orchestration layer over existing runtime tools. It
        does not execute mutation/expensive tools unless their runtime approval
        gates are satisfied.
        """
        from .. import copilot_loop

        return copilot_loop.start_loop(active_settings, project_id, payload or {})

    @app.get("/api/projects/{project_id}/copilot-loops")
    def list_project_copilot_loops(project_id: str) -> dict[str, Any]:
        """List persisted Copilot loops for the project, newest first.

        Used by the UI to recover loop state after a browser refresh. Returns
        summary metadata only (no per-step `data` payload).
        """
        from .. import copilot_loop

        return copilot_loop.list_loops(active_settings, project_id)

    @app.get("/api/projects/{project_id}/copilot-loops/compare-reports")
    def compare_project_copilot_loop_reports(
        project_id: str, left: str, right: str
    ) -> dict[str, Any]:
        """Diff two persisted Copilot loop reports for the same project.

        Missing reports return a clean unavailable response with warnings.
        Reports are never auto-generated. Path traversal in persisted state
        is rejected at the safe-member layer.
        """
        from .. import copilot_loop

        return copilot_loop.compare_reports(active_settings, project_id, left, right)

    @app.post("/api/projects/{project_id}/copilot-loops/export-review")
    def export_project_copilot_loop_review(
        project_id: str, payload: dict[str, Any] = Body(default=None)
    ) -> dict[str, Any]:
        """Export a Markdown decision-review artifact for one or two loops.

        The export path is built server-side from a constant prefix and a
        timestamp; loop IDs are regex-validated and resolved through
        project-scoped storage. The export always carries an explicit
        claim-boundary statement.
        """
        from .. import copilot_loop

        return copilot_loop.export_review(active_settings, project_id, payload or {})

    @app.post("/api/demo/copilot-loop/seed")
    def seed_demo_copilot_loop_project(
        payload: dict[str, Any] = Body(default=None)
    ) -> dict[str, Any]:
        """Seed (or reuse) the bracket-lightweighting Copilot demo project.

        Subsequent calls return the same demo project unless ``reset=true``
        is passed in the payload, in which case existing demo projects are
        removed first. Real user projects are never modified. Pre-baked
        data is clearly labelled as demo/fixture; it does not represent
        real CAD/CAE execution.
        """
        from .. import copilot_loop_demo

        return copilot_loop_demo.seed_demo_project(active_settings, payload or {})

    @app.post("/api/demo/copilot-loop/reset")
    def reset_demo_copilot_loop_projects(
        payload: dict[str, Any] = Body(default=None)  # noqa: ARG001 — payload reserved for future filtering
    ) -> dict[str, Any]:
        """Remove all Copilot-loop demo projects from the workspace.

        Only projects flagged as demo are deleted. Real user projects are
        never touched.
        """
        from .. import copilot_loop_demo

        return copilot_loop_demo.reset_demo_projects(active_settings)

    @app.post("/api/demo/copilot-loop/smoke-check")
    def demo_copilot_loop_smoke_check(
        payload: dict[str, Any] = Body(default=None)
    ) -> dict[str, Any]:
        """Run a local health-check chain against the demo fixture.

        Verifies seed → list → compare → export end-to-end without requiring
        Gmsh, CalculiX, or a real solver. Only operates on demo-flagged
        projects. Returns a structured pass/fail checklist.
        """
        from .. import copilot_loop_demo

        return copilot_loop_demo.run_demo_smoke_check(active_settings, payload or {})

    @app.get("/api/projects/{project_id}/copilot-loop/{loop_id}")
    def get_project_copilot_loop(project_id: str, loop_id: str) -> dict[str, Any]:
        from .. import copilot_loop

        return copilot_loop.load_loop(active_settings, project_id, loop_id)

    @app.post("/api/projects/{project_id}/copilot-loop/{loop_id}/advance")
    def advance_project_copilot_loop(project_id: str, loop_id: str) -> dict[str, Any]:
        from .. import copilot_loop

        return copilot_loop.advance_loop(active_settings, project_id, loop_id)

    @app.post("/api/projects/{project_id}/copilot-loop/{loop_id}/approve")
    def approve_project_copilot_loop(project_id: str, loop_id: str) -> dict[str, Any]:
        from .. import copilot_loop

        return copilot_loop.approve_loop(active_settings, project_id, loop_id)

    @app.post("/api/projects/{project_id}/copilot-loop/{loop_id}/reject")
    def reject_project_copilot_loop(project_id: str, loop_id: str) -> dict[str, Any]:
        from .. import copilot_loop

        return copilot_loop.reject_loop(active_settings, project_id, loop_id)

    @app.get("/api/projects/{project_id}/copilot-loop/{loop_id}/report")
    def get_project_copilot_loop_report(project_id: str, loop_id: str) -> dict[str, Any]:
        from .. import copilot_loop

        return copilot_loop.get_report(active_settings, project_id, loop_id)
