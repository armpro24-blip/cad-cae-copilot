# Agent-guided optimization loop — development direction

Status: **proposed direction** (planning document, not an implementation contract).
Scope: defines the next development line for CAD/CAE optimization in
`cad-cae-copilot`. Builds on the **existing** design-study, topology-optimization,
CAD-parameter, CAE-evaluation, artifact, and provenance mechanisms already in the
repo — it does **not** introduce a parallel stack.

Honesty posture: all wording here is deliberately conservative. This feature
produces **agent-guided candidate exploration with advisory ranking and
approval-gated acceptance**, backed by deterministic backend tools and auditable
artifacts. It does **not** produce "optimal designs", "production-certified
optimization", or "automatic engineering approval".

---

## 0. What already exists (grounded inventory)

This direction extends working code. Before proposing anything new, here is the
factual state of the relevant subsystems (verified against source, not assumed).

### Already implemented (reusable as-is)

| Capability | Where | Notes |
|---|---|---|
| **Design-study candidate lifecycle** (validate → execute → evaluate → rank → accept → hints → cae-evaluate) | `aieng/src/aieng/converters/design_study*.py` (7 modules) | PR1–PR6. Strong safety contracts: baseline never overwritten, no auto-promotion, advisory-only acceptance. |
| Design-study REST endpoints (7 routes) | `aieng-ui/backend/app/routers/project_workflows.py` | `POST /api/projects/{id}/design-study/{validate,candidates/{cid}/run,candidates/{cid}/evaluate,rank,hints,candidates/{cid}/accept,candidates/{cid}/cae-evaluate}` |
| **Candidate workspaces** (isolated geometry + analysis + provenance) | `candidates/<cid>/…`, `accepted/<cid>/…` | Zip members inside the `.aieng` package; baseline files untouched. |
| **Topology optimization** (pure-numpy SIMP 2D/3D) + writeback | `aieng/src/aieng/converters/topology_optimization.py`; `opt.*` tools in `runtime_tool_registry.py` | `opt.derive_problem_from_cae`, `opt.run_topology_optimization`, `opt.writeback_to_shape_ir`, `opt.run_assembly_topology_optimization`. Honest limitation flags (`production_ready:false`, plane-stress, experimental-3D). |
| **CAD parameter editing** (deterministic, no LLM) | `cad.edit_parameter`, `cad.list_editable_parameters`; `cad_generation.py`, `parameter_binding.py` | Constant text-replacement in `geometry/source.py`, min/max validation, `regression_diff` verdict. |
| **Editable-parameter index** | `parameter_binding.build_parameter_index` | Flattens `feature_graph.features[].parameters` with scope (`local`/`global`/`unscoped`). This is the natural source for "optimization variables". |
| **CAE evaluation metrics** | `cae.extract_solver_results` → `results/computed_metrics.json`; field summaries under `results/fields/` | Fields: `max_displacement`, `max_von_mises_stress`, plus per-load-case metrics. |
| **Package I/O / provenance / audit** | `project_io.write_artifact_to_package` (atomic); `provenance/tool_trace.json`, `provenance/conversion_manifest.json`, `state/revalidation_status.json`; `audit/events.jsonl` | Atomic temp-file + move; append-only audit. |
| **MCP tool registration + guide-gate + approval-gate** | `runtime.register_tool`, `runtime_tool_registry.py`, `mcp_server.py` | `opt.*` already maps to the `cae` guide topic. Approval modes via `AIENG_MCP_MANAGED_APPROVAL` / `AIENG_MCP_BLOCK_APPROVAL_TOOLS`. |

### Partial / gaps (this is the work)

| Gap | Detail |
|---|---|
| **No candidate *generation*** | Design study **consumes** externally-supplied candidate patches in `patches/design_candidates/<id>.json`. There is **no** sampler (grid / random / Latin hypercube) that *emits* candidate parameter sets from variable bounds. |
| **No variable / objective / constraint *definition* layer** | The design-study problem (`analysis/design_study_problem.json`) is validated and recorded, but there is no tool that **builds** it from the editable-parameter index + user goals. Objectives/constraints are "recorded, not executed". |
| **No iterative search loop** | Every design-study step is explicit and single-shot. There is no propose→evaluate→propose loop, no convergence criteria, no SLSQP/Bayesian driver. |
| **Design study is REST-only** | **Not exposed as MCP tools.** An external agent cannot drive design study through the MCP server today — only via HTTP. |
| **Sizing ↔ topology are disconnected** | Topology opt writes a density field; there is no documented chain from a topology result into a parameterized sizing study. |
| **No multi-objective / Pareto** | Ranking is single-objective weighted scoring + constraint filtering only. |

**Architectural conclusion:** the new feature is mostly (a) a **candidate-generation
+ problem-definition front-end** that produces design-study-compatible artifacts,
and (b) a **unified `opt.*` MCP tool layer** that wraps the existing REST design
study + the new front-end. We **extend**, we do not duplicate.

---

## 1. Product goal

Build an **agent-guided optimization workflow** where the external agent (the
orchestrator) can, using deterministic backend tools:

1. understand user goals and constraints (in the agent; recorded as structured intent),
2. identify editable CAD parameters (reuse `cad.list_editable_parameters`),
3. define an optimization problem (variables, objectives, constraints, bounds),
4. generate candidate designs (grid / random / Latin hypercube sampling),
5. run or request CAE evaluation (reuse the existing CAE pipeline),
6. compare candidates using engineering metrics (mass, max stress, max
   displacement, safety factor, pass/fail),
7. rank and explain trade-offs (advisory ranking + reason codes),
8. recommend next steps,
9. preserve all artifacts and provenance in the `.aieng` package,
10. require **human approval** before accepting any design.

The agent does the reasoning and orchestration; the **backend does the
deterministic work** (CAD edits, sampling, CAE evaluation, scoring, artifact
generation). No model is trained. No autonomous baseline mutation.

---

## 2. Non-goals for the first versions

The first versions will **not** attempt:

- **Fully autonomous production-grade structural optimization.** This is
  candidate exploration, not a certified optimizer.
- **General 3D freeform shape optimization** (arbitrary boundary-node movement,
  full NURBS/FFD).
- **Production-certified 3D topology optimization.** The existing SIMP 3D path
  stays explicitly experimental (`production_ready:false`).
- **Automatic baseline overwrite.** Baseline geometry/CAE artifacts are never
  modified by the loop; candidates live in derived workspaces only.
- **Hidden or non-auditable agent reasoning treated as engineering evidence.**
  All decisions are captured as explicit decision logs, **reason codes**,
  metrics, and artifacts. The agent's free-text rationale is a *note*, never the
  evidence of record.

When confidence is low or inputs are missing, the loop **asks the user** or marks
the result `needs_user_input` / `unknown` — it never guesses a target or claims a
result it does not have.

---

## 3. Recommended staged roadmap

### Phase 1 — Agent-guided parameter study / sizing optimization MVP

The practical starting point. Operate on **stable, editable CAD feature
parameters** that already flow through `cad.edit_parameter`:

- wall thickness, hole diameter, fillet radius, rib height, rib thickness,
  gusset dimensions, simple feature positions.

Required backend capabilities (most reuse existing code):

- define optimization variables (from the editable-parameter index),
- define objectives,
- define constraints,
- generate candidate parameter sets — **new sampler**,
- create derived candidate workspaces — **reuse design-study `candidates/<id>/`**,
- run CAD regeneration — **reuse `cad.edit_parameter` / candidate recompiler**,
- run or request CAE evaluation — **reuse the CAE pipeline + `cae-evaluate`**,
- collect metrics — **reuse `results/computed_metrics.json` extraction**,
- rank candidates — **reuse `design_study_ranking`**,
- write optimization artifacts to `.aieng`.

Start with simple, deterministic search:

- grid search,
- random search,
- Latin hypercube sampling (LHS),
- optionally **SciPy SLSQP** for low-dimensional continuous sizing problems.

Phase 1 is "open-loop": sample a batch, evaluate, rank, recommend. No adaptive
proposal yet.

### Phase 2 — CAE-backed sizing optimization loop

Add **iterative** behavior on top of Phase 1:

- propose next candidates based on previous results,
- support constrained optimization,
- support failed-candidate handling (record cleanly, continue),
- support convergence criteria (objective delta, max iterations, budget),
- support optimization summary reports.

Optional algorithms (only when Phase 1 is stable):

- **Bayesian optimization** for expensive low-dimensional CAE evaluations,
- **SLSQP** for smooth continuous variables,
- **genetic algorithm** only when discrete variables are required.

### Phase 3 — Feature-level shape optimization

Do **not** start with arbitrary boundary-node movement or full NURBS/FFD.
Implement "shape" optimization through **stable CAD feature parameters**, reusing
the Phase 1/2 framework unchanged:

- fillet radius optimization,
- hole / slot size optimization,
- hole / slot position optimization,
- rib / gusset profile parameter optimization.

The only new work is recognizing which feature parameters are shape-bearing; the
search/eval/rank machinery is identical.

### Phase 4 — 2D / extrudable topology-to-sizing workflow

Use the **existing** topology optimization only within its current safe scope
(2D / extrudable; 3D stays experimental). Target chain:

1. run 2D or extrudable topology optimization (`opt.run_topology_optimization`),
2. convert the result to a CAD-friendly representation
   (`opt.writeback_to_shape_ir`, `method=contour` → B-Rep),
3. parameterize selected features of the recovered body,
4. run sizing optimization (Phase 1/2),
5. validate with CAE,
6. preserve the full chain in `.aieng`.

Do **not** claim general production-grade 3D topology-to-CAD.

### Phase 5 — Multi-objective / Pareto exploration

Only after candidate generation and CAE-backed ranking are reliable. Start
simple:

- weighted scoring (already present),
- constraint filtering (already present),
- advisory ranking,
- two-objective Pareto plots.

Later, consider NSGA-II, Bayesian multi-objective, hypervolume-based selection —
each as its own gated increment.

---

## 4. Proposed artifact contract

**Principle: reuse the design-study artifacts that already exist; add new
artifacts only for genuinely new concepts (variables, sampling, decision log).**
The task's suggested `optimization_*.json` names are reconciled below against the
real repo layout so we do not create two competing candidate stores.

### Reuse (already written by design study / topology opt)

```text
analysis/design_study_problem.json          # variables + objective + constraints (extend, don't fork)
analysis/design_study_iterations.json        # executed-candidate iteration history
analysis/design_study_candidate_ranking.json # advisory ranking + scores + feasibility + confidence
analysis/design_study_candidate_hints.json   # next-candidate proposals
analysis/design_study_acceptance.json        # approval-gated acceptance record
analysis/topology_optimization.json          # topology result (Phase 4)
patches/design_candidates/<cid>.json         # candidate patch (now machine-GENERATED by the sampler)
candidates/<cid>/geometry/…                  # isolated candidate geometry
candidates/<cid>/analysis/evaluation.json    # normalized metrics (mass/stress/deflection/SF)
candidates/<cid>/provenance/…                # candidate provenance
accepted/<cid>/…                             # accepted candidate workspace
diagnostics/design_study_*                    # validation / scoring / acceptance diagnostics
```

### New artifacts (the optimization-study front-end)

```text
analysis/optimization_study.json             # the study envelope: links to design_study_problem,
                                             #   chosen algorithm, sampling config, budget, status
analysis/optimization_variables.json         # resolved variables bound to feature parameters
                                             #   (featureId / parameterName / cad_parameter_name / bounds / scope)
analysis/optimization_objectives.json        # objective(s): metric, direction, weight
analysis/optimization_constraints.json       # constraints: metric, op, limit, hard/soft
analysis/optimization_decision_log.json      # every backend decision with reason codes (see below)
diagnostics/optimization_report.json         # reproducible end-to-end report
```

> Implementation note: `optimization_variables/objectives/constraints.json` MAY be
> folded into `analysis/optimization_study.json` as sub-objects if the team
> prefers fewer members; they are listed separately here for clarity. The hard
> requirement is that they are **derived from / consistent with**
> `design_study_problem.json`, not a second source of truth. `candidate_evaluations`
> and `optimization_ranking` from the task map onto the existing
> `candidates/<cid>/analysis/evaluation.json` + `design_study_candidate_ranking.json`
> and SHOULD NOT be re-introduced under new names.

### Decision log — auditable reason codes

Every non-trivial backend decision (algorithm choice, candidate skip, constraint
violation, convergence stop, recommendation) appends an entry to
`analysis/optimization_decision_log.json`:

```json
{
  "decision": "select_random_search",
  "reason_codes": [
    "initial_mvp",
    "no_gradient_available",
    "small_number_of_design_variables",
    "cae_evaluation_available"
  ],
  "requires_human_review": true
}
```

Reason codes are a **closed, documented vocabulary** (e.g. `initial_mvp`,
`no_gradient_available`, `discrete_variables_present`, `expensive_cae_eval`,
`constraint_violation`, `candidate_build_failed`, `converged_objective_delta`,
`budget_exhausted`, `needs_user_input`). The agent's prose rationale may be
attached as a `note`, but the **reason codes are the machine-auditable record**.

All new artifacts carry the existing provenance discipline: written atomically
via `write_artifact_to_package`, recorded in `audit/events.jsonl`, and marked
`claim_advancement` honestly (advisory, not certified).

---

## 5. MCP / backend tool direction

**Expose a single, unified `opt.*` optimization-study tool layer** rather than a
scattered set of topology / shape / sizing / multi-objective tools. This layer
**wraps the existing design-study REST endpoints** (which are currently not
MCP-accessible) and adds the new sampling / definition steps. Wherever a
design-study endpoint already does the work, the MCP tool is a thin wrapper —
**extend, do not re-implement.**

Proposed tools (verbs map to existing endpoints where noted):

```text
opt.create_study              # new: write analysis/optimization_study.json (+ link design_study_problem)
opt.define_variables          # new: resolve editable params → optimization_variables.json
                              #      (built on cad.list_editable_parameters / build_parameter_index)
opt.define_objectives         # new: write optimization_objectives.json
opt.define_constraints        # new: write optimization_constraints.json
opt.propose_candidates        # new: grid/random/LHS sampler → patches/design_candidates/<cid>.json
opt.create_candidate_workspace# wrap: design-study candidate run (geometry regen in candidates/<cid>/)
opt.evaluate_candidate        # wrap: design-study evaluate / cae-evaluate
opt.rank_candidates           # wrap: design-study rank
opt.explain_recommendation    # new: read ranking + decision log → human-readable advisory + reason codes
opt.accept_candidate          # wrap: design-study accept  [APPROVAL REQUIRED]
opt.write_report              # new: diagnostics/optimization_report.json
```

Tool-layer rules:

- All `opt.*` tools already require the **`cae` guide topic** (existing prefix
  rule in `mcp_server.py`) — no change needed.
- `opt.accept_candidate` is **`requires_approval=True`** and routes through the
  existing approval broker. It MUST NOT overwrite baseline geometry — it only
  records acceptance into `accepted/<cid>/` + `analysis/...acceptance.json`.
- Read-only definition/inspection tools (`opt.create_study`,
  `opt.define_*`, `opt.explain_recommendation`, `opt.write_report`) are
  non-approval, but candidate *generation* and *evaluation* that mutate the
  package follow the same modeling-plan-boundary discipline as CAD authoring.
- No `opt.*` tool runs an unbounded loop inside one call. The **agent** drives
  the loop (propose → evaluate → rank → propose); each tool call is a bounded,
  deterministic step. This keeps the agent as the orchestrator and the decisions
  auditable.

> Recommendation: implement `opt.*` wrappers in `runtime_tool_registry.py` calling
> the design-study converter functions directly (in-process), rather than going
> through HTTP, so the MCP and REST surfaces share one code path.

---

## 6. Wording discipline (honesty)

Use: *"agent-guided"*, *"advisory ranking"*, *"candidate exploration"*,
*"CAE-backed evidence"*, *"approval-gated acceptance"*, *"reason-coded decision
log"*.

Avoid: *"fully autonomous optimal design"*, *"production-certified optimization"*,
*"guaranteed global optimum"*, *"automatic engineering approval"*.

Carry forward the existing honesty flags (`production_ready:false`,
`contact_physics_modeled:false`, `bolt_preload_modeled:false`, plane-stress /
experimental-3D caveats) unchanged into any artifact that reuses topology or
assembly evidence.

---

## 7. Risks & dependencies

- **Design-study MCP exposure is a prerequisite** for the unified `opt.*` layer.
  Until the wrappers exist, the loop is REST-only.
- **CAE evaluation needs a working solver path.** `cae.run_solver` requires
  CalculiX (`ccx`) on PATH; in its absence the loop can still do mass/volume and
  static-metric evaluation, but stress/displacement constraints become `unknown`.
  This must be surfaced, not hidden.
- **Candidate recompilation is injected** (the design-study executor needs a
  recompiler callable). The MCP wrapper must wire the existing
  `make_candidate_recompiler()` so geometry actually regenerates.
- **Sampling cost.** Grid search explodes combinatorially; the sampler must cap
  candidate count and `log` what was dropped (no silent truncation).
- **No JSON schema for design-study problem today** — validation is in-code. New
  artifacts SHOULD add real JSON schemas under `aieng/src/aieng/schemas/` for durability.

---

## 8. Recommended first implementation step

Implement **Issue 1 (problem artifact schema) + Issue 2 (candidate parameter
generation)** together as the smallest useful slice, then **Issue 8 (bracket
demo)** to prove the open loop end-to-end:

> A plate-with-hole / simple-bracket study with 2 variables (e.g. wall thickness
> + fillet or hole diameter), objective = minimize mass, constraints = max stress
> + max displacement, producing ≥5 candidate evaluations and one advisory
> recommendation — entirely through derived candidate workspaces, baseline
> untouched, acceptance approval-gated.

This reuses the design-study executor/evaluator/ranker that already exists; the
only genuinely new code is the sampler and the problem-definition front-end.

See `agent_guided_optimization_issues.md` for the issue breakdown.
