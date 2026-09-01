"""Solver-neutral CAE result contract.

Decouples CAE result mapping from any specific solver. The pipeline is three
layers:

    solver runner  →  result normalizer (adapter)  →  Shape IR mapper

  1. A *solver runner* (e.g. the CalculiX adapter) executes the analysis and
     produces solver-native output.
  2. A *normalizer/adapter* translates that into the neutral artifacts defined
     here: ``analysis/computed_metrics.json`` and ``analysis/field_regions.json``
     (each carrying a ``solver`` provenance block).
  3. The *Shape IR mapper* (``cae_result_map``) consumes ONLY those neutral
     artifacts + topology_map + object_registry — it never reads .frd/.dat/.inp
     or knows any solver's naming.

CalculiX is just the first adapter. Code_Aster / Elmer / FEniCSx / remote solvers
plug in by emitting the same neutral files (directly, or via their own adapter).

See ``docs/cae_result_contract.md`` for the full schemas.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from aieng import FORMAT_VERSION

CAE_CONTRACT_VERSION = "0.1"
COMPUTED_METRICS_FORMAT = "aieng.cae.computed_metrics"
FIELD_REGIONS_FORMAT = "aieng.cae.field_regions"

# Neutral (contract) artifact locations.
NEUTRAL_COMPUTED_METRICS_PATH = "analysis/computed_metrics.json"
NEUTRAL_FIELD_REGIONS_PATH = "analysis/field_regions.json"
# Solver-native (CalculiX adapter) source locations.
NATIVE_COMPUTED_METRICS_PATH = "results/computed_metrics.json"
NATIVE_FIELD_REGIONS_PATH = "results/field_regions.json"

# ── CalculiX adapter translation tables (kept OUT of the neutral mapper) ──────
_CALCULIX_METRIC_RESULT_TYPE = {
    "max_von_mises_stress": "stress", "min_von_mises_stress": "stress",
    "max_principal_stress": "stress", "max_displacement": "displacement",
    "max_deflection": "deflection", "max_strain": "strain",
}
_CALCULIX_FIELD_RESULT_TYPE = {"S": "stress", "U": "displacement", "DISP": "displacement", "E": "strain"}


def _solver_block(name: str, version: str | None, adapter: str) -> dict[str, Any]:
    return {"name": name, "version": version, "adapter": adapter}


# The id stamped on a load case whose source declared none. Exported because
# the mapper must be able to tell a placeholder from a real id — a default that
# looks declared is indistinguishable from data, and this one manufactured a
# phantom second load case in analysis/cae_result_map.json.
DEFAULT_LOAD_CASE_ID = "load_case_1"


def normalize_calculix_computed_metrics(
    native: dict[str, Any], *, solver: str = "calculix", solver_version: str | None = None,
    adapter: str = "calculix_frd_v1",
) -> dict[str, Any]:
    """CalculiX results/computed_metrics.json -> neutral analysis/computed_metrics.json."""
    load_cases: list[dict[str, Any]] = []
    for lc in native.get("load_cases") or []:
        if not isinstance(lc, dict):
            continue
        results = []
        for name, mv in (lc.get("metrics") or {}).items():
            if not isinstance(mv, dict):
                continue
            rtype = _CALCULIX_METRIC_RESULT_TYPE.get(name, name.replace("max_", "").replace("min_", ""))
            results.append({
                "result_type": rtype, "metric": name,
                "max": mv.get("value"), "min": None, "average": None, "unit": mv.get("unit"),
            })
        declared_id = lc.get("id")
        entry = {"id": str(declared_id or DEFAULT_LOAD_CASE_ID), "results": results}
        if not declared_id:
            # A placeholder must not read as a declaration. Downstream, the
            # result map listed the source's real id AND this placeholder as two
            # load cases of one analysis, so its scalar extrema could not be
            # joined to its own clusters.
            entry["id_is_placeholder"] = True
        load_cases.append(entry)
    return {
        "format": COMPUTED_METRICS_FORMAT,
        "schema_version": CAE_CONTRACT_VERSION,
        "contract_version": CAE_CONTRACT_VERSION,
        "aieng_format_version": FORMAT_VERSION,
        "solver": _solver_block(solver, solver_version, adapter),
        "load_cases": load_cases,
        "warnings": list(native.get("warnings") or []),
    }


def normalize_calculix_field_regions(
    native: dict[str, Any], *, solver: str = "calculix", solver_version: str | None = None,
    adapter: str = "calculix_frd_v1", default_load_case: str = DEFAULT_LOAD_CASE_ID,
) -> dict[str, Any]:
    """CalculiX results/field_regions.json -> neutral analysis/field_regions.json.

    The native file holds one FRD field per file (S or U); the neutral file holds
    all regions across result types in a single ``regions`` array.
    """
    field = str(native.get("field") or "").upper()
    rtype = _CALCULIX_FIELD_RESULT_TYPE.get(field, field.lower() or "unknown")
    regions: list[dict[str, Any]] = []
    for i, c in enumerate(native.get("clusters") or [], start=1):
        if not isinstance(c, dict):
            continue
        loc = c.get("location") or {}
        mag = c.get("magnitude") or {}
        regions.append({
            "id": str(c.get("id") or f"region_{i:03d}"),
            "result_type": rtype,
            "load_case_id": str(c.get("load_case_id") or native.get("load_case_id") or default_load_case),
            "load_case_id_is_placeholder": not (c.get("load_case_id") or native.get("load_case_id")),
            "center": {"x": float(loc.get("x", 0.0)), "y": float(loc.get("y", 0.0)), "z": float(loc.get("z", 0.0))},
            "bbox": c.get("bbox"),
            "value": {"peak": mag.get("value"), "min": None, "max": mag.get("value"), "unit": mag.get("unit")},
            "node_count": c.get("node_count"),
            "source_metadata": {"feature_ref": c.get("feature_ref"), "native_field": field},
        })
    return {
        "format": FIELD_REGIONS_FORMAT,
        "schema_version": CAE_CONTRACT_VERSION,
        "contract_version": CAE_CONTRACT_VERSION,
        "aieng_format_version": FORMAT_VERSION,
        "solver": _solver_block(solver, solver_version, adapter),
        "metric": native.get("metric"),
        "regions": regions,
        "warnings": list(native.get("warnings") or []),
    }


def validate_neutral_computed_metrics(doc: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return False, ["computed_metrics must be an object"]
    if not isinstance(doc.get("load_cases"), list):
        errors.append("missing 'load_cases' array")
    for lc in doc.get("load_cases") or []:
        if not isinstance(lc, dict) or "id" not in lc:
            errors.append("each load case needs an 'id'")
        for r in (lc.get("results") or []) if isinstance(lc, dict) else []:
            if isinstance(r, dict) and "result_type" not in r:
                errors.append("each result needs a 'result_type'")
    return (not errors), errors


def validate_neutral_field_regions(doc: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return False, ["field_regions must be an object"]
    if not isinstance(doc.get("regions"), list):
        errors.append("missing 'regions' array")
    for r in doc.get("regions") or []:
        if not isinstance(r, dict):
            errors.append("region must be an object")
            continue
        if "result_type" not in r:
            errors.append("each region needs a 'result_type'")
        if "center" not in r and "bbox" not in r:
            errors.append(f"region '{r.get('id')}' needs a center or bbox")
    return (not errors), errors


def _read_json(zf: zipfile.ZipFile, name: str, names: set[str]) -> Any:
    if name not in names:
        return None
    try:
        return json.loads(zf.read(name).decode("utf-8"))
    except Exception:
        return None


def load_neutral_cae_artifacts(package_path: str | Path) -> dict[str, Any]:
    """Return neutral {computed_metrics, field_regions, source} for a package.

    Prefers the neutral ``analysis/*`` files; if absent, normalizes legacy
    CalculiX ``results/*`` on the fly (so existing packages keep working). The
    returned dicts are always in the neutral contract shape.
    """
    package_path = Path(package_path)
    cm = fr = None
    source = "none"
    if package_path.exists():
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            cm = _read_json(zf, NEUTRAL_COMPUTED_METRICS_PATH, names)
            fr = _read_json(zf, NEUTRAL_FIELD_REGIONS_PATH, names)
            if cm is not None or fr is not None:
                source = "neutral"
            if cm is None:
                native_cm = _read_json(zf, NATIVE_COMPUTED_METRICS_PATH, names)
                if native_cm is not None:
                    cm = normalize_calculix_computed_metrics(native_cm)
                    source = "neutral" if fr else "calculix_normalized"
            if fr is None:
                native_fr = _read_json(zf, NATIVE_FIELD_REGIONS_PATH, names)
                if native_fr is not None:
                    fr = normalize_calculix_field_regions(native_fr)
                    source = "calculix_normalized" if source in ("none", "calculix_normalized") else source
    return {"computed_metrics": cm, "field_regions": fr, "source": source}


def _replace_members(package_path: Path, members: dict[str, bytes]) -> None:
    if not package_path.exists() or not members:
        return
    tmp = package_path.with_suffix(".tmp.aieng")
    try:
        with (
            zipfile.ZipFile(package_path, "r") as src,
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            for item in src.infolist():
                if item.filename not in members:
                    dst.writestr(item, src.read(item.filename))
            for name, data in members.items():
                dst.writestr(name, data)
        tmp.replace(package_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def write_normalized_cae_artifacts(
    package_path: str | Path, *, solver: str = "calculix", solver_version: str | None = None,
    adapter: str = "calculix_frd_v1", overwrite: bool = False,
) -> dict[str, Any]:
    """Persist neutral analysis/* from solver-native results/* (CalculiX adapter).

    Skips files that already exist unless ``overwrite``. No-op for members already
    in neutral form. Returns {written: [...], skipped: [...]}.
    """
    package_path = Path(package_path)
    written: list[str] = []
    skipped: list[str] = []
    if not package_path.exists():
        return {"written": written, "skipped": skipped}
    with zipfile.ZipFile(package_path, "r") as zf:
        names = set(zf.namelist())
        have_cm = NEUTRAL_COMPUTED_METRICS_PATH in names
        have_fr = NEUTRAL_FIELD_REGIONS_PATH in names
        native_cm = _read_json(zf, NATIVE_COMPUTED_METRICS_PATH, names)
        native_fr = _read_json(zf, NATIVE_FIELD_REGIONS_PATH, names)
    members: dict[str, bytes] = {}
    if native_cm is not None and (overwrite or not have_cm):
        neutral = normalize_calculix_computed_metrics(native_cm, solver=solver, solver_version=solver_version, adapter=adapter)
        members[NEUTRAL_COMPUTED_METRICS_PATH] = (json.dumps(neutral, indent=2, sort_keys=True) + "\n").encode()
        written.append(NEUTRAL_COMPUTED_METRICS_PATH)
    elif have_cm:
        skipped.append(NEUTRAL_COMPUTED_METRICS_PATH)
    if native_fr is not None and (overwrite or not have_fr):
        neutral = normalize_calculix_field_regions(native_fr, solver=solver, solver_version=solver_version, adapter=adapter)
        members[NEUTRAL_FIELD_REGIONS_PATH] = (json.dumps(neutral, indent=2, sort_keys=True) + "\n").encode()
        written.append(NEUTRAL_FIELD_REGIONS_PATH)
    elif have_fr:
        skipped.append(NEUTRAL_FIELD_REGIONS_PATH)
    _replace_members(package_path, members)
    return {"written": written, "skipped": skipped}
