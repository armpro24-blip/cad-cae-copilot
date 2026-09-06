from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "run_real_ccx_verification_gate.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "real-ccx-verification.yml"
_SPEC = importlib.util.spec_from_file_location("real_ccx_gate", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gate
_SPEC.loader.exec_module(gate)


def test_split_ccx_command_defaults_to_ccx() -> None:
    assert gate.split_ccx_command(None) == ["ccx"]
    assert gate.split_ccx_command("") == ["ccx"]


def test_split_ccx_command_preserves_conda_launcher() -> None:
    assert gate.split_ccx_command("conda run -n calculix-env ccx") == [
        "conda",
        "run",
        "-n",
        "calculix-env",
        "ccx",
    ]


def test_build_pythonpath_contains_core_and_backend_paths() -> None:
    path = gate.build_pythonpath(_ROOT, "existing")
    parts = path.split(os.pathsep)
    assert str(_ROOT / "aieng" / "src") in parts
    assert str(_ROOT / "aieng") in parts
    assert str(_ROOT / "aieng-ui" / "backend") in parts
    assert "existing" in parts


def test_parse_junit_summary_counts_root_testsuite(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        '<testsuite tests="3" failures="1" errors="0" skipped="1"></testsuite>',
        encoding="utf-8",
    )
    summary = gate.parse_junit_summary(junit)
    assert summary.tests == 3
    assert summary.failures == 1
    assert summary.errors == 0
    assert summary.skipped == 1


def test_parse_junit_summary_counts_testsuites_children(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        """
        <testsuites>
          <testsuite tests="2" failures="0" errors="0" skipped="1"></testsuite>
          <testsuite tests="4" failures="1" errors="1" skipped="0"></testsuite>
        </testsuites>
        """,
        encoding="utf-8",
    )
    summary = gate.parse_junit_summary(junit)
    assert summary.tests == 6
    assert summary.failures == 1
    assert summary.errors == 1
    assert summary.skipped == 1


def test_selected_targets() -> None:
    """"all" means every registered target, not a list that happens to match.

    This asserted the two labels literally, which pinned the defect as the
    expectation: `selected_targets("all")` named two while `TARGETS` held three,
    CI runs that default, and the acceptance suite was registered without ever
    running — with the lane green. A literal list here would have had to be
    edited to allow the fix, which is the tell.
    """
    assert list(gate.selected_targets("all")) == list(gate.TARGETS.values())
    assert gate.selected_targets("nafems") == [gate.TARGETS["nafems"]]
    for name in gate.TARGETS:
        assert gate.selected_targets(name) == [gate.TARGETS[name]]


def test_main_requires_resolvable_ccx_unless_skips_are_allowed(monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("AIENG_CCX_CMD", "/definitely/missing/ccx")
    monkeypatch.setattr(gate, "run_target", lambda *args, **kwargs: calls.append(args) or 0)
    assert gate.main(["--suite", "nafems"]) == 2
    assert calls == []
    assert gate.main(["--suite", "nafems", "--allow-skips"]) == 0
    assert len(calls) == 1


def test_real_ccx_workflow_is_not_manual_only() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "pull_request:" in text
    assert "push:" in text
    assert "aieng/src/aieng/nafems_verification.py" in text
    assert "aieng-ui/backend/app/simulation_runner.py" in text
    assert "reference calibration is being finished" not in text
