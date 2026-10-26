"""Sizing optimization verified against beam theory, through the real solver.

A rectangular cantilever — length L, width b, thickness t, fixed at one end,
tip force F — has closed-form Euler-Bernoulli answers:

    sigma_max = 6 F L / (b t^2)        delta_tip = 4 F L^3 / (E b t^3)

so the minimum-mass thickness under a stress limit is t* = sqrt(6 F L / (b sigma))
and under a deflection limit t* = (4 F L^3 / (E b delta))^(1/3). Those are the
answers `opt.sizing_sweep` and `opt.doe_sizing_study` must reproduce when every
variant is meshed (Gmsh, C3D10) and solved (CalculiX) for real.

Run as a user would, this chain returned NOTHING before: `cae.setup_static`
writes `@face:` targets and no `cae_mapping.json`, and the batch solver handed
them to CalculiX as an undefined node set, so all 5 sweep variants and all 12
DOE points aborted with code 201 — and the sweep blamed the mesh.

Every number below comes from tools invoked through `runtime.invoke_tool`, the
surface a connected agent drives.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

pytest.importorskip("build123d", reason="the V&V run needs the real CAD stack")
pytest.importorskip("gmsh", reason="the V&V run needs the real mesher")

F_N, L_MM, B_MM, E_MPA = 100.0, 100.0, 20.0, 69000.0  # Al6061-T6 from the material library
SIGMA_ALLOW = 100.0
DELTA_ALLOW = 1.0
MESH_MM = 2.0  # >= 2 quadratic elements through the thinnest (4 mm) variant: band "reliable"

_CODE = (
    "from build123d import *\n"
    f"BEAM_LENGTH = {L_MM}\n"
    f"BEAM_WIDTH = {B_MM}\n"
    "BEAM_THICKNESS = 8.0\n"
    "beam = Box(BEAM_LENGTH, BEAM_WIDTH, BEAM_THICKNESS)\n"
    "beam.label = 'beam'\n"
    "result = Compound(children=[beam])\n"
)


def sigma(b: float, t: float) -> float:
    return 6 * F_N * L_MM / (b * t * t)


def delta(b: float, t: float) -> float:
    return 4 * F_N * L_MM**3 / (E_MPA * b * t**3)


def _ccx_available() -> bool:
    from app.simulation_runner import _find_ccx

    try:
        return bool(_find_ccx())
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def studies(tmp_path_factory: pytest.TempPathFactory):
    if not _ccx_available():
        pytest.skip("CalculiX is not runnable here")

    from app import runtime
    from app.app_factory import create_app
    from app.config import Settings
    from app.main import default_project, save_project

    root = tmp_path_factory.mktemp("sizing_vv")
    previous_cwd = Path.cwd()
    os.chdir(root)  # `.aieng_cache` is CWD-relative; never share it across runs
    try:
        workspace = root / "workspace"
        settings = Settings(
            platform_root=root / "platform", workspace_root=workspace,
            data_root=root / "data", aieng_root=_WORKSPACE_ROOT / "aieng",
            sample_step=workspace / "sample.step",
        )
        create_app(settings)
        project_id = save_project(settings, default_project("sizing_vv"))["id"]
        built = runtime.invoke_tool(
            "cad.execute_build123d", {"project_id": project_id, "code": _CODE, "timeout": 240}
        )
        assert built["status"] == "ok", built
        setup = runtime.invoke_tool("cae.setup_static", {
            "project_id": project_id, "material": "Al6061-T6", "fix": "left",
            "load": {"at": "right", "force_n": F_N, "direction": "-Z"},
        })
        assert setup["status"] == "ok", setup
        params = {
            p["cad_parameter_name"]: p
            for p in runtime.invoke_tool(
                "cad.list_editable_parameters", {"project_id": project_id}
            )["parameters"]
        }
        t, w = params["BEAM_THICKNESS"], params["BEAM_WIDTH"]

        def ref(p):
            return {"featureId": p["featureId"], "parameterName": p["parameterName"]}

        common = {"project_id": project_id, "mesh_size_mm": MESH_MM, "timeout": 600}
        return {
            "stress": runtime.invoke_tool("opt.sizing_sweep", {
                **common, **ref(t), "values": [4, 5, 6, 7, 8],
                "stress_limit": SIGMA_ALLOW, "refine_boundary": True,
            }),
            "deflection": runtime.invoke_tool("opt.sizing_sweep", {
                **common, **ref(t), "values": [4, 5, 6, 7, 8],
                "displacement_limit": DELTA_ALLOW,
            }),
            "doe": runtime.invoke_tool("opt.doe_sizing_study", {
                **common,
                "parameters": [
                    {**ref(w), "values": [15, 20, 25]},
                    {**ref(t), "values": [4, 5, 6, 7]},
                ],
                "stress_limit": SIGMA_ALLOW, "density": 2700.0,
            }),
        }
    finally:
        os.chdir(previous_cwd)


def _grid(report):
    return [v for v in report["variants"] if v.get("origin") != "boundary_refinement"]


def test_every_variant_solves_on_a_reliable_mesh(studies) -> None:
    for name, report in studies.items():
        assert report["status"] == "ok", (name, report)
        for variant in report["variants"]:
            assert variant["solver_executed"] is True, (name, variant)
            assert variant["mesh_accuracy"]["band"] == "reliable", (name, variant)
            assert variant["credibility"]["tier"] == "executed_solver_result", (name, variant)


def test_sweep_metrics_match_beam_theory(studies) -> None:
    """Tip deflection within 3% of Euler-Bernoulli (measured 0.981-0.990x: the
    solid is slightly stiffer than the beam because b/t = 2.5-5 partly restrains
    anticlastic bending). The peak von Mises stress is taken over the whole
    model, so it includes the clamped root, where the fully fixed face adds a
    Poisson-restraint concentration to 6FL/(bt^2) that grows with refinement;
    measured 0.997-1.036x at this mesh. Bound: -5% (a larger deficit is the
    non-conservative failure the mesh band exists to catch) to +10%."""
    for variant in _grid(studies["stress"]):
        t = float(variant["value"])
        metrics = variant["metrics"]
        assert metrics["max_displacement"] == pytest.approx(delta(B_MM, t), rel=0.03), t
        ratio = metrics["max_von_mises_stress"] / sigma(B_MM, t)
        assert 0.95 <= ratio <= 1.10, (t, ratio)


def test_stress_sweep_picks_the_grid_point_above_the_closed_form_optimum(studies) -> None:
    report = studies["stress"]
    t_star = math.sqrt(6 * F_N * L_MM / (B_MM * SIGMA_ALLOW))  # 5.477 mm
    statuses = {float(v["value"]): v["status"] for v in _grid(report)}
    assert statuses == {4.0: "infeasible", 5.0: "infeasible", 6.0: "feasible",
                        7.0: "feasible", 8.0: "feasible"}
    boundary = report["boundary_estimate"]
    assert boundary["bracket"] == {"feasible_value": 6.0, "infeasible_value": 5.0}
    assert boundary["solved"] is False
    # sigma ~ t^-2, so the +/-10% stress band above maps to +/-5% in t.
    # Measured: 5.543 mm vs t* = 5.477 mm (+1.2%), fitted exponent -2.11.
    assert boundary["value"] == pytest.approx(t_star, rel=0.05)
    assert abs(boundary["fitted_exponent"] + 2.0) < 0.15  # sigma ~ t^-2


def test_refined_winner_is_solved_and_respects_the_margin(studies) -> None:
    report = studies["stress"]
    refined = report["refinement"]
    assert refined["status"] == "feasible", refined
    winner = report["recommended"]
    assert winner["value"] == refined["value"]
    assert winner["metrics"]["max_von_mises_stress"] <= SIGMA_ALLOW
    assert report["safe_to_apply"] is True
    # Lighter than the best grid point it replaces, and within 5% of t*.
    # Measured: 5.55 mm solved at 99.14 MPa (t* = 5.477 mm; best grid point 6 mm).
    t_star = math.sqrt(6 * F_N * L_MM / (B_MM * SIGMA_ALLOW))
    assert winner["value"] < 6.0
    assert winner["value"] == pytest.approx(t_star, rel=0.05)


def test_deflection_sweep_matches_the_closed_form_optimum(studies) -> None:
    report = studies["deflection"]
    t_star = (4 * F_N * L_MM**3 / (E_MPA * B_MM * DELTA_ALLOW)) ** (1 / 3)  # 6.618 mm
    assert report["recommended"]["value"] == 7.0
    boundary = report["boundary_estimate"]
    assert boundary["governing_constraint"] == "max_displacement"
    # delta ~ t^-3: the 3% deflection band maps to 1% in t. Measured 6.589 vs 6.618.
    assert boundary["value"] == pytest.approx(t_star, rel=0.01)
    assert abs(boundary["fitted_exponent"] + 3.0) < 0.1  # delta ~ t^-3


def test_doe_recommends_the_lightest_feasible_width_thickness_pair(studies) -> None:
    """sigma <= 100 MPa needs b t^2 >= 600 mm^3. Over b in {15,20,25} x t in
    {4..7} the feasible minimum-area pairs are (15,7) 105, (20,6) 120, (25,5)
    125 mm^2, so (15, 7) wins. (25, 5) is 4% under the limit in theory and
    solves 0.7% OVER it (100.7 MPa, the clamp excess at b/t = 5) — inside the
    +10% band, so its status is not asserted; every other point is >= 10% clear."""
    report = studies["doe"]
    assert report["design_points_count"] == 12
    for variant in report["variants"]:
        b, t = variant["parameters"].values()
        if abs(sigma(b, t) / SIGMA_ALLOW - 1.0) < 0.10:
            continue
        expected = "feasible" if sigma(b, t) <= SIGMA_ALLOW else "infeasible"
        assert variant["status"] == expected, variant
    winner = report["recommended"]
    assert list(winner["parameters"].values()) == [15.0, 7.0]
    assert winner["metrics"]["mass"] == pytest.approx(L_MM * 15 * 7 * 1e-9 * 2700, rel=1e-6)
    assert winner["metrics"]["max_von_mises_stress"] <= SIGMA_ALLOW
