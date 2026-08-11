"""An MCP-authored CAE setup must be usable by the optimize→verify loop (#489).

Measured failure (dogfood, 2026-08-11): a package whose materials/loads/BCs were
authored the documented, key-free way — `cae.apply_setup_patch` writing
`simulation/cae_imports/parsed_*.json` — solved fine through `cae.run_solver`
(0.001064 mm / 11.3865 MPa) but every `opt.sizing_sweep` variant came back

    solver_executed: false
    reason: "simulation/setup.yaml not found — run CAE setup first"

because `solve_package_static` read only `setup.yaml`, which only the
LLM-backed `ai_preprocessing` writes. The flagship optimize→verify loop was
therefore unreachable from the documented agent path.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.simulation_runner import _synthesize_setup_from_parsed


def _write_parsed_package(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("simulation/cae_imports/parsed_materials.json", json.dumps(
            {"materials": [{"name": "Al6061-T6", "density_kg_m3": 2700,
                            "youngs_modulus_pa": 69_000_000_000, "poisson_ratio": 0.33}]}))
        zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json", json.dumps(
            {"boundary_conditions": [{"id": "bc_001", "type": "fixed", "target": "BC_001",
                                      "dof_start": 1, "dof_end": 3, "value": 0}]}))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps(
            {"loads": [{"id": "load_001", "type": "force", "target": "LOAD_001",
                        "value_n": 500, "direction": [0, 0, -1]}]}))
        zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": [
            {"cae_entity": "BC_001", "face_ids": ["face_005"],
             "maps_to": {"feature_id": "bc_001", "role": "fixed_support"}},
            {"cae_entity": "LOAD_001", "face_ids": ["face_020"],
             "maps_to": {"feature_id": "load_001", "role": "load_application"}},
        ]}))
        zf.writestr("simulation/solver_settings.json", json.dumps(
            {"solver": "CalculiX", "analysis_type": "static", "mesh_size_mm": 3}))
    return path


def test_parsed_artifacts_become_a_deck_ready_setup(tmp_path: Path) -> None:
    setup = _synthesize_setup_from_parsed(_write_parsed_package(tmp_path / "p.aieng"))

    assert setup is not None

    # Material: Pa -> MPa, keyed by name, with material_name selected.
    assert setup["material_name"] == "Al6061-T6"
    assert setup["materials"]["Al6061-T6"]["youngs_modulus_mpa"] == 69000.0
    assert setup["materials"]["Al6061-T6"]["poisson_ratio"] == 0.33

    # NSET targets are mapped back to the feature ids the deck builder resolves.
    assert setup["boundary_conditions"] == [
        {"target_feature": "bc_001", "type": "fixed"}
    ]
    assert setup["loads"][0]["target_feature"] == "load_001"
    assert setup["loads"][0]["value_n"] == 500
    assert setup["loads"][0]["direction"] == [0, 0, -1]

    assert setup["mesh"]["target_size_mm"] == 3
    assert setup["synthesized_from"].endswith("parsed_*.json")


def test_explicit_target_feature_is_preserved(tmp_path: Path) -> None:
    """Setup-shaped entries that already name a feature must not be remapped."""
    pkg = tmp_path / "explicit.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps(
            {"loads": [{"target_feature": "feat_load", "value_n": 10, "direction": [1, 0, 0]}]}))
        zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": []}))
    setup = _synthesize_setup_from_parsed(pkg)
    assert setup is not None
    assert setup["loads"][0]["target_feature"] == "feat_load"


def test_empty_package_synthesizes_nothing(tmp_path: Path) -> None:
    """Nothing to translate must stay an honest 'no setup', not an empty deck."""
    pkg = tmp_path / "empty.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")
    assert _synthesize_setup_from_parsed(pkg) is None


def test_unmappable_target_is_dropped_not_guessed(tmp_path: Path) -> None:
    """A target with no cae_mapping entry must not be bound to a random feature."""
    pkg = tmp_path / "unmapped.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps(
            {"loads": [{"target": "GHOST_NSET", "value_n": 10, "direction": [0, 0, -1]}]}))
        zf.writestr("simulation/cae_imports/parsed_materials.json", json.dumps(
            {"materials": [{"name": "M", "youngs_modulus_pa": 1e11}]}))
        zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": []}))
    setup = _synthesize_setup_from_parsed(pkg)
    assert setup is not None          # the material alone is still worth reporting
    assert setup["loads"] == []
