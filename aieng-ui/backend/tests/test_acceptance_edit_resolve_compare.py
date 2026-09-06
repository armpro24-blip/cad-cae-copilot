"""The one task the project promises, run end to end through the tool surface.

From the 2026-09-06 direction review:

    对一个有明确尺寸、载荷和约束的支架或安装件，完成建模、参数修改、重新分析，
    并交付能追溯到几何版本和求解记录的前后对比。

Run once as a user would — every step through an MCP tool, not an internal
function — it returned a WRONG engineering answer that looked right:

    baseline   2.372665 mm / 138.7702 MPa
    edit       BEAM_THICKNESS 8 -> 16 mm      (regression_diff: clean)
    re-solve   2.372665 mm / 138.7702 MPa     0.0% change, status: completed

Four separate defects stacked into that:

1. `cae.generate_solver_input` refused to overwrite run_001 (correctly), and
   `cae.run_solver` then re-ran that FIRST deck and published its numbers as the
   new result. Three tools each behaving correctly in isolation produced a
   fiction. Decks now record the geometry revision they were built for, and
   running one against different geometry is refused (`stale_deck`).
2. The refusal named `--overwrite`, a CLI flag this tool does not take, and
   overwriting is the option that DESTROYS the baseline being compared against.
   It now names a free `run_id`.
3. `cae.run_solver` ignored the run directory in `input_deck_path` and defaulted
   `run_id` to "run_001", so solving run_002's deck wrote its FRD, log and
   `solver_run.json` OVER the baseline's — and reported `run_id: run_001`.
4. `metrics_source` recorded the temp path the FRD was staged at, so an exported
   package pointed at a directory that no longer existed and could not say which
   run produced its numbers.

The physics is the assertion that matters. Doubling a beam's thickness must cut
tip displacement ~8x (stiffness ∝ t³) and bending stress ~4x (∝ t²). Measured
after the fixes: -87.2% and -75.8%, against -87.5% and -75.0% from beam theory.
A test that only checked "status == completed" passed throughout the defect.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

pytest.importorskip("build123d", reason="the acceptance run needs the real CAD stack")
pytest.importorskip("gmsh", reason="the acceptance run needs the real mesher")

_CODE = (
    "from build123d import *\n"
    "BEAM_THICKNESS = 8.0\n"
    "beam = Box(120.0, 40.0, BEAM_THICKNESS)\n"
    "beam.label = 'beam'\n"
    "result = Compound(children=[beam])\n"
)
_THICKNESS_AFTER = 16.0


def _ccx_available() -> bool:
    from app.simulation_runner import _find_ccx

    try:
        return bool(_find_ccx())
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def acceptance(tmp_path_factory: pytest.TempPathFactory):
    """Run the promised task once; the tests below read what it produced.

    Module-scoped because it solves twice with real CalculiX. Everything goes
    through `runtime.invoke_tool` — the surface a connected agent drives — so a
    break anywhere in the documented chain fails here rather than passing at
    every individual stage.
    """
    if not _ccx_available():
        pytest.skip("CalculiX is not runnable here; the gate counts this as a failure")

    from app import runtime
    from app.app_factory import create_app
    from app.config import Settings
    from app.main import default_project, save_project
    from app.project_io import project_dir

    root = tmp_path_factory.mktemp("acceptance")
    # `GeometryCache` resolves `.aieng_cache` from the CWD; a shared cache could
    # serve the pre-edit build for the post-edit source and fake the whole point.
    previous_cwd = Path.cwd()
    os.chdir(root)
    try:
        workspace = root / "workspace"
        settings = Settings(
            platform_root=root / "platform",
            workspace_root=workspace,
            data_root=root / "data",
            aieng_root=_WORKSPACE_ROOT / "aieng",
            sample_step=workspace / "sample.step",
        )
        create_app(settings)

        record: dict[str, object] = {}
        project_id = save_project(settings, default_project("acceptance"))["id"]
        record["project_id"] = project_id

        built = runtime.invoke_tool(
            "cad.execute_build123d", {"project_id": project_id, "code": _CODE, "timeout": 240}
        )
        assert built["status"] == "ok", built
        package = project_dir(settings, project_id) / f"{project_id}.aieng"
        record["package"] = package

        setup = runtime.invoke_tool("cae.setup_static", {
            "project_id": project_id, "material": "Al6061-T6", "fix": "left",
            "load": {"at": "right", "force_n": 500, "direction": "-Z"},
        })
        assert setup["status"] == "ok", setup

        record["baseline"] = _solve(runtime, project_id, package, run_id="run_001")

        listed = runtime.invoke_tool("cad.list_editable_parameters", {"project_id": project_id})
        entry = listed["parameters"][0]
        record["edit"] = runtime.invoke_tool("cad.edit_parameter", {
            "project_id": project_id,
            "featureId": entry["featureId"],
            "parameterName": entry["parameterName"],
            "newValue": _THICKNESS_AFTER,
        })
        record["edit_impact"] = runtime.invoke_tool(
            "aieng.agent_context", {"project_id": project_id}
        )["edit_impact"]
        record["stale_deck_attempt"] = runtime.invoke_tool("cae.run_solver", {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
        })

        record["after"] = _solve(runtime, project_id, package, run_id="run_002")

        exported = root / "exported.aieng"
        shutil.copy2(package, exported)
        record["exported"] = exported
        # The deliverable, taken off the exported copy — the review's step 6 is
        # "export and reopen", so the comparison has to survive leaving the
        # workbench, not just be computable while the project is still open.
        record["comparison"] = runtime.invoke_tool(
            "cae.compare_runs", {"package_path": str(exported)}
        )
        return record
    finally:
        os.chdir(previous_cwd)


def _solve(runtime, project_id: str, package: Path, *, run_id: str) -> dict:
    """Mesh, generate a deck for this run, solve it, extract. Returns the metrics."""
    meshed = runtime.invoke_tool(
        "cae.generate_mesh", {"project_id": project_id, "mesh_size_mm": 6}
    )
    assert meshed["status"] == "completed", meshed
    deck = runtime.invoke_tool(
        "cae.generate_solver_input", {"project_id": project_id, "run_id": run_id}
    )
    assert deck["status"] == "completed", deck
    solved = runtime.invoke_tool("cae.run_solver", {
        "project_id": project_id,
        "input_deck_path": f"simulation/runs/{run_id}/solver_input.inp",
    })
    assert solved["status"] == "completed", solved
    extracted = runtime.invoke_tool("cae.extract_solver_results", {"project_id": project_id})
    assert extracted["status"] == "ok", extracted
    return _metrics(package)


def _metrics(package: Path) -> dict:
    with zipfile.ZipFile(package) as zf:
        document = json.loads(zf.read("results/computed_metrics.json"))
    for case in document.get("load_cases") or []:
        if case.get("metrics"):
            return {name: value.get("value") for name, value in case["metrics"].items()}
    raise AssertionError(f"no metrics in {package.name}")


def _member(package: Path, name: str) -> dict:
    with zipfile.ZipFile(package) as zf:
        return json.loads(zf.read(name))


def test_the_baseline_solve_produces_real_numbers(acceptance) -> None:
    baseline = acceptance["baseline"]
    assert baseline["max_displacement"] > 0
    assert baseline["max_von_mises_stress"] > 0


def test_the_edit_marks_the_earlier_evidence_as_no_longer_applicable(acceptance) -> None:
    """Step 4 of the review's chain: keep the baseline, mark what it applies to."""
    assert acceptance["edit"]["status"] == "ok", acceptance["edit"]
    impact = acceptance["edit_impact"]
    assert impact["available"] is True
    assert impact["stale"] is True
    assert impact["triggering_tool"] == "cad.edit_parameter"


def test_running_the_old_deck_after_an_edit_is_refused(acceptance) -> None:
    """The defect in one assertion: this used to return `completed`.

    It re-ran the pre-edit deck and published its displacement and stress as the
    post-edit result, with nothing flagged anywhere.
    """
    attempt = acceptance["stale_deck_attempt"]
    assert attempt["status"] == "error", attempt
    assert attempt["code"] == "stale_deck", attempt
    assert attempt["solver_execution_performed"] is False
    assert attempt["deck_geometry_revision"] != attempt["current_geometry_revision"]
    assert "run_id" in attempt["message"], "say how to get a deck for the current geometry"


class TestTheComparisonIsPhysicallyRight:
    """"status: completed" is not an engineering result.

    Doubling thickness: displacement ∝ 1/t³ (-87.5%), bending stress ∝ 1/t²
    (-75%). Wide tolerances — this is a coarse mesh on a stubby beam, and the
    assertion is about the chain being real, not about FEA accuracy.
    """

    def test_displacement_falls_about_eightfold(self, acceptance) -> None:
        before = acceptance["baseline"]["max_displacement"]
        after = acceptance["after"]["max_displacement"]
        ratio = before / after
        assert 5.0 < ratio < 12.0, (
            f"{before:.4g} -> {after:.4g} is a {ratio:.1f}x change; beam theory "
            "says ~8x for a doubled thickness. A ratio near 1.0 means the "
            "re-solve did not solve the edited geometry."
        )

    def test_stress_falls_about_fourfold(self, acceptance) -> None:
        before = acceptance["baseline"]["max_von_mises_stress"]
        after = acceptance["after"]["max_von_mises_stress"]
        ratio = before / after
        assert 2.5 < ratio < 6.0, f"{before:.4g} -> {after:.4g} is {ratio:.1f}x, expected ~4x"

    def test_the_numbers_actually_differ(self, acceptance) -> None:
        """The blunt version, because the defect produced EXACTLY equal values."""
        assert acceptance["baseline"] != acceptance["after"]


class TestTheComparisonIsDelivered:
    """The promise ends at "交付…前后对比" — a deliverable, not a test assertion.

    Every assertion above reads `record["baseline"]` and `record["after"]`,
    which this test module held in memory across the two solves. The package
    itself could not produce them: `results/computed_metrics.json` is one fixed
    path, so the re-solve's extraction had already replaced the baseline's
    numbers. `cae.compare_runs` re-derives each side from its own run's FRD, and
    these tests check the SHIPPED comparison against the numbers the chain
    actually produced.
    """

    def test_the_tool_finds_both_runs_without_being_told(self, acceptance) -> None:
        comparison = acceptance["comparison"]
        assert comparison["status"] == "ok", comparison
        assert comparison["baseline"]["run_id"] == "run_001"
        assert comparison["current"]["run_id"] == "run_002"

    def test_it_reports_the_same_numbers_the_solves_produced(self, acceptance) -> None:
        rows = {row["metric"]: row for row in acceptance["comparison"]["comparison"]}
        for metric in ("max_displacement", "max_von_mises_stress"):
            row = rows[metric]
            assert row["before"] == pytest.approx(acceptance["baseline"][metric], rel=1e-6), row
            assert row["after"] == pytest.approx(acceptance["after"][metric], rel=1e-6), row

    def test_it_carries_the_geometry_revision_of_each_deck(self, acceptance) -> None:
        """"Traceable to the geometry version" is half the promised sentence."""
        comparison = acceptance["comparison"]
        assert comparison["baseline"]["geometry_revision"] == 0
        assert comparison["current"]["geometry_revision"] == 1
        assert comparison["geometry_changed"] is True

    def test_the_reported_change_matches_beam_theory(self, acceptance) -> None:
        rows = {row["metric"]: row for row in acceptance["comparison"]["comparison"]}
        assert -95.0 < rows["max_displacement"]["percent_change"] < -75.0
        assert -85.0 < rows["max_von_mises_stress"]["percent_change"] < -55.0


class TestTheExportedPackageCanExplainItself:
    """Step 6: reopen it and still say which version and which run."""

    def test_both_runs_survive(self, acceptance) -> None:
        """The second solve used to overwrite the first run's evidence."""
        with zipfile.ZipFile(acceptance["exported"]) as zf:
            runs = sorted(n for n in zf.namelist() if n.endswith("solver_run.json"))
        assert runs == [
            "simulation/runs/run_001/solver_run.json",
            "simulation/runs/run_002/solver_run.json",
        ], runs

    def test_the_metrics_name_the_run_that_produced_them(self, acceptance) -> None:
        source = _member(acceptance["exported"], "results/computed_metrics.json")["metrics_source"]
        assert source["run_id"] == "run_002", source
        assert source["source_files"] == ["simulation/runs/run_002/outputs/result.frd"], (
            "an in-package artifact, not the temp path the FRD was staged at — "
            f"got {source['source_files']}"
        )

    def test_each_deck_records_the_geometry_it_was_built_for(self, acceptance) -> None:
        first = _member(acceptance["exported"], "simulation/runs/run_001/deck_provenance.json")
        second = _member(acceptance["exported"], "simulation/runs/run_002/deck_provenance.json")
        assert first["geometry_revision"] == 0, first
        assert second["geometry_revision"] == 1, second

    def test_the_package_says_which_geometry_revision_was_validated(self, acceptance) -> None:
        status = _member(acceptance["exported"], "state/revalidation_status.json")
        assert status["current_geometry_revision"] == status["last_validated_geometry_revision"], (
            "the re-solve validated the current geometry, so these must agree"
        )


def test_a_run_id_that_contradicts_the_deck_is_refused(acceptance) -> None:
    """Two ways to name the run, and the results go under only one of them."""
    from app import runtime

    conflicted = runtime.invoke_tool("cae.run_solver", {
        "project_id": acceptance["project_id"],
        "input_deck_path": "simulation/runs/run_002/solver_input.inp",
        "run_id": "run_001",
    })
    assert conflicted["status"] == "error", conflicted
    assert conflicted["code"] == "run_id_conflict", conflicted
    assert conflicted["solver_execution_performed"] is False


def test_overwriting_a_run_leaves_one_provenance_entry(tmp_path: Path) -> None:
    """Two entries of the same name and `zipfile` reads the FIRST — the old one.

    Carrying the existing `deck_provenance.json` forward AND writing a new one
    would make the staleness check read the revision the deck was built for
    BEFORE the overwrite, which is the wrong answer the provenance exists to
    prevent.
    """
    from aieng.simulation.deck_generator import DECK_PROVENANCE_PATH_TEMPLATE

    member = DECK_PROVENANCE_PATH_TEMPLATE.format(run_id="run_001")
    package = tmp_path / "twice.aieng"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "m"}))
        zf.writestr(member, json.dumps({"geometry_revision": 0}))
        zf.writestr("simulation/runs/run_001/solver_input.inp", "*STEP\n")

    from aieng.simulation.deck_generator import _read_existing_members

    with zipfile.ZipFile(package) as zf:
        carried = _read_existing_members(
            zf, "simulation/runs/run_001/solver_input.inp", member
        )
    assert member not in {info.filename for info, _ in carried}, (
        "the old provenance must not be carried forward when it is rewritten"
    )
