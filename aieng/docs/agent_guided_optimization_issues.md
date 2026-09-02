# Agent-guided optimization loop — issue breakdown

Companion to [`agent_guided_optimization_direction.md`](agent_guided_optimization_direction.md).
This is the canonical record of the planned issue set; the same content is filed
as GitHub issues. Each issue **extends** the existing design-study / topology /
CAD-parameter / CAE / artifact / provenance mechanisms — it does not duplicate
them.

Conventions used below:
- **Dependencies** reference other issues in this set or existing subsystems.
- **Labels** are suggestions from the repo's available set (`enhancement`,
  `documentation`, `help wanted`, `good first issue`).
- Honesty posture: advisory ranking, candidate exploration, approval-gated
  acceptance — never "optimal" / "certified" / "autonomous approval".

### Filed GitHub issues

| Doc item | GitHub | Title |
|---|---|---|
| Epic | [#36](https://github.com/armpro24-blip/cad-cae-copilot/issues/36) | Agent-guided optimization loop |
| Issue 1 | [#37](https://github.com/armpro24-blip/cad-cae-copilot/issues/37) | Optimization problem artifact schema |
| Issue 2 | [#38](https://github.com/armpro24-blip/cad-cae-copilot/issues/38) | Candidate parameter generation (sampler) |
| Issue 3 | [#39](https://github.com/armpro24-blip/cad-cae-copilot/issues/39) | Derived candidate workspaces |
| Issue 4 | [#40](https://github.com/armpro24-blip/cad-cae-copilot/issues/40) | Candidate evaluation → CAE metrics |
| Issue 5 | [#41](https://github.com/armpro24-blip/cad-cae-copilot/issues/41) | Candidate ranking and recommendation |
| Issue 6 | [#42](https://github.com/armpro24-blip/cad-cae-copilot/issues/42) | Approval-gated candidate acceptance |
| Issue 7 | [#43](https://github.com/armpro24-blip/cad-cae-copilot/issues/43) | Optimization summary report |
| Issue 8 | [#44](https://github.com/armpro24-blip/cad-cae-copilot/issues/44) | First demo case (open-loop sizing MVP) |
| Issue 9 | [#45](https://github.com/armpro24-blip/cad-cae-copilot/issues/45) | Plan Phase 2 iterative optimization |
| Issue 10 | [#46](https://github.com/armpro24-blip/cad-cae-copilot/issues/46) | Plan feature-level shape optimization |

---

## Epic: Agent-guided optimization loop

**Motivation.** Give an external agent a deterministic, auditable way to explore
CAD/CAE design candidates — define variables/objectives/constraints, generate
candidates, evaluate with CAE-backed evidence, rank advisorily, and accept only
with human approval. Start with practical parameter/sizing optimization and
extend in stages to feature-level shape, topology-to-sizing, and Pareto.

**Scope.** Overall architecture, artifact contract, staged roadmap, and a unified
`opt.*` MCP tool layer that wraps the existing (REST-only) design-study lifecycle
and adds candidate generation + problem definition. No model training; agent is
the orchestrator; backend tools are deterministic.

**Acceptance criteria.**
- Direction doc + this issue breakdown merged.
- Artifact contract reconciles with existing `design_study_*` / `candidates/<id>/`
  layout (no parallel candidate store).
- Phase 1 (Issues 1–8) defined with clear, testable acceptance criteria.
- Honesty flags preserved; baseline never auto-overwritten; acceptance gated.

**Dependencies.** Existing design-study converters (`aieng/src/aieng/converters/design_study*.py`),
`opt.*` topology tools, `cad.edit_parameter` / `cad.list_editable_parameters`,
CAE metrics (`results/computed_metrics.json`), package I/O + provenance.

**Labels.** `enhancement`

---

## Issue 1 — Define optimization problem artifact schema

**Motivation.** The design-study problem is validated in-code with no JSON schema;
the optimization front-end needs a durable, versioned contract for variables,
objectives, constraints, and the decision log.

**Scope.**
- Add JSON schemas under `aieng/src/aieng/schemas/` for the new artifacts:
  `optimization_study`, `optimization_variables`, `optimization_objectives`,
  `optimization_constraints`, `optimization_decision_log`.
- Schemas must be **consistent with** (not a fork of) `analysis/design_study_problem.json`.
- Variables carry: `featureId`, `parameterName`, `cad_parameter_name`,
  `current_value`, `min_value`, `max_value`, `scope`, candidate-ID linkage.
- Decision-log entries carry `decision`, closed-vocabulary `reason_codes[]`,
  `requires_human_review`, optional free-text `note`.

**Acceptance criteria.**
- Schema covers variables, objectives, constraints, bounds, candidate IDs,
  metrics, and decision logs.
- Artifacts validate and are saved under `.aieng` via `write_artifact_to_package`.
- Compatible with existing provenance (`audit/events.jsonl`,
  `state/revalidation_status.json`).
- Unit tests validate good/bad documents.

**Dependencies.** None (foundational). Informs Issues 2–7.

**Labels.** `enhancement`

---

## Issue 2 — Implement candidate parameter generation (sampler)

**Motivation.** Design study today *consumes* externally-supplied candidate
patches; there is no sampler that *emits* candidate parameter sets from variable
bounds. This is the core new capability for Phase 1.

**Scope.**
- New deterministic sampler supporting **grid search**, **random search**, and
  **Latin hypercube sampling**.
- Reads `optimization_variables.json`; respects each variable's bounds and scope.
- Emits candidate patches into the existing `patches/design_candidates/<cid>.json`
  format (so the existing executor/evaluator/ranker consume them unchanged).
- Caps candidate count; `log()`s anything dropped (no silent truncation).
- Deterministic given a seed (varied per-index for random/LHS); no `Math.random`
  surprises in CI.

**Acceptance criteria.**
- Supports grid / random / Latin hypercube sampling.
- Respects variable bounds.
- Emits deterministic candidate definitions in the existing patch format.
- Does not modify baseline.
- Unit tests for each sampler + bound-respect + cap behavior.

**Dependencies.** Issue 1 (variable schema). Reuses `patches/design_candidates/`.

**Labels.** `enhancement`

---

## Issue 3 — Create derived candidate workspaces

**Motivation.** Each generated candidate needs isolated geometry + analysis with
the baseline untouched. The design-study executor already does this; this issue
wires generation → execution and hardens failure handling.

**Scope.**
- For each generated candidate, run the existing design-study candidate executor
  (`execute_design_study_candidate`) with the wired `make_candidate_recompiler()`
  so geometry actually regenerates into `candidates/<cid>/geometry/`.
- Record build failures cleanly (status + reason code in the candidate's
  diagnostics) without aborting the batch.

**Acceptance criteria.**
- Each candidate has isolated geometry and analysis artifacts under
  `candidates/<cid>/`.
- Baseline is never overwritten (assert in tests).
- Failed regeneration is recorded cleanly (`candidate_build_failed` reason code),
  batch continues.

**Dependencies.** Issue 2. Reuses design-study execution + candidate recompiler.

**Labels.** `enhancement`

---

## Issue 4 — Connect candidate evaluation to CAE metrics

**Motivation.** Candidates must be scored on engineering metrics, with missing
CAE results handled honestly.

**Scope.**
- Reuse `design_study_evaluation` + the CAE pipeline (`cae-evaluate`) to populate
  `candidates/<cid>/analysis/evaluation.json`.
- Collect mass, volume, max von Mises stress, max displacement, minimum safety
  factor, and pass/fail status (against constraints).
- When CAE results are absent (e.g. no `ccx`), mark stress/displacement metrics
  `unknown` explicitly — never fabricate.

**Acceptance criteria.**
- Candidate evaluation collects mass, max stress, max displacement, safety
  factor, and pass/fail.
- Missing CAE results handled explicitly (`unknown` / `needs_user_input`).
- Evaluation output written to `.aieng` under `candidates/<cid>/analysis/`.
- Tests cover the with-CAE and without-CAE (static-metric-only) paths.

**Dependencies.** Issue 3. Reuses CAE metrics (`results/computed_metrics.json`),
`design_study_cae_evaluation`.

**Labels.** `enhancement`

---

## Issue 5 — Implement candidate ranking and recommendation

**Motivation.** Turn evaluated candidates into an advisory ranking with an
explainable recommendation.

**Scope.**
- Reuse `design_study_ranking` for constraint filtering + weighted scoring +
  feasibility classification (`feasible` / `infeasible` / `unknown` / `failed`).
- Add `opt.explain_recommendation`: read the ranking + decision log and produce a
  human-readable advisory citing explicit metrics and reason codes.
- Recommendation is **advisory**, never production sign-off; low-confidence /
  missing-metric cases produce `needs_more_evaluation`.

**Acceptance criteria.**
- Supports constraint filtering.
- Supports weighted scoring.
- Explains recommendation with explicit metrics and reason codes.
- Marks recommendation as advisory, not production sign-off.
- Tests cover feasible-winner, all-infeasible, and unknown-metric cases.

**Dependencies.** Issue 4. Reuses `design_study_candidate_ranking.json`.

**Labels.** `enhancement`

---

## Issue 6 — Add approval-gated candidate acceptance

**Motivation.** Accepting a candidate must be explicit, gated, and must never
silently change the baseline.

**Scope.**
- Expose `opt.accept_candidate` (`requires_approval=True`) wrapping
  `accept_design_study_candidate`.
- Acceptance copies the candidate into `accepted/<cid>/` and writes
  `analysis/...acceptance.json` + acceptance provenance; baseline geometry/CAE
  artifacts untouched.
- Honor the existing acceptance gates (must be best/feasible unless
  `override_unsafe`).

**Acceptance criteria.**
- No automatic baseline overwrite (assert in tests).
- Accepted candidate recorded in acceptance artifact + `accepted/<cid>/`.
- User-approval requirement is explicit (`requires_approval=True`, routed through
  the broker).

**Dependencies.** Issue 5. Reuses `design_study_acceptance`.

**Labels.** `enhancement`

---

## Issue 7 — Add optimization summary report

**Motivation.** Provide a single reproducible report of the whole study.

**Scope.**
- `opt.write_report` → `diagnostics/optimization_report.json`.
- Report aggregates: problem definition, variables/objectives/constraints, all
  candidates + metrics, ranking, failed candidates (with reason codes), and the
  advisory recommendation.
- Report is reconstructable purely from `.aieng` artifacts (no in-memory state).

**Acceptance criteria.**
- Report includes problem definition, candidates, metrics, ranking, failed
  candidates, and recommendation.
- Report is reproducible from `.aieng` artifacts.
- Test reconstructs the report from a fixture package.

**Dependencies.** Issues 1–6.

**Labels.** `enhancement`, `documentation`

---

## Issue 8 — Add first demo case (open-loop sizing MVP)

**Motivation.** Prove the open loop end-to-end on a deterministic example, mirror
the design-study demo pattern (`test_design_study_demo.py`).

**Scope.**
- Simple bracket or plate-with-hole fixture.
- Variables include at least wall thickness + a fillet or hole parameter.
- Objective: minimize mass. Constraints: max stress + max displacement.
- Full flow: define → sample (≥5 candidates) → derive workspaces → evaluate →
  rank → recommend. Deterministic static metrics so CI needs no solver.

**Acceptance criteria.**
- Simple bracket / plate-with-hole example.
- Variables include ≥ wall thickness and a fillet or hole parameter.
- Objective minimizes mass.
- Constraints include max stress and max displacement.
- Produces ≥5 candidate evaluations and a recommendation.
- Lands as a backend regression test (fast, no external solver).

**Dependencies.** Issues 1–7.

**Labels.** `enhancement`, `good first issue`

---

## Issue 9 — Plan Phase 2 iterative optimization

**Motivation.** Document the closed-loop design before building it; do not
implement until Phase 1 is stable.

**Scope (planning only).**
- Document proposed support for **SLSQP** (smooth continuous) and **Bayesian
  optimization** (expensive low-dimensional CAE).
- Define convergence criteria (objective delta, max iterations, evaluation
  budget).
- Define failed-candidate handling within the loop.
- Specify how the agent drives the loop (each tool call bounded; no unbounded
  in-tool loop).

**Acceptance criteria.**
- Document proposed SLSQP + Bayesian support.
- Define convergence criteria.
- Define failed-candidate handling.
- No implementation required unless Phase 1 is stable.

**Dependencies.** Phase 1 (Issues 1–8).

**Labels.** `documentation`, `enhancement`

**Delivered:** [`phase2_iterative_optimization_plan.md`](phase2_iterative_optimization_plan.md).

---

## Issue 10 — Plan feature-level shape optimization

**Motivation.** Define a safe, stable-parameter path to "shape" optimization
without arbitrary freeform boundary movement.

**Scope (planning only).**
- Define feature-level shape optimization scope.
- Explicitly **exclude** arbitrary freeform boundary / NURBS / FFD optimization
  in early versions.
- List supported feature parameters (fillet radius, hole/slot size + position,
  rib/gusset profile parameters) and how they reuse the Phase 1/2 framework.

**Acceptance criteria.**
- Feature-level shape optimization scope defined.
- Arbitrary freeform boundary optimization explicitly excluded for early
  versions.
- Supported feature parameters listed.

**Dependencies.** Phase 1 (Issues 1–8); relates to Issue 9.

**Delivered:** [`phase3_feature_shape_optimization_plan.md`](phase3_feature_shape_optimization_plan.md).

**Labels.** `documentation`, `enhancement`
