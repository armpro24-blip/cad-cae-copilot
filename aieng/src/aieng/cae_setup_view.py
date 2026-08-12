"""One way to read a package's CAE setup, whichever shape authored it.

Two authoring paths write the same physics in different shapes:

- ``ai_preprocessing`` (LLM-backed, needs an API key) writes
  ``simulation/setup.yaml`` — loads/BCs target a **feature id**
  (``target_feature``) and materials are a dict keyed by name in MPa;
- ``cae.setup_static`` / ``cae.apply_setup_patch`` — the documented, key-free
  agent path — write ``simulation/cae_imports/parsed_*.json``, where loads/BCs
  target an **NSET name** (``target``) and materials are a list in Pa.

Every consumer that reads only the first shape is dead on the path the docs tell
agents to use. That has now been found twice: the static solver behind
``opt.sizing_sweep`` (fixed 2026-08-11), and the topology-optimization
derivation, which was worse — it did not fail, it silently substituted a
textbook cantilever preset and reported ``status: ok``, so a real bracket would
have been optimized against a fictional load case and written back as its
geometry.

So the translation lives here, in the core library, and both callers use it.
Reading a package's physics is a package-format concern, not a backend one.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

SETUP_YAML_PATH = "simulation/setup.yaml"
SETUP_JSON_PATHS = ("simulation/setup.json", "cae/setup.json")
CAE_MAPPING_PATH = "simulation/cae_mapping.json"
PARSED_MATERIALS_PATH = "simulation/cae_imports/parsed_materials.json"
PARSED_BCS_PATH = "simulation/cae_imports/parsed_boundary_conditions.json"
PARSED_LOADS_PATH = "simulation/cae_imports/parsed_loads.json"
SOLVER_SETTINGS_PATH = "simulation/solver_settings.json"

SYNTHESIZED_FROM = "simulation/cae_imports/parsed_*.json"


def _read_text(zf: zipfile.ZipFile, member: str) -> str | None:
    try:
        return zf.read(member).decode("utf-8")
    except KeyError:
        return None


def _read_json(zf: zipfile.ZipFile, member: str) -> dict[str, Any]:
    raw = _read_text(zf, member)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_setup_document(raw: str) -> dict[str, Any]:
    if raw.lstrip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    try:
        import yaml  # lazy: only needed for YAML setups
    except ImportError:
        return {}
    try:
        parsed = yaml.safe_load(raw)
    except Exception:  # noqa: BLE001 - a malformed setup is "no setup", not a crash
        return {}
    return parsed if isinstance(parsed, dict) else {}


def synthesize_setup_from_parsed(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """Translate the ``parsed_*.json`` shape into the ``setup.yaml`` shape.

    Each NSET target is mapped back to its ``maps_to.feature_id`` through
    ``cae_mapping.json``. Returns ``None`` when there is nothing to synthesize
    from, so the caller can still report "no CAE setup" honestly rather than
    inventing one.
    """
    entity_to_feature: dict[str, str] = {}
    for mapping in _read_json(zf, CAE_MAPPING_PATH).get("mappings") or []:
        if not isinstance(mapping, dict):
            continue
        entity = mapping.get("cae_entity")
        feature_id = (mapping.get("maps_to") or {}).get("feature_id")
        if entity and feature_id:
            entity_to_feature[str(entity)] = str(feature_id)

    def target_feature(item: dict[str, Any]) -> str | None:
        explicit = item.get("target_feature")
        if explicit:
            return str(explicit)
        target = item.get("target")
        if target is None:
            return None
        return entity_to_feature.get(str(target))

    materials: dict[str, Any] = {}
    material_name: str | None = None
    for entry in _read_json(zf, PARSED_MATERIALS_PATH).get("materials") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "AIENG_MATERIAL")
        modulus_mpa = entry.get("youngs_modulus_mpa")
        if modulus_mpa is None and entry.get("youngs_modulus_pa") is not None:
            try:
                modulus_mpa = float(entry["youngs_modulus_pa"]) / 1e6
            except (TypeError, ValueError):
                modulus_mpa = None
        materials[name] = {
            "youngs_modulus_mpa": modulus_mpa if modulus_mpa is not None else 69000.0,
            "poisson_ratio": entry.get("poisson_ratio", 0.33),
            "density_kg_m3": entry.get("density_kg_m3", 2700),
        }
        if material_name is None:
            material_name = name

    boundary_conditions: list[dict[str, Any]] = []
    for bc in _read_json(zf, PARSED_BCS_PATH).get("boundary_conditions") or []:
        if not isinstance(bc, dict):
            continue
        feature_id = target_feature(bc)
        if feature_id:
            boundary_conditions.append(
                {"target_feature": feature_id, "type": bc.get("type", "fixed")}
            )

    loads: list[dict[str, Any]] = []
    for load in _read_json(zf, PARSED_LOADS_PATH).get("loads") or []:
        if not isinstance(load, dict):
            continue
        feature_id = target_feature(load)
        if not feature_id:
            continue
        loads.append({
            "target_feature": feature_id,
            "value_n": load.get("value_n", load.get("magnitude_n", load.get("value", 0.0))),
            "direction": load.get("direction") or [0.0, 0.0, -1.0],
        })

    if not (materials or boundary_conditions or loads):
        return None

    setup: dict[str, Any] = {
        "materials": materials,
        "boundary_conditions": boundary_conditions,
        "loads": loads,
        "synthesized_from": SYNTHESIZED_FROM,
    }
    if material_name:
        setup["material_name"] = material_name
    mesh_size = _read_json(zf, SOLVER_SETTINGS_PATH).get("mesh_size_mm")
    if mesh_size:
        setup["mesh"] = {"target_size_mm": mesh_size}
    return setup


def load_cae_setup(zf: zipfile.ZipFile) -> dict[str, Any]:
    """The package's CAE setup in ``setup.yaml`` shape, or ``{}`` if there is none.

    An explicit setup document always wins; the parsed artifacts are the
    fallback, so a package authored either way reads the same.
    """
    raw = _read_text(zf, SETUP_YAML_PATH)
    for member in SETUP_JSON_PATHS:
        if raw:
            break
        raw = _read_text(zf, member)
    if raw:
        parsed = _parse_setup_document(raw)
        if parsed:
            return parsed
    return synthesize_setup_from_parsed(zf) or {}


def load_cae_setup_from_package(package_path: str | Path) -> dict[str, Any]:
    """`load_cae_setup` for callers that hold a path rather than an open zip."""
    try:
        with zipfile.ZipFile(Path(package_path)) as zf:
            return load_cae_setup(zf)
    except (OSError, zipfile.BadZipFile):
        return {}
