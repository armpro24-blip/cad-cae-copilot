"""`aieng.delete_project` — irreversible, approval-gated, and untested.

The last untested tool that destroys data. The ordinary path was already
correct: it removes the directory, drops the project from `aieng.list_projects`,
leaves neighbours alone, and refuses an id that names no project (a traversal id
included — `project_dir` validates against a strict pattern before touching the
filesystem).

The FAILURE path lied. `shutil.rmtree(target, ignore_errors=True)` was followed
by an unconditional `"deleted": True`, and `rmtree` walks the directory in order,
so `metadata.json` went first. Measured with one `.aieng` package held open — an
ordinary Windows file lock:

    result:   {"status": "ok", "deleted": true, ...}
    on disk:  ['<project>.aieng']          <- the engineering data, still there
    listed:   []                           <- metadata gone, so nothing shows it

The caller is told their project is destroyed while it sits on disk, in a
directory no listing shows and no tool can address, that nothing will ever clean
up. For a delete tool that is the one claim that must never be wrong.

Two halves to the fix, and both are load-bearing:
  * `metadata.json` is removed LAST, and only once everything else is gone — so
    a failure leaves the project listed and the delete retryable;
  * the result is verified rather than asserted, so a partial delete reports
    `code: "partial_delete"` and names what is left.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("build123d", reason="building a project needs the CAD stack")

from app import cad_generation, runtime  # noqa: E402

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

_CODE = (
    "from build123d import *\n"
    "b = Box(30, 20, 10)\n"
    "b.label = 'body'\n"
    "result = Compound(children=[b])\n"
)


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


def _build(settings, label: str) -> str:
    """A project with real geometry — an empty one would not exercise the files."""
    from app.main import default_project, save_project

    project_id = save_project(settings, default_project(label))["id"]
    built = cad_generation.execute_build123d_code(
        settings, project_id, {"code": _CODE, "timeout": 180}
    )
    assert built["status"] == "ok", built
    return project_id


def _listed() -> list[str]:
    result = runtime.invoke_tool("aieng.list_projects", {})
    projects = result.get("projects") if isinstance(result, dict) else result
    return sorted(p.get("id") for p in (projects or []))


def test_a_delete_removes_the_project_and_only_that_project(settings) -> None:
    from app.project_io import project_dir

    victim = _build(settings, "to-delete")
    bystander = _build(settings, "keep-me")

    result = runtime.invoke_tool("aieng.delete_project", {"project_id": victim})

    assert result["status"] == "ok", result
    assert result["deleted"] is True
    assert not project_dir(settings, victim).exists()
    assert victim not in _listed()

    assert project_dir(settings, bystander).is_dir(), "a delete must not reach a neighbour"
    assert bystander in _listed()


class TestWhenAFileCannotBeRemoved:
    """An open handle is the ordinary case on Windows, not an exotic one."""

    @staticmethod
    def _locked_project(settings) -> tuple[str, Path, Path]:
        from app.project_io import project_dir

        project_id = _build(settings, "locked")
        folder = project_dir(settings, project_id)
        package = next(folder.glob("*.aieng"))
        return project_id, folder, package

    def test_a_partial_delete_is_reported_as_one(self, settings) -> None:
        project_id, folder, package = self._locked_project(settings)

        with package.open("rb"):
            result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        assert result["status"] == "error", result
        assert result["code"] == "partial_delete", result
        assert result["deleted"] is False
        assert package.name in " ".join(result["remaining_files"])
        assert folder.exists()

    def test_the_project_stays_listed_so_the_delete_can_be_retried(self, settings) -> None:
        """The half that turns an invisible failure into a retryable one.

        Removing `metadata.json` first left a directory that no listing showed
        and `get_project` answered 404 for — unreachable by every tool,
        including this one.
        """
        project_id, folder, package = self._locked_project(settings)

        with package.open("rb"):
            runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        assert (folder / "metadata.json").exists(), "the index must outlive the payload"
        assert project_id in _listed(), "an unlisted leftover is unreachable"

    def test_retrying_once_the_lock_is_gone_completes_it(self, settings) -> None:
        project_id, folder, package = self._locked_project(settings)

        with package.open("rb"):
            runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        retried = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})
        assert retried["status"] == "ok", retried
        assert retried["deleted"] is True
        assert not folder.exists()
        assert project_id not in _listed()

    def test_the_records_it_did_remove_are_still_reported(self, settings) -> None:
        """A partial delete is not a no-op — chat rows and runs are already gone."""
        project_id, _folder, package = self._locked_project(settings)

        with package.open("rb"):
            result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})

        assert "chat_rows_removed" in result and "autopilot_runs_removed" in result, result


@pytest.mark.parametrize("project_id,code", [
    ("definitely_not_a_project", "not_found"),
    # `project_dir` matches the id against a strict pattern before it builds a
    # path, so a traversal id never reaches `rmtree`.
    ("../../../etc", "not_found"),
    ("..", "not_found"),
])
def test_an_id_that_names_no_project_is_refused(settings, project_id: str, code: str) -> None:
    result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})
    assert result["status"] == "error", result
    assert result["code"] == code, result


@pytest.mark.parametrize("project_id", ["", "   "])
def test_a_blank_id_is_refused(settings, project_id: str) -> None:
    result = runtime.invoke_tool("aieng.delete_project", {"project_id": project_id})
    assert result["status"] == "error", result
    assert "project_id" in result["message"]


def test_a_refused_delete_touches_nothing(settings) -> None:
    """The refusals above must be refusals, not quiet partial deletions."""
    from app.project_io import project_dir

    keeper = _build(settings, "untouched")
    before = sorted(p.name for p in project_dir(settings, keeper).iterdir())

    for bad in ("definitely_not_a_project", "../../../etc", "", "   "):
        runtime.invoke_tool("aieng.delete_project", {"project_id": bad})

    after = sorted(p.name for p in project_dir(settings, keeper).iterdir())
    assert after == before, json.dumps({"before": before, "after": after})
    assert keeper in _listed()
