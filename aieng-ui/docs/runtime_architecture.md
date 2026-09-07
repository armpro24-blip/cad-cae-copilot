# aieng local runtime — architecture note

## Why chat is treated as an orchestration layer

The chat UI is not a chatbot. It is a natural-language entry point into a
structured engineering workbench. Each user message is parsed into a plan of
discrete, auditable tool calls. Responses carry structured plan steps, event
timelines, and error records — not free-form text.

This design makes the system debuggable, replayable, and connectable to
external agents (Claude Code, Codex, MCP clients) without retrofitting.

---

## Why API calls are only one tool adapter

The previous design called backend REST endpoints directly from the
orchestration layer. That conflates the transport layer with the business
logic layer. The runtime treats the backend API as *one adapter among many*:

```
Web UI / Chat UI
        │
        ▼
aieng local runtime          ← this module (backend/app/runtime.py)
        │
        ├── aieng tools       ← wraps existing package_summary / validate / convert
        ├── audit tools       ← wraps existing write_audit_log / recent_logs
        ├── cad tools         ← build123d/OCP agent modelling and parameter edits
        ├── cae tools         ← setup, solver input, CalculiX execution, postprocess
        └── optional adapters ← external CAD runtimes such as FreeCADCmd
```

The runtime module (`backend/app/runtime.py`) has *no imports from main.py*.
Tool handlers are registered at app startup via closures that capture the
active `Settings` instance. This keeps the dependency graph one-directional.

---

## CAD provider direction

The default CAD runtime is build123d/OCP. Agent-authored modelling flows call
`cad.execute_build123d`, write `.aieng` package artifacts, and expose named
parts plus topology through the CAD-neutral package graph.

FreeCAD is no longer the default runtime provider. It can still be kept as an
optional external adapter for shops that need FreeCADCmd-specific import,
preview, or legacy bridge workflows. If that adapter is re-enabled, four
integration paths are viable; pick the one that fits the deployment
environment:

| Path | How | When to use |
|------|-----|-------------|
| **A — FreeCAD Python API** | `import FreeCAD` inside a FreeCAD-hosted subprocess via `FreeCADCmd --run script.py` | Simplest; works when FreeCADCmd is on PATH |
| **B — Headless subprocess** | Spawn `FreeCADCmd --run macro.py`, capture stdout/stderr | Good for stateless one-shot operations |
| **C — Local socket bridge** | POST to `freecad-mcp` running on `localhost:PORT` | Best for interactive sessions; freecad-mcp already exists in this repo |
| **D — Workbench extension** | Named pipe or stdout capture from a running FreeCAD GUI instance | Needed for UI-driven workflows |

Any future FreeCAD macro or external-CAD mutation should remain approval-gated.
The runtime executor must pause and emit an `approval_required` event before
any external CAD subprocess mutates package state or a desktop document.

---

## How MCP can wrap runtime tools later

The runtime's tool registry is a flat dict. Wrapping it as an MCP server
requires only a thin adapter:

```
Claude Code / Codex
        │  (MCP protocol)
        ▼
aieng MCP server             ← future: backend/app/mcp_server.py
        │
        ▼
aieng local runtime          ← backend/app/runtime.py  (already exists)
        │
        ├── aieng.inspect_package
        ├── aieng.refresh_semantics
        ├── aieng.generate_preview
        ├── aieng.read_audit_log
        ├── cad.execute_build123d  (approval-gated)
        ├── cad.edit_parameter     (approval-gated)
        ├── cad.critique
        ├── cae.apply_setup_patch
        ├── cae.extract_solver_results
        ├── cae.prepare_solver_run
        ├── cae.generate_solver_input
        ├── cae.run_solver         (approval-gated)
        └── cae.write_mesh_handoff
```

Each tool in `_REGISTRY` maps directly to one MCP tool definition. The MCP
server would iterate `runtime.registered_tool_names()`, expose them as MCP
tools, and forward calls to `runtime.execute_run()` (or call handlers
directly for single-tool requests).

The approval gate (`requires_approval=True`) maps naturally to MCP's
human-in-the-loop confirmation pattern.

---

## Chat transcript events

The chat transcript is now driven by append-only event rows as well as legacy
chat messages. User and assistant compatibility text remains in `chat_messages`;
fine-grained agent progress lives in `agent_events`:

```
agent_events
  event_id      unique idempotency key
  run_id        Autopilot run id
  project_id    project scope
  session_id    chat session scope
  type          agent_message | tool_started | tool_completed | ...
  status        running | done | failed | approval | queued
  content       short display text
  payload_json  raw structured payload for collapsed details
  created_at    stable replay order
```

The frontend loads both sources on session open, maps them through
`frontend/src/app/chatTranscript.ts`, and renders compact rows from
`frontend/src/components/chat/`. `autopilot_update` remains a snapshot fallback
for older runs or backends that do not publish typed events.

Approval and conversation are intentionally separate API concepts:

- `POST /api/agent/autopilot/runs/{run_id}/continue` approves or rejects.
- `POST /api/agent/autopilot/runs/{run_id}/reply` sends normal user text or an
  approval revision request.
- `POST /api/agent/autopilot/runs/{run_id}/follow-up` queues text while a tool
  or adapter step is running.

---

## What is implemented now (Phase 0 + Phase 1 + Phase 2 + Phase 2.5)

| Component | Status |
|-----------|--------|
| `RunRecord` / `ToolCall` / `ToolResult` / `RuntimeEvent` models | ✅ |
| `ToolError` structured error payload | ✅ |
| File-backed run persistence (`data/runtime/runs/`) | ✅ configurable via `AIENG_RUNTIME_STATE_DIR` |
| In-memory + disk run store; reloads on restart | ✅ |
| Intent-based plan builder | ✅ |
| `execute_run()` with event emission and approval gate | ✅ |
| `resume_run()` — executes pending tool after approval | ✅ |
| `reject_run()` — marks run rejected, tool not executed | ✅ |
| Statuses: `pending`, `running`, `completed`, `failed`, `awaiting_approval`, `rejected`, `cancelled` | ✅ |
| `aieng.inspect_package` tool | ✅ wraps `package_summary()` |
| `aieng.refresh_semantics` tool | ✅ wraps `validate_aieng_file()` |
| `aieng.generate_preview` tool | ✅ wraps `convert_asset()` |
| `aieng.read_audit_log` tool | ✅ wraps `recent_logs()` |
| `cad.execute_build123d` tool | ✅ runs caller-supplied build123d code, writes STEP/STL/GLB/topology/feature graph artifacts. Approval-gated. |
| `cad.edit_parameter` tool | ✅ text-replaces named build123d constants and re-executes the stored source. Approval-gated. |
| `cad.critique` tool | ✅ deterministic engineering audit over build123d-generated package semantics |
| `cae.generate_solver_input` tool | ✅ writes CalculiX input deck from package setup artifacts |
| `cae.write_mesh_handoff` tool | ✅ writes an external mesher handoff contract; no mesher execution |
| `ToolResult.artifacts` hoisting | ✅ `_execute_steps()` extracts `artifacts` list from tool output dict |
| Per-project artifact audit log | ✅ package mutations append structured audit events |
| `POST /api/runtime/runs` endpoint | ✅ |
| `GET /api/runtime/runs` endpoint | ✅ listing (slim summaries, up to 50) |
| `GET /api/runtime/runs/{id}` endpoint | ✅ |
| `GET /api/runtime/runs/{id}/events` endpoint | ✅ |
| `POST /api/runtime/runs/{id}/approve` endpoint | ✅ resumes awaiting_approval run |
| `POST /api/runtime/runs/{id}/reject` endpoint | ✅ rejects awaiting_approval run |
| `GET /api/runtime/tools` endpoint | ✅ tool registry introspection |
| Tool `description` field in registry | ✅ |
| Audit log on each run + approval/rejection events | ✅ |
| Frontend approve/reject buttons (conditional on awaiting_approval) | ✅ |
| Frontend events shown as plan steps in chat | ✅ |
| Frontend CAD result summary line | ✅ compact human-readable output for build123d/model-generation results |
| Frontend artifact changed-files section | ✅ `Changed files:` block from `ToolResult.artifacts` |
| Runtime audit event log (`audit/events.jsonl`) | ✅ append-only JSONL inside ZIP; `geometry_modified`, `solver_run_completed`, `cae_summary_refreshed` events |
| `GET /api/projects/{id}/audit-events` endpoint | ✅ read-only; returns events in append order |
| Package artifact manifest | ✅ on-demand classification of all ZIP members by kind/category; freshness context from revalidation status |
| `GET /api/projects/{id}/artifact-manifest` endpoint | ✅ read-only; path-pattern catalog covers 9 categories |
| Evidence lifecycle rollup | ✅ `GET /api/projects/{id}/evidence-lifecycle`; summarizes current/stale/unsupported/claim-supporting/missing evidence without mutating the package |
| Package consistency diagnostics | ✅ 5 checks: evidence paths, audit refs, field summary sources, revalidation consistency, claim map absence |
| `GET /api/projects/{id}/package-consistency` endpoint | ✅ read-only; status rollup `ok`/`warning`/`error`; stale state is warning not error |
| Runtime metadata contracts | ✅ contract helper assertions for all metadata shapes; 11 tests covering all major endpoints |
| Explicit claim proposal skeleton | ✅ `POST /api/projects/{id}/claim-proposals`; writes `claims/proposals/{id}.json`; `claim_proposal_created` audit event; manifest classification; consistency check F |
| Claim proposal inspection APIs | ✅ `GET /api/projects/{id}/claim-proposals` (list) + `GET /api/projects/{id}/claim-proposals/{proposal_id}` (read); read-only; sorted by `created_at`; `claim_advancement: "none"` on all responses |
| Evidence reference resolver | ✅ `_resolve_evidence_reference` pure helper; `GET /api/projects/{id}/evidence-references/resolve?path=...`; stale warning when `requires_revalidation=True`; reused in `create_claim_proposal` |
| Claim proposal support packets | ✅ `GET /api/projects/{id}/claim-proposals/{proposal_id}/support-packet`; aggregates proposal + resolved evidence + audit events; `stale_evidence_count`, `missing_evidence_count`, `evidence_warnings`; read-only |
| Review readiness diagnostics | ✅ `review_readiness` field in support packet; 5 checks (A–E); rollup `blocked > warning > ready`; `claim_advancement: "none"` |
| Package semantics taxonomy | ✅ [`docs/package_semantics.md`](package_semantics.md) — canonical definitions for all concepts; six core principles; lifecycle flow diagram |

**Optional FreeCAD adapter files:**
- `backend/app/providers/freecad_preview.py` — minimal STEP preview provider through FreeCADCmd when `provider=freecad`.
- Legacy `legacy/aieng-freecad-mcp` docs remain useful as integration notes, but they are not required for the default build123d runtime.

---

## Stale-state handling after geometry edits

When `cad.edit_parameter` (or any geometry-modifying tool) succeeds, it writes
`state/revalidation_status.json` into the `.aieng` package. This artifact marks
all downstream CAE results as stale relative to the current geometry and
carries a lightweight geometry revision counter for freshness tracking.

### What the artifact contains

```json
{
  "schema_version": "0.2",
  "geometry_modified": true,
  "requires_revalidation": true,
  "reason": "geometry_changed",
  "triggering_tool": "cad.edit_parameter",
  "affected_artifacts": ["results/result_summary.json", "…"],
  "affected_domains": ["result_summary", "field_summaries", "solver_outputs"],
  "claim_advancement": "none",
  "recorded_at": "…",
  "current_geometry_revision": 2,
  "last_validated_geometry_revision": 1,
  "stale_since_geometry_revision": 2,
  "validated_by_run_id": null
}
```

After a successful solver run the artifact looks like:

```json
{
  "schema_version": "0.2",
  "geometry_modified": false,
  "requires_revalidation": false,
  "reason": "solver_rerun_completed",
  "triggering_tool": "cae.run_solver",
  "affected_artifacts": [],
  "affected_domains": ["result_summary", "field_summaries", "solver_outputs"],
  "claim_advancement": "none",
  "recorded_at": "…",
  "current_geometry_revision": 2,
  "last_validated_geometry_revision": 2,
  "stale_since_geometry_revision": null,
  "validated_by_run_id": "run_001"
}
```

### Geometry revision counter

`current_geometry_revision` is a monotonically incrementing integer that
advances by 1 on every successful `cad.edit_parameter` call. It is stored
inside the artifact — there is no separate counter file.

`last_validated_geometry_revision` records the revision at which the most
recent successful solver run completed. Results are **fresh** when
`current == last_validated`; they are **stale** when `current > last_validated`.

This is provenance/freshness metadata, not a version graph. No geometry
hashing is performed. The counter is zero-based (0 before any recorded edit).

When no `state/revalidation_status.json` exists (package created before
revision tracking, or no edits performed), the API returns:
- `current_geometry_revision: 0`
- `last_validated_geometry_revision: null`
- `requires_revalidation: false`

### State transitions

| Event | `requires_revalidation` | `current_geometry_revision` | `last_validated_geometry_revision` |
|-------|------------------------|-----------------------------|-------------------------------------|
| No artifact (initial state) | `false` | 0 | `null` |
| `cad.edit_parameter` (real executor) | `true` | N + 1 | previous validated or `null` |
| `cae.run_solver` (`return_code == 0`) | `false` | unchanged | set to `current` |

`cad.edit_parameter` in stub/mock mode does **not** write the status —
geometry was not actually modified.

### Where stale state is visible in the API

Every CAE result read endpoint injects a `revalidation_status` key via
`_build_revalidation_response(_read_revalidation_status(package_path))`:

| Endpoint | `revalidation_status` field |
|----------|-----------------------------|
| `GET /api/projects/{id}/cae-result-summary` | top-level key |
| `GET /api/projects/{id}/cae-result-fields` | top-level key |
| `GET /api/projects/{id}/cae-result-fields/{name}` | top-level key |

### What stale state means — and does not mean

**Means:** `current_geometry_revision > last_validated_geometry_revision`.
Existing CAE result artifacts were produced from an earlier geometry state.

**Does not mean:** The old results are deleted or invalid as historical evidence.
All prior solver outputs, summaries, and evidence index entries remain in the
package — they are auditable evidence from a prior geometry state.

**Does not mean:** Any engineering claim is automatically advanced or
invalidated. `claim_advancement: "none"` appears in the artifact and in every
API response. Engineering claims require an explicit claim-update workflow — a
separate, deliberate step.

### Revalidation workflow

1. `cad.edit_parameter` → geometry modified; `current_geometry_revision` incremented; `requires_revalidation: true`
2. `cae.write_mesh_handoff` or an external mesher → produce mesh evidence for the new geometry
3. `cae.generate_solver_input` → write a fresh CalculiX deck from current setup artifacts
4. `cae.run_solver` → rerun solver; on `return_code == 0`: `last_validated_geometry_revision = current`; `requires_revalidation: false`
5. `postprocess.refresh_cae_summary` → update result summary and evidence index

Only after step 4 do `current_geometry_revision == last_validated_geometry_revision`.

---

## Runtime capability profile

`GET /api/runtime/capabilities` returns a static, machine-readable profile of
every tool the workbench knows about. It is the authoritative contract for
tooling, agents, and integrations that need to reason about what the workbench
can do — without having to run anything.

### What the profile contains

```json
{
  "schema_version": "0.1",
  "generated_at": "…",
  "environment": {
    "ccx_available": true,
    "freecad_available": false
  },
  "tools": [
    {
      "name": "cae.run_solver",
      "implemented": true,
      "available": true,
      "registered": true,
      "requires_approval": true,
      "writes_artifacts": true,
      "artifact_paths": ["simulation/runs/{run_id}/solver_run.json", "…"],
      "produces_evidence": true,
      "modifies_geometry": false,
      "requires_revalidation": false,
      "advances_claims": false,
      "external_binary": "ccx",
      "external_binary_env_var": null
    }
  ],
  "result_fields": {
    "supported": ["displacement", "stress"],
    "produces_evidence": false,
    "advances_claims": false
  },
  "claim_policy": {
    "automatic_claim_advancement": false,
    "claim_advancement_requires_explicit_workflow": true
  }
}
```

### Implementation vs. availability

`implemented: true` means the tool exists in the registry and its handler
is wired up. It does not mean the external binary is present on the current
host.

`available: bool` is resolved at request time from the real environment:
`shutil.which("ccx")` for CalculiX, `settings.freecad_cmd.exists()` for
FreeCAD. A tool can be `implemented: true` and `available: false` — this is
the normal state on a machine where the binary is not installed.

Do not conflate availability with implementation. An agent checking whether
a workflow is executable must inspect `available`, not `implemented`.

### Evidence and claim semantics

`produces_evidence: true` means the tool writes artifacts into the `.aieng`
package that can be audited. It does **not** mean the tool validates an
engineering claim.

`advances_claims: false` appears on every entry. Engineering claims require
an explicit claim-update workflow — a separate, deliberate step that is not
part of solver execution, summary refresh, or capability introspection.

`claim_policy.automatic_claim_advancement: false` is the package-level
machine-readable guarantee. Any integration that reads this profile and
advances claims automatically is violating the contract.

### `registered` field

`registered: bool` reflects whether the tool name appears in the live
`_REGISTRY` at call time. For HTTP endpoints such as `cae-result-fields`
(which are not runtime tools), `registered` is `false` — this is expected.

---

## Runtime audit event log

Every tool call that modifies the `.aieng` package appends a structured
event to `audit/events.jsonl` inside the ZIP. The file is append-only JSONL
— one JSON object per line, no deletions, no reordering.

### What is recorded

| Tool | `event_type` |
|------|-------------|
| `cad.edit_parameter` (real executor only) | `geometry_modified` |
| `cae.run_solver` (on `return_code == 0`) | `solver_run_completed` |
| `postprocess.refresh_cae_summary` (on `status == "ok"`) | `cae_summary_refreshed` |

### Event schema

```json
{
  "schema_version": "0.1",
  "event_id": "<uuid hex>",
  "timestamp": "<ISO-8601 UTC>",
  "tool": "cad.edit_parameter",
  "event_type": "geometry_modified",
  "status": "completed",
  "artifacts_written": ["geometry/part.step", "state/revalidation_status.json"],
  "evidence_created": [],
  "state_changes": {
    "requires_revalidation": true,
    "current_geometry_revision": 2
  },
  "geometry_revision": 2,
  "revalidation_status": "stale",
  "claim_advancement": "none"
}
```

`claim_advancement: "none"` appears on every event — the audit log records
what happened, not what was validated. Engineering claims require an explicit
claim-update workflow.

### Reading the log

```bash
curl http://localhost:8000/api/projects/{project_id}/audit-events
```

Returns:

```json
{
  "schema_version": "0.1",
  "project_id": "...",
  "events": [...],
  "count": 3,
  "claim_advancement": "none"
}
```

Events are returned in append order (oldest first). The endpoint is
read-only; it never writes to the package.

### Audit log vs. engineering claims

The audit log answers: *which tool ran, when, which artifacts changed, and
what state transitions occurred*. It does **not** advance or validate
engineering claims. `ai/claim_map.json` and `results/claim_map.json` are
never written by audit log writes.

### Non-critical writes

All `_append_audit_event_to_package` calls are wrapped in `try/except`.
A failure to write the audit event does not fail the primary tool operation
— the audit log is provenance metadata, not a hard dependency.

---

## Package artifact manifest

`GET /api/projects/{id}/artifact-manifest` generates an on-demand, read-only
inventory of all artifact files inside the `.aieng` package ZIP. No writes
occur; the manifest is computed from the live ZIP namelist and the current
revalidation status.

### What the manifest contains

```json
{
  "schema_version": "0.1",
  "generated_at": "...",
  "claim_advancement": "none",
  "requires_revalidation": false,
  "current_geometry_revision": 2,
  "artifact_count": 9,
  "artifacts": [
    {
      "path": "audit/events.jsonl",
      "kind": "audit_log",
      "category": "audit",
      "exists": true,
      "claim_advancement": "none"
    },
    {
      "path": "results/evidence_index.json",
      "kind": "evidence_index",
      "category": "evidence_index",
      "exists": true,
      "producer_tool": "postprocess.refresh_cae_summary",
      "evidence_role": "cae_evidence_catalog",
      "requires_revalidation": false,
      "geometry_revision": 2,
      "claim_advancement": "none"
    }
  ]
}
```

### Artifact categories

| Category | Paths |
|----------|-------|
| `state` | `state/revalidation_status.json` |
| `audit` | `audit/events.jsonl` |
| `summary` | `results/result_summary.json`, `results/postprocessing_summary.md` |
| `field_summary` | `results/fields/displacement.summary.json`, `results/fields/stress.summary.json` |
| `evidence_index` | `results/evidence_index.json` |
| `solver_output` | `results/computed_metrics.json`, `simulation/runs/*/solver_run.json`, `simulation/runs/*/outputs/*.frd` |
| `mesh` | `simulation/mesh/*.inp`, `simulation/mesh/mesh_metadata.json` |
| `geometry` | `geometry/*.step`, `geometry/*.stp`, `geometry/*.iges` |
| `package` | `manifest.json`, `metadata.json` |
| `unknown` | anything not matched by the catalog |

### Freshness context

Artifacts in `solver_output`, `summary`, `field_summary`, and `evidence_index`
categories carry `requires_revalidation` and `geometry_revision` fields
derived from `state/revalidation_status.json`. When the geometry is stale,
these fields reflect the stale state so agents and tests can detect it
without parsing the revalidation status directly.

### Manifest vs. claims

The manifest answers: *what artifacts exist, what kind they are, and whether
they are fresh relative to the current geometry*. It does **not** advance or
validate engineering claims. `claim_advancement: "none"` appears on every
entry and at the top level. `ai/claim_map.json` and `results/claim_map.json`
are never written by manifest generation.

### Implementation note

The manifest is generated on demand from the ZIP namelist — it is not written
back into the package. `ARTIFACT_MANIFEST_PATH = "manifest/artifacts.json"` is
reserved as the canonical in-package path for future use, and any path equal
to it is excluded from the artifact listing to avoid self-referential entries.

---

## Evidence lifecycle rollup

`GET /api/projects/{id}/evidence-lifecycle` builds a read-only lifecycle view
over the same package artifacts. It is intended for Mission Control, reports,
and MCP agents that need to answer two questions quickly:

- What evidence exists, and is it current or stale?
- What evidence supports a draft claim-like statement, and what evidence is
  missing or unusable?

The endpoint never writes package members, never runs CAD/CAE tools, never
executes a solver, and never advances engineering claims.

### Lifecycle states

| State | Meaning |
|-------|---------|
| `current` | Artifact exists and is not stale or unsupported according to current package metadata |
| `stale` | Artifact exists but comes from a geometry state that requires revalidation |
| `unsupported` | Artifact exists but cannot be used by the current viewer/postprocessor path, for example a binary/non-UTF-8 FRD |
| `claim_supporting` | Artifact is referenced by at least one draft claim proposal |
| `missing` | Expected evidence such as topology, feature graph, CAE setup, solver deck, result summary, or evidence index is absent |

`claim_supporting` is an overlay state: an artifact can be both stale and
claim-supporting. This is deliberate. Draft claim proposals can point at stale
evidence so reviewers can see the problem, but the lifecycle view makes the
staleness explicit.

Unsupported evidence is reported separately from normal solver-result
availability. For example, a package may contain `result.frd` while the field
viewer still reports it as unsupported because the FRD is binary or non-UTF-8.
That is not treated as claim-supporting evidence in the lifecycle rollup.

### Response shape

```json
{
  "schema_version": "0.1",
  "project_id": "...",
  "claim_advancement": "none",
  "status": "warning",
  "summary": {
    "current": 8,
    "stale": 2,
    "unsupported": 1,
    "claim_supporting": 1,
    "missing": 3
  },
  "governance": {
    "automatic_claim_advancement": false,
    "claim_advancement_requires_explicit_review": true,
    "stale_evidence_may_support_draft_proposals_only": true,
    "unsupported_evidence_is_not_claim_support": true
  }
}
```

The rollup is intentionally advisory. `status: "warning"` means the package has
review issues such as stale, unsupported, or missing evidence; it is not an
engineering pass/fail result.

---

## Package consistency diagnostics

`GET /api/projects/{id}/package-consistency` runs a set of read-only checks
on the package metadata layers and returns a diagnostic report. It never
writes to the package, never executes solvers, and never advances claims.

### What the response looks like

```json
{
  "schema_version": "0.1",
  "project_id": "...",
  "status": "ok",
  "claim_advancement": "none",
  "checks": [
    {
      "id": "evidence_paths_exist",
      "status": "ok",
      "message": "All 6 evidence-indexed path(s) confirmed present."
    },
    {
      "id": "revalidation_status_consistency",
      "status": "warning",
      "message": "Geometry modified; CAE results may be stale. Re-run solver to revalidate.",
      "details": {
        "current_geometry_revision": 3,
        "last_validated_geometry_revision": 2,
        "requires_revalidation": true
      }
    }
  ]
}
```

`status` at the top level is the highest severity across all checks:
`"error"` > `"warning"` > `"ok"`.

### Checks performed

| Check ID | What it validates |
|----------|-------------------|
| `evidence_paths_exist` | Evidence index entries marked `exists=true` are present in the ZIP |
| `audit_artifact_references` | Internal artifact paths referenced in `audit/events.jsonl` are present |
| `field_summary_source_displacement` | `results/fields/displacement.summary.json` source artifact exists |
| `field_summary_source_stress` | `results/fields/stress.summary.json` source artifact exists |
| `revalidation_status_consistency` | Revalidation status fields are internally consistent |
| `claim_map_absent` | No claim map files (`ai/claim_map.json`, `results/claim_map.json`) are present |

Field summary source checks only appear when the respective field summary
artifact is present in the package.

### Diagnostics vs. engineering validation

These checks answer: *are the package metadata layers internally consistent?*
They do **not** validate engineering results, verify solver convergence, or
advance claims. `claim_advancement: "none"` appears at the top level and on
every check entry.

### Stale state is not an error

`revalidation_status_consistency` reports `"warning"` when
`requires_revalidation: true` — not `"error"`. Stale state is a valid
intermediate condition while geometry edits are pending. The appropriate
action is to re-run the solver; the endpoint does not prescribe that.

---

## Runtime metadata contracts

All metadata artifacts and read-only API endpoints are treated as versioned
contracts. Any change to their shape should bump the relevant `schema_version`
field and be reflected in the contract tests in `backend/tests/test_api.py`.

### Contract helpers

The test file defines lightweight Python assertion helpers that validate stable
shapes without overfitting to incidental timestamp or event-ID values:

| Helper | Validates |
|--------|-----------|
| `_assert_revalidation_status_contract` | `state/revalidation_status.json` artifact (v0.2) |
| `_assert_revalidation_response_contract` | `revalidation_status` sub-object in API responses |
| `_assert_audit_event_contract` | Single audit event (v0.1) |
| `_assert_audit_events_response_contract` | `GET /audit-events` envelope |
| `_assert_artifact_manifest_contract` | `GET /artifact-manifest` + per-entry |
| `_assert_package_consistency_contract` | `GET /package-consistency` + per-check |
| `_assert_cae_fields_list_contract` | `GET /cae-result-fields` list |
| `_assert_cae_field_summary_contract` | `GET /cae-result-fields/{name}` |
| `_assert_capability_profile_contract` | `GET /api/runtime/capabilities` |
| `_assert_capability_tool_contract` | Single tool entry in the capability profile |

### Schema versions

| Artifact / endpoint | `schema_version` |
|--------------------|-----------------|
| `state/revalidation_status.json` | `"0.2"` |
| Audit event (`audit/events.jsonl` line) | `"0.1"` |
| Artifact manifest response | `"0.1"` |
| Package consistency response | `"0.1"` |
| CAE result fields list response | `"0.1"` |
| CAE field summary response | `"0.1"` |
| Runtime capabilities profile | `"0.1"` |

### Hard contract invariants

- `claim_advancement: "none"` appears on every artifact, event, and API response
  that touches engineering metadata. This is asserted by every contract helper.
- `advances_claims: false` on every tool in the capability profile. Any tool
  with `advances_claims: true` would violate the claim non-advancement contract
  and is caught by `test_contract_capability_profile_no_tool_advances_claims`.
- `automatic_claim_advancement: false` in the capability profile `claim_policy`.
- Read-only metadata endpoints never write to or mutate the package ZIP.
- None of the metadata endpoints create `ai/claim_map.json` or
  `results/claim_map.json`.

### Diagnostics are not engineering validation

Schema conformance means: *the artifact has the expected fields and versions*.
It does **not** mean the underlying engineering results are correct, converged,
or certifiable. `claim_advancement: "none"` on every object is the
machine-readable guarantee that no claim has been advanced by any metadata
operation.

---

## Explicit claim proposal skeleton

Engineering claims require an explicit, human-reviewed acceptance workflow.
`POST /api/projects/{project_id}/claim-proposals` implements the first step:
recording a proposal artifact that a human or downstream workflow can later
accept or reject.

### What it does

- Validates `claim_id`, `proposed_status` (must be `supported`, `not_supported`,
  or `needs_review`), `supporting_evidence` (non-empty list of package artifact
  paths), and `rationale` (non-empty string).
- Verifies every supporting evidence path exists in the package ZIP or in the
  evidence index (`results/evidence_index.json`).
- Writes `claims/proposals/{proposal_id}.json` atomically into the package.
- Appends a `claim_proposal_created` audit event with `claim_advancement: "none"`.

### Proposal artifact shape

```json
{
  "schema_version": "0.1",
  "proposal_id": "...",
  "claim_id": "structural_integrity",
  "proposed_status": "supported",
  "status": "proposed",
  "supporting_evidence": ["results/computed_metrics.json"],
  "rationale": "Stress within allowable limits.",
  "created_at": "...",
  "created_by_tool": "claims.propose_update",
  "claim_advancement": "none"
}
```

### What it does NOT do

- Does not write `ai/claim_map.json` or `results/claim_map.json`.
- Does not advance or accept any engineering claim.
- Does not run a solver, modify geometry, or trigger revalidation.
- `claim_advancement: "none"` appears in every artifact and response as the
  machine-readable guarantee.

### Artifact classification and consistency

`claims/proposals/*.json` entries are classified as `claim_proposal` in the
artifact manifest. Package consistency check F (`claim_proposals`) verifies
that all proposals carry `status: proposed` and `claim_advancement: none`,
and that every referenced evidence path resolves in the package. The
`_TOOL_CAPABILITY_PROFILE` entry for `claims.propose_update` carries
`advances_claims: false` and `requires_explicit_acceptance_workflow: true`.

### Claim proposal inspection endpoints

Two read-only endpoints expose the proposals written into the package:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/projects/{id}/claim-proposals` | List all proposals; returns `count`, `proposals` (sorted by `created_at` then `proposal_id`), `claim_advancement: "none"` |
| `GET /api/projects/{id}/claim-proposals/{proposal_id}` | Read one proposal by `proposal_id`; returns 404 if absent; includes `proposal_path`, `claim_advancement: "none"` |

Both endpoints are **inspection-only**:
- They never modify the package ZIP.
- They never create `ai/claim_map.json` or `results/claim_map.json`.
- They do not accept proposals or advance claims.
- `claim_advancement: "none"` appears in every response.

An empty list (no proposals) is a normal successful response — the list
endpoint never returns an error for a package that exists but has no proposals.

---

## Evidence reference resolver

`_resolve_evidence_reference` is a pure module-level helper that answers, for
a single package-internal path:

| Field | Meaning |
|-------|---------|
| `exists` | Path is physically present in the package ZIP |
| `in_evidence_index` | Path appears in `results/evidence_index.json` |
| `evidence_index_entry` | The matching evidence index entry, or `null` |
| `manifest_category` / `manifest_kind` | Classification from `_ARTIFACT_PATTERN_CATALOG` |
| `evidence_role` | Evidence role from the pattern catalog, or `null` |
| `requires_revalidation` | Forwarded from `state/revalidation_status.json` |
| `current_geometry_revision` | Current geometry revision counter |
| `last_validated_geometry_revision` | Revision at last validation |
| `usable_for_claim_proposal` | `True` if `exists` or `in_evidence_index` |
| `warnings` | List of warning strings (see below) |
| `claim_advancement` | Always `"none"` |

### Warning strings

| Warning | Meaning |
|---------|---------|
| `path_not_found_in_package_or_evidence_index` | Path is neither in the ZIP nor in the evidence index |
| `evidence_from_stale_geometry_state` | `requires_revalidation=True` and the artifact category is in `_STALE_EVIDENCE_CATEGORIES` |

### Stale evidence behavior

If `state/revalidation_status.json` has `requires_revalidation: true`,
artifact categories that are downstream CAE outputs (`solver_output`,
`summary`, `field_summary`, `evidence_index`) receive the
`evidence_from_stale_geometry_state` warning. Stale evidence is **not**
removed from the package or marked as non-existent — `exists` and
`usable_for_claim_proposal` remain based on actual presence, not freshness.
Engineers must decide whether stale evidence is acceptable for a given
proposal context.

### API endpoint

`GET /api/projects/{id}/evidence-references/resolve?path=...`

Returns 400 for empty or non-internal paths, 404 when the package does not
exist, 200 otherwise (even for paths absent from the package, which return
`exists: false` with a warning).

The endpoint is **read-only**: it never modifies the package, never creates
claim maps, and carries `claim_advancement: "none"` in both the envelope and
the resolved object.

### Reuse

`_resolve_evidence_reference` is used inside `create_claim_proposal` to
validate supporting evidence paths, and inside `_build_claim_support_packet`
to resolve each evidence entry for the support packet. Any future workflow
that needs to check whether a path is usable as evidence should call this
helper rather than re-implementing ZIP membership checks and evidence index
lookups.

---

## Claim proposal support packets

`GET /api/projects/{id}/claim-proposals/{proposal_id}/support-packet`
aggregates all inspection data about a single proposal into a compact,
read-only packet:

| Field | Content |
|-------|---------|
| `proposal_id`, `claim_id`, `proposed_status`, `proposal_status`, `rationale` | Forwarded from the proposal artifact |
| `proposal_path` | `claims/proposals/{proposal_id}.json` |
| `supporting_evidence` | List of `_resolve_evidence_reference` outputs for each evidence path |
| `evidence_warnings` | Flattened list of all resolver warnings across evidence entries |
| `stale_evidence_count` | Count of evidence entries with `evidence_from_stale_geometry_state` |
| `missing_evidence_count` | Count of entries where `usable_for_claim_proposal=false` |
| `related_audit_events` | Audit events where `proposal_path` appears in `artifacts_written` |
| `claim_advancement` | Always `"none"` |

### Audit event matching

Related audit events are filtered from `audit/events.jsonl` by checking
whether the proposal's internal path (`claims/proposals/{id}.json`) appears
in the event's `artifacts_written` list. This naturally captures the
`claim_proposal_created` event emitted at proposal creation.

### Stale evidence behavior

Stale evidence (category in `_STALE_EVIDENCE_CATEGORIES` when
`requires_revalidation=True`) increments `stale_evidence_count` and adds
`evidence_from_stale_geometry_state` to `evidence_warnings`. Stale evidence
is surfaced as a warning, not automatically rejected. Engineers must decide
whether stale evidence is acceptable for a given proposal.

### What it does NOT do

- Does not accept, reject, or modify the proposal.
- Does not create `ai/claim_map.json` or `results/claim_map.json`.
- Does not advance any engineering claim.
- Does not mutate the package ZIP.
- `claim_advancement: "none"` appears in both the envelope and the packet.

Returns 404 when the package or the proposal does not exist.

### Review readiness diagnostics

The support packet includes a `review_readiness` object that gives a
machine-readable signal about whether a proposal is ready for human review:

```json
{
  "status": "ready | warning | blocked",
  "checks": [...],
  "claim_advancement": "none"
}
```

Five checks run on every support-packet request:

| Check ID | Blocked when | Warning when | OK when |
|----------|-------------|--------------|---------|
| `supporting_evidence_present` | No evidence paths declared | — | ≥1 evidence path |
| `no_missing_evidence` | Any evidence path unresolvable | — | All paths resolvable |
| `stale_evidence` | — | Any evidence is from stale geometry state | No stale evidence |
| `proposal_status_reviewable` | — | Status not `proposed`/`draft` | Status is `proposed` or `draft` |
| `claim_map_not_advanced` | — | `ai/claim_map.json` or `results/claim_map.json` present | Neither present |

**Rollup policy:** `blocked` > `warning` > `ready`. Any blocked check
makes the overall status `blocked`; any warning (with no blocked) makes it
`warning`; all OK makes it `ready`.

**Stale evidence** raises a warning but does not block review readiness.
Engineers must decide whether stale evidence is acceptable for a given
proposal. Stale evidence is not removed or marked missing.

**Missing evidence** blocks readiness because a proposal that references
absent artifacts cannot be reliably reviewed.

**`claim_advancement: "none"`** appears in both the `review_readiness`
object and the enclosing support packet. Review readiness diagnostics do
not accept proposals, create claim maps, or advance engineering claims.

---

## What remains future work

| Item | Notes |
|------|-------|
| Streaming events | Poll-based; SSE or WebSocket would enable live updates |
| Optional external CAD adapter | FreeCADCmd or another CAD backend can be reintroduced behind the provider registry when needed |
| Mesh quality metrics | Not yet implemented — external mesh handoff records intent, but no quality report |
| Field data endpoint | Extend `GET /projects/{id}/fields/{f}` to serve real VTK/HDF5 data (currently synthetic `y_normalized` with explicit "Synthetic preview, not for engineering decisions" label) |
| Solver field data endpoint | Extend `GET /projects/{id}/fields/{f}` to serve real VTK/HDF5 data |
| MCP server adapter | `backend/app/mcp_server.py` wrapping `runtime.registered_tool_names()` |
| Multi-step plan with dependencies | Steps execute sequentially; parallel/conditional is future |
| Per-run project scoping | Tool handlers accept `project_id`; run-level scoping can be tightened |
