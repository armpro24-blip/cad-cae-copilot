# AIENG Copilot Direction — Curated Execution Plan

> **Historical — kept for context, do not build from this.** A 2026-05-19 execution plan written around the old in-app UI and a FreeCAD backend.
> Where this disagrees with [`AGENTS.md`](../AGENTS.md), AGENTS.md is right.
> See [`README.md`](README.md) for what is current.

Date: 2026-05-19

This is the concise working plan after reviewing the new strategy notes,
roadmaps, and Phase 36–39b implementation work. It is intended to be the
current "do next" guide; the longer strategic analysis remains background
material.

## 1. Product Positioning

AIENG should not be positioned as a small safety plugin or as a new CAD/CAE
platform. The stronger positioning is:

> **AIENG is an evidence-grounded CAD/CAE Copilot workbench.**
>
> It helps engineers propose CAD changes, run approval-gated CAE loops, compare
> results against design targets, and keep every step reviewable through the
> `.aieng` evidence layer.

The evidence/review layer is still the differentiator, but it should be
presented as the trust mechanism inside an active Copilot loop, not as the
whole product.

## 2. What Is Already Implemented

The current codebase is already past the earlier "review report only" stage.

Implemented pieces:

- **CAE review assistant** in `aieng-ui`
  - Read-only evidence-backed report endpoint and panel.
  - Clear missing/stale/unsupported/claim-boundary reporting.
- **Phase 36 recommendation primitive** in `aieng`
  - Generates ranked CAD modification proposals from design targets, computed
    metrics, per-feature stress, and parsed features.
- **Phase 37 pre-execution verification gate** in `aieng`
  - Blocks/warns on schema, manufacturability, preserved-feature, and
    predicted regression issues.
- **Phase 38 agent/MCP bridge**
  - Read-only MCP wrappers for recommendation and verification.
  - `aieng-closed-loop-copilot` skill documents the bounded loop.
- **Phase 39 UI explainability panel**
  - Shows proposals, rationale, expected impact, and verification verdicts.
- **Phase 39b apply wiring**
  - Proposal cards can call approval-gated `cad.edit_parameter`.
  - `fail` proposals are blocked in UI; `warn` proposals need explicit
    acknowledgement.

Validation run during this review:

- `aieng`: `tests/test_cae_recommendation.py` +
  `tests/test_cae_verification.py` → **32 passed**.
- `aieng_freecad_mcp`: `tests/test_recommendation_bridge.py` →
  **14 passed**.
- `aieng-ui/backend`: `test_api.py -k "cad_recommendations or edit_parameter"` →
  **10 passed**.
- `aieng-ui/frontend`: `npm run build` → **passed**.

## 3. Main Gap Now

The project now has many individual parts of the loop, but the user experience
still feels like separate panels and buttons:

```text
review evidence → view recommendations → apply parameter edit →
manually go to CAE lifecycle → mesh/preflight/run/extract/refresh →
manually compare targets/report
```

The next milestone should not add another isolated capability. It should make
the existing pieces feel like one Copilot workflow.

## 4. Next Milestone: Closed-Loop Copilot Stepper

### Goal

Create a guided, approval-gated single-iteration loop in the web workbench:

```text
Inspect evidence
→ Recommend CAD change
→ Verify proposal
→ Approve/apply parameter edit
→ Mark downstream evidence stale
→ Prepare/mesh/solver run
→ Extract results + refresh summaries
→ Compare design targets
→ Generate loop report / next recommendation
```

### V1 constraints

- Existing `.aieng` project only.
- Existing editable parameters only.
- One proposal per iteration.
- Linear static CalculiX path only.
- Human approval remains required for CAD mutation and solver execution.
- No automatic engineering claim advancement.

## 5. Prioritized Work Items

### P0 — Unify the loop in UI

Add a Copilot/Loop panel or mode in `aieng-ui` that shows a vertical stepper:

1. Baseline evidence readiness.
2. Top proposal + verification verdict.
3. Apply proposal run status and approval buttons.
4. CAE readiness / mesh / solver run status.
5. Post-run metrics and design-target comparison.
6. Final loop summary and next suggested action.

This can initially orchestrate existing endpoints/tools rather than introducing
new backend abstractions.

### P1 — Loop report

Add a deterministic `loop_report` object that compares before/after metrics:

```text
metric | before | after | delta | target | status
```

Required honesty fields:

- run IDs / package artifact references,
- stale evidence notes,
- unsupported or missing evidence,
- explicit `claims_advanced: false`.

### P1 — Stale evidence propagation after CAD edits

After `cad.edit_parameter`, downstream CAE artifacts must be visibly stale until
re-simulation. This is the clearest demonstration of the `.aieng` evidence
layer's value.

Minimum behavior:

- old solver runs/results are retained,
- UI shows "needs re-simulation",
- target comparisons based on old results are marked stale/unknown,
- claims remain unchanged.

### P2 — Approval tiering

The strategic analysis correctly notes that binary `requires_approval` is too
blunt. Introduce tiers only after the loop UX is understandable:

- `auto`: read-only/derived summaries,
- `notify`: low-risk evidence bookkeeping,
- `confirm`: parameter edits / mesh settings,
- `gate`: solver execution / claim updates / high-risk mutations.

### P2 — Geometry-kernel verification

The current Phase 37 gate is heuristic and explicitly does not check geometry
validity. A future FreeCAD-backed verifier should test whether a proposed
parameter edit regenerates valid geometry and whether expected feature mappings
survive.

## 6. Defer Explicitly

Do not prioritize these until the closed-loop workbench demo is coherent:

- arbitrary text-to-CAD,
- topology-changing CAD edits,
- OpenFOAM/CFD workflows,
- SolidWorks/Onshape/Abaqus adapters,
- cloud/SaaS hosting,
- automatic claim advancement,
- full optimization/search loops.

## 7. Recommended External Narrative

For engineers:

> "AIENG is an open-source engineering Copilot workbench. Tell it a design
> target, review its proposed CAD change, approve the edit and simulation, then
> inspect an evidence-backed before/after report."

For developers/agent builders:

> "`.aieng` is the structured evidence layer for CAD/CAE agents. AIENG provides
> the recommendation, verification, approval, execution, and audit contracts
> needed to build trustworthy engineering Copilot loops."

For enterprise users:

> "AIENG does not replace your CAD/CAE tools. It makes AI-driven engineering
> iterations auditable, approval-gated, and reproducible around your existing
> tools."

## 8. Current Decision

The direction is now clear:

> **Build the closed-loop Copilot demo around parameter improvement, not a
> standalone safety plugin and not a full CAD/CAE platform.**

The immediate product question is no longer "what is AIENG for?" It is:

> **How fast can a new user open one project, apply one verified CAD proposal,
> re-run CAE, and understand the before/after evidence?**

