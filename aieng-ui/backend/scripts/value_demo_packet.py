"""Canonical value-demo packet for issue #368.

This script does not run CAD, mesh, or solver tools. It emits the fixed
prompt/tool sequence and evidence checklist for the real CAD->CAE value demo so
external agents and reviewers run the same small case every time.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any


VALUE_DEMO_CAD_CODE = """from build123d import *

# Single connected solid, 100 x 20 x 10 mm, centered at the origin.
# The integration test for the real solve loop uses the same dimensions.
# Dimensions are UPPER_SNAKE_CASE constants so they stay editable via
# cad.edit_parameter and sweepable via opt.sizing_sweep after the solve.
BEAM_LENGTH = 100.0
BEAM_WIDTH = 20.0
BEAM_THICKNESS = 10.0

beam = Box(BEAM_LENGTH, BEAM_WIDTH, BEAM_THICKNESS)
beam.label = "value_demo_cantilever"
beam.color = Color(0.18, 0.45, 0.85)
result = beam
"""

VALUE_DEMO_STEPS: tuple[dict[str, Any], ...] = (
    {
        "id": "onboard",
        "tool": "aieng.agent_readme, aieng.guide, aieng.create_project",
        "prompt": (
            "Start a new AIENG project named value-demo-cantilever. Read the "
            "CAD and CAE guides before creating geometry or running analysis."
        ),
        "expected": ["project_id"],
    },
    {
        "id": "build_cad",
        "tool": "cad.execute_build123d",
        "prompt": (
            "Create the single connected cantilever beam from the canonical "
            "VALUE_DEMO_CAD_CODE. Use mode=replace, model_kind=mechanical, "
            "response_detail=compact, thumbnail=true."
        ),
        "expected": [
            "geometry/generated.step",
            "geometry/topology_map.json",
            "graph/feature_graph.json",
            "named part value_demo_cantilever",
        ],
    },
    {
        "id": "pick_faces",
        "tool": "aieng.agent_context",
        "prompt": (
            "Inspect the topology and copy the @face pointer on the xmin end as "
            "FIXED_END and the @face pointer on the xmax end as LOAD_END."
        ),
        "expected": ["two resolved end-face pointers", "no invented face ids"],
    },
    {
        "id": "solve_pipeline",
        "tool": "cae.run_simulation_pipeline",
        "prompt": (
            "Run a linear static solve: Al6061-T6 material, fixed support on "
            "FIXED_END, total 50 N downward load on LOAD_END, mesh_size_mm=6, "
            "run_id=value_demo_run_001. Use the copied face pointers. Stop for "
            "normal workbench approval before the solver executes."
        ),
        "expected": [
            "simulation/mesh/mesh.inp",
            "simulation/runs/value_demo_run_001/solver_input.inp",
            "simulation/runs/value_demo_run_001/solver_run.json",
            "simulation/runs/value_demo_run_001/outputs/result.frd",
            "results/computed_metrics.json",
            "results/result_summary.json",
        ],
    },
    {
        "id": "viewer_check",
        "tool": "workbench viewer",
        "prompt": (
            "Open the result fields in the viewer and show von Mises stress and "
            "displacement magnitude. The demo passes only when the field source "
            "is the real FRD result from value_demo_run_001."
        ),
        "expected": [
            "real FRD-derived stress field",
            "real FRD-derived displacement field",
            "no synthetic fallback used",
        ],
    },
    {
        "id": "mesh_convergence",
        "tool": "cae.mesh_convergence",
        "prompt": (
            "Discharge the mesh-dependence caveat: run a convergence study at "
            "mesh_sizes [8.0, 5.0, 3.0]. Report the per-metric GCI and the "
            "Richardson-extrapolated value, and compare it against the "
            "closed-form cantilever stress (M*c/I = 15.0 MPa)."
        ),
        "expected": [
            "three successful solves at progressively finer meshes",
            "gci_fine_percent within the 5% threshold",
            "extrapolated_value close to beam theory",
            "analysis/mesh_convergence_report.json",
            "discretization-uncertainty-only honesty stated",
        ],
    },
    {
        "id": "report",
        "tool": "report.generate",
        "prompt": (
            "Generate the engineering report and write a short plain-language "
            "summary that cites max displacement, max von Mises stress, safety "
            "factor when available, the load case id, and the credibility tier."
        ),
        "expected": [
            "engineering report HTML",
            "metric citations from results/computed_metrics.json",
            "honesty boundaries included",
        ],
    },
)

EXPECTED_EVIDENCE = (
    "geometry/generated.step",
    "geometry/topology_map.json",
    "graph/feature_graph.json",
    "simulation/setup.yaml",
    "simulation/cae_mapping.json",
    "simulation/mesh/mesh.inp",
    "simulation/runs/value_demo_run_001/solver_input.inp",
    "simulation/runs/value_demo_run_001/solver_run.json",
    "simulation/runs/value_demo_run_001/outputs/result.frd",
    "results/computed_metrics.json",
    "results/result_summary.json",
    "engineering report HTML from report.generate or GET /api/projects/{id}/report",
)

PACKAGE_REQUIRED_EVIDENCE = tuple(
    item for item in EXPECTED_EVIDENCE
    if "/" in item and " or " not in item and not item.startswith("engineering report")
)

HONESTY_BOUNDARIES = (
    "The demo is linear static only.",
    "The numbers are mesh-dependent until a convergence study is run.",
    "A CalculiX exit code plus FRD extraction is solver evidence, not certification.",
    "Synthetic fallback fields are a failed demo condition for issue #368.",
    "No physical validation, fatigue, buckling, contact, or bolt preload is claimed.",
)


def build_packet() -> dict[str, Any]:
    return {
        "issue": 368,
        "name": "CAD->CAE value demo: single-solid cantilever",
        "geometry": {
            "kind": "single_connected_solid",
            "dimensions_mm": {"x": 100.0, "y": 20.0, "z": 10.0},
            "cad_code": VALUE_DEMO_CAD_CODE,
        },
        "steps": list(VALUE_DEMO_STEPS),
        "expected_evidence": list(EXPECTED_EVIDENCE),
        "honesty_boundaries": list(HONESTY_BOUNDARIES),
        "recording_checklist": [
            "Create project and CAD in the workbench",
            "Show copied fixed/load face pointers",
            "Show approval before solver execution",
            "Show real FRD fields in the viewer",
            "Show report.generate output with cited metrics and limitations",
        ],
    }


def _read_package_json(zf: zipfile.ZipFile, member: str) -> dict[str, Any] | None:
    try:
        raw = json.loads(zf.read(member).decode("utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _check(status: str, check_id: str, message: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "required": required,
        "message": message,
    }


def check_package(package_path: str | Path) -> dict[str, Any]:
    """Read-only value-demo evidence check for a generated .aieng package."""
    path = Path(package_path)
    checks: list[dict[str, Any]] = []
    if not path.exists():
        return {
            "status": "blocked",
            "package_path": str(path),
            "checks": [_check("fail", "package_exists", f"Package not found: {path}")],
            "missing_evidence": list(PACKAGE_REQUIRED_EVIDENCE),
            "honesty_boundaries": list(HONESTY_BOUNDARIES),
        }

    try:
        with zipfile.ZipFile(path, "r") as zf:
            members = set(zf.namelist())
            checks.append(_check("pass", "package_readable", f"Package is readable ({len(members)} members)."))

            missing = [member for member in PACKAGE_REQUIRED_EVIDENCE if member not in members]
            if missing:
                checks.append(_check("fail", "required_evidence", "Missing required package evidence: " + ", ".join(missing)))
            else:
                checks.append(_check("pass", "required_evidence", "All required package evidence members are present."))

            solver_run = _read_package_json(zf, "simulation/runs/value_demo_run_001/solver_run.json")
            if solver_run is None:
                checks.append(_check("fail", "solver_run_record", "Missing or unreadable solver_run.json for value_demo_run_001."))
            else:
                return_code = solver_run.get("return_code")
                solved = solver_run.get("solved")
                status = str(solver_run.get("status") or solver_run.get("state") or "").lower()
                if return_code == 0 or solved is True or status in {"completed", "ok", "success"}:
                    checks.append(_check("pass", "solver_run_record", "Solver run record indicates a completed run."))
                else:
                    checks.append(_check("fail", "solver_run_record", "Solver run record does not prove successful execution."))

            frd_path = "simulation/runs/value_demo_run_001/outputs/result.frd"
            frd_info = zf.getinfo(frd_path) if frd_path in members else None
            if frd_info and frd_info.file_size > 0:
                checks.append(_check("pass", "real_frd_result", f"FRD result exists and is non-empty ({frd_info.file_size} bytes)."))
            elif frd_info:
                checks.append(_check("fail", "real_frd_result", "FRD result exists but is empty."))
            else:
                checks.append(_check("fail", "real_frd_result", "FRD result is missing."))

            metrics = _read_package_json(zf, "results/computed_metrics.json")
            if metrics is None:
                checks.append(_check("fail", "computed_metrics", "Missing or unreadable results/computed_metrics.json."))
            else:
                metrics_blob = json.dumps(metrics, sort_keys=True).lower()
                has_stress = "von_mises" in metrics_blob or "stress" in metrics_blob
                has_disp = "displacement" in metrics_blob or "disp" in metrics_blob
                if has_stress and has_disp:
                    checks.append(_check("pass", "computed_metrics", "Computed metrics include stress and displacement signals."))
                else:
                    checks.append(_check("warn", "computed_metrics", "Computed metrics exist but stress/displacement signals were not both found."))

            summary = _read_package_json(zf, "results/result_summary.json")
            if summary is None:
                checks.append(_check("fail", "result_summary", "Missing or unreadable results/result_summary.json."))
            else:
                summary_blob = json.dumps(summary, sort_keys=True).lower()
                if "synthetic" in summary_blob and "source_frd" not in summary_blob:
                    checks.append(_check("fail", "viewer_field_source", "Result summary appears synthetic without traceable FRD source."))
                elif "source_frd" in summary_blob or frd_info:
                    checks.append(_check("pass", "viewer_field_source", "Result summary / package can be traced to an FRD result."))
                else:
                    checks.append(_check("warn", "viewer_field_source", "Could not prove viewer field provenance from result_summary alone."))

            report_members = sorted(
                member for member in members
                if member.startswith("reports/") and member.endswith((".html", ".htm"))
            )
            if report_members:
                checks.append(_check("pass", "engineering_report", "Package contains report HTML: " + ", ".join(report_members), required=False))
            else:
                checks.append(_check("warn", "engineering_report", "No report HTML in package; GET /api/projects/{id}/report or report.generate may still satisfy the demo.", required=False))
    except zipfile.BadZipFile:
        checks.append(_check("fail", "package_readable", f"Package is not a readable zip archive: {path}"))

    required_checks = [check for check in checks if check.get("required")]
    failed_required = [check for check in required_checks if check["status"] == "fail"]
    warned = [check for check in checks if check["status"] == "warn"]
    status = "blocked" if failed_required else ("warning" if warned else "pass")
    return {
        "status": status,
        "package_path": str(path),
        "checks": checks,
        "missing_evidence": [
            member for member in PACKAGE_REQUIRED_EVIDENCE
            if any(check["id"] == "required_evidence" and check["status"] == "fail" and member in check["message"] for check in checks)
        ],
        "honesty_boundaries": list(HONESTY_BOUNDARIES),
    }


def build_markdown() -> str:
    packet = build_packet()
    lines = [
        f"# {packet['name']}",
        "",
        "Canonical packet for issue #368. This file is generated from the script",
        "constants; the script itself never runs CAD, meshing, or solver tools.",
        "",
        "## CAD Code",
        "",
        "```python",
        packet["geometry"]["cad_code"].rstrip(),
        "```",
        "",
        "## Prompt / Tool Sequence",
        "",
    ]
    for index, step in enumerate(packet["steps"], start=1):
        lines.extend(
            [
                f"{index}. `{step['tool']}`",
                f"   Prompt: {step['prompt']}",
                "   Expected: " + "; ".join(step["expected"]),
                "",
            ]
        )
    lines.extend(
        [
            "## Evidence Checklist",
            "",
            *[f"- `{item}`" if "/" in item else f"- {item}" for item in packet["expected_evidence"]],
            "",
            "## Honesty Boundaries",
            "",
            *[f"- {item}" for item in packet["honesty_boundaries"]],
            "",
            "## Recording Checklist",
            "",
            *[f"- {item}" for item in packet["recording_checklist"]],
            "",
        ]
    )
    return "\n".join(lines)


def render_check_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Value Demo Evidence Check",
        "",
        f"Package: `{report.get('package_path')}`",
        f"Status: **{report.get('status')}**",
        "",
        "## Checks",
        "",
    ]
    for check in report.get("checks", []):
        marker = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}.get(str(check.get("status")), str(check.get("status")).upper())
        required = "required" if check.get("required") else "advisory"
        lines.append(f"- **{marker}** `{check.get('id')}` ({required}) — {check.get('message')}")
    lines.extend(["", "## Honesty Boundaries", ""])
    lines.extend(f"- {item}" for item in report.get("honesty_boundaries", []))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the issue #368 value-demo packet.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--print-cad-code", action="store_true")
    parser.add_argument("--check-package", help="Read-only evidence check for a generated .aieng package.")
    args = parser.parse_args()

    if args.check_package:
        report = check_package(args.check_package)
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_check_markdown(report))
        return 0 if report["status"] in {"pass", "warning"} else 1
    if args.print_cad_code:
        print(VALUE_DEMO_CAD_CODE, end="")
        return 0
    if args.format == "json":
        print(json.dumps(build_packet(), indent=2, sort_keys=True))
    else:
        print(build_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
