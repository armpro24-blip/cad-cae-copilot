"""The documented topology-optimization chain, driven from `cae.setup_static`.

`opt.run_topology_optimization` and `opt.derive_problem_from_cae` were named by
no test. Dogfooded end-to-end from the workbench's own one-call CAE authoring
path, they produced two defects.

**The chain was unreachable.** `cae.setup_static` writes each boundary condition
and load with `target: "@face:face_001"` — a pointer, which the deck path
resolves (`normalize_cae_bindings`) and AGENTS.md explicitly calls "not a missing
mapping". `synthesize_setup_from_parsed` knew only `target_feature` and an NSET
name, so every BC and load was dropped, and the derivation then honestly
reported "the CAE setup resolved to 0 support(s) and 0 load(s)" — in 2D *and*
3D, with any design space. Measured before the fix: a 120x80x40 block, fixed
left, 500 N down on the right, derived 0 supports and 0 loads. After: 1 support
and 1 load on real faces, and the 2D case gives the *documented*
plate-bending refusal instead of a blank one.

**The refusal was laundered into a result.** The derivation returns
`status: needs_user_input` rather than inventing supports and loads (#501), and
the documented next step is "inspect this, then pass it to
opt.run_topology_optimization". Passing it verbatim returned `status: ok`:
`_resolve_bcs` saw no usable explicit BCs and substituted the textbook cantilever
preset, so a plate with no supports and no loads produced a full density field,
`warnings: []`, and an `analysis/topology_optimization.json` that
`opt.writeback_to_shape_ir` would turn into the part's geometry. The preset
fallback stays — a caller may want a preset problem on purpose. What is refused
is laundering a refusal into a result.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("build123d", reason="the topopt chain needs the CAD stack")

from app import cad_generation, runtime  # noqa: E402

#: Thick enough that the 3D idealization is meaningful, and clearly a plate in
#: 2D so the documented out-of-plane refusal is the expected answer there.
_CODE = (
    "from build123d import *\n"
    "base = Box(120, 80, 40)\n"
    "base.label = 'base_plate'\n"
    "result = Compound(children=[base])\n"
)
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def authored(tmp_path_factory: pytest.TempPathFactory):
    """A project whose CAE setup was authored the documented one-call way."""
    from app.app_factory import create_app
    from app.config import Settings
    from app.main import default_project, save_project

    root = tmp_path_factory.mktemp("topopt")
    workspace = root / "workspace"
    settings = Settings(
        platform_root=root / "platform",
        workspace_root=workspace,
        data_root=root / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    create_app(settings)
    project_id = save_project(settings, default_project("topopt"))["id"]
    built = cad_generation.execute_build123d_code(
        settings, project_id, {"code": _CODE, "timeout": 180}
    )
    assert built.get("status") == "ok", built
    setup = runtime.invoke_tool("cae.setup_static", {
        "project_id": project_id,
        "material": "Al6061-T6",
        "fix": "left",
        "load": {"at": "right", "force_n": 500, "direction": "-Z"},
    })
    assert setup.get("status") == "ok", setup
    return settings, project_id


def test_the_setup_really_writes_pointer_targets(authored) -> None:
    """The premise of the whole file, pinned so it cannot drift silently."""
    from app.project_io import project_dir

    settings, project_id = authored
    with zipfile.ZipFile(project_dir(settings, project_id) / f"{project_id}.aieng") as zf:
        bcs = json.loads(zf.read("simulation/cae_imports/parsed_boundary_conditions.json"))
    target = bcs["boundary_conditions"][0]["target"]
    assert target.startswith("@face:"), (
        f"cae.setup_static no longer writes a pointer target ({target!r}); if it "
        "writes an NSET name now, the reader must still accept both"
    )


def test_the_setup_is_readable_as_supports_and_loads(authored) -> None:
    """A pointer target is a target. Before the fix: 0 and 0."""
    from aieng.cae_setup_view import load_cae_setup_from_package
    from app.project_io import project_dir

    settings, project_id = authored
    setup = load_cae_setup_from_package(
        project_dir(settings, project_id) / f"{project_id}.aieng"
    )
    assert len(setup.get("boundary_conditions") or []) == 1, setup
    assert len(setup.get("loads") or []) == 1, setup
    assert setup["boundary_conditions"][0]["target_feature"].startswith("face_")
    assert setup["loads"][0]["value_n"] == 500.0
    assert not setup.get("unresolved_targets"), setup.get("unresolved_targets")


def test_the_3d_derivation_uses_the_authored_setup(authored) -> None:
    """`status: ok` is not enough — check it is not a preset in disguise.

    "When a fix turns a failure into a success, confirm the success has the
    reason you intended" (the review lens). Here that means the supports and
    loads trace back to the faces `cae.setup_static` bound.
    """
    _settings, project_id = authored
    derived = runtime.invoke_tool(
        "opt.derive_problem_from_cae", {"project_id": project_id, "dimension": "3d"}
    )
    assert derived["status"] == "ok", derived
    problem = derived["problem"]
    assert derived["derivation"]["bc_source"] == "cae_setup"

    supports = problem["bcs"]["supports"]
    loads = problem["bcs"]["loads"]
    assert len(supports) == 1 and len(loads) == 1, problem["bcs"]
    assert supports[0]["from"]["face_ids"], supports[0]
    assert supports[0]["cells"], "a support that maps to no cells is not a support"
    assert loads[0]["from"]["value_n"] == 500.0
    assert loads[0]["cells"]


def test_the_optimizer_runs_on_those_bcs_not_a_preset(authored) -> None:
    _settings, project_id = authored
    derived = runtime.invoke_tool(
        "opt.derive_problem_from_cae", {"project_id": project_id, "dimension": "3d"}
    )
    run = runtime.invoke_tool("opt.run_topology_optimization", {
        "project_id": project_id, "problem": derived["problem"], "optimizer": "simp_3d",
    })
    assert run["status"] == "ok", run
    block = run["topology_optimization"]["problem"]
    assert block["bcs_source"] == "explicit", block
    assert block["bcs_preset"] is None, block


def test_the_2d_case_gives_the_documented_plate_bending_refusal(authored) -> None:
    """The refusal must name the real cause, which needs the BCs to be readable.

    Before the fix this said "0 support(s) and 0 load(s)" — true, and useless:
    the setup was fine and the reader could not see it.
    """
    _settings, project_id = authored
    derived = runtime.invoke_tool("opt.derive_problem_from_cae", {"project_id": project_id})
    assert derived["status"] == "needs_user_input"
    assert derived["problem"]["support_count"] == 1, (
        "the support must resolve, or the refusal cannot state the real reason"
    )
    reason = derived["reason"]
    assert reason, "the handler returned a refusal with no stated cause"
    assert "thinnest axis" in reason and "plane-stress" in reason, reason
    assert "3d" in (derived["recommendation"] or ""), derived["recommendation"]


class TestARefusalIsNotAProblem:
    def test_passing_a_refusal_through_is_refused(self, authored) -> None:
        """The documented flow is "inspect this, then pass it" — so it gets passed."""
        _settings, project_id = authored
        refusal = runtime.invoke_tool("opt.derive_problem_from_cae", {"project_id": project_id})
        assert refusal["status"] == "needs_user_input"

        run = runtime.invoke_tool("opt.run_topology_optimization", {
            "project_id": project_id, "problem": refusal["problem"],
        })
        assert run["status"] == "needs_user_input", run
        assert run["code"] == "problem_refused", run
        assert "thinnest axis" in run["message"], "carry the derivation's reason forward"
        assert "topology_optimization" not in run, "it must not produce a result"

    def test_no_artifact_is_written_for_a_refusal(self, authored) -> None:
        """The result artifact is what `opt.writeback_to_shape_ir` turns into geometry."""
        from app.project_io import project_dir

        settings, project_id = authored
        package = project_dir(settings, project_id) / f"{project_id}.aieng"
        with zipfile.ZipFile(package) as zf:
            before = zf.read("analysis/topology_optimization.json") if (
                "analysis/topology_optimization.json" in zf.namelist()) else None

        refusal = runtime.invoke_tool("opt.derive_problem_from_cae", {"project_id": project_id})
        runtime.invoke_tool("opt.run_topology_optimization", {
            "project_id": project_id, "problem": refusal["problem"],
        })

        with zipfile.ZipFile(package) as zf:
            after = zf.read("analysis/topology_optimization.json") if (
                "analysis/topology_optimization.json" in zf.namelist()) else None
        assert after == before, "a refused run must leave the package untouched"

    def test_the_library_refuses_too_not_only_the_handler(self) -> None:
        """One check, so every caller is covered — not just the MCP tool."""
        from aieng.converters.topology_optimization import (
            TopologyProblemRefused,
            run_topology_optimization,
        )

        with pytest.raises(TopologyProblemRefused) as excinfo:
            run_topology_optimization({"status": "needs_user_input", "reason": "no loads"})
        assert "no loads" in str(excinfo.value)

    def test_a_deliberate_preset_problem_still_runs(self) -> None:
        """The preset fallback is not the defect — laundering a refusal was.

        A caller asking for a textbook problem on purpose must still get one, or
        this fix would have broken the optimizer's own reference cases.
        """
        from aieng.converters.topology_optimization import run_topology_optimization

        result = run_topology_optimization(
            {"grid": {"nelx": 16, "nely": 8}, "bcs": {"preset": "cantilever"}, "max_iters": 3}
        )
        assert result["problem"]["bcs_preset"] == "cantilever"
        assert result["problem"]["bcs_source"] == "preset"


def test_the_derive_tool_honours_its_optimizer_parameters(authored) -> None:
    """`volfrac`/`penalty`/`rmin`/`max_iters` are advertised on the derive tool.

    They looked dropped at first, because a *refusal* document carries none of
    them — the confounder was the unreadable setup, not the parameters. On a
    derivation that succeeds they all survive, and this pins that.
    """
    _settings, project_id = authored
    asked = {"volfrac": 0.22, "penalty": 4.0, "rmin": 2.5, "max_iters": 7, "resolution_3d": 12}
    derived = runtime.invoke_tool(
        "opt.derive_problem_from_cae", {"project_id": project_id, "dimension": "3d", **asked}
    )
    assert derived["status"] == "ok", derived
    problem = derived["problem"]
    for key in ("volfrac", "penalty", "rmin", "max_iters"):
        assert problem[key] == asked[key], f"{key}: {problem.get(key)!r} != {asked[key]!r}"
    assert problem["grid"]["nx"] == 12, problem["grid"]
