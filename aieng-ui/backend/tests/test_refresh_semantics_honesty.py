"""`aieng.refresh_semantics` must not claim, or do, more than it does.

Dogfooding it — a tool named by no test, documented in four places — found a
recipe that was a no-op and a read-only-sounding call that was destructive:

* AGENTS.md prescribed it as step 1 of the fix for a stale-artifact "hard
  blocker". Measured: the marker and `geometry_modified: true` survive it
  untouched. Only a successful CAD write clears them, and `cad.edit_parameter`
  — the very edit that sets the flag — is not one of the writers that does.
* Its registered description promised to "refresh semantic state (face labels,
  feature graph, stale-artifact flags)". It refreshes none of them; it runs the
  package's schema + rule validation.
* On failure it wrote `status = "validation_failed"` over `viewer_ready_glb`,
  which the sidebar renders as "Needs attention". Every one of eight real
  agent-built packages fails that validation today (#513), so the documented
  recovery marked healthy projects broken while fixing nothing.

These tests pin the two behaviours that matter: it must not degrade the project,
and it must say what actually failed.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings

_STALE_MEMBER = "state/revalidation_status.json"
_STALE = {
    "schema_version": "0.1",
    "geometry_modified": True,
    "geometry_revision": 2,
    "last_validated_geometry_revision": 1,
    "triggering_tool": "cad.edit_parameter",
    "stale_artifacts": ["simulation/cae_mapping.json"],
}


def _package_bytes() -> dict[str, str]:
    """A minimal package that is a real zip but not schema-conformant.

    Deliberately not a fixture copied from a passing example: the point is what
    happens to a project whose package FAILS validation, which is every
    agent-built package until #513 is fixed.
    """
    return {
        "manifest.json": json.dumps({"schema_version": "0.1", "resources": {}}),
        "geometry/topology_map.json": json.dumps({"entities": []}),
        _STALE_MEMBER: json.dumps(_STALE),
    }


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, str, Path]:
    data_root = tmp_path / "data"
    project_id = "abc123def456"
    project_dir = data_root / "projects" / project_id
    project_dir.mkdir(parents=True)

    package = project_dir / f"{project_id}.aieng"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in _package_bytes().items():
            zf.writestr(name, payload)

    (project_dir / "metadata.json").write_text(
        json.dumps({
            "id": project_id,
            "name": "probe",
            "status": "viewer_ready_glb",
            "aieng_file": f"{project_id}.aieng",
            "last_error": None,
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("AIENG_PLATFORM_DATA", str(data_root))
    settings = Settings.from_env()
    # platform_logic reads symbols injected by app.main.
    from app import main as app_main  # noqa: F401  (import for its side effect)

    return settings, project_id, project_dir


def _metadata(project_dir: Path) -> dict[str, Any]:
    return json.loads((project_dir / "metadata.json").read_text(encoding="utf-8"))


def _members(project_dir: Path, project_id: str) -> set[str]:
    with zipfile.ZipFile(project_dir / f"{project_id}.aieng") as zf:
        return set(zf.namelist())


def test_validation_does_not_degrade_a_viewer_ready_project(project) -> None:
    """A read-only re-validation must not mark the project broken.

    Schema conformance and "is there a viewable model" are different axes. The
    sidebar reads `status` / `last_error`; a failing validation wrote both.
    """
    settings, project_id, project_dir = project
    from app import main as app_main

    result = app_main.validate_aieng_file(settings, project_id)

    assert result["ok"] is False, "fixture is deliberately non-conformant"
    after = _metadata(project_dir)
    assert after["status"] == "viewer_ready_glb", (
        "re-validating must not overwrite the lifecycle status; it turned "
        "'Model ready' into 'Needs attention' while fixing nothing"
    )
    assert not after.get("last_error"), (
        "last_error alone forces the sidebar into its error tone"
    )
    assert after["last_validation_ok"] is False, (
        "the verdict must still be recorded — in the field that means it"
    )


def test_the_failure_names_the_artifacts_that_failed(project) -> None:
    """`ok: false` with 117 undifferentiated messages is not actionable."""
    settings, project_id, _ = project
    from app import main as app_main

    summary = app_main.validate_aieng_file(settings, project_id)["validation_summary"]

    assert summary["failure_count"] > 0
    members = {entry["member"] for entry in summary["failing_artifacts"]}
    assert "manifest.json" in members, members
    for entry in summary["failing_artifacts"]:
        assert entry["failures"] >= 1
        assert entry["first"], "each group must carry an example message"
    counts = [entry["failures"] for entry in summary["failing_artifacts"]]
    assert counts == sorted(counts, reverse=True), "worst offender first"


def test_it_does_not_clear_the_stale_marker_it_was_documented_to_clear(project) -> None:
    """The measured no-op: the whole reason this tool was dogfooded."""
    settings, project_id, project_dir = project
    from app import main as app_main

    app_main.validate_aieng_file(settings, project_id)

    assert _STALE_MEMBER in _members(project_dir, project_id)
    with zipfile.ZipFile(project_dir / f"{project_id}.aieng") as zf:
        assert json.loads(zf.read(_STALE_MEMBER))["geometry_modified"] is True


def test_it_writes_no_semantic_artifact(project) -> None:
    """"Re-extract semantic labels" was a promise nothing kept."""
    settings, project_id, project_dir = project
    from app import main as app_main

    before = _members(project_dir, project_id)
    app_main.validate_aieng_file(settings, project_id)
    assert _members(project_dir, project_id) == before


def test_the_tool_description_matches_what_it_does() -> None:
    """The description an external agent reads is part of the contract."""
    from app.runtime_tool_schemas import TOOL_SCHEMAS  # noqa: F401  (import check)
    from app import runtime_registry

    source = Path(runtime_registry.__file__).parent.joinpath("aieng.py").read_text(
        encoding="utf-8"
    )
    start = source.index('"aieng.refresh_semantics",')
    description = source[start:start + 900]

    assert "does NOT re-extract semantic labels" in description
    assert "clear stale-artifact flags" in description
    for promise in ("Call this after any geometry edit to clear EDIT IMPACT",):
        assert promise not in description, f"the old claim is back: {promise}"


def test_the_guide_no_longer_prescribes_it_as_the_stale_fix() -> None:
    """Docs are a contract an agent acts on; this recipe was a no-op."""
    repo_root = Path(__file__).resolve().parents[3]
    guide = (repo_root / "AGENTS.md").read_text(encoding="utf-8")

    stale_section = guide[guide.index("## Stale-artifact warnings"):]
    stale_section = stale_section[: stale_section.index("\n---")]

    assert "aieng.refresh_semantics` does not clear it" in stale_section, (
        "the section must say what the tool actually does"
    )
    recipe = stale_section[stale_section.index("```text"): stale_section.index("```\n", 20)]
    assert "refresh_semantics" not in recipe, (
        "the numbered recovery must not start with a step that changes nothing"
    )
    assert "cae.prepare_solver_run" in recipe
