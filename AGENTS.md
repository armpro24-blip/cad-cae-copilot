# aieng Workbench — Agent Guide

Canonical detailed guide for any AI agent (Claude Code, GitHub Copilot, OpenAI
Codex, Cursor, Cline, …) working in this workspace. This is the single source of
truth for detailed guidance — `CLAUDE.md` and `.github/copilot-instructions.md`
point here. The MCP tool `aieng.agent_readme` returns a compact operational
quickstart by default; `aieng.guide {topic}` extracts detailed sections from this
file, and `aieng.agent_readme {detail: "full"}` returns the complete document.

---

## STOP — read this first

**Do NOT browse `aieng/src/` to understand what this system can do.**
That is a legacy library with a `FakeBackend` stub that produces no real geometry.

**The workbench is driven primarily through the `aieng-workbench` MCP server tools.**
If you see `aieng.*` / `cad.*` / `cae.*` tools in your tool list, use them — they
provide live UI events, topology feedback, and incremental modeling.

**If you do NOT have these MCP tools** (e.g. Kimi Code CLI without MCP
configuration), you are in **fallback mode**. You can still produce geometry by:
1. Writing build123d scripts and running them through the provided runner script.
2. Importing the resulting STEP file into the workbench with the provided importer
   script so it appears in the UI.
See the **Fallback mode** section below for the exact commands.

If the `aieng-workbench` MCP server is not in your tool list, it is configured in
this repo (`.mcp.json` for Claude Code, `.vscode/mcp.json` for VS Code/Copilot,
see MCP_SETUP for Codex, and see **Kimi Code CLI** notes in the Fallback section).

---

## First three calls every session

```
1. aieng.agent_readme                   → compact operational quickstart
2. aieng.list_projects                  → discover available project IDs
3. aieng.agent_context { project_id }   → geometry state, pointers, next steps
```

Call these **before** reading files or running code. `aieng.agent_context` gives
you the current geometry state, stale-artifact warnings, and the pointer IDs you
need to construct valid tool calls. Call `aieng.guide {topic}` only when the
current task needs detailed guidance; use `aieng.agent_readme {detail: "full"}`
only when the complete canonical guide is genuinely required.

The MCP server enforces task-guide reads per server session: CAD tools require
the `cad` topic, CAE/post-processing/topology-optimization tools require `cae`,
and package-lifecycle tools require `package`. A skipped read returns
`code: "guide_required"` without executing or requesting approval. Reading
`aieng.guide {topic: "full"}` or `aieng.agent_readme {detail: "full"}` unlocks
all categories. Operators may explicitly disable this guard with
`AIENG_MCP_REQUIRE_GUIDES=0`.

---

## Workflow priority matrix

Use this to pick the correct path when multiple options exist.

| Situation | Preferred path | Skill / contract | Fallback |
|-----------|---------------|------------------|----------|
| Create or extend CAD geometry (new model, additive features, substantial edits) | MCP-first `cad.execute_build123d` | `aieng-cad-authoring` | Write build123d script locally, import STEP |
| Pure dimensional tweak on existing editable model | `cad.edit_parameter` | `aieng-cad-authoring` | — |
| Engineering audit / read-only critique | `cad.critique` | `aieng-cad-authoring` | — |
| CAE readiness → solver → results | MCP-first `cae.*` pipeline | `aieng-cad-cae-copilot` | — |
| Schema/tool implementation or legacy IR plan editing | `aieng/` core library CLI | (no skill) | — |
| No MCP tools available (Kimi CLI, plain terminal) | Local script runner + STEP importer | — | See **Fallback mode** below |

Rules:
1. **MCP workbench first.** If `aieng.*` / `cad.*` / `cae.*` tools are in your tool list, use them. They provide live UI events, topology feedback, and incremental modeling.
2. **Fallback only when MCP is unavailable.** Do not bypass the MCP tools just because a local script is faster to write.
3. **Pick the right skill.** `aieng-cad-authoring` is for CAD mutation and read-only inspection. `aieng-cad-cae-copilot` is for evidence-first CAE workflows (setup, solver, results). Do not mix the two.
4. **Never claim a solver ran unless `cae.run_solver` completed and result artifacts exist.** See the `aieng-cad-cae-copilot` hard rules.

---

## Workspace layout

| Path | Status | Purpose |
|------|--------|---------|
| `aieng-ui/backend/` | **Active** | FastAPI backend + MCP server + all tools |
| `aieng-ui/frontend/` | **Active** | React workbench UI |
| `aieng/` | Core library | Semantic package format library — `.aieng` package engine, Shape IR, schemas, validation, CLI, artifact/evidence model |
| `aieng-agent-skills/` | Active | Agent skill definitions |
| `legacy/aieng-freecad-mcp/` | Legacy | Old FreeCAD adapter — not the default runtime |
| `archive/CAD-Agent-main/` | Archived | Historical/experimental auxiliary CAD-agent material |

### Development path rules

- **Default do not develop in `archive/` or `legacy/`**. These areas are
  preserved for reference and compatibility only.
- **CAD/CAE execution work** starts from `aieng-ui/backend`. That is where
  build123d/OCP runs, MCP tools are registered, and the active runtime lives.
- **Shape IR, schema, validation, `.aieng` package/evidence model** work starts
  from `aieng/`. This is the core semantic library; it is **not** legacy.
- **If you genuinely need to migrate logic** from `archive/` or `legacy/` into
  an active path, explicitly state: (1) what you are migrating, (2) why it is
  needed in an active path, and (3) the target active path.

---

## Frontend maintainability rules

When editing `aieng-ui/frontend/`, think about maintainability before adding or
changing code:

- Keep `src/App.tsx` as a lightweight composition layer. Do not put workflow
  orchestration, data fetching, domain actions, or large JSX trees directly in
  `App.tsx`.
- Prefer focused hooks and modules by responsibility: runtime settings, agent
  runs, geometry pointers, CAD/CAE actions, live activity streams, and pure
  formatting/helpers should live in separate files.
- Do not replace one giant file with another. If a module grows beyond a single
  clear responsibility, split it before adding more behavior.
- Preserve existing UI behavior and styling during refactors. Move code first,
  verify, then simplify.
- Remove dead components, helpers, constants, and types once they are no longer
  reachable from the active UI or API surface. Do not keep obsolete panels around
  "just in case" unless there is a concrete owner and integration path.
- For new UI work, prefer reusable components and explicit prop contracts over
  hidden cross-file coupling. Use TypeScript build results and reference searches
  to prove that cleanup is safe.

### Agent run display state

> **Post-cutover note (#17, #8).** The in-UI chat and its run→transcript
> rendering were removed in the MCP-first cutover, so the former display-state
> contract here (terminal-run transcript tones, `planToTranscriptItem` /
> `normalizeTerminalPlanSteps`, the per-row transcript items) no longer applies —
> those projection helpers and `chatTranscript.ts`'s `Transcript*` types were
> deleted. What survives is `isTerminalAutopilotStatus` (in
> [`chatTranscript.ts`](aieng-ui/frontend/src/app/chatTranscript.ts)), used by the
> activity-stream fallback to tell terminal (`completed` / `failed` / `cancelled`)
> runs apart from active ones. If an in-UI run transcript ever returns, restore a
> display-state contract along with it.

### Composer slash commands and @-mentions

The chat composer recognizes leading slash commands (`/build`, `/modify`,
`/critique`, `/explain`, `/simulate`) and surfaces a suggestion menu.
`parseComposerIntent` / `toComposerIntentMetadata` (in
[`composerIntent.ts`](aieng-ui/frontend/src/components/chat/composerIntent.ts))
attach a `composer_intent` blob to the persisted chat message `extra` and to the
autopilot run create request (echoed back on `AutopilotRunState.composer_intent`).
The raw `/command` text is always preserved in the stored user message.

**Backend routing status:**
- **`/build` (routed, mutation-required).** When
  `AutopilotRunState.composer_intent.command == "build"`, the engine injects a
  create-geometry instruction into the run context (biasing the agent toward CAD
  mutation tools such as `cad.execute_build123d`) and **forces the geometry-
  mutation guard ON** (`intent_type == "create_geometry"`) — a bare `final` is
  rejected until a CAD mutation tool has succeeded. This holds **even when the
  free text contains no create trigger word**. Asking the user for clarification
  (`ask_user`) or reporting a clear blocker is still allowed; a false success is
  not.
- **`/modify` (routed, mutation-required).** Same as `/build` but with a modify-
  geometry instruction (`intent_type == "modify_geometry"`). A read-only result
  such as `cad.critique` does **not** satisfy the requirement. If no CAD model
  exists yet, `ask_user` or a clear blocking `final` is acceptable — no false
  success.
- **`/critique` (routed, read-only).** When
  `AutopilotRunState.composer_intent.command == "critique"`, the engine injects a
  read-only critique/inspection instruction into the run context (biasing the
  agent toward `cad.critique` and read-only inspection tools such as
  `aieng.inspect_package` / `aieng.validate` / `cad.get_source`) and **suppresses
  the geometry-mutation guard** so a `final` is allowed after a read-only result
  (or a clear "no CAD available" answer) even if the free text contains words
  like "add". It does **not** force `cad.critique`, change CAD execution, or
  bypass approval.
- **`/explain` (routed, read-only).** When
  `AutopilotRunState.composer_intent.command == "explain"`, the engine injects a
  read-only explanation instruction into the run context (`intent_type ==
  "explain_project"`; biasing the agent toward read-only context/source/topology
  tools such as `aieng.agent_context` / `aieng.inspect_package` / `cad.get_source`
  / `aieng.agent_readme`) and **suppresses the geometry-mutation guard** so a
  `final` is allowed after a read-only inspection — or a clear "nothing available
  to explain" answer — even if the free text contains words like "add"/"change".
  If no CAD/project/artifact exists, `ask_user` or a clear blocking `final` is
  acceptable. It does **not** force any specific tool, change CAD execution, or
  bypass approval.
- **`/simulate` (routed, approval-gated solver workflow — v3).** When
  `AutopilotRunState.composer_intent.command == "simulate"`, the engine injects a
  simulation workflow instruction (`intent_type == "plan_simulation"`) biasing the
  agent toward CAE setup/preflight + read-only inspection tools
  (`aieng.agent_context` / `aieng.inspect_package` / `cae.prepare_solver_run` /
  `aieng.write_completeness_report`) and **suppresses the geometry-mutation
  guard** (a simulation step is not a CAD edit, and free text like "add a 500N
  load" must not trip the create/modify heuristic).
  - **Deterministic readiness report.** Alongside the prompt bias the engine
    injects a structured, deterministic readiness report from
    [`simulation_readiness.py`](aieng-ui/backend/app/agent_autopilot/simulation_readiness.py)
    (`build_simulation_readiness_report`). It classifies the six core inputs —
    **analysis_type, material, loads, constraints, mesh, solver** — as
    `present` / `missing` / `defaultable` / `unknown`. `material`, `loads`, and
    `constraints` are **required** (a `missing` one populates
    `missing_required_inputs`); `analysis_type`, `mesh`, and `solver` are
    **present** when explicitly configured, **defaultable** when absent, and
    **unknown** when explicitly unavailable (`false` / `{"available": false}`).
    `@part` / `@artifact` mention bindings are reused as `targets`. The summary
    tells the agent to **ask the user** for any missing required inputs and to
    **never claim the solver ran** (`solver_executed` is always `false`).
  - **v2: direct setup-artifact reading.** Readiness source priority is **direct
    setup artifact > `agent_context["cae"]` block > `not_found`/defaults**.
    `load_simulation_setup` reads `simulation/setup.{yaml,yml,json}` and
    `cae/setup.{yaml,yml,json}` from the package (and inline workspace artifacts
    of kind `cae_setup` / `simulation_setup`), normalizes them, and the report
    records both `setup_source` and `setup_source_kind`
    (`setup_artifact` / `workspace_artifact` / `agent_context` / `none`). The
    file loader is app-wired (`simulation_setup_loader`) and best-effort —
    malformed/missing setup safely falls back to the cae block, then `not_found`.
  - **v3: approval-gated prepare → run workflow.**
    [`simulation_workflow.py`](aieng-ui/backend/app/agent_autopilot/simulation_workflow.py)
    (`build_simulation_workflow_state`) derives, from the readiness report plus the
    observed `cae.prepare_solver_run` / `cae.run_solver` results, the phase fields
    `ready_to_prepare_solver_run`, `solver_deck_prepared`, `deck_path` /
    `manifest_path`, `ready_to_run_solver`, `solver_run_approval_required`,
    `solver_executed`, `solver_status`, and `result_artifacts`. The engine enforces
    it deterministically: **prepare/run are blocked while required inputs are
    missing or a referenced target is `known=false`** (ask the user instead);
    **`cae.run_solver` is blocked until a successful `cae.prepare_solver_run`**; and
    an allowed `cae.run_solver` still goes through the **normal approval gate** (the
    workflow never bypasses approval). A `final` that **claims solver results is
    rejected unless `solver_executed` is true** — `solver_executed` only flips true
    after an approved, non-error `cae.run_solver`, so denied/failed runs never read
    as success.
  - v3 does **not** auto-run the solver (`cae.run_solver` stays approval-gated),
    does **not** modify CAD, and does **not** bypass approval. `/simulate` is
    **not** read-only (it may patch CAE setup), so it carries
    `simulation_planning: true` rather than `read_only: true`.

Command-specific routing **never bypasses approval** — `cad.execute_build123d`,
`cae.run_solver`, and the other gated tools still pause for approval as usual.
Helpers live in [`engine.py`](aieng-ui/backend/app/agent_autopilot/engine.py):
`get_composer_command` / `is_critique_command` / `is_read_only_command` /
`is_simulation_command` / `suppresses_mutation_guard` /
`is_mutation_required_command` / `command_intent_label` / `command_mutation_intent`.

**Natural-language intent resolution ("point and shoot").** When a run carries
**no** explicit slash command, the engine resolves the plain-text message into one
of the same routed commands so all of the routing + guards above apply unchanged —
the user does not have to learn the slash vocabulary.
[`intent_resolution.py`](aieng-ui/backend/app/agent_autopilot/intent_resolution.py)
owns this:
- **`INTENT_REGISTRY`** is now the **single source of truth** for the five routed
  commands (intent_type, mutation-required / read-only / simulation, trigger
  terms). `engine.py` derives `_MUTATION_REQUIRED_COMMANDS` / `_READ_ONLY_COMMANDS`
  / `_SIMULATION_COMMANDS` / `_COMMAND_INTENT_LABELS` and the geometry-guard term
  lists from it — adding a new intent is a one-line registry edit.
- **`resolve_intent`** is three-tier: an **explicit `/command` always wins**
  (confidence 1.0); else an optional, app-wired **LLM classifier**
  (`build_llm_intent_classifier`, degrades silently to keyword when no
  provider/API key, and is **skipped for fake/replay runs** so tests stay
  deterministic); else a deterministic **keyword heuristic** (`keyword_classify`,
  mutation intent takes precedence over read-only/simulation).
- **`_resolve_natural_language_intent`** (engine) runs **before**
  `_inject_command_context`. A confident command is written back onto
  `composer_intent.command` (+ `intent_source`) so existing routing takes over; a
  plain message with no actionable intent is **left untouched** (backward
  compatible). When the inferred command is actionable but **low-confidence or
  ambiguous**, it injects `INTENT_CLARIFY_INSTRUCTION` biasing the agent toward
  `ask_user` **instead of routing on a guess** — and does **not** set a command.
  The resolution is recorded on `composer_intent.resolved_intent` for
  transparency. The resolver **never relaxes a guard**; it only proposes a command.

**Parametric-edit slot bias + deterministic binding.** For a **modify** intent
(explicit `/modify` or a resolved natural-language modify) whose message names
concrete dimensional changes ("change the wall thickness to 5mm", "把壁厚改成5"),
`extract_parameter_slots` (pure, bilingual, best-effort) pulls `{name, value,
unit}` slots and `_inject_parametric_edit_context` injects
`PARAMETRIC_EDIT_INSTRUCTION` — biasing the agent to the fast `cad.edit_parameter`
path instead of regenerating the whole model.
- **Slot → feature-parameter binding** ([`parameter_binding.py`](aieng-ui/backend/app/agent_autopilot/parameter_binding.py)).
  Each slot is bound to a concrete editable feature parameter via
  `build_parameter_index` (flattens `feature_graph.features[].parameters`,
  tokenizing the `cad_parameter_name` constant — e.g. `WALL_THICKNESS` →
  `{wall, thickness}` — plus the inferred parameter name and feature name) and
  `bind_parameter_slots` (best-token-overlap match). On a unique match the agent
  is handed the exact `featureId` / `parameterName` / constant / current value /
  range (with an **out-of-range** flag); the feature graph is read through the
  app-wired `feature_parameter_loader`. Mirrors `@part`/`@artifact` **honesty**:
  no index → `known=None` (unverified, never a false negative); index present but
  no overlap → `known=False` (not found); two+ equally-good matches →
  `known=False` (**ambiguous**, candidates listed → ask the user); never invents a
  target. Slots + bindings ride on the observation (`parameter_slots` /
  `parameter_bindings`) for the frontend/audit.
- Prompt/context only: no tool is selected, CAD execution is unchanged, the
  mutation guard is unchanged, and `cad.edit_parameter` stays inside the approved modeling-plan boundary — the
  binding only *proposes* a target, and out-of-range / ambiguous edits are routed
  to user confirmation.

**Frontend echo (intent chip).** The resolved intent still rides on
`composer_intent.resolved_intent` (+ the `parameter_bindings` observation) on the
run state. The former in-UI rendering of it — `resolvedIntentFromRun`,
`runToTranscriptItems`'s `intent`-kind item, and the `IntentChip.tsx` chip — was
removed in the MCP-first cutover (#17, #8) along with the chat transcript; the
backend signal is unchanged and available to any agent/UI that consumes the run
state. Explicit slash-command runs record no `resolved_intent`.
**Editable Parameter Explorer (discovery surface).** The "point" half of
point-and-shoot: the editable-parameter index (`build_parameter_index` +
`summarize_parameter_index` in
[`parameter_binding.py`](aieng-ui/backend/app/agent_autopilot/parameter_binding.py),
each entry now carrying a `scope` of `local` / `global` / `unscoped`) is exposed
read-only via the `cad.list_editable_parameters` tool so the user and agent can
see **what can be edited fast** before editing — and pick a precise
`cad.edit_parameter` target. Same single source the `/modify` slot binding uses;
`global` parameters are flagged as shared (edits ripple), `local` as the safe
single-part edit.
- **Frontend panel.** The workbench renders an **Editable Parameters** panel
  ([`EditableParametersPanel.tsx`](aieng-ui/frontend/src/components/EditableParametersPanel.tsx),
  fed by `useEditableParameters` → `GET /api/projects/{id}/editable-parameters`,
  shaped by the pure
  [`editableParameters.ts`](aieng-ui/frontend/src/app/editableParameters.ts)). It
  groups parameters by scope (local / global-shared / unscoped, color-coded),
  shows each parameter's current value + allowed range + editable constant, and a
  click drafts a `/modify set <name> to ` into the composer — so editing still
  flows through the existing modeling-plan-confirmed path (the panel itself never mutates).
- **Sibling read-only panels (same draft-into-composer pattern).** Workbench
  panels surface backend audits the agent already produces:
  - **Critique panel** ([`CritiquePanel.tsx`](aieng-ui/frontend/src/components/CritiquePanel.tsx),
    `useProjectCritique` → `GET /api/projects/{id}/critique`, shaped by
    [`critiqueFindings.ts`](aieng-ui/frontend/src/app/critiqueFindings.ts)) — the
    deterministic `cad.critique` findings grouped by severity; each finding's
    **Fix** button drafts `/modify <suggested_fix>` into the composer.
  - **Simulation Readiness panel** ([`SimulationReadinessPanel.tsx`](aieng-ui/frontend/src/components/SimulationReadinessPanel.tsx),
    `useSimulationReadiness` → `GET /api/projects/{id}/simulation-readiness`,
    shaped by [`simulationReadiness.ts`](aieng-ui/frontend/src/app/simulationReadiness.ts)) —
    the six core CAE inputs as present / missing / defaultable / unknown; a missing
    **required** input (material / loads / constraints) gets an **Add** that drafts
    a `/simulate …`. Hidden for pure-CAD projects (`setup_source == not_found`).
  - **Sizing Sweep panel** ([`SizingSweepPanel.tsx`](aieng-ui/frontend/src/components/SizingSweepPanel.tsx),
    `useSizingSweepReport` → `GET /api/projects/{id}/sizing-sweep-report`,
    shaped by [`sizingSweepReport.ts`](aieng-ui/frontend/src/app/sizingSweepReport.ts)) —
    renders the latest `analysis/sizing_sweep_report.json` (written by
    `opt.sizing_sweep`) as a ranked variant table; the **Apply winner** button
    drafts `/modify set <param> to <winner>` into the composer.
  - **Mesh Convergence panel** ([`MeshConvergencePanel.tsx`](aieng-ui/frontend/src/components/MeshConvergencePanel.tsx),
    `useMeshConvergenceReport` → `GET /api/projects/{id}/mesh-convergence-report`,
    shaped by [`meshConvergenceReport.ts`](aieng-ui/frontend/src/app/meshConvergenceReport.ts)) —
    renders the latest `analysis/mesh_convergence_report.json` (written by
    `cae.mesh_convergence`) with per-metric GCI / apparent-order / verdict; a
    **Finer mesh** button drafts `/simulate mesh_size_mm=<half finest>` when the
    result is not converged.
  All are read-only (run no solver, mutate nothing); actions flow through the
  existing plan-confirmed `/modify` path or approval-gated `/simulate` solver path.
- **Assembly-check viewer overlay (in-3D affordance).** The model viewer has a
  "Show assembly check" toggle ([`ModelViewer.tsx`](aieng-ui/frontend/src/components/ModelViewer.tsx),
  fed by `useGeometryReport` → `GET /api/projects/{id}/geometry-report`, shaped by
  [`geometryReport.ts`](aieng-ui/frontend/src/app/geometryReport.ts), drawn by
  [`assemblyCheck.ts`](aieng-ui/frontend/src/components/viewer/assemblyCheck.ts)).
  It draws a **red** wireframe box around each floating part and an **amber** box
  around each part in a broken / missing left-right symmetry pair — the same
  `geometry_report` structural signals `cad.design_review` folds in, made visible
  in 3D. Read-only; the toggle only appears when there is at least one alert.
- **Selectable result fields (#251).** The viewer field picker exposes the full
  CAE post-processor catalog: stress tensor components (Sxx…Syz), principal
  stresses (S1/S2/S3), Tresca / max shear, displacement magnitude and per-axis
  components (Ux/Uy/Uz), and safety factor (yield ÷ von Mises). The backend
  `/api/projects/{id}/fields/{name}` and `/cae-result-fields` endpoints serve all
  of them from the FRD when available, with honest synthetic fallbacks and units
  when no solver result is present.
- **Field peak/min markers + click-to-query probe (#252).** When a real FRD field
  overlay is active, the viewer draws a red sphere/label at the field maximum and
  a blue sphere/label at the minimum (`fieldMarkers.ts` +
  `useFieldMarkerOverlay.ts`). Clicking anywhere on the model opens a probe tooltip
  with the exact value, unit, nearest-node coordinates, and face pointer (if the
  hit primitive maps to a B-Rep face). The probe is implemented in
  `useFieldProbe.ts` and rendered by `ViewerOverlays.tsx`; it reuses the same
  nearest-node grid as the colormap so the readout matches what the user sees.
- **Field legend controls (in-legend colormap + range + bands + threshold) (#254).**
  The floating legend for the active solver result field
  ([`FieldLegend.tsx`](aieng-ui/frontend/src/components/FieldLegend.tsx)) is now
  interactive. Users can clamp the min/max range, pick from the built-in colormaps
  (`thermal`, `coolwarm`, `viridis`, `grayscale`), switch between continuous and
  discrete bands, and isolate regions above a threshold. The mapping is recomputed
  client-side in
  [`fieldColors.ts`](aieng-ui/frontend/src/components/viewer/fieldColors.ts)
  (`applyFieldColors`, `effectiveFieldRange`, `normalizeFieldValue`) and reapplied
  by `useFieldColorOverlay` without reloading the preview asset. Reset restores the
  solver-derived defaults. State lives in `useWorkbenchApp` and is reset when the
  project or selected field changes.
- **Load-case / analysis-step selector (#256).** When a CAE result summary reports
  multiple load cases, a `LoadCasePicker` appears next to the field picker in
  [`ViewerPane.tsx`](aieng-ui/frontend/src/components/ViewerPane.tsx). Switching
  the selector passes `load_case_id` to
  `GET /api/projects/{id}/fields/{field_name}`, which selects the matching FRD
  step on the backend. State lives in `useWorkbenchApp` and resets to the first
  available load case when the project changes.
- **Result animation / deformed-shape playback (#255).** The deformed-shape overlay
  (`DeformationControls` in
  [`DeformationControls.tsx`](aieng-ui/frontend/src/components/viewer/DeformationControls.tsx))
  adds a **Play / Pause** button and a choice between **Sweep** (scale 0→1→0) and
  **Oscillate** (scale ±1) modes. Animation updates deformed geometry positions via
  `applyDeformationScale` in a `requestAnimationFrame` loop rather than rebuilding
  the mesh every frame. This works for any displacement-sourced FRD field,
  including modal and buckling mode shapes.
- **CAE setup overlay (in-3D affordance, #247).** The model viewer has a "Show CAE setup"
  toggle ([`ModelViewer.tsx`](aieng-ui/frontend/src/components/ModelViewer.tsx),
  fed by `useCaeSetupOverlay` → `GET /api/projects/{id}/cae-setup-overlay`, drawn by
  [`caeSetupOverlay.ts`](aieng-ui/frontend/src/components/viewer/caeSetupOverlay.ts)).
  It renders **load arrows** (scaled/labeled with magnitude N), **constraint glyphs**
  (fixed-support cones), and tinted bound-face highlights for loaded (red) and
  constrained (blue) faces from `simulation/setup.yaml` + `cae_mapping.json`.
  Stale/unresolved face refs are flagged with an amber marker instead of being
  silently dropped. Read-only; the toggle only appears when a CAE setup exists.
- **Field-region cluster markers (in-3D affordance, #249).** The model viewer has a
  "Show field regions" toggle ([`ModelViewer.tsx`](aieng-ui/frontend/src/components/ModelViewer.tsx),
  fed by `useFieldRegions` reading `analysis/field_regions.json`, drawn by
  [`fieldRegionMarkers.ts`](aieng-ui/frontend/src/components/viewer/fieldRegionMarkers.ts)).
  It places a 3D marker at each high-stress / high-displacement cluster centroid,
  colored by field type (stress = red, displacement = blue) and sized by relative
  magnitude. Clicking a marker frames the camera on the cluster and surfaces its
  metric value plus any associated `@face:` pointer. Read-only; the toggle only
  appears when `cae.extract_field_regions` has produced clusters.
- **FE mesh preview overlay (in-3D affordance, #250).** The model viewer has a "Show
  mesh" toggle ([`ModelViewer.tsx`](aieng-ui/frontend/src/components/ModelViewer.tsx),
  fed by `useMeshPreview` → `GET /api/projects/{id}/mesh-preview`, drawn by
  [`meshPreview.ts`](aieng-ui/frontend/src/components/viewer/meshPreview.ts)).
  When a `simulation/mesh.inp` exists, toggling overlays the FE mesh as a
  semi-transparent cyan wireframe (surface edges extracted from the solid
  elements) on top of the smooth geometry and shows a small stats chip with the
  element count and target mesh size. A coarse-mesh warning flag is shown when
  the element count is very low. Read-only; the toggle only appears when a mesh
  is present and degrades cleanly when `mesh.inp` is absent.
**Follow-up / reply normalization.** Follow-up and reply messages are re-resolved
so their intent is recorded explicitly rather than left implicit
(`_normalize_followup_intent` in
[`engine.py`](aieng-ui/backend/app/agent_autopilot/engine.py), called from
`reply_to_run` and the queued `follow_up_run` path). **Deliberately lightweight**:
connected agents (Claude Code, Codex, Kimi, …) infer follow-up meaning on their
own, so this does NOT try to out-think them — it runs only the *deterministic
keyword* resolver (no LLM) and lands the normalized result in state. A dimensional
`modify` follow-up reuses the same parameter binding as the initial message
(`_emit_parametric_edit_context`), handing the agent a concrete
`cad.edit_parameter` target; other recognized commands record a visible
`followup_intent` note; an unrecognized follow-up is a no-op (the agent just
proceeds). The principle: **let the agent associate, but have the system make the
association explicit as a normalized intent.**
- **Future work:** inline value editing in the panel (a field that calls the
  plan-confirmed `cad.edit_parameter` directly), and consume the LLM classifier's
  `targets` / `parameters` beyond the deterministic slots.

**`@`-mentions (strict binding, v1).** The composer parses lightweight
`@kind:value` mentions (`extractComposerMentions` in
[`composerIntent.ts`](aieng-ui/frontend/src/components/chat/composerIntent.ts))
into `{ kind, raw, value }` and persists them on `composer_intent.mentions`.
- **`@part:<label>` and `@artifact:<id>` are routed and bound.** The engine
  resolves each mention against the available workspace context
  ([`mention_binding.py`](aieng-ui/backend/app/agent_autopilot/mention_binding.py)
  `build_mention_bindings`): `@part` against `cad.named_parts` + `topology_map`,
  `@artifact` against the known workspace artifacts. Each binding records
  `known` (`true` / `false` / `null`), `source`, `canonical_id`, and a `reason`.
  The honesty rule: when **no authoritative index is available**, the binding is
  `known: null` ("unverified") — never a false negative; `known: false` is used
  only when an index exists and the value is genuinely absent. The bindings ride
  on the mention-context observation (`mention_bindings`), the label annotates
  each value with its status (e.g. `bracket (known)`, `ghost (not found)`,
  `rotor_1 (unverified)`), and `/simulate` readiness reuses them for its
  `targets` (no duplicate lookup).
- Still **prompt/context only** — binding does not block commands or change tool
  selection/approval. Command-aware targeting guidance is unchanged: `/explain` →
  explain that part (read-only), `/critique` → critique it if available
  (read-only), `/modify` → target the CAD edit at it (approval + mutation guard
  unchanged), `/simulate` → scope the plan to it (no solver run). The agent is
  still told to ask / report "target not found" rather than invent a target, and
  mentions are never required for any command.
- Helpers: `mentioned_parts` / `mentioned_artifacts` / `mention_context_label`
  (engine), `build_mention_bindings` / `bindings_to_targets` /
  `mention_status_word` (mention_binding) — all robust to missing/malformed
  metadata.
- **Future work:** `@workspace` / `@project` / `@face` mention routing (the
  parser recognizes those kinds, but the backend does not bind them yet) and
  deeper `/simulate` readiness validation against the live CAE setup artifacts.

---

## What the workbench can actually do

### Real 3D CAD modeling (no API key needed)

`cad.execute_build123d` runs caller-supplied Python code against **build123d**
(the real OpenCASCADE geometry kernel) and produces actual STEP/STL/GLB files.
This is NOT a stub. Supported operations include:

- Primitives: `Box`, `Cylinder`, `Cone`, `Sphere`, `Torus`
- Operations: `extrude`, `revolve`, `loft`, `sweep`
- Modifications: `fillet`, `chamfer`, `shell`, `mirror`
- Boolean: add / `subtract` (`Mode.SUBTRACT`) / intersect
- Patterns: `PolarLocations`, `GridLocations`, `Locations`
- Holes, slots, countersinks

Enough to model most mechanical parts: housings, brackets, enclosures, manifolds,
and simplified consumer-product bodies.

**Code contract:**
- Bind the final model to a variable named **`result`**.
- Do **not** include export calls — the runner adds `export_step` / `export_stl` /
  `export_gltf` (build123d 0.10.0; exports are free functions).
  - If you absolutely must export manually (fallback mode), use `export_gltf(result, path, binary=True)`
    to produce a real binary GLB. Without `binary=True`, build123d writes a JSON
text file that the frontend cannot render.
- A `+` that yields a `ShapeList` is auto-wrapped in a `Compound`, so unions export fine.

**Assert design rules — `require(condition, message)`.** The runner injects a
`require()` helper (and treats a bare `assert` the same way) so you can embed
design constraints that **deterministically fail the build** instead of hoping
they hold. A failed `require()` returns a structured
`code: "design_rule_violation"` with your message (not a raw traceback); a
passing one is a no-op. Use it to encode intent that must stay true across edits
— "verified by construction":
```python
WALL_THICKNESS = 3.0
require(WALL_THICKNESS >= 3.0, "wall below 3mm CNC minimum")
require(len(result.children) == 4, "expected exactly 4 motor pods")
```
This composes with the deterministic `critique_diff` returned by every edit (a
guard against *introducing* violations) — `require()` is the guard you author
up front against ever building them in the first place.

**Name your parts.** Set `.label` on shapes and combine them with a `Compound` so
each part gets a semantic ID you can reference later (instead of anonymous
`body_001`). Labels appear as named parts in `topology_map.json` and as
`named_part` features in `feature_graph.json`.

**Color your parts.** Set `.color = Color(r, g, b)` (RGB in 0..1) on each part.
Colors flow through to **both** the agent thumbnail (so you can visually tell
parts apart) **and** the GLB the UI viewer displays. Parts without a `.color`
get a cycling palette in the thumbnail; in the UI they appear default-grey.
Use color to make part boundaries readable and to encode design intent
(e.g. red structural, blue moving, silver mechanical).
```python
from build123d import *
body = Box(40, 40, 10); body.label = "fuselage"
body.color = Color(0.78, 0.15, 0.15)   # red
fl = Cylinder(3, 30); fl.label = "motor_pod_FL"
fl.color = Color(0.20, 0.30, 0.65)     # blue
result = Compound(children=[body, fl])
```

**Incremental modeling (`mode: "append"`).** Instead of resubmitting the whole
script each step, append onto the previous result:
- `mode: "replace"` (default) — the script is the whole model.
- `mode: "append"` — the previously-stored script runs first; its model is exposed
  as **`previous_result`**. Your code adds to it and must still reassign **`result`**.
  Requires an existing model (run once with `replace` first). Labels from earlier
  steps are preserved.
```python
# step 2, mode=append — keeps the fuselage + motor_pod_FL from step 1
from build123d import *
arm = Cylinder(3, 30); arm.label = "motor_pod_FR"
result = Compound(children=[previous_result, arm])
```

**Visual feedback (multi-view contact sheet).** `cad.execute_build123d` returns a
single PNG with **four labelled views in a 2×2 grid: front, side, top, iso**.
If a reference image is attached to the project (see "Reference image
calibration" below) the layout becomes 2×3 with the reference filling the
rightmost column for side-by-side comparison. The image arrives as an MCP
image content block (disable with `{"thumbnail": false}`).
**Look at all four views** — each catches problems the others hide:
- **front** — wrong proportions (e.g. arms reaching to feet), left/right symmetry
- **side** — overhangs, depth, parts sticking out forward/back
- **top** — layout in the XY plane, parts hidden behind others in front view
- **iso** — overall 3D form

Don't judge from face counts or bounding boxes — actually look at the views.

**Iterate using fail-first review.** Before adding more parts, list 3–5 reasons
the current build does **not** look like the target object (specific to view +
specific part), then list what's right. Decide the next iteration from the
failures, not from a preset plan. This works much better than building straight
through to the finish.

**Reference image calibration.** When the user names a real product, character,
or vehicle, attach a reference image once with `cad.set_reference_image`
(pass `image_url` for HTTP fetch or `image_path` for a local file). The
reference is stored in the project's `.aieng` package and every subsequent
`cad.execute_build123d` thumbnail tiles it next to the 4 views, so you compare
proportions against the real reference instead of relying on memory.
Set the reference **before** starting iteration if you have one — that way
even the first build is calibrated. Without a reference you're guessing
proportions; with one, fail-first review can cite specific mismatches like
"forearm tapers wrong: reference shows widening toward the wrist, my build
narrows."

When the user names a real target but supplies **no** picture, call
`cad.search_reference_image { project_id, query }` (e.g. `query: "Boeing 747
side view"`). It searches Wikimedia Commons, attaches the best raster match via
the same path as `cad.set_reference_image`, and returns the matched `page_url`
so the source and its license can be verified. It degrades gracefully —
`status: "no_results"` just means proceed without a reference. (Agents with
their own image search can also find a URL and pass it to
`cad.set_reference_image` directly.)

**Response summary fields** (text-side feedback, useful when your client drops the image):
`named_parts` (all named parts now in the model), `parts_added` (what this step added),
`mode` (`replace`/`append`), `used_base` (whether an append consumed a prior model),
`geometry_report_summary` (always present in **both** `response_detail` modes — a one-line
`part_count / size / proportions / floating=N / symmetry_issues=N`; non-zero `floating` or
`symmetry_issues` is your cue to call `cad.design_review` and self-correct before reporting done),
and `modeling_fidelity` ({`level`: designed/basic/crude, `score` 0-100, `findings`}) — the
build's quality self-check; a `crude`/`basic` level means it reads as a primitive stack
(no edge-breaking, bare boxes) — don't report it done, improve it (e.g. via the `housing()` /
`rounded_box()` / `boss()` / `rib()` scaffolds) and re-build.

**Quantitative geometry report (`geometry_report`).** Every `cad.execute_build123d`
and `cad.edit_parameter` response carries a deterministic `geometry_report` —
judge proportions from these *numbers*, not only the blurry thumbnail (LLMs read
ratios far more reliably than low-res 3D renders):
- `overall_proportions` — normalized H:W:D of the whole model (largest dim = 1.0).
- `parts[].ratio_to_largest` — each named part's size relative to the biggest part.
- `symmetry[]` — for left/right name pairs (`arm_L`/`arm_R`, `motor_pod_FL`/`FR`):
  `ok:false` = the pair is NOT symmetric (fix the coordinates); `align_residual_mm`
  is how far off the mirror is; `status:missing_partner` = you named one side only.
- `gaps[]` — `status:floating` flags a detached part (usually a coordinate typo);
  `touching` = parts connect as intended; `floating_parts` lists all detached ones.
Cite specific numbers when iterating ("arm ratio_to_largest=0.5, too short → 0.7")
— this converts proportion judgment from eyeballing into a convergence metric.

**Parametric editing (`cad.edit_parameter`) — change one dimension fast, no LLM.**
When you want to resize an existing feature, do NOT regenerate the whole model.
`cad.edit_parameter` does a deterministic text replacement of a named constant in
`geometry/source.py` and re-executes build123d — sub-second to a few seconds,
fully reproducible. For this to work the source must declare dimensions as
**UPPER_SNAKE_CASE constants** (the system prompt enforces this on generated
code; do the same in hand-written code):
```python
MOTOR_POD_RADIUS = 3        # editable → feature_graph exposes radius_mm
fl = Cylinder(MOTOR_POD_RADIUS, 30); fl.label = "motor_pod_FL"
```
The feature graph then carries editable `parameters` with a `cad_parameter_name`
pointing back at the constant. Call it as:
```
cad.edit_parameter { project_id, featureId, parameterName, newValue }   [APPROVAL]
```
- `featureId` / `parameterName` come from the feature's `parameters` block in
  `feature_graph.json` (or `aieng.agent_context`).
- Validated against the parameter's declared `min_value`/`max_value` first.
- If the new value breaks the build, the package is left untouched and the error
  is returned — the prior geometry is preserved.
- Constants whose prefix is `GLOBAL_`/`DEFAULT_`/`WALL_`/`FILLET_`/`CHAMFER_` are
  also surfaced under a synthetic `Global Parameters` feature for shared dims.
  Any declared constant that matches no part name lands in a `Model Parameters`
  feature (or, if there's exactly one named part, on that part) — so every
  declared constant is editable.
Use `cad.execute_build123d` (mode=append/replace) only for changes that add or
remove geometry; use `cad.edit_parameter` for pure dimensional tweaks.

**Regression diff on every edit (`regression_diff`).** The `cad.edit_parameter`
response includes a `regression_diff` that compares the before/after topology by
named part — your safety net against an edit silently warping geometry it
shouldn't have. Read its `verdict` before trusting the result:
- `clean` — only the intended part(s) changed; `changed[]` lists each with
  `size_delta_mm` / `center_shift_mm`.
- `collateral_change` — **WARNING**: parts you did NOT target also moved
  (`collateral_parts` names them). Usually means the constant is shared across
  parts; reconsider the edit or split the constant.
- `identical` — nothing changed (wrong constant, or a no-op value).
- `topology_changed` — the part set changed (a part appeared/disappeared);
  unexpected for a pure dimensional edit.
(For edits to a `Global Parameters` constant, collateral is not judged — shared
dims are *meant* to move many parts.)

The `regression_diff` verdict (`clean` / `collateral_change` / `topology_changed`
/ `identical`, with changed and collateral parts named) is returned in the
`cad.edit_parameter` tool response for the driving agent to read. The former in-UI
"verification line" rendering of it (`editVerification.ts` →
`runToTranscriptItems` → `EditVerificationLine.tsx`) was removed in the MCP-first
cutover (#17, #8) along with the chat transcript; the verdict itself is unchanged
in the tool output.

**Part-level edits — `cad.replace_part` / `cad.remove_part` (the visible loop).**
`append` only ADDS geometry; when you need to fix or drop ONE part of a
character/product without resubmitting the whole script, use these:
- `cad.remove_part { project_id, label }` — drops the part with that `.label`.
- `cad.replace_part { project_id, label, code }` — swaps it for new build123d
  `code` (which must reassign `result` to the new part and set `result.label`,
  normally back to the same name). The high-level helpers are available in `code`.
Both append a transform step to `source.py` (so the stored script stays
self-consistent) and re-execute — no LLM — and return a `regression_diff` so you
can confirm only the targeted part changed. **Build incrementally with these +
`append` so each step shows in the viewer** — that is how the user watches the
model assemble, instead of one monolithic build appearing at the end.

**Organic vs mechanical (`model_kind`).** `cad.execute_build123d` accepts
`model_kind`: `"mechanical"` runs the bolt-pattern + base-plate feature
heuristics, `"organic"` skips them, `"auto"` (default) infers from part labels
and helper usage. Pass `"organic"` for characters/vehicles/products — otherwise
the heuristics mislabel limb cylinders as `mounting_hole_pattern` and the bottom
face as a `base_plate`, cluttering the feature graph. The chosen kind is echoed
back in `feature_graph.model_kind`.

**Advanced-feature awareness in the feature graph.** Beyond named parts, the
feature graph now tags the modelling operations it detects in the source:
`loft`, `revolve`, `sweep`, `fillet` (with average radius), and `mirror`. This
lets you (and downstream tools) see whether a body was built with industrial-
design curves or plain primitive stacking.

Example — a simplified coffee-machine body:
```python
from build123d import *
with BuildPart() as bp:
    Cylinder(radius=55, height=200)
    fillet(bp.edges().filter_by(Axis.Z, reverse=True), radius=8)
    with Locations((0, 0, 80)):
        Cylinder(radius=40, height=100, mode=Mode.SUBTRACT)
    with Locations((63, 0, 30)):
        Cylinder(radius=8, height=25, rotation=(0, 90, 0))
result = bp.part
```

### Industrial Design Mode — escape primitive stacking

For **complex visible exterior forms** (named characters, vehicles, consumer
products, electronics, anything where shape recognizability matters), `Box +
Cylinder` stacking caps the result at "high-quality pixel art." To produce
something that reads as designed rather than assembled, switch into
**industrial design mode**: build from a skeleton, generate solids by lofting
or sweeping between profiles, apply large fillets, and add details only after
the silhouette is correct.

**Activate when:** the user names a real product, character, or vehicle, or
says "make it look like a …" / "designed" / "smooth" / "rounded." Skip when
the user asks for mechanical brackets, fixtures, prototypes, or massing
studies — primitive stacking is fine there.

**Workflow:**

1. Plan landmarks (anchor Z heights, half-widths) as **named constants**.
   This lets later iterations adjust proportions in one place.
2. Build silhouette + skeleton first; verify the 4-view contact sheet reads
   correctly before adding detail.
3. Replace tapered or curved bodies with `loft` / `sweep` / `revolve` —
   **not** stacked boxes that imitate curves.
4. Apply `fillet` aggressively (radius 5–20mm) on visible edges. Apply LAST,
   after all booleans.
5. Mirror symmetric parts with `mirror(part, about=Plane.YZ)` — half the
   code, guaranteed symmetry.

**Hard rule:** if your iteration script is mostly `Box(...) + .moved(...)`
calls for a visible character/vehicle/product, stop and replace the major
exterior masses with one of the curve patterns below.

### High-level helpers — prefer these over hand-rolled boilerplate

`cad.execute_build123d` pre-injects these functions into your namespace (do NOT
import or redefine them). They wrap the error-prone BuildSketch/Plane/loft/sweep
boilerplate that LLMs routinely break, so you get smoother forms **and** fewer
failed builds. Each takes `label=` / `color=` and returns a `Part`:

| Helper | Use for | Signature |
|--------|---------|-----------|
| `lofted_stack(sections)` | torsos, cabs, fuselages, tapered bodies | sections = list of `(z, r)` circle / `(z, w, d)` rounded-rect / `(z, w, d, corner_r)` |
| `rounded_box(l, w, h, radius, edges=)` | designed enclosures (vs hard Box) | `edges="all"` or `"vertical"` |
| `capsule(radius, length, axis=)` | arms, legs, limbs, rounded pins | `axis` ∈ `"X"/"Y"/"Z"` |
| `tapered_cylinder(r_bot, r_top, h)` | necks, nozzles, tapered legs | — |
| `swept_tube(path_points, radius)` | pipes, handles, exhausts, cables | `path_points` = list of `(x,y,z)` |
| `revolved_profile(profile_points)` | bottles, vases, wheels, axisymmetric | `profile_points` = list of `(r, z)`, auto-closed to Z axis |
| `organic_blend(solids, radius)` | merge parts into ONE smooth body | fuses + fillets the joins; auto-degrades radius if infeasible |
| `naca_airfoil(chord, thickness, span=)` | wings, fins, blades, struts | symmetric NACA00xx section extruded along Y (`span` default = chord); `loft` two for a tapered wing |
| `fuselage_profile(length, max_diameter, nose_frac=, tail_frac=)` | aircraft/rocket bodies, pods | revolved body: rounded nose, constant mid, tapered tail (axis = Z) |
| `wheel(rim_radius, tire_radius, width)` | vehicle wheels, pulleys, rollers | disc with central axle bore; outer radius = `rim_radius + tire_radius`, axis = Z |
| `ribbed_plate(length, width, thickness, rib_count=, rib_height=)` | brackets, base plates, panels | flat plate + N stiffening ribs on top; bottom at Z=0 |
| `tube(outer_radius, inner_radius, length, axis=)` | pipes, bushings, sleeves, standoffs | hollow cylinder; bore runs full length; `axis` ∈ `"X"/"Y"/"Z"` |
| `hex_prism(across_flats, height, axis=)` | nut blanks, hex standoffs, hex stock | hexagonal prism; `across_flats` = wrench size (flat-to-flat) |
| `chamfered_box(length, width, height, chamfer_size, edges=)` | machined enclosures/housings with broken edges | angular counterpart to `rounded_box`; `edges="all"` or `"vertical"` |
| `l_bracket(length, width, height, thickness, fillet_radius=)` | L-shaped mounting brackets/angles | base plate (+X) + vertical wall (+Z) joined at X=0; optional rounded interior corner; bottom at Z=0 |
| `housing(length, width, height, wall=, fillet_radius=, open_top=, floor=)` | gearbox/pump bodies, electronics enclosures, valve bodies | designed shell (vs raw `Box−Box`): `wall`-thick walls + **broken (filleted) outer edges**, optional open top (cover mates there) + solid floor; bottom at Z=0 |
| `boss(diameter, height, hole_dia=, axis=)` | bearing seats, screw/insert bosses, standoffs | cylinder + optional concentric bore; base at origin along `axis`. Union onto a wall for a bearing seat (bore = bearing OD) |
| `rib(length, height, thickness, fillet_radius=)` | stiffening gussets where a wall meets a plate | right-triangle gusset in X-Z (thickness on Y, centred); right angle at origin, legs +X / +Z |
| `mounting_tab(length, width, thickness, hole_dia, fillet_radius=)` | mounting feet/lugs on a housing | flat plate, rounded outer corners + central bolt hole; bottom at Z=0 |
| `centered_on(part, ref, axes=)` | position a part relative to another (vs guessing `Location`) | moves `part`'s bbox center onto `ref`'s, on the chosen `axes` ("xyz" subset) |
| `offset_from(part, ref, dx=, dy=, dz=)` | place a part at a known offset | part center at `ref` center + (dx,dy,dz) |
| `coaxial(part, ref, axis=)` | shaft-in-bore / boss-on-hole alignment | matches the two cross-axis center coords to `ref` (keeps position along `axis`); validate with a `concentric` mate |
| `stack_on(part, ref, gap=, center=)` | cover on a housing, part on a face | part's bottom sits on `ref`'s top (+Z) + `gap`, XY-centered; validate with a `coincident` mate |

```python
# A humanoid torso + symmetric arms + blended head — no BuildSketch boilerplate:
torso = lofted_stack([(0,120,80),(200,150,90),(392,60)], label="torso")
arm_L = capsule(8, 120, label="arm_L").moved(Location((-90,0,300)))
arm_R = mirror(arm_L, about=Plane.YZ); arm_R.label = "arm_R"
head  = Sphere(45).moved(Location((0,0,440)))
result = organic_blend([torso, head], 12, label="body")  # smooth neck join
result = Compound(children=[result, arm_L, arm_R])
```

### Curve patterns — copy + adapt (when a helper doesn't fit)

**Tapered body via loft** (truck cabs, helmet crowns, conical housings):
```python
from build123d import *
with BuildPart() as bp:
    with BuildSketch(Plane.XY.offset(0)) as s1:
        RectangleRounded(200, 100, radius=10)
    with BuildSketch(Plane.XY.offset(100)) as s2:
        RectangleRounded(170, 90, radius=14)
    loft()  # smooth taper between the two sketches
result = bp.part
```

**Revolved profile** (bottles, vases, bell housings, axisymmetric parts):
```python
from build123d import *
with BuildPart() as bp:
    with BuildSketch(Plane.XZ) as s:
        with BuildLine() as l:
            Spline((0, 0), (30, 20), (40, 50), (35, 80), (40, 110))
            Line((40, 110), (0, 110))
            Line((0, 110), (0, 0))
        make_face()
    revolve(axis=Axis.Z)
result = bp.part
```

**Swept profile along a 3D path** (exhaust pipes, handles, cable routing):
```python
from build123d import *
with BuildLine() as path:
    Spline((0, 0, 0), (0, 20, 50), (0, 40, 100), (0, 30, 130))
with BuildPart() as bp:
    with BuildSketch(Plane(origin=path.line @ 0, z_dir=path.line % 0)) as prof:
        Circle(8)
    sweep(path=path.line)
result = bp.part
```

**Aggressive fillet for designed feel** — apply LAST, after all booleans:
```python
from build123d import *
with BuildPart() as bp:
    Box(100, 60, 40)
    fillet(bp.edges().filter_by(Axis.Z), radius=12)       # vertical edges
    fillet(bp.edges().group_by(Axis.Z)[-1], radius=4)     # top edges
result = bp.part
```

**Mirror for symmetric parts** — build one half, mirror the other:
```python
from build123d import *
left_arm = Box(40, 60, 100).moved(Location((-110, 0, 200)))
left_arm.label = "left_arm"
right_arm = mirror(left_arm, about=Plane.YZ)
right_arm.label = "right_arm"
result = Compound(children=[left_arm, right_arm])
```

**Named landmarks** — define proportions once, reference them everywhere:
```python
from build123d import *
# Landmarks (mm) — change here, the whole body re-proportions
HIP_Z, SHOULDER_Z = 232, 392
HIP_HALF, SHOULDER_HALF = 55, 150

with BuildPart() as bp:
    with BuildSketch(Plane.XY.offset(HIP_Z + 30)) as base:
        Rectangle(HIP_HALF * 2 + 20, 90)
    with BuildSketch(Plane.XY.offset(SHOULDER_Z)) as top:
        Rectangle(SHOULDER_HALF * 2, 80)
    loft()
result = bp.part
```

### Engineering Mode — well-formed mechanical parts

Counterpart to Industrial Design Mode. Activate when the user names a
**mechanical/engineering part** that downstream tools will need to
understand — `bracket`, `housing`, `enclosure`, `manifold`, `fixture`,
`frame`, `mount`, `flange`, `chassis`. These parts are usually destined
for CNC/3D-printing or FEA, so structure (named features, manufacturable
geometry, protected mounting interfaces) matters as much as silhouette.

Use the **canonical feature vocabulary** from
`aieng/schemas/feature_graph.schema.json` for part labels — the
`_topology_to_feature_graph` heuristic in the workbench recognizes these
names and tags them with semantic intent in the feature graph an agent
can query later:

| `.label` to use | Semantic role |
|---|---|
| `base_plate`, `back_plate`, `mount_plate` | Primary load-bearing flat body |
| `mounting_hole` / `mounting_hole_pattern` | Bolted interfaces — **protected**, don't modify casually |
| `rib`, `rib_<N>` | Stiffeners on plates / shells |
| `boss`, `boss_<name>` | Localized features carrying threaded inserts / screws |
| `flange` | Mating face for bolted assembly |
| `interface_face`, `load_interface` | Where external loads or other assemblies attach |
| `wall`, `wall_<face>` | Enclosure side walls |
| `cover`, `lid` | Removable enclosure top |

**Manufacturing rules to honor** (`cad.critique` selects rule packs by process;
CNC aluminium is the default):
- Minimum wall thickness ≥ **3 mm** (CNC), ≥ **2 mm** (sheet metal), ≥ **1.2 mm** (FDM), ≥ **0.8 mm** (SLA).
- Minimum internal corner radius ≥ **2 mm** (CNC), ≥ **0.5 mm** (sheet metal), ≥ **1 mm** (FDM), ≥ **0.4 mm** (SLA).
- Minimum hole-edge distance ≥ **2 × hole radius**.
- Through-holes prefer multiples of standard drill sizes for CNC/sheet metal; additive processes skip this check.
- Avoid undercuts unless the user explicitly asks for them (machinable from one side).

`cad.critique` accepts `process: cnc | sheet_metal | fdm | sla`. Each pack changes
which thresholds trigger a finding; the finding itself reports the pack name and
thresholds used. Explicit `min_wall_mm` or `min_corner_radius_mm` overrides the
selected pack. This is a deterministic heuristic audit, not a GD&T solver.

**Workflow:**
1. Decompose the part into named features (base_plate + holes + ribs +
   bosses + interfaces) **before** writing code. State each feature's
   role explicitly in a brief plan.
2. Build with the canonical labels above — the resulting topology and
   feature_graph then carry engineering semantics that the user (and
   downstream FEA tools) can introspect.
3. Apply the manufacturing rules during sizing — pick wall thicknesses
   and hole spacings that respect them.
4. Once geometry is in, call `cad.critique` (engineering mode) to get a
   deterministic audit against the same rules. The critique walks the
   feature graph and reports violations.
5. For parts destined for FEA, also fix mounting interfaces and load
   surfaces explicitly (the user usually wants these `@face:` pointers
   to drive `cae.apply_setup_patch`).

Example — a CNC bracket with two named ribs and a 4-bolt mounting
pattern (this is the same intent encoded in
`aieng/examples/definition_simple_bracket.yaml`, expressed as code):
```python
from build123d import *

with BuildPart() as bp:
    Box(120, 80, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    # 4 mounting holes — 10mm dia, on a 90x50 pattern, ≥ 2× r from edges
    with Locations((45, 25, 0), (-45, 25, 0), (45, -25, 0), (-45, -25, 0)):
        Hole(radius=5, depth=8)
    fillet(bp.edges().filter_by(Axis.Z), radius=4)  # preferred 2mm+ corner
base_plate = bp.part
base_plate.label = "base_plate"
base_plate.color = Color(0.55, 0.62, 0.70)

# Named rib — fits the canonical type so feature_graph tags it as `rib`
rib = Box(60, 5, 25, align=(Align.CENTER, Align.CENTER, Align.MIN))
rib = rib.moved(Location((0, 0, 8)))
rib.label = "rib_main"
rib.color = Color(0.55, 0.62, 0.70)

result = Compound(children=[base_plate, rib])
```

### Structural FEA (CalculiX)

Linear static analysis pipeline — see workflow C below.

**Analysis types (`analysis_type`).** The solver deck and result extraction are
analysis-type-aware. Set `analysis_type` in the CAE setup (via
`cae.apply_setup_patch`, written to `simulation/solver_settings.json`); the deck
generator emits the matching CalculiX step and `cae.run_solver` routes result
extraction to the right file:

| `analysis_type` | Step card | Required inputs | Results file | Key metrics |
|---|---|---|---|---|
| `static` (default) | `*STATIC` | material, **loads**, constraints | `.frd` (DISP/S) | `max_displacement`, `max_von_mises_stress` |
| `modal` / `frequency` | `*FREQUENCY` + `num_modes` (default 10) | material (**+ density**), constraints — **no loads** | `.dat` | `natural_frequencies_hz`, `first_natural_frequency_hz` |
| `buckling` / `buckle` | `*BUCKLE` + `num_factors` (default 5) | material, **loads** (reference), constraints | `.dat` | `buckling_factors`, `lowest_buckling_factor` |
| `thermal` / `heat_transfer` | `*HEAT TRANSFER, STEADY STATE` | material (**conductivity**), temperature constraints (**`*BOUNDARY` DOF 11**); heat flux load optional | `.frd` (NDTEMP) | `max_temperature`, `min_temperature` |
| `thermal_structural` / `thermal_stress` | `*UNCOUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE` | material (**conductivity + elastic + expansion**), temperature constraints (DOF 11) **and** structural constraints (DOF 1-3) | `.frd` (NDTEMP/DISP/S) | `max_temperature`, `max_displacement`, `max_von_mises_stress` |

For a `thermal` analysis the material carries `thermal_conductivity_w_mk` (W/m·K,
numerically equal to the consistent mm-tonne-s value), temperature BCs are written
as ordinary boundary conditions on **DOF 11** with the fixed temperature as their
`value` (e.g. `{target: "@face:...", dof_start: 11, dof_end: 11, value: 100}`), and
an optional concentrated heat flux is a load on DOF 11 (`*CFLUX`). A steady-state
conduction field is driven by its temperature BCs, so **no load is required**
(like modal).

A **`thermal_structural`** analysis adds thermal-expansion stress: it needs a
material **expansion** coefficient (`thermal_expansion_per_k`, plus `elastic` and
`conductivity`), an optional `reference_temperature` in `solver_settings` (the
strain-free temperature, default 0), **and** both temperature BCs (DOF 11) and
structural BCs (DOF 1-3) — fix the part so its restrained thermal growth produces
stress. It runs CalculiX `*UNCOUPLED TEMPERATURE-DISPLACEMENT` (solve temperature,
then the displacement it induces) and returns temperature, displacement and von
Mises stress in one pass.

Honesty boundary: steady-state linear conduction / sequential (one-way)
thermal-stress only — no transient, no radiation, no temperature-dependent
properties, and no fully-coupled (two-way) thermomechanics.

Honesty boundary: modal results are **linear undamped** natural frequencies (no
damping / prestress); buckling results are **linear (eigenvalue / Euler)** load
factors (`critical load = factor × applied reference load`) — no imperfection
sensitivity or post-buckling. The `/simulate` readiness report
(`simulation_readiness.py`) reflects the analysis-type-aware required inputs (a
modal request does not flag missing loads; a buckling request does). Modal needs
material **density** for the mass matrix and uses a consistent mm–tonne–s unit
system for physically-meaningful Hz. NAFEMS-style reference cases
`cantilever_modal` (first natural frequency) and `column_buckling` (Euler factor)
verify these paths — see `aieng/docs/nafems_vv_cases.md`.

**Installing CalculiX.** `cae.run_solver` needs the `ccx` executable available at runtime.

- **Windows + conda (recommended):** create a dedicated environment so installing
  CalculiX does not downgrade the main env's OpenSSL:
  ```powershell
  conda create -n calculix-env -c conda-forge calculix
  ```
  Then point the backend at it with the `AIENG_CCX_CMD` environment variable.
  **Use the `conda run` launcher form — it is the most reliable on Windows**
  because it activates the CalculiX environment so `ccx` can find its runtime
  DLLs:
  ```powershell
  $env:AIENG_CCX_CMD = "conda run -n calculix-env ccx"
  ```
  **Avoid pointing `AIENG_CCX_CMD` at a bare `ccx.exe` absolute path on Windows.**
  It passes detection but the executable, run outside its conda environment,
  fails to load its runtime DLLs and **crashes with a Windows access-violation
  code and no output** (prepending the env's `Library/bin` to PATH is *not*
  enough — full conda activation is required). `cae.run_solver` detects this
  crash signature and tells you to switch to the `conda run` form.
  The `conda run` launcher is resolved via `CONDA_EXE` / a `conda.exe` on PATH
  (usually present when uvicorn is launched from an activated conda shell);
  if it cannot be resolved, `cae.prepare_solver_run`'s `missing_items` message
  states exactly why. **The env var must be set in the same shell that launches
  `uvicorn`** (the backend process inherits it; a different shell will not). Do
  **not** install `calculix` directly into `aieng311` — it downgrades OpenSSL.

- **Linux/macOS:** use the system package (`apt install calculix-ccx`, `brew install calculix`)
  or a separate conda env. If `ccx` is already on the activated PATH, `AIENG_CCX_CMD` is optional.

- **Docker image:** already bundles `ccx`; no extra setup needed.

**`cae.prepare_solver_run` returns `recommended_next_calls`.** When the package is
not ready, the response includes a `recommended_next_calls` list. Each entry is
either a tool call (`tool`, `input`, `reason`) or an environment/action item
(`action`, `reason`). Present these as the next actionable steps. A tool entry can
be invoked directly; the solver itself (`cae.run_solver`) remains approval-gated
and is only recommended once every preflight item passes. If topology validation
reports stale face references, the first recommendation will be to rebind via
`ai_preprocessing.run_ai_preprocessing` (or `cae.apply_setup_patch`) before the
solver is offered.

**Topology revision validation.** CAE loads and boundary conditions are bound to
`@face:*` pointers that live in `geometry/topology_map.json`. Every time AI
preprocessing writes `simulation/setup.yaml` and `simulation/cae_mapping.json`, it
records a `topology_hash` of the current geometry. When a CAD edit regenerates the
model, the old CAE mapping is automatically marked `stale` and the hash is
updated.

Before running the solver, both `cae.prepare_solver_run` and `cae.run_solver`
check the recorded hash and the existence of every referenced face. If they do not
match the current topology, the run is refused with code
`stale_topology_references` and a list of the stale or missing face IDs.

For batch/parametric workflows (`opt.sizing_sweep`, `opt.cae_evaluate_candidate`,
`solve_candidate_geometry`) the runner can attempt an **adaptive geometric rebind**
instead of refusing outright. It matches each stale `@face:*` reference to the
closest face in the regenerated topology by surface type, centroid, normal/axis,
radius, and area, then continues the solve only when every match is high
confidence. Ambiguous or low-confidence matches are still refused and reported;
the baseline package is never mutated.

To recover:
1. For parametric variants / design-study candidates, enable adaptive rebind by
   calling the solver with `rebind_faces=True` and a `baseline_package_path`.
   `opt.sizing_sweep` and `solve_candidate_geometry` already do this.
2. For one-off geometry edits, call `ai_preprocessing.run_ai_preprocessing` with
   the same (or updated) task description to rebind loads/BCs against the new
   topology.
3. Or use `cae.apply_setup_patch` to manually update `simulation/cae_mapping.json`
   (`face_ids` and `topology_hash`) and `simulation/setup.yaml`.
4. Then re-run `cae.prepare_solver_run` and `cae.run_solver`.

---

## Pointer syntax — `@kind:id`

Tool responses and `aieng.agent_context` output use pointer tokens to reference
geometry entities precisely. Use them verbatim in tool arguments and in messages
to the user — the UI renders them as clickable chips.

| Prefix | Refers to |
|--------|-----------|
| `@face:id` | A BREP face (loads / supports / fixtures) |
| `@feature:id` | A CAD feature (use in `cad.edit_parameter` featureId) |
| `@edge:id` | A BREP edge |
| `@group:id` | A named face group (load surface, constraint surface, …) |
| `@artifact:id` | A package artifact (step file, mesh, result file, …) |

Example: if `aieng.agent_context` reports `@face:f_top_001` as a flat surface
suitable for a fixed support, pass `"faceId": "f_top_001"` in your CAE setup call.

**Free-form faces and CAE.** Faces produced by the high-level helpers
(loft/sweep/sphere/spline) now keep the best available surface class
(`bspline`, `bezier`, `sphere`, `cone`, `torus`, `surface_of_revolution`,
`surface_of_extrusion`, or `freeform`) and carry `freeform: true`, `uv_bounds`
when available, and a *proxy* normal sampled at the face midpoint. That is
enough to pick them and bind an approximate CAE boundary condition, but the
node-mapping is a tangent-plane band, not the exact curved surface. For accurate
fixtures/loads prefer **planar faces** (a `rounded_box` keeps its flat faces as
true planes with exact normals). This is also good engineering practice —
fixture and load on flat interfaces.

---

## Credibility tiering (read the `credibility` stamp)

Every result-bearing output carries a single **V&V-40 credibility stamp** under a
`credibility` key, derived from one shared classifier
([`aieng/src/aieng/converters/credibility.py`](aieng/src/aieng/converters/credibility.py)).
It consolidates the scattered honesty flags (`solver_executed`,
`is_solver_evidence`, `contact_physics_modeled`, `bolt_preload_modeled`,
`uncertainty_std`, `production_ready`) into one ordered tier so you don't
re-derive trust from a grab-bag of booleans. Tiers, **low → high credibility**:

`critique_finding` < `surrogate_prediction` < `proxy_assembly_result` < `executed_solver_result`

The stamp is `{tier, rank, label, evidence_basis, production_ready, signals, …}`.
The honesty invariant: a tier is **never more credible than its evidence** — an
output that claims `solver` but whose `solver_executed` is not `true` is
downgraded to `unverified` (rank 0) with a `downgrade_reason`. **Surface the tier
to the user** and never present a lower tier as if it were an executed-solver
result. `production_ready` is `false` unless explicitly certified — the workbench
does not certify by default.

---

## Tool taxonomy

### Onboarding / discovery (read-only)

| Tool | Purpose |
|------|---------|
| `aieng.agent_readme` | Compact operational quickstart by default; `detail: "full"` returns this complete guide |
| `aieng.guide` | Task-specific detailed sections from this canonical guide (`cad`, `cae`, `pointers`, `workflows`, etc.) |
| `aieng.list_projects` | All known projects with id, name, status, and (for agent-built geometry) `named_parts` + `part_count` |
| `aieng.find_projects_by_part` | Locate a project by a part label (case-insensitive substring on `named_parts`) |
| `aieng.agent_context` | Compact context: pointers, stale warnings, next steps |
| `aieng.inspect_package` | Full project summary: geometry, CAE setup, results, verdict |

### Read-only inspection (no approval)

| Tool | Purpose |
|------|---------|
| `aieng.read_audit_log` | Recent agent/user actions on this project |
| `aieng.recent_activity` | Recent CAD build/activity events + iteration errors for a project (paginated by `limit` / `since_ts`) — headless build feedback without the web viewer. Poll with `since_ts=latest_ts` for new events |
| `aieng.validate` | Schema + rule validation report (no mutation) |
| `aieng.write_completeness_report` | What is missing before simulation |
| `cae.prepare_solver_run` | Solver preflight — checks readiness, runs nothing. Returns `recommended_next_calls` with tool/input/reason entries for missing artifacts and stale face references |
| `cad.get_source` | Accumulated build123d source + `{named_parts, has_base}` — call before an incremental edit |
| `cad.list_editable_parameters` | List the parameters editable fast via `cad.edit_parameter` (the "point" of point-and-shoot): per-parameter `featureId`/`parameterName`/`cad_parameter_name`/current/min-max + `scope` (`local`/`global`/`unscoped`) + a summary. Answers "what can I change here?" |
| `cad.validate_subpart` | Execute a build123d **fragment** in isolation (no package write, no project mutation) and report whether it builds into a usable solid — build success or the exact error, non-empty-solid check, solid/face counts, per-part + total volume/area, union bbox. Verify a sub-structure (sketch→solid, a boolean, one sub-assembly) **before** committing it. `valid` = builds into a non-empty solid, NOT a manifold/watertight guarantee |
| `cad.validate_targets` | Deterministic geometry **target validator**: pass a list of targets (`named_part_present`, `feature_present`, `part_count`, `overall_size`, `part_size`, `part_center`, `no_floating_parts`, `no_deep_overlap`) and get pass/fail/unknown per target with measured-vs-expected. If no `targets` are passed, auto-loads the CAD brief's `validation_targets`. Verifies the brief's exact promises — catches plausible-but-mispositioned / over-modeled builds. Bbox-level, not GD&T; read-only |
| `cad.author_brief` | Author the **pre-code CAD brief** + validation-target list (units, model_type, parts, key dimensions) BEFORE `cad.execute_build123d`. Stored as a project sidecar; auto-derives `validation_targets` that `cad.validate_targets` checks the built model against — the plan→build→verify loop. Planning artifact only (no approval, no geometry) |
| `cad.get_brief` | Read-only: return the project's authored CAD brief (or `not_found`) |
| `cad.diagnose` | Read-only **diagnostic snapshot + repair verdict**: composes `cad.design_review` (critique + symmetry + fidelity + fix targets), structural checks (no floating / deep overlap), and the brief's `validation_targets` into one snapshot with risk `triggers`, a `ready` / `needs_repair` `verdict`, and prioritized `repair_actions`. Repair-loop contract: for a high-risk build, fix every blocking issue and re-diagnose until `ready` before presenting. Mutates nothing |
| `cad.critique` | Deterministic engineering audit with process-aware DfM rule packs (`cnc`, `sheet_metal`, `fdm`, `sla`). Checks min wall, standard hole sizes (CNC/sheet_metal only), floating components, and missing mounting interfaces. Also returns a separate **`fidelity`** block (modeling quality: `designed`/`basic`/`crude` + score) flagging primitive-stacking, no edge-breaking, bare boxes, and hidden parts. Each finding cites the rule pack/thresholds. Read-only. |
| `cad.design_review` | Read-only self-review: `cad.critique` + the left/right **symmetry** checks critique lacks + a concrete `cad.edit_parameter` **fix target** (featureId/parameterName/range) bound to each fixable finding, **plus the modeling-`fidelity`** verdict (separate axis from DfM — a crude-but-manufacturable part is flagged so you don't present it as done). Returns a severity-ranked `actions` list + merged verdict. Changes nothing; fixes still go through approval. Use it to self-correct before presenting a result |
| `cad.list_snapshots` | List the recent CAD undo timeline. A snapshot is recorded automatically after each successful `execute_build123d`/`edit_parameter`/`replace_part`/`remove_part`. Returns tiny metadata only (`snapshot_id`, `created_at`, `tool_name`, `part_count`, `named_parts`) — pair with `cad.restore_snapshot` |

### Geometry creation (requires approved modeling plan — mutates package)

| Tool | Purpose |
|------|---------|
| `cad.execute_build123d` | Run caller-supplied build123d code to create/replace geometry (mode=replace\|append). Optional `name` sets a human-recognizable project name (else placeholder projects are auto-named from part labels); optional `model_kind` (auto\|organic\|mechanical) gates the feature-graph heuristics |
| `cad.edit_parameter` | Fast parametric edit: replaces a named constant in `source.py` + re-executes build123d (no LLM). Requires the feature to carry editable parameters — see "Parametric editing" below |
| `cad.replace_part` | Swap ONE named part (by `.label`) for caller-supplied build123d code, keeping everything else. Re-executes, no LLM. See "Part-level edits" below |
| `cad.remove_part` | Drop ONE named part (by `.label`) from the model. Re-executes, no LLM |
| `cad.set_reference_image` | Attach a reference photo/drawing to a project so future thumbnails include it side-by-side for proportion calibration |
| `cad.search_reference_image` | Search Wikimedia Commons for `query` and auto-attach the best match via `cad.set_reference_image` — use when the user names a real target but gives no picture. Returns `page_url` for source/license verification; `no_results` degrades gracefully. No approval (same as `set_reference_image`) |
| `cad.restore_snapshot` | Roll the project back to an earlier snapshot (`snapshot_id` from `cad.list_snapshots`): replaces the `.aieng` package with the snapshot and republishes the viewer, clearing stale flags. Undo for an unwanted edit. Confirm first — the current state is not auto-snapshotted before restore |

### Materials & standard parts (read-only)

| Tool | Purpose |
|------|---------|
| `aieng.list_materials` | List available engineering materials, optionally filtered by `category` or `query` |
| `aieng.get_material_details` | Return full properties (mechanical, thermal) for a specific material |
| `aieng.compare_materials` | Compare two or more materials side-by-side with normalized scores |
| `aieng.list_standard_parts` | List available standard part types (fasteners, bearings, shafts, profiles, holes) |
| `aieng.get_standard_part_specs` | Return Shape IR spec, editable parameters, and presets for a part type |
| `aieng.insert_standard_part` | Insert a standard part into the current project as Shape IR (preset + optional overrides) |
| `aieng.generate_bom` | Generate a Bill of Materials from the project's feature graph |

**Usage examples:**
```
# Query materials
aieng.list_materials { category: "Aluminum Alloy" }
aieng.get_material_details { material_name: "Al6061-T6" }
aieng.compare_materials { material_names: ["Al6061-T6", "Steel-316L"] }

# Query and insert standard parts
aieng.list_standard_parts { category: "fastener" }
aieng.get_standard_part_specs { part_type: "hex_bolt", preset_name: "M8" }
aieng.insert_standard_part { part_type: "hex_bolt", preset_name: "M8", position: [0,0,0] }

# Generate BOM
aieng.generate_bom { format: "markdown" }
```

Before an incremental edit, call **`cad.get_source`** (read-only) to see the current
accumulated script, which named parts already exist, and whether `has_base` (append
is possible).

### CAE setup (no approval)

| Tool | Purpose |
|------|---------|
| `cae.apply_setup_patch` | Patch CAE setup artifacts (materials, BCs, mesh params) |
| `cae.generate_solver_input` | Generate CalculiX `.inp` deck from setup artifacts |
| `cae.write_mesh_handoff` | Write mesh handoff contract for external Gmsh |
| `cae.import_solver_evidence` | Import an external solver result file as evidence |

### Simulation execution (requires approval — runs external CalculiX)

| Tool | Purpose |
|------|---------|
| `cae.run_solver` | Execute CalculiX on the generated input deck |

### Post-processing (no approval)

| Tool | Purpose |
|------|---------|
| `cae.extract_solver_results` | Parse CalculiX FRD → `computed_metrics.json` |
| `cae.extract_field_regions` | Cluster high-stress / high-displacement regions |
| `cae.map_results` | Map stress/deflection results back to topology entities, object_registry objects, and `source_ir_node` → `analysis/cae_result_map.json` (unmapped regions reported honestly) |
| `cad.tolerance_stackup` | Read-only 1D tolerance stack-up: pass an ordered list of contributors (name, nominal, plus, minus, optional distribution) and get worst-case arithmetic min/max, RSS sigma and confidence-band min/max, controlling contributors, and honesty notes. Assumes independence and +/- 3-sigma tolerance coverage; not a GD&T solver. No geometry mutation. |
| `opt.sizing_sweep` | Parametric sizing sweep (approval required): vary ONE editable dimension across explicit `values` OR a `{min, max, steps/step}` range, solve each variant with real static FEA, and rank by objective. Range values are clamped to the parameter's declared min/max. Default is recommend-only; set `apply_winner=true` to apply the winning value through the audited `cad.edit_parameter` path and report its `regression_diff`. A variant that fails to solve is reported honestly and never recommended. |
| `opt.doe_sizing_study` | Multi-parameter DOE sizing study (approval required): jointly vary 2+ editable parameters by explicit values or ranges, generate a full-factorial or LHS design within a 64-point budget, solve each design point with real static FEA, and rank by objective + constraints. Baseline never modified; failed points reported honestly. |
| `opt.derive_problem_from_cae` | Derive a topology-optimization problem (grid + supports + loads + design space) from a project's CAE setup (`simulation/setup.yaml`) + geometry (`topology_map` faces + design-space bbox). Read-only; returns the problem + a `derivation` block. `dimension=2d` (default) projects supports/loads onto the plane of the two largest dims (out-of-plane force dropped); `dimension=3d` keeps the full 3D layout (structured voxel grid, supports→boundary layers, full 3D force) and returns `status=needs_user_input` with diagnostics if BCs can't be safely mapped |
| `opt.run_topology_optimization` | Run topology optimization (built-in self-contained SIMP, compliance-min, pure numpy — no external solver) → `analysis/topology_optimization.json`. `simp_2d` (default) or `simp_3d` (experimental structured-voxel 3D, `dimension=3d`; honest `capability` block: experimental_reference, production_ready:false). Honest coarse limitations recorded. Set `auto_derive` (or omit `problem`) to derive supports/loads/design-space from the project's CAE setup; 3D may return `needs_user_input` instead of guessing |
| `opt.writeback_to_shape_ir` | Author the optimization result back into `geometry/shape_ir.json`, then recompile through runtime routing → the optimized body meshes/views + gets verification + object_registry, linked to its `design_space_node`. 2D: `method=contour` (default) writes a marching-squares boundary as an `extruded_region` (`boundary=spline` default → closed periodic spline / CAD-friendly curve, falls back to `polygon` if it would overshoot the design-space envelope); `method=voxels` writes the blocky `density_voxels`. 3D: `method=surface` (default) writes a smooth **marching-cubes** `surface_mesh` proxy (mesh / lossy / not production CAD; falls back to `voxels` if no isosurface); `method=voxels` writes the blocky 3D `density_voxels`. Placed in the design-space frame. Default representation `brep_build123d` for 2D (analytic faces — pickable, STEP-exportable; auto-falls back to `manifold_mesh` if the B-Rep build fails); 3D defaults to `manifold_mesh` |

**Mesh-to-CAD reconstruction honesty.** Mesh outputs may run a conservative
backend-only reconstruction ladder after region segmentation / analytic fitting:
face candidates → OCC face validation → stitching plan → OCC sewing →
closed-shell solidification → STEP export → roundtrip verification. STEP export is
allowed only when OCC validates a real closed shell and solid; partial shells write
diagnostics (`diagnostics/mesh_brep_sewing.json`,
`diagnostics/mesh_brep_step_export.json`,
`diagnostics/mesh_brep_roundtrip_verification.json`) but no STEP. Successful
reconstruction writes derived CAD only to `geometry/reconstructed.step` and never
overwrites the source/generated STEP. When reconstructed topology replaces
`geometry/topology_map.json`, the original mesh topology is preserved at
`geometry/mesh_topology_map.json`; failed reruns remove stale reconstructed artifacts
and restore mesh topology. Reconstructed STEP (`geometry/reconstructed.step`) is
mesh-derived/lossy, not original design history, not production CAD certification,
and freeform/NURBS fitting remains future work.
| `postprocess.generate_computed_metrics` | Import metrics from CSV/JSON |
| `postprocess.refresh_cae_summary` | Regenerate result summary + evidence markdown |

### Package lifecycle

| Tool | Purpose |
|------|---------|
| `aieng.convert` | Import STEP/FCStd/Shape IR into a `.aieng` package. Shape IR compiles by `representation`: `brep_build123d` (default) → build123d STEP/B-Rep; `nurbs_brep` → OCP NURBS B-Rep surfaces (per-patch `bspline` faces); `implicit_sdf` → fogleman/sdf mesh; `manifold_mesh` → manifold3d CSG mesh. B-Rep reps give analytic per-face topology; mesh reps give region-level faces. Publishes a viewer preview |
| `aieng.apply_shape_ir_patch` | **[APPROVAL]** Apply a surgical patch to a project's Shape IR (set_parameter / move_control_point / add_node / remove_node / replace_node / connect / disconnect / change_representation_backend). Atomic + validated; on success recompiles through runtime routing and refreshes verification + object registry. `dry_run` previews without writing |
| `aieng.generate_preview` | Regenerate GLB/STL web preview from current STEP |
| `aieng.refresh_semantics` | Re-validate and re-extract semantic labels |
| `aieng.update_validation_status` | Write per-category validation flags |
| `aieng.write_evidence_scaffold` | Initialize `results/evidence_index.json` scaffold |
| `aieng.delete_project` | **[APPROVAL]** Permanently delete a project — its directory + chat sessions/messages. Irreversible |

### MCP introspection

| Tool | Purpose |
|------|---------|
| `mcp.check` | Guardrails, capability gaps, operation policy for this project |
| `mcp.parse_patch` | Validate a patch proposal without applying it |
| `mcp.prepare_execution` | Dry-run a patch proposal and return preflight side effects |

---

## Recommended workflows

### A — Inspect and understand a project
```
aieng.agent_context { project_id }
```
Read the geometry summary, note any `@artifact:` tokens marked stale, check
`suggested_next_steps`.

### B — CAD generation from scratch
```
1. cad.get_source            { project_id }                                (is there already a base?)
2. cad.set_reference_image   { project_id, image_url }                     (only when modelling a real product/character — sets a reference for every future thumbnail)
3. cad.execute_build123d     { project_id, code }                          [APPROVAL REQUIRED] (mode=replace, default)
4. (inspect the returned thumbnail + named_parts to confirm the shape is right)
```
Set `.label` and `.color` on each part in your build123d code and combine with
`Compound` so the result carries semantic names + readable colors (see the
build123d section above). After step 3 the project is `viewer_ready_glb` and
the web preview is current — no separate `generate_preview` call needed for
agent-built geometry. Step 2 is optional but **strongly recommended** for any
named real-world target: it pins a reference image into the project so every
build's thumbnail shows your model next to the truth.

### B2 — Incremental modeling (the sustainable loop)
```
1. cad.get_source         { project_id }                (source, named_parts, has_base)
2. cad.execute_build123d  { project_id, code, mode: "append" }   [APPROVAL REQUIRED]
3. (check response parts_added / named_parts and the thumbnail; repeat from 1)
```
In append mode the previous model is exposed as `previous_result`; your code adds to
it and must still reassign `result`. The response's `parts_added`, `named_parts`,
`mode`, and `used_base` tell you exactly what this step did — use them to decide the
next step instead of guessing or re-deriving state. Prefer this over resubmitting the
whole script each time.

### C — CAD → CAE simulation pipeline
```
1. aieng.agent_context        { project_id }
2. cae.apply_setup_patch      { project_id, patch }      (material, BCs, mesh)
3. cae.prepare_solver_run     { project_id }             (preflight, no execution)
4. cae.generate_solver_input  { project_id }             (write CalculiX .inp deck)
5. cae.run_solver             { project_id }             [APPROVAL REQUIRED]
6. cae.extract_solver_results { project_id }
7. cae.extract_field_regions  { project_id }
8. postprocess.refresh_cae_summary { project_id }
```

If step 3 reports missing artifacts or stale topology references, follow the
`recommended_next_calls` list before proceeding to step 4. The solver
(`cae.run_solver`) is only recommended once the preflight is fully ready and
remains subject to the normal approval gate.

### D — Inspect results and explain findings
```
1. aieng.agent_context        { project_id }
2. cae.extract_field_regions  { project_id, field: "stress" }
```
Summarize the high-stress clusters; reference faces with `@face:id` so the user
can click to highlight them.

### E — Parametric modification (design iteration)
```
1. aieng.agent_context     { project_id }
2. cad.edit_parameter      { project_id, featureId, parameterName, newValue }  [APPROVAL]
3. aieng.refresh_semantics { project_id }
4. (re-run the CAE pipeline if geometry changed)
```

---

## Approval-gated tools

CAD creation and ordinary editing use **one confirmation at the modeling-plan
boundary**, not one approval per execution step. The confirmation must use an
interactive control in the connecting agent; do not merely print a question and
end the conversation. Before the first
`cad.execute_build123d`, `cad.edit_parameter`, `cad.replace_part`,
`cad.remove_part`, or `cad.refine` call for a new request:

1. Present a concise modeling plan: intended parts/features, important
   dimensions/assumptions, and the expected CAD mutations.
2. Prefer the agent's native interactive question tool when available:
   Claude Code `AskUserQuestion`, Codex `request_user_input`, or the equivalent.
   Offer **Approve plan**, **Revise plan**, and **Cancel**; include a free-form
   revision path when the client supports it.
3. If no native question tool is available, call `cad.confirm_modeling_plan` so
   the MCP client displays its permission dialog. Do not invoke a CAD mutation
   if the user denies or cancels.
4. After approval, continue in the same task and iterate within that approved
   plan without asking again for every CAD tool call.
5. Ask for a new plan confirmation if the goal or mutation scope changes
   materially. Small corrective iterations discovered during visual/design
   review remain inside the approved plan.

Writing "please confirm" is not confirmation, and the original modeling request
does not itself approve the plan. Never end the conversation merely to wait for
a plain-text confirmation.

High-risk operations remain individually `[APPROVAL REQUIRED]`:
`cad.restore_snapshot`, `cae.run_solver`, `aieng.delete_project`, and
`aieng.apply_shape_ir_patch`.

---

## Stale-artifact warnings

After a geometry edit, `aieng.agent_context` includes an **EDIT IMPACT** section
listing `@artifact:` references needing revalidation. Treat these as hard blockers
before running a simulation. Typical fix:
```
1. aieng.refresh_semantics   { project_id }
2. cae.generate_solver_input { project_id }
3. cae.run_solver            { project_id }   [APPROVAL REQUIRED]
```
(A fresh `cad.execute_build123d` automatically clears stale state.)

---

## If the backend (port 8000) is unreachable

You may see `{"status": "error", "code": "connection_refused"}` or timeouts when
`AIENG_BACKEND_URL` is set — the FastAPI backend is not running.

**Do NOT restart processes yourself.** Tell the user and ask them to start it:
```powershell
conda activate aieng311
cd aieng-ui/backend
uvicorn app.main:app --reload --port 8000
```
Verify with `aieng.list_projects`. Note: if the backend is down, the MCP server
**falls back to in-process execution automatically** — tools still work (no live
UI), so you can usually continue regardless.

---

## .aieng package structure (reference)

The backend manages all package I/O; never read it directly. Structure:
```
<project_id>.aieng   (ZIP)
├── metadata.json            project name, status, timestamps
├── geometry/                source.py, sdf_source.py / manifold_source.py, shape_ir.json, generated.step, preview.stl/.glb, topology_map.json
├── graph/                   aag.json, feature_graph.json, interface_graph.json, brep_graph.json
├── state/                   revalidation_status.json (stale-artifact flags)
├── diagnostics/             shape_ir_verification.json, shape_ir_patch_report.json
├── registry/                object_registry.json (Shape IR node ↔ topology/mesh/viewer ids + params)
├── analysis/                computed_metrics.json, field_regions.json (solver-neutral CAE), cae_result_map.json (CAE ↔ topology/node), topology_optimization.json, design_study_problem.json
├── patches/                 (optional) design_candidates/<candidate_id>.json (proposed, validated, NEVER auto-applied)
├── candidates/              (optional) <candidate_id>/ derived design-study workspace (patch.json, geometry/shape_ir.json, provenance/, analysis/evaluation.json) — never overwrites baseline
├── provenance/              conversion_manifest.json (converter + geometry_execution record)
├── assembly/                (optional, multi-part) assembly_ir.json, part_registry.json, connection_graph.json, interface_resolution.json
├── simulation/              setup/deck artifacts, including assembly_cae_setup_draft.json, assembly_cae_model.json, optional assembly_calculix.inp
├── cae/                     setup.json, mesh_params.json, simulation/ (CalculiX .inp/.frd)
├── results/                 computed_metrics.json, field_regions.json, evidence_index.json
└── audit_log.jsonl          append-only action history
```

### Design study v0 (optional, parameter studies)

A package MAY carry `analysis/design_study_problem.json` — a backend contract for an
**agent-guided parameter design study**: design variables (with bounds / allowed values /
`safe_to_modify` / `semantic_role`), plus constraints and an objective that are **recorded, not
executed**. Proposed parameter changes live under `patches/design_candidates/<candidate_id>.json`.
This is **contract + validation only**: `POST /api/projects/{id}/design-study/validate` (or a
recompile) validates the problem (`diagnostics/design_study_problem_diagnostics.json`) and every
candidate (`diagnostics/design_study_candidate_validation.json`) — checking bounds, allowed values,
`safe_to_modify`, **protected interface variables**, `max_variables_per_candidate`, assembly
`selected_part_id` scope, and reasoning. **No optimization/search is run, no candidate is applied,
no geometry is recompiled, no CAE is run, and the baseline geometry is never modified.** Valid
candidates are normalized (`applied:false`) but not applied.

A validated candidate can then be **explicitly executed** into a derived workspace via
`POST /api/projects/{id}/design-study/candidates/{candidate_id}/run` (PR2). This applies the
patch to a DEEP COPY of the baseline Shape IR, writes `candidates/<id>/` (patch + derived
`geometry/shape_ir.json` — or `parts/<part>/geometry/shape_ir.json` for assembly part-scoped —
+ provenance + `analysis/evaluation.json`), and, when `compile` is enabled (default), recompiles
the candidate in a **throwaway copy** of the package so the baseline package's geometry artifacts
are never created or overwritten. Each run appends a deterministic `iter_NNN` record to
`analysis/design_study_iterations.json` and rebuilds `diagnostics/design_study_report.json`.
Execution is explicit and single-shot — **no optimizer/search/Pareto loop, no CAE, and no
candidate is ever auto-promoted into the baseline** (`baseline_modified:false` everywhere; best a
valid candidate reaches is `refine_candidate`).

Candidate evidence can be **explicitly evaluated** (PR5) via
`POST /api/projects/{id}/design-study/candidates/{candidate_id}/evaluate`, or refreshed
automatically by ranking when candidate-local evidence exists. This reads only artifacts under
`candidates/<id>/` — neutral/static metrics, optional `field_regions` / `cae_result_map`,
geometry execution manifest, and assembly/proxy evidence — then writes
`candidates/<id>/analysis/evaluation.json` plus
`candidates/<id>/diagnostics/evaluation_report.json`. The evaluator normalizes mass, volume,
max stress, max deflection, minimum safety factor, and optional compliance/stiffness proxies;
keeps units, load-case ids, and source paths; uses worst-case stress/deflection and lowest
safety factor across load cases, and surfaces which case controlled each metric in a
first-class `load_case_summary` (per-metric `controlling_load_case_id` + `load_cases_considered`,
mirrored as `controlling_load_cases` in the report) — a metric absent from every load case stays
`unknown` and is never fabricated; and marks proxy assembly evidence lower confidence with
`contact_physics_modeled:false` and `bolt_preload_modeled:false`
honesty. It never runs a solver, never recompiles geometry, never mutates baseline artifacts,
and never promotes a candidate.

Candidate proposal hints can be **explicitly generated** (PR6) via
`POST /api/projects/{id}/design-study/hints`. This reads the design-study variables,
candidate evaluations/ranking/scoring diagnostics, optional CAE/topopt maps, and assembly
recommendations, then writes `analysis/design_study_candidate_hints.json` plus
`diagnostics/design_study_candidate_hints_report.json`. Hints are structured and
machine-readable (`adjust_parameter`, `protect_parameter`, `rerun_evaluation`,
`request_user_input`, `stop_no_safe_hint`) with `variable_id`, direction, magnitude,
priority, confidence, evidence links, and safety notes. The hint layer is advisory only:
it never creates candidate patches, never runs optimization/search, never executes
candidates, never runs CAE, never ranks or accepts candidates, and never mutates geometry
or baseline artifacts. Low-confidence/proxy evidence leads to conservative hints and
explicit `contact_physics_modeled:false` / `bolt_preload_modeled:false` honesty notes.

**Executed candidates can be ranked** (PR3) via `POST /api/projects/{id}/design-study/rank`.
This reads the iteration history and per-candidate evaluation artifacts (building/refreshed from
candidate-local evidence when safe), classifies each candidate
as `feasible` / `infeasible` / `unknown` / `failed`, scores them against the problem objective
and constraints, and writes `analysis/design_study_candidate_ranking.json` +
`diagnostics/design_study_scoring_report.json`. Ranking is **advisory only** — it does not
search or propose new candidates, does not recompile geometry, does not run CAE, does not
promote any candidate to the baseline, and missing metrics honestly produce
`needs_more_evaluation` / low-confidence outcomes. The best candidate is selected only when
it is feasible, improves the objective, and has high-confidence metrics; otherwise
`best_candidate_id` is `null` and `safe_to_accept` is `false`.

**A ranked candidate can be explicitly accepted** (PR4) via
`POST /api/projects/{id}/design-study/candidates/{candidate_id}/accept`. This copies the
candidate's derived workspace into `accepted/<candidate_id>/` (patch, derived Shape IR,
evaluation, and acceptance provenance) and writes `analysis/design_study_acceptance.json` +
`diagnostics/design_study_acceptance_report.json`. Acceptance is **explicit and gated**:
- The candidate must be the `best_candidate_id` (or `override_unsafe` must be explicitly set).
- The candidate must be `feasible`; `failed` / `infeasible` / `unknown` candidates are rejected.
- The candidate workspace artifacts must exist.
- **Baseline geometry is never overwritten.** The accepted candidate is a derived design artifact
  only; production approval is **not** claimed.

**Candidate CAE evaluation request** (explicit, candidate-local) via
`POST /api/projects/{id}/design-study/candidates/{candidate_id}/cae-evaluate`. Derives
a candidate-local CAE setup from the baseline, normalizes existing candidate-local
neutral metrics into `candidates/<candidate_id>/analysis/evaluation.json`, and optionally
refreshes ranking. Solver execution is disabled by default and best-effort when enabled.
Baseline CAE artifacts are never overwritten.

**Canonical demo + regression** (`aieng-ui/backend/tests/test_design_study_demo.py`) exercises
the full PR1–PR5 pipeline end-to-end using deterministic static/neutral metrics (no external solver):
- Fixture: `aieng-ui/backend/tests/fixtures/design_study_demo/` — bracket-like baseline Shape IR,
  4 variables (wall_thickness, rib_thickness, fillet_radius, bolt_dia), 5 candidates:
  - `candidate_good` — valid, improves volume, within constraints
  - `candidate_bad_bounds` — rejected (out of bounds)
  - `candidate_protected` — rejected (protected variable)
  - `candidate_unknown` — valid but no metrics → `unknown`
  - `candidate_infeasible` — valid but stress violation → `infeasible`
- Full-flow test: validate → execute all 5 → inject static evaluations → rank → accept best.
- Hints path: explicit hint generation produces protected-variable, stress/safety, and
  rerun-evaluation hints without creating patches or modifying baseline geometry.
- Unsafe-data test: only bad candidates → ranking says no viable candidate → acceptance blocked.
- Missing-ranking test: acceptance without prior ranking → `needs_user_input`.

**Design-history branching + governed promotion (v0).** `design_study_promotion`
makes lineage first-class: `record_design_branch` records an explicit branch
(parent + provenance) for an **accepted** candidate; `promote_design_branch`
performs an **approval-gated** promotion (requires `approval=true` + an existing
branch) that moves the governed `current_baseline` pointer and records
who/why/what changed; `rollback_baseline_promotion` restores the previous
baseline. All lineage lives in `analysis/design_history.json`. Governance only —
it never overwrites baseline `.aieng` geometry (`baseline_geometry_overwritten:
false`); acceptance stays advisory until an approved promotion, and **promotion
is not certification**.

**Surrogate-assisted proposal (v0, advisory).** `optimization_surrogate` fits a
**deterministic** numpy-only Gaussian-process surrogate (RBF kernel, fixed
hyperparameters — no scikit-optimize dependency) over **evaluated** candidate
metrics and proposes new candidate patches by an upper-confidence-bound
acquisition → `analysis/design_study_surrogate_proposals.json` + proposed
`patches/design_candidates/surrogate_*.json` (valid, `applied:false`). Each
prediction carries an explicit `uncertainty_std` and is marked **advisory /
`is_solver_evidence:false`** — predictions guide search only and are never imported
as solver/verification evidence. It degrades honestly (`needs_more_evidence` /
`no_safe_variables`) on sparse/missing evidence, runs no solver, accepts nothing,
and never mutates baseline geometry.

Future work: optimizer/search loop, multi-objective Pareto ranking, richer candidate CAE evidence ingestion,
physical baseline-geometry swap on promotion, and multi-branch merge.

**Related docs:**
- [`aieng/docs/demo_catalog.md`](aieng/docs/demo_catalog.md) — canonical demos and regression flows
- [`aieng/docs/showcase_gallery.md`](aieng/docs/showcase_gallery.md) — showcase with demo talking points
- [`aieng/docs/showcase_gallery.json`](aieng/docs/showcase_gallery.json) — machine-readable gallery manifest
- [`aieng/docs/backend_capability_matrix.md`](aieng/docs/backend_capability_matrix.md) — capability status snapshot

A lightweight backend stability gate checks that canonical demos, artifact names, and honesty boundaries stay in sync:
```bash
pytest aieng/tests/test_backend_stability_gate.py -q
```
This is a consistency smoke test, not a production certification suite.

### Assembly IR v0 (optional, multi-part)

A package MAY carry `assembly/assembly_ir.json` — a backend representation of a **multi-part
assembly**: parts (+ roles / placements / materials), interfaces, and **simplified connections**
(`rigid_tie` / `bonded` / `bolted_proxy` / `welded_proxy` / `contact_proxy` / `spring_proxy`).
Connections are **PROXIES, not full nonlinear contact** — there is no bolt preload and no real
contact physics. When present, the backend best-effort writes
`diagnostics/assembly_validation.json`, `assembly/part_registry.json`,
`assembly/connection_graph.json`, and a solver-neutral `simulation/assembly_cae_setup_draft.json`
(auto on recompile, or via `POST /api/projects/{id}/assembly/process`). Schema:
`aieng/schemas/assembly_ir.schema.json`. Single-part packages are unaffected.

**Authoring the IR (no approval).** An agent builds the IR incrementally from a
package's named CAD parts (these tools mutate only assembly metadata, no
geometry, no approval):
- `cad.define_part { project_id, geometry_ref, role }` — adds a part and links it
  to a named solid (verifying the ref against the topology and reporting
  `geometry_ref_known` true/false/null — never fabricated).
- `cad.define_interface { project_id, part_id, semantic_role, face_ids }` —
  binds a part to specific B-Rep faces (`@face:*` from the brep graph /
  `agent_context`), verified against the model topology (`face_ids_known`). This
  is what makes a mate *geometric*.
- `cad.define_mate { project_id, connection_type, part_a, part_b, interface_a,
  interface_b }` — adds a connection between two **already-defined** parts (a mate
  to an undefined part is refused; proxy connections always carry honest
  `limitations`). When it references interfaces, the response includes the
  resolved `connection_geometry` verdict (`plausible` / `warning` / `invalid` /
  `insufficient_data`). Optionally add a **`mate_predicate`** (`concentric`
  shaft-in-bore / `tangent` gear-mesh / `coincident` faces-flush / `clearance`)
  with `mate_tolerance_mm` (+ `expected_clearance_mm` for `clearance`) to VERIFY
  the engineering relationship against the resolved geometry — a violated
  predicate marks the connection `invalid` (e.g. gear pitch circles that don't
  meet). Needs cylindrical `@face` interfaces for concentric/tangent.

Each call re-validates and refreshes the derived registry / connection graph /
CAE draft; with interfaces present it also resolves interface geometry and
validates connection geometry (the same two-step as `POST /assembly/process`).
Authoring stays inside the v0 honesty contract: representation + validation only
— no contact physics, no bolt preload, no solver.

When per-part / package topology maps are available, the same call also **resolves interfaces
and validates connection geometry** (geometry-validation only — still no contact/preload/solver):
it resolves each interface's `topology_refs` to bbox/centroid/normal/area, applies the part
transform into world coordinates (`assembly/interface_resolution.json`), and judges each
connection's plausibility from centroid distance / bbox overlap / normal alignment / semantic-role
fit → `geometry_status` ∈ plausible / warning / invalid / insufficient_data
(`diagnostics/assembly_connection_geometry.json`). Invalid connections are marked `disabled` +
`needs_user_input` in the CAE setup draft. Unresolved refs are reported honestly, never invented.

The same pass also writes **pre-solver interface-NSET quality diagnostics**
(`diagnostics/assembly_mesh_interface_diagnostics.json`): per interface it flags
`empty_interface` (resolves to no usable faces → empty node set, **blocking**),
`partial_resolution`, `sparse_interface` (undersampled), `disconnected_interface`
(faces form >1 region), and `over_broad_interface` (spans the part), each with
actionable remesh/re-pick guidance. `safe_for_solver` is false when any interface
is blocking, and empty interfaces add a `needs_user_input` entry to the CAE draft
(same gate as invalid connections). It is a geometry-coverage proxy — it meshes
nothing, runs no solver, and is not a mesh-convergence guarantee.

Assembly CAE v0 then produces a **solver-neutral simplified proxy model**:
`simulation/assembly_cae_model.json` plus
`diagnostics/assembly_cae_model_diagnostics.json`. Solver deck generation is optional and
best-effort: `simulation/assembly_calculix.inp` is written only when enabled simplified
connections and actual per-part mesh refs exist; otherwise
`diagnostics/assembly_solver_deck_generation.json` records `skipped`. Solver execution is
also optional; v0 normalizes generic/fake assembly results when provided, otherwise writes
`diagnostics/assembly_solver_execution.json` with `solver_executed:false`. Assembly results map
to parts/interfaces/connections/source_ir_node with confidence in
`analysis/assembly_result_map.json` and `diagnostics/assembly_result_mapping.json`.

**Bolt preload (contract + honest report).** A bolted connection MAY carry an
explicit `preload` block (`axial_force_n`, optional `method`/`fastener_id`) in the
Assembly IR; it is **never inferred** from a bolt designation or BOM/standard-part
entry. `diagnostics/assembly_bolt_preload.json` records, per bolted connection,
the preload intent + whether it is actually modeled, linked to connection /
interface / fastener IDs. In v0 the simplified proxy deck cannot apply pretension
(no solid bolt geometry / `*PRE-TENSION SECTION`), so intents are reported
`unsupported` and `bolt_preload_modeled` stays **false** — it flips true only when
a connection's preload is actually represented in a generated deck, never from
intent alone. No fatigue / loosening / torque-to-preload claim is implied.

Assembly-aware topology optimization v0 is **explicit execution only**:
setup writes `analysis/assembly_topopt_problem.json`,
`diagnostics/assembly_topopt_derivation.json`, and, when supports+loads are safe,
`analysis/topology_optimization_problem.json`. A separate explicit backend helper
`run_assembly_topology_optimization(package_path, ...)` — exposed through
`opt.run_assembly_topology_optimization` and
`POST /api/projects/{project_id}/assembly/topology-optimization/run` — consumes
those artifacts, calls the existing single-part SIMP optimizer, and writes:
- `analysis/assembly_topology_optimization.json`
- `diagnostics/assembly_topopt_execution.json`
- `diagnostics/assembly_post_optimization_verification.json`
- `analysis/assembly_optimization_summary.json`
- `analysis/assembly_design_recommendations.json`
- `diagnostics/assembly_postprocess_report.json`
- `analysis/assembly_next_actions.json`
- `parts/<selected_part_id>/analysis/topology_optimization.json`
- `parts/<selected_part_id>/geometry/optimized_shape_ir.json` when writeback is safe

This optimizes **one selected `design_part` only**. Reference, fixture, fastener,
load-source, frozen, and non-editable parts are rejected. Mounting/bolt/weld/contact/
mating connector regions are passed through as preserve masks when their grid cells
are known; unmapped preserve regions are warned, never silently ignored. Writeback
creates a selected-part derived artifact and does **not** overwrite package-level
geometry or reference parts. Post-optimization verification checks that only the
selected part got derived artifacts, that preserve interfaces stay traceable (or
warn honestly when they do not), and that proxy/contact/preload limitations are
still explicit. It does **not** certify physical interface equivalence.
After verification, a best-effort rule-based postprocess pass writes structured
assembly design recommendations and a postprocess report. These are advisory
only: they do not rerun topopt automatically, do not mutate geometry, and do
not certify downstream export/reconstruction safety beyond the same proxy-model
honesty boundaries.

Canonical backend regression/demo fixture: `aieng-ui/backend/tests/fixtures/assembly_topopt_demo/`
plus `aieng-ui/backend/tests/test_assembly_topopt_demo.py`. It exercises the full
backend-only loop on a deterministic proxy-based assembly:
`/assembly/process` → `write_assembly_topopt_problem(...)` →
`/assembly/topology-optimization/run` → post-optimization verification + recommendation/report writeback, and also pins the unsafe-data
`needs_user_input` path where no standard problem is emitted and no geometry is
overwritten. Run it with:
`pytest aieng-ui/backend/tests/test_assembly_topopt_demo.py -q`

All outputs keep `production_ready:false`, `contact_physics_modeled:false`, and
`bolt_preload_modeled:false`. Future work: real nonlinear contact modeling, bolt preload,
assembly meshing improvements, and simultaneous multi-part topology/size optimization.

**Advisory multi-part topopt problem (v0).** `write_multipart_topopt_problem(package_path,
selected_part_ids=[...])` (and the pure `derive_multipart_topopt_problem`) derive a
**reviewable** multi-part topology/size problem for **explicitly selected** design parts →
`analysis/assembly_multipart_topopt_problem.json` + `diagnostics/assembly_multipart_topopt_derivation.json`.
It preserves frozen/reference/fastener/load-source/fixture parts (marked non-design), derives
per-part design spaces + topology/sizing variables, and records coupled connections + a recorded
(not executed) objective. It **refuses honestly** (`status:needs_user_input`) on no selection,
unknown/non-optimizable/ambiguous (design-and-frozen) parts, or a design-design coupling whose
interface constraints are missing/unresolved. Advisory only: **no optimizer execution, no
auto-acceptance, no baseline promotion** (`optimizer_executed:false`, `baseline_modified:false`,
`production_grade_simultaneous_optimization:false`).

---

## Fallback mode — when you do not have MCP tools

Some agent clients (notably **Kimi Code CLI** in its default configuration) do not
automatically load user-defined MCP servers. If `aieng.list_projects` is not in
your tool list, follow this fallback path.

### Environment topology (know where things are)

| Service | Address | Purpose |
|---------|---------|---------|
| React UI | `http://localhost:5173` | The workbench front-end |
| FastAPI backend | `http://localhost:8000` | API + MCP bridge + static assets |
| Platform data | `aieng-ui/data/` | Projects, runtime config, logs |
| Projects root | `aieng-ui/data/projects/` | One folder per project |
| Conda env | `aieng311` | Where build123d / OCP live |

### Running build123d without MCP

Use the provided runner so exports are handled exactly like the backend does
(including `binary=True` for GLB):

```bash
conda activate aieng311
cd aieng-ui/backend/scripts
python agent_build123d_runner.py my_model.py --out-dir ./output
```

Output files:
- `output/result.step` — AP214 STEP
- `output/result.stl` — binary STL
- `output/result.glb` — **binary** GLB (if export succeeded)
- `output/topology.json` — face / solid entities with labels

### Registering the model in the UI without MCP

After you have a STEP file (and optional preview GLB/STL), import it as a proper
project so the React UI can display it:

```bash
conda activate aieng311
cd aieng-ui/backend/scripts
python agent_import_project.py ../../output/result.step \
    --name "My Model" \
    --preview ../../output/result.stl \
    --project-id my_model_001
```

This atomically:
1. Creates the `.aieng` package.
2. Runs topology + feature-graph enrichment.
3. Creates the project directory + `metadata.json`.
4. Copies the preview into `viewer/`.
5. Updates the project status to `viewer_ready_*`.

Refresh the UI (`http://localhost:5173`) and the project will appear.

### Kimi Code CLI specific notes

Kimi Code CLI does **not** read `.mcp.json` automatically (unlike Claude Code).
To give Kimi the workbench MCP tools, you must either:
- Use Kimi's settings UI to add the MCP server defined in `.mcp.json`.
- Or accept fallback mode and use the scripts above.

### Direct REST API (last resort)

If you need to trigger backend actions without MCP, the backend exposes standard
HTTP endpoints. The most useful ones for agents:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | Backend status, tool count |
| `/api/projects` | GET | List projects |
| `/api/projects` | POST | Create empty project |
| `/api/projects/{id}/upload` | POST | Upload STEP or `.aieng` |
| `/api/projects/{id}/cad-preview` | GET | Stream GLB/STL preview |
| `/api/projects/{id}/agent-context` | GET | Full geometry + CAE context |
| `/api/agent/invoke-tool` | POST | Run any MCP tool by name (emits UI events) |

Example — invoke a tool directly:
```bash
curl -X POST http://localhost:8000/api/agent/invoke-tool \
  -H "Content-Type: application/json" \
  -d '{"tool": "cad.execute_build123d", "input": {"project_id": "...", "code": "..."}}'
```

---

## Common mistakes to avoid

| Mistake | Correct approach |
|---------|-----------------|
| Reading `aieng/src/` to learn capabilities | Call `aieng.agent_readme`; real engine is `aieng-ui/backend` |
| Running code to diagnose the backend | Use MCP tools; if backend down, ask user to start it |
| Including `export_step(...)` in build123d code | Omit exports — the runner adds them |
| `result.export_step(path)` (build123d <0.9 API) | Use `export_step(result, path)`, or just omit |
| `cae.run_solver` without preflight | Call `cae.prepare_solver_run` first |
| Referencing stale artifacts after an edit | `aieng.refresh_semantics` then regenerate |
| Raw face indices instead of `@face:id` | Use pointer IDs from `aieng.agent_context` |
| Judging geometry from one view (iso) only | Inspect all 4 views in the contact sheet (front/side/top/iso) — alignment errors hide in iso |
| Monochrome parts → can't tell which is which | Set `.color = Color(r,g,b)` on each labelled part |
| Building straight to finish without review | After each step, list 3–5 fail-first objections by view + part, then decide next iteration |
| Stacking `Box(...)` for a character/vehicle/product | Switch to Industrial Design Mode — use `loft`/`sweep`/`revolve` + aggressive `fillet` for visible exterior forms |

---

## Environment variables (for MCP server operators)

| Variable | Purpose |
|----------|---------|
| `AIENG_PLATFORM_DATA` | Override the data directory (default `aieng-ui/data`) |
| `AIENG_BACKEND_URL` | When set, forward tool calls to the running backend for live UI |
| `AIENG_MCP_MANAGED_APPROVAL` | Set to `1` to route approval-gated external MCP calls through the workbench backend approval card; unavailable approval fails safe. When no approval surface (viewer) is connected, gated calls **fail fast** with `code: approval_surface_unavailable` instead of blocking to the approval timeout |
| `AIENG_MCP_APPROVAL_MODE` | Set to `elicit` (or run with `--approval-mode elicit`) for **headless approval**: gated mutations are approved via **MCP client elicitation** — the server asks the connecting CLI/IDE agent to prompt the human, so **no workbench viewer is required**. If the client does not support elicitation there is no surface, and the gated tool **fails safe** (`behavior: deny`, `code: approval_surface_unavailable`) — it never runs silently. Broker modes (`AIENG_MCP_MANAGED_APPROVAL` / agentic) take precedence when both are set |
| `AIENG_MCP_BLOCK_APPROVAL_TOOLS` | Set to `1` for inspection-only mode: hard-block **all mutating tools** at the server level — not just approval-gated ones, but also the plan-boundary CAD authoring/edit tools (`cad.execute_build123d`/`edit_parameter`/`replace_part`/`remove_part`/`refine`/`set_reference_image`). Read-only inspection tools still run |
| `AIENG_MCP_REQUIRE_GUIDES` | Require the relevant `aieng.guide` topic before CAD, CAE, or package-lifecycle tools (default `1`; set `0` to disable) |
| `AIENG_AGENTIC_PERMISSION_TOOL` | Set to `1` only for an agentic session driver that uses the backend approval broker |
| `AIENG_AGENTIC_APPROVAL_TIMEOUT_SECONDS` | Maximum seconds to wait for a workbench approval decision (default `900`) |
| `AIENG_CAD_MAX_MEMORY_MB` | POSIX-only address-space (`RLIMIT_AS`) cap for the CAD execution subprocess (default `4096`; `0` disables). No-op on Windows. See `aieng-ui/backend/docs/cad_execution_boundary.md` |
| `AIENG_CAD_MAX_CPU_SECONDS` | POSIX-only CPU-time (`RLIMIT_CPU`) cap for the CAD execution subprocess (default = build timeout + 30s; `0` disables). Hard backstop behind the wall-clock timeout |
| `AIENG_CAD_MAX_FILE_MB` | POSIX-only single-file write-size (`RLIMIT_FSIZE`) cap for the CAD execution subprocess (default `512`; `0` disables) |

Full wiring (Claude Code / Copilot / Codex): `aieng-ui/backend/MCP_SETUP.md`.
CAD execution boundary, threat model, and deployment hardening: `aieng-ui/backend/docs/cad_execution_boundary.md`.
