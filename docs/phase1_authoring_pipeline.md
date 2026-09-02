# Phase 1 Authoring Pipeline

This document describes the Phase 1 `.aieng` authoring pipeline: the end-to-end system that turns natural-language intent into a versioned, auditable engineering data package.

Phase 1 is **not** a text-to-CAD demo. It is a primitive-first, backend-agnostic, evidence-first authoring system. The output is a `.aieng` zip archive containing not only geometry but also the intent, assumptions, construction history, raw execution traces, and evidence entries required for downstream AI and human audit.

---

## 1. What Phase 1 Enables

You can run the following end-to-end flow today:

```text
natural-language intent
→ rule-based modeling_plan
→ schema validation
→ backend discovery
→ backend execution
→ .aieng package
→ authoring records
→ evidence / trace
→ diagnostic package on failure
```

### CLI Walkthrough

Generate a modeling plan from intent:

```bash
aieng plan \
  --intent "create a 120x80x10 rectangular plate with 4 mounting holes" \
  --out modeling_plan.json
```

Validate the plan:

```bash
aieng validate-plan modeling_plan.json
```

Execute through the FakeBackend (fast, no FreeCAD required):

```bash
aieng init-from-plan modeling_plan.json \
  --out generated.aieng \
  --backend fake
```

Execute through the FreeCAD reference backend (requires FreeCAD installation):

```bash
aieng init-from-plan modeling_plan.json \
  --out generated.aieng \
  --backend freecad
```

The FakeBackend is useful for CI, testing, and prototyping. The FreeCAD backend produces real STEP geometry.

---

## 2. Design Principles

### General-first, domain-specializable later

Phase 1 builds a **general CAD/CAE authoring substrate** first. Domain-specific intelligence (e.g., aerospace bracket design rules, medical implant constraints) will be layered later via domain context, example libraries, validation extensions, and optionally fine-tuned planners. The core pipeline does not hard-code any domain.

### No family-first modeling

We do **not** treat `plate`, `bracket`, `enclosure`, or `housing` as core primitives. If the user says "plate", the planner emits `create_box` plus optional `create_cylindrical_cut` operations. Family templates may be introduced later as **user-defined macros**, not as schema-level operations.

### Primitive-first operation IR

`modeling_plan` is an **intermediate representation (IR)** for CAD operations. It is:

- Not FreeCAD Python
- Not CadQuery script
- Not a parametric template catalog
- Not a STEP file

It is a declarative, backend-agnostic JSON document that any compliant `BackendAdapter` can execute.

### Backend-agnostic execution

FreeCAD is the **reference backend** for Phase 1, but it is **not an architecture boundary**.

- `aieng` core defines the `BackendAdapter` protocol and discovers backends via entry points.
- `aieng_freecad_mcp` provides the FreeCAD implementation.
- Future backends (CadQuery, Onshape REST, SolidWorks COM, NX Open, Abaqus/Ansys/OpenFOAM) can be added without touching `aieng` core.
- MCP is one possible transport among many. The protocol is synchronous and transport-agnostic.

### Evidence-first package output

The output is not a bare STEP file. It is a `.aieng` package containing:

- **Intended operations** (`authoring/modeling_plan.json`)
- **Actual operations** (`authoring/construction_history.json`)
- **Raw traces** (`provenance/tool_trace.jsonl`)
- **Evidence entries** (`results/evidence_index.json`)
- **Validation status** (`validation/status.yaml`)
- **Geometry** (`geometry/source.step`)

This structure lets downstream agents answer: *What did the user ask for? What did the planner decide? What did the backend actually do? What assumptions were made?*

---

## 3. Core Components

### `modeling_plan.schema.json`

**Path:** `aieng/src/aieng/schemas/modeling_plan.schema.json`

Kernel-agnostic CAD operation IR. Schema version `0.1.0`, JSON Schema Draft 2020-12.

Phase 1 operations:
- `create_box`
- `create_cylindrical_cut`

Key fields:
- `intent.original_text` — required, `minLength: 1`
- `units.length` / `units.angle` — required
- `assumptions` — explicit planner assumptions
- `missing_information` — gaps in user input
- `steps` — ordered primitive operations
- `checks` — post-execution validation predicates (`bounding_box`, `operation_count`)

Constraints:
- `confidence` is an enum: `certain | inferred | guessed` (not an uncalibrated 0–1 float)
- `target` lives at the **step level**, not inside `parameters`
- `additionalProperties: false` on critical objects to prevent undefined-field injection
- Family operations (`create_plate`, `create_bracket`, etc.) are **prohibited** by the schema enum

### Plan Validator

**Path:** `aieng/src/aieng/modeling_plan/validate.py`

Validates a modeling plan dict against Phase 1 rules.

Checks:
1. Schema compliance (JSON Schema Draft 2020-12)
2. Duplicate `step_id`
3. Duplicate `creates` identifiers
4. Unresolved `target` references (must point to a prior step)
5. Operation whitelist enforcement (no family operations)
6. Required parameter keys per operation
7. Assumption ref validity (warn if missing)

**Important:** This module is **independent** from `aieng.validate` to avoid circular dependencies. It defines its own `PlanValidationMessage` and `PlanValidationReport` types.

Public API:
```python
from aieng.modeling_plan.validate import validate_modeling_plan, validate_modeling_plan_file

report = validate_modeling_plan(plan_dict)
assert report.ok  # True if no FAIL messages
```

### RuleBased Planner

**Path:** `aieng/src/aieng/modeling_plan/planner.py`

A regex-based intent parser that emits primitive-only modeling plans.

Supported patterns:
- Dimensions: `120x80x10`, `120 x 80 x 10`, `120 by 80 by 10`
- Units: `mm`, `cm`, `m`, `in` (default `mm` with recorded assumption)
- Holes: `4 holes`, `four holes`, `4 mounting holes`
- Missing dimensions → fallback defaults with `requires_user_confirmation: true` assumption

The planner never emits family operations. A "plate" becomes `create_box` + `create_cylindrical_cut`.

### Backend Protocol

**Path:** `aieng/src/aieng/backend_adapter.py`

Defines the execution contract between `aieng` core and backend adapters.

```python
class BackendAdapter(Protocol):
    backend_id: str
    transport_type: str
    adapter_version: str

    def validate_capabilities(self, plan: dict[str, Any]) -> list[str]: ...
    def dry_run(self, plan: dict[str, Any], output_dir: Path) -> BackendExecutionResult: ...
    def execute_plan(self, plan: dict[str, Any], output_dir: Path) -> BackendExecutionResult: ...
```

Key dataclasses:
- `StepExecutionResult` — per-step status, inputs, outputs, evidence, trace, backend metadata
- `BackendExecutionResult` — overall status (`success | partial | failed`), artifacts, construction history, evidence entries, trace entries

**Critical rule:** Backends write temporary artifacts to `output_dir`. They do **not** write `.aieng` zip files. Package assembly is the orchestrator's responsibility.

### Backend Discovery

**Path:** `aieng/src/aieng/backend_discovery.py`

Resolves a `backend_id` string to a `BackendAdapter` class without hard cross-package imports.

Resolution order:
1. **Built-in registry** — e.g., `"fake"` → `aieng.backends.fake_backend:FakeBackend`
2. **Entry points** — `importlib.metadata` group `aieng.backends`
3. **Dotted path fallback** — `module.path:ClassName` or `module.path.ClassName`

Example entry point registration (from `aieng_freecad_mcp/pyproject.toml`):
```toml
[project.entry-points."aieng.backends"]
freecad = "freecad_mcp.aieng_bridge.modeling_executor:FreeCADModelingBackend"
```

### Fake Backend

**Path:** `aieng/src/aieng/backends/fake_backend.py`

A pure-Python reference backend for testing the core pipeline without FreeCAD.

Properties:
- `backend_id = "fake"`, `transport_type = "in_process"`
- Generates a placeholder STEP file and full `BackendExecutionResult`
- Supports artificial failure injection:
  - `fail_at_step_id` — simulates a step failure
  - `fail_export` — simulates STEP export failure
- Every step produces at least one evidence entry and one trace entry
- Construction history includes `backend_metadata` per step

Use this backend in CI and for `aieng` core integration tests.

### `init_from_plan`

**Path:** `aieng/src/aieng/orchestration/init_from_plan.py`

The orchestrator that assembles the `.aieng` package.

Pipeline:
1. Read `modeling_plan.json`
2. **Hard gate:** `validate_modeling_plan()` — fails → `ValueError`, no package created
3. Discover backend class via `discover_backend(backend_id)`
4. **Hard gate:** `backend.validate_capabilities(plan)` — fails → `RuntimeError`, no package created
5. Execute backend in a temp directory
6. Assemble `.aieng` package:
   - `authoring/modeling_plan.json` — frozen intent
   - `authoring/construction_history.json` — actual execution history
   - `provenance/tool_trace.jsonl` — append-only raw events
   - `results/evidence_index.json` — evidence ledger
   - `validation/status.yaml` — pipeline status
   - `geometry/source.step` / `geometry/normalized.step` — if exported
7. Returns `out_path`

**Diagnostic package strategy:**
- If backend returns `partial` or `failed`, a diagnostic package is still written.
- It contains all authoring records, traces, evidence, and status, but may lack geometry.
- This ensures failures are auditable, not silent.

### FreeCAD Reference Backend

**Path:** `aieng_freecad_mcp/src/freecad_mcp/aieng_bridge/modeling_executor.py`

The first real CAD backend. It compiles an entire modeling plan into a **single bounded FreeCAD Python script** and executes it via one `FreeCADCmd` subprocess.

Key properties:
- Registered via entry point as `backend_id = "freecad"`
- `transport_type = "subprocess"`, `kernel = "FreeCAD"`
- Supports `create_box` and `create_cylindrical_cut`
- Uses `Part::Box`, `Part::Cylinder`, `Part::Cut`
- Cylinder axis handled safely: checks `Length < 1e-12` before `normalize()`
- Exports **only the current body** (not all objects in the document)
- FreeCAD path resolution: constructor arg → `FREECAD_MCP_FREECAD_PATH` → `FREECAD_HOME` → PATH search

**Security:** The generated script is a fixed template driven by JSON plan data. User strings are never interpolated into executable Python code.

---

## 4. Package Outputs

### Success package contents

```text
manifest.json
authoring/modeling_plan.json
authoring/construction_history.json
provenance/tool_trace.jsonl
results/evidence_index.json
validation/status.yaml
geometry/source.step
geometry/normalized.step
```

### Diagnostic package contents (backend partial/failed)

Same as above, but `geometry/source.step` and `geometry/normalized.step` may be absent. The `validation/status.yaml` marks:

```yaml
modeling_status: partial   # or failed
diagnostic_package: true
geometry_available: false
```

### File semantics

| Path | Semantics |
|:---|:---|
| `authoring/modeling_plan.json` | **Intended** design. Frozen after creation. What the planner thought the user wanted. |
| `authoring/construction_history.json` | **Actual** design. What the backend executed. Includes per-step `backend_metadata`. |
| `provenance/tool_trace.jsonl` | **Raw events**. Append-only JSON Lines. One line per execution event. |
| `results/evidence_index.json` | **Evidence ledger**. Stable handles. Success steps → `geometry_modification`; failed steps → `validation_report`. |
| `validation/status.yaml` | **Pipeline status**. Modeling status, geometry availability, step counts, errors, warnings. |
| `geometry/source.step` | **Exported STEP** from backend. |
| `geometry/normalized.step` | Phase 1: byte-for-byte copy of `source.step`. Future phases may normalize geometry. |

---

## 5. Current CLI

```bash
# Generate a modeling plan from natural language
aieng plan --intent "..." --out modeling_plan.json [--units mm]

# Validate a modeling plan against Phase 1 schema and rules
aieng validate-plan modeling_plan.json

# Execute plan and create .aieng package (FakeBackend — fast, no FreeCAD)
aieng init-from-plan modeling_plan.json --out generated.aieng --backend fake

# Execute plan and create .aieng package (FreeCAD — real geometry)
aieng init-from-plan modeling_plan.json --out generated.aieng --backend freecad
```

### Backend selection guidance

| Backend | Use case | Dependencies |
|:---|:---|:---|
| `fake` | CI, testing, prototyping, core pipeline validation | None |
| `freecad` | Real geometry generation, manual verification, reference implementation | FreeCAD installation |

---

## 6. Test Coverage

| PR | Tests | What they cover |
|:---|:---|:---|
| PR 1 — Schema + Validator | 27 | Schema self-validation, schema invalidations (missing fields, family ops, bad confidence, target-in-params), logic validations (dup step_id, dup creates, unresolved target, missing params, assumption refs) |
| PR 2 — Planner + CLI | 18 | Intent parsing (dimensions, units, hole counts, number words), plan generation, CLI file I/O, validate-plan exit codes |
| PR 3 — Backend Protocol + Discovery + FakeBackend | 26 | Dataclass construction, mutable default isolation, backend discovery (built-in, entry points, dotted path, error cases), FakeBackend success/partial/failed/export-failure scenarios, evidence/trace per step |
| PR 4a — Orchestrator | 17 | Package assembly, success/diagnostic package contents, status.yaml, manifest resources, overwrite behavior, validation/capability hard gates, fallback evidence |
| PR 4b — FreeCAD Backend | 8 non-FreeCAD + 6 FreeCAD-marked | Capability validation, dry_run logic, missing FreeCAD handling, entry point discovery, real FreeCAD box+cuts, STEP export, current-body-only export, evidence/trace generation |

**Total:** 88 tests in `aieng` core (all pass, no regressions across PRs). FreeCAD tests are marked `@pytest.mark.freecad` and skipped unless `FREECAD_MCP_FREECAD_PATH` or a `FreeCADCmd` binary is available.

---

## 7. What Phase 1 Does Not Do

The following are **explicitly out of scope** for Phase 1 and are reserved for future phases:

- **No UI / Web frontend** — CLI only.
- **No LLM planner** — Rule-based regex planner only. LLM-based planning is a future enhancement.
- **No object family / template catalog** — `plate`, `bracket`, `enclosure` are not schema operations.
- **No modify-existing-package** — Phase 1 only supports create-new.
- **No CAE simulation** — No meshing, solving, BCs, or result import.
- **No CadQuery adapter** — FreeCAD is the only real backend.
- **No commercial CAD adapters yet** — Onshape, SolidWorks, NX adapters are future work.
- **No topology / feature graph post-processing** — Not implemented yet. Will be PR 5.
- **No complex sketch solver** — Only primitives (`create_box`, `create_cylindrical_cut`).
- **No direct arbitrary CAD Python execution** — Backends execute bounded scripts derived from structured JSON plans only.

---

## 8. Why This Is Not Just Text-to-CAD

A typical text-to-CAD system outputs code, mesh, or a STEP file. The AI-generated artifact is opaque: you cannot easily audit what assumptions were made, what operations were intended vs. executed, or how to safely edit the result later.

Phase 1 outputs a `.aieng` **package**, which is a versioned, self-describing engineering data repository. The package contains:

| Artifact | Purpose |
|:---|:---|
| `modeling_plan` | The **intended** design, frozen. You can diff intent against history. |
| `assumptions` | Every guess, default, or inferred value is explicit and flaggable. |
| `construction_history` | The **actual** design, with per-step backend metadata. |
| `tool_trace` | Raw execution events for forensic debugging. |
| `evidence_index` | Stable handles for claims about geometry validity. |
| `diagnostic package` | Even on failure, you get a complete audit trail, not a silent crash. |
| `backend-agnostic contract` | The same plan can run on FreeCAD today, CadQuery tomorrow, Onshape next quarter. |

The core value is **engineering intent preservation and auditability**, not just geometry generation.

---

## 9. Next Steps

### PR 5: Semantic post-processing

After `init_from_plan` creates the base package, run:
- `extract_topology_package` → `geometry/topology_map.json`
- `build_aag_package` → `graph/aag.json`
- `recognize_features_package` → `graph/feature_graph.json`
- `update_validation_status_package` → refreshed `validation/status.yaml`

This will be gated by `run_postprocess: bool = True` and `postprocess_strict: bool = False`.

### More primitive operations

Extend the schema and backends:
- `create_fillet`
- `create_chamfer`
- `create_sketch_and_extrude`
- `create_circular_pattern`
- `create_mirror`

### Better planner

- LLM-based planner with structured output
- Clarification policy: when to ask the user vs. when to assume
- Assumption risk scoring and user confirmation workflow

### Additional backends

- CadQuery (Python programmatic CAD)
- Onshape REST API
- Commercial CAD adapters (SolidWorks, NX, CATIA) via COM/OpenAPI

### CAE readiness

- Material assignment
- Load and boundary condition definitions
- Meshing adapter contract
- Solver adapter contract (Abaqus, Ansys, OpenFOAM)

---

## 10. Developer Notes

### For `aieng` core contributors

- **Do not add family operations to the core schema.** If you need `create_plate`, implement it as a planner-level macro that emits `create_box` + cuts.
- **New operations must enter the schema, validator, and backend capability contract simultaneously.** A backend cannot execute an operation the validator does not know about.
- **Backend adapters must not write `.aieng` zip files.** They write temp artifacts to `output_dir` and return structured data. The orchestrator owns package assembly.
- **Backend execution must return evidence and trace entries.** Even a single-step plan must produce at least one evidence entry and one trace entry per step.
- **If execution fails, prefer a diagnostic package over silent failure.** Return `BackendExecutionResult(overall_status="partial")` or `"failed"` with populated `errors`, `trace_entries`, and `evidence_entries`.

### For backend adapter developers

- Implement the `BackendAdapter` protocol (`validate_capabilities`, `dry_run`, `execute_plan`).
- Register your backend via `pyproject.toml` entry points under group `aieng.backends`.
- Keep CAD-kernel-specific logic inside your adapter package. Do not leak FreeCAD/SolidWorks/NX specifics into `aieng` core.
- Use `BackendExecutionResult.overall_status` correctly:
  - `success` = all modeling steps succeeded + artifact exported
  - `partial` = at least one step succeeded, but later step or export failed
  - `failed` = no step succeeded or backend could not start

### For agent integrators

- Read `authoring/modeling_plan.json` to understand **intent**.
- Read `authoring/construction_history.json` to understand **actuals**.
- Read `validation/status.yaml` to understand **pipeline health**.
- Read `results/evidence_index.json` to understand **what claims are supported**.
- Do not treat `geometry/source.step` as the sole source of truth. The package is the source of truth.
