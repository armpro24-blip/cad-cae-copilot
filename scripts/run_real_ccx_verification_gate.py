#!/usr/bin/env python3
"""Run the real-CalculiX verification gate.

This is intentionally separate from the normal focused CI suite. It is for
machines that have a real ``ccx`` executable and the optional CAD/mesh stack
available, and it treats skipped tests as a gate failure by default so a missing
solver cannot produce a false green run.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PytestTarget:
    label: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class JUnitSummary:
    tests: int
    failures: int
    errors: int
    skipped: int


TARGETS: dict[str, PytestTarget] = {
    "nafems": PytestTarget(
        label="NAFEMS real-ccx numerical verification",
        args=("aieng/tests/test_nafems_verification.py", "-q", "-k", "real_ccx"),
    ),
    "backend": PytestTarget(
        label="Backend CAD->CAE real-ccx solve loop",
        args=("aieng-ui/backend/tests/test_cae_solve_integration.py", "-q"),
    ),
    # The whole promised task, through the tool surface: build, set up, solve,
    # edit, re-solve, compare, export. It belongs in the gate rather than the
    # ordinary lanes because a skip here must not read as a pass — an
    # acceptance run that quietly does not run is the failure it exists to
    # catch.
    "acceptance": PytestTarget(
        label="Acceptance: edit -> re-solve -> compare -> export",
        args=("aieng-ui/backend/tests/test_acceptance_edit_resolve_compare.py", "-q"),
    ),
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def split_ccx_command(value: str | None) -> list[str]:
    if value and value.strip():
        return shlex.split(value)
    return ["ccx"]


def command_executable_exists(command: list[str]) -> bool:
    if not command:
        return False
    first = command[0]
    if os.path.isabs(first):
        return Path(first).exists()
    return shutil.which(first) is not None


def build_pythonpath(root: Path, existing: str | None) -> str:
    entries = [
        root / "aieng" / "src",
        root / "aieng",
        root / "aieng-ui" / "backend",
    ]
    parts = [str(p) for p in entries]
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def parse_junit_summary(path: Path) -> JUnitSummary:
    root = ET.parse(path).getroot()
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = root.findall("testsuite")
        if not suites:
            suites = [root]

    def _sum_attr(name: str) -> int:
        return sum(int(suite.attrib.get(name, "0")) for suite in suites)

    return JUnitSummary(
        tests=_sum_attr("tests"),
        failures=_sum_attr("failures"),
        errors=_sum_attr("errors"),
        skipped=_sum_attr("skipped"),
    )


def selected_targets(suite: str) -> list[PytestTarget]:
    if suite == "all":
        return [TARGETS["nafems"], TARGETS["backend"]]
    return [TARGETS[suite]]


def run_target(
    target: PytestTarget,
    *,
    env: dict[str, str],
    allow_skips: bool,
) -> int:
    with tempfile.TemporaryDirectory(prefix="aieng-real-ccx-") as tmp:
        junit = Path(tmp) / "pytest.xml"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            *target.args,
            "--junitxml",
            str(junit),
        ]
        print(f"\n==> {target.label}")
        print("+ " + " ".join(shlex.quote(part) for part in cmd))
        completed = subprocess.run(cmd, cwd=repo_root(), env=env, text=True)
        if not junit.exists():
            print(f"ERROR: pytest did not write JUnit XML for {target.label}", file=sys.stderr)
            return completed.returncode or 1
        summary = parse_junit_summary(junit)
        print(
            "summary: "
            f"tests={summary.tests} failures={summary.failures} "
            f"errors={summary.errors} skipped={summary.skipped}"
        )
        if completed.returncode != 0:
            return completed.returncode
        if summary.tests == 0:
            print(f"ERROR: {target.label} selected zero tests.", file=sys.stderr)
            return 1
        if summary.skipped and not allow_skips:
            print(
                f"ERROR: {target.label} skipped {summary.skipped} test(s). "
                "This gate requires a fully configured real solver environment.",
                file=sys.stderr,
            )
            return 1
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=("all", "nafems", "backend", "acceptance"),
        default="all",
        help="Verification suite to run.",
    )
    parser.add_argument(
        "--ccx-cmd",
        default=None,
        help="CalculiX command to expose as AIENG_CCX_CMD, e.g. 'conda run -n calculix-env ccx'.",
    )
    parser.add_argument(
        "--allow-skips",
        action="store_true",
        help="Allow pytest skips. Useful for exploratory local checks, not for CI gates.",
    )
    args = parser.parse_args(argv)

    root = repo_root()
    ccx_value = args.ccx_cmd or os.environ.get("AIENG_CCX_CMD") or "ccx"
    ccx_command = split_ccx_command(ccx_value)
    if not command_executable_exists(ccx_command) and not args.allow_skips:
        print(
            "ERROR: CalculiX command is not resolvable. "
            f"Set AIENG_CCX_CMD or pass --ccx-cmd. Got: {ccx_value!r}",
            file=sys.stderr,
        )
        return 2
    if not command_executable_exists(ccx_command):
        print(
            "WARNING: CalculiX command is not resolvable; pytest may skip real solver tests.",
            file=sys.stderr,
        )

    env = os.environ.copy()
    env["AIENG_CCX_CMD"] = ccx_value
    env["PYTHONPATH"] = build_pythonpath(root, env.get("PYTHONPATH"))

    rc = 0
    for target in selected_targets(args.suite):
        target_rc = run_target(target, env=env, allow_skips=args.allow_skips)
        if target_rc != 0 and rc == 0:
            rc = target_rc
    if rc == 0:
        print("\nReal-ccx verification gate passed.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
