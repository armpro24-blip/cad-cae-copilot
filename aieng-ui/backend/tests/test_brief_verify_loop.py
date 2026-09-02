"""The documented plan → build → verify loop, at the tool level.

`cad.author_brief` → `cad.get_brief` → `cad.validate_targets` → `cad.diagnose`
is the loop AGENTS.md tells an agent to close before presenting a build. All
four tools were named by no test: the sweep of the 80 registered tools put them
among the 17 with zero coverage, and four of the four documented chains
dogfooded before this one were broken.

This one was not. Dogfooding it end to end on a real project found the loop
working — the brief's targets auto-loaded, a deliberately wrong rib dimension
came back as `fail` with `measured [60,5,25] vs [60,5,40]`, and `cad.diagnose`
turned that into `needs_repair` with a prioritized action. So these tests pin
behaviour that already works, plus the one real gap the round did find.

No CAD stack needed: `validate_targets` and `diagnose` read the package's
topology and feature graph, so a hand-built package is enough.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.app_factory import create_app
from app.config import Settings
from app.main import default_project, save_project
from app.project_io import project_dir

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

# A plate with a rib sitting on it — two touching solids, as a machined bracket.
#
# `name`, not `label`: the validator keys solids by `name or id`, which is what
# the real CAD writer emits. A fixture using `label` made
# `named_part_present` pass (it also reads the feature graph) while `part_size`
# reported "part not found" — two checks contradicting each other about the same
# part. That was the fixture's error, not the product's; real packages carry
# `name`, checked on three of them.
_TOPOLOGY = {
    "format_version": "0.1.0",
    "entities": [
        {"id": "body_001", "type": "solid", "name": "base_plate",
         "bounding_box": [0, 0, 0, 80, 60, 8], "face_ids": ["face_001"]},
        {"id": "body_002", "type": "solid", "name": "rib_main",
         "bounding_box": [10, 27.5, 8, 70, 32.5, 33], "face_ids": ["face_002"]},
    ],
}
_FEATURE_GRAPH = {
    "format_version": "0.1.0",
    "features": [
        {"id": "feat_body_001", "type": "named_part", "name": "base_plate",
         "geometry_refs": {"solids": ["body_001"]}},
        {"id": "feat_body_002", "type": "named_part", "name": "rib_main",
         "geometry_refs": {"solids": ["body_002"]}},
    ],
}


@pytest.fixture()
def project(tmp_path: Path) -> tuple[Settings, str]:
    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    create_app(settings)  # binds the tool registry to these settings
    project_id = save_project(settings, default_project("brief loop"))["id"]

    package = project_dir(settings, project_id) / f"{project_id}.aieng"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("graph/feature_graph.json", json.dumps(_FEATURE_GRAPH))
    record = save_project(settings, {
        **json.loads((project_dir(settings, project_id) / "metadata.json").read_text("utf-8")),
        "aieng_file": f"{project_id}.aieng",
    })
    assert record["aieng_file"]
    return settings, project_id


def _tool(name: str, payload: dict) -> dict:
    from app import runtime

    return runtime.invoke_tool(name, payload)


def _brief(project_id: str, *, rib_height: float) -> dict:
    return _tool("cad.author_brief", {
        "project_id": project_id,
        "request": "a CNC bracket: base plate with a stiffening rib",
        "units": "mm",
        "model_type": "single_part",
        "parts": [
            {"name": "base_plate", "role": "load-bearing plate", "size_mm": [80, 60, 8]},
            {"name": "rib_main", "role": "stiffener", "size_mm": [60, 5, rib_height]},
        ],
        "tolerance_mm": 1,
    })


def test_the_brief_round_trips_through_get_brief(project) -> None:
    _settings, project_id = project

    authored = _brief(project_id, rib_height=25)
    assert authored["status"] == "ok"

    read_back = _tool("cad.get_brief", {"project_id": project_id})
    assert read_back["status"] == "ok"
    assert read_back["request"] == authored["request"]
    assert read_back["validation_targets"] == authored["validation_targets"]
    assert [p["name"] for p in read_back["parts"]] == ["base_plate", "rib_main"]


def test_get_brief_says_not_found_rather_than_inventing_one(project) -> None:
    _settings, project_id = project
    result = _tool("cad.get_brief", {"project_id": project_id})
    assert result.get("status") == "not_found", result


def test_a_single_part_brief_derives_the_floating_check(project) -> None:
    """The gap this dogfood round found.

    The derivation ran only for `assembly`/`product`, so a `single_part` with two
    bodies — where a detached body is unambiguously broken geometry — got no
    such target, while the tool's own description promised one.
    """
    _settings, project_id = project
    kinds = [t["kind"] for t in _brief(project_id, rib_height=25)["validation_targets"]]
    assert "no_floating_parts" in kinds, kinds


def test_validate_targets_loads_the_brief_when_none_are_passed(project) -> None:
    """The link that makes it a loop rather than two unrelated tools."""
    _settings, project_id = project
    _brief(project_id, rib_height=25)

    result = _tool("cad.validate_targets", {"project_id": project_id})
    assert result["targets_source"] == "cad_brief", result.get("targets_source")
    assert result["verdict"] == "pass", result["targets"]
    assert result["summary"]["fail"] == 0


def test_a_wrong_promise_in_the_brief_fails_with_the_measurement(project) -> None:
    """A validator that passes everything is worth nothing.

    Measured on a real project during the dogfood: a rib declared 40mm tall
    against a 25mm build came back `fail` with both numbers and the deviation.
    """
    _settings, project_id = project
    _brief(project_id, rib_height=40)  # the build is 25

    result = _tool("cad.validate_targets", {"project_id": project_id})
    assert result["verdict"] == "fail"
    failed = [t for t in result["targets"] if t["status"] == "fail"]
    assert len(failed) == 1, failed
    assert failed[0]["kind"] == "part_size"
    assert failed[0]["measured"] == [60.0, 5.0, 25.0]
    assert failed[0]["expected"] == [60.0, 5.0, 40.0]
    assert "15.0" in failed[0]["detail"], "the deviation must be stated, not just 'fail'"


def test_diagnose_turns_a_failing_target_into_a_repair_action(project) -> None:
    """`cad.diagnose` composes the brief's verdict into the repair loop."""
    _settings, project_id = project
    _brief(project_id, rib_height=40)

    result = _tool("cad.diagnose", {"project_id": project_id})

    assert result["verdict"] == "needs_repair"
    assert "brief_targets_failing" in result["triggers"]
    assert result["snapshot"]["brief_targets"]["fail"] == 1
    actions = [a for a in result["repair_actions"] if a["source"] == "validate_targets"]
    assert actions and "rib_main" in actions[0]["issue"]
    assert actions[0]["fix"], "an action without a fix is not actionable"


def test_diagnose_is_ready_when_the_brief_is_met(project) -> None:
    """The verdict must be reachable, or `needs_repair` carries no information."""
    _settings, project_id = project
    _brief(project_id, rib_height=25)

    result = _tool("cad.diagnose", {"project_id": project_id})
    assert result["snapshot"]["brief_targets"]["fail"] == 0
    assert "brief_targets_failing" not in result["triggers"]
