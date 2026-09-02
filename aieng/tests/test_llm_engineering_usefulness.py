"""Smoke tests for the Phase 30 automated A/B benchmark harness.

Validates the plumbing without spending money on real model API calls:

- The deliberately-broken fixture builds.
- AIENG tools execute in-process and return inspectable dicts.
- The deterministic substring scorer assigns the expected verdict to a
  range of synthetic model outputs.
- The full inspect_ai eval pipeline runs to completion against the
  built-in ``mockllm/model`` provider (no API keys).

Skipped when ``inspect_ai`` is not installed (it lives under the
optional ``[benchmark]`` extra).
"""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import pytest

inspect_ai = pytest.importorskip(
    "inspect_ai",
    reason="inspect_ai not installed; "
    "pip install -e '.[benchmark]' to enable Phase 30 tests",
)


# Lazy imports — only reached when inspect_ai is available.
from inspect_ai import eval as inspect_eval  # noqa: E402
from inspect_ai.scorer import Target  # noqa: E402

from benchmarks.llm_engineering_usefulness.scenarios.diagnose_broken_cae_setup import (  # noqa: E402
    build_fixture,
    task as scenario_task,
)
from benchmarks.llm_engineering_usefulness.tools.aieng_tools import (  # noqa: E402
    aieng_cae_preprocessing_summary,
    aieng_inspect_package,
    aieng_read_artifact,
)


# ---------------------------------------------------------------------------
# Fixture build
# ---------------------------------------------------------------------------


@pytest.fixture
def broken_package(tmp_path: Path) -> Path:
    return build_fixture.build_broken_package(tmp_path / "fixture.aieng")


def test_fixture_is_well_formed_zip(broken_package: Path) -> None:
    assert broken_package.exists()
    with zipfile.ZipFile(broken_package, "r") as zf:
        names = set(zf.namelist())
    assert "manifest.json" in names
    assert "simulation/cae_imports/parsed_materials.json" in names
    assert "simulation/load_cases/load_case_001.json" in names


def test_fixture_contains_the_documented_defect(broken_package: Path) -> None:
    """The whole point of this fixture: the load case references a material
    that the materials artifact never defines. If this regresses, the
    scenario stops measuring what its rubric scores against."""
    with zipfile.ZipFile(broken_package, "r") as zf:
        materials = json.loads(zf.read("simulation/cae_imports/parsed_materials.json"))
        load_case = json.loads(zf.read("simulation/load_cases/load_case_001.json"))

    defined_materials = {m["name"] for m in materials["materials"]}
    assert "Steel" in defined_materials
    assert "Aluminum6061" not in defined_materials
    assert load_case["material_ref"] == "Aluminum6061"


def test_fixture_carries_bulk_artifacts(broken_package: Path) -> None:
    """The scaled-up fixture must include bulk topology/features/element
    artifacts so the defect is buried in plausible noise rather than
    obvious in a small prompt. Pinning the design intent — a regression
    that strips these would revert the scenario to its ceiling-prone
    original form."""
    with zipfile.ZipFile(broken_package, "r") as zf:
        names = set(zf.namelist())
        topology = json.loads(zf.read("simulation/cae_imports/parsed_topology.json"))
        features = json.loads(zf.read("simulation/cae_imports/parsed_features.json"))
        elements = json.loads(zf.read("simulation/mesh/element_listing.json"))

    for required in (
        "simulation/cae_imports/parsed_topology.json",
        "simulation/cae_imports/parsed_features.json",
        "simulation/mesh/element_listing.json",
        "simulation/load_cases/load_case_002.json",
        "simulation/load_cases/load_case_003.json",
        "graph/assembly_metadata.json",
    ):
        assert required in names, f"bulk artifact missing: {required}"

    assert len(topology["faces"]) >= 100, "topology bulk too small"
    assert len(features["features"]) >= 30, "features bulk too small"
    assert len(elements["elements"]) >= 100, "element listing bulk too small"


def test_fixture_red_herring_load_cases_are_not_defects(broken_package: Path) -> None:
    """load_case_002 and load_case_003 must reference a defined material
    (Steel). A model that flags them as broken is wrong — only
    load_case_001's Aluminum6061 reference is the defect."""
    with zipfile.ZipFile(broken_package, "r") as zf:
        materials = json.loads(zf.read("simulation/cae_imports/parsed_materials.json"))
        lc2 = json.loads(zf.read("simulation/load_cases/load_case_002.json"))
        lc3 = json.loads(zf.read("simulation/load_cases/load_case_003.json"))

    defined_materials = {m["name"] for m in materials["materials"]}
    assert lc2["material_ref"] in defined_materials
    assert lc3["material_ref"] in defined_materials


def test_ensure_fixture_rebuilds_even_when_stale_file_exists(tmp_path: Path) -> None:
    """Regression: _ensure_fixture must rebuild every call, not short-circuit
    on file-exists. A stale fixture from a previous scenario version would
    otherwise feed wrong inputs to the eval — we hit this exact bug when
    moving from the 8-artifact original to the 14-artifact scaled form."""
    target = tmp_path / "fixture.aieng"
    # Pre-create a deliberately-wrong "stale" fixture so we can confirm the
    # ensure helper overwrites it rather than re-using its contents.
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "stale_fixture"}))
        zf.writestr("only_member.txt", "this should be gone after rebuild")

    rebuilt = scenario_task._ensure_fixture(fixture_path=str(target))
    assert rebuilt == target

    with zipfile.ZipFile(target, "r") as zf:
        names = set(zf.namelist())
    assert "only_member.txt" not in names, "stale fixture survived rebuild"
    # Confirms the canonical scaled artifact set is now present.
    assert "graph/assembly_metadata.json" in names
    assert "simulation/cae_imports/parsed_topology.json" in names
    assert "simulation/load_cases/load_case_001.json" in names


def test_fixture_size_pushes_condition_a_over_token_budget(broken_package: Path) -> None:
    """The Condition A raw-dump input must be substantially larger than the
    2,000-token efficiency budget — otherwise the second-axis scorer can't
    distinguish "selective tool access" from "raw dump". Rough check:
    >= 25 KB of dumped JSON, which decodes to ~6,000+ tokens."""
    from benchmarks.llm_engineering_usefulness.scenarios.diagnose_broken_cae_setup.task import (
        _build_condition_a_input,
    )

    dumped = _build_condition_a_input(broken_package)
    assert len(dumped.encode("utf-8")) >= 25_000, (
        f"Condition A dump is only {len(dumped.encode('utf-8'))} bytes; "
        "the scenario will hit a correctness ceiling at this size."
    )


# ---------------------------------------------------------------------------
# Tool surface — exercised in-process (no inspect_ai eval loop required)
# ---------------------------------------------------------------------------


def test_aieng_inspect_package_tool_lists_artifacts(broken_package: Path) -> None:
    inspect_fn = aieng_inspect_package()
    result = asyncio.run(inspect_fn(package_path=str(broken_package)))
    assert "members" in result
    assert "simulation/load_cases/load_case_001.json" in result["members"]
    assert "cae_detection" in result


def test_aieng_read_artifact_tool_returns_parsed_json(broken_package: Path) -> None:
    read_fn = aieng_read_artifact()
    result = asyncio.run(
        read_fn(
            package_path=str(broken_package),
            artifact_path="simulation/load_cases/load_case_001.json",
        )
    )
    assert result["exists"] is True
    assert result["parsed_json"]["material_ref"] == "Aluminum6061"


def test_aieng_preprocessing_summary_tool_runs(broken_package: Path) -> None:
    summary_fn = aieng_cae_preprocessing_summary()
    result = asyncio.run(summary_fn(package_path=str(broken_package)))
    assert "status" in result
    assert "schema_version" in result


# ---------------------------------------------------------------------------
# Deterministic rubric scorer
# ---------------------------------------------------------------------------


class _StubTaskState:
    """Minimum surface the scorer reads from."""

    class _Output:
        def __init__(self, completion: str) -> None:
            self.completion = completion

    def __init__(self, completion: str) -> None:
        self.output = self._Output(completion)


def _score(completion: str):
    scorer = scenario_task.diagnose_rubric_scorer()
    state = _StubTaskState(completion)
    return asyncio.run(scorer(state, Target("anything")))


def test_rubric_scores_correct_diagnosis_as_C() -> None:
    score = _score(
        "The load case load_case_001 references material Aluminum6061 which is "
        "not defined in parsed_materials.json — that is the undefined material "
        "preventing the solver from running."
    )
    assert score.value == "C"
    assert "names_undefined_material" in score.explanation


def test_rubric_scores_partial_diagnosis() -> None:
    score = _score(
        "There is a load case referencing a material that is not defined."
    )
    assert score.value in ("P", "I")


def test_rubric_scores_incorrect_diagnosis_as_I() -> None:
    score = _score(
        "The mesh looks fine and the solver settings are present. "
        "I do not see any defect."
    )
    assert score.value == "I"


def test_rubric_penalises_invented_defects() -> None:
    """Even a response naming Aluminum6061 should drop a grade if it
    *also* invents an unrelated defect."""
    score = _score(
        "Aluminum6061 is an undefined material referenced by the load case. "
        "Also the mesh is missing and solver settings are missing."
    )
    assert score.value in ("P", "I")  # downgraded from C by penalties


# ---------------------------------------------------------------------------
# Token-efficiency scorer (second axis)
# ---------------------------------------------------------------------------


class _StubUsage:
    def __init__(self, total: int) -> None:
        self.total_tokens = total


class _StubStateWithUsage:
    """TaskState-shaped stub carrying a state.token_usage attribute."""

    def __init__(self, total: int) -> None:
        self.token_usage = _StubUsage(total)
        self.output = _StubTaskState._Output("")


def _efficiency(total: int):
    scorer = scenario_task.token_efficiency_scorer()
    state = _StubStateWithUsage(total)
    return asyncio.run(scorer(state, Target("anything")))


def test_efficiency_at_budget_scores_one() -> None:
    # rubric.yaml budget is 2000
    score = _efficiency(2000)
    assert score.value == pytest.approx(1.0)
    assert "used=2000" in score.explanation
    assert "budget=2000" in score.explanation


def test_efficiency_under_budget_caps_at_one() -> None:
    # 500 tokens is well under 2000 — ratio is capped at 1.0, not 4.0
    score = _efficiency(500)
    assert score.value == pytest.approx(1.0)


def test_efficiency_over_budget_drops_below_one() -> None:
    # Roughly 2x the budget — should score ~0.5
    score = _efficiency(4000)
    assert score.value == pytest.approx(0.5)


def test_efficiency_matches_kimi_condition_b_actuals() -> None:
    """Calibration check: the first Kimi run on Condition B used 6,272
    tokens. Under the 2,000-token budget that scores ~0.319 — well below
    Condition A's 1.0. This is the differentiation the second axis exists
    to surface, so the test pins the value."""
    score = _efficiency(6272)
    assert 0.30 < float(score.value) < 0.35
    assert "used=6272" in score.explanation


def test_efficiency_missing_usage_returns_zero() -> None:
    """If the harness can't surface token usage, score 0 (worst-case
    rather than crash). This gates against silent regressions if
    inspect_ai changes its TaskState shape."""

    class _BareState:
        pass

    bare = _BareState()
    bare.output = _StubTaskState._Output("")  # has output but no usage
    scorer = scenario_task.token_efficiency_scorer()
    score = asyncio.run(scorer(bare, Target("anything")))
    assert score.value == 0.0
    assert "unavailable" in score.explanation.lower()


def test_efficiency_falls_back_to_output_usage() -> None:
    """If state.token_usage is absent but state.output.usage is set
    (older inspect_ai behaviour), the scorer still works."""

    class _BareState:
        pass

    bare = _BareState()
    bare.output = _StubTaskState._Output("")
    bare.output.usage = _StubUsage(1500)  # type: ignore[attr-defined]
    scorer = scenario_task.token_efficiency_scorer()
    score = asyncio.run(scorer(bare, Target("anything")))
    assert score.value == pytest.approx(1.0)
    assert "used=1500" in score.explanation


# ---------------------------------------------------------------------------
# End-to-end harness smoke against mockllm/model
# ---------------------------------------------------------------------------


def test_condition_a_runs_end_to_end_with_mock_model(broken_package: Path) -> None:
    """The whole eval pipeline (task → dataset → solver → scorer) executes
    against the built-in mock provider. No API key required. We do NOT assert
    a specific verdict — mockllm returns a fixed default that won't satisfy
    the rubric. The test confirms only that the harness completes and that
    both axis scorers (correctness + token efficiency) registered."""
    task = scenario_task.diagnose_broken_cae_setup_condition_a(
        fixture_path=str(broken_package)
    )
    logs = inspect_eval(task, model="mockllm/model", display="none")
    assert logs, "inspect_ai.eval returned no logs"
    log = logs[0]
    assert log.status == "success"
    assert log.samples is not None and len(log.samples) == 1
    sample = log.samples[0]
    # Both scorers must report — correctness and token efficiency.
    assert sample.scores is not None
    assert "diagnose_rubric_scorer" in sample.scores
    assert "token_efficiency_scorer" in sample.scores


def test_condition_b_runs_end_to_end_with_mock_model(broken_package: Path) -> None:
    """Condition B includes ``use_tools(*AIENG_TOOLS)`` in the solver. The
    mock model won't actually call the tools, but the eval must still
    register them without error — this is the regression test for tool
    wire-up. Also pins multi-scorer wiring."""
    task = scenario_task.diagnose_broken_cae_setup_condition_b(
        fixture_path=str(broken_package)
    )
    logs = inspect_eval(task, model="mockllm/model", display="none")
    assert logs, "inspect_ai.eval returned no logs"
    log = logs[0]
    assert log.status == "success"
    assert log.samples is not None and len(log.samples) == 1
    sample = log.samples[0]
    assert sample.scores is not None
    assert "diagnose_rubric_scorer" in sample.scores
    assert "token_efficiency_scorer" in sample.scores


# ===========================================================================
# Scenario 2 — mass_reduction_recommendation
# ===========================================================================

from benchmarks.llm_engineering_usefulness.scenarios.mass_reduction_recommendation import (  # noqa: E402
    build_fixture as mr_build_fixture,
    task as mr_task,
)


@pytest.fixture
def recommendation_package(tmp_path: Path) -> Path:
    return mr_build_fixture.build_recommendation_package(
        tmp_path / "mr_fixture.aieng"
    )


def test_mr_fixture_is_well_formed_zip(recommendation_package: Path) -> None:
    assert recommendation_package.exists()
    with zipfile.ZipFile(recommendation_package, "r") as zf:
        names = set(zf.namelist())
        # No duplicate entries — every member listed at most once.
        assert len(zf.namelist()) == len(names)
    assert "manifest.json" in names
    assert "results/stress_by_feature.json" in names
    assert "results/computed_metrics.json" in names
    assert "simulation/cae_imports/parsed_features.json" in names
    # Phase 35 PR 4: structured design-targets resource is part of the package.
    assert "task/design_targets.yaml" in names


def test_mr_fixture_design_targets_validate_against_schema(
    recommendation_package: Path,
) -> None:
    """The packaged design_targets.yaml must validate against
    ``schemas/design_targets.schema.json`` so it is interchangeable with
    every other Phase-35-aware consumer."""
    import yaml as _yaml
    import jsonschema

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "aieng"
        / "schemas"
        / "design_targets.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    with zipfile.ZipFile(recommendation_package, "r") as zf:
        targets = _yaml.safe_load(
            zf.read("task/design_targets.yaml").decode("utf-8")
        )

    jsonschema.validate(instance=targets, schema=schema)
    assert targets["target_set_id"] == "mass_reduction_recommendation_v1"
    ids = {t["target_id"] for t in targets["targets"]}
    assert "mass_reduce_10pct" in ids
    assert "safety_factor_min" in ids
    by_id = {t["target_id"]: t for t in targets["targets"]}
    assert by_id["mass_reduce_10pct"]["comparator"] == "reduce_by_at_least"
    assert by_id["mass_reduce_10pct"]["threshold"] == 10.0
    assert by_id["safety_factor_min"]["comparator"] == ">="
    assert by_id["safety_factor_min"]["threshold"] == 1.5
    # Legacy/compat field names are also present (so legacy readers validate).
    assert by_id["mass_reduce_10pct"]["operator"] == "reduce_by_at_least"
    assert by_id["mass_reduce_10pct"]["value"] == 10.0
    assert by_id["safety_factor_min"]["operator"] == ">="
    assert by_id["safety_factor_min"]["value"] == 1.5
    # Honesty guards preserved.
    cp = targets["claim_policy"]
    assert cp["targets_are_acceptance_criteria"] is True
    assert cp["compliance_requires_evidence"] is True
    assert cp["physical_correctness_not_claimed"] is True


def test_mr_manifest_lists_task_design_targets(
    recommendation_package: Path,
) -> None:
    """Manifest resources block must surface the new task resource so
    Condition B's structured access path can discover it."""
    with zipfile.ZipFile(recommendation_package, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
    task_resources = manifest.get("resources", {}).get("task", [])
    assert "task/design_targets.yaml" in task_resources


def test_mr_condition_a_raw_dump_includes_design_targets(
    recommendation_package: Path,
) -> None:
    """Condition A must see the same numeric targets as Condition B — no
    structured-access advantage. The raw dump should embed both the YAML
    contents and the two target IDs."""
    raw = mr_task._build_condition_a_input(recommendation_package)
    assert "task/design_targets.yaml" in raw
    assert "mass_reduce_10pct" in raw
    assert "safety_factor_min" in raw
    # The numeric thresholds are present in the YAML body that A sees.
    assert "1.5" in raw
    assert "10.0" in raw or "10 percent" in raw


def test_mr_condition_b_prompt_references_structured_resource(
    recommendation_package: Path,
) -> None:
    """Condition B's user prompt should direct the model to the structured
    resource (task/design_targets.yaml) rather than relying only on
    prompt-inlined numeric targets."""
    prompt = mr_task._USER_PROMPT_CONDITION_B.format(
        package_path=str(recommendation_package),
        proposals=mr_task._PROPOSALS,
    )
    assert "task/design_targets.yaml" in prompt


def test_mr_proposals_no_longer_inlines_numeric_safety_factor() -> None:
    """The shared proposals block (used by both A and B) must not be the
    only place the numeric safety floor lives. The numeric requirements
    now live in the structured task resource; the proposals block points
    at it."""
    proposals = mr_task._PROPOSALS
    # The literal "safety factor of 1.5" inline numeric must be gone.
    assert "safety factor of 1.5" not in proposals.lower()
    # And the prompt should point at the structured resource.
    assert "task/design_targets.yaml" in proposals


def test_mr_fixture_carries_decision_relevant_artifacts(recommendation_package: Path) -> None:
    """The stress-by-feature artifact is the data the model must read to
    answer correctly. Pinning its contents — if these regress, the rubric
    stops scoring against what the model actually sees."""
    with zipfile.ZipFile(recommendation_package, "r") as zf:
        stress = json.loads(zf.read("results/stress_by_feature.json"))
        features = json.loads(zf.read("simulation/cae_imports/parsed_features.json"))
        materials = json.loads(zf.read("simulation/cae_imports/parsed_materials.json"))

    assert stress["yield_strength_mpa"] == 350.0
    assert stress["minimum_required_safety_factor"] == 1.5
    by_feat = {f["feature_ref"]: f for f in stress["features"]}

    # back_wall is the engineered "correct answer" — heavily over-designed.
    assert by_feat["back_wall"]["max_von_mises_stress_mpa"] == 22.0
    assert by_feat["back_wall"]["safety_factor"] > 10

    # central_rib and mounting_hole sit near the SF=1.5 floor, so the wrong
    # answers really would violate the constraint if the model picked them.
    assert by_feat["central_rib"]["safety_factor"] < 2.0
    assert by_feat["mounting_hole"]["safety_factor"] < 2.0

    # back_wall must also have the largest mass contribution so reducing
    # it produces a meaningful saving.
    by_feat_features = {f["id"]: f for f in features["features"]}
    masses = {f["id"]: f["mass_contribution_kg"] for f in features["features"]}
    assert masses["back_wall"] >= max(
        masses["central_rib"],
        masses["flange"],
        masses["mounting_bosses"],
    )
    assert materials["materials"][0]["yield_strength_mpa"] == 350.0


def _mr_score(completion: str):
    scorer = mr_task.recommendation_rubric_scorer()
    state = _StubTaskState(completion)
    return asyncio.run(scorer(state, Target("anything")))


def test_mr_rubric_correct_answer_with_evidence_scores_C() -> None:
    score = _mr_score(
        "The back_wall feature is at only 22 MPa with a safety factor of 15.9, "
        "indicating it is over-designed. Thinning it from 20 mm to 10 mm preserves "
        "a large margin against yield.\n\nAnswer: A"
    )
    assert score.value == "C"
    assert "final_answer_is_A" in score.explanation
    assert "evidence_grounded" in score.explanation


def test_mr_rubric_correct_answer_no_evidence_scores_P_or_C() -> None:
    """Naming A but providing no engineering reasoning still gets partial
    credit. C is OK if the patterns trigger broadly enough; P also acceptable."""
    score = _mr_score("Answer: A")
    assert score.value in ("C", "P")


def test_mr_rubric_wrong_final_answer_B_is_downgraded() -> None:
    score = _mr_score(
        "The central_rib carries the most load, so reducing it gives the "
        "largest mass saving.\n\nAnswer: B"
    )
    # Final-answer-B penalty (0.6) zeroes out the correct answer (0 hits)
    # and the evidence_grounded match contributes 0.4. Adjusted < 0.3 → I.
    assert score.value in ("I", "P")


def test_mr_rubric_wrong_final_answer_C_is_downgraded() -> None:
    score = _mr_score(
        "Enlarging the mounting_hole redistributes stress.\n\nAnswer: C"
    )
    assert score.value in ("I", "P")


def test_mr_rubric_no_letter_answer_scores_I_or_P() -> None:
    score = _mr_score("It depends on many factors.")
    assert score.value in ("I", "P")


def test_mr_condition_a_runs_end_to_end_with_mock_model(recommendation_package: Path) -> None:
    task = mr_task.mass_reduction_recommendation_condition_a(
        fixture_path=str(recommendation_package)
    )
    logs = inspect_eval(task, model="mockllm/model", display="none")
    assert logs
    log = logs[0]
    assert log.status == "success"
    assert log.samples is not None and len(log.samples) == 1
    sample = log.samples[0]
    assert sample.scores is not None
    assert "recommendation_rubric_scorer" in sample.scores
    assert "token_efficiency_scorer" in sample.scores


def test_mr_condition_b_runs_end_to_end_with_mock_model(recommendation_package: Path) -> None:
    task = mr_task.mass_reduction_recommendation_condition_b(
        fixture_path=str(recommendation_package)
    )
    logs = inspect_eval(task, model="mockllm/model", display="none")
    assert logs
    log = logs[0]
    assert log.status == "success"
    sample = log.samples[0]
    assert sample.scores is not None
    assert "recommendation_rubric_scorer" in sample.scores
    assert "token_efficiency_scorer" in sample.scores


# ===========================================================================
# Scenario 3 — stress_concentrator_recommendation
# ===========================================================================

from benchmarks.llm_engineering_usefulness.scenarios.stress_concentrator_recommendation import (  # noqa: E402
    build_fixture as sc_build_fixture,
    task as sc_task,
)


@pytest.fixture
def concentrator_package(tmp_path: Path) -> Path:
    return sc_build_fixture.build_concentrator_package(
        tmp_path / "sc_fixture.aieng"
    )


def test_sc_fixture_is_well_formed_zip(concentrator_package: Path) -> None:
    assert concentrator_package.exists()
    with zipfile.ZipFile(concentrator_package, "r") as zf:
        names = set(zf.namelist())
    assert "manifest.json" in names
    assert "results/stress_by_feature.json" in names
    assert "results/computed_metrics.json" in names


def test_sc_fixture_engineered_concentrator_below_sf_floor(concentrator_package: Path) -> None:
    """The fillet_inner_corner feature must be the single SF < 1.5
    feature; if this regresses the scenario stops being measurable."""
    with zipfile.ZipFile(concentrator_package, "r") as zf:
        stress = json.loads(zf.read("results/stress_by_feature.json"))
    floor = stress["minimum_required_safety_factor"]
    below_floor = [f for f in stress["features"] if f["safety_factor"] < floor]
    assert len(below_floor) == 1
    assert below_floor[0]["feature_ref"] == "fillet_inner_corner"
    assert below_floor[0]["safety_factor"] == 1.25
    # And every other feature must be comfortably above the floor.
    for f in stress["features"]:
        if f["feature_ref"] == "fillet_inner_corner":
            continue
        assert f["safety_factor"] >= 2.5, f"{f['feature_ref']} too close to floor"


def _sc_score(completion: str):
    scorer = sc_task.concentrator_rubric_scorer()
    state = _StubTaskState(completion)
    return asyncio.run(scorer(state, Target("anything")))


def test_sc_rubric_full_correct_answer_scores_C() -> None:
    score = _sc_score(
        "The stress concentrator is fillet_inner_corner, a 1 mm fillet at "
        "the central_rib/flange joint. It hits 280 MPa von Mises (yield "
        "350 MPa, safety factor 1.25 — below the declared 1.5 floor). "
        "I recommend increasing the fillet radius. Re-analysis would be "
        "required to confirm the new safety factor."
    )
    assert score.value == "C"
    assert "names_concentrator_feature" in score.explanation
    assert "proposes_radius_increase" in score.explanation


def test_sc_rubric_diagnosis_only_no_proposal_scores_P() -> None:
    score = _sc_score(
        "The fillet_inner_corner is the concentrator at 280 MPa."
    )
    assert score.value in ("P", "C")  # partial without a proposal


def test_sc_rubric_overclaim_penalised() -> None:
    score = _sc_score(
        "Increase the fillet radius on fillet_inner_corner. This will "
        "restore the safety factor and the part will be guaranteed safe."
    )
    # The overclaim penalty must downgrade an otherwise-correct response.
    assert score.value in ("P", "I")


def test_sc_rubric_wrong_feature_penalised() -> None:
    score = _sc_score(
        "The fillet_outer_corner is the concentrator. Remove the central_rib."
    )
    assert score.value in ("I", "P")


def test_sc_condition_a_runs_end_to_end_with_mock_model(concentrator_package: Path) -> None:
    task = sc_task.stress_concentrator_recommendation_condition_a(
        fixture_path=str(concentrator_package)
    )
    logs = inspect_eval(task, model="mockllm/model", display="none")
    assert logs
    log = logs[0]
    assert log.status == "success"
    sample = log.samples[0]
    assert sample.scores is not None
    assert "concentrator_rubric_scorer" in sample.scores
    assert "token_efficiency_scorer" in sample.scores


def test_sc_condition_b_runs_end_to_end_with_mock_model(concentrator_package: Path) -> None:
    task = sc_task.stress_concentrator_recommendation_condition_b(
        fixture_path=str(concentrator_package)
    )
    logs = inspect_eval(task, model="mockllm/model", display="none")
    assert logs
    log = logs[0]
    assert log.status == "success"
    sample = log.samples[0]
    assert sample.scores is not None
    assert "concentrator_rubric_scorer" in sample.scores
    assert "token_efficiency_scorer" in sample.scores


# ===========================================================================
# Scenario 4 — setup_correction_missing_items
# ===========================================================================

from benchmarks.llm_engineering_usefulness.scenarios.setup_correction_missing_items import (  # noqa: E402
    build_fixture as sct_build_fixture,
    task as sct_task,
)


@pytest.fixture
def setup_correction_package(tmp_path: Path) -> Path:
    return sct_build_fixture.build_setup_correction_package(
        tmp_path / "sct_fixture.aieng"
    )


def test_sct_fixture_actually_missing_loads_and_solver_settings(setup_correction_package: Path) -> None:
    """The whole point of this fixture is the negative space — these two
    artifacts must be absent. If a future build_fixture refactor adds them
    back, the scenario stops measuring what its rubric scores against."""
    with zipfile.ZipFile(setup_correction_package, "r") as zf:
        names = set(zf.namelist())
    assert "simulation/cae_imports/parsed_loads.json" not in names
    assert "simulation/solver_settings.json" not in names
    # And these must be present.
    assert "simulation/cae_imports/parsed_materials.json" in names
    assert "simulation/cae_imports/parsed_boundary_conditions.json" in names
    assert "simulation/mesh/mesh_metadata.json" in names
    assert "simulation/load_cases/load_case_001.json" in names


def test_sct_fixture_load_case_has_dangling_reference(setup_correction_package: Path) -> None:
    """load_case_001 must reference a load that the missing
    parsed_loads.json would have defined."""
    with zipfile.ZipFile(setup_correction_package, "r") as zf:
        lc = json.loads(zf.read("simulation/load_cases/load_case_001.json"))
    assert lc["load_refs"] == ["load_lateral"]


def _sct_score(completion: str):
    scorer = sct_task.setup_correction_rubric_scorer()
    state = _StubTaskState(completion)
    return asyncio.run(scorer(state, Target("anything")))


def test_sct_rubric_full_answer_scores_C() -> None:
    score = _sct_score(
        "The package is not ready for the solver.\n"
        "  - parsed_loads.json is missing — no load definitions.\n"
        "  - solver_settings.json is missing — no analysis step.\n"
        "  - load_case_001 references load_lateral but the load is "
        "undefined (dangling reference).\n"
        "Plan: add parsed_loads.json defining load_lateral; add "
        "solver_settings.json."
    )
    assert score.value == "C"


def test_sct_rubric_partial_answer_two_of_three() -> None:
    score = _sct_score(
        "The parsed_loads file is missing and solver settings are missing. "
        "Add the missing files."
    )
    assert score.value in ("P", "C")


def test_sct_rubric_overclaim_ready_to_run_downgraded() -> None:
    score = _sct_score(
        "The setup is complete and ready for solver. Run the solver as-is."
    )
    assert score.value in ("I", "P")


def test_sct_rubric_invents_missing_mesh_downgraded() -> None:
    score = _sct_score(
        "parsed_loads is missing and the mesh is missing. Add the loads."
    )
    # Mesh-missing hallucination penalty must downgrade.
    assert score.value in ("P", "I")


# Issue #60 — negation-aware hallucination penalties -------------------------

def test_sct_rubric_penalises_unsafe_positive_ready_for_solver() -> None:
    score = _sct_score("The setup looks good. ready for solver.")
    assert score.value in ("I", "P")
    assert "ready for solver" in score.explanation


def test_sct_rubric_penalises_unsafe_positive_setup_complete() -> None:
    score = _sct_score("The setup is complete.")
    assert score.value in ("I", "P")
    assert "the setup is complete" in score.explanation


def test_sct_rubric_does_not_penalise_ready_for_solver_false() -> None:
    """A correct answer that quotes the structured field
    ``ready_for_solver: false`` must not be downgraded."""
    score = _sct_score(
        "The package is not ready for the solver.\n"
        "  - parsed_loads.json is missing.\n"
        "  - solver_settings.json is missing.\n"
        "  - load_case_001 references load_lateral but the load is undefined.\n"
        "Preprocessing summary: ready_for_solver: false\n"
        "Plan: add parsed_loads.json and solver_settings.json."
    )
    assert score.value == "C"


def test_sct_rubric_does_not_penalise_not_ready_for_solver() -> None:
    score = _sct_score(
        "The package is not ready for solver.\n"
        "parsed_loads.json is missing and solver_settings.json is missing.\n"
        "load_case_001 references undefined load_lateral."
    )
    assert score.value == "C"


def test_sct_rubric_does_not_penalise_setup_is_not_complete() -> None:
    score = _sct_score(
        "The setup is not complete.\n"
        "parsed_loads.json is missing and solver_settings.json is missing.\n"
        "load_case_001 references undefined load_lateral."
    )
    assert score.value == "C"


def test_sct_rubric_does_not_penalise_ready_for_solver_spaced_false() -> None:
    """The spaced form ``ready for solver: false`` (without underscore)
    was also matched by the old substring penalty; the fix must catch it."""
    score = _sct_score(
        "parsed_loads.json is missing.\n"
        "solver_settings.json is missing.\n"
        "load_case_001 references undefined load_lateral.\n"
        "ready for solver: false"
    )
    assert score.value == "C"


def test_sct_condition_a_runs_end_to_end_with_mock_model(setup_correction_package: Path) -> None:
    task = sct_task.setup_correction_missing_items_condition_a(
        fixture_path=str(setup_correction_package)
    )
    logs = inspect_eval(task, model="mockllm/model", display="none")
    assert logs
    log = logs[0]
    assert log.status == "success"
    sample = log.samples[0]
    assert sample.scores is not None
    assert "setup_correction_rubric_scorer" in sample.scores
    assert "token_efficiency_scorer" in sample.scores


def test_sct_condition_b_runs_end_to_end_with_mock_model(setup_correction_package: Path) -> None:
    task = sct_task.setup_correction_missing_items_condition_b(
        fixture_path=str(setup_correction_package)
    )
    logs = inspect_eval(task, model="mockllm/model", display="none")
    assert logs
    log = logs[0]
    assert log.status == "success"
    sample = log.samples[0]
    assert sample.scores is not None
    assert "setup_correction_rubric_scorer" in sample.scores
    assert "token_efficiency_scorer" in sample.scores
