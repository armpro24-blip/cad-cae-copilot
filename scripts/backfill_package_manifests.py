"""Repair the stub manifest in packages written before #515.

Until #515 the workbench created every `.aieng` package with a hand-rolled
manifest — literally `{"schema_version": "0.1"}` — instead of calling
`aieng.package.build_manifest`. Those packages declare no `model_id`, so the
format's own validator rejects them and its AI summary writer reports each one
as `unknown_model`.

New packages are correct, and the CAD path now upgrades a stub manifest whenever
it rewrites a package, so any project still being worked on repairs itself. This
script is for the ones nobody will touch again.

What it does NOT do: it upgrades `manifest.json` and nothing else. The remaining
schema disagreements (`cae_mapping.json`, the `parsed_*` artifacts) are writer
questions with their own answers, tracked in #513 — rewriting those here would
be guessing at content this script cannot know.

Usage::

    python scripts/backfill_package_manifests.py                 # dry run
    python scripts/backfill_package_manifests.py --apply         # write
    python scripts/backfill_package_manifests.py --apply --backup .backup/manifests

Idempotent: a package whose manifest already declares `model_id` is left alone,
so re-running changes nothing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "aieng" / "src"))

from aieng.package import upgrade_manifest  # noqa: E402

_MANIFEST = "manifest.json"


@dataclass
class Outcome:
    package: Path
    action: str  # "upgraded" | "already_conforming" | "unreadable"
    detail: str = ""


def _read_manifest(package: Path) -> dict | None:
    with zipfile.ZipFile(package, "r") as zf:
        if _MANIFEST not in zf.namelist():
            return {}
        loaded = json.loads(zf.read(_MANIFEST).decode("utf-8"))
    return loaded if isinstance(loaded, dict) else None


def _rewrite_manifest(package: Path, manifest: dict) -> None:
    """Replace one member, atomically, leaving every other member's content intact.

    The archive is rebuilt (so compressed bytes and zip metadata change); what is
    preserved is every other member's name and content.
    """
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    tmp = package.with_suffix(".backfill.tmp")
    try:
        with (
            zipfile.ZipFile(package, "r") as src,
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            for item in src.infolist():
                if item.filename != _MANIFEST:
                    dst.writestr(item, src.read(item.filename))
            dst.writestr(_MANIFEST, payload)
        tmp.replace(package)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def process(package: Path, *, apply: bool, backup_dir: Path | None) -> Outcome:
    try:
        manifest = _read_manifest(package)
    except Exception as exc:  # noqa: BLE001 - a broken zip is reported, not fixed
        return Outcome(package, "unreadable", f"{type(exc).__name__}: {exc}")
    if manifest is None:
        return Outcome(package, "unreadable", "manifest.json is not a JSON object")
    if manifest.get("model_id"):
        return Outcome(package, "already_conforming", str(manifest["model_id"]))

    upgraded = upgrade_manifest(manifest, package.stem)
    added = sorted(set(upgraded) - set(manifest))
    dropped = sorted(set(manifest) - set(upgraded))
    detail = f"+{','.join(added)}" + (f"  -{','.join(dropped)}" if dropped else "")

    if apply:
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package, backup_dir / package.name)
        _rewrite_manifest(package, upgraded)
    return Outcome(package, "upgraded", detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=_REPO_ROOT / "aieng-ui" / "data" / "projects",
        help="platform projects directory (default: the repo's own data dir)",
    )
    parser.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    parser.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="copy each package here before rewriting it (recommended with --apply)",
    )
    args = parser.parse_args(argv)

    packages = sorted(args.projects_root.glob("*/*.aieng"))
    if not packages:
        print(f"no packages under {args.projects_root}")
        return 0

    outcomes = [process(p, apply=args.apply, backup_dir=args.backup) for p in packages]
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.action] = counts.get(outcome.action, 0) + 1
        if outcome.action != "already_conforming":
            print(f"  {outcome.action:<18} {outcome.package.name:<24} {outcome.detail}")

    verb = "upgraded" if args.apply else "would upgrade"
    print(
        f"\n{len(packages)} package(s): {verb} {counts.get('upgraded', 0)}, "
        f"already conforming {counts.get('already_conforming', 0)}, "
        f"unreadable {counts.get('unreadable', 0)}"
    )
    if not args.apply and counts.get("upgraded"):
        print("dry run — pass --apply (and --backup DIR) to write")
    return 1 if counts.get("unreadable") else 0


if __name__ == "__main__":
    sys.exit(main())
