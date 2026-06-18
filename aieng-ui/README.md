# aieng-ui

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)
[![AIENG Demo Health](https://github.com/armpro24-blip/aieng-ui/actions/workflows/demo-health.yml/badge.svg)](https://github.com/armpro24-blip/aieng-ui/actions/workflows/demo-health.yml)

Web workbench and FastAPI service for the `.aieng` engineering platform.

**Keywords:** CAD · CAE · FEA · workbench · FastAPI · React · Three.js · LLM agent · MCP · build123d · OpenCASCADE · CalculiX · approval gate · audit · evidence review · design automation

## What This Is

`aieng-ui` provides:

`aieng-ui` is the reference runtime/workbench; package semantics are delegated to the `aieng` core repo, which remains the semantic source of truth.

- **FastAPI service layer** — project/file management, preview generation, semantic package inspection, CAE artifact detection (`GET /api/projects/{project_id}/cae-artifacts`)
- **React SPA** — STEP upload, Three.js viewer (GLB/STL), semantic summary panel, honest CAE lifecycle panel (setup / simulation runs / results) with one-click refresh and external metrics import, CAE Review Report Assistant, Closed-loop Copilot Stepper, artifact inspector (read-only JSON/text evidence review), chat/orchestration panel, audit log, settings drawer, in-viewer result-field coloring (legend + field picker)
- **Local orchestration runtime** — `RunRecord`, `ToolCall`, `ToolResult`, `RuntimeEvent` types; intent-based plan builder; synchronous executor with approval gate
- **CAD provider registry** — pluggable `CadProvider` interface; build123d/OCP is the default provider, with FreeCAD kept as an optional external adapter
- **Shape IR conversion (multi-target)** — `.shape.json` / `.shape_ir.json` sources convert into `.aieng` and compile by `representation` through a pluggable compiler registry: `brep_build123d` (default) → build123d STEP/B-Rep; `nurbs_brep` → OCP NURBS B-Rep surfaces (exact per-patch `bspline` faces); `implicit_sdf` → fogleman/sdf marching-cubes mesh; `manifold_mesh` → manifold3d CSG mesh. Shape IR is the source; STEP/STL/GLB are derived products recorded at different evidence levels in `provenance/conversion_manifest.json`
- **Conservative mesh-to-CAD reconstruction:** mesh outputs can produce analytic face candidates, OCC sewing diagnostics, and (only for validated closed OCC solids) `geometry/reconstructed.step` plus roundtrip verification. Partial shells and invalid solids write diagnostics but do **not** export STEP; stale reconstructed artifacts are removed on failed reruns. Reconstructed CAD is mesh-derived/lossy, does not overwrite source/generated STEP, preserves original mesh topology at `geometry/mesh_topology_map.json`, and is not production certified.
- **Assembly CAE + topopt v0** — Assembly IR packages can produce `simulation/assembly_cae_model.json`, simplified proxy connection diagnostics, optional `simulation/assembly_calculix.inp` only when mesh prerequisites exist, best-effort/skipped solver execution diagnostics, and `analysis/assembly_result_map.json`. Backend-only assembly-aware topopt setup derives a selected design-part problem and preserve/guidance evidence; explicit execution (`opt.run_assembly_topology_optimization` / `POST /assembly/topology-optimization/run`) runs the existing optimizer and writes selected-part derived artifacts without overwriting reference parts. A best-effort post-optimization verifier then writes `diagnostics/assembly_post_optimization_verification.json` and `analysis/assembly_optimization_summary.json` to check selected-part-only writeback, preserve-region traceability, and proxy-honesty fields. A follow-on rule-based postprocess pass writes `analysis/assembly_design_recommendations.json`, `diagnostics/assembly_postprocess_report.json`, and `analysis/assembly_next_actions.json`; these recommendations are advisory only and never rerun topopt or mutate geometry automatically. Connections remain simplified proxies; no nonlinear contact, bolt preload, or multi-part optimization is claimed.
- **B-Rep graph engine** — symbolic face/edge/group pointer index (`@face:`, `@edge:`, `@group:`) derived from topology maps; deterministic, CAD-neutral, read-only
- **Engineering action planner** — typed intent classification for chat-first CAD/CAE workflows (generate, refine, preprocess, simulate, change-material, refine-mesh, set-target)
- **Simulation runner** — external mesh handoff / solver input generation → CalculiX solve → FRD parse → atomic write-back, with SSE streaming progress and post-processing verdict vs. design targets
- **Stress heatmap generator** — per-node Von Mises stress colormap as binary GLB from CalculiX FRD results
- **Contextual engineering chat** — Claude-powered chat grounded in live project state (geometry, FEA setup, simulation results, design targets)
- **Local Agent Autopilot quality gate** ? external agents return one JSON action at a time; CAD actions use a compact brief gate, semantic labels/colors, approval-gated build123d execution, and automatic read-only `cad.critique` follow-up where registered.
- **Live project sync** ? the backend publishes `project_changed` / `viewer_asset_changed` events through `/api/agent-activity/stream`; the React workbench refreshes project metadata and viewer assets automatically, with a visible Live/Polling/Reconnecting status and polling fallback.
- **Product-mode UI shell** ? the right rail defaults to `Build`, `Files`, and `Advanced`; technical model metrics and agent run internals remain available under details panels instead of dominating the first screen.

## Role in the vertical CAE MVP

`aieng-ui` is the **workbench**: the local FastAPI runtime + React SPA where the vertical CAE MVP actually executes. It owns the moving parts that `aieng` deliberately does not:

- The runtime tool registry (table below) and the `POST /api/runtime/runs` orchestration entry point.
- The **approval gate** — `cae.run_solver`, `cad.execute_build123d`, `cad.edit_parameter`, and other package mutations are `requires_approval=True`; the runtime pauses before mutation or subprocess execution and exposes explicit `approve`/`reject` REST endpoints.
- The **external CalculiX subprocess adapter** — `subprocess.run([ccx, …], shell=False)` with timeout, captured stdout/stderr/return code, and honest `converged: null` semantics. AIENG does not host a solver.
- **Artifact write-back** into the `.aieng` package (atomic ZIP rewrite via temp file + `shutil.move`).
- The **audit/event timeline** (`RuntimeEvent` sequence).
- The schema-version drift warning surfaced through the `aieng_bridge` to the chat panel.

External agents (Claude Code, Codex, MCP clients) reach the workbench through the MCP server in `backend/app/mcp_server.py`. For the reproducible end-to-end demo see [`docs/quickstart-vertical-cae-demo.md`](docs/quickstart-vertical-cae-demo.md).

When `AIENG_BACKEND_URL` points MCP calls at the running FastAPI backend, external-agent mutations emit live UI events. The viewer refreshes on CAD preview changes without a manual browser reload; if the SSE stream drops, the UI shows `Polling` / `Reconnecting` and refreshes the active project periodically until the stream recovers.

For the step-by-step evidence-grounded CAD/CAE Copilot loop, see
[`docs/closed-loop-copilot-stepper.md`](docs/closed-loop-copilot-stepper.md).
For the issue #10 v0.26 demo acceptance path, see
[`docs/copilot-loop-v0.26-demo-walkthrough.md`](docs/copilot-loop-v0.26-demo-walkthrough.md).

40 registered runtime tools (mutation / expensive operations are approval-gated).
Honest status semantics: `skipped`, `partial`, `error`, and `completed` are never
conflated. Key tools listed below; full registry available via `GET /api/runtime/tools`.

| Tool | Status |
|------|--------|
| `aieng.inspect_package` | Working |
| `aieng.find_projects_by_part` | Working — locate a project by a part label (case-insensitive substring on `named_parts`) |
| `aieng.delete_project` | Working — permanently delete a project (directory + chat sessions/messages). Approval-gated. |
| `aieng.refresh_semantics` | Working |
| `aieng.generate_preview` | Working |
| `aieng.convert` | Working — imports STEP/STP/FCStd/Shape IR. Shape IR compiles by `representation` (pluggable registry): `brep_build123d` → build123d STEP/B-Rep; `nurbs_brep` → OCP NURBS B-Rep (per-patch `bspline` faces); `implicit_sdf` → SDF marching-cubes mesh; `manifold_mesh` → manifold3d CSG mesh. B-Rep reps → analytic faces; mesh reps → region-level. Publishes a viewer preview |
| `aieng.read_audit_log` | Working |
| `cad.execute_build123d` | Working — caller-supplied build123d Python code → STEP/STL/GLB. Returns a 2×2 multi-view contact sheet thumbnail (front/side/top/iso) with per-part colors honoured, plus a deterministic `geometry_report` (overall proportions, per-part size ratios, left/right symmetry residuals, floating-part gaps) for numeric self-review. Approval-gated. |
| `cad.get_source` | Working — read accumulated build123d source + named_parts + has_base for incremental editing |
| `cad.set_reference_image` | Working — attach a reference photo/drawing to a project so future thumbnails tile it side-by-side for proportion calibration |
| `cad.critique` | Working — read-only deterministic engineering audit (min wall thickness, standard hole sizes, floating components) based on the canonical labels from `aieng/schemas/feature_graph.schema.json` |
| `cad.refine` | Working — LLM-assisted refinement of an existing build123d model from natural-language feedback. Approval-gated. |
| `cad.edit_parameter` | Working — fast build123d parametric edit: replaces a named UPPER_SNAKE_CASE constant in `geometry/source.py` and re-executes (no LLM). Validated against the parameter's declared `min`/`max`; on build failure the prior geometry is preserved. Approval-gated. |
| `postprocess.generate_computed_metrics` | Working — normalizes external metrics into `computed_metrics.json` and writes it back into the `.aieng` package |
| `postprocess.refresh_cae_summary` | Working — regenerates CAE result summary, evidence index, and markdown |
| `mcp.check` | Working — checks MCP guardrails, capability gaps, operation policy |
| `mcp.parse_patch` | Working — parses an `.aieng` patch proposal without executing |
| `mcp.prepare_execution` | Working — dry-run `.aieng` patch proposal; returns preflight side effects |
| `cae.apply_setup_patch` | Working — controlled patches to CAE setup artifacts |
| `cae.extract_solver_results` | Working — parses CalculiX FRD and writes `computed_metrics.json` |
| `cae.prepare_solver_run` | Working — preflight inspection, no solver execution |
| `cae.generate_solver_input` | Working — generate a CalculiX input deck from package setup artifacts |
| `cae.run_solver` | Working — external CalculiX execution adapter MVP, approval-gated |
| `cae.write_mesh_handoff` | Working — write a mesh handoff contract for an external mesher |
| `cae.import_solver_evidence` | Working — import external solver evidence into the `.aieng` package |

External agents (Claude Code, Codex, custom MCP clients) can access all runtime tools via `python -m app.mcp_server` from the backend. See [`backend/MCP_SETUP.md`](backend/MCP_SETUP.md).

## Local agent reliability and diagnostics

Local Agent Autopilot run reads (`GET /api/agent/autopilot/runs/{run_id}`) include
backward-compatible recovery metadata:

- `stale: boolean`
- `recovery_state: "active" | "needs_resume" | "waiting" | "terminal"`
- `stale_reason` when a persisted `status="running"` run has no live backend
  worker and has exceeded the conservative stale threshold.

`awaiting_approval`, `blocked`, and `chatting` are **waiting** states, not stale
failures. `completed`, `failed`, and `cancelled` are terminal. The backend does
not silently rewrite stale running runs to failed; clients may show a recovery
message and can safely call `POST /api/agent/autopilot/runs/{run_id}/cancel` to
clean up a stale run even after a backend restart. A cancel request emits one
public `run_cancelled` terminal event and terminal runs are no-ops on repeat
cancel.

Autopilot transcript events now carry advisory, backward-compatible metadata on
both the top-level event object and the stored payload:

- `category`: `status`, `progress`, `terminal`, `tool`, `approval`,
  `user_input`, `artifact`, or `diagnostic`
- `visibility`: `public` or `diagnostic`
- `user_visible`: boolean

Existing consumers can continue to switch on `type` / `status`. Newer UI code
should prefer `user_visible=false` for diagnostic rows (for example
`agent_phase_changed`) and show at most one public progress row when a
same-phase `run_status_changed` also exists. Public terminal events are
reserved for `run_status_changed` with `completed` / `failed` / `cancelled` and
`run_cancelled`; tool failures remain `category="tool"` unless a separate run
terminal status event is emitted. Waiting states (`awaiting_approval`,
`blocked`, `chatting`) are not terminal.

Local CLI readiness is available at:

- `GET /api/local-agents/capabilities` — legacy/backward-compatible capability
  summary for UI selectors.
- `GET /api/local-agents/preflight`
- `GET /api/local-agents/preflight?adapter=claude-code`

The preflight endpoint classifies adapters as `ready`, `missing_binary`,
`auth_error`, `timeout`, `session_not_found`, `unsupported_flag`, or
`unknown_error`, includes feature flags (`json_output`, `schema_output`,
`session_resume`, `tool_disable`, `non_interactive`), and returns safe diagnostics
such as resolved executable path, cwd, platform, PATH summary, and environment
variable **names**. It must not return token/API-key values.

Claude Code adapter notes:

- `--bare` is not used by default because Claude Code 2.1.x on Windows can then
  skip normal auth/keychain state and report `Not logged in`; set
  `AIENG_CLAUDE_CODE_BARE=1` only for explicit diagnostics.
- Plain CLI preflight runs `claude -p "Say hello" --output-format json` with a
  default timeout of 20 seconds; override with
  `AIENG_CLAUDE_PREFLIGHT_TIMEOUT_SECONDS`.
- Autopilot runs without a chat `session_id` derive a stable run-based Claude
  session id, so approval/continue steps reuse the same CLI conversation.
- `session_not_found` means the CLI could not resume the requested conversation;
  start a fresh run or inspect adapter diagnostics.

## Evidence review API

Read-only endpoints for human review of artifacts inside a project's `.aieng`
package. These do NOT execute solvers, mutate packages, or advance claims —
they exist so a reviewer (or agent) can inspect what the runtime wrote.

| Endpoint | Purpose |
|---|---|
| `GET /api/projects/{project_id}/cae-review-report` | Generate a read-only CAE review report from existing lifecycle summaries: setup readiness, missing information, stale evidence, result metrics, design-target comparisons, and claim boundaries. Does not execute solvers, mutate packages, or advance claims. |
| `GET /api/projects/{project_id}/artifact?path=...` | Read a single artifact from the project's `.aieng` package. Returns `{path, exists, media_type, size_bytes?, parsed_json?, text?, warnings}`. JSON files are parsed when ≤ 2 MB; text files are inlined when ≤ 256 KB. Missing artifacts return `exists: false` with 200. Path traversal, absolute paths, and backslashes are rejected with 400. |
| `POST /api/projects/{project_id}/artifact/diff` | Compute RFC-6901 JSON Pointer paths for differences between two JSON values supplied in the body as `{before, after}`. Returns `{changed_paths, added_paths, removed_paths}`. Pure computation; no package access. |
| `POST /api/projects/{project_id}/solver-input` | Import a CalculiX `.inp` solver input deck into the package. Body: `{text, run_id?, overwrite?}`. Writes to `simulation/runs/{run_id}/solver_input.inp` (default `run_id` `"run_001"`). Minimal CalculiX keyword scan rejects obvious non-decks; missing `*NODE` / `*STEP` blocks are accepted with warnings. Import only — no mesh generation, no deck generation, no physical correctness validation. 10 MB cap. |
| `POST /api/llm/test` | Test LLM provider configuration (`config_ready`) and optionally verify real API connectivity (`connection_verified`). Body: `{llm_config, verify_connection?}`. Never returns the API key. |
| `POST /api/projects/{project_id}/engineering-action-plan` | Typed, read-only action candidate for a chat prompt. Classifies intent (generate, refine, preprocess, simulate, change-material, refine-mesh, set-target) and returns confidence, extracted inputs, and execution policy. Does not execute tools or mutate the package. |
| `POST /api/projects/{project_id}/brep-graph/build` | Build symbolic B-Rep graph, entity pointer index, and markdown digest from `geometry/topology_map.json`. Writes `graph/brep_graph.json`, `graph/entity_index.json`, and `ai/brep_digest.md` into the package atomically. No CAD kernel or LLM involved. |
| `GET /api/projects/{project_id}/brep-graph` | Read existing B-Rep graph artifacts from the project package. |
| `POST /api/projects/{project_id}/run-simulation` | Mesh with Gmsh + solve with CalculiX from AI preprocessing output. Requires `confirmed=true` (approval gate). Returns gracefully if tools are not installed. Writes `simulation/solver_log.txt`, `simulation/result.frd`, `simulation/mesh.inp`, and `simulation/results_summary.json` atomically. |
| `POST /api/projects/{project_id}/run-simulation-stream` | Streaming SSE variant of `run-simulation`. Events: checking_tools → meshing → building_nsets → solving → parsing → done \| error. |
| `GET /api/simulation/tools` | Check whether Gmsh and CalculiX are available on this host. |
| `GET /api/projects/{project_id}/fields/{field_name}` | Canonical per-vertex result-field coloring from the FRD (Von Mises, displacement, principal stresses, safety factor, …) for in-viewer painting with legend/field-picker. Supersedes the retired `/stress-heatmap` GLB route (#246). |
| `POST /api/projects/{project_id}/chat-set-target` | Parse a natural-language message and upsert a design target into `design_targets.yaml`. Body: `{ message: str }`. |
| `POST /api/projects/{project_id}/contextual-chat` | Context-aware engineering chat grounded in the current project state. Injects geometry summary, simulation results, verdict, and design targets into the system prompt. Body: `{ message: str, history?: [...], api_key?: str }` |

Pair the two reads: capture a JSON artifact before an action, capture it again
after, then POST both to `/artifact/diff` to surface the structural delta.

The solver-input importer closes the biggest functional gap in the vertical
CAE MVP — `cae.run_solver` previously assumed the deck was already present
inside the package. Pair this endpoint with `aieng_get_cae_preprocessing_summary`
(or `cae.prepare_solver_run`) before approving execution.

The artifact inspector is exposed in the CAE panel of the React SPA: enter an
artifact path (e.g. `results/computed_metrics.json`) to view parsed JSON or
text inline. Clickable artifact paths appear in the CAE artifact grid and in
runtime chat history for low-risk file types (`.json`, `.txt`, `.md`, `.yaml`,
`.yml`, `.inp`, `.csv`, `.log`).

When `cae.apply_setup_patch` changes setup artifacts, the runtime chat bubble
shows an **artifact diff** panel: path, operation, JSON pointer, changed/added/
removed RFC-6901 paths, and compact before/after values. This is evidence review
metadata only — it does not prove physical correctness or mean the solver was
rerun. Stale-artifact warnings remain visible.

## Fresh-clone Copilot MVP demo path

Use this path for the v0.28 public-facing Copilot MVP demo. The demo is deterministic and works without FreeCAD, Gmsh, or CalculiX.

1. Clone the three sibling repos into one workspace.

   ```bash
   mkdir aieng-workspace
   cd aieng-workspace
   git clone https://github.com/armpro24-blip/aieng-ui.git
   git clone https://github.com/armpro24-blip/aieng.git
   ```

2. Install backend dependencies.

   ```bash
   cd aieng-ui/backend
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   pip install -e ".[dev]"
   pip install -e ../../aieng
   ```

   On Windows PowerShell:

   ```powershell
   cd aieng-ui\backend
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -e ".[dev]"
   pip install -e ..\..\aieng
   ```

3. Install frontend dependencies.

   ```bash
   cd ../frontend
   npm ci
   ```

4. Run the quick demo health gate from the `aieng-ui` repo root.

   ```bash
   cd ..
   ./scripts/check_demo_health.sh
   ```

   On Windows PowerShell:

   ```powershell
   cd ..
   .\scripts\check_demo_health.ps1
   ```

5. Start the backend and frontend in two terminals.

   ```bash
   # terminal 1
   cd aieng-ui/backend
   source .venv/bin/activate
   uvicorn app.main:app --reload

   # terminal 2
   cd aieng-ui/frontend
   npm run dev
   ```

   On Windows PowerShell, prefer the guarded launcher from the `aieng-ui` repo root:

   ```powershell
   .\scripts\backend.ps1
   ```

   It refuses to start if port `8000` already has a listener, preventing stale
   backend processes from serving old MCP tools. To intentionally replace an
   existing local backend, run:

   ```powershell
   .\scripts\backend.ps1 -Force
   ```

6. Open the Vite URL, usually `http://localhost:5173`, then open **Copilot Loop** and run:

   **Try the Copilot Loop demo -> Seed demo project -> Run demo health check -> Compare selected loops -> Load report diff -> Export review**

The expected demo shows one rejected loop, one approved loop, loop comparison, structured "What Changed" highlights, report diff, and a decision-review export with claim-boundary text.

The demo uses **deterministic fixture data**. It does **not** run FreeCAD/Gmsh/CalculiX, does **not** certify design safety, and does **not** advance engineering claims automatically. Imported metrics and real solver outputs are separate evidence categories; the UI and reports must not treat them as accepted engineering claims.

For the release checklist, screenshot/GIF checklist, and known limitations, see [`docs/v0.28-copilot-mvp-release-checklist.md`](docs/v0.28-copilot-mvp-release-checklist.md). For the detailed walkthrough, see [`docs/demo-release-walkthrough.md`](docs/demo-release-walkthrough.md).

### Run the full Copilot MVP demo

The full end-to-end Copilot loop — health check → CAD/package inspection →
design targets → computed metrics → target comparison → approval-gated CAD edit →
stale-evidence tracking → structural preflight → approval-gated solver run →
FRD extraction → Copilot Loop report → **Engineering Review Support Packet
export** — is documented step-by-step in
[`docs/full-copilot-demo-walkthrough.md`](docs/full-copilot-demo-walkthrough.md).
Missing tools and missing evidence are reported honestly; the demo does not
certify design safety or advance engineering claims.

## Local validation and CI

Run the local release gate:

```bash
./scripts/check_demo_health.sh --full
```

On Windows PowerShell:

```powershell
.\scripts\check_demo_health.ps1 -Full -Frontend
```

Manual commands:

```bash
cd backend
python -m pytest -q -k "smoke_check"
python -m pytest -q

cd ../frontend
npm run build
```

Optional real-environment checks:

```bash
cd backend
python -m pytest tests/test_api.py::test_run_solver_real_ccx_skipped_if_unavailable -v

# Legacy optional adapter check; requires AIENG_TEST_REAL_FREECAD=1 and FreeCADCmd.
python -m pytest tests/test_api.py::test_cae_generate_mesh_real_freecad_integration -v
```

CI is defined in [`.github/workflows/demo-health.yml`](.github/workflows/demo-health.yml). It runs a smoke-check gate on push and pull request, plus full backend tests and frontend build. Real CalculiX and legacy FreeCAD-adapter checks are optional local gates and are expected to skip cleanly when binaries are unavailable.

Focused reliability gates used during local-agent/backend work:

```bash
cd backend
python -m pytest tests -q -k "claude or adapter or autopilot"
python -m pytest tests/test_agent_reliability_diagnostics.py -q
python -m pytest tests/test_cad_generation.py -q -k "label or named_parts or append_preserves or single_labeled"
```

Full backend pytest may still expose unrelated environment-dependent failures;
isolate failures before treating them as regressions in local-agent reliability
work.

## Tests

```bash
cd backend
python -m pytest -c NUL tests/test_api.py -v
```

A generic end-to-end post-processing smoke test (`test_postprocessing_smoke_metrics_import_and_summary_refresh`) validates the full metrics-import → summary-refresh workflow without solver execution or part-family fixtures.

A vertical CAE workflow benchmark (`test_vertical_cae_workflow_end_to_end`) demonstrates the full agent-run lifecycle through the runtime REST API: preflight → approval-gated external solver execution (mocked ccx) → FRD scalar extraction → computed metrics write-back → result summary refresh, with honest limitations enforced (`converged=null`, explicit warnings, no physical correctness claim). See [`../docs/aieng-agent-workflow.md`](../docs/aieng-agent-workflow.md) for the reusable agent workflow pattern, and [`../docs/demo-vertical-cae-workflow.md`](../docs/demo-vertical-cae-workflow.md) for a step-by-step walkthrough with agent prompt.

Assembly-aware topopt benchmark/demo:

```bash
cd backend
python -m pytest -q tests/test_assembly_topopt_demo.py
python -m pytest -q ../aieng/tests/test_assembly_topopt.py
```

The canonical package seed lives in `tests/fixtures/assembly_topopt_demo/`. The
backend demo test proves the proxy-based loop end to end on a deterministic
assembly: `/assembly/process` writes assembly validation/registry/CAE proxy
artifacts, `write_assembly_topopt_problem(...)` emits the selected-part setup,
and `/assembly/topology-optimization/run` writes selected-part derived artifacts
plus post-optimization verification diagnostics and a rule-based recommendation/report layer. The verifier checks that
reference/frozen parts were not modified, preserve interfaces remain traceable
or are warned honestly, and proxy limitations stay explicit. The recommendation
pass turns those artifacts plus `analysis/assembly_result_map.json` into
machine-readable next-step advice without rerunning optimization. It does not certify
real contact or unchanged interface geometry. The demo also pins the unsafe-data path where no safe
`analysis/topology_optimization_problem.json` is emitted, execution returns
`needs_user_input`, and no package-level geometry or frozen/reference parts are
overwritten. This remains a proxy model: `contact_physics_modeled:false` and
`bolt_preload_modeled:false` are required outputs, not optional claims.

For a current capability/status table and artifact/path inventory, see
`../aieng/docs/backend_capability_matrix.md` and
`../aieng/docs/backend_artifact_reference.md`.

If you have CalculiX installed, you can run a real-environment smoke test: [`docs/quickstart-real-ccx.md`](docs/quickstart-real-ccx.md).
For historical notes on the older FreeCAD/Gmsh real-binary path, see [`docs/walkthrough-real-cae-pipeline.md`](docs/walkthrough-real-cae-pipeline.md).

Before demoing, use the [`docs/demo-readiness-checklist.md`](docs/demo-readiness-checklist.md). If something breaks during setup or runtime, see [`docs/troubleshooting-vertical-cae-mvp.md`](docs/troubleshooting-vertical-cae-mvp.md).

## Documentation

Repo-level docs:

- [Package semantics](docs/package_semantics.md) — canonical concept definitions (artifact, evidence, claim, proposal, support packet, review readiness, etc.) and six core principles
- [Runtime architecture](docs/runtime_architecture.md) — orchestration layer, runtime tools, and optional external CAD adapter notes
- [Real CAE pipeline walkthrough](docs/walkthrough-real-cae-pipeline.md) — historical real-binary path: STEP → mesh → ccx → FRD → evidence index; evidence vs. claim discipline; manual validation checklist
- [Vertical CAE MVP milestone](docs/milestone-vertical-cae-mvp.md) — current MVP positioning, real capabilities, boundaries, and check commands

Workspace-level docs (covers all three repos):

- [System architecture](../docs/system_architecture.md) — three-repo overview and data flow
- [Repo boundaries](../docs/repo_boundaries.md) — ownership, coupling points, what must not cross
- [Runtime and agents](../docs/runtime_and_agents.md) — run lifecycle, REST API, future MCP integration
- [CAD adapter strategy](../docs/cad_adapter_strategy.md) — provider interface, adding new backends
- [Package contract](../docs/package_contract.md) — `.aieng` ZIP format and package states
- [Roadmap](../docs/roadmap.md) — phases 1–5
