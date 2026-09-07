"""`aieng.read_audit_log` read a deleted subsystem's files, not the live trail.

AGENTS.md advertises the tool as "Recent agent/user actions on this project" and
documents `audit_log.jsonl` in the package as the "append-only action history".
Measured across the 50 projects on disk:

| where                                        | projects | entries |
|----------------------------------------------|----------|---------|
| `<project_dir>/logs/*.json` (what it read)   | 6        | —       |
| package `audit_log.jsonl` (what it ignored)  | 2        | 3       |

The 6 are `agent_autopilot_*.json` / `chat_*.json` — leftovers from the in-app
chat and autopilot layer the MCP-first cutover (#17, #8) deleted. The reader
outlived its writer, so every project built through today's path returned
nothing, while the one live writer (`cad.edit_parameter`, the only thing that
appends to `audit_log.jsonl`) wrote a trail nothing read. `stale-artifact` from
the review lens.

Two more, in the same four lines:

- the schema declares `limit` (default 50); the body never read it, so every
  call got `recent_logs`' own default of 8;
- what came back was file *metadata* (name, path, url, size), so even a
  non-empty answer did not answer the question the tool's description asks.

Coverage is now stated rather than implied. Only `cad.edit_parameter` writes
entries, so an empty log means "no parametric edit was recorded" — reporting a
bare `[]` for that is how a reader concludes the project has no history.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="module")
def workbench(tmp_path_factory):
    """A platform root plus a helper that makes projects with real packages."""
    from app import runtime
    from app.app_factory import create_app
    from app.config import Settings
    from app.main import default_project, save_project
    from app.project_io import project_dir

    root = tmp_path_factory.mktemp("audit")
    settings = Settings(
        platform_root=root / "platform",
        workspace_root=root / "workspace",
        data_root=root / "data",
        aieng_root=Path(__file__).resolve().parents[3] / "aieng",
        sample_step=root / "workspace" / "sample.step",
    )
    create_app(settings)

    def make(name: str, *, audit_lines: list[str] | None = None,
             legacy_logs: int = 0) -> str:
        project = save_project(settings, default_project(name))
        pid = project["id"]
        pkg = project_dir(settings, pid) / f"{pid}.aieng"
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"model_id": name}))
            if audit_lines is not None:
                zf.writestr("audit_log.jsonl", "".join(f"{line}\n" for line in audit_lines))
        project["aieng_file"] = pkg.name
        save_project(settings, project)

        if legacy_logs:
            logs = project_dir(settings, pid) / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            for index in range(legacy_logs):
                (logs / f"agent_autopilot_{index}.json").write_text("{}", encoding="utf-8")
        return pid

    def read(pid: str, **extra: Any) -> dict[str, Any]:
        return runtime.invoke_tool(
            "aieng.read_audit_log", {"project_id": pid, **extra}
        )

    return make, read


def _edit_entry(index: int) -> str:
    return json.dumps({
        "timestamp": f"2026-09-07T10:{index:02d}:00+00:00",
        "tool": "cad.edit_parameter",
        "cad_parameter_name": "PLATE_THICKNESS",
        "previous_value": 6.0 + index,
        "new_value": 7.0 + index,
        "action": "accepted_parametric_edit",
    })


# ── the live trail is read ──────────────────────────────────────────────────


def test_the_package_audit_log_is_what_comes_back(workbench) -> None:
    make, read = workbench
    pid = make("has-trail", audit_lines=[_edit_entry(i) for i in range(3)])

    result = read(pid)

    assert result["audit_log_present"] is True
    assert result["entry_count"] == 3
    assert [e["tool"] for e in result["entries"]] == ["cad.edit_parameter"] * 3
    assert result["entries"][0]["cad_parameter_name"] == "PLATE_THICKNESS"


def test_a_package_with_no_audit_log_says_so_rather_than_returning_nothing(
    workbench,
) -> None:
    """`[]` alone reads as "nothing happened". These are different facts."""
    make, read = workbench
    pid = make("no-trail")

    result = read(pid)

    assert result["audit_log_present"] is False
    assert result["entries"] == []
    assert "not that nothing happened" in result["coverage"]
    # And it names where geometry/solver history IS traceable.
    assert "deck_provenance.json" in result["coverage"]


# ── the documented `limit` is actually read ─────────────────────────────────


def test_the_declared_limit_is_honoured_and_returns_the_newest(workbench) -> None:
    make, read = workbench
    pid = make("many", audit_lines=[_edit_entry(i) for i in range(12)])

    result = read(pid, limit=5)

    assert result["limit"] == 5
    assert result["entry_count"] == 5
    assert [e["new_value"] for e in result["entries"]] == [14.0, 15.0, 16.0, 17.0, 18.0], (
        "the newest five, not the oldest"
    )


def test_the_default_limit_is_the_declared_fifty_not_eight(workbench) -> None:
    """`recent_logs`' own default of 8 silently capped every call."""
    make, read = workbench
    pid = make("twenty", audit_lines=[_edit_entry(i) for i in range(20)])

    result = read(pid)

    assert result["limit"] == 50
    assert result["entry_count"] == 20


def test_an_out_of_range_limit_is_clamped_not_rejected(workbench) -> None:
    make, read = workbench
    pid = make("clamped", audit_lines=[_edit_entry(0)])

    assert read(pid, limit=0)["limit"] == 1
    assert read(pid, limit=9999)["limit"] == 500
    assert read(pid, limit="nonsense")["limit"] == 50


# ── honesty about what it cannot parse or does not cover ────────────────────


def test_a_malformed_line_is_reported_rather_than_dropped(workbench) -> None:
    """Skipping it silently would under-report the very history this tool shows."""
    make, read = workbench
    pid = make("broken", audit_lines=[_edit_entry(0), "{not json", _edit_entry(1)])

    result = read(pid)

    assert result["entry_count"] == 3
    assert any("unparsed_line" in entry for entry in result["entries"])


def test_legacy_chat_log_files_are_still_reported_but_not_as_actions(
    workbench,
) -> None:
    """Pre-cutover projects keep their files; they are just not the audit trail."""
    make, read = workbench
    pid = make("legacy", legacy_logs=4)

    result = read(pid)

    assert result["legacy_log_file_count"] == 4
    assert len(result["recent_logs"]) == 4
    assert result["entries"] == [], "those files are not recorded actions"
    assert "removed in-app" in result["coverage"]


def test_a_call_without_a_project_id_is_an_error_not_an_empty_history(
    workbench,
) -> None:
    _, read = workbench
    from app import runtime

    result = runtime.invoke_tool("aieng.read_audit_log", {})

    assert result["status"] == "error"
    assert result["code"] == "missing_project_id"
