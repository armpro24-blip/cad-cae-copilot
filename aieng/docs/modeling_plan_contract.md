# Modeling Plan Contract (Phase 1)

## 1. Purpose

This document defines the engineering contract for the `modeling_plan` intermediate representation (IR) used in Phase 1 of the `.aieng` modeling pipeline.

A `modeling_plan` is a **machine-readable, backend-agnostic description of primitive CAD operations** that transforms user intent into geometry. It is intentionally **not** a script, not a family template, and not a direct CAD API call sequence. It is a declarative plan that any compliant backend adapter can execute.

## 2. Core Principles

### 2.1 Primitive-First

Phase 1 only supports two primitive operations:

- `create_box` — additive rectangular prism
- `create_cylindrical_cut` — subtractive cylinder (boolean cut)

**Family operations are prohibited.** The following must never appear in a Phase 1 plan:

- `create_plate`
- `create_bracket`
- `create_enclosure`
- `create_hole_pattern`
- Any other higher-level semantic operation

Rationale: Family operations embed unstated assumptions (e.g., "plate" implies a flat rectangular prism with a default thickness). Primitive operations force the planner to be explicit about every geometric decision, which improves traceability and reduces hallucination.

### 2.2 Backend-Agnostic

A `modeling_plan` must be executable by any backend that implements the `BackendAdapter` protocol. The plan does not contain:

- FreeCAD-specific API calls
- STEP file paths (until execution time)
- Kernel-specific tolerances or flags

Backend-specific behavior (e.g., how FreeCAD handles `Placement.Rotation`) is the responsibility of the backend adapter, not the plan.

### 2.3 Evidence-First

Every step in a plan must be traceable to:

1. The original user intent (`intent.original_text`)
2. The planner's interpretation (`intent.interpreted_goal`)
3. Explicit assumptions (`assumptions`)
4. Confidence level (`confidence` per step)

The plan is **frozen** after creation. The `authoring/modeling_plan.json` inside the `.aieng` package is the canonical intended design. The actual geometry is recorded separately in `authoring/construction_history.json`.

## 3. Schema Version

- **Schema file**: `aieng/src/aieng/schemas/modeling_plan.schema.json`
- **Schema version**: `0.1.0`
- **JSON Schema Draft**: `2020-12`

The `plan_schema_version` field is independent from `.aieng` `FORMAT_VERSION`.

## 4. Operation Specifications

### 4.1 `create_box`

Creates a rectangular prism (additive).

**Parameters** (`CreateBoxParameters`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `length` | `number > 0` | ✅ | — | X-dimension |
| `width` | `number > 0` | ✅ | — | Y-dimension |
| `height` | `number > 0` | ✅ | — | Z-dimension |
| `origin` | `[x, y, z]` | ❌ | `[0, 0, 0]` | Anchor point |
| `origin_mode` | `"corner" \| "center"` | ❌ | `"corner"` | Semantic of `origin` |
| `name` | `string` | ❌ | auto | FreeCAD object name |

**Origin Semantics**:

- `"corner"` (Phase 1 default): `origin` is the **minimum corner** (xmin, ymin, zmin) of the box.
- `"center"`: `origin` is the geometric center of the box.

If `origin_mode` is omitted, adapters must treat it as `"corner"`.

### 4.2 `create_cylindrical_cut`

Creates a cylindrical void by boolean subtraction from a target body.

**Step-level fields**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `target` | `string` | ✅ | `step_id` of the body to cut into. Must refer to a **prior** step. |

**Parameters** (`CreateCylindricalCutParameters`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `radius` | `number > 0` | ✅ | — | Cylinder radius |
| `depth` | `number > 0` | ✅ | — | Cylinder height (extrusion depth) |
| `position` | `[x, y, z]` | ✅ | — | Cylinder base center |
| `axis` | `[x, y, z]` | ❌ | `[0, 0, 1]` | Cylinder axis direction vector |
| `name` | `string` | ❌ | auto | FreeCAD object name for the cut feature |

**Important**: `target` lives at the **step level**, not inside `parameters`. This is because `target` is a plan graph/reference relationship, not a geometric parameter.

## 5. Confidence Levels

Per-step confidence is an **enum**, not a numeric probability:

- `certain` — The user's intent unambiguously maps to this operation.
- `inferred` — The intent is clear but requires a reasonable default (e.g., "a plate" → 10mm thickness).
- `guessed` — The intent is vague and the parameter value is a rough estimate.

Rationale: LLM numeric confidence scores are typically uncalibrated. A three-level enum communicates actionable information to downstream review without false precision.

## 6. Checks

Checks are post-execution validation predicates. They do not drive execution; they verify that the backend produced the expected geometry.

### 6.1 `bounding_box`

Validates the overall bounding box of the final geometry.

```json
{
  "check_type": "bounding_box",
  "parameters": {
    "expected_size": [120.0, 80.0, 10.0],
    "tolerance": 0.5,
    "origin_mode": "corner"
  }
}
```

- `expected_size`: `[length, width, height]` in plan units.
- `tolerance`: Absolute tolerance in plan units.
- `origin_mode`: Must match the `create_box` origin mode for consistent validation.

### 6.2 `operation_count`

Validates that the expected number of each operation type was executed.

```json
{
  "check_type": "operation_count",
  "parameters": {
    "by_operation": {
      "create_box": 1,
      "create_cylindrical_cut": 4
    }
  }
}
```

## 7. Validation Rules (Programmatic)

The `PlanValidator` (`aieng.modeling_plan.validate`) enforces the following rules in addition to schema compliance:

1. **Schema compliance** — Full JSON Schema Draft 2020-12 validation.
2. **Step ID uniqueness** — No duplicate `step_id` values.
3. **Creates uniqueness** — No duplicate `creates` identifiers.
4. **Target resolution** — For `create_cylindrical_cut`, `target` must refer to a `step_id` that appears **before** the current step.
5. **Operation whitelist** — Only `create_box` and `create_cylindrical_cut` are allowed.
6. **Required parameters** — Each operation must have its mandatory parameter keys.
7. **Assumption ref validity** — `assumption_refs` must point to existing `assumptions[].id` entries (warn, not fail).

## 8. Future Work (Out of Scope for Phase 1)

The following are explicitly **not** supported in Phase 1 and are reserved for future phases:

- Modify-existing-package workflows
- LLM-based planner (Phase 1 uses a rule-based planner)
- Family templates (`create_plate`, `create_bracket`, etc.)
- CAE operations (mesh, solve, BCs)
- Feature recognition as a plan input
- Parametric constraints and design tables
- Multi-body assemblies
