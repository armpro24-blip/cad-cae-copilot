"""A load written the documented agent way must reach CalculiX with its force.

Measured failure (dogfood, 2026-08-11): `cae.apply_setup_patch` writes
`simulation/cae_imports/parsed_loads.json` in the AUTHORING shape the tool
prescribes — a total force `value_n` plus a `direction` vector:

    {"loads": [{"id": "load_001", "target": "LOAD_001",
                "value_n": 500, "direction": [0, 0, -1]}]}

`_resolve_loads`'s parsed_loads fallback returned those dicts verbatim, but the
deck emitter consumes the per-DOF shape `{target, dof, value}`. So it read
`value` (absent -> 0.0) and `dof` (absent -> 2) and wrote

    *CLOAD
    LOAD_001, 2, 0.000000

CalculiX then solved a completely unloaded 47k-DOF model, converged, and the
package reported max_displacement 0.0 mm / max_von_mises 0.0 MPa with
`warnings: []` — a silent wrong answer dressed as a result.
"""
from __future__ import annotations

from aieng.simulation.deck_generator import _normalize_parsed_loads, _resolve_loads


_AUTHORED = [
    {"id": "load_001", "type": "force", "target": "LOAD_001",
     "value_n": 500, "direction": [0, 0, -1]}
]


def test_authoring_shape_keeps_magnitude_and_direction() -> None:
    out = _normalize_parsed_loads(_AUTHORED)

    assert len(out) == 1, out
    entry = out[0]
    assert entry["target"] == "LOAD_001"
    assert entry["dof"] == 3, "a -Z load must land on DOF 3, not the DOF-2 default"
    assert entry["value"] == -500.0, "the 500 N total force must survive translation"


def test_diagonal_load_keeps_every_component() -> None:
    out = _normalize_parsed_loads(
        [{"id": "l", "target": "T", "value_n": 100, "direction": [0.6, 0.0, -0.8]}]
    )
    by_dof = {e["dof"]: e["value"] for e in out}
    assert by_dof == {1: 60.0, 3: -80.0}


def test_emitter_shape_passes_through_unchanged() -> None:
    """Packages already authored per-DOF must keep working."""
    already = [{"id": "l_dof3", "target": "T", "dof": 3, "value": -12.5}]
    assert _normalize_parsed_loads(already) == already


def test_resolve_loads_uses_the_translation_for_the_parsed_fallback() -> None:
    warnings: list[str] = []
    loads = _resolve_loads(None, {"loads": _AUTHORED}, {"mappings": []}, warnings)
    assert loads and loads[0]["dof"] == 3 and loads[0]["value"] == -500.0


def test_zero_total_load_is_flagged_not_silently_solved() -> None:
    """An all-zero load set must produce a loud warning, not a quiet 0 MPa run."""
    import zipfile
    import json
    import tempfile
    from pathlib import Path

    from aieng.simulation.deck_generator import generate_solver_input_package

    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "zero_load.aieng"
        mesh = "\n".join([
            "*NODE", "1, 0.0, 0.0, 0.0", "2, 1.0, 0.0, 0.0",
            "3, 0.0, 1.0, 0.0", "4, 0.0, 0.0, 1.0",
            "*ELEMENT, TYPE=C3D4, ELSET=EALL", "1, 1, 2, 3, 4",
            "*NSET, NSET=BC_001", "1, 2",
            "*NSET, NSET=LOAD_001", "3, 4",
        ])
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": "0.1"}))
            zf.writestr("simulation/cae_imports/source_solver_deck.inp", mesh)
            zf.writestr("simulation/solver_settings.json",
                        json.dumps({"solver": "CalculiX", "analysis_type": "static"}))
            zf.writestr("simulation/cae_imports/parsed_materials.json", json.dumps(
                {"materials": [{"name": "M", "youngs_modulus_pa": 69e9,
                                "poisson_ratio": 0.33, "density_kg_m3": 2700}]}))
            zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json", json.dumps(
                {"boundary_conditions": [{"id": "bc", "type": "fixed", "target": "BC_001",
                                          "dof_start": 1, "dof_end": 3, "value": 0}]}))
            # A load that carries no force at all.
            zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps(
                {"loads": [{"id": "l", "target": "LOAD_001", "value_n": 0,
                            "direction": [0, 0, -1]}]}))
            zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": [
                {"cae_entity": "BC_001", "maps_to": {"feature_id": "bc"}, "face_ids": ["f1"]},
                {"cae_entity": "LOAD_001", "maps_to": {"feature_id": "l"}, "face_ids": ["f2"]},
            ]}))

        result = generate_solver_input_package(pkg)

    warnings = " ".join(result.get("warnings") or [])
    assert "UNLOADED" in warnings or "0 N" in warnings, result.get("warnings")
