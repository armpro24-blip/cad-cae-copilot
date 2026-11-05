"""A peak stress at a sharp concave edge or a fully fixed face is flagged as singular.

Measured on the canonical bracket: the peak sat 0.75 mm from the rib-to-plate
junction and rose 11.1 -> 17.6 MPa over four mesh sizes while the displacement
agreed to 0.2 %. A plate's hole concentration converges and must not be flagged.
"""
from __future__ import annotations

import pytest

from app.stress_peak_check import concave_edge_samples, judge_stress_peak

_EDGE = [(-30.0, y / 10.0, 12.0) for y in range(-25, 26)]  # the rib-end junction line


def test_a_peak_beside_a_concave_edge_is_singular() -> None:
    verdict = judge_stress_peak({"x": -30.0, "y": -1.81, "z": 12.75}, concave_samples=_EDGE,
                                element_size_mm=3.0, restraint_id=None)
    assert verdict["singular"] is True
    assert verdict["causes"] == ["concave_edge"]
    assert verdict["distance_to_concave_edge_mm"] == pytest.approx(0.75, abs=0.01)
    assert "Do not quote" in verdict["note"]


def test_a_peak_more_than_one_element_from_the_edge_is_not_blamed_on_it() -> None:
    verdict = judge_stress_peak({"x": 0.0, "y": -6.0, "z": 0.0}, concave_samples=_EDGE,
                                element_size_mm=3.0, restraint_id=None)
    assert verdict["singular"] is False
    assert verdict["causes"] == []
    assert "cae.mesh_convergence" in verdict["note"]


def test_a_peak_on_a_fully_fixed_face_is_singular() -> None:
    verdict = judge_stress_peak({"x": 40.8, "y": 22.3, "z": 12.0}, concave_samples=[],
                                element_size_mm=3.0, restraint_id="bc_004")
    assert verdict["causes"] == ["restrained_face"]
    assert "bc_004" in verdict["note"]


def test_unexamined_edges_are_unknown_not_clean() -> None:
    verdict = judge_stress_peak({"x": 0.0, "y": 0.0, "z": 0.0}, concave_samples=None,
                                element_size_mm=3.0, restraint_id=None)
    assert verdict["singular"] is False
    assert "unknown" in verdict["note"]


build123d = pytest.importorskip("build123d", reason="edge classification needs the CAD kernel")


def _step(tmp_path, shape) -> str:
    path = tmp_path / "model.step"
    build123d.export_step(shape, str(path))
    return str(path)


def test_a_rib_on_a_plate_has_its_junction_as_concave_edges(tmp_path) -> None:
    """Two separate solids: the re-entrant corner exists only once they are fused."""
    from build123d import Align, Box, Compound, Location

    plate = Box(120, 80, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    rib = Box(60, 5, 25, align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Location((0, 0, 8)))
    samples = concave_edge_samples(_step(tmp_path, Compound(children=[plate, rib])), 1.0)
    assert samples
    assert all(abs(z - 8.0) < 1e-6 and abs(x) <= 30.0 + 1e-6 and abs(y) <= 2.5 + 1e-6
               for x, y, z in samples)


def test_a_plate_with_a_hole_has_no_concave_edge(tmp_path) -> None:
    from build123d import Box, BuildPart, Hole

    with BuildPart() as bp:
        Box(200, 50, 5)
        Hole(radius=6)
    assert concave_edge_samples(_step(tmp_path, bp.part), 1.0) == []
