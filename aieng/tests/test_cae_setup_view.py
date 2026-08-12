"""Reading a package's CAE setup, whichever authoring path wrote it.

The shared reader exists because "only reads `simulation/setup.yaml`" was found
twice in different consumers — the second time (topology optimization) it did not
even fail honestly, it substituted a textbook cantilever. What it must NOT do is
trade one silent fiction for another: a synthesized setup feeds the static
solver, so an assumed material property has to be visible as assumed, and a
value the artifacts carry as text has to become a number rather than fail a
schema three steps later.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from aieng.cae_setup_view import load_cae_setup_from_package, synthesize_setup_from_parsed

_MAPPING = {"mappings": [
    {"cae_entity": "N_FIX", "maps_to": {"feature_id": "feat_fix"}},
    {"cae_entity": "N_LOAD", "maps_to": {"feature_id": "feat_load"}},
]}


def _write(pkg: Path, members: dict[str, object]) -> Path:
    with zipfile.ZipFile(pkg, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body if isinstance(body, str) else json.dumps(body))
    return pkg


def _parsed_members(*, materials=None, loads=None, bcs=None) -> dict[str, object]:
    return {
        "simulation/cae_mapping.json": _MAPPING,
        "simulation/cae_imports/parsed_materials.json": {"materials": materials or []},
        "simulation/cae_imports/parsed_boundary_conditions.json": {
            "boundary_conditions": bcs if bcs is not None else [
                {"target": "N_FIX", "type": "fixed"}]},
        "simulation/cae_imports/parsed_loads.json": {
            "loads": loads if loads is not None else [
                {"target": "N_LOAD", "value_n": 500.0, "direction": [0.0, 0.0, -1.0]}]},
    }


def test_the_key_free_authoring_shape_is_read(tmp_path: Path) -> None:
    """NSET targets become feature ids — the shape every consumer expects."""
    pkg = _write(tmp_path / "p.aieng", _parsed_members(materials=[
        {"name": "Al6061-T6", "youngs_modulus_pa": 6.9e10,
         "poisson_ratio": 0.33, "density_kg_m3": 2700}]))
    setup = load_cae_setup_from_package(pkg)

    assert setup["synthesized_from"] == "simulation/cae_imports/parsed_*.json"
    assert setup["boundary_conditions"] == [{"target_feature": "feat_fix", "type": "fixed"}]
    assert setup["loads"] == [
        {"target_feature": "feat_load", "value_n": 500.0, "direction": [0.0, 0.0, -1.0]}]
    assert setup["materials"]["Al6061-T6"]["youngs_modulus_mpa"] == 69000.0
    assert "assumed_properties" not in setup


def test_an_explicit_setup_document_wins(tmp_path: Path) -> None:
    members = _parsed_members(materials=[{"name": "Al6061-T6", "youngs_modulus_mpa": 69000}])
    members["simulation/setup.yaml"] = (
        "boundary_conditions:\n  - {target_feature: explicit_fix, type: fixed}\n"
        "loads:\n  - {target_feature: explicit_load, value_n: 12.0}\n"
    )
    setup = load_cae_setup_from_package(_write(tmp_path / "p.aieng", members))
    assert setup["boundary_conditions"][0]["target_feature"] == "explicit_fix"
    assert "synthesized_from" not in setup


def test_assumed_material_properties_are_named(tmp_path: Path) -> None:
    """A caller must be able to tell a declared property from an invented one."""
    pkg = _write(tmp_path / "p.aieng", _parsed_members(materials=[{"name": "Unknown"}]))
    setup = load_cae_setup_from_package(pkg)

    assert setup["materials"]["Unknown"]["youngs_modulus_mpa"] == 69000.0
    assert setup["assumed_properties"] == [
        "Unknown.youngs_modulus_mpa", "Unknown.poisson_ratio", "Unknown.density_kg_m3"]


def test_text_valued_artifacts_become_numbers(tmp_path: Path) -> None:
    """Otherwise the setup looks complete and fails a schema three steps later."""
    pkg = _write(tmp_path / "p.aieng", _parsed_members(
        materials=[{"name": "Steel", "youngs_modulus_mpa": "210000",
                    "poisson_ratio": "0.3", "density_kg_m3": "7850"}],
        loads=[{"target": "N_LOAD", "value_n": "250", "direction": ["0", "0", "-1"]}],
    ))
    setup = load_cae_setup_from_package(pkg)

    material = setup["materials"]["Steel"]
    assert material == {"youngs_modulus_mpa": 210000.0, "poisson_ratio": 0.3,
                        "density_kg_m3": 7850.0}
    assert setup["loads"][0]["value_n"] == 250.0
    assert setup["loads"][0]["direction"] == [0.0, 0.0, -1.0]
    assert "assumed_properties" not in setup


def test_a_malformed_direction_falls_back_without_crashing(tmp_path: Path) -> None:
    pkg = _write(tmp_path / "p.aieng", _parsed_members(
        loads=[{"target": "N_LOAD", "value_n": 10.0, "direction": "downward"}]))
    assert load_cae_setup_from_package(pkg)["loads"][0]["direction"] == [0.0, 0.0, -1.0]


def test_a_zero_direction_is_unusable_not_a_zero_force(tmp_path: Path) -> None:
    """[0,0,0] would resolve to zero force and be refused as "no usable load"."""
    pkg = _write(tmp_path / "p.aieng", _parsed_members(
        loads=[{"target": "N_LOAD", "value_n": 10.0, "direction": [0.0, 0.0, 0.0]}]))
    assert load_cae_setup_from_package(pkg)["loads"][0]["direction"] == [0.0, 0.0, -1.0]


def test_nan_and_infinity_do_not_reach_a_stiffness_matrix(tmp_path: Path) -> None:
    """`float("nan")` succeeds, so text coercion alone is not enough."""
    pkg = _write(tmp_path / "p.aieng", _parsed_members(
        materials=[{"name": "Bad", "youngs_modulus_mpa": "nan",
                    "poisson_ratio": float("inf"), "density_kg_m3": 7850}],
        loads=[{"target": "N_LOAD", "value_n": "inf", "direction": [0.0, 0.0, -1.0]}],
    ))
    setup = load_cae_setup_from_package(pkg)

    assert setup["materials"]["Bad"]["youngs_modulus_mpa"] == 69000.0
    assert setup["materials"]["Bad"]["poisson_ratio"] == 0.33
    assert setup["assumed_properties"] == ["Bad.youngs_modulus_mpa", "Bad.poisson_ratio"]
    assert setup["loads"][0]["value_n"] == 0.0


def test_nothing_to_synthesize_from_returns_none(tmp_path: Path) -> None:
    """So the caller can report "no CAE setup" honestly instead of inventing one."""
    pkg = _write(tmp_path / "p.aieng", {"geometry/topology_map.json": {"entities": []}})
    with zipfile.ZipFile(pkg) as zf:
        assert synthesize_setup_from_parsed(zf) is None
    assert load_cae_setup_from_package(pkg) == {}


def test_a_missing_package_is_not_a_crash(tmp_path: Path) -> None:
    assert load_cae_setup_from_package(tmp_path / "absent.aieng") == {}
