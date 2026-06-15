"""Smoke tests for the regression benchmark runner."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REGRESSION_DIR = Path(__file__).resolve().parents[1]
RUNNER = REGRESSION_DIR / "runner.py"
COMPARE = REGRESSION_DIR / "compare.py"


@pytest.fixture
def clean_runs(tmp_path: Path) -> Path:
    """Use a temporary runs directory for each test."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return runs_dir


def test_runner_core_prompts(clean_runs: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--tags", "core", "--output", str(clean_runs / "run1")],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    manifest_path = clean_runs / "run1" / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert not any(p["status"] == "failed" for p in manifest["prompts"])
    assert any(p["status"] == "passed" for p in manifest["prompts"])


def test_runner_all_prompts_load(clean_runs: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--tags", "all", "--output", str(clean_runs / "run2")],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((clean_runs / "run2" / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["prompts"]) == 22
    passed = sum(1 for p in manifest["prompts"] if p["status"] == "passed")
    skipped = sum(1 for p in manifest["prompts"] if p["status"] == "skipped")
    assert passed == 5
    assert skipped == 17


def test_compare_runs(clean_runs: Path) -> None:
    subprocess.run(
        [sys.executable, str(RUNNER), "--tags", "core", "--output", str(clean_runs / "base")],
        check=True,
        timeout=180,
    )
    subprocess.run(
        [sys.executable, str(RUNNER), "--tags", "core", "--output", str(clean_runs / "curr")],
        check=True,
        timeout=180,
    )
    result = subprocess.run(
        [
            sys.executable,
            str(COMPARE),
            "--baseline",
            str(clean_runs / "base"),
            "--current",
            str(clean_runs / "curr"),
            "--output",
            str(clean_runs / "diff.md"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (clean_runs / "diff.md").exists()
