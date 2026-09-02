"""A load case is a requirement, and the tools must keep that honest.

`cae.author_load_case` / `cae.apply_load_case` record the physics intent before
analysing — in engineering words, plus what the part must survive. Neither tool
was named by any test, which is what put them in this dogfood round.

Dogfooding found them **sound**: every documented claim held when measured. So
these tests pin the claims rather than a fix. That is worth doing precisely
because the claims are honesty contracts — the kind that erode silently, with no
failing test to notice.

The strongest one is the last: `apply_load_case` must write the same setup
`cae.setup_static` would, or the recorded requirement and the analysis that
actually ran can drift apart. Measured byte-identical across all four members.

Needs the real CAD stack (the resolver reads geometry), so it skips without it
and runs in the `Real CCX Verification` lane.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

pytest.importorskip("build123d", reason="the load-case resolver reads real geometry")

from app.app_factory import create_app  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import default_project, save_project  # noqa: E402
from app.project_io import project_dir  # noqa: E402

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

_CODE = (
    "from build123d import *\n"
    "PLATE_LENGTH = 120.0\n"
    "base = Box(PLATE_LENGTH, 80, 8)\n"
    "base.label = 'base_plate'\n"
    "rib = Box(60, 5, 25).moved(Location((0, 0, 16.5)))\n"
    "rib.label = 'rib_main'\n"
    "result = Compound(children=[base, rib])\n"
)
_PHYSICS = {
    "material": "Al6061-T6",
    "fix": "bottom",
    "load": {"at": "rib_main top", "force_n": 500, "direction": "-Z"},
}
_SETUP_MEMBERS = (
    "simulation/solver_settings.json",
    "simulation/cae_imports/parsed_materials.json",
    "simulation/cae_imports/parsed_boundary_conditions.json",
    "simulation/cae_imports/parsed_loads.json",
)


@pytest.fixture(scope="module")
def workbench(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("load_case")
    workspace = root / "workspace"
    settings = Settings(
        platform_root=root / "platform",
        workspace_root=workspace,
        data_root=root / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    create_app(settings)  # binds the tool registry to these settings
    return settings


def _tool(name: str, payload: dict) -> dict:
    from app import runtime

    return runtime.invoke_tool(name, payload)


def _project(settings, label: str, *, with_geometry: bool = True) -> str:
    from app import cad_generation

    project_id = save_project(settings, default_project(label))["id"]
    if with_geometry:
        built = cad_generation.execute_build123d_code(
            settings, project_id, {"code": _CODE, "timeout": 180}
        )
        assert built.get("status") == "ok", built
    return project_id


def _members(settings, project_id: str, names=_SETUP_MEMBERS) -> dict[str, bytes]:
    """Raw member bytes.

    Comparing parsed values would let two different serialisations of the same
    setup pass a test whose whole claim is that the bytes match — and they do,
    measured across all four members.
    """
    package = project_dir(settings, project_id) / f"{project_id}.aieng"
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(package) as zf:
        present = set(zf.namelist())
        for member in names:
            if member in present:
                out[member] = zf.read(member)
    return out


def test_authoring_records_which_face_it_resolved_and_how_sure_it_is(workbench) -> None:
    """A "checked when written" claim is worth nothing unless the check is recorded."""
    project_id = _project(workbench, "resolved")

    result = _tool("cae.author_load_case", {
        "project_id": project_id, "name": "motor_thrust",
        "description": "motor thrust down on the rib; bolted at the base",
        "acceptance": {"min_safety_factor": 2.0, "max_displacement_mm": 0.5},
        **_PHYSICS,
    })
    assert result["status"] == "ok", result

    resolved = result["load_case"]["resolved_when_authored"]
    for side in ("fix", "load"):
        assert resolved[side]["face_pointers"], side
        assert resolved[side]["face_pointers"][0].startswith("@face:"), side
        assert resolved[side]["why"], "a pointer without a reason is not a check"
        assert resolved[side]["confidence"] in {"low", "medium", "high"}, side


def test_wording_it_cannot_pin_records_nothing_and_says_what_it_could(workbench) -> None:
    """Ambiguity is caught while rewording is still cheap."""
    project_id = _project(workbench, "vague")

    result = _tool("cae.author_load_case", {
        "project_id": project_id, "name": "vague",
        "description": "push it somewhere near the middle bit",
        "material": "Al6061-T6", "fix": "the wobbly face",
        "load": {"at": "somewhere near the middle bit", "force_n": 100,
                 "direction": "sideways"},
    })

    assert result["status"] == "needs_user_input"
    assert result["code"] == "load_case_unresolved"
    assert "Recorded nothing" in result["message"]
    assert result["candidates"], "a refusal without candidates is not actionable"
    assert any("@face:" in c for c in result["candidates"])

    store = project_dir(workbench, project_id) / "cae_load_cases.json"
    assert not store.exists(), "nothing may be stored for wording it could not pin"


def test_authoring_without_geometry_does_not_claim_a_check_it_did_not_run(workbench) -> None:
    """A case may be recorded before the part exists — but not as verified."""
    project_id = _project(workbench, "no geometry", with_geometry=False)

    result = _tool("cae.author_load_case", {
        "project_id": project_id, "name": "early",
        "description": "authored before any geometry", **_PHYSICS,
    })

    assert result["status"] == "ok"
    assert result["resolution"]["checked_against_geometry"] is False
    assert result["design_targets_written"] is None
    assert "resolved_when_authored" not in result["load_case"], (
        "recording a resolution it never performed would be the whole defect"
    )


def test_acceptance_criteria_land_in_the_existing_design_targets(workbench) -> None:
    """No parallel verdict system: the normal comparison must pick them up."""
    project_id = _project(workbench, "targets")
    _tool("cae.author_load_case", {
        "project_id": project_id, "name": "motor_thrust",
        "description": "thrust", "acceptance": {"min_safety_factor": 2.0}, **_PHYSICS,
    })

    package = project_dir(workbench, project_id) / f"{project_id}.aieng"
    with zipfile.ZipFile(package) as zf:
        raw = zf.read("task/design_targets.yaml").decode("utf-8")
    assert "motor_thrust__minimum_safety_factor" in raw, raw[:400]
    assert "load case 'motor_thrust' (cae.author_load_case)" in raw, (
        "a target must name where it came from"
    )
    # YAML wraps the note, so match a fragment that cannot straddle a break.
    assert "asserts nothing about the" in raw, (
        "recording a requirement must not read as a compliance claim"
    )
    assert "compliance_requires_evidence: true" in raw

    context = _tool("aieng.agent_context", {"project_id": project_id})
    comparison = context["target_comparison"]
    assert comparison["available"] is True
    assert comparison["summary"]["unknown"] >= 1, (
        "before a solve every criterion is unknown — never a silent pass"
    )
    assert comparison["summary"]["pass"] == 0


def test_re_authoring_revises_its_own_targets_and_leaves_the_others(workbench) -> None:
    project_id = _project(workbench, "revision")
    _tool("cae.author_load_case", {
        "project_id": project_id, "name": "motor_thrust", "description": "500 N",
        "acceptance": {"min_safety_factor": 2.0, "max_displacement_mm": 0.5}, **_PHYSICS,
    })
    _tool("cae.author_load_case", {
        "project_id": project_id, "name": "second_case", "description": "lighter",
        "acceptance": {"max_stress_mpa": 120.0}, **_PHYSICS,
    })
    _tool("cae.author_load_case", {
        "project_id": project_id, "name": "motor_thrust", "description": "revised: 800 N",
        "acceptance": {"min_safety_factor": 3.0},
        "material": "Al6061-T6", "fix": "bottom",
        "load": {"at": "rib_main top", "force_n": 800, "direction": "-Z"},
    })

    package = project_dir(workbench, project_id) / f"{project_id}.aieng"
    with zipfile.ZipFile(package) as zf:
        raw = zf.read("task/design_targets.yaml").decode("utf-8")

    assert "second_case__max_von_mises_stress" in raw, "another case's target was lost"
    assert "motor_thrust__minimum_safety_factor" in raw
    assert "max_displacement" not in raw, (
        "the revision dropped that criterion; a stale one would be judged forever"
    )


def test_applying_a_case_writes_exactly_what_setup_static_would(workbench) -> None:
    """The claim that makes a load case executable rather than decorative.

    If these two paths can diverge, the recorded requirement and the analysis
    that actually ran drift apart — and the requirement stops being evidence of
    anything. Measured byte-identical across all four setup members.
    """
    direct = _project(workbench, "via setup_static")
    assert _tool("cae.setup_static", {"project_id": direct, **_PHYSICS})["status"] == "ok"

    via_case = _project(workbench, "via load case")
    _tool("cae.author_load_case", {
        "project_id": via_case, "name": "same", "description": "same physics", **_PHYSICS})
    assert _tool("cae.apply_load_case",
                 {"project_id": via_case, "name": "same"})["status"] == "ok"

    from_static = _members(workbench, direct)
    from_case = _members(workbench, via_case)

    assert set(from_static) == set(_SETUP_MEMBERS), sorted(from_static)
    assert from_case == from_static, {
        m: (from_static.get(m), from_case.get(m))
        for m in _SETUP_MEMBERS if from_static.get(m) != from_case.get(m)
    }


def test_a_case_authored_before_the_geometry_still_applies_after_it(workbench) -> None:
    """The point of storing it outside the package."""
    project_id = _project(workbench, "authored first", with_geometry=False)
    _tool("cae.author_load_case", {
        "project_id": project_id, "name": "early", "description": "first", **_PHYSICS})

    from app import cad_generation

    built = cad_generation.execute_build123d_code(
        workbench, project_id, {"code": _CODE, "timeout": 180})
    assert built.get("status") == "ok"

    assert _tool("cae.apply_load_case",
                 {"project_id": project_id, "name": "early"})["status"] == "ok"
    assert set(_members(workbench, project_id)) == set(_SETUP_MEMBERS)
