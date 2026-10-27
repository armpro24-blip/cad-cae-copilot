"""Tests for the parametric sizing-sweep orchestrator + opt.sizing_sweep wiring.

The per-variant build+solve step is injected so the orchestration is exercised
without Gmsh/CalculiX. A separate skip-gated check covers solve_package_static's
honest degradation when the solver tools are unavailable.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import app.project_io as project_io
from app.sizing_sweep_runner import SIZING_SWEEP_REPORT_PATH, run_sizing_sweep


@pytest.fixture
def fake_project(monkeypatch, tmp_path: Path):
    """Make get_project / resolve_project_path resolve to an existing dummy package."""
    pkg = tmp_path / "proj.aieng"
    pkg.write_bytes(b"PK\x03\x04")  # existence is all the orchestrator checks
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)
    return pkg


@pytest.fixture
def valid_project(monkeypatch, tmp_path: Path):
    """Make get_project / resolve_project_path resolve to a valid .aieng package."""
    pkg = tmp_path / "proj.aieng"
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"version": "0.1.0"}))
        zf.writestr("graph/feature_graph.json", json.dumps({"features": []}))
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)
    return pkg


def _fake_evaluator(metric_by_value):
    """value → {value, metrics, solver_executed} from a {value: metrics} table."""
    def _ev(value):
        m = metric_by_value.get(value)
        if m is None:
            return {"value": value, "metrics": {}, "solver_executed": False, "error": "no result"}
        return {"value": value, "metrics": m, "solver_executed": True}
    return _ev


def test_sweep_recommends_lightest_feasible(fake_project) -> None:
    table = {
        2.0: {"max_von_mises_stress": 260.0, "mass": 1.0},   # infeasible
        3.0: {"max_von_mises_stress": 170.0, "mass": 1.5},   # feasible (lightest feasible)
        4.0: {"max_von_mises_stress": 120.0, "mass": 2.0},   # feasible (heavier)
    }
    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f_wall", parameter_name="thickness",
        values=[2.0, 3.0, 4.0],
        objective="min_mass", stress_limit=200.0,
        evaluate_value=_fake_evaluator(table),
    )
    assert report["status"] == "ok"
    assert report["tool"] == "opt.sizing_sweep"
    assert report["recommended"]["value"] == 3.0
    assert report["safe_to_apply"] is True
    assert report["honesty"]["baseline_modified"] is False
    assert "cad.edit_parameter" in report["next_step"]
    assert report["swept_values"] == [2.0, 3.0, 4.0]


def test_sweep_no_feasible_variant(fake_project) -> None:
    table = {3.0: {"max_von_mises_stress": 300.0, "mass": 1.5}}
    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f", parameter_name="t",
        values=[3.0], objective="min_mass", stress_limit=200.0,
        evaluate_value=_fake_evaluator(table),
    )
    assert report["status"] == "ok"
    assert report["recommended"] is None
    assert report["safe_to_apply"] is False
    assert "No value recommended" in report["next_step"]


def test_sweep_unsolved_variant_is_not_recommended(fake_project) -> None:
    table = {3.0: None, 4.0: {"max_von_mises_stress": 120.0, "mass": 2.0}}  # 3.0 fails to solve
    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f", parameter_name="t",
        values=[3.0, 4.0], objective="min_mass", stress_limit=200.0,
        evaluate_value=_fake_evaluator(table),
    )
    assert report["recommended"]["value"] == 4.0
    # not all variants solved → overall credibility downgraded
    assert report["credibility"]["tier"] == "unverified"


def test_sweep_dedupes_and_rejects_empty(fake_project) -> None:
    empty = run_sizing_sweep(
        None, "proj1", feature_id="f", parameter_name="t",
        values=[], evaluate_value=_fake_evaluator({}),
    )
    assert empty["status"] == "error" and empty["code"] == "bad_input"


def test_sweep_caps_value_count(fake_project) -> None:
    report = run_sizing_sweep(
        None, "proj1", feature_id="f", parameter_name="t",
        values=[float(i) for i in range(26)],
        evaluate_value=_fake_evaluator({}),
    )
    assert report["status"] == "error" and report["code"] == "too_many_values"


def test_sweep_project_not_found(monkeypatch, tmp_path: Path) -> None:
    def _raise(settings, pid):
        raise KeyError("nope")
    monkeypatch.setattr(project_io, "get_project", _raise)
    report = run_sizing_sweep(
        None, "missing", feature_id="f", parameter_name="t",
        values=[1.0], evaluate_value=_fake_evaluator({1.0: {"mass": 1.0}}),
    )
    assert report["status"] == "error" and report["code"] == "project_not_found"


def test_sweep_missing_package(monkeypatch) -> None:
    monkeypatch.setattr(project_io, "get_project", lambda s, p: {"aieng_file": "x"})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda s, p, f: None)
    report = run_sizing_sweep(
        None, "p", feature_id="f", parameter_name="t",
        values=[1.0], evaluate_value=_fake_evaluator({1.0: {"mass": 1.0}}),
    )
    assert report["status"] == "error" and report["code"] == "no_package"


def test_solve_package_static_degrades_when_tools_unavailable(tmp_path: Path) -> None:
    """Without Gmsh/CalculiX, solve_package_static must report honest failure,
    never a fake success."""
    from app.simulation_runner import check_simulation_tools, solve_package_static

    if check_simulation_tools()["ready"]:
        pytest.skip("Gmsh + CalculiX available — degradation path not exercised")
    result = solve_package_static(tmp_path / "nope.aieng")
    assert result["solver_executed"] is False
    assert result["status"] == "tools_unavailable"
    assert result["metrics"] == {}


def _make_pkg_with_feature_graph(tmp_path: Path) -> Path:
    import json
    import zipfile

    pkg = tmp_path / "proj.aieng"
    feature_graph = {
        "features": [
            {
                "id": "f_wall",
                "parameters": {
                    "thickness": {
                        "current_value": 3.0,
                        "min_value": 2.0,
                        "max_value": 8.0,
                        "cad_parameter_name": "THICKNESS",
                    }
                },
            }
        ]
    }
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph))
    return pkg


def test_sweep_expands_range_with_steps(monkeypatch, tmp_path: Path) -> None:
    pkg = _make_pkg_with_feature_graph(tmp_path)
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)

    table = {v: {"max_von_mises_stress": 100.0, "mass": float(v)} for v in [2.0, 3.0, 4.0, 5.0, 6.0]}
    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f_wall", parameter_name="thickness",
        range={"min": 2.0, "max": 6.0, "steps": 5},
        objective="min_mass", stress_limit=200.0,
        evaluate_value=_fake_evaluator(table),
    )
    assert report["status"] == "ok"
    assert report["swept_values"] == [2.0, 3.0, 4.0, 5.0, 6.0]
    assert report["range"]["min"] == 2.0
    assert report["recommended"]["value"] == 2.0


def test_sweep_expands_range_with_step_and_clamps(monkeypatch, tmp_path: Path) -> None:
    pkg = _make_pkg_with_feature_graph(tmp_path)
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)

    # Range extends beyond declared max (8.0); should clamp to 8.
    table = {v: {"max_von_mises_stress": 100.0, "mass": float(v)} for v in [2.0, 4.0, 6.0, 8.0]}
    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f_wall", parameter_name="thickness",
        range={"min": 1.0, "max": 10.0, "step": 2.0},
        objective="min_mass", stress_limit=200.0,
        evaluate_value=_fake_evaluator(table),
    )
    assert report["status"] == "ok"
    assert report["swept_values"] == [2.0, 4.0, 6.0, 8.0]


def test_sweep_range_rejects_too_many_steps(monkeypatch, tmp_path: Path) -> None:
    pkg = _make_pkg_with_feature_graph(tmp_path)
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)

    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f_wall", parameter_name="thickness",
        range={"min": 2.0, "max": 8.0, "steps": 30},
        evaluate_value=_fake_evaluator({}),
    )
    assert report["status"] == "error"
    assert report["code"] == "too_many_values"
    assert "25" in report["message"]


def test_sweep_apply_winner_calls_edit_parameter(monkeypatch, tmp_path: Path) -> None:
    pkg = _make_pkg_with_feature_graph(tmp_path)
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)

    table = {
        2.0: {"max_von_mises_stress": 260.0, "mass": 1.0},
        3.0: {"max_von_mises_stress": 170.0, "mass": 1.5},
        4.0: {"max_von_mises_stress": 120.0, "mass": 2.0},
    }

    applied = {}

    def fake_edit(settings, project_id, feature_id, parameter_name, new_value, **kwargs):
        applied.update({"project_id": project_id, "feature_id": feature_id, "value": new_value})
        return {"status": "ok", "regression_diff": {"verdict": "clean"}}

    monkeypatch.setattr("app.cad_generation.edit_build123d_parameter", fake_edit)

    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f_wall", parameter_name="thickness",
        values=[2.0, 3.0, 4.0],
        objective="min_mass", stress_limit=200.0,
        apply_winner=True,
        evaluate_value=_fake_evaluator(table),
    )
    assert report["status"] == "ok"
    assert report["apply_status"] == "ok"
    assert report["baseline_modified"] is True
    assert report["applied_value"] == 3.0
    assert applied["value"] == 3.0
    assert report["regression_diff"]["verdict"] == "clean"


def test_sweep_apply_winner_honestly_reports_edit_failure(monkeypatch, tmp_path: Path) -> None:
    pkg = _make_pkg_with_feature_graph(tmp_path)
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)

    table = {3.0: {"max_von_mises_stress": 170.0, "mass": 1.5}}

    def fake_edit(*args, **kwargs):
        return {"status": "error", "message": "build failed"}

    monkeypatch.setattr("app.cad_generation.edit_build123d_parameter", fake_edit)

    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f_wall", parameter_name="thickness",
        values=[3.0],
        objective="min_mass", stress_limit=200.0,
        apply_winner=True,
        evaluate_value=_fake_evaluator(table),
    )
    assert report["status"] == "ok"
    assert report["apply_status"] == "error"
    assert report["baseline_modified"] is False
    assert "build failed" in report["apply_error"]


def test_sweep_persists_report_to_package(valid_project: Path) -> None:
    table = {
        2.0: {"max_von_mises_stress": 260.0, "mass": 1.0},
        3.0: {"max_von_mises_stress": 170.0, "mass": 1.5},
        4.0: {"max_von_mises_stress": 120.0, "mass": 2.0},
    }
    report = run_sizing_sweep(
        None, "proj1",
        feature_id="f_wall", parameter_name="thickness",
        values=[2.0, 3.0, 4.0],
        objective="min_mass", stress_limit=200.0,
        evaluate_value=_fake_evaluator(table),
    )
    assert report["status"] == "ok"
    assert report["artifact_path"] == SIZING_SWEEP_REPORT_PATH
    assert "artifact_write_error" not in report

    with zipfile.ZipFile(valid_project, "r") as zf:
        assert SIZING_SWEEP_REPORT_PATH in zf.namelist()
        persisted = json.loads(zf.read(SIZING_SWEEP_REPORT_PATH).decode("utf-8"))
    assert persisted["tool"] == "opt.sizing_sweep"
    assert persisted["recommended"]["value"] == 3.0


# --- Product-grade checks (2026-10) -----------------------------------------
#
# The analytic model is the Euler-Bernoulli cantilever used for the real-ccx
# V&V: sigma_max = 6 F L / (b t^2). With F = 100 N, L = 100 mm, b = 20 mm that is
# sigma = 3000 / t^2 MPa, so the stress-limited minimum-mass thickness for an
# allowable of 100 MPa is t* = sqrt(6 F L / (b sigma)) = sqrt(30) = 5.4772 mm.


def _beam(value, *, band="reliable"):
    return {
        "value": value,
        "metrics": {"max_von_mises_stress": 3000.0 / value**2, "mass": 2000.0 * value},
        "solver_executed": True,
        "mesh_accuracy": {"band": band, "recommended_action": "re-mesh with mesh_size_mm <= 2"},
    }


def test_boundary_estimate_recovers_the_closed_form_and_refinement_solves_it(fake_project) -> None:
    solved: list[float] = []

    def evaluate(value):
        solved.append(value)
        return _beam(value)

    report = run_sizing_sweep(
        None, "proj1", feature_id="f", parameter_name="t",
        values=[4.0, 5.0, 6.0, 7.0, 8.0], stress_limit=100.0,
        refine_boundary=True, evaluate_value=evaluate,
    )
    boundary = report["boundary_estimate"]
    assert boundary["status"] == "estimated"
    assert boundary["interpolated"] is True and boundary["solved"] is False
    # log-log interpolation is exact for a power law: t* = sqrt(30)
    assert boundary["value"] == pytest.approx(30 ** 0.5, rel=1e-12)
    assert boundary["fitted_exponent"] == pytest.approx(-2.0, rel=1e-12)
    assert boundary["bracket"] == {"feasible_value": 6.0, "infeasible_value": 5.0}
    # One extra solve, at the estimate rounded TOWARD the feasible side (5.4772 -> 5.48).
    assert solved == [4.0, 5.0, 6.0, 7.0, 8.0, 5.48]
    assert report["refinement"]["status"] == "feasible"
    assert report["recommended"]["value"] == 5.48
    assert report["recommended"]["origin"] == "boundary_refinement"
    assert report["recommended"]["metrics"]["max_von_mises_stress"] <= 100.0


def test_boundary_not_applicable_when_the_constraint_is_inactive(fake_project) -> None:
    report = run_sizing_sweep(
        None, "proj1", feature_id="f", parameter_name="t",
        values=[6.0, 7.0], stress_limit=100.0, refine_boundary=True,
        evaluate_value=_beam,
    )
    assert report["recommended"]["value"] == 6.0
    assert report["boundary_estimate"]["status"] == "not_applicable"
    assert report["refinement"]["status"] == "skipped"


def test_unreliable_mesh_pass_does_not_win(fake_project) -> None:
    """A min-mass sweep at one mesh size gives its THINNEST variant the fewest
    elements through the thickness. Before, that variant won on a stress an
    unreliable mesh under-predicts, stamped executed_solver_result."""
    def evaluate(value):
        return _beam(value, band="unreliable" if value == 6.0 else "reliable")

    report = run_sizing_sweep(
        None, "proj1", feature_id="f", parameter_name="t",
        values=[5.0, 6.0, 7.0], stress_limit=100.0, evaluate_value=evaluate,
    )
    statuses = {v["value"]: v["status"] for v in report["variants"]}
    assert statuses == {5.0: "infeasible", 6.0: "unreliable_mesh", 7.0: "feasible"}
    assert report["recommended"]["value"] == 7.0
    assert "unreliable mesh" in report["recommendation_reason"]
    unreliable = next(v for v in report["variants"] if v["value"] == 6.0)
    assert unreliable["credibility"]["tier"] == "unverified"

    only_unreliable = run_sizing_sweep(
        None, "proj1", feature_id="f", parameter_name="t",
        values=[5.0, 6.0], stress_limit=100.0, evaluate_value=evaluate,
    )
    assert only_unreliable["recommended"] is None
    assert only_unreliable["safe_to_apply"] is False
    assert "mesh_size_mm" in only_unreliable["recommendation_reason"]
    assert only_unreliable["credibility"]["tier"] == "unverified"


def test_ranking_inputs_are_refused_not_replaced(fake_project) -> None:
    """A typo'd objective used to become min_mass and SF 0 used to become 1.0 —
    a different study, reported as the one asked for — and only after N solves."""
    calls: list[float] = []

    def evaluate(value):
        calls.append(value)
        return _beam(value)

    for kwargs in ({"objective": "min_weight"}, {"safety_factor": 0.0},
                   {"stress_limit": -5.0}, {"displacement_limit": float("nan")}):
        report = run_sizing_sweep(
            None, "proj1", feature_id="f", parameter_name="t", values=[5.0],
            evaluate_value=evaluate, **{"stress_limit": 100.0, **kwargs},
        )
        assert report["status"] == "error" and report["code"] == "bad_input", kwargs
    assert calls == []


def test_range_clamping_is_reported_and_an_outside_range_refused(monkeypatch, tmp_path: Path) -> None:
    pkg = _make_pkg_with_feature_graph(tmp_path)  # declared bounds [2, 8]
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)

    report = run_sizing_sweep(
        None, "proj1", feature_id="f_wall", parameter_name="thickness",
        range={"min": 0.5, "max": 6.0, "steps": 5}, stress_limit=1000.0,
        evaluate_value=_beam,
    )
    assert report["range"]["clamped"] is True
    assert report["range"]["requested"] == {"min": 0.5, "max": 6.0}
    assert report["swept_values"] == [2.0, 3.0, 4.0, 5.0, 6.0]

    outside = run_sizing_sweep(
        None, "proj1", feature_id="f_wall", parameter_name="thickness",
        range={"min": 9.0, "max": 12.0, "steps": 3}, evaluate_value=_beam,
    )
    # Before: clamping produced min=9, max=8 and the sweep walked BACKWARDS
    # through 9.0, 8.5, 8.0 — values outside the request and the bounds.
    assert outside["status"] == "error" and outside["code"] == "bad_input"
    assert "outside" in outside["message"]


def test_mass_uses_density_in_kg_per_m3() -> None:
    from app.sizing_sweep_runner import mass_metrics

    topo = {"entities": [{"type": "solid", "volume": 1.0e6}, {"type": "face", "volume": 5.0}]}
    assert mass_metrics(topo, 2700.0) == {"volume": 1.0e6, "mass": pytest.approx(2.7), "mass_unit": "kg"}
    assert mass_metrics(topo, None)["mass"] == 1.0e6
    assert mass_metrics({}, 2700.0) == {}


def test_invalid_density_is_refused_by_both_runners(fake_project, monkeypatch, tmp_path: Path) -> None:
    """A negative density turns min_mass into max_mass: the heaviest feasible
    design was recommended, stamped safe_to_apply."""
    from app.doe_sizing_runner import run_doe_sizing_study

    def boom(*_args):
        raise AssertionError("no variant may be solved for a refused input")

    for density in (-2700.0, 0.0, float("nan"), True, "2700"):
        sweep = run_sizing_sweep(
            None, "proj1", feature_id="f", parameter_name="t", values=[5.0, 6.0],
            stress_limit=100.0, density=density, evaluate_value=boom,
        )
        assert sweep["code"] == "bad_input" and "density" in sweep["message"], density
        doe = run_doe_sizing_study(
            None, "proj1",
            parameters=[{"featureId": "f", "parameterName": "a", "values": [1.0]},
                        {"featureId": "f", "parameterName": "b", "values": [1.0]}],
            stress_limit=100.0, density=density, evaluate_design_point=boom,
        )
        assert doe["code"] == "bad_input" and "density" in doe["message"], density


def test_a_silent_solver_crash_is_not_blamed_on_the_mesh() -> None:
    """ccx 2.23 intermittently exits 0xFFFFFFFF with no *ERROR and no FRD; the
    same deck solves on a re-run. That was reported `likely_cause: mesh` with a
    finer mesh suggested — the wrong advice."""
    from app.sizing_sweep_runner import record_from_solve

    crash = {
        "solver_executed": False, "status": "solver_error",
        "error": "CalculiX returned code 4294967295 or produced no FRD",
        "failure_kind": "solver_crash_undiagnosed",
        "solver_diagnosis": ["Solver exited with a non-zero return code — see the log."],
        "solver_retry": {"attempts": 2, "first_returncode": 4294967295, "succeeded": False},
    }
    record = record_from_solve(crash, {}, None, 2.0)
    assert record["likely_cause"] == "solver_crash"
    assert record["suggested_retry"]["mesh_size_mm"] == 2.0
    assert "automatic retry" in record["suggested_retry"]["reason"]
    assert record["solver_retry"]["attempts"] == 2

    reported = {**crash, "failure_kind": "solver_reported_error",
                "solver_diagnosis": ["Solver reported: *ERROR in e_c3d: nonpositive jacobian"]}
    reported.pop("solver_retry")
    assert record_from_solve(reported, {}, None, 2.0)["likely_cause"] == "mesh"


def test_a_reported_error_is_blamed_on_the_mesh_only_when_it_names_the_mesh() -> None:
    """Every reported abort used to be `likely_cause: mesh` + a finer mesh. Measured
    on ccx 2.23 (#549): the automatic retry of a silent crash reported
    `*ERROR in bounadd: increase nboun_` — ccx's own array sizing, not the mesh —
    and the identical deck solved on other runs."""
    from app.sizing_sweep_runner import record_from_solve

    base = {
        "solver_executed": False, "status": "solver_error",
        "error": "CalculiX returned code 201 or produced no FRD",
        "failure_kind": "solver_reported_error",
    }
    internal = record_from_solve(
        {**base, "solver_diagnosis": ["Solver reported: *ERROR in bounadd: increase nboun_"]},
        {}, None, 2.0,
    )
    assert internal["likely_cause"] == "solver_internal_limit"
    assert internal["suggested_retry"]["mesh_size_mm"] == 2.0  # same mesh, not finer

    other = record_from_solve(
        {**base, "solver_diagnosis": ["Solver reported: *ERROR in mafillsm: something else"]},
        {}, None, 2.0,
    )
    assert other["likely_cause"] == "solver_error"
    assert other["suggested_retry"]["mesh_size_mm"] == 2.0


def test_crash_retry_reruns_the_same_deck_once(monkeypatch, tmp_path: Path) -> None:
    from app import simulation_runner as sr

    deck = tmp_path / "aieng_run.inp"
    deck.write_text("*NODE\n")
    frd = tmp_path / "aieng_run.frd"

    def scripted(outcomes):
        calls: list[int] = []

        def run(inp_path, work_dir, timeout=180):
            rc, log, makes_frd = outcomes[len(calls)]
            calls.append(rc)
            if makes_frd:
                frd.write_text("frd")
            return rc, log, frd if makes_frd else None
        return run, calls

    estimates = "Estimated memory consumption 12 MB\n"
    run, calls = scripted([(4294967295, estimates, False), (0, "Job finished", True)])
    monkeypatch.setattr(sr, "_run_calculix", run)
    rc, _log, out, retry = sr._run_calculix_with_crash_retry(deck, tmp_path)
    assert (rc, out, len(calls)) == (0, frd, 2)
    assert retry == {"attempts": 2, "first_returncode": 4294967295, "succeeded": True,
                     "reason": retry["reason"]}

    frd.unlink()
    run, calls = scripted([(201, " *ERROR reading *CLOAD: node set X has not yet been defined", False)])
    monkeypatch.setattr(sr, "_run_calculix", run)
    rc, _log, out, retry = sr._run_calculix_with_crash_retry(deck, tmp_path)
    assert (rc, out, retry, len(calls)) == (201, None, None, 1)  # a reported error is never retried

    run, calls = scripted([(0, "Job finished", True)])
    monkeypatch.setattr(sr, "_run_calculix", run)
    assert sr._run_calculix_with_crash_retry(deck, tmp_path)[3] is None and len(calls) == 1


def test_a_setup_that_cannot_be_prepared_fails_the_variant_not_the_study(monkeypatch, tmp_path: Path) -> None:
    """Copy + normalisation ran outside the solver's error boundary, so one
    malformed package raised out of solve_package_static and aborted the study."""
    from app import simulation_runner as sr

    pkg = tmp_path / "variant.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("simulation/cae_mapping.json", "{not json")
    monkeypatch.setattr(sr, "check_simulation_tools", lambda: {"ready": True, "missing": []})
    result = sr.solve_package_static(pkg)
    assert result["solver_executed"] is False
    assert result["status"] == "solve_error"
    assert "could not prepare the CAE setup" in result["error"]
