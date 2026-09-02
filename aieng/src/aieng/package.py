from __future__ import annotations

from copy import deepcopy
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import FORMAT_VERSION, __version__

PACKAGE_DIRECTORIES = (
    "geometry/",
    "graph/",
    "engineering_context/",
    "simulation/",
    "ai/",
    "ai/patches/",
    "results/",
    "task/",
    "previews/",
    "validation/",
    "visual/",
    "provenance/",
)

DEFAULT_RESOURCES: dict[str, Any] = {
    "geometry": {},
    "graph": {},
    "simulation": {},
    "ai": {"patches": []},
    "results": {},
    "task": {},
    "previews": {},
}

DEFAULT_UNITS = {
    "length": "mm",
    "mass": "kg",
    "force": "N",
    "stress": "MPa",
}


@dataclass(frozen=True)
class Manifest:
    model_id: str
    format_version: str
    units: dict[str, str]
    resources: dict[str, Any]
    created_by: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "format_version": self.format_version,
            "units": self.units,
            "resources": self.resources,
            "created_by": self.created_by,
        }


def build_manifest(model_id: str) -> Manifest:
    clean_model_id = model_id.strip()
    if not clean_model_id:
        raise ValueError("model_id must not be empty")
    return Manifest(
        model_id=clean_model_id,
        format_version=FORMAT_VERSION,
        units=dict(DEFAULT_UNITS),
        resources=deepcopy(DEFAULT_RESOURCES),
        created_by={
            "tool": f"aieng {__version__}",
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
    )


#: Keys a legacy writer emitted that this format never declared. Dropped on
#: upgrade because they are pure `additionalProperties` violations carrying no
#: information — `schema_version` duplicated `format_version` and nothing read it.
LEGACY_MANIFEST_KEYS = frozenset({"schema_version"})


def _overlay(base: dict[str, Any], extra: Any) -> Any:
    """Overlay a recorded value onto a canonical default, losing nothing.

    Two dicts merge key by key so a PARTIAL input keeps the defaults it did not
    mention — `{"units": {"length": "m"}}` must not drop `mass`/`force`/`stress`
    and leave the result non-conforming. Anything else replaces the default
    outright, including a value of the wrong type: a manifest carrying
    `"resources": []` is malformed, and silently swapping in defaults would
    invent data the package never declared. The validator reports it instead.
    """
    if not isinstance(extra, dict) or not isinstance(base, dict):
        return deepcopy(extra)
    for key, value in extra.items():
        base[key] = _overlay(base.get(key), value)
    return base


def upgrade_manifest(manifest: dict[str, Any] | None, model_id: str) -> dict[str, Any]:
    """Add the identity fields a legacy manifest lacks, discarding nothing.

    It repairs what it can name; it is not a promise of conformance. A manifest
    whose `resources` tree the writer shaped wrongly stays wrong, because
    guessing the writer's intent from a migration is how content gets lost.

    For packages written before the workbench used this module's
    :func:`build_manifest` — their manifest was `{"schema_version": "0.1"}` plus
    whatever `resources` later writers merged in, so it declared no `model_id`
    and the AI summary writer reported them all as ``unknown_model``.

    Deliberately additive: a field the input already declares WINS, so an
    upgrade never renames a model or rewrites someone's `created_by` provenance,
    and unknown extra keys (e.g. the CAD path's `geometry_execution` record) are
    preserved rather than discarded to make a validator happy. Idempotent.
    """
    existing = {k: v for k, v in (manifest or {}).items() if k not in LEGACY_MANIFEST_KEYS}
    upgraded = build_manifest(model_id).to_dict()
    for key, value in existing.items():
        # Overlay, not replace: a declared field wins leaf by leaf, so a partial
        # `units` or `created_by` keeps the rest of its required keys.
        upgraded[key] = _overlay(upgraded.get(key), value)
    return upgraded


def create_package(
    model_id: str,
    out: str | Path,
    *,
    overwrite: bool = False,
    design_targets: str | Path | None = None,
) -> Path:
    """Create an empty Phase 0 .aieng zip package."""
    out_path = Path(out)
    if out_path.suffix != ".aieng":
        raise ValueError("output path must end with .aieng")
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"package already exists: {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(model_id).to_dict()
    design_targets_path = Path(design_targets) if design_targets is not None else None
    if design_targets_path is not None:
        if not design_targets_path.exists():
            raise FileNotFoundError(f"design targets file not found: {design_targets_path}")
        manifest["resources"].setdefault("task", {})["design_targets"] = "task/design_targets.yaml"

    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        for directory in PACKAGE_DIRECTORIES:
            package.writestr(directory, b"")
        if design_targets_path is not None:
            package.writestr("task/design_targets.yaml", design_targets_path.read_bytes())
    return out_path


def build_step_import_manifest(model_id: str) -> dict[str, Any]:
    """Build a manifest for a Phase 1 STEP resource import package."""
    manifest = build_manifest(model_id).to_dict()
    manifest["source_mode"] = "step"
    manifest["resources"]["geometry"] = {
        "source": "geometry/source.step",
        "normalized": "geometry/normalized.step",
    }
    return manifest


def model_id_from_package_path(out: str | Path) -> str:
    """Infer a stable model ID from the output package filename."""
    model_id = Path(out).stem.strip()
    if not model_id:
        raise ValueError("could not infer model_id from output path")
    return model_id


def read_manifest(package_path: str | Path) -> dict[str, Any]:
    with zipfile.ZipFile(package_path) as package:
        with package.open("manifest.json") as manifest_file:
            return json.load(manifest_file)
