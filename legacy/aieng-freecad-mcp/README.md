> **LEGACY** — This directory is the old FreeCAD MCP adapter, preserved for
> compatibility/reference only. It is NOT the current default CAD/CAE execution
> runtime. The default runtime is `aieng-ui/backend`. See `LEGACY_NOTICE.md`
> for details.

# aieng-freecad-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-1.25%2B-purple.svg)](https://modelcontextprotocol.io/)
[![Status](https://img.shields.io/badge/status-legacy-lightgrey.svg)]()

Composable FreeCAD MCP execution interface with optional `.aieng` semantic/evidence enhancement.

**Keywords:** MCP · Model Context Protocol · FreeCAD · CAD · CAE · LLM agents · Claude · agent tools · CalculiX · STEP · engineering · design automation

> This repo is **one MCP adapter implementation**, not the semantic model. For outward-facing positioning of `.aieng` and demonstration guidance, see [`../aieng/docs/public-positioning.md`](../aieng/docs/public-positioning.md).

## What This Is / Is Not

This project is a composable execution interface for bounded CAD/CAE operations.

It is not an autonomous CAD/CAE workflow engine. The agent or caller decides tool order.

Core boundary:

- MCP executes explicit operations and returns structured results.
- `.aieng` is optional and enhances context, constraints, evidence, and provenance.
- Evidence is not a claim.
- Claim updates are explicit and evidence-backed.
- Arbitrary Python/shell execution is not a normal public tool path.

## Role in the vertical CAE MVP

`aieng_freecad_mcp` is the **agent-facing MCP bridge**. It exposes two surfaces:

1. **Standalone FreeCAD execution** (pre-runtime, legacy) — direct CAD tool calls that may run FreeCAD in-process or via `FreeCADCmd`. See the composable demo paths below.
2. **Runtime bridge** — thin HTTP wrappers around the `aieng-ui` workbench REST API. The MCP tool layer never imports FreeCAD or parses `.aieng` packages in this mode; it only forwards calls.

For the vertical CAE MVP only the runtime bridge is on the critical path. Solver execution, the approval gate, FRD extraction, schema-version validation, and artifact write-back all live in `aieng-ui` — the MCP layer is intentionally thin so the agent surface stays decoupled from execution. AIENG is not a solver and not a CAD kernel; this repo is not, either.

For the reproducible end-to-end demo see [`../aieng-ui/docs/quickstart-vertical-cae-demo.md`](../aieng-ui/docs/quickstart-vertical-cae-demo.md). For the full runtime bridge tool reference see [`docs/mcp_runtime_tools.md`](docs/mcp_runtime_tools.md).

## Standalone vs `.aieng`-Enhanced

Standalone mode:

- No `.aieng` package required.
- Tools run from explicit inputs.
- Evidence can be returned in responses.
- No package persistence.

`.aieng`-enhanced mode:

- Reads package context (task, graph, constraints, references, claims, evidence).
- Enforces guard checks (including protected and semantic-only cases).
- Persists evidence and trace when requested.
- Does not auto-advance claims.

## Five Composable Paths

CAD and CAE are independent first-class capabilities.

1. CAD-only: patch parse/execute, modified artifacts, evidence, trace.
2. CAE-only: post-processing evidence without prior CAD mutation.
3. Optional CAD->CAE: explicit orchestration helper when invoked.
4. Reference: mapping and `needs_review` marking after geometry changes.
5. Claim: explicit `aieng_update_claim` updates `claim_map.json`.

Non-negotiable behavior:

- CAD does not automatically trigger CAE.
- CAE does not require prior CAD mutation.
- Claim updates are explicit only.

## Quickstart

```bash
# install
pip install -e ".[dev]"

# tests
python -m pytest tests/

# composable demo (all paths)
python scripts/run_v1_demo.py --path all

# individual paths
python scripts/run_v1_demo.py --path cad-only
python scripts/run_v1_demo.py --path cae-only
python scripts/run_v1_demo.py --path cad-cae
python scripts/run_v1_demo.py --path reference
python scripts/run_v1_demo.py --path claim

# Milestone-1 acceptance (repeatable, structured JSON)
python scripts/run_milestone1_acceptance.py
python scripts/run_milestone1_acceptance.py --json > milestone1_report.json
```

## Runtime Bridge (Phase 3)

`aieng_freecad_mcp` also exposes MCP tools that delegate to the `aieng-ui`
orchestration runtime over HTTP.  These tools do not duplicate FreeCAD or
package logic — all execution, approval gating, and audit logging remain in
the `aieng-ui` backend.

```bash
# Start aieng-ui backend first
cd path/to/aieng-ui && uvicorn app.main:app --port 8000

# Start MCP server with runtime bridge enabled
AIENG_RUNTIME_BASE_URL=http://localhost:8000 freecad-mcp
```

Runtime bridge MCP tools:

| Tool | Description |
|------|-------------|
| `aieng_list_runtime_tools` | List tools registered in the runtime |
| `aieng_start_runtime_run` | Start a run from a natural-language message |
| `aieng_get_runtime_run` | Fetch a run record by ID |
| `aieng_inspect_geometry` | Inspect CAD geometry via the runtime; waits up to 120 s |
| `aieng_export_step` | Export STEP via the runtime; returns artifact metadata |
| `aieng_approve_runtime_run` | Approve an `awaiting_approval` run |
| `aieng_reject_runtime_run` | Reject an `awaiting_approval` run |
| `aieng_get_cae_status` | Honest CAE artifact detection via the runtime; returns mode + per-artifact presence |
| `aieng_get_cae_preprocessing_summary` | CAE setup readiness summary; read-only, no solver execution |
| `aieng_get_cae_simulation_run_summary` | Simulation run metadata summary; read-only, no solver execution |
| `aieng_get_cae_result_summary` | CAE/post-processing result summary via the runtime |
| `aieng_generate_computed_metrics` | Normalize external metrics into `results/computed_metrics.json` |
| `aieng_refresh_cae_summary` | Regenerate CAE result summary, evidence index, and markdown after imports |
| `aieng_apply_cae_setup_patch` | Controlled patches to CAE setup artifacts inside `.aieng` |
| `aieng_extract_solver_results` | Parse CalculiX FRD and write `computed_metrics.json` |
| `aieng_prepare_solver_run` | Preflight plan for external solver run; no execution |
| `aieng_run_solver` | Execute external CalculiX solver; approval-gated |

See [`docs/mcp_runtime_tools.md`](docs/mcp_runtime_tools.md) for full reference,
Claude Code configuration, and limitations.

See [`../docs/demo-vertical-cae-workflow.md`](../docs/demo-vertical-cae-workflow.md) for a reproducible walkthrough of the full agent-run CAE lifecycle (preflight → approval-gated solver → FRD extraction → summary refresh) with a ready-to-use agent prompt.

---

## Tool and Policy Summary

Key `.aieng` bridge tools:

- `aieng_parse_patch`, `aieng_execute_patch`
- `aieng_run_cad_to_cae_workflow` (optional explicit orchestration)
- `aieng_postprocess_results`
- `aieng_update_claim` (only claim-map mutator)
- `aieng_build_reference_map`, `aieng_mark_references_needing_review`
- `aieng_inspect_capabilities` (read-only, planning-neutral)
- `aieng_read_design_targets` (read-only package inspection)
- `aieng_read_design_target_comparisons` (read-only package inspection)
- `freecad_runtime_capabilities` (read-only runtime detection)

Policy highlights:

- All non-claim-update tools keep `claims_advanced: false`.
- Unsupported, missing, `not_found`, and `needs_review` are first-class outputs.
- Surrogate outputs are mock/demonstration evidence, not solver validation.
- Design target inspection tools are read-only; they do not mutate packages or advance claims.
- Pass/fail design target comparisons are artifact-threshold checks, not engineering certification.

## Computed Metrics Exporter (Phase 7)

`freecad_mcp.computed_metrics_exporter` normalizes external post-processing
metrics into the canonical `results/computed_metrics.json` schema consumed by
`aieng` (Phase 6).

```bash
# From flat JSON
python -m freecad_mcp.computed_metrics_exporter \
  --input postprocess_result.json \
  --output results/computed_metrics.json \
  --software "FreeCAD FEM / CalculiX"

# From CSV
python -m freecad_mcp.computed_metrics_exporter \
  --input metrics.csv \
  --output results/computed_metrics.json \
  --load-case-id load_case_001
```

Supported inputs:
- Flat JSON with keys like `max_von_mises_stress_mpa`, `max_displacement_mm`, `factor_of_safety`
- CSV with columns `name`, `value`, `unit`
- JSON already close to the Phase 6 schema (validated and normalized)

Output follows the Phase 6 schema:
- `schema_version`: `"0.1"`
- `metrics_source`: `{tool, software, source_files}`
- `load_cases[].id` and `load_cases[].metrics`
- `warnings` for skipped/unrecognized keys

No solver execution. No VTU/FRD/ODB parsing.

## AIENG read-only feature inspection bridge (v0.17)

`freecad_mcp.aieng_bridge.inspect_features` is the safe, read-only
function that AIENG's `aieng-ui` workbench discovers automatically (its
v0.16 bridge-discovery layer probes for exactly this import path).

```python
from freecad_mcp.aieng_bridge import inspect_features

result = inspect_features("/path/to/part.FCStd")
# result == {
#   "status": "ok",
#   "schema_version": "0.1",
#   "input_path": "...",
#   "freecad_version": "...",
#   "feature_count": N,
#   "features": [
#     {
#       "id": "Pad",
#       "label": "Pad",
#       "type": "PartDesign::Pad",
#       "source_object": "Pad",
#       "parameters": [
#         {"name": "Length", "value": 10.0, "unit": "mm",
#          "kind": "quantity", "editable": True,
#          "editor_mode": "editable"}
#       ],
#       "metadata": {"visibility": True, "has_shape": True,
#                    "property_count": 1}
#     }
#   ]
# }
```

The function spawns `FreeCADCmd` in a controlled subprocess and runs a
**fixed embedded inspection script** (the constant
`FREECAD_FEATURE_INSPECT_SCRIPT`). The script opens the CAD document,
walks `Document.Objects`, captures safe scalar / Quantity properties,
and closes the document **without saving**. Caller input never enters
the executed code path — only the validated source-path environment
variable is read by the script.

### Read-only safety boundaries

- No CAD edit, no parameter change, no expression set.
- No STEP/STL/IGES export.
- No mesh generation, no solver run.
- No claim advancement.
- No arbitrary Python from the caller.
- The document is closed without saving on every exit path.
- The result of the inspection is **metadata only** — feature
  identifiers, labels, types, parameter names + values + units +
  editor-mode flags.

### Supported inputs

- `.FCStd` (FreeCAD native)
- `.step` / `.stp`

### Dependencies

- `FreeCADCmd` resolved from the explicit `freecad_cmd=` argument or
  the `FREECAD_MCP_FREECAD_PATH` environment variable.
- Missing `FreeCADCmd` → clear `RuntimeError`. The aieng-ui v0.16
  bridge-discovery layer maps `RuntimeError` to
  `status: skipped, reason: bridge_unavailable` automatically.

### Error contract

| Cause                                | Raised                       |
|--------------------------------------|------------------------------|
| Source path missing                  | `FileNotFoundError`          |
| Unsupported extension                | `ValueError`                 |
| FreeCADCmd not configured / missing  | `RuntimeError`               |
| Subprocess timeout                   | `RuntimeError`               |
| Subprocess crashed / no result file  | `RuntimeError`               |
| Result file is not valid JSON        | `RuntimeError`               |
| Embedded script reported `status=error` | `RuntimeError`            |

### How aieng-ui discovers this function

aieng-ui's `freecad_adapter._FREECAD_BRIDGE_CANDIDATES` includes
`("freecad_mcp.aieng_bridge", "inspect_features", "path_only")` at the
top. On every read-only inspection request, aieng-ui calls
`preflight_freecad_adapter(settings)`; on `ready` or `partial` it
attempts `importlib.import_module("freecad_mcp.aieng_bridge")`,
resolves `inspect_features`, and invokes it with the source path. The
result is normalised, written as evidence
(`simulation/cae_imports/parsed_features.json` and
`graph/feature_graph.json`), and surfaced in the loop stepper +
Project Health Check.

When `freecad_mcp` is not installed, aieng-ui's discovery falls back
to the next candidate, or returns `status: skipped` honestly. Neither
end of the bridge ever crashes for missing dependencies.

### Opt-in real FreeCAD inspection test (v0.22)

A real-FreeCAD integration test is included but **disabled by default**.
It validates that `inspect_features` returns metadata from an actual
`.FCStd` file without modifying it.

```bash
# Run only when FreeCAD is installed and explicitly enabled
AIENG_RUN_FREECAD_INTEGRATION=1 python -m pytest -q -k "real_freecad_inspect"
```

Requirements:
- FreeCADCmd available on `PATH` or set via `FREECAD_MCP_FREECAD_PATH`
- `AIENG_RUN_FREECAD_INTEGRATION=1` environment variable

What the test does:
- Generates a minimal box FCStd fixture via FreeCADCmd
- Calls `freecad_mcp.aieng_bridge.inspect_features` on it
- Asserts feature metadata is returned
- Asserts the input file is not modified (size + mtime unchanged)
- No save, edit, export, mesh, or solver call is made

Default CI remains hermetic — the test skips automatically when FreeCAD
is unavailable or the env var is not set.

## Runtime Optionality

Real runtime dependencies are optional:

- FreeCAD (+ FEM workbench)
- Gmsh or Netgen
- CalculiX

Without these, mock/surrogate paths still work for demos and tests. Real validation requires real runtime execution and explicit claim update.

## Workspace Docs

Cross-repo context:

- [`../docs/system_architecture.md`](../docs/system_architecture.md) — three-repo overview
- [`../docs/repo_boundaries.md`](../docs/repo_boundaries.md) — ownership and coupling points
- [`../docs/package_contract.md`](../docs/package_contract.md) — `.aieng` ZIP format
- [`../docs/roadmap.md`](../docs/roadmap.md) — phases 1–5

## Docs

- `docs/product_boundary.md` - boundary and non-goals
- `docs/architecture.md` - module and execution architecture
- `docs/tool_contract.md` - tool/result contracts
- `docs/evidence_and_claim_policy.md` - evidence/claim discipline
- `docs/freecad_execution_policy.md` - execution safety
- `docs/release_v1_demo.md` - composable demo paths
- `docs/known_limitations.md` - explicit limitations
- `docs/development_todo.md` - planned work only
- `docs/roadmap.md` - release roadmap

## Known Limitations

- Topology-changing edits are unsupported.
- Topology-stable face IDs are not guaranteed.
- No automatic BC/load remapping after geometry change (`needs_review` is used).
- VTK export depends on field-data availability.
- Compound claim logic (AND/OR) is not implemented.
