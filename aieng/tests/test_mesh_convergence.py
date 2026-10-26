"""Tests for the ASME V&V-20 mesh-convergence analyzer.

Synthetic series follow phi(h) = phi_ext + C*h^p, so the apparent order, the
Richardson-extrapolated asymptote, and the GCI are known in closed form and can be
asserted exactly.
"""
from __future__ import annotations

import pytest

from aieng.converters.mesh_convergence import analyze_mesh_convergence


def _levels(pairs):
    # pairs: list of (size, value)
    return [{"size": s, "value": v} for s, v in pairs]


def test_not_converged_coarse_series_recovers_order_and_asymptote() -> None:
    # phi_ext=10, C=1, p=2 at h=[1,2,4] -> phi=[11,14,26]
    report = analyze_mesh_convergence(_levels([(1.0, 11.0), (2.0, 14.0), (4.0, 26.0)]),
                                      metric_name="max_von_mises_stress")
    assert report["apparent_order"] == pytest.approx(2.0, rel=1e-3)
    assert report["extrapolated_value"] == pytest.approx(10.0, rel=1e-3)
    # GCI_fine = 1.25 * (3/11) / (2^2 - 1) = 11.36%
    assert report["gci_fine_percent"] == pytest.approx(11.3636, rel=1e-3)
    assert report["converged"] is False
    assert report["verdict"] == "not_converged_refine_further"


def test_converged_fine_series_is_in_asymptotic_range() -> None:
    # Same p=2, phi_ext=10, but finer h=[0.25,0.5,1.0] -> near asymptote
    report = analyze_mesh_convergence(_levels([(0.25, 10.0625), (0.5, 10.25), (1.0, 11.0)]))
    assert report["apparent_order"] == pytest.approx(2.0, rel=1e-3)
    assert report["extrapolated_value"] == pytest.approx(10.0, rel=1e-3)
    assert report["gci_fine_percent"] == pytest.approx(0.7764, rel=1e-2)
    assert report["converged"] is True
    assert report["verdict"] == "converged"
    assert report["asymptotic_range"] is True  # GCI ratio ≈ 1


def test_two_grids_reports_relative_change_only() -> None:
    report = analyze_mesh_convergence(_levels([(1.0, 10.1), (2.0, 10.3)]))
    assert report["verdict"] == "two_grid_relative_change_only"
    assert report["apparent_order"] is None
    assert report["gci_fine_percent"] is None
    assert report["relative_change_finest_pair_percent"] == pytest.approx(
        abs((10.3 - 10.1) / 10.1) * 100.0, rel=1e-3
    )


def test_insufficient_grids() -> None:
    assert analyze_mesh_convergence(_levels([(1.0, 10.0)]))["verdict"] == "insufficient_grids"
    assert analyze_mesh_convergence([])["verdict"] == "insufficient_grids"


def test_flat_series_is_converged_flat() -> None:
    report = analyze_mesh_convergence(_levels([(1.0, 10.0), (2.0, 10.0), (4.0, 10.0)]))
    assert report["verdict"] == "converged_flat"
    assert report["converged"] is True


def test_oscillatory_series_flagged_non_monotonic() -> None:
    # sign change in successive deltas -> oscillatory, not in asymptotic range
    report = analyze_mesh_convergence(_levels([(1.0, 10.1), (2.0, 9.9), (4.0, 10.3)]))
    assert report["non_monotonic"] is True
    assert report["converged"] is False
    assert report["verdict"] == "oscillatory_not_converged"


def test_drops_failed_values_and_keeps_node_count() -> None:
    levels = [
        {"size": 1.0, "value": 11.0, "node_count": 9000},
        {"size": 2.0, "value": None},          # failed solve — dropped
        {"size": 4.0, "value": 26.0, "node_count": 1200},
    ]
    report = analyze_mesh_convergence(levels)
    # only two usable grids remain
    assert report["level_count"] == 2
    assert report["verdict"] == "two_grid_relative_change_only"
    assert report["levels"][0]["node_count"] == 9000


def test_unequal_refinement_ratios_still_estimates_positive_order() -> None:
    # h = [1, 1.5, 3] (r21=1.5, r32=2), phi = 10 + h^2
    report = analyze_mesh_convergence(_levels([(1.0, 11.0), (1.5, 12.25), (3.0, 19.0)]))
    assert report["apparent_order"] is not None
    assert report["apparent_order"] > 0
    assert report["extrapolated_value"] == pytest.approx(10.0, abs=0.5)


def test_honesty_block_always_present() -> None:
    report = analyze_mesh_convergence(_levels([(1.0, 11.0), (2.0, 14.0), (4.0, 26.0)]))
    h = report["honesty"]
    assert h["is_discretization_uncertainty_only"] is True
    assert h["model_validated"] is False
    assert h["production_ready"] is False


# --- Reference: Celik, Ghia, Roache & Freitas, "Procedure for Estimation and ---
# Reporting of Uncertainty Due to Discretization in CFD Applications", ASME J.
# Fluids Eng. editorial policy (published 2008, 130(7):078001), Table 1
# "Sample calculations of discretization error". Every expected number below
# is read from that table; tolerances are half a unit of its last printed digit
# (p to 2 decimals, phi_ext to 4 decimals, percentages to 1 decimal).
#
# The table's grids are 2-D (Eq. 2: h = [(1/N) sum dA]^(1/2)), so passing the
# printed cell counts N with dimension=2 reproduces the printed r21/r32 too.

CELIK_TABLE_1 = {
    # column: (N1, N2, N3, phi1, phi2, phi3, p, phi_ext21, e_a21 %, e_ext21 %, GCI21 %)
    "reattachment_length_monotonic": (18000, 8000, 4500, 6.063, 5.972, 5.863,
                                      1.53, 6.1685, 1.5, 1.7, 2.2),
    "axial_velocity_p_below_1": (18000, 4500, 980, 10.7880, 10.7250, 10.6050,
                                 0.75, 10.8801, 0.6, 0.9, 1.1),
    "axial_velocity_oscillatory": (18000, 4500, 980, 6.0042, 5.9624, 6.0909,
                                   1.51, 6.0269, 0.7, 0.4, 0.5),
}


@pytest.mark.parametrize("column", list(CELIK_TABLE_1))
def test_reproduces_celik_2008_table_1(column: str) -> None:
    n1, n2, n3, f1, f2, f3, p, ext, e_a, e_ext, gci = CELIK_TABLE_1[column]
    levels = [
        {"size": 1.0, "value": f1, "element_count": n1},
        {"size": 2.0, "value": f2, "element_count": n2},
        {"size": 4.0, "value": f3, "element_count": n3},
    ]
    report = analyze_mesh_convergence(levels, dimension=2)
    assert report["h_basis"] == "element_count"
    assert report["apparent_order"] == pytest.approx(p, abs=0.01)
    assert report["extrapolated_value"] == pytest.approx(ext, abs=5e-4)
    assert report["relative_change_finest_pair_percent"] == pytest.approx(e_a, abs=0.05)
    # Table prints 0.9% for the middle column, but Eq. 6 on its own phi_ext and
    # phi1 gives (10.8801-10.7880)/10.8801 = 0.85%; allow the table's rounding.
    assert report["extrapolated_relative_error_percent"] == pytest.approx(e_ext, abs=0.1)
    assert report["gci_fine_percent"] == pytest.approx(gci, abs=0.05)
    if column == "axial_velocity_oscillatory":
        # eps32/eps21 < 0 is oscillatory convergence (Celik, Step 3)
        assert report["non_monotonic"] is True
        assert report["converged"] is False
    else:
        assert report["non_monotonic"] is False
        assert report["converged"] is True


def test_celik_refinement_ratios_from_cell_counts() -> None:
    n1, n2, n3 = CELIK_TABLE_1["reattachment_length_monotonic"][:3]
    report = analyze_mesh_convergence(
        [{"size": 1.0, "value": 6.063, "element_count": n1},
         {"size": 2.0, "value": 5.972, "element_count": n2},
         {"size": 4.0, "value": 5.863, "element_count": n3}],
        dimension=2,
    )
    assert report["pairs"][0]["r"] == pytest.approx(1.5, abs=5e-4)    # Table 1: 1.5
    assert report["pairs"][1]["r"] == pytest.approx(1.333, abs=5e-4)  # Table 1: 1.333


def test_realized_element_counts_override_the_requested_size() -> None:
    # Gmsh was asked for sizes 1, 2, 4 (nominal r = 2) but delivered meshes
    # whose realized r21 = 1.5, r32 = 2.0. A metric phi = 10 + h^2 on the
    # REALIZED h has order 2; from the nominal ratio it reads as something else.
    h = [1.0, 1.5, 3.0]
    levels = [
        {"size": s, "value": 10.0 + hi ** 2, "element_count": 1e6 / hi ** 3}
        for s, hi in zip([1.0, 2.0, 4.0], h)
    ]
    report = analyze_mesh_convergence(levels)
    assert report["h_basis"] == "element_count"
    assert report["apparent_order"] == pytest.approx(2.0, abs=1e-3)
    assert report["extrapolated_value"] == pytest.approx(10.0, abs=1e-3)
    nominal = analyze_mesh_convergence([{"size": l["size"], "value": l["value"]} for l in levels])
    assert nominal["h_basis"] == "requested_size"
    assert abs(nominal["apparent_order"] - 2.0) > 0.3  # what the old path reported
    assert any("REQUESTED mesh size" in q for q in nominal["qualifications"])


def test_verdict_does_not_depend_on_units() -> None:
    # The same p=2 series in three unit scales. An absolute 1e-12 flatness test
    # called the small-unit copy "converged_flat".
    base = [(1.0, 11.0), (2.0, 14.0), (4.0, 26.0)]
    reports = [
        analyze_mesh_convergence(_levels([(s, v * scale) for s, v in base]))
        for scale in (1.0, 1e-14, 1e9)
    ]
    assert [r["verdict"] for r in reports] == ["not_converged_refine_further"] * 3
    assert [r["apparent_order"] for r in reports] == [pytest.approx(2.0, rel=1e-4)] * 3


def test_flat_coarse_pair_under_a_moving_fine_pair_is_not_converged() -> None:
    # medium == coarse, but the finest grid still moved 4%: Celik Step 3 says
    # the procedure does not work here. It used to report `converged_flat`.
    report = analyze_mesh_convergence(_levels([(1.0, 100.0), (2.0, 104.0), (4.0, 104.0)]))
    assert report["verdict"] == "indeterminate"
    assert report["converged"] is None


@pytest.mark.parametrize(
    "counts, values",
    [
        ([1000, 1000, 125], [10.0, 10.0, 13.0]),  # used to be converged_flat, GCI 0%
        ([1000, 1000], [10.0, 10.0]),             # used to be a converged two-grid result
    ],
)
def test_repeated_realized_grid_is_refused(counts, values) -> None:
    # Different requested sizes, one realized mesh: no refinement happened.
    levels = [
        {"size": s, "value": v, "element_count": n}
        for s, v, n in zip([1.0, 2.0, 4.0], values, counts)
    ]
    report = analyze_mesh_convergence(levels)
    assert report["verdict"] == "repeated_grid"
    assert report["converged"] is None
    assert report.get("gci_fine_percent") is None


def test_diverging_series_is_not_converged() -> None:
    # The change GROWS under refinement (eps21 = 1.0 > eps32 = 0.5). The old
    # constant-ratio formula dropped Celik's absolute value: p = -1, GCI = -25%,
    # and "-25% <= 5%" reported the series as converged.
    report = analyze_mesh_convergence(_levels([(1.0, 10.0), (2.0, 11.0), (4.0, 11.5)]))
    assert report["verdict"] == "diverging_not_converged"
    assert report["converged"] is False
    assert report["apparent_order"] is None
    assert report["gci_fine_percent"] is None


def test_small_refinement_ratio_is_warned() -> None:
    # r21 = 1.2 next to r32 = 2: Celik's fixed-point iteration for p diverges
    # here (it raised OverflowError); the bracketed solve recovers p = 2 exactly.
    report = analyze_mesh_convergence(_levels([(1.0, 11.0), (1.2, 11.44), (2.4, 15.76)]))
    assert report["apparent_order"] == pytest.approx(2.0, rel=1e-6)
    assert report["extrapolated_value"] == pytest.approx(10.0, rel=1e-6)
    assert any("1.3" in w for w in report["warnings"])
    clean = analyze_mesh_convergence(_levels([(1.0, 11.0), (2.0, 14.0), (4.0, 26.0)]))
    assert clean["warnings"] == []
