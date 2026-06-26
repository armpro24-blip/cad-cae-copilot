"""Generate a runnable CalculiX solver input deck from existing .aieng setup artifacts.

Phase 33 — Honest deck generation from CAE setup.

This module assembles a runnable `.inp` deck by:
1. Extracting the mesh section (and existing BC/load mappings) from a previously
   imported source solver deck (`simulation/cae_imports/source_solver_deck.inp`).
2. Generating *MATERIAL cards from current setup artifacts.
3. Generating *STEP / *STATIC / output-request cards.

It does NOT generate mesh nodes or elements. If no source deck with mesh is
present, generation refuses with an explicit `missing_items` list.

## Why hand-assembled keyword cards instead of `pyccx`?

A reasonable off-the-shelf option for this layer is
[`pyccx`](https://github.com/drlukeparry/pyccx) (BSD-2, v0.2.0 2025-08),
which exposes Python wrappers around CalculiX cards. We chose to hand-
assemble the cards directly here for three reasons that are load-bearing
for the Phase 33 honesty boundary:

1. **No solver execution.** `pyccx`'s purpose is to drive CalculiX —
   build a model and run `ccx`. Phase 33 explicitly forbids solver
   execution: AIENG generates the deck, an external runtime (e.g.
   `aieng-ui` or a separate orchestrator) runs the solver and writes
   back evidence. Importing `pyccx` would pull in execution machinery
   we are contractually not allowed to call from this module.
2. **Explicit refusal surface.** The `missing_items` path
   (`missing_items=["materials", ...]`) is the contract surface for
   "setup is incomplete". A library that builds a model object before
   you can serialize it makes the *which-piece-is-missing* signal
   harder to surface honestly. The hand-written `_resolve_*` paths
   keep the rejection reasons explicit.
3. **Minimal runtime dependency.** `pyccx` would become a required
   dependency of the simulation extra; today the runtime needs only
   `pyyaml`. Phase 33 is intentionally a *linear-static-only*
   generator; the surface is small enough that the
   maintenance/legibility trade favors keeping it in-tree.

If a future phase needs nonlinear/modal/transient cards or
`pyccx`-style abstractions for re-export, we should add it as an
optional `[solver]` extra in `pyproject.toml` (mirroring `[geometry]`
for CadQuery) rather than pulling it into the core runtime. Re-visit
this decision when Phase 36 closed-loop benchmark shows a concrete
need for richer deck features that the hand-assembled path cannot
honestly express.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from aieng import FORMAT_VERSION

SOLVER_INPUT_PATH_TEMPLATE = "simulation/runs/{run_id}/solver_input.inp"
SOURCE_DECK_PATH = "simulation/cae_imports/source_solver_deck.inp"
SETUP_PATH = "simulation/setup.yaml"
PARSED_MATERIALS_PATH = "simulation/cae_imports/parsed_materials.json"
PARSED_BCS_PATH = "simulation/cae_imports/parsed_boundary_conditions.json"
PARSED_LOADS_PATH = "simulation/cae_imports/parsed_loads.json"
SOLVER_SETTINGS_PATH = "simulation/solver_settings.json"
CAE_MAPPING_PATH = "simulation/cae_mapping.json"

# Keywords that belong to the mesh / structural section of a CalculiX deck.
# Stored as the first token (before any space or comma) for robust matching.
_MESH_KEYWORDS = frozenset(
    {
        "*NODE",
        "*ELEMENT",
        "*NSET",
        "*ELSET",
        "*SOLID",       # *SOLID SECTION
        "*SHELL",       # *SHELL SECTION
        "*BEAM",        # *BEAM SECTION
        "*SURFACE",
    }
)

# Keywords that terminate a data block.
_CONTROL_KEYWORDS = frozenset(
    {
        "*MATERIAL",
        "*ELASTIC",
        "*DENSITY",
        "*BOUNDARY",
        "*STEP",
        "*STATIC",
        "*CLOAD",
        "*DLOAD",
        "*NODE FILE",
        "*EL FILE",
        "*END STEP",
    }
)


class DeckGenerationError(Exception):
    """Raised when deck generation cannot proceed due to missing or invalid data."""


class MissingSetupError(DeckGenerationError):
    """Raised when required setup artifacts are absent."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_solver_input_package(
    package_path: str | Path,
    *,
    run_id: str = "run_001",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Generate a runnable CalculiX `.inp` inside a `.aieng` package.

    Args:
        package_path: Path to the `.aieng` package.
        run_id: Solver run identifier (used in output path).
        overwrite: Whether to overwrite an existing solver input deck.

    Returns:
        Dict with ``ok``, ``out_path``, ``missing_items``, ``warnings``.

    Raises:
        MissingSetupError: If required artifacts are missing.
        FileNotFoundError: If the package does not exist.
        ValueError: If the package is not a valid `.aieng` archive.
        FileExistsError: If the output deck already exists and overwrite=False.
    """
    package_file = Path(package_path)
    if not package_file.exists():
        raise FileNotFoundError(f"package does not exist: {package_file}")
    if package_file.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")

    out_path_in_zip = SOLVER_INPUT_PATH_TEMPLATE.format(run_id=run_id)

    try:
        with zipfile.ZipFile(package_file, mode="r") as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names:
                raise ValueError("package is missing manifest.json")

            if out_path_in_zip in names and not overwrite:
                raise FileExistsError(
                    f"{out_path_in_zip} already exists; use --overwrite to replace it"
                )

            manifest = json.loads(zf.read("manifest.json"))
            setup = _read_optional_yaml(zf, SETUP_PATH)
            parsed_materials = _read_optional_json(zf, PARSED_MATERIALS_PATH)
            parsed_bcs = _read_optional_json(zf, PARSED_BCS_PATH)
            parsed_loads = _read_optional_json(zf, PARSED_LOADS_PATH)
            solver_settings = _read_optional_json(zf, SOLVER_SETTINGS_PATH)
            cae_mapping = _read_optional_json(zf, CAE_MAPPING_PATH)
            source_deck_text = (
                zf.read(SOURCE_DECK_PATH).decode("utf-8", errors="replace")
                if SOURCE_DECK_PATH in names
                else None
            )
            members = _read_existing_members(zf, out_path_in_zip)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"package is not a valid zip archive: {package_file}") from exc

    # --- readiness check -----------------------------------------------------
    missing: list[str] = []
    warnings: list[str] = []

    analysis_type = normalize_analysis_type(solver_settings, setup)

    if source_deck_text is None:
        missing.append("mesh_source_deck")
        warnings.append(
            "No source solver deck found. Import a complete CalculiX `.inp" "` containing mesh "
            "via `aieng import-cae-deck` before generating a solver input."
        )

    materials = _resolve_materials(setup, parsed_materials)
    if not materials:
        missing.append("materials")

    bcs = _resolve_boundary_conditions(setup, parsed_bcs, cae_mapping, warnings)
    if not bcs:
        missing.append("boundary_conditions")

    loads = _resolve_loads(setup, parsed_loads, cae_mapping, warnings)
    # Loads are required for static and buckling (the reference perturbation
    # load), but a modal (`*FREQUENCY`) analysis solves for natural frequencies
    # of the unloaded structure, and a steady-state `thermal` analysis is driven
    # by its temperature boundary conditions (a heat flux load is optional) — so
    # neither requires a load (a thermal-structural run is likewise driven by its
    # temperature field).
    if not loads and analysis_type not in ("modal", "thermal", "thermal_structural"):
        missing.append("loads")

    if missing:
        raise MissingSetupError(
            f"Cannot generate solver input: missing required setup: {', '.join(missing)}"
        )

    # --- deck assembly -------------------------------------------------------
    deck_text = _assemble_deck(
        manifest=manifest,
        run_id=run_id,
        source_deck_text=source_deck_text,
        materials=materials,
        boundary_conditions=bcs,
        loads=loads,
        solver_settings=solver_settings,
        analysis_type=analysis_type,
        warnings=warnings,
    )

    # --- write back into package ---------------------------------------------
    _rewrite_package(package_file, members, manifest, out_path_in_zip, deck_text)

    return {
        "ok": True,
        "out_path": out_path_in_zip,
        "missing_items": [],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Setup artifact resolution
# ---------------------------------------------------------------------------


def _resolve_materials(
    setup: dict[str, Any] | None,
    parsed_materials: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return a list of material dicts, preferring setup.yaml over parsed JSON."""
    if isinstance(setup, dict):
        setup_mats = setup.get("materials")
        if isinstance(setup_mats, dict) and setup_mats:
            result: list[dict[str, Any]] = []
            for name, props in setup_mats.items():
                if not isinstance(props, dict):
                    continue
                entry: dict[str, Any] = {"name": name}
                e = props.get("youngs_modulus_mpa")
                nu = props.get("poisson_ratio")
                if e is not None and nu is not None:
                    entry["elastic"] = {"youngs_modulus": e, "poisson_ratio": nu}
                rho = props.get("density_kg_m3")
                if rho is not None:
                    entry["density"] = _density_kg_m3_to_tonne_mm3(rho)
                k = (
                    props.get("thermal_conductivity_w_mk")
                    or props.get("conductivity")
                    or props.get("thermal_conductivity")
                )
                if k is not None:
                    entry["conductivity"] = k
                a = (
                    props.get("thermal_expansion_per_k")
                    or props.get("expansion")
                    or props.get("thermal_expansion")
                )
                if a is not None:
                    entry["expansion"] = a
                result.append(entry)
            return result

    if isinstance(parsed_materials, dict):
        mats = parsed_materials.get("materials")
        if isinstance(mats, list):
            return [m for m in mats if isinstance(m, dict)]

    return []


def _density_kg_m3_to_tonne_mm3(value: Any) -> float:
    """Convert SI density to the mm-N-MPa-tonne system used by CalculiX decks."""
    return float(f"{float(value) * 1e-12:.8g}")


def _resolve_boundary_conditions(
    setup: dict[str, Any] | None,
    parsed_bcs: dict[str, Any] | None,
    cae_mapping: dict[str, Any] | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Return BC dicts. Prefers setup.yaml when feature-to-nset mapping exists."""
    if isinstance(setup, dict):
        setup_bcs = setup.get("boundary_conditions")
        if isinstance(setup_bcs, list) and setup_bcs:
            mapped_bcs: list[dict[str, Any]] = []
            for bc in setup_bcs:
                if not isinstance(bc, dict):
                    continue
                target_feature = bc.get("target_feature")
                nset = _feature_to_nset(target_feature, cae_mapping)
                if nset is None:
                    warnings.append(
                        f"BC '{bc.get('id', 'unknown')}' target_feature '{target_feature}' "
                        f"has no CAE mapping; falling back to parsed boundary conditions."
                    )
                    continue
                mapped_bcs.append(
                    {
                        "id": bc.get("id", "unknown"),
                        "target": nset,
                        "dof_start": _bc_dof_start(bc.get("type", "fixed")),
                        "dof_end": 3,
                        "value": 0.0,
                    }
                )
            if mapped_bcs:
                return mapped_bcs

    if isinstance(parsed_bcs, dict):
        bcs = parsed_bcs.get("boundary_conditions")
        if isinstance(bcs, list):
            return [b for b in bcs if isinstance(b, dict)]

    return []


def _resolve_loads(
    setup: dict[str, Any] | None,
    parsed_loads: dict[str, Any] | None,
    cae_mapping: dict[str, Any] | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Return load dicts. Prefers setup.yaml when feature-to-nset mapping exists."""
    if isinstance(setup, dict):
        setup_loads = setup.get("loads")
        if isinstance(setup_loads, list) and setup_loads:
            mapped_loads: list[dict[str, Any]] = []
            for load in setup_loads:
                if not isinstance(load, dict):
                    continue
                target_feature = load.get("target_feature")
                nset = _feature_to_nset(target_feature, cae_mapping)
                if nset is None:
                    warnings.append(
                        f"Load '{load.get('id', 'unknown')}' target_feature '{target_feature}' "
                        f"has no CAE mapping; falling back to parsed loads."
                    )
                    continue
                direction = load.get("direction", [0, -1, 0])
                value = float(load.get("value_n", 0.0) or 0.0)
                components = _load_components(direction, value)
                for dof, component in components:
                    mapped_loads.append(
                        {
                            "id": f"{load.get('id', 'unknown')}_dof{dof}",
                            "target": nset,
                            "dof": dof,
                            "value": component,
                        }
                    )
            if mapped_loads:
                return mapped_loads

    if isinstance(parsed_loads, dict):
        loads = parsed_loads.get("loads")
        if isinstance(loads, list):
            return [l for l in loads if isinstance(l, dict)]

    return []


def _feature_to_nset(
    feature_id: str | None,
    cae_mapping: dict[str, Any] | None,
) -> str | None:
    """Look up a feature_id in cae_mapping and return the corresponding CAE entity (nset)."""
    if not feature_id or not isinstance(cae_mapping, dict):
        return None
    for mapping in cae_mapping.get("mappings", []):
        if not isinstance(mapping, dict):
            continue
        maps_to = mapping.get("maps_to")
        if not isinstance(maps_to, dict):
            continue
        if maps_to.get("feature_id") == feature_id:
            return mapping.get("cae_entity")
    return None


def _bc_dof_start(bc_type: str) -> int:
    """Map BC type to starting DOF for CalculiX *BOUNDARY."""
    t = str(bc_type).lower().strip()
    if t in {"fixed", "clamp", "encastre"}:
        return 1
    if t in {"pinned", "hinge"}:
        return 1
    if t == "rollers":
        return 2
    return 1


def _primary_dof_from_direction(direction: Any) -> int:
    """Return the dominant DOF (1=x, 2=y, 3=z) from a direction vector."""
    if isinstance(direction, list) and len(direction) >= 3:
        abs_vals = [abs(direction[0]), abs(direction[1]), abs(direction[2])]
        return int(abs_vals.index(max(abs_vals))) + 1
    return 2  # default to y


def _load_components(direction: Any, magnitude: float) -> list[tuple[int, float]]:
    """Return signed load components for CalculiX DOFs 1..3.

    Setup loads express ``value_n`` as a total force magnitude plus a direction
    vector. Preserve the sign and every non-zero component; dropping everything
    except the dominant axis would silently rotate diagonal or negative loads.
    """
    if isinstance(direction, list) and len(direction) >= 3:
        components: list[tuple[int, float]] = []
        for dof, raw in enumerate(direction[:3], start=1):
            try:
                component = float(raw)
            except (TypeError, ValueError):
                component = 0.0
            if abs(component) > 1e-9:
                components.append((dof, magnitude * component))
        if components:
            return components

    dof = _primary_dof_from_direction(direction)
    return [(dof, magnitude)]


# ---------------------------------------------------------------------------
# Deck assembly
# ---------------------------------------------------------------------------


def _count_nset_nodes(source_deck_text: str) -> dict[str, int]:
    """Count the nodes in each ``*NSET`` defined in the (source) deck.

    A concentrated ``*CLOAD`` applied to an NSET is applied by CalculiX to EVERY
    node in that set, so to represent a *total* force on a face the per-node value
    must be ``total / n_nodes``. These counts let the step builder do that
    division. Tolerant of comma- or space-attached headers (``*NSET, NSET=X`` and
    ``*NSET,NSET=X``).
    """
    counts: dict[str, int] = {}
    current: str | None = None
    for raw in source_deck_text.splitlines():
        stripped = raw.strip()
        upper = stripped.upper()
        if upper.startswith("*"):
            current = None
            if upper.split(",")[0].split()[0] == "*NSET":
                for token in stripped.replace(" ", "").split(","):
                    if token.upper().startswith("NSET="):
                        current = token.split("=", 1)[1]
                        counts.setdefault(current, 0)
            continue
        if current and stripped and not stripped.startswith("**"):
            counts[current] += sum(1 for p in stripped.split(",") if p.strip())
    return counts


def _extract_solid_section_materials(source_deck_text: str) -> set[str]:
    """Extract material names referenced by *SOLID SECTION in the source deck."""
    materials: set[str] = set()
    for line in source_deck_text.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("*SOLID SECTION"):
            for part in stripped.split(","):
                p = part.strip()
                if p.startswith("MATERIAL="):
                    materials.add(p.split("=", 1)[1].strip())
    return materials


def _fmt_card_number(value: Any) -> str:
    """Format a numeric value for a CalculiX card field.

    CalculiX reads card fields with a fixed maximum width; an 18-digit float repr
    (e.g. ``2.6999999999999998e-09`` produced by a unit conversion) overflows it
    and aborts with ``*ERROR reading ...``. Clamp numbers to 8 significant figures
    (ample engineering precision). Non-numeric values pass through as ``str``.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.8g}"
    return str(value)


def _assemble_deck(
    manifest: dict[str, Any],
    run_id: str,
    source_deck_text: str | None,
    materials: list[dict[str, Any]],
    boundary_conditions: list[dict[str, Any]],
    loads: list[dict[str, Any]],
    solver_settings: dict[str, Any] | None,
    warnings: list[str],
    analysis_type: str = "static",
) -> str:
    """Assemble the complete CalculiX deck text."""
    model_id = manifest.get("model_id", "unknown_model")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # Strain-free reference temperature for thermal expansion (thermal-structural).
    _ss = solver_settings if isinstance(solver_settings, dict) else {}
    try:
        _ref_temp = float(_ss.get("reference_temperature", _ss.get("ref_temperature", 0.0)) or 0.0)
    except (TypeError, ValueError):
        _ref_temp = 0.0

    lines: list[str] = []

    # ---- Header ----
    lines += [
        "*HEADING",
        f"AIENG generated solver input - model_id={model_id} run_id={run_id}",
        f"Generated at: {now}",
        "**",
        "** This deck was assembled by aieng.simulation.deck_generator.",
        "** Mesh and element/node sets are preserved from the source solver deck.",
        "** Material definitions reflect the current simulation/setup.yaml state.",
        "**",
    ]

    # ---- Mesh section (preserved from source deck) ----
    mesh_section = _extract_mesh_section(source_deck_text) if source_deck_text else None
    if mesh_section:
        lines.append("** --- Mesh section (from source_solver_deck.inp) ---")
        lines.append(mesh_section)
        lines.append("")
    else:
        # Should not reach here because readiness check catches missing mesh,
        # but keep defensive.
        lines.append("** ERROR: No mesh section available.")

    # ---- Material name reconciliation ----
    # If the source deck's *SOLID SECTION references a material name that does
    # not match setup.yaml, align the setup material name so the deck stays
    # runnable.  This is the honest path: generator does not edit mesh-to-
    # material mapping, only the material property values.
    solid_mats = _extract_solid_section_materials(source_deck_text or "")
    if len(materials) == 1 and len(solid_mats) == 1:
        setup_name = materials[0].get("name", "")
        solid_name = list(solid_mats)[0]
        if setup_name != solid_name:
            warnings.append(
                f"Renaming setup material '{setup_name}' to '{solid_name}' "
                f"so it matches the *SOLID SECTION reference in the source deck."
            )
            materials[0]["name"] = solid_name
    elif solid_mats:
        setup_names = {m.get("name", "") for m in materials}
        for sm in solid_mats:
            if sm not in setup_names:
                warnings.append(
                    f"*SOLID SECTION references material '{sm}' which is not defined "
                    f"in simulation/setup.yaml. The solver may fail."
                )

    # ---- Material definitions ----
    if materials:
        lines.append("** --- Material definitions ---")
        for mat in materials:
            name = mat.get("name", "unnamed")
            elastic = mat.get("elastic")
            density = mat.get("density")
            conductivity = mat.get("conductivity")
            expansion = mat.get("expansion")
            lines.append(f"*MATERIAL, NAME={name}")
            if isinstance(elastic, dict):
                e = _fmt_card_number(elastic.get("youngs_modulus", ""))
                nu = _fmt_card_number(elastic.get("poisson_ratio", ""))
                lines += ["*ELASTIC", f"{e}, {nu}"]
            if density is not None:
                lines += ["*DENSITY", f"{_fmt_card_number(density)}"]
            # Thermal conductivity (*CONDUCTIVITY) — required for a heat-transfer
            # analysis; harmless extra material data for a structural one.
            if conductivity is not None:
                lines += ["*CONDUCTIVITY", f"{_fmt_card_number(conductivity)}"]
            # Thermal expansion (*EXPANSION) — drives thermal stress in a
            # thermal-structural run. ZERO sets the strain-free reference temperature.
            if expansion is not None:
                lines += [
                    f"*EXPANSION, ZERO={_fmt_card_number(_ref_temp)}",
                    f"{_fmt_card_number(expansion)}",
                ]
            lines.append("")
    else:
        lines.append("** WARNING: No material definitions available.")

    # ---- Boundary conditions ----
    if boundary_conditions:
        lines.append("** --- Boundary conditions ---")
        lines.append("*BOUNDARY")
        for bc in boundary_conditions:
            target = bc.get("target", "unknown")
            dof_start = bc.get("dof_start", 1)
            dof_end = bc.get("dof_end", 3)
            value = bc.get("value", 0.0)
            lines.append(f"{target}, {dof_start}, {dof_end}, {value}")
        lines.append("")

    # ---- Initial temperature (thermal-structural) ----
    # *EXPANSION strain is computed relative to this strain-free reference; set
    # every node to the reference temperature so the *UNCOUPLED step's computed
    # temperature field produces the correct (T - T_ref) thermal strain. Uses the
    # NALL node set emitted by the source-deck builder.
    if analysis_type == "thermal_structural":
        lines.append("** --- Initial temperature (strain-free reference) ---")
        lines += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"NALL, {_fmt_card_number(_ref_temp)}"]
        lines.append("")
        if not any(m.get("expansion") is not None for m in materials):
            warnings.append(
                "Thermal-structural analysis needs a material *EXPANSION coefficient "
                "to produce thermal stress; none was found — the displacement field "
                "will be zero."
            )

    # A modal (*FREQUENCY) analysis needs *DENSITY to form the mass matrix.
    if analysis_type == "modal" and not any(
        m.get("density") is not None for m in materials
    ):
        warnings.append(
            "Modal analysis requires material density (*DENSITY) to build the mass "
            "matrix; none was found — eigenfrequencies cannot be computed."
        )

    # ---- Step (analysis-type-specific; CalculiX *CLOAD is step-dependent, so
    #      loads are emitted inside the step here, not before it) ----
    lines.append(f"** --- Step ({analysis_type}) ---")
    nset_counts = _count_nset_nodes(source_deck_text or "")
    lines += _step_block(analysis_type, solver_settings, loads, warnings, nset_counts)

    return "\n".join(lines) + "\n"


def _extract_mesh_section(source_deck_text: str) -> str:
    """Extract mesh-related lines from a CalculiX deck.

    Preserves *NODE, *ELEMENT, *NSET, *ELSET, *SOLID SECTION, *SURFACE,
    and their data lines.  Stops at non-mesh keywords (e.g. *MATERIAL,
    *BOUNDARY, *STEP).
    """

    def _is_mesh_keyword(line: str) -> bool:
        upper = line.upper().strip()
        tokens = upper.split()
        if not tokens:
            return False
        # The keyword may be comma-attached to its first parameter — gmsh emits
        # `*ELSET,ELSET=EALL` with no space — so strip the parameter off the
        # first whitespace token to recover the bare keyword. The space-after-
        # comma form (`*SOLID SECTION, ELSET=EALL`) already tokenized cleanly;
        # the comma-attached form did not, silently dropping the
        # `*ELSET,ELSET=EALL` definition and leaving EALL undefined for ccx.
        head = tokens[0].split(",")[0]

        # *NODE by itself (or with NSET=...) is a mesh keyword.
        # *NODE FILE, *NODE PRINT, *NODE OUTPUT are output requests — not mesh.
        if head == "*NODE":
            second = tokens[1].split(",")[0] if len(tokens) > 1 else ""
            return second not in {"FILE", "PRINT", "OUTPUT"}

        return head in _MESH_KEYWORDS

    lines = source_deck_text.splitlines()
    mesh_lines: list[str] = []
    in_mesh_block = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("**"):
            if in_mesh_block:
                mesh_lines.append(line)
            continue

        if stripped.startswith("*"):
            if _is_mesh_keyword(stripped):
                in_mesh_block = True
                mesh_lines.append(line)
            else:
                in_mesh_block = False
            continue

        if in_mesh_block:
            mesh_lines.append(line)

    return "\n".join(mesh_lines)


def _resolve_step_name(solver_settings: dict[str, Any] | None) -> str:
    """Derive a step name from solver settings, if available."""
    if isinstance(solver_settings, dict):
        name = solver_settings.get("step_name") or solver_settings.get("analysis_type")
        if isinstance(name, str) and name:
            return name.replace(" ", "_")
    return "LoadStep"


# Supported CalculiX analysis types. Modal/buckling are linear eigenvalue
# analyses CalculiX solves natively (`*FREQUENCY` / `*BUCKLE`); static is the
# default. Aliases map common spellings to the canonical key.
_ANALYSIS_TYPE_ALIASES: dict[str, str] = {
    "": "static",
    "static": "static",
    "linear_static": "static",
    "modal": "modal",
    "frequency": "modal",
    "eigenfrequency": "modal",
    "eigen": "modal",
    "natural_frequency": "modal",
    "buckling": "buckling",
    "buckle": "buckling",
    "linear_buckling": "buckling",
    "thermal": "thermal",
    "heat_transfer": "thermal",
    "heat": "thermal",
    "steady_state_thermal": "thermal",
    "thermal_steady_state": "thermal",
    "conduction": "thermal",
    "thermal_structural": "thermal_structural",
    "thermal_stress": "thermal_structural",
    "thermomechanical": "thermal_structural",
    "coupled_temperature_displacement": "thermal_structural",
    "uncoupled_temperature_displacement": "thermal_structural",
    "thermal_expansion": "thermal_structural",
}


def normalize_analysis_type(
    solver_settings: dict[str, Any] | None, setup: dict[str, Any] | None = None
) -> str:
    """Return the canonical analysis type — ``static`` / ``modal`` / ``buckling``.

    Reads ``analysis_type`` (or ``step_type``) from ``solver_settings`` first, then
    ``setup``; unknown / absent values fall back to ``static``.
    """
    raw: Any = None
    if isinstance(solver_settings, dict):
        raw = solver_settings.get("analysis_type") or solver_settings.get("step_type")
    if not raw and isinstance(setup, dict):
        raw = setup.get("analysis_type")
    key = str(raw or "static").strip().lower().replace("-", "_").replace(" ", "_")
    return _ANALYSIS_TYPE_ALIASES.get(key, "static")


def _step_block(
    analysis_type: str,
    solver_settings: dict[str, Any] | None,
    loads: list[dict[str, Any]],
    warnings: list[str],
    nset_counts: dict[str, int] | None = None,
) -> list[str]:
    """Build the analysis-type-specific *STEP block.

    CalculiX `*CLOAD` is step-dependent, so loads are emitted INSIDE the step
    (homogeneous `*BOUNDARY` stays in the model definition before the step).
    - static: `*STATIC` + loads + U/S output.
    - modal:  `*FREQUENCY` + N eigenvalues + mode-shape (U) output; loads ignored.
    - buckling: `*BUCKLE` + N factors + the reference load + U output.

    A load's ``value`` is the intended TOTAL force on its target node set. Because
    a ``*CLOAD`` on an NSET is applied by CalculiX to every node in the set, the
    emitted per-node value is ``total / n_nodes`` (``nset_counts``). When the node
    count is unknown the total is emitted unchanged and a warning is recorded, so
    the load is never silently dropped.
    """
    ss = solver_settings if isinstance(solver_settings, dict) else {}
    step_name = _resolve_step_name(solver_settings)
    counts = nset_counts or {}

    def _cload_lines() -> list[str]:
        out = ["*CLOAD"]
        for load in loads:
            target = load.get("target", "unknown")
            total = float(load.get("value", 0.0) or 0.0)
            n_nodes = counts.get(target, 0)
            if n_nodes > 0:
                per_node = total / n_nodes
            else:
                per_node = total
                warnings.append(
                    f"Load on '{target}': node count unknown, applying the value "
                    f"per node (CalculiX *CLOAD semantics) instead of distributing "
                    f"a total force — verify the resulting magnitude."
                )
            out.append(f"{target}, {load.get('dof', 2)}, {per_node:.6f}")
        return out

    def _int_setting(*keys: str, default: int, lo: int, hi: int) -> int:
        for k in keys:
            if k in ss:
                try:
                    return max(lo, min(int(ss[k]), hi))
                except (TypeError, ValueError):
                    break
        return default

    if analysis_type == "modal":
        n = _int_setting("num_modes", "num_eigenvalues", default=10, lo=1, hi=100)
        return [
            f"*STEP, NAME={step_name}",
            "*FREQUENCY, STORAGE=YES",
            f"{n}",
            "*NODE FILE",
            "U",
            "*END STEP",
        ]

    if analysis_type == "buckling":
        n = _int_setting("num_factors", "num_modes", default=5, lo=1, hi=50)
        if not loads:
            warnings.append(
                "Buckling analysis has no reference load (*CLOAD); buckling factors "
                "are meaningless without one — add a reference load."
            )
        block = [f"*STEP, NAME={step_name}", "*BUCKLE", f"{n}"]
        if loads:
            block += _cload_lines()
        block += ["*NODE FILE", "U", "*END STEP"]
        return block

    if analysis_type == "thermal":
        # Steady-state heat conduction. Temperature boundary conditions
        # (`*BOUNDARY` on DOF 11) live in the model definition before the step and
        # drive the field; a concentrated heat flux (`*CFLUX` on DOF 11) is an
        # optional in-step driver. NT = nodal temperature output.
        block = [f"*STEP, NAME={step_name}", "*HEAT TRANSFER, STEADY STATE"]
        if loads:
            block.append("*CFLUX")
            for load in loads:
                target = load.get("target", "unknown")
                total = float(load.get("value", 0.0) or 0.0)
                n_nodes = counts.get(target, 0)
                if n_nodes > 0:
                    per_node = total / n_nodes
                else:
                    per_node = total
                    if total:
                        warnings.append(
                            f"Heat flux on '{target}': node count unknown, applying "
                            f"the value per node (*CFLUX semantics) instead of "
                            f"distributing a total — verify the magnitude."
                        )
                block.append(f"{target}, 11, {per_node:.6f}")
        block += ["*NODE FILE", "NT", "*END STEP"]
        return block

    if analysis_type == "thermal_structural":
        # Sequential thermal stress in one *UNCOUPLED step: solve the temperature
        # field, then the displacement field it induces. Temperature BCs
        # (*BOUNDARY DOF 11) and structural BCs (*BOUNDARY DOF 1-3) both live in
        # the model definition; material *EXPANSION + the reference *INITIAL
        # CONDITIONS turn (T - T_ref) into thermal strain → stress. Outputs the
        # temperature (NT), displacement (U) and stress (S) fields.
        block = [
            f"*STEP, NAME={step_name}",
            "*UNCOUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE",
        ]
        if loads:
            block.append("*CFLUX")
            for load in loads:
                target = load.get("target", "unknown")
                total = float(load.get("value", 0.0) or 0.0)
                n_nodes = counts.get(target, 0)
                per_node = total / n_nodes if n_nodes > 0 else total
                block.append(f"{target}, 11, {per_node:.6f}")
        block += ["*NODE FILE", "NT, U", "*EL FILE", "S", "*END STEP"]
        return block

    # static (default)
    block = [f"*STEP, NAME={step_name}", "*STATIC", "1.0, 1.0"]
    if loads:
        block += _cload_lines()
    block += ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP"]
    return block


# ---------------------------------------------------------------------------
# Package I/O helpers
# ---------------------------------------------------------------------------


def _read_optional_json(zf: zipfile.ZipFile, member: str) -> Any | None:
    if member not in set(zf.namelist()):
        return None
    try:
        return json.loads(zf.read(member))
    except Exception:
        return None


def _read_optional_yaml(zf: zipfile.ZipFile, member: str) -> Any | None:
    if member not in set(zf.namelist()):
        return None
    try:
        return yaml.safe_load(zf.read(member).decode("utf-8", errors="replace"))
    except Exception:
        return None


def _read_existing_members(
    zf: zipfile.ZipFile,
    skip_path: str,
) -> list[tuple[zipfile.ZipInfo, bytes]]:
    skip = {"manifest.json", skip_path}
    seen: set[str] = set()
    members: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info in zf.infolist():
        if info.filename in skip or info.filename in seen:
            continue
        seen.add(info.filename)
        data = b"" if info.is_dir() else zf.read(info.filename)
        members.append((info, data))
    return members


def _rewrite_package(
    path: Path,
    members: list[tuple[zipfile.ZipInfo, bytes]],
    manifest: dict[str, Any],
    out_path_in_zip: str,
    deck_text: str,
) -> None:
    """Atomically rewrite the .aieng package with the new solver input deck."""
    resources = manifest.setdefault("resources", {})
    simulation_resources = resources.setdefault("simulation", {})
    if not isinstance(simulation_resources, dict):
        raise ValueError("manifest resources.simulation must be an object")

    runs = simulation_resources.setdefault("runs", {})
    if not isinstance(runs, dict):
        runs = {}
        simulation_resources["runs"] = runs
    run_entry = runs.setdefault(Path(out_path_in_zip).parent.name, {})
    if not isinstance(run_entry, dict):
        run_entry = {}
        runs[Path(out_path_in_zip).parent.name] = run_entry
    run_entry["solver_input"] = out_path_in_zip

    manifest_json = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    deck_bytes = deck_text.encode()

    # Ensure the parent directory entry exists in the ZIP
    dir_entry = str(Path(out_path_in_zip).parent) + "/"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as fh:
        temp_path = Path(fh.name)

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for info, data in members:
                zf.writestr(info, data)
            if dir_entry not in {m.filename for m, _ in members}:
                zf.writestr(dir_entry, b"")
            zf.writestr("manifest.json", manifest_json)
            zf.writestr(out_path_in_zip, deck_bytes)
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
