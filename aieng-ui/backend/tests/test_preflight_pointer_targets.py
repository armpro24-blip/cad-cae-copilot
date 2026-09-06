"""The preflight must not block a setup that is already correct (#485/#486).

Measured 2026-08-11: after authoring loads/BCs the documented pointer way
(`target: "@face:face_005"`), `cae.prepare_solver_run` reported
`nset_binding_invalid` and handed the caller a `REPLACE_WITH_REFERENCED_NSET`
template — for bindings `normalize_cae_bindings` (#376) synthesises by itself at
deck generation. Continuing to `cae.generate_solver_input` on the *same* package
derived BC_001/LOAD_001 with no empty sets, so the preflight had sent the agent
to hand-write a mapping it did not need.

Also pins the read-back that answers "what is set up in this project?" without
running anything, and the FRD default (#486).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.runtime_registry.cae import (
    _describe_cae_setup,
    _find_package_frd,
    _validate_cae_mapping_for_solver,
)

_TOPOLOGY = {
    "entities": [
        {"id": "body_001", "type": "solid", "name": "base_plate"},
        {"id": "face_005", "type": "face", "surface_type": "plane", "area": 9600.0,
         "normal": [0, 0, -1], "body_id": "body_001"},
        {"id": "face_008", "type": "face", "surface_type": "plane", "area": 235.8,
         "normal": [0.53, 0, 0.85], "body_id": "body_001"},
    ]
}


def _pointer_package(path: Path, *, with_mapping: bool = False) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("simulation/solver_settings.json",
                    json.dumps({"solver": "CalculiX", "analysis_type": "static"}))
        zf.writestr("simulation/cae_imports/parsed_materials.json", json.dumps(
            {"materials": [{"name": "Al6061-T6", "youngs_modulus_pa": 69e9,
                            "poisson_ratio": 0.33, "density_kg_m3": 2700}]}))
        zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json", json.dumps(
            {"boundary_conditions": [{"id": "bc_001", "type": "fixed",
                                      "target": "@face:face_005",
                                      "dof_start": 1, "dof_end": 3, "value": 0}]}))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps(
            {"loads": [{"id": "load_001", "type": "force", "target": "@face:face_008",
                        "value_n": 500, "direction": [0, 0, -1]}]}))
        if with_mapping:
            zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": [
                {"cae_entity": "BC_001", "face_ids": ["face_005"],
                 "maps_to": {"feature_id": "bc_001"}},
            ]}))
    return path


def test_resolvable_pointer_targets_are_not_undefined_nsets(tmp_path: Path) -> None:
    result = _validate_cae_mapping_for_solver(_pointer_package(tmp_path / "p.aieng"))

    assert result["valid"] is True, result["warnings"]
    assert result["undefined_nsets"] == []
    assert result["pointer_targets_pending_binding"] == ["@face:face_005", "@face:face_008"]
    assert any("automatically" in w for w in result["warnings"])


def test_pointer_to_a_face_that_does_not_exist_is_still_refused(tmp_path: Path) -> None:
    """The guard must keep catching a genuinely wrong face."""
    pkg = tmp_path / "ghost.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps(
            {"loads": [{"id": "l", "target": "@face:face_999", "value_n": 10,
                        "direction": [0, 0, -1]}]}))
    result = _validate_cae_mapping_for_solver(pkg)

    assert result["valid"] is False
    assert result["undefined_nsets"] == ["@face:face_999"]
    assert any("no such face exists" in w for w in result["warnings"])


def test_named_nset_without_a_mapping_is_still_refused(tmp_path: Path) -> None:
    pkg = tmp_path / "named.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps(
            {"loads": [{"id": "l", "target": "LOAD_TOP", "value_n": 10,
                        "direction": [0, 0, -1]}]}))
    result = _validate_cae_mapping_for_solver(pkg)

    assert result["valid"] is False
    assert result["undefined_nsets"] == ["LOAD_TOP"]


def test_setup_read_back_is_in_engineering_language(tmp_path: Path) -> None:
    lines = _describe_cae_setup(_pointer_package(tmp_path / "d.aieng"))
    blob = "\n".join(lines)

    assert "analysis: static" in blob
    assert "Al6061-T6" in blob and "69 GPa" in blob
    assert "held (fixed, DOF 1-3)" in blob
    assert "9600.0 mm²" in blob and "on base_plate" in blob
    assert "500 N along [0.00, 0.00, -1.00]" in blob
    assert "mesh: not generated yet" in blob


def test_read_back_of_an_empty_package_says_nothing(tmp_path: Path) -> None:
    pkg = tmp_path / "empty.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")
    assert _describe_cae_setup(pkg) == []


def test_package_frd_is_found_without_being_told_where(tmp_path: Path) -> None:
    pkg = tmp_path / "run.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("simulation/runs/run_001/outputs/result.frd", "  1C" + "x" * 40)
    found = _find_package_frd(pkg)

    assert found is not None
    # The member name comes back too: the temp path is meaningless to anyone
    # who opens the package later, and `metrics_source` needs the in-package
    # artifact and its run to say which run produced a number.
    path, tmpdir, member = found
    assert Path(path).exists() and Path(path).suffix == ".frd"
    assert member == "simulation/runs/run_001/outputs/result.frd"
    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)


def test_a_requested_run_is_never_answered_with_another_runs_frd(tmp_path: Path) -> None:
    """`asked-a-got-b`: the newest FRD is the right default, not the right answer.

    With no run named, "the newest" is the only sensible pick. But when the
    caller names `run_001`, falling back to whatever else is in the package
    hands them run_002's numbers — and `cae.extract_solver_results` then records
    them under the run they asked for. Wrong result, right-looking provenance.
    """
    pkg = tmp_path / "two_runs.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("simulation/runs/run_002/outputs/result.frd", "  1C" + "x" * 40)

    assert _find_package_frd(pkg, "run_001", require_run=True) is None

    # Unnamed, the newest result is still what you get.
    found = _find_package_frd(pkg)
    assert found is not None and found[2].startswith("simulation/runs/run_002/")
    import shutil

    shutil.rmtree(found[1], ignore_errors=True)


def test_no_frd_in_package_returns_none(tmp_path: Path) -> None:
    pkg = tmp_path / "norun.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")
    assert _find_package_frd(pkg) is None
