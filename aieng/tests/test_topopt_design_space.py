"""Choosing a design space, and mapping a load that is not on its boundary (#500).

The 3D derivation defaults its design space to the LARGEST SINGLE SOLID, so on
the archetypal bracket — a base plate carrying a rib — the load applied to the
rib lands outside it and nothing could be optimized. Three separate causes were
tangled together there, each of which alone made the case unusable:

1. no way to name a different design space at all;
2. the face's normal axis was inferred from its bounding box's thinnest extent,
   which is exact for an axis-aligned face and wrong for every inclined one — a
   gusset hypotenuse is thin along the RIB'S THICKNESS, not along its own normal;
3. a load was required to lie on a boundary PLANE, which an inclined face
   spanning the domain never does.

The default is deliberately unchanged; the refusal now names the design spaces
that would work instead.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from aieng.converters.topology_optimization import (  # noqa: E402
    WHOLE_MODEL_DESIGN_SPACE,
    derive_topopt_problem_from_package,
    run_topology_optimization,
)


def _bracket(pkg: Path, *, load_face_bbox, load_normal) -> Path:
    """A 120x80x6 plate (`base_plate`) carrying a rib that stands on top of it."""
    topo = {"entities": [
        {"id": "body_001", "type": "solid", "name": "base_plate",
         "source_ir_node": "base_plate", "bounding_box": [-60, -40, 0, 60, 40, 6]},
        {"id": "body_002", "type": "solid", "name": "rib_main",
         "source_ir_node": "rib_main", "bounding_box": [0, -2.5, 6, 35, 2.5, 29]},
        # plate underside — a genuine boundary face of every design space here
        {"id": "face_bottom", "type": "face", "body_id": "body_001",
         "bounding_box": [-60, -40, 0, 60, 40, 0], "normal": [0, 0, -1]},
        {"id": "face_load", "type": "face", "body_id": "body_002",
         "bounding_box": list(load_face_bbox), "normal": list(load_normal)},
    ]}
    mapping = {"mappings": [
        {"cae_entity": "N_FIX", "maps_to": {"feature_id": "feat_fix"}, "face_ids": ["face_bottom"]},
        {"cae_entity": "N_LOAD", "maps_to": {"feature_id": "feat_load"}, "face_ids": ["face_load"]},
    ]}
    setup = (
        "boundary_conditions:\n  - {id: bc1, target_feature: feat_fix, type: fixed}\n"
        "loads:\n  - {id: ld1, target_feature: feat_load, type: force, value_n: 500.0, "
        "direction: [0.0, 0.0, -1.0]}\n"
    )
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(topo))
        zf.writestr("simulation/cae_mapping.json", json.dumps(mapping))
        zf.writestr("simulation/setup.yaml", setup)
    return pkg


def _inclined_bracket(tmp_path: Path) -> Path:
    """Load on the rib's INCLINED face — thin in y, normal mostly +z."""
    return _bracket(
        tmp_path / "bracket.aieng",
        load_face_bbox=[3, -2.5, 9.7, 34, 2.5, 29],
        load_normal=[0.53, 0.0, 0.85],
    )


def _codes(problem: dict) -> list[str]:
    return [c["design_space_node"] for c in problem.get("design_space_candidates", [])]


# ── naming a design space ────────────────────────────────────────────────────

def test_the_default_refuses_and_names_the_design_spaces_that_would_work(tmp_path: Path):
    pytest.importorskip("yaml")
    problem = derive_topopt_problem_from_package(
        _inclined_bracket(tmp_path), dimension="3d", resolution_3d=12)

    assert problem["status"] == "needs_user_input"
    assert problem["design_space_node"] == "base_plate", "default is the largest solid"
    assert _codes(problem) == ["base_plate", "rib_main", WHOLE_MODEL_DESIGN_SPACE]
    assert any("lies outside the design space" in d for d in problem["diagnostics"]), \
        problem["diagnostics"]
    assert any("rib_main" in d for d in problem["diagnostics"]), "name the owning body"


def test_the_suggested_envelope_actually_produces_a_usable_problem(tmp_path: Path):
    """A suggestion that does not work is worse than no suggestion."""
    pytest.importorskip("yaml")
    problem = derive_topopt_problem_from_package(
        _inclined_bracket(tmp_path), dimension="3d", resolution_3d=12,
        design_space_node=WHOLE_MODEL_DESIGN_SPACE)

    assert problem.get("status") == "ok", problem.get("reason")
    assert problem["design_space_node"] == WHOLE_MODEL_DESIGN_SPACE
    assert len(problem["bcs"]["supports"]) == 1 and len(problem["bcs"]["loads"]) == 1
    load = problem["bcs"]["loads"][0]
    assert load["fz"] == -500.0 and load["cells"]

    result = run_topology_optimization(problem, optimizer="simp_3d")["result"]
    history = result["compliance_history"]
    assert history[-1] < history[0], "the optimizer must actually reduce compliance"


def test_an_unknown_design_space_is_refused_with_the_known_ones(tmp_path: Path):
    pytest.importorskip("yaml")
    problem = derive_topopt_problem_from_package(
        _inclined_bracket(tmp_path), dimension="3d", design_space_node="not_a_body")
    assert problem["status"] == "needs_user_input"
    assert "matches no body" in problem["reason"]
    assert WHOLE_MODEL_DESIGN_SPACE in problem["reason"]


# ── the two mapping defects ──────────────────────────────────────────────────

def test_a_face_outside_the_design_space_is_not_on_its_boundary(tmp_path: Path):
    """`min(rel, 1 - rel)` goes NEGATIVE above the domain, which read as "boundary".

    The rib's load face sits 19 mm above a 6 mm plate. Before the range check it
    was silently mapped onto the plate's top layer — a load applied where it does
    not act, reported as a derived problem.
    """
    pytest.importorskip("yaml")
    problem = derive_topopt_problem_from_package(
        _inclined_bracket(tmp_path), dimension="3d", resolution_3d=12,
        design_space_node="base_plate")
    assert problem["status"] == "needs_user_input", "a load above the plate is not on it"
    assert problem["load_count"] == 0


def test_an_inclined_load_face_maps_by_its_recorded_normal(tmp_path: Path):
    """Bbox-thinnest-extent picks the rib's thickness axis, not the face normal."""
    pytest.importorskip("yaml")
    problem = derive_topopt_problem_from_package(
        _inclined_bracket(tmp_path), dimension="3d", resolution_3d=12,
        design_space_node=WHOLE_MODEL_DESIGN_SPACE)
    assert problem.get("status") == "ok"
    load = problem["bcs"]["loads"][0]
    # it cuts through the domain, so it is mapped by occupancy — and says so
    assert load["mapping"] == "occupied_cells"
    assert any("cuts through the design space" in w
               for w in problem["derivation"]["warnings"]), problem["derivation"]["warnings"]


def test_an_explicit_zero_load_stays_zero(tmp_path: Path):
    """`float(value_n or 1.0)` turned a deliberate 0 N into a 1 N reference load.

    The mirror of the silent-zero-load defect: there a real load became 0, here a
    deliberate 0 becomes a load that was never applied. `cae.setup_static`
    refuses `force_n: 0` precisely so zero stress is never reported as a result;
    the derivation must not reintroduce it.
    """
    pytest.importorskip("yaml")
    pkg = _inclined_bracket(tmp_path)
    setup = (
        "boundary_conditions:\n  - {id: bc1, target_feature: feat_fix, type: fixed}\n"
        "loads:\n  - {id: ld1, target_feature: feat_load, type: force, value_n: 0.0, "
        "direction: [0.0, 0.0, -1.0]}\n"
    )
    tmp = pkg.with_suffix(".tmp.aieng")
    with zipfile.ZipFile(pkg) as src, zipfile.ZipFile(tmp, "w") as dst:
        for item in src.infolist():
            if item.filename != "simulation/setup.yaml":
                dst.writestr(item, src.read(item.filename))
        dst.writestr("simulation/setup.yaml", setup)
    tmp.replace(pkg)

    problem = derive_topopt_problem_from_package(
        pkg, dimension="3d", resolution_3d=12, design_space_node=WHOLE_MODEL_DESIGN_SPACE)
    assert problem["status"] == "needs_user_input", "a 0 N load is not a load"
    assert problem["load_count"] == 0


def test_a_face_without_a_bbox_is_diagnosed_not_crashed(tmp_path: Path):
    """Callers pass `faces.get(...).get("bbox") or []` — indexing that would raise."""
    pytest.importorskip("yaml")
    pkg = _bracket(
        tmp_path / "nobbox.aieng",
        load_face_bbox=[0, -2.5, 29, 35, 2.5, 29],
        load_normal=[0, 0, 1],
    )
    topo = json.loads(zipfile.ZipFile(pkg).read("geometry/topology_map.json").decode("utf-8"))
    for ent in topo["entities"]:
        if ent["id"] == "face_load":
            ent.pop("bounding_box")
    tmp = pkg.with_suffix(".tmp.aieng")
    with zipfile.ZipFile(pkg) as src, zipfile.ZipFile(tmp, "w") as dst:
        for item in src.infolist():
            if item.filename != "geometry/topology_map.json":
                dst.writestr(item, src.read(item.filename))
        dst.writestr("geometry/topology_map.json", json.dumps(topo))
    tmp.replace(pkg)

    problem = derive_topopt_problem_from_package(pkg, dimension="3d", resolution_3d=12)
    assert problem["status"] == "needs_user_input"      # diagnosed, not an IndexError


def test_a_boundary_load_still_maps_as_a_boundary_layer(tmp_path: Path):
    """The occupancy fallback must not swallow the ordinary case."""
    pytest.importorskip("yaml")
    pkg = _bracket(
        tmp_path / "flat.aieng",
        load_face_bbox=[0, -2.5, 29, 35, 2.5, 29],   # flat rib top, on the envelope's zmax
        load_normal=[0, 0, 1],
    )
    problem = derive_topopt_problem_from_package(
        pkg, dimension="3d", resolution_3d=12, design_space_node=WHOLE_MODEL_DESIGN_SPACE)
    assert problem.get("status") == "ok", problem.get("reason")
    assert problem["bcs"]["loads"][0]["mapping"] == "boundary_layer"
    assert not any("cuts through" in w for w in problem["derivation"]["warnings"])
