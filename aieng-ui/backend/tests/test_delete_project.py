"""`aieng.delete_project` — irreversible, approval-gated, and untested.

The last untested tool that destroys data. The ordinary path was already
correct: it removes the directory, drops the project from `aieng.list_projects`,
leaves neighbours alone, and refuses an id that names no project (a traversal id
included — `project_dir` matches the id against a strict pattern before it
builds a path, so `../../../etc` never reaches `rmtree`).

The FAILURE path lied. `shutil.rmtree(target, ignore_errors=True)` was followed
by an unconditional `"deleted": True`, and `rmtree` walks the directory in order,
so `metadata.json` went first. Measured with one file undeletable:

    result:   {"status": "ok", "deleted": true, ...}
    on disk:  ['<project>.aieng']          <- the engineering data, still there
    listed:   []                           <- metadata gone, so nothing shows it

The caller is told their project is destroyed while it sits on disk, in a
directory no listing shows, no tool can address, and nothing will ever clean up.
For a delete tool that is the one claim that must never be wrong.

**Why these tests build a project by hand and inject the failure.** The first
version used the real CAD path and a held-open file handle. Both were mistakes:
`build123d` is absent from the ordinary CI lanes, so the module-level skip meant
green CI covered none of this; and POSIX happily unlinks an open file, so on
Linux the "undeletable" file would simply have been deleted. Nothing here reads
geometry — the code under test moves files and reports what happened — so a
synthetic project is the same test, and it runs everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import runtime  # noqa: E402

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def settings(tmp_path: Path):
    from app.app_factory import create_app
    from app.config import Settings

    workspace = tmp_path / "workspace"
    resolved = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    create_app(resolved)
    return resolved


def _project(settings, label: str) -> tuple[str, Path]:
    """A project directory shaped like a real one: package, viewer, metadata."""
    from app.main import default_project, save_project
    from app.project_io import project_dir

    project_id = save_project(settings, default_project(label))["id"]
    folder = project_dir(settings, project_id)
    (folder / f"{project_id}.aieng").write_bytes(b"PK\x03\x04 not really a zip")
    (folder / "viewer").mkdir(exist_ok=True)
    (folder / "viewer" / "model.glb").write_bytes(b"glTF binary preview")
    assert (folder / "metadata.json").exists(), "save_project should have written it"
    return project_id, folder


def _listed() -> list[str]:
    result = runtime.invoke_tool("aieng.list_projects", {})
    projects = result.get("projects") if isinstance(result, dict) else result
    return sorted(p.get("id") for p in (projects or []))


def _refuse_to_unlink(monkeypatch: pytest.MonkeyPatch, doomed: Path) -> list[Path]:
    """Make one TOP-LEVEL file undeletable, on any OS. Returns what it blocked.

    Callers assert the returned list is non-empty. Without that, a guard that
    silently stops matching — as this one did on Linux — turns every test using
    it into a test of the wrong scenario, which is how the first version passed
    on Windows and failed in CI.

    Windows raises here of its own accord when a file is open; POSIX does not.
    Injecting the failure keeps the test measuring the tool's REPORTING rather
    than the platform's file semantics.

    Patched at `os.unlink` rather than `Path.unlink` because that is the call
    the removal loop ends up making. It matches on the absolute path, which is
    only reliable for a top-level entry: POSIX `shutil.rmtree` walks with
    `os.unlink(name, dir_fd=...)`, passing a bare filename, so a nested target
    needs `_rmtree_leaves_behind` below instead. The first version of this
    helper did not, which passed on Windows and failed in CI.
    """
    import os

    real_unlink = os.unlink
    blocked: list[Path] = []

    def guarded(path, *args, **kwargs):
        if Path(path) == doomed:
            blocked.append(Path(path))
            raise PermissionError(f"cannot remove {path}")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", guarded)
    return blocked


def _rmtree_leaves_behind(monkeypatch: pytest.MonkeyPatch, survivor: Path) -> None:
    """Make `shutil.rmtree` unable to fully clear the directory holding `survivor`.

    Patched at `rmtree` rather than inside it, so the simulation does not depend
    on how a platform's implementation walks the tree.
    """
    import shutil

    real_rmtree = shutil.rmtree

    def partial(path, *args, **kwargs):
        if Path(path) == survivor.parent:
            for child in Path(path).iterdir():
                if child != survivor:
                    real_rmtree(child, *args, **kwargs) if child.is_dir() else child.unlink()
            return
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", partial)


def test_a_delete_removes_the_project_and_only_that_project(settings) -> None:
    victim, victim_dir = _project(settings, "to-delete")
    bystander, bystander_dir = _project(settings, "keep-me")

    result = runtime.invoke_tool("aieng.delete_project", {"project_id": victim})

    assert result["status"] == "ok", result
    assert result["deleted"] is True
    assert not victim_dir.exists()
    assert victim not in _listed()

    assert bystander_dir.is_dir(), "a delete must not reach a neighbour"
    assert bystander in _listed()


class TestWhenSomethingCannotBeRemoved:
    def test_a_partial_delete_is_reported_as_one(
        self, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id, folder = _project(settings, "locked")
        package = folder / f"{project_id}.aieng"
        blocked = _refuse_to_unlink(monkeypatch, package)

        result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        assert blocked, "the guard never fired, so this measured a full delete"
        assert result["status"] == "error", result
        assert result["code"] == "partial_delete", result
        assert result["deleted"] is False
        assert package.name in " ".join(result["remaining_files"])
        assert package.exists(), "the payload is what could not be removed"

    def test_the_project_stays_listed_so_the_delete_can_be_retried(
        self, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The half that turns an invisible failure into a retryable one.

        Removing `metadata.json` first left a directory that no listing showed
        and `get_project` answered 404 for — unreachable by every tool,
        including this one.
        """
        project_id, folder = _project(settings, "locked")
        blocked = _refuse_to_unlink(monkeypatch, folder / f"{project_id}.aieng")

        runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        assert blocked, "the guard never fired, so this measured a full delete"
        assert (folder / "metadata.json").exists(), "the index must outlive the payload"
        assert project_id in _listed(), "an unlisted leftover is unreachable"

    def test_retrying_once_the_obstacle_is_gone_completes_it(
        self, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id, folder = _project(settings, "locked")
        with monkeypatch.context() as patched:
            refused = _refuse_to_unlink(patched, folder / f"{project_id}.aieng")
            first = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})
        assert refused, "the guard never fired, so this measured a full delete"
        assert first["code"] == "partial_delete", first

        retried = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})
        assert retried["status"] == "ok", retried
        assert retried["deleted"] is True
        assert not folder.exists()
        assert project_id not in _listed()

    def test_a_file_inside_a_subdirectory_counts_as_remaining(
        self, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id, folder = _project(settings, "stubborn")
        survivor = folder / "viewer" / "model.glb"
        _rmtree_leaves_behind(monkeypatch, survivor)

        result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        assert survivor.exists(), "the setup must hold, or this asserts nothing"
        assert result["deleted"] is False, result
        assert any("viewer" in entry for entry in result["remaining_files"]), result
        assert (folder / "metadata.json").exists()
        assert project_id in _listed()

    def test_an_empty_leftover_directory_counts_as_remaining(
        self, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Counting only FILES let an empty leftover directory read as "gone".

        `remaining` would then be empty, so the final `rmtree` ran and took
        `metadata.json` with it — putting back the unlisted directory the whole
        change exists to prevent. Nothing inside it is a file, which is exactly
        why the file-only filter could not see it.
        """
        import shutil

        project_id, folder = _project(settings, "hollow")
        stubborn = folder / "viewer"
        real_rmtree = shutil.rmtree

        def leave_it_empty(path, *args, **kwargs):
            if Path(path) == stubborn:
                for child in Path(path).iterdir():
                    child.unlink()
                return  # the directory itself survives, holding nothing
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", leave_it_empty)

        result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        assert stubborn.is_dir() and not any(stubborn.iterdir()), "the setup must hold"
        assert result["deleted"] is False, result
        assert result["remaining_files"] == ["viewer"], result
        assert (folder / "metadata.json").exists(), "the index must survive"
        assert project_id in _listed()

def _captured_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Every live event, caught at the sink `_publish_live_event` actually uses.

    Patched on `agent_activity.publish` deliberately: the first version patched
    a name on `app_context` that the publisher — a closure — never reads, so it
    would have passed whether or not the event was sent. The positive test below
    is what proves this observation point is live.
    """
    from app import agent_activity

    seen: list[dict] = []
    monkeypatch.setattr(agent_activity, "publish", seen.append)
    return seen


def test_a_full_delete_announces_itself(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    published = _captured_events(monkeypatch)
    project_id, _folder = _project(settings, "loud")

    runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

    assert [e for e in published if e.get("type") == "project_deleted"], published


class TestTheDeletionEvent:
    def test_no_deletion_event_is_published_for_a_partial_delete(
        self, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A listener that drops the project on this event would desync.

        The project is still on disk and still listed, so announcing it gone is
        the same false claim the return value used to make.
        """
        published = _captured_events(monkeypatch)
        project_id, folder = _project(settings, "quiet")
        blocked = _refuse_to_unlink(monkeypatch, folder / f"{project_id}.aieng")

        runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        assert blocked, "the guard never fired, so this measured a full delete"
        assert not [e for e in published if e.get("type") == "project_deleted"], published
        assert project_id in _listed(), "still there, so nothing should say otherwise"


class TestRecordCleanup:
    """A failed cleanup is not the same answer as "there were none"."""

    def test_a_failed_chat_cleanup_is_not_reported_as_deleted(
        self, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import db

        published = _captured_events(monkeypatch)
        monkeypatch.setattr(
            db, "delete_project_chat",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked")),
        )
        project_id, folder = _project(settings, "chatty")

        result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        # The files really are gone here, so only the records are outstanding —
        # a `project_deleted` event would still be a completed-deletion claim.
        assert not [e for e in published if e.get("type") == "project_deleted"], published
        assert result["status"] == "error", result
        assert result["deleted"] is False
        assert "chat" in result["failed_cleanups"], result
        assert result["chat_rows_removed"] is None, "0 would mean 'there were none'"
        assert "cleanup failed" in result["message"]

    def test_a_successful_delete_still_reports_its_counts(self, settings) -> None:
        project_id, _folder = _project(settings, "counted")
        result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})
        assert result["status"] == "ok", result
        assert result["chat_rows_removed"] == 0
        assert result["autopilot_runs_removed"] == 0
        assert "failed_cleanups" not in result


@pytest.mark.parametrize("project_id", [
    "definitely_not_a_project",
    # `project_dir` matches the id against a strict pattern before it builds a
    # path, so a traversal id never reaches `rmtree`.
    "../../../etc",
    "..",
])
def test_an_id_that_names_no_project_is_refused(settings, project_id: str) -> None:
    result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})
    assert result["status"] == "error", result
    assert result["code"] == "not_found", result


@pytest.mark.parametrize("project_id", ["", "   "])
def test_a_blank_id_is_refused(settings, project_id: str) -> None:
    result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})
    assert result["status"] == "error", result
    assert "project_id" in result["message"]


def test_a_refused_delete_touches_nothing(settings) -> None:
    """The refusals above must be refusals, not quiet partial deletions."""
    keeper, folder = _project(settings, "untouched")
    before = sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))

    for bad in ("definitely_not_a_project", "../../../etc", "", "   "):
        runtime.invoke_tool("aieng.delete_project", {"project_id": bad})

    after = sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))
    assert after == before, json.dumps({"before": before, "after": after})
    assert keeper in _listed()
