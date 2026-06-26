"""Tests for ``aieng.simulation.deck_generator`` (Phase 33).

Coverage:

- ``missing_items`` rejection paths (materials / BCs / loads absent).
- ``mesh_source_deck`` rejection path when ``source_solver_deck.inp`` is absent.
- Generated ``.inp`` contains the required CalculiX keyword cards.
- ``FileExistsError`` when overwrite is not set.
- ``FileNotFoundError`` when the package does not exist.
- The generator never claims solver convergence (honesty boundary).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import yaml

from aieng.simulation.deck_generator import (
    MissingSetupError,
    SOLVER_INPUT_PATH_TEMPLATE,
    _extract_mesh_section,
    generate_solver_input_package,
    normalize_analysis_type,
)


def test_extract_mesh_section_keeps_comma_attached_elset() -> None:
    """Regression: gmsh emits `*ELSET,ELSET=EALL` with the parameter
    comma-attached to the keyword (no space). The mesh-section extractor must
    still recognise it as a mesh keyword and preserve the EALL definition;
    dropping it leaves `*SOLID SECTION, ELSET=EALL` referencing an undefined
    set and ccx fails with `*ERROR reading *SOLID SECTION: element set EALL`."""
    deck = "\n".join(
        [
            "*NODE",
            "1, 0.0, 0.0, 0.0",
            "*ELEMENT, type=C3D4, ELSET=Volume1",
            "1, 1, 2, 3, 4",
            "*ELSET,ELSET=EALL",          # comma-attached, no space
            "1",
            "*SOLID SECTION, ELSET=EALL, MATERIAL=AIENG_MATERIAL",
            "*MATERIAL, NAME=AIENG_MATERIAL",   # non-mesh: terminates extraction
            "*ELASTIC",
            "69000, 0.33",
        ]
    )
    mesh = _extract_mesh_section(deck)
    assert "*ELSET,ELSET=EALL" in mesh.replace(" ", "")
    assert "1" in mesh.splitlines()  # the EALL data line survived
    # Non-mesh material cards are still excluded from the mesh section.
    assert "*MATERIAL" not in mesh.upper()


def test_extract_mesh_section_drops_2d_surface_elements() -> None:
    """Regression (#418): raw-gmsh source decks carry 2D surface element blocks
    (CPS3) alongside the C3D4 solids. ccx's gen3delem aborts on plane-stress
    elements in a 3D model, so the extractor must keep only solid elements,
    drop the surface element blocks, and drop *ELSET sets that referenced only
    those surface elements (while keeping the solid-only EALL)."""
    deck = "\n".join(
        [
            "*NODE",
            "1, 0.0, 0.0, 0.0",
            "2, 1.0, 0.0, 0.0",
            "*ELEMENT, type=CPS3, ELSET=Surface1",
            "1, 1, 2, 3",
            "2, 2, 3, 4",
            "*ELEMENT, type=C3D4, ELSET=Volume1",
            "1007, 1, 2, 3, 4",
            "1008, 2, 3, 4, 5",
            "*ELSET,ELSET=SURF1",   # references the dropped CPS3 elements
            "1, 2",
            "*ELSET,ELSET=EALL",    # references only the solid elements
            "1007, 1008",
            "*NSET, NSET=FIXED_END",
            "1, 2",
            "*SOLID SECTION, ELSET=EALL, MATERIAL=AIENG_MATERIAL",
            "*MATERIAL, NAME=AIENG_MATERIAL",
        ]
    )
    mesh = _extract_mesh_section(deck)
    flat = mesh.replace(" ", "")
    # 2D surface elements and their surface-only elset are gone.
    assert "CPS3" not in mesh.upper()
    assert "ELSET=SURF1" not in flat.upper()
    # Solid elements + their solid-only EALL survive.
    assert "C3D4" in mesh.upper()
    assert "*ELSET,ELSET=EALL" in flat
    assert "1007" in flat and "1008" in flat
    # NSET + section preserved.
    assert "NSET=FIXED_END" in flat
    assert "*SOLIDSECTION,ELSET=EALL" in flat
    assert "*MATERIAL" not in mesh.upper()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


_MINIMAL_SOURCE_DECK = """\
*NODE
1, 0.0, 0.0, 0.0
2, 1.0, 0.0, 0.0
3, 1.0, 1.0, 0.0
4, 0.0, 1.0, 0.0
*ELEMENT, TYPE=C3D4, ELSET=E_ALL
1, 1, 2, 3, 4
*NSET, NSET=N_FIX
1, 4
*NSET, NSET=N_LOAD
2, 3
*SOLID SECTION, ELSET=E_ALL, MATERIAL=Steel
1.0
"""


_SETUP_WITH_FEATURE_REFS = {
    "materials": {
        "Steel": {
            "youngs_modulus_mpa": 210000.0,
            "poisson_ratio": 0.3,
            "density_kg_m3": 7850.0,
        },
    },
    "boundary_conditions": [
        {"id": "bc_fix_001", "target_feature": "feat_fix", "type": "fixed"},
    ],
    "loads": [
        {
            "id": "load_pull_001",
            "target_feature": "feat_load",
            "value_n": 1000.0,
            "direction": [0, -1, 0],
        },
    ],
}


_CAE_MAPPING_WITH_FEATURES = {
    "mappings": [
        {"cae_entity": "N_FIX", "maps_to": {"feature_id": "feat_fix"}},
        {"cae_entity": "N_LOAD", "maps_to": {"feature_id": "feat_load"}},
    ],
}


def _write_package(
    path: Path,
    *,
    include_source_deck: bool = True,
    setup: dict | None = None,
    cae_mapping: dict | None = None,
    parsed_materials: dict | None = None,
    parsed_bcs: dict | None = None,
    parsed_loads: dict | None = None,
    solver_settings: dict | None = None,
) -> Path:
    """Write a minimal .aieng package that can drive deck generation."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "model_id": "deck_gen_test",
            "format_version": "0.1.0",
            "units": {"length": "mm", "mass": "kg", "force": "N", "stress": "MPa"},
            "resources": {"simulation": {}, "results": {}},
            "created_by": {"tool": "test", "created_at": "2026-01-01T00:00:00Z"},
        }
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("simulation/", b"")
        zf.writestr("simulation/cae_imports/", b"")
        zf.writestr("simulation/runs/", b"")
        if include_source_deck:
            zf.writestr("simulation/cae_imports/source_solver_deck.inp", _MINIMAL_SOURCE_DECK)
        if setup is not None:
            zf.writestr("simulation/setup.yaml", yaml.safe_dump(setup))
        if cae_mapping is not None:
            zf.writestr("simulation/cae_mapping.json", json.dumps(cae_mapping))
        if solver_settings is not None:
            zf.writestr("simulation/solver_settings.json", json.dumps(solver_settings))
        if parsed_materials is not None:
            zf.writestr(
                "simulation/cae_imports/parsed_materials.json",
                json.dumps(parsed_materials),
            )
        if parsed_bcs is not None:
            zf.writestr(
                "simulation/cae_imports/parsed_boundary_conditions.json",
                json.dumps(parsed_bcs),
            )
        if parsed_loads is not None:
            zf.writestr(
                "simulation/cae_imports/parsed_loads.json",
                json.dumps(parsed_loads),
            )
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_generates_deck_with_required_keyword_cards(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path / "ok.aieng",
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    result = generate_solver_input_package(pkg, run_id="run_001")
    assert result["ok"] is True
    assert result["missing_items"] == []

    out_path = SOLVER_INPUT_PATH_TEMPLATE.format(run_id="run_001")
    with zipfile.ZipFile(pkg) as zf:
        assert out_path in zf.namelist()
        deck = zf.read(out_path).decode("utf-8")

    # Required CalculiX keyword cards must be present.
    for kw in ("*HEADING", "*NODE", "*ELEMENT", "*MATERIAL", "*ELASTIC", "*DENSITY",
               "*BOUNDARY", "*CLOAD", "*STEP", "*STATIC", "*END STEP"):
        assert kw in deck, f"missing keyword card: {kw}"


def test_manifest_records_solver_input_for_run(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path / "ok.aieng",
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    generate_solver_input_package(pkg, run_id="run_001")
    with zipfile.ZipFile(pkg) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    runs = manifest["resources"]["simulation"]["runs"]
    assert runs["run_001"]["solver_input"] == SOLVER_INPUT_PATH_TEMPLATE.format(run_id="run_001")


# ---------------------------------------------------------------------------
# Analysis-type-aware step block (modal / buckling) — issue: FEA breadth
# ---------------------------------------------------------------------------


def _read_deck(pkg: Path, run_id: str) -> str:
    out_path = SOLVER_INPUT_PATH_TEMPLATE.format(run_id=run_id)
    with zipfile.ZipFile(pkg) as zf:
        return zf.read(out_path).decode("utf-8")


def test_static_step_block_unchanged(tmp_path: Path) -> None:
    """Default (no analysis_type) keeps the classic *STATIC step with U/S output."""
    pkg = _write_package(
        tmp_path / "static.aieng",
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    generate_solver_input_package(pkg, run_id="run_static")
    deck = _read_deck(pkg, "run_static")
    assert "*STATIC" in deck
    assert "*EL FILE" in deck and "*CLOAD" in deck
    assert "*FREQUENCY" not in deck and "*BUCKLE" not in deck


def test_cload_distributes_total_force_across_nset_nodes(tmp_path: Path) -> None:
    """A load value is the TOTAL force on its node set. The N_LOAD set has 2 nodes
    and the load is 1000 N, so the per-node *CLOAD must be 500 (1000/2) — not 1000
    applied to every node (which would be a 2x over-load)."""
    pkg = _write_package(
        tmp_path / "cload.aieng",
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    generate_solver_input_package(pkg, run_id="run_cload")
    deck = _read_deck(pkg, "run_cload")

    cload_lines = [
        ln.strip() for ln in deck.splitlines()
        if ln.strip().startswith("N_LOAD,")
    ]
    assert cload_lines, f"expected an N_LOAD *CLOAD line; deck:\n{deck}"
    # dof 2 (direction [0,-1,0]); per-node = -1000 / 2 nodes = -500.
    parts = [p.strip() for p in cload_lines[0].split(",")]
    assert parts[0] == "N_LOAD"
    assert parts[1] == "2"
    assert abs(float(parts[2]) + 500.0) < 1e-3, cload_lines[0]


def test_setup_material_density_is_converted_to_deck_units(tmp_path: Path) -> None:
    """setup.yaml stores density in kg/m^3; the CalculiX deck uses tonne/mm^3."""
    pkg = _write_package(
        tmp_path / "density.aieng",
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    generate_solver_input_package(pkg, run_id="run_density")
    deck = _read_deck(pkg, "run_density")

    assert "*DENSITY\n7.85e-09" in deck
    assert "\n7850.0\n" not in deck


def test_setup_load_direction_preserves_signed_components(tmp_path: Path) -> None:
    """Feature-referenced setup loads must not lose sign or off-axis components."""
    setup = {
        "materials": _SETUP_WITH_FEATURE_REFS["materials"],
        "boundary_conditions": _SETUP_WITH_FEATURE_REFS["boundary_conditions"],
        "loads": [
            {
                "id": "load_diag",
                "target_feature": "feat_load",
                "value_n": 1000.0,
                "direction": [0.25, -0.5, 0.0],
            }
        ],
    }
    pkg = _write_package(
        tmp_path / "signed_components.aieng",
        setup=setup,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    generate_solver_input_package(pkg, run_id="run_signed_components")
    deck = _read_deck(pkg, "run_signed_components")

    cload_lines = sorted(
        ln.strip() for ln in deck.splitlines()
        if ln.strip().startswith("N_LOAD,")
    )
    assert cload_lines == ["N_LOAD, 1, 125.000000", "N_LOAD, 2, -250.000000"]


def test_modal_step_block_emits_frequency_and_no_load_required(tmp_path: Path) -> None:
    """A modal analysis needs no loads: *FREQUENCY + N modes, no *STATIC/*CLOAD."""
    setup = {
        "materials": _SETUP_WITH_FEATURE_REFS["materials"],  # carries density
        "boundary_conditions": _SETUP_WITH_FEATURE_REFS["boundary_conditions"],
        # deliberately NO loads
    }
    pkg = _write_package(
        tmp_path / "modal.aieng",
        setup=setup,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
        solver_settings={"analysis_type": "modal", "num_modes": 6},
    )
    result = generate_solver_input_package(pkg, run_id="run_modal")
    assert result["ok"] is True
    deck = _read_deck(pkg, "run_modal")
    assert "*FREQUENCY" in deck
    assert "*STATIC" not in deck
    assert "*CLOAD" not in deck  # modal ignores loads
    # the requested mode count is the data line after *FREQUENCY
    lines = [ln.strip() for ln in deck.splitlines()]
    fi = next(i for i, ln in enumerate(lines) if ln.startswith("*FREQUENCY"))
    assert lines[fi + 1] == "6"


def test_modal_without_density_warns(tmp_path: Path) -> None:
    """Modal needs *DENSITY for the mass matrix; warn honestly when absent."""
    setup = {
        "materials": {"Steel": {"youngs_modulus_mpa": 210000.0, "poisson_ratio": 0.3}},
        "boundary_conditions": _SETUP_WITH_FEATURE_REFS["boundary_conditions"],
    }
    pkg = _write_package(
        tmp_path / "modal_nodens.aieng",
        setup=setup,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
        solver_settings={"analysis_type": "frequency"},
    )
    result = generate_solver_input_package(pkg, run_id="run_modal_nd")
    assert any("density" in w.lower() for w in result["warnings"])


def test_buckling_step_block_emits_buckle_with_reference_load(tmp_path: Path) -> None:
    """Buckling requires a reference load and emits *BUCKLE + N factors + *CLOAD."""
    pkg = _write_package(
        tmp_path / "buckle.aieng",
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
        solver_settings={"analysis_type": "buckling", "num_factors": 3},
    )
    result = generate_solver_input_package(pkg, run_id="run_buckle")
    assert result["ok"] is True
    deck = _read_deck(pkg, "run_buckle")
    assert "*BUCKLE" in deck
    assert "*CLOAD" in deck  # reference perturbation load
    assert "*STATIC" not in deck
    lines = [ln.strip() for ln in deck.splitlines()]
    bi = next(i for i, ln in enumerate(lines) if ln.startswith("*BUCKLE"))
    assert lines[bi + 1] == "3"


def test_buckling_still_requires_loads(tmp_path: Path) -> None:
    """Unlike modal, buckling without a reference load is rejected as missing."""
    setup = {
        "materials": _SETUP_WITH_FEATURE_REFS["materials"],
        "boundary_conditions": _SETUP_WITH_FEATURE_REFS["boundary_conditions"],
    }
    pkg = _write_package(
        tmp_path / "buckle_noload.aieng",
        setup=setup,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
        solver_settings={"analysis_type": "buckle"},
    )
    with pytest.raises(MissingSetupError) as exc:
        generate_solver_input_package(pkg, run_id="run_buckle_nl")
    assert "loads" in str(exc.value)


# ---------------------------------------------------------------------------
# missing_items rejection paths
# ---------------------------------------------------------------------------


def test_refuses_when_source_deck_missing(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path / "no_mesh.aieng",
        include_source_deck=False,
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    with pytest.raises(MissingSetupError) as exc:
        generate_solver_input_package(pkg, run_id="run_002")
    assert "mesh_source_deck" in str(exc.value)


def test_refuses_when_materials_missing(tmp_path: Path) -> None:
    setup = {
        "boundary_conditions": _SETUP_WITH_FEATURE_REFS["boundary_conditions"],
        "loads": _SETUP_WITH_FEATURE_REFS["loads"],
    }
    pkg = _write_package(
        tmp_path / "no_mat.aieng",
        setup=setup,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    with pytest.raises(MissingSetupError) as exc:
        generate_solver_input_package(pkg, run_id="run_003")
    assert "materials" in str(exc.value)


def test_refuses_when_boundary_conditions_missing(tmp_path: Path) -> None:
    setup = {
        "materials": _SETUP_WITH_FEATURE_REFS["materials"],
        "loads": _SETUP_WITH_FEATURE_REFS["loads"],
    }
    pkg = _write_package(
        tmp_path / "no_bc.aieng",
        setup=setup,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    with pytest.raises(MissingSetupError) as exc:
        generate_solver_input_package(pkg, run_id="run_004")
    assert "boundary_conditions" in str(exc.value)


def test_refuses_when_loads_missing(tmp_path: Path) -> None:
    setup = {
        "materials": _SETUP_WITH_FEATURE_REFS["materials"],
        "boundary_conditions": _SETUP_WITH_FEATURE_REFS["boundary_conditions"],
    }
    pkg = _write_package(
        tmp_path / "no_load.aieng",
        setup=setup,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    with pytest.raises(MissingSetupError) as exc:
        generate_solver_input_package(pkg, run_id="run_005")
    assert "loads" in str(exc.value)


# ---------------------------------------------------------------------------
# Package-level error paths
# ---------------------------------------------------------------------------


def test_missing_package_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        generate_solver_input_package(tmp_path / "does_not_exist.aieng")


def test_non_aieng_suffix_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "wrong.zip"
    bad.write_bytes(b"")
    with pytest.raises(ValueError, match=r"\.aieng"):
        generate_solver_input_package(bad)


def test_existing_deck_requires_overwrite(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path / "again.aieng",
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    generate_solver_input_package(pkg, run_id="run_006")
    with pytest.raises(FileExistsError):
        generate_solver_input_package(pkg, run_id="run_006")
    # overwrite=True must succeed without raising
    result = generate_solver_input_package(pkg, run_id="run_006", overwrite=True)
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Honesty boundary — no convergence claim is emitted
# ---------------------------------------------------------------------------


def test_generated_deck_does_not_claim_convergence(tmp_path: Path) -> None:
    """The deck generator must never declare convergence or solver evidence.

    Convergence stays ``null`` until external solver evidence is imported.
    See issue #54 honesty boundaries.
    """
    pkg = _write_package(
        tmp_path / "honesty.aieng",
        setup=_SETUP_WITH_FEATURE_REFS,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    generate_solver_input_package(pkg, run_id="run_honesty")
    out_path = SOLVER_INPUT_PATH_TEMPLATE.format(run_id="run_honesty")
    with zipfile.ZipFile(pkg) as zf:
        deck = zf.read(out_path).decode("utf-8")
        # Generator must not touch result/convergence resources at all.
        for forbidden in (
            "results/solver_evidence",
            "validation/status.yaml",
            "results/claim_map.json",
            "results/evidence_index.json",
        ):
            assert forbidden not in zf.namelist(), (
                f"deck_generator must not synthesize {forbidden}; only an external solver may."
            )

    lowered = deck.lower()
    for forbidden_phrase in ("converged: true", "convergence: passed", "solver run completed"):
        assert forbidden_phrase not in lowered


def test_setup_material_renamed_to_match_solid_section(tmp_path: Path) -> None:
    """When setup material name differs from *SOLID SECTION, generator aligns
    the deck to keep it runnable and records a warning."""
    setup = {
        "materials": {
            "Aluminum_6061": {  # differs from "Steel" in source deck *SOLID SECTION
                "youngs_modulus_mpa": 69000.0,
                "poisson_ratio": 0.33,
                "density_kg_m3": 2700.0,
            },
        },
        "boundary_conditions": _SETUP_WITH_FEATURE_REFS["boundary_conditions"],
        "loads": _SETUP_WITH_FEATURE_REFS["loads"],
    }
    pkg = _write_package(
        tmp_path / "rename.aieng",
        setup=setup,
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
    )
    result = generate_solver_input_package(pkg, run_id="run_rename")
    assert any("Renaming setup material" in w for w in result["warnings"])
    out_path = SOLVER_INPUT_PATH_TEMPLATE.format(run_id="run_rename")
    with zipfile.ZipFile(pkg) as zf:
        deck = zf.read(out_path).decode("utf-8")
    assert "*MATERIAL, NAME=STEEL" in deck


# ---------------------------------------------------------------------------
# Steady-state thermal (#371)
# ---------------------------------------------------------------------------

def test_thermal_aliases_normalize() -> None:
    for raw in ("thermal", "heat_transfer", "heat transfer", "steady_state_thermal", "conduction"):
        assert normalize_analysis_type({"analysis_type": raw}) == "thermal"


def test_thermal_step_block_emits_heat_transfer_no_load_required(tmp_path: Path) -> None:
    """A steady-state thermal analysis needs no structural load: *HEAT TRANSFER +
    *CONDUCTIVITY + temperature *BOUNDARY (DOF 11) + NT output, no *STATIC/*CLOAD."""
    pkg = _write_package(
        tmp_path / "thermal.aieng",
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
        parsed_materials={"materials": [{"name": "Steel", "conductivity": 50.0}]},
        parsed_bcs={"boundary_conditions": [
            {"id": "hot", "target": "N_FIX", "dof_start": 11, "dof_end": 11, "value": 100.0},
            {"id": "cold", "target": "N_LOAD", "dof_start": 11, "dof_end": 11, "value": 0.0},
        ]},
        # deliberately NO loads
        solver_settings={"analysis_type": "thermal"},
    )
    result = generate_solver_input_package(pkg, run_id="run_thermal")
    assert result["ok"] is True  # no "missing loads" — thermal is load-optional
    deck = _read_deck(pkg, "run_thermal")
    assert "*HEAT TRANSFER, STEADY STATE" in deck
    assert "*CONDUCTIVITY" in deck
    assert "*STATIC" not in deck
    assert "*CLOAD" not in deck
    # nodal-temperature output requested, and the temperature BCs emitted on DOF 11
    assert "NT" in deck
    assert "N_FIX, 11, 11, 100.0" in deck


def test_thermal_with_no_material_conductivity_still_generates(tmp_path: Path) -> None:
    """Absent conductivity must not crash deck generation (solver will report it);
    the deck simply omits *CONDUCTIVITY."""
    pkg = _write_package(
        tmp_path / "thermal_nocond.aieng",
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
        parsed_materials={"materials": [{"name": "Steel"}]},
        parsed_bcs={"boundary_conditions": [
            {"id": "hot", "target": "N_FIX", "dof_start": 11, "dof_end": 11, "value": 100.0},
        ]},
        solver_settings={"analysis_type": "thermal"},
    )
    result = generate_solver_input_package(pkg, run_id="run_thermal_nc")
    assert result["ok"] is True
    deck = _read_deck(pkg, "run_thermal_nc")
    assert "*HEAT TRANSFER, STEADY STATE" in deck
    assert "*CONDUCTIVITY" not in deck


# ---------------------------------------------------------------------------
# Thermal-structural coupling (#371)
# ---------------------------------------------------------------------------

def test_thermal_structural_aliases_normalize() -> None:
    for raw in ("thermal_structural", "thermal_stress", "thermomechanical",
                "uncoupled_temperature_displacement"):
        assert normalize_analysis_type({"analysis_type": raw}) == "thermal_structural"


def test_thermal_structural_emits_uncoupled_step_expansion_initial_temp(tmp_path: Path) -> None:
    """Thermal-structural: *UNCOUPLED TEMPERATURE-DISPLACEMENT + *EXPANSION +
    *INITIAL CONDITIONS (ref temp) + NT/U/S output, load-optional, no *STATIC."""
    pkg = _write_package(
        tmp_path / "ts.aieng",
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
        parsed_materials={"materials": [{
            "name": "Steel",
            "elastic": {"youngs_modulus": 210000.0, "poisson_ratio": 0.3},
            "conductivity": 50.0,
            "expansion": 1.2e-5,
        }]},
        parsed_bcs={"boundary_conditions": [
            {"id": "fix", "target": "N_FIX", "dof_start": 1, "dof_end": 3, "value": 0.0},
            {"id": "hot", "target": "N_FIX", "dof_start": 11, "dof_end": 11, "value": 100.0},
            {"id": "cold", "target": "N_LOAD", "dof_start": 11, "dof_end": 11, "value": 0.0},
        ]},
        # deliberately NO loads (thermal field drives it)
        solver_settings={"analysis_type": "thermal_structural", "reference_temperature": 20.0},
    )
    result = generate_solver_input_package(pkg, run_id="run_ts")
    assert result["ok"] is True
    deck = _read_deck(pkg, "run_ts")
    assert "*UNCOUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE" in deck
    assert "*EXPANSION, ZERO=20" in deck
    assert "*INITIAL CONDITIONS, TYPE=TEMPERATURE" in deck
    assert "*STATIC" not in deck
    # outputs temperature + displacement + stress
    assert "NT, U" in deck
    assert "*EL FILE" in deck


def test_thermal_structural_without_expansion_warns(tmp_path: Path) -> None:
    """No *EXPANSION coefficient -> honest warning (displacement would be zero)."""
    pkg = _write_package(
        tmp_path / "ts_noexp.aieng",
        cae_mapping=_CAE_MAPPING_WITH_FEATURES,
        parsed_materials={"materials": [{
            "name": "Steel",
            "elastic": {"youngs_modulus": 210000.0, "poisson_ratio": 0.3},
            "conductivity": 50.0,
        }]},
        parsed_bcs={"boundary_conditions": [
            {"id": "fix", "target": "N_FIX", "dof_start": 1, "dof_end": 3, "value": 0.0},
            {"id": "hot", "target": "N_FIX", "dof_start": 11, "dof_end": 11, "value": 100.0},
        ]},
        solver_settings={"analysis_type": "thermal_structural"},
    )
    result = generate_solver_input_package(pkg, run_id="run_ts_ne")
    assert result["ok"] is True
    assert any("expansion" in w.lower() for w in result["warnings"])
