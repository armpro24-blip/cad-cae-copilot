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
