# Command Reference

This document describes all current `.aieng` CLI commands as of the Phase 15B milestone.

The CLI is invoked as `aieng <command> [arguments]`. All commands produce structured output lines beginning with `PASS` or `FAIL`.

## Global import policy

All `import-*` commands follow the same default policy. External imports are evidence/artifact ingestion only and do not automatically advance claims.

Import layers are intentionally separated:

1. **Artifact presence** - the imported file or package resource is attached and referenced.
2. **Parsed facts** - deterministic parsed observations may be written as structured data.
3. **Claim linkage** - evidence may point to tracked claim IDs so support relationships are explicit.
4. **Human review required** - claim proposals are review artifacts only; claim status changes require human review.

Claim proposals do not auto-advance. Importing alone does not change claim state.

---

## `aieng init`

**Purpose:** Create a new empty `.aieng` package with a manifest and typed empty directories.

**Nature:** No mock, rule, or user content — creates a minimal valid package skeleton.

**Required inputs:**
- `--model-id <id>` — stable string identifier for the model (e.g. `bracket_001`)
- `--out <package.aieng>` — output path; must end in `.aieng`


**Optional flags:**
- `--overwrite` — replace an existing package at the output path

**Generated outputs:**
- `<package.aieng>` — zip package containing `manifest.json` and empty typed directories

**Example:**
```bash
aieng init --model-id bracket_001 --out build/bracket_001.aieng
```

---

## `aieng import-step`

**Purpose:** Import a STEP file as geometry resources into a new `.aieng` package. Creates the package if it does not exist.

**Nature:** External geometry import only (global import policy: evidence-only by default). This command records STEP geometry resources inside the package but does not parse topology, confirm feature semantics, generate mesh/solver artifacts, or advance validation or claim state. Claim proposals require human review.

**Required inputs:**
- `<step_file>` — path to a `.step` or `.stp` file
- `--out <package.aieng>` — output package path


**Optional flags:**
- `--overwrite` — overwrite an existing package at the output path

**Generated outputs:**
- `manifest.json` — updated with geometry resource paths
- `geometry/source.step` — copy of the input STEP file
- `geometry/normalized.step` — copy of the input STEP file (Phase 1 behavior: no normalization applied yet)

**Example:**
```bash
aieng import-step examples/bracket.step --out build/bracket_001.aieng
```

---

## `aieng define`

**Purpose:** Create a definition-sourced `.aieng` package from structured YAML, without importing STEP geometry.

**Nature:** Semantic model definition only. The command writes feature, constraint, material, validation-status, completeness/missingness, and AI-readable limitation resources. It does not generate CAD geometry, topology, mesh, solver input, solver results, or manufacturing evidence.

**Required inputs:**
- `<definition_yaml>` — path to a YAML file conforming to `schemas/model_definition.schema.json`
- `--out <package.aieng>` — output package path

**Optional flags:**
- `--overwrite` — overwrite an existing package at the output path

**Generated outputs:**
- `manifest.json` — records `source_mode: definition`
- `graph/feature_graph.json` — feature definitions normalized from `feature_id` to the existing feature graph `id` field
- `graph/constraints.json` — structured constraints copied from the definition
- `engineering_context/material.yaml` — material and coordinate-system context from the definition
- `validation/status.yaml` — records `geometry_status.step_imported: false` and `geometry_status.definition_sourced: true`
- `validation/completeness_report.json` - records `source_mode: definition`, missing geometry/topology, semantic-only features, and partial simulation intent when applicable
- `README_FOR_AI.md` — explains that no STEP geometry, topology, mesh, solver, or validation evidence exists yet

**Example:**
```bash
aieng define examples/definition_simple_bracket.yaml --out build/definition_simple_bracket.aieng
aieng validate build/definition_simple_bracket.aieng
```

---

## `aieng extract-topology`

**Purpose:** Generate a topology map for an existing `.aieng` package.

**Nature:** Backend-selectable. The `--backend auto` default prefers `occ` when OCP/CadQuery runtime is detected, and falls back to `mock` otherwise. `mock` produces deterministic fixed topology without STEP parsing. `occ` performs experimental OCP/CadQuery-based real STEP extraction.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package containing `geometry/normalized.step`


**Optional flags:**
- `--overwrite` — replace an existing `geometry/topology_map.json`
- `--backend <name>` — geometry backend to use (default: `auto`; supported: `auto`, `mock`, `occ`)

**Generated outputs:**
- `geometry/topology_map.json` — topology map with stable face, edge, and body IDs; conforms to `schemas/topology_map.schema.json`; includes `metadata.extraction_backend` and `metadata.real_step_parsing` fields
- `manifest.json` — updated with topology map resource path

**Supported backends:**

| Backend | Status | Description |
|---------|--------|-------------|
| `auto` | Default | Uses `occ` when OCP is available; otherwise uses `mock` |
| `mock` | Stable | Mock-based; deterministic fixed topology; no STEP parsing; no CAD kernel required |
| `occ` | Experimental (Phase 7B.2+) | OCP/CadQuery-based real STEP extraction when OCP is installed; raises clear error if OCP is absent; pythonocc-core is detected but not yet supported |

**Example:**
```bash
aieng extract-topology build/bracket_001.aieng
aieng extract-topology build/bracket_001.aieng --backend mock
aieng extract-topology build/bracket_001.aieng --backend occ
```

---

## `aieng build-aag`

**Purpose:** Generate `graph/aag.json` for an existing `.aieng` package.

**Nature:** Generated attributed adjacency graph/index only. Builds a face-level adjacency resource derived from `geometry/topology_map.json`. The AAG is a generated index for structured reasoning and navigation. `geometry/topology_map.json` remains the topology source of truth.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package
- `geometry/topology_map.json` must already exist in the package

**Optional flags:**
- `--overwrite` — replace an existing `graph/aag.json`

**Generated outputs:**
- `graph/aag.json` — attributed adjacency graph with face nodes and adjacency arcs; conforms to `schemas/aag.schema.json`
- `manifest.json` — updated with `resources.graph.aag` when written

**Overwrite behavior:**
- Refuses to overwrite an existing `graph/aag.json` unless `--overwrite` is passed.

**Evidence and claim boundaries:**
- AAG is derived from topology metadata; it is not topology source-of-truth data.
- AAG is not automatic feature truth and does not by itself validate engineering correctness.
- AAG generation does not run meshing or solver execution.
- AAG generation does not modify geometry or execute patch operations.

**Example:**
```bash
aieng build-aag build/bracket_001.aieng
aieng build-aag build/bracket_001.aieng --overwrite
```

**Recommended pipeline (Phase 11B):**
```bash
aieng import-step examples/bracket.step --out build/bracket_001.aieng
aieng extract-topology build/bracket_001.aieng
aieng build-aag build/bracket_001.aieng
aieng recognize-features build/bracket_001.aieng
aieng validate build/bracket_001.aieng
```

---

## `aieng recognize-features`

**Purpose:** Generate a rule-based feature graph from the topology map.

**Nature:** Rule-based. Applies deterministic heuristic rules to `geometry/topology_map.json`: the largest planar face becomes the base plate candidate, cylindrical faces become hole candidates, grouped cylindrical holes become a hole pattern candidate, and remaining topology is classified as unknown features. Results are candidates only, not guaranteed engineering truth.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package containing `geometry/topology_map.json`


**Optional flags:**
- `--overwrite` — replace an existing `graph/feature_graph.json`

**Generated outputs:**
- `graph/feature_graph.json` — feature objects with stable IDs referencing topology IDs; conforms to `schemas/feature_graph.schema.json`
- `manifest.json` — updated with feature graph resource path

**Example:**
```bash
aieng recognize-features build/bracket_001.aieng
```

---

## `aieng apply-context`

**Purpose:** Apply a user-provided engineering context YAML to generate structured constraints, simulation setup, and protected region resources.

**Nature:** User-context-based. All engineering assumptions come from the supplied YAML file: material, protected features, fixed supports, force loads, and simulation targets. No engineering meaning is inferred from geometry.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package containing `graph/feature_graph.json`
- `--context <context.yaml>` — path to a context YAML file (see `examples/bracket_user_context.yaml` for format)


**Optional flags:**
- `--overwrite` — replace existing context-derived resources

**Generated outputs:**
- `graph/constraints.json` — structured constraints targeting feature IDs; conforms to `schemas/constraints.schema.json`
- `simulation/setup.yaml` — static structural simulation intent with materials, boundary conditions, and loads
- `ai/protected_regions.json` — protected feature IDs and allowed/forbidden operation summary
- `manifest.json` — updated with all three resource paths

**Example:**
```bash
aieng apply-context build/bracket_001.aieng --context examples/bracket_user_context.yaml
```

**Context YAML format:**
```yaml
material: Al6061-T6
protected_features:
  - feat_hole_pattern_001
simulation:
  type: static_structural
  fixed:
    - feat_hole_pattern_001
  loads:
    - target: feat_base_plate_001
      type: force
      value_n: 500
      direction: [1, 0, 0]
targets:
  max_von_mises_stress_mpa: 120
```

---

## `aieng summarize`

**Purpose:** Generate AI-readable derived summaries from existing structured package resources.

**Nature:** Rule-based and deterministic. Summaries are generated from `manifest.json`, `graph/feature_graph.json`, `graph/constraints.json`, `simulation/setup.yaml`, and `ai/protected_regions.json` using deterministic templates. No LLM, RAG, MCP, skill, or plugin is called. Summaries are derived views; the structured JSON/YAML resources remain the source of truth.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package; `graph/feature_graph.json` should already exist


**Optional flags:**
- `--overwrite` — replace existing summary files

**Generated outputs:**
- `README_FOR_AI.md` — top-level AI reader guide listing model ID, features, constraints, simulation intent, and key caveats
- `ai/summary.md` — detailed engineering summary with feature list, constraint list, simulation setup summary, and validation state
- `manifest.json` — updated with both summary resource paths

**Example:**
```bash
aieng summarize build/bracket_001.aieng
```

---

## `aieng apply-patch`

**Purpose:** Execute an accepted patch proposal as a semantic parameter update and record execution metadata.

**Nature:** Deterministic semantic edit scaffold. Applies supported `modify_parameter` operations to `graph/feature_graph.json`, updates patch status, and records an execution record. By default this does not perform arbitrary STEP/B-rep editing, mesh generation, solver execution, result generation, or manufacturing checks; those remain external CAD/CAE responsibilities.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package with `graph/feature_graph.json` and target patch file
- `--patch <patch_id>` — patch ID to execute (for example `patch_0001`)

**Optional flags:**
- `--out <path>` — optional external path for a modified STEP copy when a future write-back path exists
- `--overwrite` — overwrite existing execution output when allowed

**Generated outputs:**
- `graph/feature_graph.json` — updated semantic parameter values for supported `modify_parameter` operations
- `ai/patches/patch_NNNN.json` — updated with execution status and `execution_record`
- optional external STEP copy only when a future executable write-back path exists

**Guardrails:**
- mock and OCP-extracted feature parameters are semantic-only by default
- unsupported geometry operations such as `add_feature` or `remove_feature` are rejected
- executable CAD write-back is reserved for a future `cadquery_parametric` + `executable_by_regeneration` + `cadquery_regeneration` path
- patch execution does not imply solver validation, mesh generation by `.aieng`, or engineering safety

**Example:**
```bash
aieng apply-patch build/bracket_001.aieng --patch patch_0001
```

---

## `aieng propose-patch`

**Purpose:** Generate a structured, unexecuted patch proposal from existing package resources and a user intent string.

**Nature:** Rule-based and deterministic. Patch proposals are generated by matching the intent string against known rule patterns (for example mass reduction, load-assignment, or boundary-condition intents). Protected features from `ai/protected_regions.json` are checked and explicitly flagged. When `graph/allowed_operations_catalog.json` is present, operation admissibility is policy-gated and forbidden operations are downgraded to `needs_review` rather than emitted as executable proposals. No geometry is modified, no solver is run, and no LLM is called. The proposal must be validated and executed separately.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package with `graph/feature_graph.json`
- `--intent "<text>"` — user intent string describing the desired engineering change

**Optional context inputs used when present:**
- `ai/protected_regions.json` — protected-target avoidance and checks
- `graph/constraints.json` — additional protected target inference
- `objects/interface_graph.json` and `graph/allowed_operations_catalog.json` — role-aware target selection and operation policy gating

**Generated outputs:**
- `ai/patches/patch_NNNN.json` — structured patch proposal with stable patch ID, operations list targeting feature IDs, protected-feature checks, expected effects, and required validation steps; conforms to `schemas/patch_proposal.schema.json`
- `manifest.json` — updated with the new patch path

**Note:** `propose-patch` does not support `--overwrite`. Each invocation creates the next numbered patch file.

**Example:**
```bash
aieng propose-patch build/bracket_001.aieng --intent "Reduce mass by 15% while keeping mounting holes unchanged."
```

---

## `aieng export-updated-deck`

**Purpose:** Export an updated CalculiX deck scaffold reflecting the current `simulation/setup.yaml` state for external CAE software.

**Nature:** Deterministic CAE scaffold export. Writes `simulation/updated_deck.inp` using the current semantic simulation state. This remains a scaffold only: no mesh, no node sets, no element sets, no solver run, and no result import. External CAE software remains responsible for producing mesh, node/element sets, solver execution, and results.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package containing `simulation/setup.yaml`

**Optional flags:**
- `--out <path>` — optional external path for a copied updated deck
- `--overwrite` — overwrite existing `simulation/updated_deck.inp`

**Generated outputs:**
- `simulation/updated_deck.inp` — updated scaffold deck based on current `simulation/setup.yaml`
- `manifest.json` — updated with `resources.simulation.updated_deck`
- `validation/status.yaml` — when present, CAE import/export status fields are refreshed

**Behavior notes:**
- reflects current semantic setup values, including modified material properties when present
- includes scaffold markers and current-state language
- does not create a complete runnable FEA model
- does not make `.aieng` a mesher or solver

**Example:**
```bash
aieng export-updated-deck build/bracket_001.aieng
aieng export-updated-deck build/bracket_001.aieng --out build/updated_deck.inp
```

---

## `aieng update-validation-status`

**Purpose:** Generate `validation/status.yaml` for an existing `.aieng` package — a machine-readable record of what has and has not been validated.

**Nature:** Deterministic and rule-based. Inspects which resources are present inside the package and records their status. No geometry parsing, meshing, solving, or LLM call is performed.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package


**Optional flags:**
- `--overwrite` — replace an existing `validation/status.yaml`

**Generated outputs:**
- `validation/status.yaml` — YAML document with 8 sections: `package_validation`, `geometry_status`, `topology_status`, `feature_status`, `engineering_context_status`, `solver_mesh_status`, `patch_status`, and `claim_policy`
- `manifest.json` — updated with `resources.validation.status = "validation/status.yaml"`

**Status file structure:**
```yaml
generated_by: aieng 0.1.0
model_id: bracket_001
package_format_version: "0.1.0"
generated_at: "2026-05-06T..."
package_validation:
  package_resources_present: true
  manifest_present: true
  structured_resources_validated: structurally_checked
geometry_status:
  source_geometry_present: true
  normalized_geometry_present: true
  real_geometry_parsing: not_run
  real_geometry_validity: not_run
  reason: "No real CAD kernel validation in current phase."
topology_status:
  topology_map_present: true
  extraction_mode: mock
  status: mock_generated
  warning: "Topology map is deterministic mock data."
feature_status:
  feature_graph_present: true
  recognition_mode: rule_based
  status: candidate_only
  warning: "Feature labels are candidates, not confirmed engineering truth."
engineering_context_status:
  context_source: user_provided
  status: structured_context_present
  protected_regions_present: true
  simulation_intent_present: true
  solver_deck_scaffold_present: true
solver_mesh_status:
  mesh_generation: not_run
  solver_execution: not_run
  stress_validation: not_validated
  displacement_validation: not_validated
  manufacturing_validation: not_run
patch_status:
  patch_proposals_present: true
  patch_execution: not_run
  geometry_modified_by_patch: false
  solver_run_for_patch: false
  patch_validation_required: true
claim_policy:
  allowed_claims:
    - "The package contains structured engineering context."
    - ...
  forbidden_claims:
    - "The design is safe."
    - "A solver has been run."
    - ...
```

**Example:**
```bash
aieng update-validation-status build/bracket_001.aieng
aieng validate build/bracket_001.aieng
```

---

## `aieng write-completeness-report`

**Purpose:** Generate `validation/completeness_report.json` for an existing `.aieng` package. The report records what CAD/CAE information is available, partial, missing, unknown, unsupported, conflicting, or not applicable.

**Nature:** Deterministic best-effort conversion report. It inspects package resources and writes explicit missingness instead of inferring absent CAD/CAE facts. No CAD geometry editing, mesh generation, solver execution, optimization, manufacturing check, or LLM call is performed.

**Required inputs:**
- `<package.aieng>` - path to an existing `.aieng` package

**Optional flags:**
- `--overwrite` - replace an existing `validation/completeness_report.json`

**Generated outputs:**
- `validation/completeness_report.json` - JSON report with `conversion_mode: best_effort`, claim policy const guards, category statuses, missing items, and next recommended actions
- `manifest.json` - updated with `resources.validation.completeness_report = "validation/completeness_report.json"`

**Status vocabulary:**
- `available` - structured resources exist for this category
- `partial` - some structured information exists, but it is incomplete or candidate-level
- `missing` - information is absent and should not be guessed
- `unknown` - the package cannot determine whether the information should exist
- `unsupported` - a claim or result lacks evidence; unsupported is not false
- `conflicting` - resources disagree and need review
- `not_applicable` - not needed for the current package/task state

**Example:**
```bash
aieng write-completeness-report build/bracket_001.aieng
aieng summarize build/bracket_001.aieng --overwrite
aieng validate build/bracket_001.aieng
```

---

## `aieng write-evidence-report`

**Purpose:** Generate `validation/evidence_report.json` as a consolidated validation-state read view.

**Nature:** Deterministic derived-view generation. Aggregates existing ledgers from `validation/status.yaml` and `results/evidence_index.json`. This resource is read-model convenience only; source ledgers remain authoritative.

**Required inputs:**
- `<package.aieng>` - path to an existing `.aieng` package containing:
  - `validation/status.yaml`
  - `results/evidence_index.json`

**Optional flags:**
- `--overwrite` - replace an existing `validation/evidence_report.json`

**Generated outputs:**
- `validation/evidence_report.json` - consolidated claim/evidence summary with status counts, per-claim linked evidence pointers, and validation-state snapshot
- `manifest.json` - updated with `resources.validation.evidence_report = "validation/evidence_report.json"`

**Behavior notes:**
- Derived view only; no claim auto-advance.
- If source ledgers disagree, validators should report consistency failures.
- No CAD/CAE execution implied.

**Example:**
```bash
aieng write-evidence-report build/bracket_001.aieng
aieng summarize build/bracket_001.aieng --overwrite
aieng validate build/bracket_001.aieng
```

---

## `aieng write-mesh-handoff`

**Purpose:** Generate `simulation/mesh_handoff_contract.json` for external meshing workflows.

**Nature:** Handoff contract generation only. `.aieng` writes a structured meshing contract but does not execute Gmsh or generate mesh files.

**Required inputs:**
- `<package.aieng>` - path to an existing `.aieng` package containing `geometry/topology_map.json`

**Optional flags:**
- `--overwrite` - replace an existing `simulation/mesh_handoff_contract.json`

**Generated outputs:**
- `simulation/mesh_handoff_contract.json` - JSON handoff contract for external meshing
- `manifest.json` - updated with `resources.simulation.mesh_handoff_contract`

**Contract content (summary):**
- geometry source path (`geometry/normalized.step` preferred)
- mesher target and mesh recommendations
- topology IDs for meshing reference (`body_ids`, `face_ids`, `edge_ids`)
- target claim IDs expected to be supported by mesh evidence
- execution boundary flags (`external_tools_execute: true`, `aieng_core_executes_mesher: false`)

**Example:**
```bash
aieng write-mesh-handoff build/bracket_001.aieng
aieng validate build/bracket_001.aieng
```

---

## `aieng import-cae-deck`

**Purpose:** Import a CAE deck scaffold into an existing `.aieng` package and generate deterministic parsed CAE resources.

**Nature:** External CAE deck import only (global import policy: evidence-only by default). Phase 10A supports `--format calculix` and parses only minimal cards: `*MATERIAL` (`*ELASTIC`, `*DENSITY`), `*BOUNDARY`, and `*CLOAD`. It records parsed CAE artifacts and conservative mapping scaffolds only; it does not run a solver, generate a mesh, validate CAE-to-geometry meaning, or advance claim state. It does not automatically change claim status. Claim proposals require human review.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package
- `--deck <solver_deck.inp>` — path to external CAE deck text file
- `--format calculix` — deck format (Phase 10A)


**Optional flags:**
- `--overwrite` — replace existing CAE import scaffold resources if already present

**Generated outputs:**
- `simulation/cae_imports/source_solver_deck.inp` — imported source CAE deck text
- `simulation/cae_imports/parsed_materials.json` — parsed materials (`name`, optional elastic, optional density)
- `simulation/cae_imports/parsed_boundary_conditions.json` — parsed boundary conditions (`id`, `target`, DOF range, value)
- `simulation/cae_imports/parsed_loads.json` — parsed loads (`id`, `target`, `dof`, `value`)
- `simulation/cae_mapping.json` — conservative mapping scaffold to feature/interface IDs (default unmapped; no automatic CAE-to-geometry inference)
- `manifest.json` — updated with all CAE import resource paths under `resources.simulation`

**Example:**
```bash
aieng import-cae-deck build/bracket_001.aieng --deck examples/bracket_loadcase.inp --format calculix
```

---

## `aieng apply-cae-mapping`

**Purpose:** Apply explicit user-provided CAE entity mappings to `simulation/cae_mapping.json`.

**Nature:** Explicit mapping only. Reads user YAML and applies mappings from CAE target names (for example `FIXED_HOLES`, `LOAD_FACE`) to `.aieng` `feature_id` and/or `interface_id`. It does not infer mappings automatically, does not run a solver, and does not import results.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package
- `--mapping <mapping.yaml>` — user mapping YAML



**Prerequisite for `interface_id` mappings:**
- If the mapping YAML references `maps_to.interface_id`, the package must already contain `objects/interface_graph.json`.
- Run `aieng build-interface-graph <package.aieng> --overwrite` before applying such mappings.
- The reference mapping file `examples/bracket_cae_mapping.yaml` maps `FIXED_HOLES` to `iface_feat_hole_pattern_001`, so it requires this prerequisite.
- Phase 10C note: rerun `aieng build-interface-graph <package.aieng> --overwrite` after `aieng apply-cae-mapping` to enrich `objects/interface_graph.json` with derived `cae_refs`.

**Optional flags:**
- `--overwrite` — overwrite existing mapped entries in `simulation/cae_mapping.json`

**Validation behavior:**
- mapping YAML must include `mappings` list
- each mapping must include `cae_entity` and `maps_to` with at least one of `cae_target_id` (the BC/load id it serves), `feature_id`, or `interface_id`
- `mapping_method` must be `user_provided`
- `confidence` must be one of `high`, `medium`, `low`
- referenced `feature_id` values must exist in `graph/feature_graph.json`
- referenced `interface_id` values must exist in `objects/interface_graph.json`

**Generated outputs:**
- `simulation/cae_mapping.json` — updated in place with explicit mapped entries
- mapping metadata includes source mapping file tracking

**Example:**
```bash
aieng build-interface-graph build/bracket_001.aieng --overwrite
aieng apply-cae-mapping build/bracket_001.aieng --mapping examples/bracket_cae_mapping.yaml --overwrite
aieng build-interface-graph build/bracket_001.aieng --overwrite
```

---

## `aieng build-visual-index`

**Purpose:** Generate `visual/annotation_layers.json` for an existing `.aieng` package.

**Nature:** Annotation scaffold only. Reads `graph/feature_graph.json` (required), and optionally `geometry/topology_map.json`, `ai/protected_regions.json`, `graph/constraints.json`, and `simulation/setup.yaml`. No rendering, glTF, image, or 3D geometry visualization is performed.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package containing `graph/feature_graph.json`


**Optional flags:**
- `--overwrite` — replace an existing `visual/annotation_layers.json`

**Generated outputs:**
- `visual/annotation_layers.json` — structured annotation scaffold with layers: `features`, `protected_regions`, `simulation_targets`, `unknown_or_unclassified`
- `manifest.json` — updated with `resources.visual.annotation_layers`

**Annotation layers:**

| Layer ID | Content | `visual_role` |
|----------|---------|---------------|
| `features` | All features from feature graph | `candidate_feature` |
| `protected_regions` | Protected features with forbidden/allowed operations | `protected_region` |
| `simulation_targets` | Boundary condition and load targets from simulation setup | `simulation_context` |
| `unknown_or_unclassified` | Features with type `unknown_feature` | `unclassified_geometry` |

Each annotation item references a `feature_id` from the feature graph and optionally topology face/edge IDs from the topology map.

**Example:**
```bash
aieng build-visual-index build/bracket_001.aieng --overwrite
aieng validate build/bracket_001.aieng
```

---

## `aieng build-visual-manifest`

**Purpose:** Generate `visual/model_manifest.json` for an existing `.aieng` package.

**Nature:** Visual claim scaffold only. Records which visual resources are present versus not generated, and encodes explicit no-rendering claim policy for Phase 8B. No rendering, glTF generation, screenshot generation, or viewer output is performed.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package


**Optional flags:**
- `--overwrite` — replace an existing `visual/model_manifest.json`

**Generated outputs:**
- `visual/model_manifest.json` — visual resource manifest with `visual_resources`, `rendering_status`, and `claim_policy`
- `manifest.json` — updated with `resources.visual.model_manifest`

**Behavior notes:**
- Works whether `visual/annotation_layers.json` exists or not.
- Marks annotation layer status as `present` when available, otherwise `missing`.
- Keeps rendered/viewer flags false in Phase 8B.

**Example:**
```bash
aieng build-visual-manifest build/bracket_001.aieng --overwrite
aieng validate build/bracket_001.aieng
```

---

## `aieng build-object-registry`

**Purpose:** Generate `objects/object_registry.json` for an existing `.aieng` package.

**Nature:** Generated navigation index only. Builds a cross-file object and relationship index from available structured resources so AI readers and deterministic tooling can discover object IDs, definitions, and references quickly. It does not replace any source JSON/YAML file as authority.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package


**Optional flags:**
- `--overwrite` — replace an existing `objects/object_registry.json`

**Generated outputs:**
- `objects/object_registry.json` — object/relationship index scaffold
- `manifest.json` — updated with `resources.objects.object_registry`

**Indexed source mappings (Phase 9A):**
- `geometry/topology_map.json` → topology entities
- `graph/feature_graph.json` → features + parent/child and feature/topology relationships
- `graph/constraints.json` → constraints + target relationships
- `simulation/setup.yaml` → simulation, materials, boundary conditions, loads
- `ai/protected_regions.json` → protected region records and feature relationships
- `ai/patches/*.json` → patches and patch operations
- `visual/annotation_layers.json` → visual annotations and feature/topology targeting
- `visual/model_manifest.json` → visual resource entries
- `validation/status.yaml` → validation status object

**Behavior notes:**
- Optional resources may be missing; command still succeeds.
- Unresolved references are represented as `kind: unresolved_reference` with `status: unresolved`.
- Registry ordering is deterministic.

**Example:**
```bash
aieng build-object-registry build/bracket_001.aieng --overwrite
aieng validate build/bracket_001.aieng
```

---

## `aieng build-interface-graph`

**Purpose:** Generate `objects/interface_graph.json` for an existing `.aieng` package.

**Nature:** Generated interface index only. Builds interface records from existing structured context and identifies interface-related roles such as mounting candidates, protected interfaces, fixed-support interfaces, and load-application interfaces. In Phase 10C, if `simulation/cae_mapping.json` is present, it also adds derived `cae_refs` from explicit user-provided CAE mappings. It does not infer CAE mappings automatically and does not replace source JSON/YAML files as authority.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package with `graph/feature_graph.json`


**Optional flags:**
- `--overwrite` — replace an existing `objects/interface_graph.json`

**Generated outputs:**
- `objects/interface_graph.json` — interface index scaffold
- `manifest.json` — updated with `resources.objects.interface_graph`

**Phase 10C enrichment behavior:**
- When `simulation/cae_mapping.json` is present, `build-interface-graph` copies mapped CAE entities into interface `cae_refs`.
- This enrichment uses explicit mappings only. It does not infer mappings automatically, run CAE, import solver results, generate a mesh, or modify geometry.

**Source mappings (Phase 9B):**
- `graph/feature_graph.json` — mounting interface candidates and topology refs
- `graph/constraints.json` — interface-related constraint links
- `simulation/setup.yaml` — fixed boundary condition and load interface links
- `ai/protected_regions.json` — protected interface flags and allowed/forbidden operations
- `visual/annotation_layers.json` — visual annotation references for interface features
- `objects/object_registry.json` — listed as source context when present

**Behavior notes:**
- Optional source resources may be missing; command still succeeds if `graph/feature_graph.json` exists.
- No assembly graph, mating constraints, geometry modification, patch execution, meshing, or solver run is performed.
- `objects/interface_graph.json` is generated index data and not source-of-truth.
- If you want object registry entries for interfaces, run `aieng build-object-registry` after this command.

**Example:**
```bash
aieng build-interface-graph build/bracket_001.aieng --overwrite
aieng validate build/bracket_001.aieng
```

---

## `aieng build-allowed-operations-catalog`

**Purpose:** Generate `graph/allowed_operations_catalog.json` for an existing `.aieng` package.

**Nature:** Generated operation-policy index only. The catalog aggregates per-feature operation admissibility from existing structured resources. It is intended for patch planning guidance and does not execute CAD/CAE operations.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package with `graph/feature_graph.json`

**Optional flags:**
- `--overwrite` — replace an existing `graph/allowed_operations_catalog.json`

**Generated outputs:**
- `graph/allowed_operations_catalog.json` — per-feature operation admissibility catalog
- `manifest.json` — updated with `resources.graph.allowed_operations_catalog`

**Source mappings (Phase 19 C3 minimal/enhanced):**
- `graph/feature_graph.json` — feature IDs and feature types
- `ai/protected_regions.json` — protected flags affecting operation status
- `graph/constraints.json` — blocking constraint references by feature
- `objects/interface_graph.json` — interface roles used to refine preconditions for load/BC assignment when present

**Behavior notes:**
- `modify_parameter` and `remove_feature` are marked `forbidden` for protected features.
- `assign_boundary_condition` and `assign_load` include role-aware preconditions if interface roles are available.
- The catalog is advisory policy data; source-of-truth remains the structured source resources and validator checks.

**Example:**
```bash
aieng build-allowed-operations-catalog build/bracket_001.aieng --overwrite
aieng validate build/bracket_001.aieng
```

---

## `aieng geometry-backends`

**Purpose:** List available geometry backends and their dependency status.

**Nature:** Diagnostic only. Uses `importlib.util.find_spec` to detect optional geometry runtimes without importing them. No package is created or modified.

**Required inputs:** None.

**Generated outputs:** None. Backend status is printed to stdout.

**Output format:**
```text
Geometry backends:
  mock: available
  occ: not available — No supported OCC runtime found. Install pythonocc-core or OCP/CadQuery.
```

Or, when OCP/CadQuery is installed:
```text
Geometry backends:
  mock: available
  occ: runtime detected (OCP/CadQuery) — experimental real STEP extraction available (Phase 7B.2)
```

Or, when only pythonocc-core is installed:
```text
Geometry backends:
  mock: available
  occ: runtime detected (pythonocc-core) — Phase 7B.2 implements OCP/CadQuery only; pythonocc-core extraction not yet supported
```

**Phase 7B.2/7C status:** The `mock` backend is always available. The `occ` backend detects whether OCP (CadQuery) or pythonocc-core is installed. If OCP is installed, `aieng extract-topology --backend occ` performs experimental real STEP parsing. If only pythonocc-core is installed, a `NotImplementedError` is raised with instructions to install CadQuery instead. If no OCC runtime is installed, a clear install hint is printed.

For an end-to-end OCP topology demo, see [docs/ocp_topology_demo.md](ocp_topology_demo.md) or run:
```bash
python scripts/run_ocp_topology_demo.py path/to/model.step
```

Install: `pip install cadquery`

**Example:**
```bash
aieng geometry-backends
```

---

## `aieng validate`

**Purpose:** Validate a `.aieng` package and report pass/warn/fail for each check.

**Nature:** Deterministic validation. No geometry parsing, arbitrary CAD write-back, meshing, solving, or LLM call. Validation also emits a global reminder that import pathways do not automatically advance claim state.

**Required inputs:**
- `<package.aieng>` — path to a `.aieng` package

**Generated outputs:** None. Validation results are printed to stdout.

**Output format:**
```text
PASS manifest.json exists
PASS format_version = 0.1.0
PASS geometry/source.step exists
WARN graph/semantic_graph.json missing
FAIL feature feat_hole_001 references unknown face face_999
```

Exit code is `0` if all checks pass, `1` if any check fails.

**Checks performed:**
- `manifest.json` exists and conforms to schema
- Format version is supported
- All resource paths listed in `manifest.json` exist inside the package
- JSON resources conform to their schemas
- When `graph/aag.json` is present: schema conformance, unique node/arc IDs, node topology face references resolve to known face entities, arc endpoint references resolve to known nodes, and `shared_edge_ids` resolve to known topology edges
- Feature IDs in `graph/feature_graph.json` reference topology IDs that exist in `geometry/topology_map.json`
- Feature parameter guardrails in `graph/feature_graph.json` remain internally consistent (`parameter_source`, `editability`, `writeback_strategy`)
- Constraint targets reference feature IDs that exist in `graph/feature_graph.json`
- Simulation setup references feature IDs that exist in `graph/feature_graph.json`
- Patch proposals do not modify protected targets without explicit violation status
- Applied patch execution records remain consistent with semantic-only no-geometry-modified claims unless a future executable regeneration path is explicitly declared
- When `simulation/updated_deck.inp` is referenced in `manifest.json`: file is non-empty text, contains scaffold warning/current-state markers, and does not assert solver completion
- When `simulation/solver_deck.inp` is referenced in `manifest.json`: file is non-empty text, contains scaffold warning, does not assert solver completion
- When `validation/status.yaml` is referenced in `manifest.json`: file is valid YAML, contains all required sections, claim policy is present, solver/mesh status does not make false claims, patch status correctly records no geometry modification
- When `visual/annotation_layers.json` is present: schema conformance, layer IDs unique, annotation item IDs unique, `feature_id` values exist in feature graph, topology refs exist in topology map
- When `visual/model_manifest.json` is present: schema conformance, `status: present` resources must exist, Phase 8B rendering flags (`rendered_geometry_present`, `viewer_ready`) must be false, forbidden visual claims must include rendered 3D/model.glb-not-present language
- When `objects/object_registry.json` is present: schema conformance, object IDs unique, relationship endpoints resolve to known/unresolved objects, `defined_in`/`referenced_by` paths exist, and notes must explicitly state registry is not source-of-truth
- When `objects/interface_graph.json` is present: schema conformance, interface IDs unique, `feature_ids` resolve to known features, topology refs resolve when topology exists, constraint/simulation/visual refs resolve when corresponding source files exist, protected interfaces enforce protected-operation semantics, and notes must state generated-index/not-source-of-truth policy
- Missing optional resources are reported as warnings, not failures

**Example:**
```bash
aieng validate build/bracket_001.aieng
```

---

## `aieng export-calculix`

**Purpose:** Export a CalculiX scaffold deck from `simulation/setup.yaml` for external CAE software. The output is a text `.inp` file containing material definitions, boundary condition intent, load intent, validation targets, and protected-region notes as CalculiX comments. No mesh is generated, no solver is run, and the output is not a complete runnable FEA model.

**Nature:** Rule-based and deterministic. Reads structured resources from the package and formats them into CalculiX `.inp` syntax. It prepares a scaffold for external CAE tooling; `.aieng` core does not call Gmsh, CalculiX, pyccx/pygccx, a CAD parser, an LLM, or an external dependency.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package containing `simulation/setup.yaml`

**Optional inputs:**
- `graph/feature_graph.json` — used to name features in BC and load sections
- `ai/protected_regions.json` — used to include protected-region notes

**Optional flags:**
- `--out <path>` — also write the deck to an external filesystem path (e.g. `build/solver_deck.inp`)
- `--overwrite` — replace existing `simulation/solver_deck.inp` inside the package and/or the `--out` path

**Generated outputs:**
- `simulation/solver_deck.inp` inside the `.aieng` package — scaffold deck as a CalculiX-style `.inp` text file
- `<--out path>` if `--out` is provided — copy of the same deck at the specified external path
- `manifest.json` — updated with `resources.simulation.solver_deck = "simulation/solver_deck.inp"`

**What the deck contains:**
- Header comments clearly marking it as a scaffold, not a runnable FEA model
- Material block: `*MATERIAL`, `*ELASTIC`, `*DENSITY` populated from `simulation/setup.yaml`
- Boundary condition intent (as comments with feature IDs — no node sets generated)
- Load intent (as comments with feature IDs, force values, and direction — no node mapping)
- Validation targets (e.g. `max_von_mises_stress_mpa < 120`)
- Protected-region notes from `ai/protected_regions.json` (if present)
- Missing mesh notice and required next steps

**What the deck does NOT contain:**
- `*NODE` — no mesh node coordinates
- `*ELEMENT` — no element connectivity
- `*NSET` / `*ELSET` — no node/element sets
- `*BOUNDARY` — no active boundary condition definitions
- `*CLOAD` — no active force load definitions
- `*STEP` / `*STATIC` — no analysis step

**Example:**
```bash
aieng export-calculix build/bracket_001.aieng --out build/solver_deck.inp --overwrite
```

**Full Phase 6A chain:**
```bash
aieng import-step examples/bracket.step --out build/bracket_001.aieng --overwrite
aieng extract-topology build/bracket_001.aieng --overwrite
aieng recognize-features build/bracket_001.aieng --overwrite
aieng apply-context build/bracket_001.aieng --context examples/bracket_user_context.yaml --overwrite
aieng summarize build/bracket_001.aieng --overwrite
aieng propose-patch build/bracket_001.aieng --intent "Reduce mass by 15% while keeping mounting holes unchanged."
aieng export-calculix build/bracket_001.aieng --out build/solver_deck.inp --overwrite
aieng validate build/bracket_001.aieng
```

---

## `aieng write-task-spec`

**Purpose:** Write a structured task specification (`task/task_spec.yaml`) to an existing `.aieng` package. The task spec records the agent task intent, required outputs, forbidden claims, and claim policy so agents understand what they are asked to do and what they must not assert before proposing CAD/CAE actions.

**Nature:** Deterministic writer. Generates a fixed-shape YAML document from the provided intent, mode, and optional task ID. No geometry access, no feature recognition, no solver, no mesh.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package
- `--intent "<task intent>"` — human-readable task intent

**Optional flags:**
- `--task-id <id>` — stable task identifier (default: `task_001`)
- `--mode <mode>` — task execution mode: `proposal_only` | `analysis_ready` | `execution_ready` (default: `proposal_only`)
- `--overwrite` — replace an existing `task/task_spec.yaml`

**Generated outputs:**
- `task/task_spec.yaml` inside the `.aieng` package
- `manifest.json` — updated with `resources.task.task_spec = "task/task_spec.yaml"`

**claim_policy guarantee:** All generated task specs carry `no_solver_run_claim: true`, `no_mesh_generation_claim: true`, and `no_geometry_modification_claim: true`. These cannot be set to false via the CLI.

**Validator behavior (when task spec is present):**
- valid YAML, schema conformance, recognized mode, recognized required outputs, non-empty forbidden_claims
- `claim_policy` flags must all be `true`
- WARN if `mode != proposal_only`

**Example:**
```bash
aieng write-task-spec build/bracket_001.aieng \
  --intent "Reduce mass by 15% while keeping mounting holes unchanged."
```

---

## `aieng write-external-tool-requirements`

**Purpose:** Write a structured external tool handoff contract (`task/external_tool_requirements.json`) into an existing `.aieng` package. The contract describes which external CAD/CAE capabilities are required, which candidate tools may provide them, and what evidence must be written back after execution.

**Nature:** Deterministic rule-based generation. Reads `task/task_spec.yaml` if present and copies its `task_id` into `source_task_id`. Does not call FreeCAD, Gmsh, CalculiX, sim-cli, mechanical_agent, or any other external tool. Candidate tools listed have status `candidate` — none are installed or invoked.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package

**Optional flags:**
- `--handoff-id <id>` — stable handoff identifier (default: `handoff_001`)
- `--overwrite` — replace an existing `task/external_tool_requirements.json`

**Generated outputs:**
- `task/external_tool_requirements.json` inside the `.aieng` package
- `manifest.json` — updated with `resources.task.external_tool_requirements`

**Execution boundary guarantee:** All generated handoff contracts carry:
- `handoff_policy.external_tools_execute: true`
- `handoff_policy.aieng_core_executes_external_tools: false`
- `forbidden_core_actions` including `run_solver`, `generate_mesh`, and `modify_cad_geometry`

These cannot be set to violating values via the CLI.

**Validator behavior (when resource is present):**
- valid JSON, schema conformance
- `handoff_policy` execution-boundary flags must be correctly set
- known `tool_role` and `status` values
- non-empty `forbidden_core_actions` and `writeback_requirements`
- WARN if `source_task_id` is set but `task/task_spec.yaml` is absent

**Example:**
```bash
aieng write-external-tool-requirements build/bracket_001.aieng
aieng validate build/bracket_001.aieng
```

---

## `aieng write-evidence-scaffold`

**Purpose:** Write `results/evidence_index.json` to an existing `.aieng` package. This structured resource records what evidence from external tools is present and the claim-policy execution boundary.

**Nature:** Deterministic scaffold generator. Reads current package state to seed evidence items. No solver execution, mesh generation, or CAD modification is performed or claimed.

**Required inputs:**
- `<package.aieng>` — path to an existing `.aieng` package

**Optional flags:**
- `--overwrite` — replace existing `results/evidence_index.json`

**Generated outputs:**
- `results/evidence_index.json` inside the `.aieng` package
- `manifest.json` — updated with `resources.results.evidence_index`

**Behavior:**
- If `task/task_spec.yaml` is present: adds `ev_task_spec_001` evidence item
- If `task/external_tool_requirements.json` is present: adds `ev_handoff_001` evidence item

**Execution boundary guarantee:** All generated evidence resources carry:
- `claim_policy.external_tools_execute: true`
- `claim_policy.aieng_core_generates_solver_evidence: false`
- `claim_policy.aieng_core_generates_mesh_evidence: false`
- `claim_policy.aieng_core_modifies_cad_geometry: false`

**Validator behavior (when resources are present):**
- valid JSON, schema conformance
- `claim_policy` execution-boundary flags must be correctly set
- evidence IDs must be unique within `evidence_index.json`

**Example:**
```bash
aieng write-task-spec build/bracket_001.aieng --intent "Reduce mass by 15%."
aieng write-external-tool-requirements build/bracket_001.aieng
aieng write-evidence-scaffold build/bracket_001.aieng
aieng validate build/bracket_001.aieng
```

---

## `aieng record-evidence`

**Purpose:** Record externally produced evidence in `results/evidence_index.json`.

**Nature:** Writeback-only metadata recording. This command does not run CAD, meshing, solving, post-processing, optimization, or manufacturing checks.

**Required inputs:**
- `<package.aieng>` — path to an existing package that already contains `results/evidence_index.json`
- `--kind solver_result|mesh_evidence|geometry_modification|validation_report`
- `--producer-kind external_cad|external_cae|external_solver|external_agent|aieng_core`
- `--producer-tool <tool_id>`
- `--artifact-kind yaml|json|inp|step|result_file`
- `--artifact-path <path>`
- `--claim-support <claim_id>[,<claim_id>...]`

**Optional flags:**
- `--evidence-id <id>` — explicit ID; if omitted, the next deterministic ID is generated by evidence kind
- `--verification-status available|missing|unverified|schema_validated` (default: `available`)
- `--notes "..."` — repeatable

**Behavior notes:**
- Fails if scaffold resources are missing; run `aieng write-evidence-scaffold <package.aieng>` first.
- Rejects empty or unknown claim IDs in `--claim-support`.
- Rejects duplicate evidence IDs.
- Rejects `producer-kind aieng_core` for `solver_result`, `mesh_evidence`, and `geometry_modification`.

**Example:**
```bash
aieng record-evidence build/bracket_001.aieng \
  --kind solver_result \
  --producer-kind external_solver \
  --producer-tool ccx_2_21 \
  --artifact-kind result_file \
  --artifact-path external/solver/job_001.dat \
  --claim-support claim_solver_result_001
```

---

## `aieng import-solver-evidence`

**Purpose:** Import an external solver result file and record it as evidence in `results/evidence_index.json`.

**Nature:** External solver-result import only (global import policy: evidence-only by default). This command records imported solver result artifacts and known marker observations in the evidence ledger. It does not execute a solver and does not automatically change claim status. Claim proposals require human review.

**Required inputs:**
- `<package.aieng>` - path to an existing package that already contains `results/evidence_index.json`
- `--result-file <path>` - external solver result file path
- `--format calculix_dat`

**Optional flags:**
- `--producer-tool <tool_id>` (default: `calculix`)
- `--claim-support <claim_id>[,<claim_id>...]` (default: `claim_solver_result_001`)
- `--verification-status available|missing|unverified|schema_validated` (default: `unverified`)
- `--evidence-id <id>`
- `--notes "..."` - repeatable

**Behavior notes:**
- Uses explicit known-pattern extraction only and records observations deterministically:
  - keyword marker counts remain available in evidence notes
  - supported numeric observations are written to `structured_payload` when present (for example max von Mises and max displacement from known `calculix_dat` text patterns)
  - unmatched observations are recorded explicitly as `unknown` or `unsupported`; they are not inferred
- Returns claim review suggestions derived from parsed numeric observations, but these are advisory only.
- Fails if evidence scaffold resources are missing; run `aieng write-evidence-scaffold <package.aieng>` first.
- Claim verification status remains unchanged; claim proposals require human review.

**Example:**
```bash
aieng import-solver-evidence build/bracket_001.aieng \
  --result-file external/solver/job_001.dat \
  --format calculix_dat \
  --producer-tool ccx_2_21 \
  --claim-support claim_solver_result_001
```

---

## `aieng import-mesh-evidence`

**Purpose:** Import an external mesh artifact file and record it as evidence in `results/evidence_index.json`.

**Nature:** External mesh import only (global import policy: evidence-only by default). This command copies imported mesh artifacts into the `.aieng` package by default, records known deterministic mesh summary observations in the evidence ledger, and stores a structured mesh payload for traceability. It does not execute meshing and does not automatically change claim status. Claim proposals require human review.

**Required inputs:**
- `<package.aieng>` - path to an existing package that already contains `results/evidence_index.json`
- `--mesh-file <path>` - external mesh artifact path
- `--format gmsh_msh`

**Optional flags:**
- `--producer-tool <tool_id>` (default: `gmsh`)
- `--claim-support <claim_id>[,<claim_id>...]` (default: `claim_mesh_evidence_001`)
- `--verification-status available|missing|unverified|schema_validated` (default: `unverified`)
- `--evidence-id <id>`
- `--reference-only` - record an external mesh reference without copying the artifact into the package
- `--package-path <path>` - optional package-relative path for copied mesh artifacts (default: `results/mesh_artifacts/<evidence_id>.msh`)
- `--notes "..."` - repeatable

**Behavior notes:**
- Uses explicit known-pattern extraction only and records observations in `structured_payload` plus evidence notes.
- Default storage mode is `copied_into_package`; the mesh artifact is written under `results/mesh_artifacts/` using a deterministic evidence ID path.
- `--reference-only` records `structured_payload.artifact.storage_mode: external_reference` and leaves the external file outside the package.
- For `gmsh_msh`, records deterministic summary fields when present, such as declared node/element counts from known section headers.
- Declared node/element counts are intake metadata only; they are not mesh quality validation.
- Quality metrics are not inferred from mesh presence. If quality evidence is absent, quality remains unknown.
- Unknown or unmappable semantics are not guessed.
- Fails if evidence scaffold resources are missing; run `aieng write-evidence-scaffold <package.aieng>` first.
- Claim verification status remains unchanged; claim proposals require human review.

**Example:**
```bash
aieng import-mesh-evidence build/bracket_001.aieng \
  --mesh-file external/mesh/job_001.msh \
  --format gmsh_msh \
  --producer-tool gmsh \
  --claim-support claim_mesh_evidence_001
```

---

## `aieng record-trace`

**Purpose:** Record an external tool execution step in `provenance/tool_trace.json`.

**Nature:** Provenance/audit record only. .aieng does not execute any external tool. This command records what a caller reports about an externally executed step.

This complements `record-evidence`. It closes the audit-trail gap left after Phase 14B external tool handoff contracts.

**Required inputs:**
- `<package.aieng>` - path to an existing `.aieng` package
- `--tool-id <id>` - external tool identifier (e.g. `freecad`, `gmsh`, `calculix`)
- `--tool-role agent_runtime|cad_runtime|cae_runtime|cae_preprocessor|solver|postprocessor|manufacturing_checker`
- `--step-name <name>` - name of the executed step (e.g. `modify_hole_diameter`)
- `--exit-status success|failure|skipped` - step exit status as reported by the external tool

**Optional flags:**
- `--tool-version <version>` - optional tool version string
- `--input <path>` - input path used by the step; repeat for multiple
- `--output <path>` - output path produced by the step; repeat for multiple
- `--artifact <evidence_id>` - evidence ID recorded for this step; repeat for multiple
- `--claim <claim_id>` - claim ID advanced by this step; repeat for multiple
- `--notes "..."` - optional note; repeat for multiple lines

**Behavior notes:**
- Creates `provenance/tool_trace.json` on first call, appends on subsequent calls.
- Entry IDs are deterministic: `trace_0001`, `trace_0002`, ...
- If `task/task_spec.yaml` exists, `source_task_id` is set from it.
- If `task/external_tool_requirements.json` exists, `source_handoff_id` is set from it.
- Does not modify `results/evidence_index.json`.
- Does not infer claim status.
- Does not execute external tools. Records only what the caller provides.

**Tool trace is audit/provenance, not engineering validation by itself.** Evidence and claim status remain separate resources.

**Example:**
```bash
aieng record-trace build/bracket_001.aieng \
  --tool-id freecad \
  --tool-role cad_runtime \
  --step-name modify_hole_diameter \
  --exit-status success \
  --tool-version 0.21.2 \
  --input geometry/source.step \
  --input graph/feature_graph.json:feat_hole_001 \
  --output geometry/modified_patch_001.step \
  --artifact ev_geometry_modification_001 \
  --claim claim_geometry_modification_001 \
  --notes "External CAD tool reported geometry modification."
```

---

## Phase 18A reference commands

The following commands are implemented and available.

See [`reference_notation.md`](reference_notation.md) for the full design and [`roadmap.md`](roadmap.md) Phase 18A for status.

### `aieng ref-inspect <package.aieng> '<ref>' --json`

**Purpose:** Resolve one `@aieng[<resource-path>#<id>]` reference and print the underlying record.

**Nature:** Read-only. Does not modify any resource. Does not invoke CAD/CAE tools. Does not advance any claim.

**Required inputs:**
- `<package.aieng>` — path to the package
- `'<ref>'` — the `@aieng[...]` reference string (quote in shells)

**Optional flags:**
- `--json` — print structured JSON output (the default for programmatic use)

**Output:** the original reference string, resolved resource path, record type, the full record contents, and related canonical references found in the record payload.

### `aieng ref-list <package.aieng> --type <kind>`

**Purpose:** Enumerate references of a given kind inside the package.

**Nature:** Read-only.

**Required inputs:**
- `<package.aieng>` — path to the package
- `--type <kind>` — one of: `feature`, `topology`, `interface`, `claim`, `evidence`, `trace`, `patch`, `constraint`, `protected_region`, `cae_mapping`, `completeness_item`, `task_spec_item`, `all`

**Optional flags:**
- `--json` — print JSON array output

**Output:** one canonical `@aieng[...]` reference per line (or a JSON array).

### `aieng ref-check <package.aieng>`

**Purpose:** Validate every cross-resource reference in the package.

**Nature:** Read-only. Also called internally by `aieng validate`.

**Required inputs:**
- `<package.aieng>` — path to the package

**Checks:**
- indexability of canonical references in supported structured resources
- cross-resource link validity for claim/evidence/trace IDs
- forbidden evidence target patterns in claim actual evidence fields

**Exit code:** non-zero on any failure.

### Status

Implemented in Phase 18A as read-only commands with no CAD/CAE execution side effects.
