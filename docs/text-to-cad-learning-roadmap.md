# Text-to-CAD Learning Roadmap for AIENG Workbench

> **Historical — kept for context, do not build from this.** A contributor learning path last updated 2026-05-27.
> Where this disagrees with [`AGENTS.md`](../AGENTS.md), AGENTS.md is right.
> See [`README.md`](README.md) for what is current.

> A structured learning path for contributors working on LLM-driven CAD generation, agentic CAD/CAE workflows, and the AIENG workbench ecosystem.
>
> **Last updated:** 2026-05-27
> **Prerequisites:** Python 3.11+, basic linear algebra, familiarity with Git

---

## Table of Contents

1. [Foundations: CAD Geometry for AI Engineers](#phase-1-foundations-cad-geometry-for-ai-engineers)
2. [Python CAD Scripting with build123d](#phase-2-python-cad-scripting-with-build123d)
3. [Text-to-CAD: From Research to Practice](#phase-3-text-to-cad-from-research-to-practice)
4. [Reference Projects: Skill-Based Agent Workflows](#phase-4-reference-projects-skill-based-agent-workflows)
5. [The AIENG Package & Evidence Layer](#phase-5-the-aieng-package--evidence-layer)
6. [FEA/CAE Pipeline Fundamentals](#phase-6-feacae-pipeline-fundamentals)
7. [AI-Driven Preprocessing & B-Rep Graphs](#phase-7-ai-driven-preprocessing--b-rep-graphs)
8. [Agent Architecture: MCP, Runtime & Approval Gates](#phase-8-agent-architecture-mcp-runtime--approval-gates)
9. [Closed-Loop CAD/CAE Copilot](#phase-9-closed-loop-cadcae-copilot)
10. [Capstone Project: Build an End-to-End Workflow](#phase-10-capstone-project)

---

## Phase 1: Foundations — CAD Geometry for AI Engineers

### Why this matters
Text-to-CAD systems fail most often at the geometry representation layer, not the LLM layer. Understanding how solids are represented lets you write better prompts, debug generated scripts, and design more robust agent workflows.

### Topics

| Topic | What to learn | Resources |
|-------|--------------|-----------|
| **B-Rep (Boundary Representation)** | Faces, edges, vertices, surface parametrization, topological vs. geometric data | OpenCASCADE docs; `aieng-ui/backend/app/brep_graph.py` |
| **CSG (Constructive Solid Geometry)** | Boolean operations (union, subtract, intersect), primitive hierarchies | build123d tutorials |
| **Mesh vs. B-Rep** | When to use triangle meshes (STL/GLB) vs. exact surfaces (STEP/B-Rep) | [CGAL docs](https://doc.cgal.org/) |
| **STEP (ISO 10303)** | AP214/AP242, how semantic data travels between CAD systems | `aieng/docs/package-semantics.md` |
| **File formats** | `.step`, `.stl`, `.glb`, `.3mf`, `.inp` — what each carries and loses | Project benchmarks in `aieng/benchmarks/` |

### Hands-on exercise
1. Import a STEP file into the AIENG workbench.
2. Inspect `geometry/topology_map.json` inside the resulting `.aieng` package.
3. Identify 5 faces by their topological properties (planar vs. cylindrical, adjacency count).

### Checkpoints
- [ ] Can explain the difference between topological and geometric data in a B-Rep.
- [ ] Can read a STEP file header and identify the AP schema.
- [ ] Can inspect `topology_map.json` and map face IDs to geometric types.

---

## Phase 2: Python CAD Scripting with build123d

### Why this matters
AIENG's primary geometry engine is **build123d** (OpenCASCADE Python wrapper). The `cad.execute_build123d` MCP tool is the main path from text to geometry in our system. Fluency in build123d is non-negotiable.

### Topics

| Topic | Key constructs | Project reference |
|-------|---------------|-------------------|
| **Primitives** | `Box`, `Cylinder`, `Cone`, `Sphere`, `Torus` | `AGENTS.md` build123d section |
| **Operations** | `extrude`, `revolve`, `loft`, `sweep` | build123d docs + AGENTS.md "Curve patterns" |
| **Modifications** | `fillet`, `chamfer`, `shell`, `mirror` | Benchmark models in `aieng/benchmarks/` |
| **Boolean modes** | `Mode.ADD`, `Mode.SUBTRACT`, `Mode.INTERSECT` | `AGENTS.md` code examples |
| **Patterns** | `PolarLocations`, `GridLocations`, `Locations` | Benchmark 02 (flange) |
| **Labels & Compounds** | `.label`, `Compound(children=[...])` | Critical for named parts in topology |
| **Per-part color** | `.color = Color(r, g, b)` (RGB in 0..1) | Renders in agent thumbnail + GLB viewer |
| **Industrial Design Mode** | Loft / sweep / revolve + aggressive fillet for visible exterior forms | `AGENTS.md` "Industrial Design Mode" |
| **Engineering Mode** | Canonical labels — `base_plate`, `mounting_hole_pattern`, `rib`, `boss`, `flange`, `interface_face` | `AGENTS.md` "Engineering Mode" |
| **Export contract** | Bind final model to `result`; omit export calls | `AGENTS.md` "Code contract" |

### Visual feedback the agent gets back
`cad.execute_build123d` returns a **2×2 contact-sheet PNG** (front / side / top / iso) so the agent can verify silhouette and alignment from four angles at once — alignment errors hide in iso but show clearly in front/side. When a reference image is attached via `cad.set_reference_image`, the layout expands to 2×3 with the reference filling the right column. Per-part `.color` values render in every tile so parts can be distinguished at a glance.

### Hands-on exercises
1. Reproduce benchmark 02 (circular flange) from the `text-to-cad-main` reference project using build123d in the AIENG workbench.
2. Create a parametric bracket with labeled parts (`body`, `mounting_hole_1`, `gusset`). Verify the labels appear in `topology_map.json`. Assign each part a distinct `.color` and confirm the contact sheet shows them in colour.
3. Practice incremental modeling: create a base box in step 1 (`mode=replace`), then append a cylindrical boss in step 2 (`mode=append`).
4. Attach a real reference image to a project with `cad.set_reference_image` (URL or local file) and rebuild a model. Compare the contact-sheet 2×3 layout against the reference for proportion calibration.
5. Build a deliberately-flawed engineering part (rib < 3mm, hole at 9.5mm, floating bolt) and run `cad.critique` — verify the audit returns the three findings with appropriate severities.

### Checkpoints
- [ ] Can write a 20-line build123d script that produces a valid STEP file.
- [ ] Understands the `result` binding contract and why exports must be omitted.
- [ ] Can use `mode=append` with `previous_result` for incremental modeling.
- [ ] Knows why `.label` matters for downstream B-Rep graph and CAE workflows.
- [ ] Sets `.color` on parts and recognizes them in the contact-sheet thumbnail.
- [ ] Knows when to switch into Industrial Design Mode (visible exterior forms → loft/sweep) vs Engineering Mode (mechanical parts → canonical labels + cad.critique).
- [ ] Uses `cad.set_reference_image` before iterating on named real-world targets.

### Comparison with CadQuery
Our `refer/CAD-Agent/` uses CadQuery. Understand the tradeoffs:

| Aspect | build123d (our stack) | CadQuery (reference) |
|--------|----------------------|----------------------|
| API style | Fluent/chaining with `BuildPart` context | Method chaining on `Workplane` |
| Selector syntax | `bp.edges().filter_by(Axis.Z)` | `cq.Workplane().edges("\|Z")` |
| Export | Free functions (`export_step(result, path)`) | `.val().exportStep(path)` |
| Community | Smaller, newer | Larger, more mature |

---

## Phase 3: Text-to-CAD — From Research to Practice

### Why this matters
Text-to-CAD is not a single technique. It spans prompt engineering, code generation, iterative repair, validation, and visual review. Understanding the design space prevents over-investing in any one approach.

### Approaches landscape

```
Text / Image / Sketch
         │
         ├──► Direct mesh generation (point clouds, NeRF, diffusion) — NOT our path
         │
         ├──► Code generation (LLM → Python CAD script) — OUR PRIMARY PATH
         │     ├─ One-shot generation
         │     ├─ Iterative refinement (repair loop)
         │     └─ Skill-constrained generation (agent skills)
         │
         └──► Parametric editing (LLM → parameter changes on existing model)
               └─ Our `cad.edit_parameter` tool (Phase 18+)
```

### Topics

| Topic | Key papers / tools | Relevance to AIENG |
|-------|-------------------|-------------------|
| **Code-generation paradigm** | Text-to-CAD (Zoo.dev), CodeCAD agents | Our `cad.execute_build123d` follows this |
| **Iterative repair** | Self-debugging code agents | `references/repair-loop.md` in text-to-cad-main |
| **Visual review** | Multimodal LLM inspection | Our `thumbnail` feedback after `execute_build123d` |
| **Skill-based workflows** | Skills.sh, Cursor skills | `aieng-agent-skills/` and `CAD-Agent-Skills/` |
| **Constraint grounding** | Natural-language-to-constraints | Our `task/design_targets.yaml` system |

### Hands-on exercises
1. Use Claude Code with the `aieng-workbench` MCP server to generate a part from a natural language description. Inspect the generated build123d code.
2. Intentionally introduce a bug (e.g., wrong radius causing self-intersection). Observe how the system responds and what feedback channels exist.
3. Compare one-shot vs. two-shot (append) generation for the same complex part. Which produces cleaner code? More stable face IDs?

### Checkpoints
- [ ] Can articulate why code-generation beats direct mesh generation for engineering workflows.
- [ ] Has traced a complete text → build123d script → STEP → `.aieng` → viewer pipeline.
- [ ] Understands why iterative (append) modeling is preferred for complex parts.

---

## Phase 4: Reference Projects — Skill-Based Agent Workflows

### Why this matters
Our `refer/` directory contains two canonical skill-based CAD agent implementations. Studying them informs how we design `aieng-agent-skills/` and MCP tool contracts.

### Project A: `refer/text-to-cad-main` (earthtojake)

**What it is:** A collection of portable agent skills for build123d-based CAD generation.

**Key concepts to learn:**
- `@cad[...]` reference syntax for geometry-aware follow-up edits
- Skill contract: `SKILL.md` as the agent's operating manual
- Render skill: CAD Explorer viewer integration
- `step.parts` catalog integration for off-the-shelf components
- URDF/SRDF/SDF robot description generation

**Study path:**
1. Read `skills/cad/SKILL.md` — the main agent contract.
2. Read `skills/cad/references/build123d-modeling.md` — patterns index.
3. Read `skills/cad/references/repair-loop.md` — iteration and repair rules.
4. Run one benchmark from `benchmarks/` locally to see the full pipeline.

### Project B: `refer/CAD-Agent/` (fhwangyinan)

**What it is:** An evidence-driven CadQuery compiler workflow with mandatory review gates.

**Key concepts to learn:**
- **Skill Constraint Handoff**: Every plan declares `fidelity_level`, `modeling_phases`, `exit_visual_bar`
- **Single-focus iteration discipline**: One script per iteration, with declared `primary_scope` and `out_of_scope`
- **Review gates**: `phase_gate.json` → script → `gate_results.json` → `review_report.md`
- **Visual review**: Agent inspects `front.png`, `side.png`, `top.png`, `iso.png` before declaring `export_ready`
- **Reference acquisition**: `reference_sources.json`, `reference_measurements.json` for reference-driven models

**Study path:**
1. Read `CAD-Agent-Skills/SKILL.md` — the main contract.
2. Read `CAD-Agent-Skills/pipeline-contract.md` — artifact chain specification.
3. Read `CAD-Agent-Skills/visual-review.md` — inspection requirements.
4. Study the `iteration_plan.json` + `phase_gate.json` pattern.

### Comparison table

| Aspect | text-to-cad-main | CAD-Agent |
|--------|-----------------|-----------|
| CAD library | build123d | CadQuery |
| Iteration style | Skill-driven, tool-augmented | Phase-gated, evidence-driven |
| Visual review | Render skill / CAD Explorer | Agent self-inspection of PNGs |
| Reference handling | `step.parts` catalog | `reference_sources.json` + checklists |
| Export formats | STEP, STL, 3MF, DXF, GLB, URDF | STEP, STL |
| Best for | Rapid prototyping, robot/mechanism design | Precision engineering, reference-critical parts |

### Checkpoints
- [ ] Has read both `SKILL.md` files and can articulate their design philosophies.
- [ ] Can explain the difference between `@cad[...]` references and B-Rep `@face:` pointers.
- [ ] Understands why "agent self-review" is a hard gate, not a nice-to-have.

---

## Phase 5: The AIENG Package & Evidence Layer

### Why this matters
`.aieng` is the central innovation of this project: an auditable engineering context package that makes CAD/CAE state legible to AI agents. Understanding its semantics is essential for anyone building on top of the platform.

### Core concepts

| Concept | Location in package | Meaning |
|---------|--------------------|---------|
| `manifest.json` | Root | Package identity, schema version, timestamps |
| `geometry/topology_map.json` | `geometry/` | Face/edge/vertex index with geometric types |
| `graph/feature_graph.json` | `graph/` | Recognized features (holes, fillets, bosses) |
| `graph/brep_graph.json` | `graph/` | Symbolic pointer index (`@face:`, `@edge:`, `@group:`) |
| `simulation/setup.yaml` | `cae/` | FEA setup: materials, BCs, loads, mesh params |
| `simulation/load_cases/*.json` | `cae/` | Per-load-case definitions |
| `results/computed_metrics.json` | `results/` | Imported or extracted scalar results |
| `results/result_summary.json` | `results/` | Honest LLM-facing summary with limitations |
| `task/design_targets.yaml` | `task/` | Engineering requirements as testable targets |
| `audit_log.jsonl` | Root | Append-only action history |

### Key design invariants
1. **Evidence is not a claim.** Solver results are evidence; claims require explicit update.
2. **Freshness is explicit.** Stale artifacts are flagged after geometry edits.
3. **Missing information is recorded.** `unsupported`, `uncertain`, and `missing` are first-class states.
4. **The package is the source of truth.** Loose files are secondary.

### Hands-on exercises
1. Create a `.aieng` package from a STEP file using the CLI.
2. Inspect each JSON artifact. Trace how a face in `topology_map.json` becomes `@face:f_top_001` in `brep_graph.json`.
3. Modify a setup artifact and observe the stale-artifact warnings in `agent_context`.

### Checkpoints
- [ ] Can explain the difference between `geometry/topology_map.json` and `graph/brep_graph.json`.
- [ ] Can read a `result_summary.json` and identify the honesty boundaries (`converged: null`, `limitations`).
- [ ] Understands why `design_targets.yaml` is a requirement resource, not a solver result.

---

## Phase 6: FEA/CAE Pipeline Fundamentals

### Why this matters
AIENG is not just a CAD viewer. The vertical CAE pipeline (Gmsh → CalculiX → FRD parsing → result summary) is a core differentiator. Understanding each step lets you debug failures and extend the pipeline.

### Pipeline overview

```
STEP geometry
    │
    ▼
Gmsh meshing ──► mesh.inp (CalculiX format)
    │
    ▼
AI preprocessing (materials, BCs, loads)
    │
    ▼
CalculiX input deck (.inp)
    │
    ▼
cae.run_solver (approval-gated subprocess)
    │
    ▼
result.frd (CalculiX output)
    │
    ▼
FRD scalar extraction ──► computed_metrics.json
    │
    ▼
Result summary + stress heatmap GLB
```

### Topics

| Topic | What to learn | Project file |
|-------|--------------|--------------|
| **Gmsh scripting** | Mesh size, element types, physical groups | `simulation_runner.py::_mesh_with_gmsh()` |
| **CalculiX input deck** | `*NODE`, `*ELEMENT`, `*MATERIAL`, `*BOUNDARY`, `*CLOAD`, `*STEP` | `tests/fixtures/minimal_cantilever.inp` |
| **FRD format** | Fixed-width text, DISP and S blocks, node-based data | `aieng/simulation/frd_result_extractor.py` |
| **Von Mises stress** | Tensor invariants, yield criteria | `post_processing.py::compute_fos()` |
| **NSET mapping** | Face IDs → node sets via bbox + normal heuristics | `simulation_runner.py::_build_nsets()` |

### Hands-on exercises
1. Run the vertical CAE demo: `pytest tests/test_api.py::test_vertical_cae_workflow_end_to_end -v`.
2. Inspect the `.inp` deck in `tests/fixtures/minimal_cantilever.inp`. Identify nodes, elements, material, BCs, and loads.
3. If you have `ccx` installed, run the real solver demo: `pytest tests/test_api.py::test_run_solver_real_ccx_skipped_if_unavailable -v`.
4. Inspect the resulting `result.frd` file. Find the DISP block and compute displacement magnitude for one node by hand.

### Checkpoints
- [ ] Can read and understand a CalculiX `.inp` deck.
- [ ] Can parse a FRD file and extract DISP and S blocks.
- [ ] Can explain how face IDs from `cae_mapping.json` become NSETs in the solver deck.
- [ ] Understands why `converged: null` is the correct honesty boundary for CalculiX.

---

## Phase 7: AI-Driven Preprocessing & B-Rep Graphs

### Why this matters
This is where LLMs intersect with engineering semantics. The B-Rep graph engine (Phase 40) and AI preprocessing v2 (Phase 41) enable agents to set up FEA using explicit face pointers instead of vague natural language.

### Topics

| Topic | What it is | How it works |
|-------|-----------|--------------|
| **B-Rep Graph Engine** | Symbolic geometry index | `brep_graph.py` builds `@face:`, `@edge:`, `@group:` pointers from topology |
| **Face role inference** | Semantic classification | `support_candidate`, `load_candidate`, `mounting_candidate`, `stiffener` |
| **Pointer syntax** | `@kind:id` tokens | Used in tool args and rendered as clickable chips in the UI |
| **AI Preprocessing v2** | NL → FEA setup with B-Rep context | Claude receives `brep_digest` and returns `target_pointers` |
| **Pointer resolution** | `@face:f_top_001` → actual face ID | `entity_index.json` maps pointer tokens to topology entries |

### Hands-on exercises
1. Build a B-Rep graph for a project: `POST /api/projects/{id}/brep-graph/build`.
2. Inspect `graph/brep_graph.json` and `graph/entity_index.json`.
3. Manually construct a `cae_mapping.json` that uses `@face:` pointers instead of raw face indices.
4. Trace how `ai_preprocessing.py` injects B-Rep context into the Claude prompt and resolves pointers back to face IDs.

### Checkpoints
- [ ] Can explain why `@face:f_top_001` is better than "the top face" for agent workflows.
- [ ] Can manually classify faces in a simple model into `support_candidate` / `load_candidate` roles.
- [ ] Understands the relationship between `brep_graph.json`, `entity_index.json`, and `cae_mapping.json`.

---

## Phase 8: Agent Architecture — MCP, Runtime & Approval Gates

### Why this matters
The AIENG workbench is designed for agent use. Understanding the runtime architecture, MCP protocol surface, and approval gate mechanics is essential for building reliable agent workflows.

### Architecture layers

```
Agent (Claude Code / Codex / Cursor)
    │
    ├──► MCP protocol ──► aieng_freecad_mcp ──► HTTP ──► aieng-ui runtime
    │
    └──► Direct HTTP ──► aieng-ui REST API

Runtime (aieng-ui/backend/app/runtime.py)
    │
    ├──► Intent classification ──► Plan builder ──► Step executor
    │
    └──► Approval gate (requires_approval=True tools pause here)
```

### Topics

| Topic | What to learn | Project reference |
|-------|--------------|-------------------|
| **MCP protocol** | Model Context Protocol, tool registration, capability negotiation | `aieng_freecad_mcp/` |
| **Runtime model** | `RunRecord`, `ToolCall`, `ToolResult`, `RuntimeEvent` | `runtime.py` |
| **Intent map** | Keyword-based intent classification | `_INTENT_MAP` in `runtime.py` |
| **Approval gate** | `requires_approval=True` → `awaiting_approval` → `approve`/`reject` | `main.py` approve/reject endpoints |
| **Artifact write-back** | Atomic ZIP rewrite into `.aieng` | `write_artifact_to_package()` |
| **Event timeline** | `run_started → plan_created → tool_started → approval_required → ...` | Audit log format |

### Hands-on exercises
1. List all runtime tools: `GET /api/runtime/tools`.
2. Start a run, observe it pause at `awaiting_approval`, then approve it via the REST API.
3. Trace the full event timeline for a `cae.run_solver` execution.
4. Implement a new runtime tool handler in `main.py` and register it.

### Checkpoints
- [ ] Can explain the difference between MCP bridge tools and direct REST API calls.
- [ ] Has traced a full approval-gated execution through the event timeline.
- [ ] Understands why artifact write-back uses atomic ZIP rewrite (temp file + `shutil.move`).

---

## Phase 9: Closed-Loop CAD/CAE Copilot

### Why this matters
This is the frontier of the project: a copilot that can recommend CAD modifications based on simulation results, verify them, execute them, and re-simulate — all within trust boundaries.

### The closed loop

```
Simulation results
    │
    ▼
Recommend CAD modifications (Phase 36)
    │     ├─ Read design_targets.yaml
    │     ├─ Read computed_metrics.json
    │     └─ Read stress_by_feature.json
    │
    ▼
Verify proposals (Phase 37)
    │     ├─ Schema checks
    │     ├─ Manufacturability checks (thickness, diameter floors)
    │     └─ Regression prediction (SF_after heuristic)
    │
    ▼
Execute via cad.edit_parameter (Phase 39b)
    │     └─ Approval-gated; fail-verdict proposals blocked
    │
    ▼
Re-simulate (Phase 38 skill)
    │
    ▼
Compare against design targets
```

### Topics

| Topic | What it is | File |
|-------|-----------|------|
| **Recommendation engine** | Ranked CAD modification proposals | `aieng/cae_recommendation.py` |
| **Modification vocabulary** | `thin`, `thicken`, `add_fillet`, `resize_hole`, `remove`, `reduce_count` | `cae_recommendation.py` |
| **Verification gate** | Pre-execution safety checks | `aieng/cae_verification.py` |
| **Closed-loop skill** | Agent-facing iteration contract | `aieng-agent-skills/skills/aieng-closed-loop-copilot/SKILL.md` |
| **Explainability panel** | UI display of proposals + verdicts | `RecommendationsPanel.tsx` |

### Hands-on exercises
1. Run the recommendation CLI on a benchmark package: `aieng recommend-cad-modifications <package>`.
2. Run verification on those proposals: `aieng verify-cad-modifications <package>`.
3. Trace the full loop: recommend → verify → apply one proposal → re-simulate → compare.
4. Read the closed-loop copilot skill and identify the five explicit stop conditions.

### Checkpoints
- [ ] Can explain why proposals are hypotheses, not certified engineering decisions.
- [ ] Can articulate the difference between Phase 36 (recommendation), Phase 37 (verification), and Phase 39b (execution).
- [ ] Understands why `verification_does_not_replace_resimulation = true`.

---

## Phase 10: Capstone Project

### Choose one

**Option A: Custom Skill Authoring**
Write a new agent skill for a specific domain (e.g., sheet metal, gears, or 3D-printable enclosures). It should include:
- A `SKILL.md` with operating contract
- Domain-specific build123d patterns
- A validation checklist
- At least one benchmark scenario

**Option B: CAE Pipeline Extension**
Extend the vertical CAE pipeline in one direction:
- Add support for thermal analysis (new load case type, new FRD field parsing)
- Implement modal analysis support (frequency extraction from FRD)
- Add a new post-processing metric (e.g., strain energy)

**Option C: B-Rep Graph Enhancement**
Improve the B-Rep graph engine:
- Add a new face role inference rule (e.g., `cooling_fin` for engine parts)
- Implement edge adjacency analysis
- Add geometric similarity grouping (group faces with similar area + normal)

**Option D: Closed-Loop Integration**
Build a complete closed-loop demo:
1. Upload a bracket STEP file.
2. Run AI preprocessing → mesh → solve.
3. Get recommendations for mass reduction.
4. Verify and apply one proposal.
5. Re-mesh, re-solve, compare.
6. Document the delta in a markdown report.

---

## Quick Reference: Key Files to Study

### CAD / Geometry
| File | Why |
|------|-----|
| `AGENTS.md` | Canonical agent guide for build123d workflows |
| `aieng-ui/backend/app/brep_graph.py` | B-Rep graph engine (Phase 40) |
| `aieng-ui/backend/app/simulation_runner.py` | Gmsh + CalculiX orchestration (Phase 42) |

### AI / Agent
| File | Why |
|------|-----|
| `aieng-ui/backend/app/ai_preprocessing.py` | NL-to-FEA with B-Rep context (Phase 41) |
| `aieng-ui/backend/app/engineering_action_plan.py` | Intent classification (Phase 46) |
| `aieng-ui/backend/app/contextual_chat.py` | Evidence-grounded chat (Phase 45) |

### Evidence / Package
| File | Why |
|------|-----|
| `aieng/src/aieng/cae_recommendation.py` | CAD modification proposals (Phase 36) |
| `aieng/src/aieng/cae_verification.py` | Pre-execution verification (Phase 37) |
| `aieng/src/aieng/cae_result_summary.py` | Honest result summaries |

### Runtime / Orchestration
| File | Why |
|------|-----|
| `aieng-ui/backend/app/runtime.py` | Tool registry, plan builder, executor |
| `aieng-ui/backend/app/main.py` | Tool handlers, REST endpoints |
| `aieng_freecad_mcp/src/freecad_mcp/tools_runtime/__init__.py` | MCP bridge tools |

---

## Recommended Reading Order

1. Start with `AGENTS.md` and build a simple part using `cad.execute_build123d`.
2. Read `docs/package_contract.md` to understand `.aieng` semantics.
3. Study `refer/text-to-cad-main/skills/cad/SKILL.md` for skill design patterns.
4. Study `refer/CAD-Agent/CAD-Agent-Skills/SKILL.md` for evidence-driven iteration.
5. Run the vertical CAE demo in `docs/demo-vertical-cae-workflow.md`.
6. Read the Phase 36–39 closed-loop documentation.
7. Pick a capstone project and build.

---

## Glossary

| Term | Definition |
|------|-----------|
| **B-Rep** | Boundary Representation — exact solid geometry via faces, edges, vertices |
| **build123d** | Python CAD library wrapping OpenCASCADE; our primary geometry engine |
| **CalculiX** | Open-source FEA solver (ccx) |
| **CSG** | Constructive Solid Geometry — solids built from primitives + booleans |
| **FRD** | CalculiX result file format (text, fixed-width) |
| **Gmsh** | Open-source mesh generator |
| **MCP** | Model Context Protocol — tool-calling protocol for AI agents |
| **NSET** | Node set — group of mesh nodes for BC/load application |
| **STEP** | ISO 10303 standard CAD exchange format |
| **.aieng** | AI-readable engineering context package format (ZIP archive) |
