"""The point-and-shoot loop, and what a geometry edit records.

`aieng.apply_shape_ir_patch` was named by no test — #521 covered the converter,
not the MCP handler, which additionally commits the patched IR, recompiles,
publishes the viewer preview and persists the report. Dogfooding it produced two
defects, one of them in a neighbouring tool.

**The documented loop did not close.** AGENTS.md calls
`cad.list_editable_parameters` the "point" of point-and-shoot — read it, then
pass a parameter to `cad.edit_parameter` — and says each entry carries
`featureId`/`parameterName`. It carried `feature_id`/`parameter_name`, so
passing an entry through as documented returned `invalid_contract`.

**A geometry-replacing patch recorded nothing.** `cad.edit_parameter` and
`opt.writeback_to_shape_ir` both write `state/revalidation_status.json`;
`aieng.apply_shape_ir_patch` recompiles the body from the patched Shape IR — the
compiled source and the STEP both change — and recorded no edit, so
`agent_context.edit_impact` stayed `available: false` and no warning reached the
agent. AGENTS.md: "Every tool that changes geometry records it".

A note for whoever measures this next: `GeometryCache` lives in a
repo-relative `.aieng_cache/`, shared by every run. A first attempt here
concluded the patch changed nothing at all — it was reading a cached build of an
identical earlier probe. Run from a fresh cwd, or vary a dimension.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("build123d", reason="this loop needs the real CAD stack")

from app import cad_generation, runtime  # noqa: E402

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

_CODE = (
    "from build123d import *\n"
    "PLATE_LENGTH = 60.0\n"
    "base = Box(PLATE_LENGTH, 40, 8)\n"
    "base.label = 'base_plate'\n"
    "result = Compound(children=[base])\n"
)

_SHAPE_IR = {
    "format_version": "0.1.0",
    "model_id": "beam",
    "representation": "brep_build123d",
    "parts": [{"id": "beam", "kind": "rounded_box", "dimensions": [62.0, 22.0, 12.0],
               "radius": 2.0, "parameters": {"radius": 2.0}}],
}


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Settings plus a private geometry cache.

    `GeometryCache` resolves `.aieng_cache` relative to the CWD, so without this
    the tests share one cache with every other run on the machine — and a cache
    hit would let a broken recompile look like a working one.
    """
    from app.app_factory import create_app
    from app.config import Settings

    monkeypatch.chdir(tmp_path)  # so `.aieng_cache` is this test's own
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


def _new_project(settings, label: str) -> str:
    from app.main import default_project, save_project

    return save_project(settings, default_project(label))["id"]


def _revalidation(package: Path) -> dict | None:
    with zipfile.ZipFile(package) as zf:
        if "state/revalidation_status.json" not in zf.namelist():
            return None
        return json.loads(zf.read("state/revalidation_status.json"))


def test_a_listed_parameter_can_be_passed_straight_to_the_edit_tool(settings) -> None:
    """The documented loop, executed exactly as documented.

    Deliberately reads ONLY `featureId` / `parameterName` — the keys AGENTS.md
    names. Falling back to the snake_case ones would make this test pass against
    the bug it exists to catch.
    """
    from app.project_io import project_dir

    project_id = _new_project(settings, "point-and-shoot")
    built = cad_generation.execute_build123d_code(
        settings, project_id, {"code": _CODE, "timeout": 180}
    )
    assert built["status"] == "ok", built

    listed = runtime.invoke_tool("cad.list_editable_parameters", {"project_id": project_id})
    assert listed["status"] == "ok", listed
    assert listed["parameters"], "the model declares PLATE_LENGTH; something should be editable"
    entry = listed["parameters"][0]

    edited = runtime.invoke_tool("cad.edit_parameter", {
        "project_id": project_id,
        "featureId": entry["featureId"],
        "parameterName": entry["parameterName"],
        "newValue": 70.0,
    })
    assert edited["status"] == "ok", edited

    package = project_dir(settings, project_id) / f"{project_id}.aieng"
    status = _revalidation(package)
    assert status and status["requires_revalidation"] is True
    assert status["triggering_tool"] == "cad.edit_parameter"


def test_the_listing_still_carries_the_keys_the_frontend_reads(settings) -> None:
    """Both spellings, because the panel and the endpoint read snake_case.

    Renaming instead of adding would have fixed the agent loop by breaking the
    UI — `editableParameters.ts` and `/api/projects/{id}/editable-parameters`
    both key on `feature_id` / `parameter_name`.
    """
    project_id = _new_project(settings, "both-spellings")
    cad_generation.execute_build123d_code(settings, project_id, {"code": _CODE, "timeout": 180})

    entry = runtime.invoke_tool(
        "cad.list_editable_parameters", {"project_id": project_id}
    )["parameters"][0]
    assert entry["featureId"] == entry["feature_id"]
    assert entry["parameterName"] == entry["parameter_name"]


class TestTheShapeIrPatchHandler:
    """The MCP handler, not the converter #521 covered."""

    @staticmethod
    def _converted(settings, label: str) -> tuple[str, Path]:
        from app.project_io import project_dir

        project_id = _new_project(settings, label)
        source = project_dir(settings, project_id) / "source" / "beam.shape_ir.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(_SHAPE_IR), encoding="utf-8")
        converted = runtime.invoke_tool(
            "aieng.convert", {"project_id": project_id, "sourcePath": str(source)}
        )
        assert converted["status"] == "completed", converted
        return project_id, Path(converted["out_path"])

    @staticmethod
    def _compiled_line(package: Path) -> str:
        with zipfile.ZipFile(package) as zf:
            source = zf.read("geometry/source.py").decode()
        return next(line for line in source.splitlines() if "rounded_box" in line)

    def test_a_patch_rebuilds_the_geometry_and_records_the_edit(self, settings) -> None:
        project_id, package = self._converted(settings, "patched")
        before = self._compiled_line(package)
        with zipfile.ZipFile(package) as zf:
            step_before = zf.read("geometry/generated.step")

        applied = runtime.invoke_tool("aieng.apply_shape_ir_patch", {
            "project_id": project_id,
            "patch": {"operations": [
                {"op": "set_parameter", "target": "beam", "parameter": "radius", "value": 4.3}
            ]},
        })
        assert applied["status"] == "ok", applied

        assert "4.3" in self._compiled_line(package), self._compiled_line(package)
        assert self._compiled_line(package) != before
        with zipfile.ZipFile(package) as zf:
            assert zf.read("geometry/generated.step") != step_before, (
                "the source changed but the built geometry did not — a cache hit "
                "on an identical earlier build would look exactly like this"
            )

        status = _revalidation(package)
        assert status, "a geometry-replacing tool must record the edit"
        assert status["requires_revalidation"] is True
        assert status["triggering_tool"] == "aieng.apply_shape_ir_patch", (
            "it must name itself, not inherit another tool's name"
        )

    def test_agent_context_then_warns(self, settings) -> None:
        """The record only matters because this is what the agent is told."""
        project_id, _package = self._converted(settings, "warned")
        runtime.invoke_tool("aieng.apply_shape_ir_patch", {
            "project_id": project_id,
            "patch": {"operations": [
                {"op": "set_parameter", "target": "beam", "parameter": "radius", "value": 4.4}
            ]},
        })

        impact = runtime.invoke_tool(
            "aieng.agent_context", {"project_id": project_id}
        )["edit_impact"]
        assert impact["available"] is True, "before the fix this stayed False forever"
        assert impact["stale"] is True
        assert impact["triggering_tool"] == "aieng.apply_shape_ir_patch"

    def test_a_dry_run_neither_writes_nor_records(self, settings) -> None:
        project_id, package = self._converted(settings, "dry")
        before = self._compiled_line(package)

        result = runtime.invoke_tool("aieng.apply_shape_ir_patch", {
            "project_id": project_id, "dry_run": True,
            "patch": {"operations": [
                {"op": "set_parameter", "target": "beam", "parameter": "radius", "value": 9.9}
            ]},
        })
        assert result["status"] == "ok", result
        assert result["patch_report"]["ok"] is True
        assert self._compiled_line(package) == before
        assert _revalidation(package) is None, "a preview must not mark the package stale"

    def test_a_malformed_patch_is_rejected_and_changes_nothing(self, settings) -> None:
        """`{ops: [...]}` instead of `{operations: [...]}` — an easy miss."""
        project_id, package = self._converted(settings, "malformed")
        before = self._compiled_line(package)

        result = runtime.invoke_tool("aieng.apply_shape_ir_patch", {
            "project_id": project_id,
            "patch": {"ops": [{"op": "set_parameter", "target": "beam",
                               "parameter": "radius", "value": 4.0}]},
        })
        assert result["status"] == "rejected", result
        assert "operations" in result["patch_report"]["error"]
        assert self._compiled_line(package) == before
        assert _revalidation(package) is None
