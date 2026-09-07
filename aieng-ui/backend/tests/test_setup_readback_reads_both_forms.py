"""The setup read-back must not report a real load as 0 N.

AGENTS.md points at `cae.prepare_solver_run`'s `setup_description` to answer
"what is set up in this project?" — "`cae.setup_static` echoes what *it* just
bound, this reports what *is* bound."

Two cold-start agent trials independently found it reporting `load: 0 N` on real
projects. Confirmed live on `6bd4cbe32c00`, whose `parsed_loads.json` holds
`{"dof": 3, "value": -50}`:

    load: 0 N along [0.00, 0.00, -1.00] on @face:face_002 ...
    mesh: not generated yet

in the same response whose preflight said `has_mesh: true` and
`mesh_artifact_path: "simulation/mesh.inp"`.

Three defects in four lines, all the same shape — a reader that knows one of two
legitimate forms:

1. `float(load.get("value_n") or 0.0)` — `parsed_cae_loads.schema.json` declares
   TWO complete forms in its own `anyOf` and explains them in its own comment:
   `dof` + `value` is how a solver deck states a load, `value_n` + `direction`
   is how an engineer does. A deck-imported load has no `value_n`, so it read as
   zero — the exact value `cae.setup_static` REFUSES because it "would converge
   on an unloaded model and report zero stress as a result".
2. `load.get("direction") or [0, 0, -1]` — an invented direction, right for that
   project by luck and wrong for any load on X or Y or any positive one.
3. `has_mesh = "simulation/mesh/mesh.inp" in names` — one hardcoded path;
   an imported source deck puts the mesh at `simulation/mesh.inp`.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.runtime_registry.cae import _describe_cae_setup, _load_magnitude_and_direction

_TOPOLOGY = {
    "entities": [
        {"id": "body_001", "type": "solid", "name": "beam"},
        {"id": "face_001", "type": "face", "surface_type": "plane", "area": 1000.0,
         "normal": [-1.0, 0.0, 0.0], "body_id": "body_001"},
        {"id": "face_002", "type": "face", "surface_type": "plane", "area": 1000.0,
         "normal": [1.0, 0.0, 0.0], "body_id": "body_001"},
    ]
}


# ── the pure reader, both documented forms ──────────────────────────────────

def test_the_authored_form_is_read() -> None:
    newtons, direction = _load_magnitude_and_direction(
        {"value_n": 500.0, "direction": [0.0, 0.0, -1.0]}
    )
    assert newtons == 500.0
    assert direction == [0.0, 0.0, -1.0]


def test_the_deck_form_is_read() -> None:
    """`{"dof": 3, "value": -50}` is 50 N along -Z. It used to read as 0 N."""
    newtons, direction = _load_magnitude_and_direction({"dof": 3, "value": -50})

    assert newtons == 50.0
    assert direction == [0.0, 0.0, -1.0]


def test_the_deck_form_direction_follows_the_dof_and_sign() -> None:
    """The old default was [0,0,-1] for everything — right here, wrong elsewhere."""
    assert _load_magnitude_and_direction({"dof": 1, "value": 250})[1] == [1.0, 0.0, 0.0]
    assert _load_magnitude_and_direction({"dof": 2, "value": -80})[1] == [0.0, -1.0, 0.0]
    assert _load_magnitude_and_direction({"dof": 3, "value": 12.5})[1] == [0.0, 0.0, 1.0]
    assert _load_magnitude_and_direction({"dof": 1, "value": -250})[0] == 250.0


def test_neither_form_reports_no_magnitude_rather_than_zero() -> None:
    """`None` means "not recorded". Rendering it as 0 N is the defect itself."""
    assert _load_magnitude_and_direction({}) == (None, None)
    assert _load_magnitude_and_direction({"target": "@face:face_002"}) == (None, None)
    # A dof outside 1-3 is not a direction this reader can derive.
    assert _load_magnitude_and_direction({"dof": 11, "value": 100.0}) == (None, None)


def test_an_authored_load_with_no_direction_says_so() -> None:
    newtons, direction = _load_magnitude_and_direction({"value_n": 500.0})

    assert newtons == 500.0
    assert direction is None, "absent is not [0, 0, -1]"


# ── the rendered read-back ──────────────────────────────────────────────────

def _package(path: Path, *, load: dict, mesh_member: str | None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("simulation/solver_settings.json",
                    json.dumps({"analysis_type": "static", "solver": "CalculiX"}))
        zf.writestr("simulation/cae_imports/parsed_materials.json", json.dumps(
            {"materials": [{"name": "aluminum_6061",
                            "elastic": {"youngs_modulus": 69000.0, "poisson_ratio": 0.33}}]}))
        zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json", json.dumps(
            {"boundary_conditions": [
                {"id": "bc_fixed_end", "type": "fixed", "target": "@face:face_001",
                 "dof_start": 1, "dof_end": 3, "value": 0}]}))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps({"loads": [load]}))
        if mesh_member:
            zf.writestr(mesh_member, "*NODE\n1, 0.0, 0.0, 0.0\n")
    return path


def test_a_deck_imported_load_is_not_reported_as_zero(tmp_path: Path) -> None:
    """The measured case, on the shape the real project carries."""
    pkg = _package(
        tmp_path / "deck.aieng",
        load={"id": "load_tip", "target": "@face:face_002", "dof": 3, "value": -50},
        mesh_member="simulation/mesh.inp",
    )

    blob = "\n".join(_describe_cae_setup(pkg))

    assert "load: 50 N along [0.00, 0.00, -1.00]" in blob, blob
    assert "load: 0 N" not in blob


def test_the_mesh_is_found_at_either_legitimate_path(tmp_path: Path) -> None:
    """`cae.generate_mesh` writes one path, an imported deck the other."""
    authored = {"id": "load_001", "target": "@face:face_002",
                "value_n": 500.0, "direction": [0.0, 0.0, -1.0]}
    for member in ("simulation/mesh/mesh.inp", "simulation/mesh.inp"):
        pkg = _package(tmp_path / f"{member.count('/')}.aieng",
                       load=authored, mesh_member=member)
        assert "mesh: present" in _describe_cae_setup(pkg), member


def test_no_mesh_still_says_not_generated(tmp_path: Path) -> None:
    pkg = _package(
        tmp_path / "nomesh.aieng",
        load={"id": "load_001", "target": "@face:face_002",
              "value_n": 500.0, "direction": [0.0, 0.0, -1.0]},
        mesh_member=None,
    )

    assert "mesh: not generated yet" in _describe_cae_setup(pkg)


def test_a_load_recorded_in_neither_form_is_named_as_such(tmp_path: Path) -> None:
    pkg = _package(
        tmp_path / "empty.aieng",
        load={"id": "load_001", "target": "@face:face_002"},
        mesh_member="simulation/mesh.inp",
    )

    blob = "\n".join(_describe_cae_setup(pkg))

    assert "magnitude not recorded" in blob, blob
    assert "0 N" not in blob


# ── the interface refuses an unsupported analysis before the deck does ──────

def test_the_analysis_type_enum_matches_what_the_generator_can_emit() -> None:
    """Drift here would let the interface accept what deck generation refuses.

    Asserted against the generator's own alias table rather than a literal list,
    so adding an analysis type cannot leave the schema behind.
    """
    from aieng.simulation.deck_generator import _ANALYSIS_TYPE_ALIASES
    from app.runtime_tool_schemas import TOOL_SCHEMAS

    accepted = {k for k in _ANALYSIS_TYPE_ALIASES if k}
    for tool in ("cae.setup_static", "cae.author_load_case"):
        enum = TOOL_SCHEMAS[tool]["properties"]["analysis_type"].get("enum")
        assert enum, f"{tool} must constrain analysis_type, not accept free text"
        unknown = sorted(set(enum) - accepted)
        assert not unknown, f"{tool} offers what the generator cannot emit: {unknown}"
