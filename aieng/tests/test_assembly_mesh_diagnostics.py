"""Pre-solver assembly mesh-interface NSET quality diagnostics (#200)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from aieng.converters.assembly_interface_resolution import (
    ASSEMBLY_CAE_DRAFT_PATH,
    ASSEMBLY_MESH_INTERFACE_DIAGNOSTICS_PATH,
    diagnose_mesh_interfaces,
    resolve_and_validate_assembly_geometry,
    resolve_assembly_interfaces,
)


def _face(fid: str, bbox: list[float], *, area: float = 100.0) -> dict:
    return {"id": fid, "type": "face", "bounding_box": bbox, "normal": [0, 0, 1.0], "area": area}


def _assembly(refs: dict, *, part_id: str = "p") -> dict:
    return {
        "format": "aieng.assembly_ir",
        "schema_version": "0.1",
        "unit": "mm",
        "parts": [{"id": part_id, "transform": {"translation": [0, 0, 0], "unit": "mm"}}],
        "interfaces": [{"id": "if1", "part_id": part_id, "semantic_role": "mounting_face", "topology_refs": refs}],
        "connections": [],
    }


def _diag(asm: dict, topo_by_part: dict) -> dict:
    resolution = resolve_assembly_interfaces(asm, topo_by_part)
    return diagnose_mesh_interfaces(asm, topo_by_part, resolution)


def _rec(diag: dict) -> dict:
    return diag["interfaces"][0]


def _codes(rec: dict) -> set[str]:
    return {f["code"] for f in rec["findings"]}


# Large part body so interfaces aren't flagged over-broad unless they truly span it.
_BIG_BODY = {"id": "p_body", "type": "solid", "bounding_box": [0, 0, 0, 200, 200, 5]}


def test_healthy_interface_is_ok_and_solver_safe() -> None:
    topo = {"p": {
        "p_body": _BIG_BODY,
        "f1": _face("f1", [0, 0, 5, 10, 10, 5]),
        "f2": _face("f2", [10, 0, 5, 20, 10, 5]),  # adjacent -> one region
    }}
    diag = _diag(_assembly({"face_ids": ["f1", "f2"]}), topo)
    rec = _rec(diag)
    assert rec["status"] == "ok"
    assert rec["findings"] == []
    assert rec["face_count"] == 2
    assert rec["region_count"] == 1
    assert diag["safe_for_solver"] is True
    assert diag["honesty"]["solver_executed"] is False


def test_empty_interface_blocks_solver() -> None:
    topo = {"p": {"p_body": _BIG_BODY}}  # 'ghost' face absent
    diag = _diag(_assembly({"face_ids": ["ghost"]}), topo)
    rec = _rec(diag)
    assert rec["status"] == "blocking"
    assert "empty_interface" in _codes(rec)
    assert diag["safe_for_solver"] is False
    assert diag["blocking_interfaces"] == ["if1"]


def test_single_face_interface_is_sparse_warning() -> None:
    topo = {"p": {"p_body": _BIG_BODY, "f1": _face("f1", [0, 0, 5, 10, 10, 5])}}
    rec = _rec(_diag(_assembly({"face_ids": ["f1"]}), topo))
    assert rec["status"] == "warning"
    assert "sparse_interface" in _codes(rec)


def test_disconnected_interface_regions_warn() -> None:
    topo = {"p": {
        "p_body": _BIG_BODY,
        "f1": _face("f1", [0, 0, 5, 10, 10, 5]),
        "f2": _face("f2", [150, 150, 5, 160, 160, 5]),  # far from f1 -> separate region
    }}
    rec = _rec(_diag(_assembly({"face_ids": ["f1", "f2"]}), topo))
    assert "disconnected_interface" in _codes(rec)
    assert rec["region_count"] == 2
    assert rec["status"] == "warning"


def test_over_broad_interface_warns() -> None:
    # Interface face spans essentially the whole part footprint.
    topo = {"p": {
        "p_body": {"id": "p_body", "type": "solid", "bounding_box": [0, 0, 0, 100, 100, 10]},
        "f_big": _face("f_big", [0, 0, 10, 100, 100, 10], area=10000.0),
    }}
    rec = _rec(_diag(_assembly({"face_ids": ["f_big"]}), topo))
    assert "over_broad_interface" in _codes(rec)
    assert rec["status"] == "warning"


# ── signal, not noise: a warning must describe a defect, not a geometry (#497) ──
#
# Measured on a dogfood gearbox: 4 of 4 correctly-authored interfaces warned
# (`ok: 0`), because both rules were satisfied by construction rather than by
# defect — one planar face is exactly how `cad.define_interface` is meant to be
# used, and a ring-shaped rim carries the part's own bbox diagonal while covering
# a fifth of it. A check that fires on every correct input gets ignored, and the
# genuinely blocking `empty_interface` gets ignored with it.

def test_one_substantial_face_is_not_sparse() -> None:
    """A single planar mating face is the normal authoring result, not a defect."""
    topo = {"p": {
        "p_body": {"id": "p_body", "type": "solid", "bounding_box": [0, 0, 0, 100, 100, 40]},
        # a rim covering ~a quarter of the 100x100 cross-section
        "rim": _face("rim", [0, 0, 40, 100, 100, 40], area=2400.0),
    }}
    rec = _rec(_diag(_assembly({"face_ids": ["rim"]}), topo))
    assert rec["status"] == "ok", rec["findings"]
    assert rec["coverage_fraction"] == 0.24


def test_a_single_sliver_face_is_still_sparse() -> None:
    """The real sparse signal — a tiny face — must survive."""
    topo = {"p": {
        "p_body": {"id": "p_body", "type": "solid", "bounding_box": [0, 0, 0, 100, 100, 40]},
        "sliver": _face("sliver", [0, 0, 40, 4, 4, 40], area=16.0),
    }}
    rec = _rec(_diag(_assembly({"face_ids": ["sliver"]}), topo))
    assert "sparse_interface" in _codes(rec)


def test_a_full_cylindrical_face_is_not_over_broad_by_construction() -> None:
    """A cylinder's lateral area is pi x its own bbox cross-section, always.

    A short journal band on a long shaft is the TRUE mating region, so it must
    not be flagged merely for being curved.
    """
    topo = {"p": {
        "p_body": {"id": "p_body", "type": "solid", "bounding_box": [-10, -10, 0, 10, 10, 200]},
        # 2*pi*r*L with r=10, L=20 -> 1256.6, over a bbox cross-section of 20x20
        "journal": _face("journal", [-10, -10, 90, 10, 10, 110], area=1256.6),
    }}
    rec = _rec(_diag(_assembly({"face_ids": ["journal"]}), topo))
    assert "over_broad_interface" not in _codes(rec), rec["findings"]
    assert "sparse_interface" not in _codes(rec), rec["findings"]
    assert rec["status"] == "ok"


def test_a_cylinder_spanning_the_whole_part_is_over_broad() -> None:
    """Selecting the entire shaft surface as the journal IS over-broad."""
    topo = {"p": {
        "p_body": {"id": "p_body", "type": "solid", "bounding_box": [-10, -10, 0, 10, 10, 200]},
        "whole": _face("whole", [-10, -10, 0, 10, 10, 200], area=12566.0),
    }}
    rec = _rec(_diag(_assembly({"face_ids": ["whole"]}), topo))
    assert rec["coverage_fraction"] > 1.0, "the wrapping branch must be exercised"
    assert "over_broad_interface" in _codes(rec)
    assert any("every axis" in f["message"] for f in rec["findings"])


def test_partial_resolution_warns_without_blocking() -> None:
    topo = {"p": {
        "p_body": _BIG_BODY,
        "f1": _face("f1", [0, 0, 5, 10, 10, 5]),
        "f2": _face("f2", [10, 0, 5, 20, 10, 5]),
    }}
    # one ref resolves, one is missing -> partial (still has usable faces)
    rec = _rec(_diag(_assembly({"face_ids": ["f1", "f2", "ghost"]}), topo))
    assert rec["status"] == "warning"
    assert "partial_resolution" in _codes(rec)
    assert "empty_interface" not in _codes(rec)


def test_orchestrator_writes_diagnostics_and_blocks_empty(tmp_path: Path) -> None:
    """No topology in the package -> interfaces are unresolved -> empty -> the
    mesh diagnostics artifact is written and the CAE draft is gated."""
    asm = {
        "format": "aieng.assembly_ir",
        "schema_version": "0.1",
        "unit": "mm",
        "parts": [
            {"id": "a", "role": "design_part", "transform": {"translation": [0, 0, 0], "unit": "mm"}},
            {"id": "b", "role": "reference_part", "transform": {"translation": [0, 0, 10], "unit": "mm"}},
        ],
        "interfaces": [
            {"id": "if_a", "part_id": "a", "semantic_role": "mounting_face", "topology_refs": {"face_ids": ["a_top"]}},
            {"id": "if_b", "part_id": "b", "semantic_role": "support_face", "topology_refs": {"face_ids": ["b_bot"]}},
        ],
        "connections": [{"id": "c1", "type": "rigid_tie", "part_a": "a", "part_b": "b",
                          "interface_a": "if_a", "interface_b": "if_b", "behavior": ["load_transfer"]}],
        "analysis_intent": {"design_parts": ["a"], "frozen_parts": ["b"]},
    }
    pkg = tmp_path / "asm.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("assembly/assembly_ir.json", json.dumps(asm))

    res = resolve_and_validate_assembly_geometry(pkg)
    assert res["assembly_present"] is True
    assert res["mesh_interface_safe_for_solver"] is False

    with zipfile.ZipFile(pkg) as zf:
        diag = json.loads(zf.read(ASSEMBLY_MESH_INTERFACE_DIAGNOSTICS_PATH))
        draft = json.loads(zf.read(ASSEMBLY_CAE_DRAFT_PATH))
    assert diag["safe_for_solver"] is False
    assert set(diag["blocking_interfaces"]) == {"if_a", "if_b"}
    assert draft["status"] == "needs_user_input"
    assert any("empty/unusable node set" in m for m in draft.get("needs_user_input", []))
