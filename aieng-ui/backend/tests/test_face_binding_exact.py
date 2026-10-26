"""Face -> node binding uses the mesh's own surfaces, and the mesher checks Jacobians.

Three measured defects, each fixed at the layer that caused it:

1. **Interior nodes in a face set.** The tolerance band (2 % of the face's
   longest side) caught nodes up to 1.83 mm inside a 10 mm bar: a symmetry BC on
   its 100 x 20 face restrained 412 interior nodes, and a uniform-tension patch
   test (exact answer sigma = F/A) came back +8 % at the peak. Binding by gmsh's
   per-CAD-surface element sets is exact.
2. **Any cylinder that is not along Z.** The cylinder branch measured radial
   distance in XY from the bbox centre. On a 10 mm hole along X it selected 108
   nodes, 13 of them not on the hole, out of the hole's 774.
3. **Inverted quadratic tets.** Curving mid-side nodes onto small holes and
   fillets folded elements inside out (4 at 6 mm, 26 at 3 mm, on a plate with
   M3 holes and 1 mm fillets). `cae.generate_mesh` reported success and ccx
   aborted with "nonpositive jacobian".
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.simulation_runner import _FaceNodeResolver, _nodes_on_face

pytest.importorskip("gmsh", reason="exact binding is read off a real gmsh mesh")
build123d = pytest.importorskip("build123d", reason="the geometry comes from the CAD kernel")


def _mesh(part, size: float) -> tuple[str, dict, dict]:
    from app.simulation_runner import _mesh_with_gmsh, _parse_inp_nodes

    work = Path(tempfile.mkdtemp(prefix="aieng_binding_"))
    step = work / "part.step"
    build123d.export_step(part, str(step))
    quality: dict = {}
    inp = _mesh_with_gmsh(step, work, size, quality_out=quality)
    return inp.read_text(), _parse_inp_nodes(inp), quality


def test_a_plane_face_set_holds_exactly_the_surface_nodes() -> None:
    bar = build123d.Box(100, 20, 10, align=(build123d.Align.MIN,) * 3)
    text, nodes, _ = _mesh(bar, 5.0)
    bottom = {"id": "f", "type": "face", "surface_type": "plane", "normal": [0, 0, -1],
              "center": [50, 10, 0], "bounding_box": [0, 0, 0, 100, 20, 0]}

    ids, method = _FaceNodeResolver(nodes, text).nodes_for_face(bottom)

    assert method == "mesh_surface"
    assert ids and all(abs(nodes[n][2]) < 1e-9 for n in ids)
    on_plane = {n for n, p in nodes.items() if abs(p[2]) < 1e-9}
    assert set(ids) == on_plane
    # The band this replaces: interior nodes up to 2 mm deep.
    band = _nodes_on_face(nodes, bottom)
    assert any(nodes[n][2] > 1e-6 for n in band)


def test_a_hole_along_x_is_bound_completely_and_only() -> None:
    block = build123d.Box(40, 30, 30) - build123d.Cylinder(5, 40, rotation=(0, 90, 0))
    text, nodes, _ = _mesh(block, 3.0)
    hole = {"id": "h", "type": "face", "surface_type": "cylinder", "radius": 5.0,
            "axis": [1.0, 0.0, 0.0], "axis_point": [0.0, 0.0, 0.0],
            "bounding_box": [-20, -5, -5, 20, 5, 5]}
    on_hole = {n for n, p in nodes.items() if abs((p[1] ** 2 + p[2] ** 2) ** 0.5 - 5) < 1e-6}

    ids, method = _FaceNodeResolver(nodes, text).nodes_for_face(hole)
    assert method == "mesh_surface" and set(ids) == on_hole

    # The tolerance-band fallback now honours the recorded axis too.
    assert set(_nodes_on_face(nodes, hole)) >= on_hole


def test_inverted_quadratic_elements_are_repaired_before_the_solver_sees_them() -> None:
    from build123d import Axis, Box, BuildPart, Hole, Locations, fillet

    with BuildPart() as bp:
        Box(100, 60, 6)
        with Locations((35, 20, 0), (-35, 20, 0), (35, -20, 0), (-35, -20, 0)):
            Hole(radius=1.6)
        fillet(bp.edges().filter_by(Axis.Z), radius=1.0)
    _text, _nodes, quality = _mesh(bp.part, 3.0)

    assert quality["inverted_element_count"] == 0, quality
    assert quality["min_scaled_jacobian"] > 0
    # Evidence the case still exercises the repair (26 inverted when measured).
    assert quality["straightened_element_count"] > 0


# ── review follow-ups (#546) ────────────────────────────────────────────────


def test_a_caller_without_diagnostics_cannot_receive_an_inverted_mesh(monkeypatch) -> None:
    """The sizing and legacy solve paths pass no `quality_out`, so they could not
    refuse on it; the mesher refuses for them instead of handing ccx the mesh."""
    from app import simulation_runner as sr

    monkeypatch.setattr(sr, "_validate_element_jacobians", lambda _gmsh: {
        "inverted_element_count": 1, "straightened_element_count": 1,
        "min_scaled_jacobian": -0.5, "element_count": 10,
    })
    box = build123d.Box(10, 10, 10)
    with pytest.raises(sr.InvertedElementsError):
        _mesh_without_quality(box, 5.0)
    # A caller that asks for the diagnostics keeps the structured check.
    _text, _nodes, quality = _mesh(box, 5.0)
    assert quality["inverted_element_count"] == 1


def _mesh_without_quality(part, size: float) -> None:
    from app.simulation_runner import _mesh_with_gmsh

    work = Path(tempfile.mkdtemp(prefix="aieng_binding_"))
    step = work / "part.step"
    build123d.export_step(part, str(step))
    _mesh_with_gmsh(step, work, size)


def _two_bodies(*parts) -> tuple[str, dict]:
    compound = build123d.Compound(children=list(parts))
    text, nodes, _ = _mesh(compound, 3.0)
    return text, nodes


def test_a_glued_interface_binds_nothing_rather_than_its_perimeter() -> None:
    """Both sides of the interface are internal after the OCC fragment. The
    band fallback used to bind the interface's PERIMETER nodes (they are also on
    the outer boundary), so a load or restraint 'on' it looked bound."""
    lower = build123d.Box(10, 10, 10, align=(build123d.Align.MIN,) * 3)
    upper = build123d.Pos(0, 0, 10) * build123d.Box(10, 10, 10, align=(build123d.Align.MIN,) * 3)
    text, nodes = _two_bodies(lower, upper)
    interface = {"id": "f", "type": "face", "surface_type": "plane", "normal": [0, 0, 1],
                 "center": [5, 5, 10], "bounding_box": [0, 0, 10, 10, 10, 10], "area": 100.0}

    ids, method = _FaceNodeResolver(nodes, text).nodes_for_face(interface)

    assert (ids, method) == ([], "internal_surface")


def test_a_ring_face_does_not_absorb_a_flush_cap_in_its_hole() -> None:
    """A plate's top face around a flush pin's cap: both are coplanar and inside
    the ring's box. Their areas must add up to the face, so the cap is left out."""
    import math

    plate = build123d.Box(40, 40, 5) - build123d.Cylinder(5, 5)
    pin = build123d.Cylinder(5, 5)
    text, nodes = _two_bodies(plate, pin)
    ring_area = 40 * 40 - math.pi * 25
    ring = {"id": "f", "type": "face", "surface_type": "plane", "normal": [0, 0, 1],
            "center": [0, 0, 2.5], "bounding_box": [-20, -20, 2.5, 20, 20, 2.5],
            "area": round(ring_area, 4)}

    ids, method = _FaceNodeResolver(nodes, text).nodes_for_face(ring)

    assert method == "mesh_surface"
    assert ids and all(abs(nodes[n][2] - 2.5) < 1e-9 for n in ids)
    assert all(math.hypot(nodes[n][0], nodes[n][1]) > 5 - 1e-6 for n in ids)
