"""The release path must work through the channels this project actually uses.

Two defects sit behind these tests, both live on `main` until #510:

1. `release.yml` could not cut a release. `github-release` needed
   `verify-published-install`, which needs the publish jobs, which need PyPI
   Trusted Publishing that is deliberately not configured (#273). So the
   documented release automation was unreachable and both alphas were tagged by
   hand — an `undocumented-path` in the release machinery itself.

2. Neither release had an attached asset, so the one per-artifact download
   counter these channels can offer did not exist, and the embedding-depth
   baseline read `unknown / TBD` on every row.

These are static checks on the workflow and the baseline doc. They prove the
wiring, not that a release succeeded.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

yaml = pytest.importorskip("yaml")

_REPO = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO / ".github" / "workflows" / "release.yml"
_BASELINE_DOC = _REPO / "aieng" / "docs" / "release" / "embedding_depth_baseline.md"
_CAPTURE_SCRIPT = _REPO / "scripts" / "capture_embedding_depth_baseline.py"
_PUBLISH_JOBS = ("publish-aieng-format", "publish-aieng-workbench-mcp",
                 "verify-published-install")


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _jobs() -> dict:
    return _workflow()["jobs"]


def _dispatch_inputs() -> dict:
    # PyYAML parses the bare key `on:` as the boolean True.
    triggers = _workflow()[True]
    return triggers["workflow_dispatch"]["inputs"]


def test_a_release_can_be_cut_without_publishing_to_an_index() -> None:
    target = _dispatch_inputs()["target"]
    assert "none" in target["options"], (
        "there must be a way to run the release workflow without publishing — "
        "PyPI is out of scope (#273) and a tag must still be able to cut a release"
    )
    assert target["default"] == "none", (
        "the default must be the supported path; defaulting to an index publish "
        "makes the common case the one that cannot work"
    )


@pytest.mark.parametrize("job_name", _PUBLISH_JOBS)
def test_index_publishing_is_gated_on_the_target(job_name: str) -> None:
    condition = _jobs()[job_name].get("if", "")
    assert "target != 'none'" in condition, (
        f"{job_name} must not run when nothing is being published; found: "
        f"{condition!r}"
    )


def test_the_github_release_survives_a_skipped_publish_chain() -> None:
    """The actual bug: a skipped `needs` skips the dependent job by default."""
    condition = _jobs()["github-release"].get("if", "")
    assert "!cancelled()" in condition or "always()" in condition, (
        "github-release depends on the publish chain, so without !cancelled() / "
        "always() it is skipped whenever that chain is skipped — which is every "
        "release under the current distribution decision"
    )
    assert "verify-published-install.result" in condition, (
        "relaxing the gate must still inspect the verification's outcome; see "
        "test_a_failed_publish_does_not_get_a_release_announcing_it for which "
        "outcomes are acceptable on which path"
    )


def test_the_release_attaches_the_built_dists() -> None:
    """Wheels on the release are an index-free install path and a counter."""
    job = _jobs()["github-release"]
    steps = job["steps"]
    assert any("download-artifact" in str(step.get("uses", "")) for step in steps), (
        "github-release must fetch the dists built by the build job"
    )
    create = "\n".join(str(step.get("run", "")) for step in steps)
    assert "gh release create" in create
    assert "$assets" in create and "*.whl" in create, (
        "the built wheels must be passed to `gh release create`, or the release "
        "carries no downloadable artifact and no download counter"
    )
    assert "no dists to attach" in create, (
        "an empty dist set must fail loudly; silently creating an assetless "
        "release is how this went unnoticed in the first place"
    )


# ── the baseline the release gate points at ──────────────────────────────────

def _capture_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_capture", _CAPTURE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the script's dataclasses resolve their string
    # annotations (`from __future__ import annotations`) through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_an_unmeasurable_signal_is_never_rendered_as_a_number() -> None:
    """`unmeasurable` and `0` are different claims.

    Reporting "we have no counter" as zero would read as "nobody uses it" —
    the same shape as the release docs promising a publication that was never
    coming. This is the one behavioural rule of the capture script, so it is
    tested on the pure objects rather than by calling the GitHub API.
    """
    capture = _capture_module()

    blind = capture.Signal("x", "X", "nowhere", unmeasurable_reason="no counter exists")
    assert blind.measurable is False
    assert blind.rendered_value() == "unmeasurable"
    assert blind.to_dict()["value"] is None
    assert blind.to_dict()["unmeasurable_reason"]

    real = capture.Signal("y", "Y", "somewhere", 0)
    assert real.measurable is True
    assert real.rendered_value() == "0", "a measured zero must stay a zero"

    payload = {
        "captured_on": "2026-01-01",
        "signals": {"x": blind.to_dict(), "y": real.to_dict()},
    }
    table = capture._render_markdown(payload)
    assert "**unmeasurable**" in table
    assert re.search(r"\|\s*Y\s*\|\s*0\s*\|", table)


def test_the_baseline_doc_is_dated_and_names_its_gaps() -> None:
    text = _BASELINE_DOC.read_text(encoding="utf-8")

    assert re.search(r"captured 20\d\d-\d\d-\d\d", text), (
        "a rolling-window number without its capture date is not a measurement"
    )
    assert "scripts/capture_embedding_depth_baseline.py" in text, (
        "the doc must say how to recapture, or the next reading is a new invention"
    )
    assert "Unmeasurable is not zero" in text
    for gap in ("GHCR image pulls", "`.aieng` packages created",
                "Third-party MCP integrations"):
        assert gap in text, f"the doc must account for {gap}"
    assert "No analytics in the product" in text, (
        "the non-goal is what keeps 'unmeasurable' an accepted answer"
    )


def test_the_release_gate_points_at_the_baseline_instead_of_the_dead_table() -> None:
    gate = (
        _REPO / "aieng" / "docs" / "release" / "current_alpha_release_gate.md"
    ).read_text(encoding="utf-8")

    assert "embedding_depth_baseline.md" in gate
    assert "PyPI/TestPyPI stats" not in gate, (
        "the old metrics table sourced every row from an index this project does "
        "not publish to, so every row was permanently 'unknown / TBD'"
    )


def test_a_failed_publish_does_not_get_a_release_announcing_it() -> None:
    """Skipped-because-deliberate and skipped-because-upstream-failed differ.

    The first version of this gate accepted any non-`failure` verify result. But
    a FAILED publish job leaves `verify-published-install` *skipped* (its own
    `needs` failed), so that gate would have cut a release announcing a publish
    that never happened — while the test above, which only checks the
    `!= 'failure'` clause, passed. Same coverage-boundary shape as the defect
    this file documents, one level up.
    """
    condition = _jobs()["github-release"].get("if", "")
    normalised = " ".join(condition.split())

    assert "target == 'none'" in normalised and "result == 'skipped'" in normalised, (
        "the no-publish path must require the verify job to be SKIPPED"
    )
    assert "target != 'none'" in normalised and "result == 'success'" in normalised, (
        "the publish path must require the verify job to have SUCCEEDED, not "
        "merely to have avoided the 'failure' conclusion"
    )


def test_the_capture_cli_renders_every_documented_form(monkeypatch, capsys) -> None:
    """The three documented invocations, without touching the network."""
    import json as _json

    capture = _capture_module()
    monkeypatch.setattr(capture, "_gh_api", lambda path: None)

    for argv, expect in (
        ([], "Embedding-depth baseline — captured"),
        (["--markdown"], "| Signal | Value | Window | Source |"),
    ):
        assert capture.main(argv) == 0
        assert expect in capsys.readouterr().out

    assert capture.main(["--json"]) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["channels"]["not_used"] == ["PyPI", "TestPyPI"]
    # Every API call was refused, so every signal must say so rather than
    # reporting a number — the script's one behavioural rule, end to end.
    assert all(
        entry.get("unmeasurable_reason") for entry in payload["signals"].values()
    ), payload["signals"]
    assert all(entry["value"] is None for entry in payload["signals"].values())
