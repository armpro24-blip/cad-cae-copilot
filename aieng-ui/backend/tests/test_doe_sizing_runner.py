"""Tests for multi-parameter DOE sizing study runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.doe_sizing_runner import (
    _generate_design_points,
    _safe_param_key,
    run_doe_sizing_study,
)
from app import project_io


def test_safe_param_key_and_parse() -> None:
    key = _safe_param_key("f_wall", "thickness")
    assert key == "f_wall#thickness"


def test_full_factorial_generates_cartesian_product() -> None:
    points = _generate_design_points(
        {"a": [1.0, 2.0], "b": [10.0, 20.0]},
        "full_factorial",
        budget=64,
    )
    assert len(points) == 4
    assert all("a" in p and "b" in p for p in points)


def test_full_factorial_respects_budget() -> None:
    with pytest.raises(ValueError, match="budget"):
        _generate_design_points(
            {"a": list(range(10)), "b": list(range(10))},
            "full_factorial",
            budget=64,
        )


def _strata(points: list[dict[str, float]], key: str, lo: float, hi: float) -> list[int]:
    n = len(points)
    return sorted(min(int((p[key] - lo) / (hi - lo) * n), n - 1) for p in points)


def test_lhs_is_a_latin_hypercube() -> None:
    """McKay, Beckman & Conover (1979): every 1-D projection has exactly one point
    per stratum. The previous sampler shuffled replicated grid levels, so a
    6-point "LHS" over [1, 3] put three points on each of two levels and none in
    four of the six strata."""
    bounds = {"a": (1.0, 3.0), "b": (10.0, 20.0)}
    points = _generate_design_points(None, "lhs", budget=8, seed=7, bounds=bounds)
    assert len(points) == 8
    for key, (lo, hi) in bounds.items():
        assert _strata(points, key, lo, hi) == list(range(8)), key
        assert all(lo <= p[key] <= hi for p in points)
    # Seeded → reproducible; another seed → another design.
    assert points == _generate_design_points(None, "lhs", budget=8, seed=7, bounds=bounds)
    assert points != _generate_design_points(None, "lhs", budget=8, seed=8, bounds=bounds)


def test_budget_outside_cap_is_refused() -> None:
    for budget in (1, 65):
        with pytest.raises(ValueError, match="budget"):
            _generate_design_points({"a": [1.0], "b": [2.0]}, "full_factorial", budget=budget)


def test_lhs_study_reports_seed_and_refuses_discrete_levels(monkeypatch, tmp_path: Path) -> None:
    pkg = tmp_path / "proj.aieng"
    _make_minimal_pkg(pkg)
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)
    seen: list[dict[str, float]] = []

    def evaluate(point):
        seen.append(point)
        return {"value": str(point), "parameters": dict(point),
                "metrics": {"mass": 1.0, "max_von_mises_stress": 1.0}, "solver_executed": True}

    report = run_doe_sizing_study(
        None, "proj1", method="lhs", budget=5,
        parameters=[
            {"featureId": "f_wall", "parameterName": "thickness", "range": {"min": 2.0, "max": 4.0}},
            # declared max is 5.0 → clamped
            {"featureId": "f_rib", "parameterName": "thickness", "range": {"min": 1.0, "max": 9.0}},
        ],
        stress_limit=200.0, evaluate_design_point=evaluate,
    )
    assert report["status"] == "ok", report
    assert isinstance(report["seed"], int)
    assert report["parameters"][1]["interval"] == [1.0, 5.0]
    assert len(seen) == 5 and all(1.0 <= p["f_rib#thickness"] <= 5.0 for p in seen)

    refused = run_doe_sizing_study(
        None, "proj1", method="lhs", budget=5,
        parameters=[
            {"featureId": "f_wall", "parameterName": "thickness", "values": [2.0, 3.0]},
            {"featureId": "f_rib", "parameterName": "thickness", "range": {"min": 1.0, "max": 2.0}},
        ],
        evaluate_design_point=evaluate,
    )
    assert refused["code"] == "bad_input"
    assert "Latin hypercube" in refused["message"]


def test_doe_default_evaluator_ranks_mass_from_the_real_solver_shape(monkeypatch, tmp_path: Path) -> None:
    """The DOE derived mass from a `volume_mm3` solver metric no solver writes
    (`solve_package_static` returns only stress/displacement), so its DEFAULT
    objective, min_mass, had nothing to rank and could never recommend a point —
    while its tests injected `mass` directly and passed. Mass now comes from the
    variant's topology, as in opt.sizing_sweep."""
    import re
    import zipfile

    from app import cad_generation, simulation_runner

    pkg = tmp_path / "proj.aieng"
    _make_minimal_pkg(pkg)
    with zipfile.ZipFile(pkg, "a") as zf:
        zf.writestr("geometry/source.py", "WALL_THICKNESS = 2.5\nRIB_THICKNESS = 1.5\nresult = None\n")
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)

    def fake_build(settings, source, **kwargs):
        wall = float(re.search(r"WALL_THICKNESS = ([0-9.]+)", source).group(1))
        rib = float(re.search(r"RIB_THICKNESS = ([0-9.]+)", source).group(1))
        topo = {"entities": [{"type": "solid", "volume": 100.0 * (wall + rib)}],
                "_stress": 1000.0 / (wall * rib)}
        return {"topo": topo, "step_bytes": b"", "feature_graph": {}}

    def fake_solve(package_path, **kwargs):
        with zipfile.ZipFile(package_path) as zf:
            source = zf.read("geometry/source.py").decode()
        wall = float(re.search(r"WALL_THICKNESS = ([0-9.]+)", source).group(1))
        rib = float(re.search(r"RIB_THICKNESS = ([0-9.]+)", source).group(1))
        # exactly the keys solve_package_static returns
        return {"solver_executed": True, "status": "success",
                "metrics": {"max_von_mises_stress": 1000.0 / (wall * rib), "max_displacement": 0.1},
                "mesh_accuracy": {"band": "reliable"}}

    def fake_write(pkg_path, generated_code, **kwargs):
        with zipfile.ZipFile(pkg_path) as zf:
            members = {n: zf.read(n) for n in zf.namelist() if n != "geometry/source.py"}
        with zipfile.ZipFile(pkg_path, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)
            zf.writestr("geometry/source.py", generated_code)

    monkeypatch.setattr(cad_generation, "_execute_build123d_cached", fake_build)
    monkeypatch.setattr(cad_generation, "_write_cad_artifacts", fake_write)
    monkeypatch.setattr(simulation_runner, "solve_package_static", fake_solve)

    report = run_doe_sizing_study(
        None, "proj1",
        parameters=[
            {"featureId": "f_wall", "parameterName": "thickness", "values": [2.0, 3.0]},
            {"featureId": "f_rib", "parameterName": "thickness", "values": [1.0, 2.0]},
        ],
        stress_limit=300.0, density=2700.0,
    )
    assert report["status"] == "ok", report
    # stress = 1000/(w r) <= 300 → w r >= 3.33: (2,2) mass 400, (3,2) 500; (3,1) is 333 MPa.
    assert report["recommended"]["parameters"] == {"f_wall#thickness": 2.0, "f_rib#thickness": 2.0}
    assert report["recommended"]["metrics"]["mass"] == pytest.approx(400.0 * 1e-9 * 2700.0)
    assert report["recommended"]["metrics"]["mass_unit"] == "kg"


def test_doe_missing_constant_is_an_error_not_the_baseline_value(monkeypatch, tmp_path: Path) -> None:
    """A constant absent from source.py was skipped silently, so the point was
    solved at the BASELINE value while labelled with the requested one."""
    import zipfile

    from app.doe_sizing_runner import make_default_evaluate_design_point

    pkg = tmp_path / "proj.aieng"
    _make_minimal_pkg(pkg)
    with zipfile.ZipFile(pkg, "a") as zf:
        zf.writestr("geometry/source.py", "WALL_THICKNESS = 2.5\nresult = None\n")  # no RIB_THICKNESS
    evaluate = make_default_evaluate_design_point(
        None, pkg,
        [{"featureId": "f_wall", "parameterName": "thickness"},
         {"featureId": "f_rib", "parameterName": "thickness"}],
        mesh_size_mm=None, timeout=10, density=None,
    )
    out = evaluate({"f_wall#thickness": 3.0, "f_rib#thickness": 2.0})
    assert out["solver_executed"] is False
    assert "RIB_THICKNESS" in out["error"]


def _fake_evaluator(expected: dict[tuple[float, float], dict[str, float]]) -> Any:
    def evaluate(point: dict[str, float]) -> dict[str, Any]:
        key = (point["f_wall#thickness"], point["f_rib#thickness"])
        metrics = expected.get(key, {"mass": 10.0, "max_von_mises_stress": 100.0})
        return {
            "value": f"wall={point['f_wall#thickness']},rib={point['f_rib#thickness']}",
            "parameters": dict(point),
            "metrics": dict(metrics),
            "solver_executed": True,
            "error": None,
        }
    return evaluate


def _make_minimal_pkg(path: Path) -> None:
    import zipfile
    feature_graph = {
        "features": [
            {
                "id": "f_wall",
                "parameters": {
                    "thickness": {"current_value": 2.5, "min_value": 1.0, "max_value": 10.0, "cad_parameter_name": "WALL_THICKNESS"}
                },
            },
            {
                "id": "f_rib",
                "parameters": {
                    "thickness": {"current_value": 1.5, "min_value": 0.5, "max_value": 5.0, "cad_parameter_name": "RIB_THICKNESS"}
                },
            },
        ]
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph))


def test_doe_ranks_2_parameter_full_factorial(monkeypatch, tmp_path: Path) -> None:
    pkg = tmp_path / "proj.aieng"
    _make_minimal_pkg(pkg)
    monkeypatch.setattr(project_io, "get_project", lambda settings, pid: {"aieng_file": str(pkg)})
    monkeypatch.setattr(project_io, "resolve_project_path", lambda settings, pid, f: pkg)

    expected = {
        (2.0, 1.0): {"mass": 8.0, "max_von_mises_stress": 210.0},  # infeasible
        (2.0, 2.0): {"mass": 9.0, "max_von_mises_stress": 180.0},  # feasible
        (3.0, 1.0): {"mass": 10.0, "max_von_mises_stress": 150.0},  # feasible
        (3.0, 2.0): {"mass": 12.0, "max_von_mises_stress": 120.0},  # feasible
    }

    report = run_doe_sizing_study(
        None,
        "proj1",
        parameters=[
            {"featureId": "f_wall", "parameterName": "thickness", "values": [2.0, 3.0]},
            {"featureId": "f_rib", "parameterName": "thickness", "values": [1.0, 2.0]},
        ],
        method="full_factorial",
        objective="min_mass",
        stress_limit=200.0,
        evaluate_design_point=_fake_evaluator(expected),
    )

    assert report["status"] == "ok"
    assert report["method"] == "full_factorial"
    assert report["design_points_count"] == 4
    feasible = [v for v in report["variants"] if v["status"] == "feasible"]
    assert len(feasible) == 3
    # Best feasible is the lightest feasible point.
    assert report["recommended"]["parameters"]["f_wall#thickness"] == 2.0
    assert report["recommended"]["parameters"]["f_rib#thickness"] == 2.0


def test_doe_rejects_single_parameter() -> None:
    report = run_doe_sizing_study(
        None,
        "proj1",
        parameters=[{"featureId": "f_wall", "parameterName": "thickness", "values": [2.0, 3.0]}],
    )
    assert report["status"] == "error"
    assert report["code"] == "bad_input"
