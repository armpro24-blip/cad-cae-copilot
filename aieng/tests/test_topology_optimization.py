"""Tests for topology optimization (contract + pluggable optimizer + 2D SIMP)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from aieng.converters.shape_ir import (  # noqa: E402
    compile_shape_ir,
    compile_shape_ir_to_build123d_source,
    density_voxel_cells,
    extruded_region_geometry,
    sample_periodic_catmull_rom,
)
from aieng.converters.shape_ir_manifold import (  # noqa: E402
    compile_shape_ir_to_manifold_source,
)
from aieng.converters.topology_optimization import (  # noqa: E402
    SHAPE_IR_PATH,
    SMOOTH_MESH_RECONSTRUCTION_PATH,
    TOPOLOGY_OPTIMIZATION_PATH,
    available_optimizers,
    derive_topopt_problem_from_package,
    extract_density_contours,
    run_topology_optimization,
    simp_2d,
    derive_topopt_problem_3d_from_package,
    simp_3d,
    topology_result_to_shape_ir,
    write_shape_ir_from_topology_optimization,
    write_topology_optimization,
)


# ── experimental 3D SIMP ─────────────────────────────────────────────────────

def test_simp_3d_registered_with_capability():
    assert "simp_3d" in available_optimizers()
    res = run_topology_optimization(
        {"grid": {"nx": 8, "ny": 5, "nz": 3}, "volume_fraction": 0.4, "max_iters": 6},
        optimizer="simp_3d")
    cap = res["optimizer"]["capability"]
    assert res["optimizer"]["name"] == "simp_3d"
    assert cap["dimension"] == "3d" and cap["method"] == "SIMP"
    assert cap["physics"] == "linear_elastic" and cap["mesh"] == "structured_voxel_grid"
    assert cap["material"] == "single_material" and cap["backend"] == "builtin_numpy"
    assert cap["engineering_level"] == "experimental_reference" and cap["production_ready"] is False


def test_simp_3d_cantilever_reduces_compliance_and_meets_volume():
    out = simp_3d({"grid": {"nx": 12, "ny": 6, "nz": 4}, "volume_fraction": 0.4, "max_iters": 12})
    h = out["compliance_history"]
    assert len(h) > 1 and h[-1] < h[0] and all(v > 0 for v in h)
    assert abs(out["achieved_volume_fraction"] - 0.4) < 0.05
    assert out["bcs_source"] == "preset" and out["bcs_preset"] == "cantilever_3d"
    assert len(out["density_3d"]) == 4 and len(out["density_3d"][0]) == 6 and len(out["density_3d"][0][0]) == 12
    assert all(0.0 <= v <= 1.0 for pl in out["density_3d"] for row in pl for v in row)


def test_simp_3d_explicit_bcs_respected():
    nx, ny, nz = 10, 5, 4
    supports = [{"cells": [[0, j, k] for j in range(ny) for k in range(nz)]}]   # clamp x=0 layer
    loads = [{"cells": [[nx - 1, ny // 2, nz // 2]], "fx": 0.0, "fy": -1.0, "fz": 0.0}]
    out = simp_3d({"grid": {"nx": nx, "ny": ny, "nz": nz}, "volume_fraction": 0.4,
                   "max_iters": 10, "bcs": {"supports": supports, "loads": loads}})
    assert out["bcs_source"] == "explicit" and out["bcs_preset"] is None
    assert out["compliance_history"][-1] < out["compliance_history"][0]


def test_run_topology_optimization_3d_contract():
    res = run_topology_optimization(
        {"grid": {"nx": 10, "ny": 6, "nz": 3}, "volume_fraction": 0.35, "max_iters": 8,
         "design_space_node": "blk", "source_ir_node": "blk", "load_case_id": "lc1",
         "frame": {"origin": [0, 0, 0], "u_axis": "x", "v_axis": "y", "w_axis": "z",
                   "cell_size": [4.0, 4.0, 4.0]}},
        optimizer="simp_3d")
    assert res["dimension"] == "3d"
    g = res["result"]["density_grid_3d"]
    assert g["nx"] == 10 and g["ny"] == 6 and g["nz"] == 3
    assert res["result"]["solid_voxel_count"] >= 0
    assert res["result"]["objective_history"] and res["result"]["compliance_history"]
    assert res["problem"]["bcs_source"] == "preset"
    assert res["frame"]["cell_size"] == [4.0, 4.0, 4.0]
    assert res["provenance"]["source_ir_node"] == "blk"
    assert res["provenance"]["load_case_id"] == "lc1"
    assert res["limitations"]


_TOPO3D = {
    "format": "aieng.topology_optimization", "contract_version": "0.1", "dimension": "3d",
    "optimizer": {"name": "simp_3d"}, "objective": "compliance_minimization",
    "provenance": {"design_space_node": "blk", "source_ir_node": "blk"},
    "frame": {"origin": [10.0, 20.0, 0.0], "u_axis": "x", "v_axis": "y", "w_axis": "z",
              "cell_size": [2.0, 2.0, 3.0]},
    "result": {"threshold": 0.5, "final_compliance": 5.0, "achieved_volume_fraction": 0.5,
               "density_grid_3d": {"nx": 2, "ny": 2, "nz": 2, "values": [
                   [[1.0, 0.0], [0.0, 1.0]], [[1.0, 1.0], [0.0, 0.0]]]}},
}


def test_density_voxel_cells_3d_places_in_frame():
    node = {"type": "density_voxels", "dimension": 3,
            "density_3d": [[[1.0, 0.0], [0.0, 1.0]]],   # nz=1, ny=2, nx=2
            "threshold": 0.5, "cell_size": [2.0, 3.0, 4.0], "origin": [10.0, 20.0, 30.0]}
    cells, size = density_voxel_cells(node)
    assert size == (2.0, 3.0, 4.0)
    # (k=0,j=0,i=0) -> x=10+1, y=20+1.5, z=30+2 ; (k=0,j=1,i=1) -> x=10+3, y=20+4.5, z=30+2
    assert [11.0, 21.5, 32.0] in cells and [13.0, 24.5, 32.0] in cells and len(cells) == 2


def test_topology_result_to_shape_ir_3d_node():
    payload = topology_result_to_shape_ir(_TOPO3D)
    assert payload["representation"] == "manifold_mesh"   # 3D default runtime
    assert payload["provenance"]["dimension"] == "3d"
    node = payload["parts"][0]
    assert node["type"] == "density_voxels" and node["dimension"] == 3
    assert node["cell_size"] == [2.0, 2.0, 3.0] and node["origin"] == [10.0, 20.0, 0.0]
    assert set(["preview", "voxelized", "lossy", "not_production_cad"]).issubset(node["tags"])
    assert node["density_3d"] == _TOPO3D["result"]["density_grid_3d"]["values"]


def test_3d_surface_marching_cubes_writeback():
    pytest.importorskip("skimage")
    # a solid core in a 5x5x5 field -> marching cubes yields a closed surface
    vals = [[[1.0 if (1 <= i <= 3 and 1 <= j <= 3 and 1 <= k <= 3) else 0.0
              for i in range(5)] for j in range(5)] for k in range(5)]
    topo = {**_TOPO3D, "result": {"threshold": 0.5,
            "density_grid_3d": {"nx": 5, "ny": 5, "nz": 5, "values": vals}}}
    payload = topology_result_to_shape_ir(topo, method="surface")
    assert payload["representation"] == "manifold_mesh"
    assert payload["provenance"]["evidence"] == "smooth_mesh_preview"
    node = payload["parts"][0]
    assert node["type"] == "smooth_mesh_proxy" and node["dimension"] == 3
    assert node["smoothing"] == "marching_cubes"
    assert node["vertex_count"] > 0 and node["triangle_count"] > 0
    assert node["preview_only"] is True and node["cad_editable"] is False
    assert node["representation_kind"] == "mesh" and node["geometry_kind"] == "mesh"
    assert set(["lossy", "not_production_cad", "smooth_mesh_proxy"]).issubset(node["tags"])
    # placed in the frame (origin [10,20,0], cells 2x2x3)
    assert all(v[0] >= 10.0 - 1e-3 and v[2] >= -1e-3 for v in node["vertices"])


def test_3d_surface_falls_back_to_voxels_when_empty():
    pytest.importorskip("skimage")
    empty = {**_TOPO3D, "result": {"threshold": 0.5,
             "density_grid_3d": {"nx": 2, "ny": 2, "nz": 2,
                                 "values": [[[0.0, 0.0], [0.0, 0.0]], [[0.0, 0.0], [0.0, 0.0]]]}}}
    node = topology_result_to_shape_ir(empty, method="surface")["parts"][0]
    assert node["type"] == "density_voxels"   # honest fallback (no isosurface)
    assert "surface_fallback" in node["source_optimization"]


def test_3d_smooth_mesh_method_aliases():
    pytest.importorskip("skimage")
    vals = [[[1.0 if (1 <= i <= 3 and 1 <= j <= 3 and 1 <= k <= 3) else 0.0
              for i in range(5)] for j in range(5)] for k in range(5)]
    topo = {**_TOPO3D, "result": {"threshold": 0.5,
            "density_grid_3d": {"nx": 5, "ny": 5, "nz": 5, "values": vals}}}
    for alias in ("smooth_mesh", "marching_cubes", "surface"):
        node = topology_result_to_shape_ir(topo, method=alias)["parts"][0]
        assert node["type"] == "smooth_mesh_proxy", alias
        assert node["iso_value"] == 0.5 and node["bbox"] is not None


def test_3d_smooth_mesh_writes_reconstruction_diagnostics(tmp_path: Path):
    pytest.importorskip("skimage")
    vals = [[[1.0 if (1 <= i <= 3 and 1 <= j <= 3 and 1 <= k <= 3) else 0.0
              for i in range(5)] for j in range(5)] for k in range(5)]
    topo = {**_TOPO3D, "limitations": ["experimental 3D SIMP"],
            "provenance": {"design_space_node": "plate", "source_ir_node": "plate"},
            "problem": {"load_case_id": "lc1", "volfrac": 0.4},
            "result": {"threshold": 0.5,
                       "density_grid_3d": {"nx": 5, "ny": 5, "nz": 5, "values": vals}}}
    pkg = tmp_path / "sm.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("metadata.json", "{}")
        zf.writestr(TOPOLOGY_OPTIMIZATION_PATH, json.dumps(topo))
    payload = write_shape_ir_from_topology_optimization(pkg, method="smooth_mesh")
    assert payload["parts"][0]["type"] == "smooth_mesh_proxy"
    with zipfile.ZipFile(pkg) as zf:
        assert SMOOTH_MESH_RECONSTRUCTION_PATH in zf.namelist()
        d = json.loads(zf.read(SMOOTH_MESH_RECONSTRUCTION_PATH))
    assert d["method"] == "marching_cubes" and d["succeeded"] is True
    assert d["iso_value"] == 0.5 and d["input_grid_shape"] == {"nx": 5, "ny": 5, "nz": 5}
    assert d["vertex_count"] > 0 and d["face_count"] > 0 and d["bbox"] is not None
    assert d["frame_placement_applied"] is True
    assert d["geometry_kind"] == "mesh" and d["cad_editable"] is False and d["not_production_cad"] is True
    assert d["load_case_id"] == "lc1" and d["source_ir_node"] == "plate"
    assert d["limitations"] == ["experimental 3D SIMP"]


def test_3d_smooth_mesh_diagnostics_records_voxel_fallback(tmp_path: Path):
    # empty field -> no isosurface -> fallback to voxels, recorded honestly in diagnostics
    topo = {**_TOPO3D, "result": {"threshold": 0.5,
            "density_grid_3d": {"nx": 2, "ny": 2, "nz": 2,
                                "values": [[[0.0, 0.0], [0.0, 0.0]], [[0.0, 0.0], [0.0, 0.0]]]}}}
    pkg = tmp_path / "fb.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("metadata.json", "{}")
        zf.writestr(TOPOLOGY_OPTIMIZATION_PATH, json.dumps(topo))
    payload = write_shape_ir_from_topology_optimization(pkg, method="marching_cubes")
    assert payload["parts"][0]["type"] == "density_voxels"   # honest fallback
    with zipfile.ZipFile(pkg) as zf:
        d = json.loads(zf.read(SMOOTH_MESH_RECONSTRUCTION_PATH))
    assert d["succeeded"] is False and d["fallback"] == "density_voxels" and d["fallback_reason"]


def test_3d_surface_compiles_and_executes_in_manifold():
    pytest.importorskip("skimage")
    pytest.importorskip("manifold3d")
    vals = [[[1.0 if (1 <= i <= 3 and 1 <= j <= 3 and 1 <= k <= 3) else 0.0
              for i in range(5)] for j in range(5)] for k in range(5)]
    topo = {**_TOPO3D, "result": {"threshold": 0.5,
            "design_space_node": "blk", "source_ir_node": "blk",
            "density_grid_3d": {"nx": 5, "ny": 5, "nz": 5, "values": vals}}}
    payload = topology_result_to_shape_ir(topo, method="marching_cubes")
    node = payload["parts"][0]
    # frame placement maps the bbox into design-space coords (frame origin [10,20,0])
    assert node["bbox"][0] >= 10.0 - 1e-3 and node["bbox"][2] >= -1e-3
    src = compile_shape_ir_to_manifold_source(payload)
    assert "Mesh" in src and "Manifold(" in src
    ns: dict = {}
    exec(compile(src, "<m>", "exec"), ns)
    m = ns["result"]
    assert m.volume() > 0                       # non-empty watertight smooth mesh (winding flipped)


def test_3d_voxels_compile_and_execute_in_manifold():
    pytest.importorskip("manifold3d")
    payload = topology_result_to_shape_ir(_TOPO3D)
    src = compile_shape_ir_to_manifold_source(payload)
    assert "Manifold.cube" in src
    ns: dict = {}
    exec(compile(src, "<m>", "exec"), ns)
    m = ns["result"]
    import numpy as np
    verts = np.asarray(m.to_mesh().vert_properties)[:, :3]
    assert m.volume() > 0                                  # non-empty watertight voxel body
    # placed in the frame: origin [10,20,0], cells 2x2x3 -> within [10,14]x[20,24]x[0,6]
    assert verts[:, 0].min() >= 10.0 - 1e-6 and verts[:, 2].min() >= -1e-6


def test_simp_2d_reduces_compliance_and_meets_volume():
    out = simp_2d({"grid": {"nelx": 24, "nely": 8}, "volfrac": 0.5,
                   "max_iters": 20, "bcs": {"preset": "cantilever"}})
    hist = out["compliance_history"]
    assert len(hist) > 1
    assert hist[-1] < hist[0]                       # optimization lowered compliance
    assert all(h > 0 for h in hist)
    assert abs(out["achieved_volume_fraction"] - 0.5) < 0.05   # volume budget respected
    dens = out["density"]
    assert len(dens) == 8 and len(dens[0]) == 24    # nely x nelx
    assert all(0.0 <= v <= 1.0 for row in dens for v in row)
    assert out["iterations"] > 0


def test_run_topology_optimization_contract_and_provenance():
    res = run_topology_optimization(
        {"grid": {"nelx": 20, "nely": 8}, "volfrac": 0.4, "max_iters": 12,
         "design_space_node": "bracket_body"},
        optimizer="simp_2d",
    )
    assert res["format"] == "aieng.topology_optimization"
    assert res["optimizer"]["name"] == "simp_2d" and res["optimizer"]["dimension"] == 2
    assert res["objective"] == "compliance_minimization"
    assert res["problem"]["design_space_node"] == "bracket_body"
    assert res["provenance"]["design_space_node"] == "bracket_body"
    assert res["result"]["density_grid"]["nrows"] == 8 and res["result"]["density_grid"]["ncols"] == 20
    assert res["result"]["solid_element_count"] >= 0
    assert res["limitations"]  # honest scope recorded


def test_precomputed_optimizer_is_neutral():
    """A non-SIMP optimizer flows through the same contract — proves neutrality."""
    grid = [[0.0, 1.0, 1.0], [1.0, 1.0, 0.0]]
    res = run_topology_optimization(
        {"density": grid, "volfrac": 0.66, "compliance_history": [9.9],
         "final_compliance": 9.9, "design_space_node": "n1"},
        optimizer="precomputed",
    )
    assert res["optimizer"]["name"] == "precomputed" and res["optimizer"]["fallback"] is False
    assert res["result"]["density_grid"]["values"] == grid
    assert res["result"]["final_compliance"] == 9.9


def test_optimizer_registry_and_fallback():
    assert {"simp_2d", "precomputed"} <= set(available_optimizers())
    # unknown optimizer -> falls back to simp_2d and records it
    res = run_topology_optimization({"grid": {"nelx": 12, "nely": 6}, "volfrac": 0.5, "max_iters": 6},
                                    optimizer="does_not_exist")
    assert res["optimizer"]["name"] == "simp_2d" and res["optimizer"]["fallback"] is True
    assert res["optimizer"]["requested"] == "does_not_exist"


# ── writeback: optimization result -> Shape IR representation ────────────────

_TOPO = {
    "format": "aieng.topology_optimization",
    "contract_version": "0.1",
    "optimizer": {"name": "simp_2d"},
    "objective": "compliance_minimization",
    "problem": {"design_space_node": "bracket_body"},
    "provenance": {"design_space_node": "bracket_body"},
    "result": {
        "threshold": 0.5,
        "final_compliance": 12.3,
        "achieved_volume_fraction": 0.5,
        "density_grid": {"nrows": 2, "ncols": 3,
                         "values": [[0.9, 0.1, 0.8], [0.2, 0.95, 0.7]]},
    },
}


def test_density_voxel_cells_thresholds_and_places():
    node = {"type": "density_voxels", "density": [[0.9, 0.1], [0.2, 0.8]],
            "threshold": 0.5, "cell_size": [2.0, 2.0], "thickness": 3.0, "origin": [0, 0, 0]}
    cells, (sx, sy, sz) = density_voxel_cells(node)
    assert (sx, sy, sz) == (2.0, 2.0, 3.0)
    # 2 cells above threshold: (row0,col0) and (row1,col1); centres in XY, z=sz/2
    assert [1.0, 1.0, 1.5] in cells and [3.0, 3.0, 1.5] in cells
    assert len(cells) == 2


_TOPO_FRAMED = {
    **_TOPO,
    "problem": {
        "design_space_node": "plate",
        "derivation": {"frame": {
            "origin": [5.0, 10.0, 0.0], "u_axis": "x", "v_axis": "y",
            "cell_size": [40.0, 40.0], "thickness": 10.0}},
    },
}


def test_density_voxel_cells_respects_frame_axes():
    # XZ plane: u=x, v=z, extruded along y; origin offset baked in.
    node = {"type": "density_voxels", "density": [[1.0, 0.0]],
            "threshold": 0.5, "cell_size": [2.0, 3.0], "thickness": 4.0,
            "origin": [10.0, 20.0, 30.0], "u_axis": "x", "v_axis": "z"}
    cells, size = density_voxel_cells(node)
    assert size == (2.0, 4.0, 3.0)            # world x=su, y=thickness, z=sv
    assert cells == [[11.0, 22.0, 31.5]]      # x=10+1, z=30+1.5, y=20+2


def test_topology_result_to_shape_ir_uses_frame():
    payload = topology_result_to_shape_ir(_TOPO_FRAMED)
    node = payload["parts"][0]
    assert node["placed_in_frame"] is True
    assert node["origin"] == [5.0, 10.0, 0.0]
    assert node["cell_size"] == [40.0, 40.0] and node["thickness"] == 10.0
    assert node["u_axis"] == "x" and node["v_axis"] == "y"


def test_topology_result_to_shape_ir_explicit_args_override_frame():
    payload = topology_result_to_shape_ir(_TOPO_FRAMED, cell_size=(2.0, 2.0), origin=(0.0, 0.0, 0.0))
    node = payload["parts"][0]
    assert node["cell_size"] == [2.0, 2.0] and node["origin"] == [0.0, 0.0, 0.0]


def test_topology_result_to_shape_ir_no_frame_defaults_to_unit_grid():
    node = topology_result_to_shape_ir(_TOPO)["parts"][0]   # _TOPO has no derivation frame
    assert node["placed_in_frame"] is False
    assert node["cell_size"] == [1.0, 1.0] and node["origin"] == [0.0, 0.0, 0.0]


def test_topology_result_to_shape_ir_payload():
    payload = topology_result_to_shape_ir(_TOPO, representation="manifold_mesh")
    assert payload["representation"] == "manifold_mesh"
    assert payload["provenance"]["from_topology_optimization"] is True
    assert payload["provenance"]["design_space_node"] == "bracket_body"
    (node,) = payload["parts"]
    assert node["type"] == "density_voxels"
    assert node["id"] == "optimized_bracket_body"
    assert node["density"] == _TOPO["result"]["density_grid"]["values"]
    assert node["threshold"] == 0.5
    assert node["source_optimization"]["design_space_node"] == "bracket_body"


def test_density_voxels_compiles_in_both_backends():
    payload = topology_result_to_shape_ir(_TOPO, representation="manifold_mesh")
    # manifold: a CSG union loop over baked cell centres -> watertight mesh
    msrc = compile_shape_ir_to_manifold_source(payload)
    assert "Manifold.cube" in msrc and "for _c in" in msrc
    assert "result =" in msrc
    # build123d: a Compound of extruded boxes, one labelled named part
    bsrc = compile_shape_ir_to_build123d_source(payload)
    assert "Box(" in bsrc and "Compound(children=" in bsrc
    assert ".label = " in bsrc and "optimized_bracket_body" in bsrc
    # dispatcher routes manifold_mesh to the manifold runtime
    dispatched = compile_shape_ir(payload)
    assert dispatched["representation"] == "manifold_mesh"
    assert "Manifold.cube" in dispatched["source"]


# ── contour-ized writeback (smooth boundary instead of voxels) ───────────────

def test_extract_density_contours_returns_world_polygons():
    pytest.importorskip("skimage")
    # a solid 3x3 block in the middle of a 5x5 field -> one closed loop
    grid = [[0, 0, 0, 0, 0], [0, 1, 1, 1, 0], [0, 1, 1, 1, 0], [0, 1, 1, 1, 0], [0, 0, 0, 0, 0]]
    polys = extract_density_contours(grid, 0.5, origin_u=0.0, origin_v=0.0, su=2.0, sv=2.0)
    assert len(polys) == 1
    loop = polys[0]
    assert len(loop) >= 4 and loop[0] == loop[-1]          # closed
    xs = [p[0] for p in loop]; ys = [p[1] for p in loop]
    # the loop hugs the solid block (cols/rows 1..3 -> world ~[1,7] at cell centres)
    assert min(xs) >= 0.0 and max(xs) <= 10.0 and min(ys) >= 0.0 and max(ys) <= 10.0


def test_extruded_region_geometry_bakes_plane_and_holes():
    # XY plane: sign_v=+1, no flip; an outer square with an inner square hole
    node = {"type": "extruded_region", "u_axis": "x", "v_axis": "y", "thickness": 5.0,
            "origin": [0, 0, 2],
            "polygons": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                         [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]]]}
    g = extruded_region_geometry(node)
    assert g["thickness"] == 5.0 and g["base"] == [0.0, 0.0, 2.0]   # base offset on w=z
    assert g["w_vec"] == [0.0, 0.0, 1.0]
    roles = sorted(is_hole for _pts, is_hole in g["classified"])
    assert roles == [False, True]                                   # one solid, one hole


def test_extruded_region_geometry_flips_v_on_xz_plane():
    # XZ plane: y_dir = w×u = (0,1,0)×(1,0,0) = (0,0,-1) = -v -> sign_v=-1
    node = {"type": "extruded_region", "u_axis": "x", "v_axis": "z", "thickness": 4.0,
            "origin": [0, 6, 0], "polygons": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    g = extruded_region_geometry(node)
    assert g["base"] == [0.0, 6.0, 0.0] and g["w_vec"] == [0.0, 1.0, 0.0]
    # local v-coordinates are negated so the body lands on +z after the affine
    assert all(pt[1] <= 0 for pt in g["local_polys"][0])


def test_contour_writeback_emits_extruded_region_and_compiles():
    pytest.importorskip("skimage")
    framed = {**_TOPO_FRAMED, "result": {"threshold": 0.5, "density_grid": {
        "nrows": 5, "ncols": 5,
        "values": [[0, 0, 0, 0, 0], [0, 1, 1, 1, 0], [0, 1, 1, 1, 0], [0, 1, 1, 1, 0], [0, 0, 0, 0, 0]]}}}
    payload = topology_result_to_shape_ir(framed, method="contour")
    node = payload["parts"][0]
    assert node["type"] == "extruded_region" and node["polygons"]
    assert node["placed_in_frame"] is True
    msrc = compile_shape_ir_to_manifold_source(payload)
    assert "CrossSection" in msrc and ".transform(" in msrc
    bsrc = compile_shape_ir_to_build123d_source(payload)
    assert "BuildSketch(Plane(" in bsrc and "extrude(amount=" in bsrc


def test_contour_writeback_falls_back_to_voxels_when_empty():
    pytest.importorskip("skimage")
    empty = {**_TOPO_FRAMED, "result": {"threshold": 0.5,
             "density_grid": {"nrows": 2, "ncols": 2, "values": [[0.0, 0.0], [0.0, 0.0]]}}}
    node = topology_result_to_shape_ir(empty, method="contour")["parts"][0]
    assert node["type"] == "density_voxels"                          # honest fallback
    assert "contour_fallback" in node["source_optimization"]


def test_contour_executes_in_manifold_within_design_space(tmp_path: Path):
    pytest.importorskip("skimage")
    pytest.importorskip("manifold3d")
    framed = {**_TOPO_FRAMED, "result": {"threshold": 0.5, "density_grid": {
        "nrows": 5, "ncols": 5,
        "values": [[0, 0, 0, 0, 0], [0, 1, 1, 1, 0], [0, 1, 1, 1, 0], [0, 1, 1, 1, 0], [0, 0, 0, 0, 0]]}}}
    # frame: origin [5,10,0], cell 40x40 -> design plane spans [5,205]x[10,210]
    payload = topology_result_to_shape_ir(framed, method="contour")
    src = compile_shape_ir_to_manifold_source(payload)
    ns: dict = {}
    exec(compile(src, "<m>", "exec"), ns)
    m = ns["result"]
    import numpy as np
    verts = np.asarray(m.to_mesh().vert_properties)[:, :3]
    assert m.volume() > 0
    assert verts[:, 2].min() >= -1e-6 and verts[:, 2].max() <= 10.0 + 1e-6   # extruded along z by thickness
    assert verts[:, 0].min() >= 5.0 - 1e-6 and verts[:, 1].min() >= 10.0 - 1e-6  # placed at frame origin


# ── spline / smooth-curve boundary (vs raw polyline) ─────────────────────────

_RING = {**_TOPO_FRAMED, "result": {"threshold": 0.5, "density_grid": {
    "nrows": 7, "ncols": 7, "values": [
        [0, 0, 0, 0, 0, 0, 0],
        [0, 1, 1, 1, 1, 1, 0],
        [0, 1, 0, 0, 0, 1, 0],
        [0, 1, 0, 0, 0, 1, 0],
        [0, 1, 0, 0, 0, 1, 0],
        [0, 1, 1, 1, 1, 1, 0],
        [0, 0, 0, 0, 0, 0, 0]]}}}


def test_sample_periodic_catmull_rom_densifies_and_stays_closed():
    pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 8.0], [0.0, 8.0]]
    dense = sample_periodic_catmull_rom(pts, subdiv=8)
    assert len(dense) == 4 * 8                       # subdiv per segment, periodic
    assert dense[0] == [0.0, 0.0]                    # passes through the through-points
    # stays within a sane neighbourhood of the loop (mild interpolant overshoot)
    assert all(-3 <= p[0] <= 13 and -3 <= p[1] <= 11 for p in dense)


def test_spline_boundary_emits_true_spline_in_brep_and_densifies_in_mesh():
    pytest.importorskip("skimage")
    payload = topology_result_to_shape_ir(_RING, method="contour", boundary="spline")
    node = payload["parts"][0]
    assert node["type"] == "extruded_region" and node["boundary"] == "spline"
    bsrc = compile_shape_ir_to_build123d_source(payload)
    assert "Spline(" in bsrc and "periodic=True" in bsrc and "make_face" in bsrc
    assert "Polygon(" not in bsrc                     # spline path, not polyline
    msrc = compile_shape_ir_to_manifold_source(payload)
    assert "spline->densified" in msrc and "CrossSection" in msrc


def test_polygon_boundary_fallback_preserved():
    pytest.importorskip("skimage")
    payload = topology_result_to_shape_ir(_RING, method="contour", boundary="polygon")
    node = payload["parts"][0]
    assert node["boundary"] == "polygon"
    bsrc = compile_shape_ir_to_build123d_source(payload)
    assert "Polygon(" in bsrc and "Spline(" not in bsrc   # raw polyline preserved


def test_spline_boundary_extracts_hole_and_subtracts():
    pytest.importorskip("skimage")
    # a ring (solid border, hollow centre) -> outer + inner loop; inner is a hole
    node = topology_result_to_shape_ir(_RING, method="contour", boundary="spline",
                                       simplify_tol=0.4)["parts"][0]
    assert len(node["polygons"]) == 2
    bsrc = compile_shape_ir_to_build123d_source(node and {"parts": [node], "representation": "brep_build123d"})
    assert "make_face(mode=Mode.ADD)" in bsrc and "make_face(mode=Mode.SUBTRACT)" in bsrc


def test_spline_falls_back_to_polygon_on_design_space_overshoot():
    pytest.importorskip("skimage")
    # design space exactly the solid block's extent -> a spline through boundary
    # cell-centres bulges outside it -> must fall back to polygon for safety.
    framed = {
        "optimizer": {"name": "simp_2d"}, "objective": "compliance_minimization",
        "provenance": {"design_space_node": "blk"},
        "problem": {"design_space_node": "blk", "derivation": {
            "design_space_bbox": [0, 0, 0, 50, 50, 10],
            "frame": {"origin": [0, 0, 0], "u_axis": "x", "v_axis": "y",
                      "cell_size": [10.0, 10.0], "thickness": 10.0}}},
        "result": {"threshold": 0.5, "density_grid": {"nrows": 5, "ncols": 5, "values": [
            [1, 1, 1, 1, 1], [1, 0, 0, 0, 1], [1, 0, 0, 0, 1], [1, 0, 0, 0, 1], [1, 1, 1, 1, 1]]}},
    }
    node = topology_result_to_shape_ir(framed, method="contour", boundary="spline")["parts"][0]
    assert node["type"] == "extruded_region"
    assert node["boundary"] == "polygon"                       # fell back
    assert "spline_fallback" in node["source_optimization"]


def test_spline_kept_when_within_envelope():
    pytest.importorskip("skimage")
    # generous design space (margin around the part) -> spline stays inside -> kept.
    framed = {
        "optimizer": {"name": "simp_2d"}, "objective": "compliance_minimization",
        "provenance": {"design_space_node": "blk"},
        "problem": {"design_space_node": "blk", "derivation": {
            "design_space_bbox": [-40, -40, 0, 90, 90, 10],
            "frame": {"origin": [0, 0, 0], "u_axis": "x", "v_axis": "y",
                      "cell_size": [10.0, 10.0], "thickness": 10.0}}},
        "result": {"threshold": 0.5, "density_grid": {"nrows": 5, "ncols": 5, "values": [
            [0, 0, 0, 0, 0], [0, 1, 1, 1, 0], [0, 1, 1, 1, 0], [0, 1, 1, 1, 0], [0, 0, 0, 0, 0]]}},
    }
    node = topology_result_to_shape_ir(framed, method="contour", boundary="spline")["parts"][0]
    assert node["boundary"] == "spline"                        # within envelope -> kept
    assert "spline_fallback" not in node["source_optimization"]


def test_spline_boundary_executes_in_manifold(tmp_path: Path):
    pytest.importorskip("skimage")
    pytest.importorskip("manifold3d")
    payload = topology_result_to_shape_ir(_RING, method="contour", boundary="spline")
    src = compile_shape_ir_to_manifold_source(payload)
    ns: dict = {}
    exec(compile(src, "<m>", "exec"), ns)
    m = ns["result"]
    import numpy as np
    verts = np.asarray(m.to_mesh().vert_properties)[:, :3]
    assert m.volume() > 0
    # placed in the _TOPO_FRAMED frame (origin [5,10,0], thickness 10 along z)
    assert verts[:, 2].min() >= -1e-6 and verts[:, 2].max() <= 10.0 + 1e-6
    assert verts[:, 0].min() >= 5.0 - 1.0 and verts[:, 1].min() >= 10.0 - 1.0


def test_write_shape_ir_from_topology_optimization_into_package(tmp_path: Path):
    pkg = tmp_path / "opt.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"name": "opt"}))
        zf.writestr(TOPOLOGY_OPTIMIZATION_PATH, json.dumps(_TOPO))
    payload = write_shape_ir_from_topology_optimization(pkg, representation="manifold_mesh")
    assert payload["parts"][0]["type"] == "density_voxels"
    with zipfile.ZipFile(pkg) as zf:
        assert SHAPE_IR_PATH in zf.namelist()
        loaded = json.loads(zf.read(SHAPE_IR_PATH))
    assert loaded["representation"] == "manifold_mesh"
    assert loaded["provenance"]["design_space_node"] == "bracket_body"


def test_write_shape_ir_requires_prior_optimization(tmp_path: Path):
    pkg = tmp_path / "bare.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("metadata.json", "{}")
    with pytest.raises(FileNotFoundError):
        write_shape_ir_from_topology_optimization(pkg)


# ── derive a problem from CAE setup + geometry ───────────────────────────────

def test_simp_2d_explicit_bcs_solve_and_reduce_compliance():
    """A left-edge support + right-tip downward load (cell-based) behaves like a
    cantilever, lowering compliance — proves explicit BCs feed the solver."""
    nelx, nely = 24, 8
    supports = [{"cells": [[0, j] for j in range(nely)]}]          # clamp left column
    loads = [{"cells": [[nelx - 1, nely // 2]], "fx": 0.0, "fy": -1.0}]  # tip load down
    out = simp_2d({"grid": {"nelx": nelx, "nely": nely}, "volfrac": 0.5, "max_iters": 20,
                   "bcs": {"supports": supports, "loads": loads}})
    assert out["bcs_source"] == "explicit" and out["bcs_preset"] is None
    hist = out["compliance_history"]
    assert hist[-1] < hist[0] and all(h > 0 for h in hist)


def test_simp_2d_degenerate_explicit_falls_back_to_preset():
    # support but no load -> degenerate -> preset fallback (still solves)
    out = simp_2d({"grid": {"nelx": 12, "nely": 6}, "volfrac": 0.5, "max_iters": 6,
                   "bcs": {"supports": [{"cells": [[0, 0]]}], "loads": []}})
    assert out["bcs_source"] == "preset" and out["bcs_preset"] == "cantilever"


def _write_cae_project(pkg: Path, direction: str = "[0.0, 0.0, -1.0]") -> None:
    """A 120x80x10 plate: left face fixed, right face loaded (default: downward -Z)."""
    topo = {"entities": [
        {"id": "body_plate", "type": "solid", "source_ir_node": "plate",
         "bounding_box": [0, 0, 0, 120, 80, 10]},
        {"id": "face_left", "type": "face", "body_id": "body_plate",
         "bounding_box": [0, 0, 0, 0, 80, 10], "normal": [-1, 0, 0]},
        {"id": "face_right", "type": "face", "body_id": "body_plate",
         "bounding_box": [120, 0, 0, 120, 80, 10], "normal": [1, 0, 0]},
    ]}
    cae_map = {"mappings": [
        {"maps_to": {"feature_id": "feat_fix"}, "cae_entity": "N_FIX", "face_ids": ["face_left"]},
        {"maps_to": {"feature_id": "feat_load"}, "cae_entity": "N_LOAD", "face_ids": ["face_right"]},
    ]}
    setup = (
        "boundary_conditions:\n"
        "  - id: bc1\n    target_feature: feat_fix\n    type: fixed\n"
        "loads:\n"
        "  - id: ld1\n    target_feature: feat_load\n    type: force\n"
        f"    value_n: 500.0\n    direction: {direction}\n"
    )
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(topo))
        zf.writestr("simulation/cae_mapping.json", json.dumps(cae_map))
        zf.writestr("simulation/setup.yaml", setup)


def test_plate_bending_is_refused_not_replaced_by_a_textbook_cantilever(tmp_path: Path):
    """The archetypal case must not silently become someone else's problem.

    The projection plane is spanned by the two LARGEST dimensions, so for a plate
    or bracket the load that matters — bending, normal to the face — is always
    along the thinnest axis and always dropped. This used to return `status: ok`
    carrying the `cantilever` preset, so a real part was optimized against a load
    case that was not its own, and `opt.writeback_to_shape_ir` then wrote that
    result back as the part's geometry.
    """
    pytest.importorskip("yaml")
    pkg = tmp_path / "plate.aieng"
    _write_cae_project(pkg)  # 500 N along -Z on a 120x80x10 plate
    prob = derive_topopt_problem_from_package(pkg, resolution=40)

    assert prob["status"] == "needs_user_input"
    assert prob.get("bcs", {}).get("preset") is None, "a preset must never stand in"
    assert "thinnest axis" in prob["reason"] and "bending" in prob["reason"]
    assert "dimension='3d'" in prob["recommendation"]
    assert prob["out_of_plane_loads"] == ["feat_load"]
    # the derivation it DID manage is still reported, so the diagnosis is checkable
    assert prob["plane"] == {"u_axis": "x", "v_axis": "y", "out_of_plane_axis": "z"}
    assert prob["support_count"] == 1 and prob["load_count"] == 0
    assert prob["frame"]["thickness"] == 10.0
    assert prob["grid"]["nelx"] == 40 and prob["grid"]["nely"] == round(40 * 80 / 120)


def test_an_unresolvable_load_does_not_borrow_the_bending_reason(tmp_path: Path):
    """A load that lands on no cells is a binding problem, not plate bending.

    Both end in "no usable load", so the refusal must not blame the physics when
    the real fault is that the target never resolved.
    """
    pytest.importorskip("yaml")
    pkg = tmp_path / "plate_unbound.aieng"
    _write_cae_project(pkg)
    # point the load at a feature no mapping knows about
    setup = (
        "boundary_conditions:\n  - {id: bc1, target_feature: feat_fix, type: fixed}\n"
        "loads:\n  - {id: ld1, target_feature: feat_ghost, type: force, "
        "value_n: 500.0, direction: [0.0, 0.0, -1.0]}\n"
    )
    tmp = pkg.with_suffix(".tmp.aieng")
    with zipfile.ZipFile(pkg) as src, zipfile.ZipFile(tmp, "w") as dst:
        for item in src.infolist():
            if item.filename != "simulation/setup.yaml":
                dst.writestr(item, src.read(item.filename))
        dst.writestr("simulation/setup.yaml", setup)
    tmp.replace(pkg)

    prob = derive_topopt_problem_from_package(pkg, resolution=40)
    assert prob["status"] == "needs_user_input"
    assert prob["out_of_plane_loads"] == [], "an unbound load is not an out-of-plane load"
    assert "bending" not in prob["reason"]
    # a setup that exists but resolved to nothing must not read as "no CAE setup"
    assert "no CAE setup" not in prob["reason"]
    assert "0 load(s)" in prob["reason"]


def test_in_plane_load_still_derives_a_full_problem(tmp_path: Path):
    """The refusal is about the idealization, not about being strict."""
    pytest.importorskip("yaml")
    pkg = tmp_path / "plate_in_plane.aieng"
    _write_cae_project(pkg, direction="[0.0, -1.0, 0.0]")
    prob = derive_topopt_problem_from_package(pkg, resolution=40)

    assert prob.get("status") != "needs_user_input"
    d = prob["derivation"]
    assert d["derived"] is True
    assert d["plane"] == {"u_axis": "x", "v_axis": "y", "out_of_plane_axis": "z"}
    assert prob["design_space_node"] == "plate"
    sup_cells = prob["bcs"]["supports"][0]["cells"]
    assert sup_cells and all(c[0] == 0 for c in sup_cells)
    assert prob["bcs"]["loads"][0]["fy"] == -500.0
    assert d["frame"]["thickness"] == 10.0 and d["frame"]["cell_size"][0] == round(120 / 40, 6)


def test_derive_in_plane_load_produces_usable_problem_and_solves(tmp_path: Path):
    pytest.importorskip("yaml")
    pkg = tmp_path / "plate2.aieng"
    _write_cae_project(pkg)
    # rewrite the load to act in-plane (-Y) so derivation yields a real load
    setup = (
        "boundary_conditions:\n  - {id: bc1, target_feature: feat_fix, type: fixed}\n"
        "loads:\n  - {id: ld1, target_feature: feat_load, type: force, value_n: 500.0, direction: [0.0, -1.0, 0.0]}\n"
    )
    import zipfile as _z
    tmp = pkg.with_suffix(".tmp.aieng")
    with _z.ZipFile(pkg) as src, _z.ZipFile(tmp, "w") as dst:
        for it in src.infolist():
            if it.filename != "simulation/setup.yaml":
                dst.writestr(it, src.read(it.filename))
        dst.writestr("simulation/setup.yaml", setup)
    tmp.replace(pkg)

    prob = derive_topopt_problem_from_package(pkg, resolution=24, max_iters=15)
    assert prob["derivation"]["derived"] is True
    assert prob["bcs"]["loads"][0]["fy"] == -500.0
    res = run_topology_optimization(prob)
    assert res["problem"]["bcs_source"] == "explicit"
    assert res["problem"]["derivation"]["plane"]["u_axis"] == "x"
    assert res["result"]["compliance_history"][-1] < res["result"]["compliance_history"][0]


def test_derive_3d_maps_supports_loads_full_vector(tmp_path: Path):
    pytest.importorskip("yaml")
    pkg = tmp_path / "plate3d.aieng"
    _write_cae_project(pkg)   # 120x80x10, left face fixed, right face load -Y
    prob = derive_topopt_problem_from_package(pkg, dimension="3d", resolution_3d=12)
    assert prob["status"] == "ok" and prob["dimension"] == "3d"
    g = prob["grid"]
    assert g["nx"] >= 2 and g["ny"] >= 2 and g["nz"] >= 2
    assert prob["frame"]["w_axis"] == "z" and len(prob["frame"]["cell_size"]) == 3
    sup = prob["bcs"]["supports"][0]["cells"]
    assert all(c[0] == 0 for c in sup)                 # left boundary layer (x=0)
    ld = prob["bcs"]["loads"][0]
    # the -Z load is KEPT in 3D (full vector), unlike 2D which would drop the out-of-plane part
    assert ld["fz"] == -500.0 and ld["fx"] == 0.0 and ld["fy"] == 0.0
    assert all(c[0] == g["nx"] - 1 for c in ld["cells"])                 # right boundary layer
    # and it actually solves
    res = run_topology_optimization(prob, optimizer="simp_3d")
    assert res["dimension"] == "3d"
    assert res["result"]["compliance_history"][-1] < res["result"]["compliance_history"][0]


def test_derive_3d_needs_user_input_without_bcs(tmp_path: Path):
    pkg = tmp_path / "bare3d.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(
            {"entities": [{"id": "b", "type": "solid", "bounding_box": [0, 0, 0, 30, 20, 10]}]}))
    out = derive_topopt_problem_3d_from_package(pkg, resolution=10)
    assert out["status"] == "needs_user_input"
    assert out["dimension"] == "3d" and out["diagnostics"]
    assert out["grid"]["nx"] >= 2                       # still reports the grid it would use


def test_a_package_without_a_cae_setup_says_so(tmp_path: Path):
    """No physics to derive from is a question for the user, not a preset."""
    pkg = tmp_path / "bare.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(
            {"entities": [{"id": "b", "type": "solid", "bounding_box": [0, 0, 0, 30, 10, 5]}]}))
    prob = derive_topopt_problem_from_package(pkg)
    assert prob["status"] == "needs_user_input"
    assert prob.get("bcs", {}).get("preset") is None
    assert "no CAE setup" in prob["reason"]
    assert "cae.setup_static" in prob["recommendation"]
    assert prob["support_count"] == 0 and prob["load_count"] == 0


def test_derive_run_writeback_places_body_in_design_space(tmp_path: Path):
    """Full chain: derive (frame) -> run -> writeback. The density_voxels node lands
    in the design-space frame, so nelx*su == design width and the body spans the bbox."""
    pytest.importorskip("yaml")
    pkg = tmp_path / "plate.aieng"
    # in-plane load: a 2D plane-stress problem the idealization can actually carry
    _write_cae_project(pkg, direction="[0.0, -1.0, 0.0]")  # 120 x 80 x 10 plate, origin at 0
    problem = derive_topopt_problem_from_package(pkg, resolution=12, max_iters=6)
    res = run_topology_optimization(problem)
    node = topology_result_to_shape_ir(res)["parts"][0]
    nelx, nely = problem["grid"]["nelx"], problem["grid"]["nely"]
    frame = problem["derivation"]["frame"]
    assert node["placed_in_frame"] is True
    assert node["origin"] == frame["origin"] == [0.0, 0.0, 0.0]
    assert node["cell_size"] == frame["cell_size"] and node["thickness"] == 10.0
    # the grid exactly tiles the in-plane design-space extents
    assert abs(nelx * node["cell_size"][0] - 120) < 1e-6
    assert abs(nely * node["cell_size"][1] - 80) < 1e-6


def test_write_topology_optimization_into_package(tmp_path: Path):
    pkg = tmp_path / "m.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/shape_ir.json", json.dumps({"parts": [{"id": "plate", "type": "box"}]}))
    res = write_topology_optimization(
        pkg, {"density": [[1.0, 0.0], [0.0, 1.0]], "design_space_node": "plate"},
        optimizer="precomputed",
    )
    assert res["result"]["density_grid"]["values"] == [[1.0, 0.0], [0.0, 1.0]]
    with zipfile.ZipFile(pkg) as zf:
        assert TOPOLOGY_OPTIMIZATION_PATH in zf.namelist()
        loaded = json.loads(zf.read(TOPOLOGY_OPTIMIZATION_PATH))
    assert loaded["optimizer"]["name"] == "precomputed"
    assert loaded["provenance"]["design_space_node"] == "plate"
