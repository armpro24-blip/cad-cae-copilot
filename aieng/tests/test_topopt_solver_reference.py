"""The sparse, vectorized SIMP solvers against independent references.

* the pre-rewrite dense solvers (verbatim copies in ``_topopt_reference``): run
  with ``emin=0, xmin=1e-3`` the new core is the same algorithm, so iterations,
  compliance history and density field must agree to round-off;
* a line-by-line Python port of ``top88.m`` (Andreassen et al. 2011, SMO
  43:1-16) on its own half-MBB benchmark (60x20, volfrac 0.5, penal 3, rmin 1.5),
  for both of its filters — the defaults of ``simp_2d`` ARE top88 (modified SIMP,
  Emin=1e-9), so no option is needed to match it.

Reference numbers below come from running the port, not from the paper.
"""
from __future__ import annotations

import numpy as np
import pytest

# scipy is a core dependency (sparse solves); no importorskip, so a missing
# install fails loudly instead of silently skipping the reference checks.
from _topopt_reference import legacy_simp_2d, legacy_simp_3d, top88  # noqa: E402
from aieng.converters.topology_optimization import (  # noqa: E402
    _solve_reduced,
    run_topology_optimization,
    simp_2d,
    simp_3d,
)

CLASSIC = {"emin": 0.0, "xmin": 1e-3}   # the legacy solver's interpolation


def _rel(a: list[float], b: list[float]) -> float:
    assert len(a) == len(b)
    return max(abs(x - y) / abs(y) for x, y in zip(a, b))


@pytest.mark.parametrize("problem", [
    {"grid": {"nelx": 24, "nely": 8}, "volfrac": 0.5, "max_iters": 60, "bcs": {"preset": "cantilever"}},
    {"grid": {"nelx": 30, "nely": 10}, "volfrac": 0.4, "max_iters": 300, "bcs": {"preset": "mbb_beam"}},
    {"grid": {"nelx": 20, "nely": 12}, "volfrac": 0.35, "rmin": 2.3, "max_iters": 15,
     "bcs": {"supports": [{"cells": [[0, j] for j in range(12)]}],
             "loads": [{"cells": [[19, 6]], "fx": 0.3, "fy": -1.0}]}},
], ids=["cantilever", "mbb", "explicit_truncated"])
def test_simp_2d_reproduces_the_legacy_dense_solver(problem):
    old = legacy_simp_2d(problem)
    new = simp_2d({**problem, **CLASSIC})
    assert new["iterations"] == old["iterations"]
    assert _rel(new["compliance_history"], old["compliance_history"]) < 1e-8
    assert np.abs(np.asarray(new["density"]) - old["x"]).max() <= 5.01e-5   # output rounded to 4 dp
    assert new["final_change"] == pytest.approx(old["final_change"], abs=1e-6)


def test_simp_2d_reproduces_the_legacy_solver_with_result_guidance():
    nelx, nely = 16, 8
    preserve = np.zeros((nely, nelx))
    preserve[2:4, 10:13] = 1
    weight = np.ones((nely, nelx))
    weight[:, :4] = 1.8
    problem = {"grid": {"nelx": nelx, "nely": nely}, "volfrac": 0.4, "max_iters": 25,
               "bcs": {"preset": "cantilever"}, "use_result_guidance": True,
               "guidance_field": {"preserve_mask": preserve.tolist(), "stiffness_weight_field": weight.tolist(),
                                  "options": {"preserve_min_density": 0.6}}}
    old = legacy_simp_2d(problem)
    new = simp_2d({**problem, **CLASSIC})
    assert new["guidance_consumed"] and new["guidance_cells_preserved"] == 6
    assert new["iterations"] == old["iterations"]
    assert _rel(new["compliance_history"], old["compliance_history"]) < 1e-8
    assert np.abs(np.asarray(new["density"]) - old["x"]).max() <= 5.01e-5


@pytest.mark.parametrize("problem", [
    {"grid": {"nx": 8, "ny": 4, "nz": 3}, "volume_fraction": 0.4, "max_iters": 30},
    {"grid": {"nx": 7, "ny": 4, "nz": 3}, "volume_fraction": 0.3, "rmin": 1.8, "max_iters": 12,
     "bcs": {"supports": [{"cells": [[0, j, k] for j in range(4) for k in range(3)]}],
             "loads": [{"cells": [[6, 2, 1]], "fx": 0.0, "fy": -1.0, "fz": 0.4}]}},
], ids=["cantilever_3d", "explicit"])
def test_simp_3d_reproduces_the_legacy_dense_solver(problem):
    old = legacy_simp_3d(problem)
    new = simp_3d({**problem, **CLASSIC})
    g = problem["grid"]
    assert new["iterations"] == old["iterations"]
    assert _rel(new["compliance_history"], old["compliance_history"]) < 1e-8
    old_x = old["x"].reshape(g["nz"], g["ny"], g["nx"])
    assert np.abs(np.asarray(new["density_3d"]) - old_x).max() <= 5.01e-5


@pytest.mark.parametrize("ft, filt, iters, c_final", [
    (1, "sensitivity", 94, 203.1925),
    (2, "density", 127, 218.8032),
])
def test_simp_2d_matches_top88_on_the_mbb_beam(ft, filt, iters, c_final):
    """MBB 60x20 / volfrac 0.5 / penal 3 / rmin 1.5 — top88's own example.

    ``iters`` / ``c_final`` pin what the port produces, so a change to the port
    itself is caught too; the solver is compared to the port, iteration by
    iteration."""
    ref = top88(60, 20, 0.5, 3.0, 1.5, ft)
    assert ref["iterations"] == iters
    assert ref["compliance_history"][-1] == pytest.approx(c_final, abs=1e-4)
    new = simp_2d({"grid": {"nelx": 60, "nely": 20}, "volfrac": 0.5, "penalty": 3.0, "rmin": 1.5,
                   "filter": filt, "bcs": {"preset": "mbb_beam"}})
    assert new["iterations"] == ref["iterations"]
    assert new["converged"] is True and new["max_iters_reached"] is False
    assert new["final_change"] <= 0.01
    assert _rel(new["compliance_history"], ref["compliance_history"]) < 1e-8
    assert np.abs(np.asarray(new["density"]) - ref["xPhys"]).max() <= 5.01e-5
    assert new["achieved_volume_fraction"] == pytest.approx(0.5, abs=1e-3)


def test_truncated_run_is_reported_as_not_converged():
    """The old default (40 iterations) stops MBB 60x20 less than halfway to the
    94 iterations it needs, and the result looked exactly like a finished one."""
    res = run_topology_optimization(
        {"grid": {"nelx": 60, "nely": 20}, "volfrac": 0.5, "max_iters": 40, "bcs": {"preset": "mbb_beam"}})
    r = res["result"]
    assert r["converged"] is False and r["max_iters_reached"] is True
    assert r["iterations"] == 40 and r["final_change"] > r["convergence_tolerance"] == 0.01
    assert any("NOT converged" in w for w in res["warnings"])
    full = run_topology_optimization(
        {"grid": {"nelx": 60, "nely": 20}, "volfrac": 0.5, "bcs": {"preset": "mbb_beam"}})["result"]
    assert full["converged"] is True and full["max_iters"] == 200 and full["iterations"] == 94


def test_non_discreteness_is_reported_and_tracks_greyness():
    """Sigmund (2007) M_nd = 4/n * sum x(1-x) * 100 %. The density filter leaves a
    greyer design than the sensitivity filter on the same problem."""
    base = {"grid": {"nelx": 60, "nely": 20}, "volfrac": 0.5, "bcs": {"preset": "mbb_beam"}}
    sens = simp_2d(base)
    dens = simp_2d({**base, "filter": "density"})
    x = np.asarray(sens["density"])
    assert sens["non_discreteness_percent"] == pytest.approx(400 * np.mean(x * (1 - x)), abs=0.05)
    assert 0 < sens["non_discreteness_percent"] < dens["non_discreteness_percent"] < 100
    uniform = simp_2d({**base, "max_iters": 1, "move": 1e-9})   # never leaves volfrac = 0.5
    assert uniform["non_discreteness_percent"] == pytest.approx(100.0, abs=1e-3)


@pytest.mark.parametrize("bad", [
    {"volfrac": float("nan")}, {"volfrac": 0.0}, {"volfrac": 1.5}, {"rmin": 0.0},
    {"penalty": 0.5}, {"filter": "heaviside"}, {"max_iters": 0}, {"emin": 0.0, "xmin": 0.0},
    {"grid": {"nelx": 0, "nely": 4}},
])
def test_invalid_options_are_refused_not_coerced(bad):
    with pytest.raises(ValueError):
        simp_2d({"grid": {"nelx": 8, "nely": 4}, "volfrac": 0.5, "bcs": {"preset": "cantilever"}, **bad})


def test_element_stiffness_passes_the_constant_strain_patch_test():
    """Closed form, independent of every port: a unit element under u_x = x has
    strain energy u^T K u = E/(1-nu^2) (2D plane stress) and
    E(1-nu)/((1+nu)(1-2nu)) (3D), and exactly 3 / 6 rigid-body (zero) modes."""
    from aieng.converters.topology_optimization import _element_stiffness, _hex8_stiffness
    nu = 0.3
    K2 = _element_stiffness()
    u2 = np.zeros(8)
    u2[0::2] = [0, 1, 1, 0]                     # local node x-coordinates
    assert u2 @ K2 @ u2 == pytest.approx(1 / (1 - nu ** 2), rel=1e-12)
    assert np.sum(np.abs(np.linalg.eigvalsh(K2)) < 1e-10) == 3
    K3 = _hex8_stiffness(nu)
    u3 = np.zeros(24)
    u3[0::3] = [0, 1, 1, 0, 0, 1, 1, 0]
    assert u3 @ K3 @ u3 == pytest.approx((1 - nu) / ((1 + nu) * (1 - 2 * nu)), rel=1e-12)
    assert np.sum(np.abs(np.linalg.eigvalsh(K3)) < 1e-10) == 6


def test_singular_stiffness_is_refused():
    from scipy.sparse import csc_matrix
    K = csc_matrix(np.array([[1.0, -1.0], [-1.0, 1.0]]))   # free-floating spring: a mechanism
    with pytest.raises(ValueError, match="singular|did not converge"):
        _solve_reduced(K, np.array([1.0, 0.0]))


def test_large_grid_is_feasible_without_a_dense_matrix():
    """200x100 would need a ~13 GB dense stiffness matrix in the old solver."""
    out = simp_2d({"grid": {"nelx": 200, "nely": 100}, "volfrac": 0.4, "rmin": 3.0,
                   "max_iters": 2, "bcs": {"preset": "mbb_beam"}})
    assert out["iterations"] == 2 and out["compliance_history"][1] < out["compliance_history"][0]
    assert out["converged"] is False


# ── PR #544 review findings (each failed before its fix) ─────────────────────

@pytest.mark.parametrize("bcs", [{"loads": []}, {"supports": []}, {"supports": [], "loads": []}])
def test_explicitly_empty_bc_lists_are_refused_not_replaced_by_a_preset(bcs):
    """A caller override ``problem: {bcs: {supports: [], loads: []}}`` merged over a
    derived problem clears the part's real BCs; it used to solve the cantilever
    preset with only a warning. Keys present = an explicit (here empty) request."""
    from aieng.converters.topology_optimization import TopologyProblemRefused
    with pytest.raises(TopologyProblemRefused):
        simp_2d({"grid": {"nelx": 12, "nely": 6}, "volfrac": 0.5, "max_iters": 4, "bcs": bcs})
    with pytest.raises(TopologyProblemRefused):
        simp_3d({"grid": {"nx": 6, "ny": 3, "nz": 2}, "volume_fraction": 0.4, "max_iters": 3, "bcs": bcs})


def test_named_preset_with_empty_lists_still_solves_the_preset_it_names():
    out = simp_2d({"grid": {"nelx": 12, "nely": 6}, "volfrac": 0.5, "max_iters": 4,
                   "bcs": {"preset": "mbb_beam", "supports": [], "loads": []}})
    assert out["bcs_preset"] == "mbb_beam" and not any("preset" in w for w in out["warnings"])


def test_density_filter_with_a_preserve_mask_is_refused():
    """The floor is applied to the design variable, but the density filter averages
    it into the physical field — measured: a preserved cell (floor 0.9) exported at
    0.587 while still counted as preserved."""
    nelx, nely = 30, 10
    pm = np.zeros((nely, nelx))
    pm[0, 15] = 1
    with pytest.raises(ValueError, match="density"):
        simp_2d({"grid": {"nelx": nelx, "nely": nely}, "volfrac": 0.3, "filter": "density", "rmin": 2.5,
                 "bcs": {"preset": "cantilever"}, "use_result_guidance": True,
                 "guidance_field": {"preserve_mask": pm.tolist(), "options": {"preserve_min_density": 0.9}}})


@pytest.mark.parametrize("filt", ["sensitivity", "density"])
def test_full_volume_fraction_returns_a_solid_design(filt):
    """volfrac=1.0 makes the volume constraint inactive: the bisection never found a
    multiplier and divided by zero once its bracket underflowed."""
    out = simp_2d({"grid": {"nelx": 8, "nely": 4}, "volfrac": 1.0, "max_iters": 5,
                   "filter": filt, "bcs": {"preset": "cantilever"}})
    assert out["achieved_volume_fraction"] == 1.0 and out["converged"] is True
    out3 = simp_3d({"grid": {"nx": 4, "ny": 2, "nz": 2}, "volume_fraction": 1.0, "max_iters": 5,
                    "filter": filt})
    assert out3["achieved_volume_fraction"] == 1.0
