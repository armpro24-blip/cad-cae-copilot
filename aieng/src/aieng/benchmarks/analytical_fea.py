"""Quantitative analytical-FEA benchmark scorer.

This module implements the verify harness for the benchmark corpus requested in
issue #257. It consumes the per-case ``reference.json`` files in
``aieng/benchmarks/datasets/analytical_fea`` and the runnable ``.aieng``
packages produced by ``build_packages.py``.

The output is a machine-readable scorecard (``aieng.benchmark.analytical_fea.scorecard``)
with per-case verdict, deviation percent, and tolerance — suitable for CI and
for the regression runner described in issue #237.

Honesty boundary
----------------

* The scorecard records "agreement with analytical reference within tolerance".
* It never claims certification, ASME V&V-10 compliance, or NAFEMS endorsement.
* Meshes are intentionally coarse for CI runtime; tolerance bands absorb
  discretisation error.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aieng import FORMAT_VERSION
from aieng.nafems_verification import (
    REFERENCE_CASES,
    run_case,
    run_mesh_convergence_study,
    verify_case,
)


SCORECARD_FORMAT = "aieng.benchmark.analytical_fea.scorecard"
SCORECARD_VERSION = "0.1.0"


def _default_dataset_root() -> Path:
    """Resolve the default corpus directory relative to this source file."""
    # aieng/src/aieng/benchmarks/analytical_fea.py -> aieng/benchmarks/datasets/analytical_fea
    return Path(__file__).resolve().parents[3] / "benchmarks" / "datasets" / "analytical_fea"


def load_corpus(dataset_root: Path | str | None = None) -> dict[str, Any]:
    """Load ``corpus.json`` from the analytical FEA dataset directory."""
    root = Path(dataset_root or _default_dataset_root())
    path = root / "corpus.json"
    if not path.exists():
        raise FileNotFoundError(f"corpus manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_reference(case_id: str, dataset_root: Path | str | None = None) -> dict[str, Any]:
    """Load a single case's ``reference.json``."""
    root = Path(dataset_root or _default_dataset_root())
    path = root / case_id / "reference.json"
    if not path.exists():
        raise FileNotFoundError(f"reference file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _import_fixture_builder() -> Any:
    """Import the NAFEMS fixture builder from the tests fixtures tree.

    The builder is kept alongside the existing NAFEMS regression tests so it can
    be reused without duplication. This import is delayed and path-limited so
    the scorer module remains importable outside the test tree.
    """
    repo_root = _default_dataset_root().parents[2]
    fixtures_root = repo_root / "tests" / "fixtures"
    if not fixtures_root.exists():
        raise RuntimeError(
            "Cannot locate tests/fixtures directory; run from a source checkout."
        )
    if str(fixtures_root) not in sys.path:
        sys.path.insert(0, str(fixtures_root))
    try:
        from nafems import build_fixtures  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "Failed to import nafems.build_fixtures; ensure the repo checkout is intact."
        ) from exc
    return build_fixtures


def build_fixture_packages(
    packages_dir: Path | str,
    dataset_root: Path | str | None = None,
) -> dict[str, Path]:
    """Build all analytical FEA ``.aieng`` packages into ``packages_dir``.

    Returns a mapping from ``case_id`` to package path.
    """
    build_fixtures = _import_fixture_builder()
    out_dir = Path(packages_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    corpus = load_corpus(dataset_root)
    case_ids = [c["case_id"] for c in corpus.get("cases", [])]
    built: dict[str, Path] = {}
    for case_id in case_ids:
        builder = build_fixtures.CASE_BUILDERS.get(case_id)
        if builder is None:
            raise KeyError(f"no fixture builder for case {case_id}")
        path = builder(out_dir / f"{case_id}.aieng")
        built[case_id] = path
    return built


def ensure_packages(
    packages_dir: Path | str | None,
    dataset_root: Path | str | None = None,
) -> Path:
    """Return a directory containing all case packages, building them if needed."""
    if packages_dir is not None:
        path = Path(packages_dir)
        missing = [
            case["case_id"]
            for case in load_corpus(dataset_root).get("cases", [])
            if not (path / f"{case['case_id']}.aieng").exists()
        ]
        if not missing:
            return path
        build_fixture_packages(path, dataset_root)
        return path

    # No packages directory supplied: build into a temp directory.
    tmp = Path(tempfile.mkdtemp(prefix="analytical_fea_"))
    build_fixture_packages(tmp, dataset_root)
    return tmp


CaseRunner = Callable[[Path, str], dict[str, Any]]


def score_case(
    case_id: str,
    package_path: Path | str,
    reference: dict[str, Any],
    *,
    run_id: str = "analytical_fea_run_001",
    runner: CaseRunner = run_case,
) -> dict[str, Any]:
    """Run one case and compare its computed metrics to the reference.

    Args:
        case_id: Canonical case identifier.
        package_path: Path to the ``.aieng`` package for the case.
        reference: Loaded ``reference.json`` content.
        run_id: Solver run identifier.
        runner: Callable that executes the case and returns computed metrics.
            Defaults to :func:`aieng.nafems_verification.run_case`.

    Returns:
        Scorecard entry with ``case_id``, ``status``, ``verdict``, and per-metric
        comparisons.
    """
    run_result = runner(Path(package_path), run_id=run_id)
    status = run_result.get("status")

    if status == "skipped":
        return {
            "case_id": case_id,
            "status": "skipped",
            "verdict": "skipped",
            "missing_tools": run_result.get("missing_tools"),
            "message": run_result.get("message"),
            "metrics": [],
        }

    if status != "ok" or run_result.get("computed_metrics") is None:
        return {
            "case_id": case_id,
            "status": status or "error",
            "verdict": "fail",
            "message": run_result.get("message"),
            "solver_log_tail": run_result.get("solver_log_tail"),
            "metrics": [],
        }

    verification = verify_case(case_id, run_result["computed_metrics"], reference=reference)
    return {
        "case_id": case_id,
        "status": "ok",
        "verdict": verification["verdict"],
        "analysis_type": run_result.get("analysis_type", reference.get("analysis_type", "static")),
        "metrics": verification["metrics"],
    }


def _default_mesh_refinements() -> dict[str, list[tuple[int, int, int]]]:
    return {
        "tension_rod": [(10, 2, 2), (20, 2, 2), (40, 4, 4)],
        "cantilever_end_load": [(10, 2, 2), (20, 4, 4), (40, 8, 8)],
    }


def run_mesh_convergence_score(
    case_id: str,
    packages_dir: Path,
    refinements: list[tuple[int, int, int]] | None = None,
    *,
    run_id_prefix: str = "mesh_conv",
) -> dict[str, Any]:
    """Run a mesh-convergence sub-study for one case and return a scorecard fragment."""
    if refinements is None:
        refinements = _default_mesh_refinements().get(case_id, [])
    if len(refinements) < 2:
        return {
            "case_id": case_id,
            "status": "skipped",
            "message": "fewer than two refinement levels configured",
        }

    build_fixtures = _import_fixture_builder()
    builder = build_fixtures.CASE_BUILDERS.get(case_id)
    if builder is None:
        return {
            "case_id": case_id,
            "status": "skipped",
            "message": f"no fixture builder for case {case_id}",
        }

    levels: dict[tuple[int, int, int], Path] = {}
    for divisions in refinements:
        path = packages_dir / f"{case_id}_{divisions[0]}x{divisions[1]}x{divisions[2]}.aieng"
        builder(path, mesh_divisions=divisions)
        levels[divisions] = path

    try:
        study = run_mesh_convergence_study(case_id, levels, run_id_prefix=run_id_prefix)
    except Exception as exc:
        return {
            "case_id": case_id,
            "status": "error",
            "message": f"mesh convergence study failed: {type(exc).__name__}: {exc}",
        }
    return study


def score_corpus(
    packages_dir: Path | str | None = None,
    *,
    dataset_root: Path | str | None = None,
    run_id: str = "analytical_fea_run_001",
    runner: CaseRunner | None = None,
    mesh_convergence: bool = False,
) -> dict[str, Any]:
    """Run the full analytical FEA corpus and emit a scorecard.

    Args:
        packages_dir: Directory containing pre-built ``.aieng`` packages. If
            omitted, packages are built into a temporary directory.
        dataset_root: Directory containing ``corpus.json`` and per-case
            ``reference.json`` files.
        run_id: Solver run identifier.
        runner: Optional case runner override (useful for testing / mocking).
        mesh_convergence: Whether to run optional mesh-convergence substudies.

    Returns:
        Machine-readable scorecard dict.
    """
    corpus = load_corpus(dataset_root)
    pkg_dir = ensure_packages(packages_dir, dataset_root)
    active_runner: CaseRunner = runner or run_case

    case_results: list[dict[str, Any]] = []
    any_fail = False
    any_skip = False

    for entry in corpus.get("cases", []):
        case_id = entry["case_id"]
        reference = load_reference(case_id, dataset_root)
        package_path = pkg_dir / f"{case_id}.aieng"
        case_result = score_case(
            case_id,
            package_path,
            reference,
            run_id=run_id,
            runner=active_runner,
        )
        if case_result["verdict"] == "fail":
            any_fail = True
        elif case_result["verdict"] == "skipped":
            any_skip = True

        if mesh_convergence and case_result["status"] == "ok":
            case_result["mesh_convergence"] = run_mesh_convergence_score(case_id, pkg_dir)

        case_results.append(case_result)

    total = len(case_results)
    passed = sum(1 for c in case_results if c["verdict"] == "pass")
    failed = sum(1 for c in case_results if c["verdict"] == "fail")
    skipped = sum(1 for c in case_results if c["verdict"] == "skipped")

    if any_fail:
        status = "failed"
    elif any_skip:
        status = "skipped"
    else:
        status = "passed"

    return {
        "format": SCORECARD_FORMAT,
        "format_version": SCORECARD_VERSION,
        "aieng_format_version": FORMAT_VERSION,
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "layer": "analytical_fea",
        "corpus": corpus.get("corpus_id"),
        "status": status,
        "cases": case_results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "honesty": {
            "claim": "agreement with analytical reference within documented tolerance",
            "certified": False,
            "asme_vv_10_certified": False,
            "nafems_certified": False,
            "reference_basis": "closed-form beam/rod/column theory",
            "mesh_note": (
                "Coarse C3D8 meshes for fast CI. Only metrics with gating=true decide a "
                "case verdict. In the default corpus every peak stress is non-gating and "
                "may deviate far outside its band (clamped-end concentration, bending "
                "under-prediction); a custom dataset_root sets its own gates."
            ),
        },
    }


def write_scorecard(scorecard: dict[str, Any], out_path: Path | str) -> Path:
    """Write the scorecard to disk as sorted, indented JSON."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scorecard, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the analytical FEA benchmark corpus and emit a scorecard"
    )
    parser.add_argument(
        "--packages-dir",
        type=Path,
        default=None,
        help="Directory with pre-built .aieng packages (default: build temp packages)",
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=None,
        help="Root of the analytical_fea dataset (default: aieng/benchmarks/datasets/analytical_fea)",
    )
    parser.add_argument(
        "--run-id",
        default="analytical_fea_run_001",
        help="Solver run identifier",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Path to write the JSON scorecard (default: print to stdout)",
    )
    parser.add_argument(
        "--mesh-convergence",
        action="store_true",
        help="Run optional mesh-convergence substudies",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build fixture packages and exit without running the scorer",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.build_only:
        pkg_dir = ensure_packages(args.packages_dir, args.dataset_root)
        print(f"Built fixture packages in {pkg_dir}")
        return 0

    scorecard = score_corpus(
        packages_dir=args.packages_dir,
        dataset_root=args.dataset_root,
        run_id=args.run_id,
        mesh_convergence=args.mesh_convergence,
    )
    text = json.dumps(scorecard, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote scorecard to {args.out}")
    else:
        print(text)
    return 0 if scorecard["status"] in ("passed", "skipped") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
