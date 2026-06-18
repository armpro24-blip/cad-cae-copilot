# Simulation runner sync/SSE consolidation audit (#183)

Audit of the duplicated simulation / solver run-event paths after the MCP-first
cutover, plus the smallest safe consolidation. Goal: one clear contract for
solver readiness, approval, run events, and result reporting — preserving
approval gating and solver-result honesty.

## Paths that exist

| Path | Entry point | Role | Status |
|---|---|---|---|
| **MCP `cae.run_solver`** | `app/runtime_registry/cae.py` (`_tool_cae_run_solver`) | Agent-driven solver execution on a **pre-generated** deck. `requires_approval=True`. Enforces the stale-topology hash check. Writes `simulation/runs/{run_id}/…` + imports evidence + refreshes summaries. | **Canonical** (MCP-first) |
| **MCP `cae.prepare_solver_run`** | `app/runtime_registry/cae.py` (`_tool_cae_prepare_solver_run`) | Read-only preflight: readiness + `recommended_next_calls`. Runs nothing. | **Canonical** preflight |
| **REST `POST /api/projects/{id}/run-simulation`** | `app/routers/project_workflows.py` → `simulation_runner.run_simulation` | Synchronous mesh (Gmsh) → deck → solve (CalculiX) → parse, in one call. `confirmed=true` gate. | **Legacy** (embedded-agent era); retained, no live UI consumer |
| **REST `POST /api/projects/{id}/run-simulation-stream`** | `app/routers/project_workflows.py` → `simulation_runner.run_simulation_stream` | SSE variant of the above (`checking_tools → meshing → building_nsets → solving → parsing → done\|error`). | **Legacy**; **its only frontend consumer is dead code** (see Finding 2) |

### Verdict: active vs stale

- The **MCP `cae.run_solver` / `cae.prepare_solver_run`** pair is the **canonical,
  actively-wired** solver path for the MCP-first workbench.
- `simulation_runner.py` (REST sync + SSE) is the **legacy embedded-agent path**.
  It is still registered and its sync helpers (`_read_member`, meshing, NSET
  building, deck generation) remain reused by other workflow routes, so the
  module stays. (The dead `/stress-heatmap` GLB route that previously consumed
  `_read_member` was retired in favor of the canonical `/fields/{name}` path — #246.)
  But:
  - the **SSE endpoint has no live frontend consumer** (Finding 2), and
  - the two REST halves had **drifted** (Finding 1).

The two REST entry points differ from the MCP tool by design: `simulation_runner`
**meshes + generates the deck itself**, whereas `cae.run_solver` runs a
**pre-generated** deck. They are not simple duplicates of each other, so this
audit does **not** merge REST into MCP; it consolidates the REST sync/SSE
duplication and aligns the honesty guard across all three.

## Findings

### Finding 1 — sync/SSE duplication + stale-topology honesty drift  ✅ FIXED here
`run_simulation` (sync) and `run_simulation_stream` (SSE) were ~95% identical
copies of the same pipeline. They had drifted: **the sync path enforced
`validate_cae_topology_references()` (abort on stale face references) but the
streaming path did not.** A stale streaming run could therefore solve against
stale face references and stream a *wrong result as success* — a solver-honesty
violation.

**Fix:** both entry points now delegate to a single generator,
`_run_simulation_core`, which yields progress events and a terminal
`{"step": "done", "result": …}` event and raises `_SimAbort` for
contract-bearing aborts. The sync wrapper drains the events and re-raises
`_SimAbort` as the historical `HTTPException`; the SSE wrapper formats every
event and never raises. The stale-topology guard now lives in the shared core,
so both paths enforce it and **cannot drift again**. The MCP `cae.run_solver`
path already enforced the equivalent check (`cae.py`).

Approval gating is unchanged: the REST core still requires `confirmed=true`
before any external process runs; `cae.run_solver` is still `requires_approval=True`.

### Finding 2 — dead frontend SSE consumer  ⏳ follow-up
The only consumer of `/run-simulation-stream` is `executeSimulation` in
`frontend/src/app/useEngineeringActions.ts` (via `api.runSimulationStream`).
After the MCP-first chat removal (#17, #8) it is **orphaned**: `useWorkbenchApp.ts`
mounts `useEngineeringActions` but does **not** destructure `executeSimulation`,
and it passes a no-op `setChatHistory: () => undefined`. Nothing else references
`executeSimulation` / `runSimulationStream`. It is unreachable dead code.

**Recommendation (split issue):** remove `executeSimulation` +
`api.runSimulationStream` + the now-unused `simulationProgress` state, and decide
whether to keep the `/run-simulation-stream` endpoint at all (no live consumer)
or drop it with the dead frontend. Deferred here because it touches the frontend
build, which is verified separately from the backend test suite.

### Finding 3 — result-shape divergence between REST and MCP  ⏳ follow-up
The REST path writes `simulation/results_summary.json` (flat: status, von Mises,
displacement, `full_metrics`, `verdict`). The MCP path writes
`simulation/runs/{run_id}/solver_run.json` (run metadata) plus
`results/computed_metrics.json` (via `aieng_bridge`). Consumers
(`contextual_chat.py`, the heatmap route) read the REST `results_summary.json`
shape only.

**Recommendation (split issue):** define one canonical result-summary contract
that both paths can produce/consume, or document the two shapes and their
intended readers explicitly. Out of scope for this PR (no honesty bug; behavior
preserved).

## Tests

Sync path (`tests/test_simulation_runner.py`, unchanged contract): `confirmed`
gate (400), missing package (404), tools-unavailable (200), missing setup.yaml
(422), full mock run (200), unresolved-face abort (422), stale-topology /
missing-face aborts (422, `ccx` not called).

Streaming path (`tests/test_simulation_stream.py`, pre-existing): SSE format,
`confirmed` gate, missing project/package, tools-unavailable → `done`, no-STEP →
error, progress events before `done`.

Added by this PR (`tests/test_simulation_runner.py`) to cover the consolidation:

- **stale-topology mismatch aborts the streaming path before solving** — parity
  with the sync path; `ccx` not called. (This was the un-covered honesty gap.)
- streaming full mock run emits `meshing`/`solving` progress then a terminal
  `done` event whose `result` matches the **sync shape** and writes
  `simulation/results_summary.json` — pins sync/SSE result-shape parity.

Run: `pytest tests/test_simulation_runner.py tests/test_simulation_stream.py -q`
