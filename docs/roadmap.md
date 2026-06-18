# System Roadmap

This document tracks the workspace-level system roadmap across all three
repos. Per-repo roadmaps (`aieng/docs/roadmap.md`,
`aieng_freecad_mcp/docs/roadmap.md`) contain more detail at the component
level.

---

## Phase 0 — Current State (as of May 2026)

### aieng (semantic package engine)
- Phases 0–20 complete. Rigorous interop validation achieved.
- STEP import, topology, features, enrichment, AI summary, validation.
- CAE scaffold: CalculiX deck export, mesh/solver evidence import.
- Claim map, evidence ledger, completeness report, tool trace.
- MCP server (`aieng serve`) exposing package-level tools.
- Benchmark scaffold (questions, rubric, execution infrastructure).
- 40+ CLI commands.

### aieng_freecad_mcp (FreeCAD execution adapter)
- MCP-facing tool contract defined and implemented.
- Five composable execution paths: CAD-only, CAE-only, CAD→CAE, reference, claim.
- Evidence and claim policy enforcement.
- Milestone-1 acceptance scripts pass.
- Real FreeCAD subprocess, mesh generation, and solver execution are optional
  (mock/surrogate paths work for tests and demos without real runtime).

### aieng-ui (web workbench)
- FastAPI service layer with full project/file management.
- React SPA: STEP upload, Three.js viewer (GLB/STL), semantic summary panel,
  conditional CAE panel, honest CAE lifecycle panel (setup / runs / results),
  artifact inspector, chat/orchestration panel, audit log, settings drawer.
- Local orchestration runtime: `RunRecord`, `ToolCall`, `ToolResult`,
  `RuntimeEvent`, tool registry, intent-based plan builder, synchronous
  executor with approval gate.
- 27 registered runtime tools including working `cae.generate_mesh`,
  `cae.run_solver`, `cae.extract_solver_results`, `cae.apply_setup_patch`,
  `cad.edit_parameter`, `postprocess.refresh_cae_summary`. Mutation/expensive
  operations are approval-gated. `freecad.run_macro` remains a skeleton.
- REST endpoints: `POST /api/runtime/runs`, `GET /api/runtime/runs/{id}`,
  `GET /api/runtime/runs/{id}/events`, plus `approve`/`reject` resumption.
- CAE scalar field visualization in the frontend viewer remains synthetic
  (`y_normalized` colormap); real per-node field serving is still future work.
- Backend test suite: 175 passing, 3 skipped (real-FreeCAD and real-ccx tests
  gated on host binaries and `AIENG_TEST_REAL_FREECAD=1`).

---

## Phase 1 — Runtime Persistence and Approval Resumption ✅ Implemented

**Scope:** `aieng-ui`

**Delivered:**
- File-backed run store (`data/runtime/runs/{run_id}.json`); configurable via
  `AIENG_RUNTIME_STATE_DIR`. Runs survive server restart.
- `POST /api/runtime/runs/{id}/approve` — resumes a run that is
  `awaiting_approval`. Executes the pending tool, continues remaining steps.
- `POST /api/runtime/runs/{id}/reject` — marks run `rejected`; pending tool
  not executed.
- `GET /api/runtime/runs` — lists recent runs (slim summaries).
- `GET /api/runtime/tools` — tool registry introspection.
- `ToolError` structured error payload on all failed/rejected tools.
- Statuses: `pending`, `running`, `completed`, `failed`, `awaiting_approval`,
  `rejected`, `cancelled`.
- Frontend approve/reject buttons conditional on `awaiting_approval` state.
- 21 backend tests passing (13 original + 8 Phase 1 additions).

**Still future:** SSE/WebSocket streaming events.

---

## Phase 2 — Real FreeCAD Bridge ✅ Implemented (geometry inspection)

**Scope:** `aieng-ui` + `aieng_freecad_mcp`

**Delivered:**
- `aieng_freecad_mcp/src/freecad_mcp/geometry_inspector.py` — `FREECAD_INSPECT_SCRIPT`
  embedded FreeCAD script + synchronous `run_geometry_inspection()` launcher;
  accepts `.step`, `.stp`, or `.fcstd`; returns face/edge/vertex counts, bounding
  box, volume, surface area, and FreeCAD version.
- `aieng-ui/backend/app/freecad_bridge.py` — thin bridge; injects
  `aieng_freecad_mcp/src` into `sys.path` and delegates to `run_geometry_inspection`.
- `_tool_freecad_inspect_geometry` in `main.py` — resolves input file from
  `inputPath` / `project_id → source_step`, validates existence, calls bridge.
- Intent map keywords expanded: `bounding box`, `volume`, `face count`,
  `cad geometry`, `solid`, `part geometry`.
- Frontend: `formatGeometryResult()` produces a compact Chinese summary line
  in chat when a geometry inspection run completes.
- 4 new backend tests (25 total); geometry inspector tests in `aieng_freecad_mcp`.

**Still future (Phase 2 remainder):**
- `freecad.run_macro` remains a skeleton (approval-gated but not yet connected).
- End-to-end test: STEP upload → FreeCAD import → `.aieng` package → UI preview.

**Depends on:** Phase 1 (stable approval resumption).

---

## Phase 2.5 — FreeCAD Export and Artifact Loop ✅ Implemented

**Scope:** `aieng-ui` + `aieng_freecad_mcp`

**Delivered:**
- `aieng_freecad_mcp/src/freecad_mcp/step_exporter.py` — STEP export via
  `FreeCADCmd`; returns `artifacts` list with `{path, kind, role}` entries.
- `freecad_bridge.export_step()` in `aieng-ui` — thin wrapper.
- `freecad.export_step` runtime tool in `main.py` — auto-generates safe
  `{stem}_export.step` output path; writes per-project audit log.
- `ToolResult.artifacts` hoisting — `_execute_steps()` extracts artifacts
  from tool output dicts automatically.
- Frontend `变更文件:` block — `formatArtifactChanges()` shows changed files
  in the chat output when a run has artifacts.
- 31 backend tests passing; 12 step-exporter tests passing.

---

## Phase 3.5 — CAE Artifact Detection + Honest UI Status Panel ✅ Implemented

**Scope:** `aieng` + `aieng-ui` + `aieng_freecad_mcp`

**Delivered:**
- `aieng detect-cae-artifacts` CLI — canonical artifact path list and honest zip-scan detector.
- `aieng-ui` backend: `GET /api/projects/{project_id}/cae-artifacts` endpoint; artifact detection merged into `package_summary` under `cae.artifact_detection`.
- `aieng-ui` frontend: honest CAE Artifact Status panel showing mode badge (CAD-only / CAE setup / CAE result / CAE validation) and per-artifact presence grid.
- `aieng_freecad_mcp`: `aieng_get_cae_status` MCP tool delegates to the new endpoint.
- No solver execution; no synthetic result claims. Detection is read-only.

---

## Phase 4 — Post-processing Result Summary Contract ✅ Implemented

**Scope:** `aieng` + optional `aieng-ui`

**Delivered:**
- `aieng/src/aieng/cae_result_summary.py` — `generate_cae_result_summary()`, `generate_evidence_index()`, `generate_postprocessing_markdown()`, `write_cae_result_summary_package()`.
- `aieng summarize-cae-results` CLI — prints JSON or markdown; `--write` flag writes three files into the package.
- Schema versioned (`0.1`) with honest fields: `computed_values.extrema_computed: false`, `status.solved: null`, `status.converged: null`.
- `llm_summary` block with `one_line`, `key_findings`, `risks`, `recommended_next_actions`, `limitations`.
- Evidence index entries catalog all 15 CAE artifacts with `kind`, `role`, `supports`.
- `aieng-ui` backend: `GET /api/projects/{project_id}/cae-result-summary` endpoint; `package_summary` includes `cae.result_summary`.
- Frontend: minimal "Post-processing Summary" block below artifact panel.
- No solver execution. No VTU/FRD/ODB numerical parsing. No synthetic extrema.

---

## Phase 4.5 — MCP Wrapper for CAE Result Summary ✅ Implemented

**Scope:** `aieng_freecad_mcp`

**Delivered:**
- `aieng_runtime_client.py`: `get_cae_result_summary(project_id)` → `GET /api/projects/{project_id}/cae-result-summary`.
- `tools_runtime/__init__.py`: `aieng_get_cae_result_summary` MCP tool — thin wrapper, no local computation.
- Tests: client call verification, error handling, registry sanity check.
- Docs updated in `mcp_runtime_tools.md` and `README.md`.
- Honest limitations documented: artifact-presence only, no numerical field parsing, no solver execution.

---

## Phase 35 — Design-Target Evidence Resource ✅ Implemented

**Scope:** `aieng` + `aieng-ui` + `aieng_freecad_mcp`

**Goal:** Formalize `task/design_targets.yaml` as a first-class package resource so benchmark scenarios and engineering workflows no longer inline design goals only in prompts.

All five sub-PRs landed. Workspace-level activity confirming this is in `aieng/`
git log (commits `c40a208`, `8ac64cd`, `4e52754`, `f3df0f0`, `71f65e0`,
`887bd0c`, `97b5d09`, `86b0ee7`), in `aieng_freecad_mcp/` (`1118944`), and in
`aieng-ui/` (`662ec9b`).

**Delivered:**
- **PR 1 — Schema / docs / examples:** `aieng/schemas/design_targets.schema.json`
  supports dual format (legacy `0.1.0` and modern `0.1.1` with `target_id`,
  `target_type`, `comparator`, `threshold`, `priority`, `scope`,
  `protected_features`); `aieng/schemas/design_target_comparison.schema.json`
  defines the comparison block; `aieng/examples/design_targets/bracket_mass_reduction/design_targets.yaml`
  is the canonical example; `aieng/docs/design_targets.md` documents the contract.
- **PR 2 — Validation + comparison logic:** `_validate_design_targets()` and
  `_compare_design_targets()` extended in `aieng/src/aieng/validate.py` and
  `aieng/src/aieng/cae_result_summary.py`; supports `<=`, `<`, `>=`, `>`, `==`,
  `within_range`, `preserve`, `reduce_by_at_least`, `priority` with honest
  `pass`/`fail`/`unknown`/`not_evaluated` semantics.
- **PR 3 — CLI + writeback:** `aieng compare-design-targets` CLI with
  `--write-summary` for atomic injection into `results/result_summary.json`.
- **PR 4 — Benchmark integration:** mass-reduction targets moved from
  prompt-only into `task/design_targets.yaml` for the
  `mass_reduction_recommendation` scenario.
- **PR 5 — UI display + MCP wrapper:** `aieng-ui` renders the comparisons block
  in the CAE results panel with status badges; `aieng_freecad_mcp` exposes
  `aieng_inspect_design_targets` MCP tool.

**Boundary rules preserved (and verified by tests):**
- Design targets are requirements, not solver results.
- Comparisons do not advance `claim_map.json`.
- Missing evidence produces `unknown` or `not_evaluated`, never fake pass/fail.

---

## Phase 3 — MCP Adapter for aieng-ui Runtime ✅ Implemented

**Scope:** `aieng_freecad_mcp`

**Delivered:**
- `aieng_freecad_mcp/src/freecad_mcp/aieng_runtime_client.py` — synchronous
  HTTP client wrapping all runtime REST endpoints; uses stdlib `urllib.request`;
  configurable via `AIENG_RUNTIME_BASE_URL`.
- `aieng_freecad_mcp/src/freecad_mcp/tools_runtime/__init__.py` —
  `register_runtime_tools(mcp, client)` registers 7 MCP tools that delegate to
  the runtime REST API.
- MCP tools: `aieng_list_runtime_tools`, `aieng_start_runtime_run`,
  `aieng_get_runtime_run`, `aieng_inspect_geometry`, `aieng_export_step`,
  `aieng_approve_runtime_run`, `aieng_reject_runtime_run`.
- `server.py` updated — registers runtime bridge tools at startup; fixed
  lifespan `mcp` → `_server` scoping bug.
- 37 new tests (19 client + 18 MCP tools); all pass without FreeCAD or running
  backend.
- `aieng_freecad_mcp/docs/mcp_runtime_tools.md` — usage, Claude Code config,
  tool reference, limitations.

**Architectural rule preserved:** MCP tools call the REST API and return the
result.  FreeCAD logic, approval gates, audit logs, and event timelines remain
inside `aieng-ui`.

**Depends on:** Phase 1 + Phase 2.5 (runtime and export must be implemented).

---

## Phase 5 — Solver Metadata + Load Case Normalization ✅ Implemented

**Scope:** `aieng` + `aieng-ui` + `aieng_freecad_mcp`

**Delivered:**
- `aieng/src/aieng/cae_result_summary.py` — reads `results/solver_metadata.json`,
  `results/field_metadata.json`, `simulation/solver_settings.json`, and
  `simulation/load_cases/*.json` from the package ZIP.
- Normalizes solver name/software, solver settings, load cases, and field metadata
  into the honest CAE result summary (schema `"0.2"`).
- Evidence index catalogs metadata artifacts and dynamic load case entries.
- Markdown summary includes solver, load cases, solver settings, and field metadata.
- `aieng-ui` frontend displays solver info, load case list, solver settings, and
  field metadata count in the CAE panel.
- No solver execution. No VTU/FRD numerical parsing.

---

## Phase 6 — Precomputed Metrics Ingestion ✅ Implemented

**Scope:** `aieng` + `aieng-ui`

**Delivered:**
- `aieng` reads optional `results/computed_metrics.json` produced by external
  post-processors or solver adapters.
- Supports metric keys: `max_von_mises_stress`, `max_displacement`,
  `minimum_safety_factor`, with `value`, `unit`, `location`, `field`, `basis`.
- `computed_values.extrema_computed` set to `true` only when valid metrics are
  present; otherwise remains `false`.
- Metrics attach to matching load cases from `simulation/load_cases/*.json`;
  unknown load cases are preserved as result-only entries.
- Summary schema bumped to `"0.3"`.
- Evidence index includes `results/computed_metrics.json` entry.
- Markdown and LLM summaries honestly label metrics as imported/external.
- Risks mention low safety factor (`< 1.5`) when present.
- Limitations clarify that metrics are imported, not computed by `aieng`, and
  that source field files are not parsed.
- `aieng-ui` frontend lightly displays imported metrics (σ_max, U_max, SF_min)
  with source label.
- No solver execution. No VTU/FRD/ODB numerical parsing.

---

## Phase 7 — FreeCAD / External Postprocessor Computed Metrics Exporter ✅ Implemented

**Scope:** `aieng_freecad_mcp`

**Delivered:**
- `freecad_mcp.computed_metrics_exporter` module with `export_computed_metrics()`
- CLI: `python -m freecad_mcp.computed_metrics_exporter --input <path> --output <path>`
- Supports flat JSON, CSV (`name,value,unit`), and Phase-6-schema JSON inputs
- Normalizes to canonical `results/computed_metrics.json` (schema `"0.1"`)
- Maps common flat keys (`max_von_mises_stress_mpa`, `max_displacement_mm`, `factor_of_safety`) to canonical metric keys
- Preserves metric objects with `location`, `field`, `basis` when present
- Warnings for unrecognized keys and empty inputs
- Structured error taxonomy (`ComputedMetricsExportError`)
- Machine-readable JSON stdout; stderr for errors
- Round-trip test verifies exporter output is correctly ingested by `aieng` Phase 6
- No FreeCAD dependency; no solver execution; no VTU/FRD/ODB parsing

---

## Phase 8 — Runtime and MCP Wrapper for Computed Metrics Export ✅ Implemented

**Scope:** `aieng-ui` + `aieng_freecad_mcp`

**Delivered:**
- `aieng-ui/backend/app/freecad_bridge.py`: added `export_computed_metrics()` bridge
  function that imports `freecad_mcp.computed_metrics_exporter` at call time.
- `aieng-ui/backend/app/runtime.py`: extended `execute_run()` to accept
  `ctx["tool_input"]` and merge structured parameters into each plan step's
  input. Added planner keywords for computed metrics intent.
- `aieng-ui/backend/app/main.py`: registered `postprocess.generate_computed_metrics`
  runtime tool with input/output path resolution, artifact metadata, and audit
  logging. Extended `POST /api/runtime/runs` to accept optional `tool_input`.
- `aieng_freecad_mcp/src/freecad_mcp/aieng_runtime_client.py`: extended
  `start_run()` with optional `tool_input` parameter.
- `aieng_freecad_mcp/src/freecad_mcp/tools_runtime/__init__.py`: added
  `aieng_generate_computed_metrics` MCP tool that delegates to the runtime with
  structured params.
- Frontend changed-artifacts display already handles `computed_metrics` artifacts.
- No solver execution. No VTU/FRD/ODB parsing.

---

## Phase 9 — Refresh CAE Summary After Metrics Export ✅ Implemented

**Scope:** `aieng-ui` + `aieng_freecad_mcp`

**Delivered:**
- `aieng-ui/backend/app/aieng_bridge.py`: `refresh_cae_result_summary()` thin bridge to `aieng.cae_result_summary.write_cae_result_summary_package`.
- `aieng-ui/backend/app/runtime.py`: planner keywords for `postprocess.refresh_cae_summary` intent; `build_plan` routes "refresh cae summary" to the tool.
- `aieng-ui/backend/app/main.py`: registered `postprocess.refresh_cae_summary` runtime tool with `packagePath` / `project_id` resolution, artifact metadata, and structured error `missing_cae_summary_package_path`.
- `aieng_freecad_mcp/src/freecad_mcp/aieng_runtime_client.py`: `start_run()` supports optional `tool_input`.
- `aieng_freecad_mcp/src/freecad_mcp/tools_runtime/__init__.py`: `aieng_refresh_cae_summary` MCP tool delegates to runtime.
- Backend tests: planner intent matching + missing package path error handling.
- No solver execution. No VTU/FRD/ODB parsing.

---

## Phase 10 — UI One-Click Post-Processing Refresh ✅ Implemented

**Scope:** `aieng-ui`

**Delivered:**
- Frontend `api.ts`: `startRun()` accepts optional `toolInput` parameter and forwards it as `tool_input` in the POST body.
- Frontend `App.tsx`:
  - Added `caeRefreshing` local state (disables button, shows "正在刷新 CAE 摘要…").
  - Added `refreshCaeSummary()` handler: calls `api.startRun("refresh cae summary", selectedId, { project_id, overwrite: true })`, appends result to chat history, refreshes project summary on success, and shows notice on error.
  - Added "刷新 CAE 摘要" button inside the CAE Artifact Status panel, with honest sub-label: "重新生成 .aieng CAE 摘要/证据文件（不执行求解器）".
- Build passes (`npm run build`).
- Backend tests unchanged (40 passing).

**Limitations:**
- No file picker for "导入计算指标" — left as future work.
- Solver execution remains external; the button only regenerates summary/evidence files from existing package artifacts.

---

## Phase 11 — Import Metrics UI Flow ✅ Implemented

**Scope:** `aieng-ui`

**Delivered:**
- Frontend `App.tsx`:
  - Added `metricsInputPath`, `metricsLoadCaseId`, `metricsSoftware`, and `metricsImporting` state.
  - Added `importMetricsAndRefresh()` handler: validates input path, runs `postprocess.generate_computed_metrics` via `api.startRun()` with `tool_input`, then runs `postprocess.refresh_cae_summary`, appends both runs to chat history, refreshes project summary on success, and shows notices on error.
  - Added minimal input area in CAE Artifact Status panel:
    - Text input for metrics source path (JSON/CSV).
    - Text input for load case ID (default `load_case_001`).
    - Text input for software label.
    - Button "导入计算指标并刷新摘要" with loading state.
  - Honest sub-label: "从已有的 JSON/CSV 文件导入指标，再刷新 CAE 摘要。不执行求解器。"
- Frontend `api.ts`: `startRun()` already supports `toolInput` from Phase 10.
- Backend `aieng-ui/backend/app/main.py`: fixed latent `NameError` in `_tool_generate_computed_metrics` where `project` was undefined; now uses `get_project(active_settings, project_id)`.
- Build passes (`npm run build`).
- Backend tests pass (40 passing).

**Runtime call sequence:**
1. `POST /api/runtime/runs` — message `"generate computed metrics"`, `tool_input: { inputPath, project_id, loadCaseId, software }`.
2. `POST /api/runtime/runs` — message `"refresh cae summary"`, `tool_input: { project_id, overwrite: true }`.

**Limitations:**
- Path-based input only (no file picker/upload yet).
- No solver execution.
- Summary logic remains in `aieng`.

---

## Phase 12 — Generic End-to-End Post-Processing Smoke Test ✅ Implemented

**Scope:** `aieng-ui` backend tests

**Delivered:**
- `aieng-ui/backend/tests/test_api.py`: added `test_postprocessing_smoke_metrics_import_and_summary_refresh`.
- Test flow:
  1. Creates a temporary project with a minimal `.aieng` package (generic names only).
  2. Writes a generic metrics CSV with `max_von_mises_stress`, `max_displacement`, `minimum_safety_factor`.
  3. Runs `postprocess.generate_computed_metrics` via runtime REST endpoint.
  4. Asserts run completes and `results/computed_metrics.json` is produced on disk.
  5. Injects `results/computed_metrics.json` into the `.aieng` package ZIP.
  6. Runs `postprocess.refresh_cae_summary` via runtime REST endpoint.
  7. Asserts run completes and changed artifacts include `result_summary.json`, `evidence_index.json`, `postprocessing_summary.md`.
  8. Calls `GET /api/projects/{project_id}/cae-result-summary` and asserts imported metrics are visible:
     - `computed_values.extrema_computed == true`
     - `max_von_mises_stress.value == 187.4`
     - `max_displacement.value == 0.82`
     - `minimum_safety_factor.value == 1.33`
- Uses real `aieng` and `aieng_freecad_mcp` repo roots (no mocking of exporter or summarizer).
- No part-family-specific fixtures. No domain-specific geometry.
- Backend tests: 41 passing (40 existing + 1 new).

**Limitations:**
- No solver execution. No VTU/FRD/ODB parsing.

---

## Phase 13 — Runtime Artifact Write-Back into `.aieng` Package ✅ Implemented

**Scope:** `aieng-ui` backend

**Delivered:**
- `aieng-ui/backend/app/main.py`:
  - Added `write_artifact_to_package()` helper following the standard aieng safe-rewrite pattern (temp file + atomic `shutil.move`).
  - Skips duplicate ZIP entries; preserves all unrelated members.
  - Validates `.aieng` suffix and `manifest.json` presence.
  - Supports `overwrite=True/False`.
- Integrated write-back into `_tool_generate_computed_metrics`:
  - After `freecad_bridge.export_computed_metrics` succeeds, resolves the project's `.aieng` package path.
  - Writes `results/computed_metrics.json` into the package ZIP.
  - Returns both filesystem and package artifacts in the tool result.
  - Warns (non-fatal) if package path cannot be resolved.
- Updated `test_postprocessing_smoke_metrics_import_and_summary_refresh`:
  - Removed manual ZIP injection step.
  - Added assertion that the `.aieng` package contains `results/computed_metrics.json` after the generate step.
- Added focused helper tests:
  - `test_write_artifact_to_package_adds_new_file`
  - `test_write_artifact_to_package_overwrites_existing`
  - `test_write_artifact_to_package_refuses_overwrite_by_default`
  - `test_write_artifact_to_package_missing_source_raises`
  - `test_write_artifact_to_package_missing_manifest_raises`
- Backend tests: 46 passing (41 existing + 5 new).

**Architectural principle preserved:**
- `.aieng` package remains the stable evidence source of truth.
- Loose project files are not the long-term source of truth.

**Limitations:**
- Currently only `results/computed_metrics.json` is written back; future artifacts can reuse `write_artifact_to_package`.
- No solver execution. No VTU/FRD/ODB parsing.

---

## Phase 14 — Pre-Processing Summary Contract ✅ Implemented

**Scope:** `aieng`

**Delivered:**
- `aieng/src/aieng/cae_preprocessing_summary.py`:
  - `generate_preprocessing_summary(package_path)` — scans `.aieng` ZIP for setup artifacts and returns an honest readiness dict (schema `"0.1"`).
  - `generate_preprocessing_markdown(summary)` — produces human/LLM-readable markdown.
  - `write_preprocessing_summary_package(package_path, overwrite)` — safe ZIP rewrite (temp file + atomic move) writing `simulation/preprocessing_summary.json` and `simulation/preprocessing_summary.md`.
  - Tolerant of missing or malformed JSON; adds warnings without failing.
  - Conservative `ready_for_solver` heuristic: requires materials, loads, boundary conditions, mesh, and solver settings or load cases.
- `aieng/src/aieng/cli.py`:
  - Added `summarize-cae-preprocessing` CLI command with `--json`, `--write`, and `--overwrite` flags.
- Tests (`aieng/tests/test_cae_preprocessing_summary.py`, 14 tests):
  - CAD-only package, complete setup, partial setup, mesh detection, load case detection, malformed JSON tolerance, markdown output, ZIP writer behavior, overwrite/refuse, no duplicate entries.
  - All generic fixtures; no part-family-specific data.
- `aieng` tests: 1396 passed (1382 existing + 14 new).

**Schema highlights:**
- `status.has_cae_setup`, `has_materials`, `has_loads`, `has_boundary_conditions`, `has_constraints`, `has_mesh`, `has_load_cases`, `has_solver_settings`, `has_cae_mapping`
- `status.ready_for_solver` — conservative heuristic
- `status.missing_items` — list of absent setup artifacts
- `artifacts.*` — normalized lists/dicts from parsed setup files
- `llm_summary.one_line`, `key_findings`, `risks`, `recommended_next_actions`, `limitations`

**Limitations:**
- Readiness is artifact-based only; no physical correctness validation.
- No solver execution. No mesh generation. No VTU/FRD/ODB parsing.
- UI/runtime integration is future work (Phase 15 or later).

---

## Phase 15 — Simulation Run Metadata Contract ✅ Implemented

**Scope:** `aieng`

**Delivered:**
- `aieng/src/aieng/cae_simulation_run_summary.py`:
  - `generate_simulation_run_summary(package_path)` — scans `.aieng` ZIP for run metadata under `simulation/runs/*/solver_run.json` and legacy `simulation/solver_run.json`; returns honest summary dict (schema `"0.1"`).
  - `generate_simulation_run_markdown(summary)` — produces human/LLM-readable markdown.
  - `write_simulation_run_summary_package(package_path, overwrite)` — safe ZIP rewrite writing `simulation/simulation_run_summary.json` and `simulation/simulation_run_summary.md`.
  - Tolerant of missing or malformed JSON; adds warnings without failing.
  - Supports `simulation/runs/<run_id>/solver_run.json`, `run_manifest.json`, `solver_log.txt`, `solver_input.inp`, and legacy root-level paths.
- `aieng/src/aieng/cli.py`:
  - Added `summarize-cae-runs` CLI command with `--json`, `--write`, and `--overwrite` flags.
- Tests (`aieng/tests/test_cae_simulation_run_summary.py`, 14 tests):
  - No runs, single completed converged run, failed run, multiple runs with latest selection, malformed JSON tolerance, legacy solver_run.json, markdown output, ZIP writer behavior, overwrite/refuse, no duplicate entries.
  - All generic fixtures; no part-family-specific data.
- `aieng` tests: 1410 passed (1396 existing + 14 new).

**Schema highlights:**
- `status.has_simulation_runs`, `run_count`, `latest_run_id`
- `status.has_completed_run`, `has_converged_run`, `has_failed_run`
- `runs[].run_id`, `solver`, `software`, `analysis_type`, `state`, `solved`, `converged`, `warnings`, `errors`, `input_files`, `output_files`, `log_file`
- `llm_summary.one_line`, `key_findings`, `risks`, `recommended_next_actions`, `limitations`

**Limitations:**
- Summary is metadata-based only; no solver execution, no physical correctness validation.
- No VTU/FRD/ODB numerical parsing.
- UI/runtime integration is future work.

---

## Phase 16 — UI CAE Lifecycle Sections: Setup / Runs / Results ✅ Implemented

**Scope:** `aieng-ui` backend + frontend

**Delivered:**
- `aieng-ui/backend/app/main.py`:
  - Added `_generate_cae_preprocessing_summary()` and `_generate_cae_simulation_run_summary()` helpers following the existing safe `sys.path` injection pattern.
  - Updated `package_summary()` to merge `preprocessing_summary` and `simulation_run_summary` into `cae` alongside existing `result_summary`.
  - Added endpoints:
    - `GET /api/projects/{project_id}/cae-preprocessing-summary`
    - `GET /api/projects/{project_id}/cae-simulation-run-summary`
- `aieng-ui/frontend/src/types.ts`:
  - Added `CaePreprocessingSummary` and `CaeSimulationRunSummary` exported types.
- `aieng-ui/frontend/src/api.ts`:
  - Added `getCaePreprocessingSummary(projectId)` and `getCaeSimulationRunSummary(projectId)` wrappers.
- `aieng-ui/frontend/src/App.tsx`:
  - Updated CAE Artifact Status panel to show three compact lifecycle sections:
    1. **Setup / Pre-processing** — readiness grid (materials, loads, BCs, mesh, solver settings), missing items, honest limitation label.
    2. **Simulation Runs** — run count, latest run, completed/converged/failed flags, latest solver/software, warnings count, honest limitation label.
    3. **Results / Post-processing** — existing imported metrics, load cases, field metadata, limitations.
- Backend tests (`aieng-ui/backend/tests/test_api.py`, 4 new tests):
  - `test_get_cae_preprocessing_summary_endpoint`
  - `test_get_cae_simulation_run_summary_endpoint`
  - `test_get_cae_preprocessing_summary_missing_package_returns_404`
  - `test_get_cae_simulation_run_summary_missing_package_returns_404`
- Backend tests: 50 passed.
- Frontend build passes (`npm run build`).
- `aieng` and `aieng_freecad_mcp` tests unchanged and passing.

**Limitations:**
- Setup/run summaries are only displayed when the `.aieng` package contains them (generated by `aieng` CLI or runtime).
- No solver execution. No mesh generation. No VTU/FRD/ODB parsing.
- Summary logic remains in `aieng`; `aieng-ui` only displays it.

---

## Phase 17 — MCP Tools for CAE Lifecycle Summaries ✅ Implemented

**Scope:** `aieng_freecad_mcp`

**Delivered:**
- `aieng_runtime_client.py`: `get_cae_preprocessing_summary(project_id)` → `GET /api/projects/{project_id}/cae-preprocessing-summary`.
- `aieng_runtime_client.py`: `get_cae_simulation_run_summary(project_id)` → `GET /api/projects/{project_id}/cae-simulation-run-summary`.
- `tools_runtime/__init__.py`: `aieng_get_cae_preprocessing_summary` MCP tool — thin wrapper, no local computation.
- `tools_runtime/__init__.py`: `aieng_get_cae_simulation_run_summary` MCP tool — thin wrapper, no local computation.
- 4 new MCP tool tests + 2 new client tests; all 51 tests pass.
- `docs/mcp_runtime_tools.md` updated with full tool reference.
- MCP bridge now exposes all three CAE lifecycle panels: setup readiness, simulation run status, and post-processing results.

**Architectural rule preserved:** MCP tools call the REST API and return the result. No computation or package parsing in the MCP layer.

**Depends on:** Phase 14 (preprocessing summary contract) + Phase 15 (simulation run summary contract) + Phase 16 (UI lifecycle endpoints).

---

## Phase 18 — Evidence-Grounded Engineering Actions MVP ✅ Implemented

**Scope:** `aieng-ui`

**Pattern:** Read `.aieng` evidence → propose controlled action → apply through
runtime → write changed artifacts → refresh dependent summaries → mark
downstream evidence stale → return evidence-backed result.

This pattern is the first step toward a broader class of evidence-grounded
engineering actions where the workbench proposes explicit, reviewable, and
safely constrained edits to design or simulation setup, grounded in prior
evidence. Later phases will extend this to CAD parameter edits, dimension
changes, simulation run requests/imports, and post-processing workflows.
This phase implements only the first action type.

**First action: `cae.apply_setup_patch`**

Applies small, explicit, reviewable patches to CAE setup artifacts inside a
`.aieng` package. All patches are validated before any write; the entire
package is rewritten atomically.

**Delivered:**
- `aieng_bridge.py`: `refresh_preprocessing_summary()` — parallel to the
  existing `refresh_cae_result_summary()`; calls `write_preprocessing_summary_package`
  from the `aieng` package.
- `runtime.py`: `cae.apply_setup_patch` entry in `_INTENT_MAP`.
- `main.py`: module-level constants (`_ALLOWED_PATCH_PREFIXES`,
  `_ALLOWED_PATCH_EXACT`, `_SUPPORTED_PATCH_OPERATIONS`, `_SETUP_STALE_ARTIFACTS`)
  and helpers (`_is_allowed_patch_path`, `_parse_json_pointer`,
  `_json_pointer_get`, `_json_pointer_set`, `_apply_single_patch`,
  `_apply_patches_to_package`, `_compute_stale_artifacts`).
- `main.py`: `_tool_cae_apply_setup_patch` handler — validates all patches
  before applying, applies atomically, refreshes preprocessing summary,
  returns `changed_artifacts`, `stale_artifacts`, `warnings`.
- Supported operations: `create_file`, `replace_json` (with optional JSON
  Pointer RFC 6901 + `before` guard), `merge_object`, `append_array_item`.
- Allowed write targets: `simulation/cae_imports/`, `simulation/load_cases/`,
  `simulation/solver_settings.json`, `simulation/cae_mapping.json`,
  `graph/constraints.json`.
- Rejected: path traversal (`..`), absolute paths, `results/` writes,
  unknown `action_type`, `claims_advanced=true`, `before` mismatch.
- 10 new backend tests; all 60 backend tests pass.

**Explicit non-scope (do not implement in this phase):**
- Real solver execution, mesh generation, VTU/FRD/ODB field parsing.
- Arbitrary CAD mutation or CAD parameter editing.
- Arbitrary Python/shell execution.
- Complex frontend diff UI.

**Depends on:** Phase 14 (preprocessing summary contract).

---

## Phase 19 — FRD Scalar Extraction ✅ Implemented

**Scope:** `aieng` + `aieng-ui`

**Delivers the first real numbers into the evidence pipeline.** Before this phase,
`computed_values.extrema_computed` was always `false` — agents could see CAE
artifact presence but had no actual stress or displacement values to reason about.
Phase 19 closes this gap with a pure-Python CalculiX FRD parser.

**Pattern:** FRD file → parser → per-node DISP/S fields → scalar extrema →
`computed_metrics.json` written into `.aieng` package → `refresh_cae_result_summary`
→ `extrema_computed: true` with real values in the result summary.

**Delivered:**
- `aieng/src/aieng/simulation/frd_result_extractor.py`:
  - `parse_frd(frd_path)` — pure-Python CalculiX FRD text parser; reads DISP and S
    fields as fixed-width 12-char per-value format; handles continuation (-2) lines.
  - `extract_computed_metrics(frd_path, *, load_case_id, software)` — computes max
    total displacement (from ALL component or √(D1²+D2²+D3²)) and max von Mises
    stress (computed from stress tensor Sxx/Syy/Szz/Sxy/Sxz/Syz per node); returns
    `computed_metrics.json`-compatible dict.
  - `write_computed_metrics_package(package_path, frd_path, ...)` — extracts
    metrics and writes `results/computed_metrics.json` into the package atomically.
- `aieng-ui/backend/app/aieng_bridge.py`: `extract_frd_solver_results()` — bridges
  the extractor into the `aieng-ui` runtime.
- `aieng-ui/backend/app/runtime.py`: `cae.extract_solver_results` entry in
  `_INTENT_MAP`.
- `aieng-ui/backend/app/main.py`: `_tool_cae_extract_solver_results` handler +
  registration. Accepts `frdPath`, `loadCaseId`, `software`, `overwrite`,
  `refresh_result_summary`. On success: writes package, optionally refreshes
  result summary, returns `metrics`, `artifacts`, `warnings`.
- 20 new `aieng` unit tests + 3 new `aieng-ui` integration tests; 63 backend tests
  total pass.

**No external dependencies** — FRD parsing uses only the Python standard library.

**Honest limitations:**
- Only DISP and S fields are processed. Other CalculiX output fields (strain,
  reaction forces, contact pressures) are not yet extracted.
- Binary FRD format is not supported; file must be UTF-8 text (default CalculiX output).
- VTU/ODB format parsing is not implemented in this phase.
- No solver execution; the FRD file must already exist.

**Depends on:** Phase 6 (computed_metrics.json schema) + Phase 18 (evidence-grounded
action pattern).

---

## Phase 20A — MCP Wrappers for Phase 18/19 Runtime Tools ✅ Implemented

**Scope:** `aieng_freecad_mcp`

**Goals:**
- Expose `aieng_apply_cae_setup_patch` and `aieng_extract_solver_results` as MCP tools.
- Both delegate entirely to the `aieng-ui` runtime REST API (`start_run` + `wait_for_run`).
- No patching or FRD parsing logic is reimplemented in the MCP layer.
- Preserves existing 13 MCP tools; registry grows to 15.

**Key constraint:** MCP is the agent-facing adapter only. `aieng-ui` runtime remains
the execution layer. Solver execution is not implemented here.

---

## Phase 20B — Solver Execution Preflight / Run Contract ✅ Implemented

**Scope:** `aieng-ui` backend

**Goals:**
- Add `cae.prepare_solver_run` runtime tool.
- Inspects a `.aieng` package and returns a reviewable preflight plan for a future
  CalculiX solver run — without executing anything.
- Checks artifact presence: mesh, solver settings, load case JSON, CalculiX input deck.
- Checks whether a `ccx` executable is on PATH (via `shutil.which`); never runs it.
- Returns `ready_to_run`, `preflight`, `planned_artifacts`, `requires_approval=true`,
  and `solver_execution_performed=false`.
- `planned_artifacts` lists exactly what a real solver run would produce:
  `solver_run.json`, `solver_log.txt`, `outputs/result.frd`, and optionally
  `results/computed_metrics.json` + result summary artifacts.
- Warnings explicitly state no solver execution was performed.

**Honest limitations:**
- No solver execution; no mesh generation; no input deck generation.
- `ccx_available` reflects `shutil.which` only — a found executable is not run.
- `ready_to_run=true` requires all four artifacts AND ccx on PATH; in practice
  `ccx_available` will be `false` on most development machines.
- Physical correctness of the planned run is not checked or guaranteed.

**Depends on:** Phase 18 (evidence-grounded action pattern) + Phase 19 (FRD extraction).

---

## Phase 21 — External CalculiX Solver Execution MVP ✅ Implemented

**Scope:** `aieng-ui` backend

**Goals:**
- Add `cae.run_solver` runtime tool — an external CalculiX execution adapter, not an
  AIENG solver.
- Accept a pre-existing CalculiX input deck (`.inp`) inside the `.aieng` package.
- Validate input deck path (reject path traversal, absolute paths, non-`.inp` files).
- Locate `ccx` via `shutil.which` with fallback names (`ccx_linux`, `ccx2.21`, `ccx_static`).
- Run `ccx` as a subprocess with `shell=False`, argument list, and configurable timeout.
- Capture stdout/stderr, return code, and duration.
- Write artifacts back into the package atomically:
  - `simulation/runs/{run_id}/solver_input.inp`
  - `simulation/runs/{run_id}/solver_log.txt`
  - `simulation/runs/{run_id}/solver_run.json`
  - `simulation/runs/{run_id}/outputs/result.frd`
- `solver_run.json` records: `run_id`, `solver`, `state`, `solved`, `converged`
  (conservatively `null` unless reliable evidence exists), `return_code`, timestamps,
  `input_files`, `output_files`, `log_file`, `warnings`, `errors`.
- Optionally extract FRD scalar results into `results/computed_metrics.json` via the
  existing Phase 19 extraction path.
- Optionally refresh CAE result summary and preprocessing summary.
- Approval-gated (`requires_approval=True`) — the runtime pauses before executing
  the external solver.
- Honest error when `ccx` is unavailable; `solver_execution_performed=false`.
- On timeout: mark run failed, write metadata, return `return_code=-1`.

**Explicit non-scope (do not implement in this phase):**
- Mesh generation.
- Input deck generation.
- FreeCAD FEM full pipeline.
- Field rendering or visualization.
- Physical correctness claims.
- Arbitrary shell commands.

**Backend tests:** 11 new tests covering path traversal rejection, non-`.inp` rejection,
ccx-unavailable error, mocked subprocess success, FRD write-back, FRD extraction call,
summary refresh, timeout handling, introspection, and no mesh generation.

**Honest limitations:**
- Only CalculiX (`ccx`) is supported.
- `converged` is always `null` because CalculiX exit codes alone are not reliable
  evidence of convergence.
- The input deck must already exist inside the package.
- `shell=False` with a fixed argument list; no arbitrary command injection.

**Depends on:** Phase 19 (FRD extraction) + Phase 20B (solver run preflight).

---

## Phase 32 — Real-Environment CAE Demo Readiness ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** Make the approval-gated external CalculiX execution path reproducible
on a developer/reviewer machine without mocks, so the pipeline is demonstrable
end-to-end against real binaries.

**Delivered:**
- `aieng-ui/docs/quickstart-real-ccx.md` — install paths for `ccx` on Windows,
  WSL, and conda-forge; reproducible one-pytest-command smoke test;
  expected PASS / SKIP signals; honest limitations.
- `aieng-ui/backend/tests/fixtures/minimal_cantilever.inp` — committed
  1-element CalculiX deck used as the hand-written fixture.
- `aieng-ui/backend/tests/test_api.py::test_run_solver_real_ccx_skipped_if_unavailable`
  — `pytest.mark.skipif(shutil.which("ccx") is None, ...)`; exercises the real
  subprocess code path through the approval gate; asserts `result.frd` and
  `solver_run.json` are written back into the `.aieng` package with
  `converged: null`.
- `aieng-ui/backend/tests/test_api.py::test_full_real_pipeline_step_to_summary`
  — doubly-gated on `AIENG_TEST_REAL_FREECAD=1` + FreeCADCmd + `ccx`; runs
  `cae.generate_mesh` → in-test deck completion → `cae.run_solver` →
  `postprocess.refresh_cae_summary` with no mocks anywhere; asserts
  `computed_values.extrema_computed: true` with non-zero real stress and
  displacement served by `GET /api/projects/{id}/cae-result-summary`.
- `aieng-ui/README.md` links the quickstart.

**Boundary rules preserved:** real solver output is evidence (`result.frd`,
`computed_metrics.json`), not an engineering claim. `solver_run.json.converged`
remains `null` because CalculiX exit codes are not reliable convergence
evidence. Claim advancement still requires an explicit claim-update workflow.

**Depends on:** Phase 19 (FRD extraction) + Phase 21 (external CalculiX
execution MVP).

---

## Phase 36 — CAD-modification Recommendation Primitive ✅ Implemented

**Scope:** `aieng`

**Goal:** First step toward a closed-loop CAD/CAE Copilot. Given the design
targets, computed metrics, and per-feature stress already produced by Phases
19/21/35, emit a ranked list of CAD-modification proposals an agent can
reason about. Verification (Phase 37) and execution (Phase 38+) are
deliberately out of scope so the recommendation contract can stabilise first.

**Delivered:**
- `aieng/src/aieng/cae_recommendation.py`:
  `generate_cad_modification_recommendations()` reads
  `task/design_targets.yaml`, `results/computed_metrics.json`,
  `results/stress_by_feature.json`, and
  `simulation/cae_imports/parsed_features.json`; returns a structured
  `proposals` block (schema `0.1`).
  `generate_recommendations_markdown()` for the human-/LLM-readable form.
- Modification vocabulary (deliberately small): `thin`, `thicken`,
  `add_fillet`, `resize_hole`, `remove`, `reduce_count`.
- Ranking rules: mass-reduction proposals are ranked by
  `(safety_factor / min_required_sf) × mass_contribution_kg` and refuse
  thin-margin features; stress-rescue proposals trigger only when current
  `minimum_safety_factor < required min_SF`; preserved-interface targets
  remove features from the candidate set entirely.
- CLI: `aieng recommend-cad-modifications <package> [--output text|json]`.
- 13 new tests; validated against the
  `mass_reduction_recommendation` benchmark fixture (top proposal is
  `back_wall thin 20.0 -> 10.0` — the engineered-correct answer).

**Boundary rules preserved:** proposals are *hypotheses*
(`claims_advanced=false`, `requires_verification_simulation=true`),
package is byte-identical after the call, no CAD/CAE execution. Numerical
impact is qualitative scaling, not a solver prediction.

**Next phases:** Phase 37 verification gate (now ✅); Phase 38 closed-loop
orchestrator; Phase 39 explainability surface in `aieng-ui`.

**Depends on:** Phase 19 (FRD extraction) + Phase 21 (CalculiX execution) +
Phase 35 (design-target evidence resource).

---

## Phase 37 — Pre-execution Verification Gate ✅ Implemented

**Scope:** `aieng`

**Goal:** Trust layer between the Phase 36 recommender and a future
execution adapter. Reject obviously-unsafe CAD modification proposals
*before* they touch geometry, surface predicted-risk warnings for review,
and keep re-simulation as the authoritative correctness check.

**Delivered:**
- `aieng/src/aieng/cae_verification.py`:
  `verify_cad_modification_proposal()` (single proposal) +
  `verify_recommendations()` (batch over Phase 36 output) +
  `generate_verification_markdown()`.
- Seven checks across three categories:
  - **Schema** -- `proposal_shape`, `action_in_vocabulary`,
    `feature_exists`, `parameter_change`,
    `preserved_feature_not_modified`. All block on fail.
  - **Manufacturability** -- hard floors on `thickness_mm` (≥ 1.0),
    `diameter_mm` (≥ 2.0), `fillet_radius_mm` (≥ 0.2).
  - **Regression** -- `thinning_sf_floor` predicts post-thinning safety
    factor via `SF_after ≈ SF_before × (t_after / t_before)^2` bending
    heuristic; `thicken_when_unnecessary` warns when SF is already
    above the floor.
- Strictness modes: `lenient` / `default` / `strict`.
- CLI: `aieng verify-cad-modifications <package> [--proposals <json>] `
  `[--strictness lenient|default|strict] [--output text|json]`. Exit
  code 1 if any proposal fails.
- 19 new tests; full Phase 36 + 37 chain validated against the
  `mass_reduction_recommendation` benchmark fixture: the gate passes
  the engineered-correct `back_wall thin 20→10` (predicted SF 3.98) and
  blocks the two over-aggressive `flange` and `reinforcement_gusset`
  thinning proposals.

**Boundary rules preserved:** verification is heuristic and
pre-execution only;
`claim_policy.verification_does_not_replace_resimulation = true`;
geometry-kernel checks explicitly NOT performed (deferred to a future
`aieng_freecad_mcp` Phase 37b); package is byte-identical after the
call; no claim advancement.

**Next phases:** Phase 38 closed-loop orchestrator (now ✅);
Phase 39 explainability panel; Phase 37b geometry-kernel checks.

**Depends on:** Phase 35 (design-target evidence) + Phase 36
(recommendation primitive).

---

## Phase 38 — Closed-loop Copilot Skill + MCP Wrappers ✅ Implemented

**Scope:** `aieng_freecad_mcp` + `aieng-agent-skills`

**Goal:** Bridge the Phase 36 recommendation primitive and Phase 37
verification gate into the agent-facing tool surface, and teach an
agent how to chain them with the existing CAE lifecycle for a bounded,
trust-gated closed loop:
`recommend → verify → execute → re-simulate → compare`.

**Delivered:**
- `aieng_freecad_mcp/src/freecad_mcp/aieng_bridge/recommendation.py`
  and `.../verification.py` -- subprocess bridges to the
  `aieng recommend-cad-modifications` and
  `aieng verify-cad-modifications` CLIs. Handle missing CLI, timeout,
  malformed output honestly (never raise).
- Two new MCP tools registered in `tools_aieng/__init__.py`:
  `aieng_recommend_cad_modifications` and
  `aieng_verify_cad_modifications`, both read-only.
- 14 new tests in `tests/test_recommendation_bridge.py`; full related
  test suite (50 tests) passes.
- New skill `aieng-agent-skills/skills/aieng-closed-loop-copilot/SKILL.md`
  (`skill_version: 0.1.0`): bounded iteration loop with five explicit
  stop conditions (target met, no surviving proposal, no improvement,
  budget exhausted, approval denied); trust-layer reporting; honest
  reporting checklist.

**Architectural decision:** the MCP bridges delegate to the `aieng`
CLI via subprocess (with a `python -m aieng.cli` fallback) rather than
importing the `aieng` Python package directly. Preserves the existing
decoupling between `aieng_freecad_mcp` and `aieng`.

**Boundary rules preserved:**
- MCP bridge is read-only; `claim_policy_meta.claims_advanced = false`.
- Verification verdicts are predictions, not certifications. Target
  satisfaction is only claimed after re-simulation + comparison.
- Solver execution stays approval-gated.
- One change per iteration; predictions are not composable.

**Next phases:** Phase 39 explainability panel (MVP now ✅);
Phase 37b geometry-kernel checks in `aieng_freecad_mcp`.

**Depends on:** Phase 36 (recommendation primitive) + Phase 37
(verification gate).

---

## Phase 39 — Explainability Panel in aieng-ui (MVP) ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** Surface the Phase 36 recommendation list and Phase 37
verification verdicts as a first-class workbench panel. MVP-first:
ship a read-only display of ranked proposals with per-proposal
verdicts + failing check IDs; optimise interactions in a follow-up.

**Delivered:**
- Backend: `GET /api/projects/{project_id}/cad-recommendations`
  endpoint (with optional `?strictness=lenient|default|strict`)
  delegating to a new
  `_generate_cad_recommendations_with_verification` helper that
  imports the Phase 36 + 37 modules via the existing sys.path
  injection pattern.
- 3 new backend tests covering happy path, strictness forwarding,
  and 404 on missing package.
- Frontend: `RecommendationsPanel` component with per-proposal
  cards (rank / feature / action_type / parameter_change /
  confidence / rationale / expected_impact / targets_addressed /
  risks) plus expandable verification-checks detail per proposal.
  Strictness selector + refresh button.
- New `"recommend"` tab in the control pane.
- TypeScript + Vite build passes; 8 related backend tests pass.

**Deferred for Phase 39b:** "apply this proposal" button (now ✅
below), auto-refresh after solver runs, markdown rendering of
`llm_summary`, browser-level UI verification.

**Boundary rules preserved:** endpoint is read-only; embedded
`claim_policy` block keeps `proposals_are_hypotheses`,
`verification_does_not_replace_resimulation`,
`geometry_kernel_checks_not_performed`, and `claims_advanced=false`.

**Next phases:** Phase 39b interaction wiring (now ✅);
Phase 37b geometry-kernel checks.

**Depends on:** Phase 36 + Phase 37 + Phase 38.

---

## Phase 39b — Apply Proposal + Approval Wiring ✅ Implemented

**Scope:** `aieng-ui` (frontend only -- the `cad.edit_parameter`
runtime + approval gate already exist from Phase 18).

**Goal:** Turn the Phase 39 read-only panel into an interactive
Copilot surface. From a proposal card, the user can submit
`cad.edit_parameter`, see the approval-gated run inline, approve or
reject it, and have the recommendation list auto-refresh after a
successful execution.

**Delivered:**
- Per-card **Apply proposal** button. Disabled when the verdict is
  `fail`; for `warn` verdicts requires an explicit checkbox before
  unlocking; for `pass` verdicts applies immediately.
- Apply submits `cad.edit_parameter` via
  `api.startRun("edit cad parameter", project, { featureId, `
  `parameterName, newValue, project_id })` -- planner routes the
  intent string to the approval-gated tool.
- After submission the card swaps the button for an inline run
  status block (status badge, run id, `Approve & execute` and
  `Reject` controls for `awaiting_approval`, error list on failure).
- On approval of a `completed` run the panel auto-refetches
  recommendations so the regenerated proposals from the post-edit
  package replace the old list.
- Per-card state isolation; top-of-panel `Apply error: ...` banner
  for submission failures.
- Scoped CSS for apply/approve/reject/run-status controls.

**Build + tests:** `tsc && vite build` clean; 10 related backend
tests pass (3 Phase 39 endpoint tests + 7 existing
`cad.edit_parameter` tool tests). No backend changes -- Apply
reuses the Phase 18 runtime tool.

**Boundary rules preserved:** approval gate is the existing runtime
mechanism (UI never bypasses); `fail` verdicts cannot be applied
from the UI; auto-refetch is display-only (does not advance claims
or skip re-simulation).

**Still deferred (Phase 39c+):** skip-feature drill-down, markdown
`llm_summary`, SSE/WebSocket auto-refresh on solver-run events,
combined "apply + re-simulate" action.

**Depends on:** Phase 18 (`cad.edit_parameter` runtime + approval
gate) + Phase 39 (read-only panel).

---

---

## Phase 40 — B-Rep Graph Engine ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** Build a CAD-neutral, deterministic symbolic B-Rep graph layer derived from
`geometry/topology_map.json`. Downstream CAD/CAE actions should refer to explicit
face and edge pointers (`@face:face_001`) instead of vague natural language.

**Delivered:**
- `aieng-ui/backend/app/brep_graph.py` — `build_brep_graph_from_topology()`,
  `build_brep_digest()`, `load_or_build_digest()`.
- Pointer syntax: `@face:<id>`, `@edge:<id>`, `@group:<id>`.
- Face roles inferred from topology: `support_candidate`, `load_candidate`,
  `mounting_candidate`, `stiffener`, `stress_relief`.
- Selection groups from feature graph + geometry heuristics (cylindrical patterns).
- Face adjacency relations: explicit (from edge topology) + inferred (bbox-based,
  marked low-confidence).
- Endpoints:
  - `POST /api/projects/{id}/brep-graph/build` — writes `graph/brep_graph.json`,
    `graph/entity_index.json`, `ai/brep_digest.md` atomically.
  - `GET /api/projects/{id}/brep-graph` — read existing artifacts.
- Tests: 4 backend tests covering pointer generation, face adjacency inference,
  endpoint write-back, and contextual-chat transient digest inclusion.

**Boundary rules preserved:**
- No CAD kernel calls, no LLM calls, no mesh/solver execution.
- Inferred adjacency is explicitly marked `virtual: true`, `confidence: low`.
- Pointers are stable only within the current topology snapshot.

**Depends on:** Phase 2 (topology extraction in `.aieng` package).

---

## Phase 41 — AI Preprocessing v2 with B-Rep Context ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** Enrich the AI-driven FEA preprocessing setup generator with B-Rep digest
context so Claude can select supports and loads using explicit face/group pointers
instead of guessing face IDs.

**Delivered:**
- `aieng-ui/backend/app/ai_preprocessing.py` extended:
  - `_build_brep_context()` builds transient B-Rep digest + entity index.
  - `call_claude_for_fea_setup()` receives `brep_digest` in the user prompt.
  - `_validate_and_normalize_fea_setup()` resolves `target_pointers` (`@face:` /
    `@group:`) to actual `target_face_ids` via the entity index.
  - `_fea_setup_to_cae_mapping()` emits `selection_pointer_syntax` and
    `selection_source: brep_pointer` when pointers were used.
  - `run_ai_preprocessing()` optionally writes B-Rep artifacts into the package
    (`write_brep_graph: true` by default).
- Tests: 7 new backend tests covering B-Rep context inclusion, pointer resolution,
  and endpoint behavior with/without topology.

**Boundary rules preserved:**
- Validation still checks all `target_face_ids` against actual topology.
- Unknown materials fallback to `Al6061-T6` with a warning.
- Load values > 1 GN trigger a sanity warning.

**Depends on:** Phase 40 (B-Rep graph engine).

---

## Phase 42 — Simulation Runner (Gmsh + CalculiX) ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** Close the structural CAE execution gap: from `simulation/setup.yaml`
(produced by AI preprocessing) to a real CalculiX solve with FRD results, all
triggerable from the chat UI.

**Delivered:**
- `aieng-ui/backend/app/simulation_runner.py`:
  - `check_simulation_tools()` — detects Gmsh (`import gmsh`) and CalculiX
    (`shutil.which ccx`).
  - `_mesh_with_gmsh()` — meshes STEP geometry, exports CalculiX-format `.inp`.
  - `_build_nsets()` — maps `cae_mapping.json` face IDs to mesh node IDs via
    topology bounding-box + normal-vector heuristics (cylinder, plane, fallback).
  - `_build_calculix_deck()` — assembles full `.inp` deck with material, BCs,
    loads, and output requests.
  - `_run_calculix()` — subprocess execution with timeout, stdout/stderr capture.
  - `_diagnose_solver_log()` — actionable failure-pattern detection (singular,
    divergence, empty element sets, etc.).
  - `run_simulation()` — synchronous orchestration; writes
    `simulation/solver_log.txt`, `simulation/result.frd`, `simulation/mesh.inp`,
    `simulation/results_summary.json` atomically.
  - `run_simulation_stream()` — SSE streaming variant with progress events:
    checking_tools → meshing → building_nsets → solving → parsing → done | error.
- Endpoints:
  - `POST /api/projects/{id}/run-simulation` (requires `confirmed=true`).
  - `POST /api/projects/{id}/run-simulation-stream` (SSE).
  - `GET /api/simulation/tools`.
- Results summary includes `von_mises_max_mpa`, `displacement_max_mm`, `node_count`,
  `bc_count`, `load_count`, and a `verdict` block from post-processing.

**Boundary rules preserved:**
- Approval-gated (`confirmed=true` required).
- Graceful degradation when tools are missing (`status: tools_unavailable`).
- `converged` is not claimed; solver exit code alone is not reliable evidence.
- Empty NSETs and zero BC/load counts are honestly reported as warnings.

**Depends on:** Phase 41 (AI preprocessing with setup.yaml output) +
Phase 19 (FRD scalar extraction in `aieng`).

---

## Phase 43 — Post-processing Intelligence ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** After simulation, automatically interpret σ_max / u_max against design
targets, compute Factor-of-Safety, flag pass/fail, and emit specific engineering
suggestions — all embedded in the simulation results summary.

**Delivered:**
- `aieng-ui/backend/app/post_processing.py`:
  - `compute_fos()` — static FoS = yield_strength / σ_max; ratings: safe (≥2.0),
    marginal (≥1.0), critical (<1.0).
  - `_fos_advisory()` — number-rich advisory for marginal/critical ratings:
    material alternatives that achieve FoS ≥ 2.0, load-reduction percentage,
    geometry tips.
  - `interpret_results()` — compares solver metrics against design targets;
    returns `overall` (pass/fail/partial/no_targets/unknown), per-item status,
    and engineering suggestions.
  - Material yield-strength catalog covering Al, Steel, Ti, Cast Iron, Nylon, PETG.
- Integrated into `simulation_runner.py`: verdict is computed automatically after
  a successful solve and stored in `simulation/results_summary.json`.
- Tests: 8 new backend tests covering FoS computation, advisory generation,
  target comparison, and suggestion deduplication.

**Boundary rules preserved:**
- Suggestions are hypotheses, not certified engineering decisions.
- Missing design targets produce `overall: no_targets` with an honest explanation.
- No claim advancement; verdict is evidence metadata only.

**Depends on:** Phase 42 (simulation runner producing σ_max / u_max) +
Phase 35 (design targets as first-class package resource).

---

## Phase 44 — Stress Heatmap GLB Visualization ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** Replace the synthetic `y_normalized` colormap in the Three.js viewer with
real per-node Von Mises stress coloring from the CalculiX FRD file.

**Delivered:**
- `aieng-ui/backend/app/stress_heatmap.py`:
  - `_parse_frd_vm_stress()` — pure-Python FRD parser for STRESS and DISP blocks;
    handles fixed-width 12-char columns including consecutive negative values.
  - `_parse_inp_mesh()` — parses Gmsh `.inp` node coordinates and C3D4/C3D10
    tetrahedra.
  - `_extract_surface_triangles()` — extracts surface faces via tet-face counting.
  - `_thermal_color()` — blue→cyan→green→yellow→red colormap.
  - `_build_glb()` — constructs binary GLB (glTF 2.0) with POSITION + COLOR_0.
  - `generate_heatmap_glb()` — public API returning `(glb_bytes, min_mpa, max_mpa)`.
- Endpoint: `GET /api/projects/{id}/stress-heatmap` — returns
  `Content-Type: model/gltf-binary` with `X-Stress-Min-Mpa` and `X-Stress-Max-Mpa`
  headers. Returns 409 if mesh or FRD are not yet present.
- Frontend: toggle button in CAE panel swaps the Three.js viewer between CAD
  preview and stress heatmap.
- Tests: 15 tests covering thermal colormap, FRD parsing (including negative
  consecutive values), INP mesh parsing, surface extraction, and GLB validity.

**Boundary rules preserved:**
- No solver execution; requires pre-existing `simulation/mesh.inp` and
  `simulation/result.frd`.
- Falls back to displacement magnitude if no STRESS block is found.
- Binary FRD format is not supported (text only).

> **Retired (#246).** This `stress_heatmap.py` module and the
> `GET /api/projects/{id}/stress-heatmap` GLB route were removed. The route was
> never consumed by the frontend, and the canonical in-viewer path
> (`GET /api/projects/{id}/fields/{name}` → per-vertex coloring with legend,
> field picker, probe, and colormap controls) now owns stress/displacement
> visualization — leaving a single stress-color code path.

**Depends on:** Phase 42 (simulation runner writing mesh.inp and result.frd).

---

## Phase 45 — Contextual Engineering Chat ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** Provide an engineering chat that is grounded in the actual project state
(geometry, FEA setup, simulation results, design targets) so the LLM can answer
questions accurately without inventing numbers.

**Delivered:**
- `aieng-ui/backend/app/contextual_chat.py`:
  - `build_context_block()` — reads `.aieng` package and formats a structured
    context block: geometry summary, B-Rep digest, FEA setup, simulation results
    with verdict, and design targets.
  - `chat_with_context()` — calls Claude with the context-injected system prompt;
    returns `{"reply", "context_used", "project_id"}`.
- System prompt instructs the LLM to:
  - Be concise and specific, referencing actual numbers.
  - Suggest actionable changes for failed targets.
  - Tell the user what chat commands trigger which tools
    ("generate ...", "set up FEA ...", "run simulation", etc.).
  - Answer in the same language the user writes in.
- Endpoint: `POST /api/projects/{id}/contextual-chat` — body:
  `{ message, history?, api_key? }`.
- No external tool calls; pure read-only chat.

**Boundary rules preserved:**
- Read-only; does not execute solvers, edit CAD, or mutate packages.
- Context block is rebuilt from the package on every turn; no stale caching.
- If no package exists, the chat degrades gracefully with "No project package loaded."

**Depends on:** Phase 40 (B-Rep digest for geometry context) +
Phase 42/43 (simulation results and verdict for grounded answers).

---

## Phase 46 — Engineering Action Plan ✅ Implemented

**Scope:** `aieng-ui`

**Goal:** Centralize the first-pass intent/action decision for chat-first CAD/CAE
workflows so the frontend can stop relying on brittle ad-hoc keyword ordering.

**Delivered:**
- `aieng-ui/backend/app/engineering_action_plan.py`:
  - `classify_engineering_message()` — priority-ordered intent detection:
    `simulate` > `set_target` > `change_material` > `refine_mesh` > `preprocess` >
    `generate` > `refine`.
  - `_extract_inputs()` — extracts material hints and mesh size from natural
    language (e.g. "refine mesh to 1.25 mm" → `mesh_size_mm: 1.25`).
  - `_action_for_intent()` — returns typed action metadata: `id`, `endpoint`,
    `approval_tier` (auto / confirm / gate), `writes`, `external_tools`.
  - `build_engineering_action_plan()` — inspects project package state
    (has_generated_cad, has_setup, has_results) to contextualize intent ranking.
- Endpoint: `POST /api/projects/{id}/engineering-action-plan` — body:
  `{ message }`; returns read-only action candidate with confidence and reason.
- Frontend `ChatPanel.tsx` now delegates intent detection to the backend endpoint
  instead of local keyword heuristics.
- Tests: 6 tests covering priority ordering, material/mesh extraction, and
  endpoint package-state awareness.

**Boundary rules preserved:**
- Read-only; never executes tools or mutates packages.
- `candidate_only: true`; `must_not_auto_execute_external_tools: true` for
  simulation intents.
- Approval tiers are honest: `gate` for solver execution, `confirm` for CAD/FEA
  generation, `auto` for target setting.

**Depends on:** Phase 35.1 (Natural Language Intent Planner foundation).

---

## Future — Real Field Serving + Multi-CAD Adapter

The two largest remaining gaps in the vertical pipeline:

**Real per-node field serving.** `aieng-ui` backend `GET /api/projects/{id}/fields/{field_name}`
currently returns a synthetic descriptor; the frontend Three.js colormap uses
`y_normalized`. Real per-node scalar data (JSON array or VTK reference) sourced
from the FRD would replace the synthetic visualization and let
`cae.results_available` reflect actual solver output instead of evidence
metadata presence. Mesh generation and CalculiX execution adapters already
exist (`cae.generate_mesh`, `cae.run_solver`) so this is now a viewer/transport
concern, not a missing adapter.

**Multi-CAD adapter strategy.** `aieng-ui` ships a pluggable `CadProvider`
interface; FreeCAD is the first implementation. Adding a second provider
(e.g. pythonOCC, CadQuery) would validate that the interface isolates
CAD-specific code. Vendor-specific SDKs (SolidWorks, Fusion 360, Onshape) are
deferred until the interface is exercised by at least one non-FreeCAD provider.

---

## Out of Scope (Intentional Boundaries)

These are not on the roadmap because they would violate the design invariants:

| Item | Why out of scope |
|------|-----------------|
| Full CAD geometry kernel | `.aieng` is the artifact contract; a new kernel is a different product |
| Autonomous agent driving the browser DOM | Agents call the runtime layer, not the UI |
| Automatic claim advancement from solver output | Claims are explicit and evidence-backed only |
| LLM inference inside the service layer | The service layer calls LLM-capable tools; it does not host a model |
| Topology-changing CAD edits | Marked as unsupported; face-ID stability not guaranteed |
| Automatic BC/load remapping after geometry change | `needs_review` flag is the correct response |
