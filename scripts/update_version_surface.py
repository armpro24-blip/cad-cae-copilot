"""Compute hashes for the AIENG version surface.

Run this script after changing MCP tool schemas, artifact schemas, or skill prompts,
then bump the relevant surface version in aieng/schemas/version_surface.json.

Usage:
    python scripts/update_version_surface.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_SURFACE_PATH = REPO_ROOT / "aieng" / "src" / "aieng" / "schemas" / "version_surface.json"


def _canonical_json_sha256(obj: object) -> str:
    """Return SHA256 of object serialised as compact canonical JSON."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _hash_text(path: Path) -> str:
    """Hash UTF-8 text after normalizing checkout-dependent line endings."""
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _hash_directory_texts(pattern: str) -> str:
    """Hash all files matching glob, sorted by relative path."""
    files = sorted(REPO_ROOT.glob(pattern))
    entries = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        entries.append({"path": rel, "sha256": _hash_text(path)})
    return _canonical_json_sha256(entries)


def _hash_tool_surface() -> str:
    """Hash the MCP tool surface: curated tool schemas + registered tool names."""
    import sys

    backend = REPO_ROOT / "aieng-ui" / "backend"
    sys.path.insert(0, str(backend))
    from app.runtime_tool_schemas import TOOL_SCHEMAS

    schemas = {name: TOOL_SCHEMAS[name] for name in sorted(TOOL_SCHEMAS)}
    return _canonical_json_sha256({"tool_schemas": schemas})


def _hash_artifact_schemas() -> str:
    """Hash all aieng artifact schemas."""
    return _hash_directory_texts("aieng/src/aieng/schemas/*.schema.json")


def _hash_skill_prompts() -> str:
    """Hash all agent skill prompt / instruction files."""
    return _hash_directory_texts("aieng-agent-skills/**/SKILL.md")


def _compute_surface() -> dict[str, Any]:
    """Compute the current version surface without writing it."""
    return {
        "mcp_tool_surface": {"sha256": _hash_tool_surface()},
        "artifact_schemas": {"sha256": _hash_artifact_schemas()},
        "skill_prompts": {"sha256": _hash_skill_prompts()},
    }


def _write_surface() -> None:
    VERSION_SURFACE_PATH.parent.mkdir(parents=True, exist_ok=True)

    computed = _compute_surface()

    if VERSION_SURFACE_PATH.exists():
        surface = json.loads(VERSION_SURFACE_PATH.read_text(encoding="utf-8"))
    else:
        surface = {
            "format": "aieng.version_surface.v1",
            "version": "0.1.0-alpha.1",
        }

    surface["surfaces"] = {
        "mcp_tool_surface": {
            "version": surface.get("surfaces", {}).get("mcp_tool_surface", {}).get("version", "0.1.0-alpha.1"),
            "policy": "unstable",
            "sha256": computed["mcp_tool_surface"]["sha256"],
        },
        "artifact_schemas": {
            "version": surface.get("surfaces", {}).get("artifact_schemas", {}).get("version", "0.1.0-alpha.1"),
            "policy": "unstable",
            "sha256": computed["artifact_schemas"]["sha256"],
        },
        "skill_prompts": {
            "version": surface.get("surfaces", {}).get("skill_prompts", {}).get("version", "0.1.0-alpha.1"),
            "policy": "unstable",
            "sha256": computed["skill_prompts"]["sha256"],
        },
    }
    surface["compat_policy"] = {
        "status": "alpha",
        "summary": (
            "The alpha contract declares itself unstable. Drift is detectable, "
            "not frozen. Connected agents should compare the version surface before "
            "relying on tool shapes, schemas, or prompt contracts."
        ),
        "breaking_changes": {
            "mcp_tool_surface": "Renaming a tool, removing a tool, removing/retyping a required parameter, or tightening additionalProperties.",
            "artifact_schemas": "Removing a schema, removing a required field, or changing a field type in a way that invalidates existing packages.",
            "skill_prompts": "Removing a skill, changing a skill's MCP tool contract, or changing prompt instructions that agents depend on.",
        },
        "additive_changes": {
            "mcp_tool_surface": "Adding a new tool or adding optional parameters to an existing tool.",
            "artifact_schemas": "Adding a new schema or adding optional fields to an existing schema.",
            "skill_prompts": "Adding a new skill or adding clarifying guidance that does not change tool usage.",
        },
        "deprecation_window": "No deprecation window during alpha; breaking changes are allowed but must bump the relevant surface version and update this file.",
    }
    surface["note"] = (
        "Generated by scripts/update_version_surface.py. Run it after changing "
        "tool schemas, artifact schemas, or skill prompts, then bump the affected "
        "surface version."
    )

    VERSION_SURFACE_PATH.write_text(
        json.dumps(surface, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _check_surface() -> list[str]:
    """Return list of mismatch descriptions between stored and computed hashes."""
    mismatches: list[str] = []
    if not VERSION_SURFACE_PATH.exists():
        return ["version_surface.json is missing"]

    surface = json.loads(VERSION_SURFACE_PATH.read_text(encoding="utf-8"))
    computed = _compute_surface()
    for name in ("mcp_tool_surface", "artifact_schemas", "skill_prompts"):
        stored = surface.get("surfaces", {}).get(name, {})
        stored_hash = stored.get("sha256")
        computed_hash = computed[name]["sha256"]
        if stored_hash != computed_hash:
            mismatches.append(
                f"{name}: hash mismatch (stored {stored_hash!r}, computed {computed_hash!r}). "
                f"Run scripts/update_version_surface.py and bump the surface version."
            )
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="update_version_surface")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check stored hashes match current artifacts without writing.",
    )
    args = parser.parse_args(argv)

    if args.check:
        mismatches = _check_surface()
        if mismatches:
            for m in mismatches:
                print(f"MISMATCH: {m}", file=sys.stderr)
            return 1
        print("Version surface hashes match current artifacts.")
        return 0

    _write_surface()
    surface = json.loads(VERSION_SURFACE_PATH.read_text(encoding="utf-8"))
    print(f"Updated {VERSION_SURFACE_PATH.relative_to(REPO_ROOT)}")
    print(json.dumps(surface["surfaces"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
