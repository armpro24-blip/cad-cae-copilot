# FreeCAD Action Agent MVP (v0.38)

> **Historical — kept for context, do not build from this.** It describes the FreeCAD action agent driven from the Pilot Console; both the console and the whole backend intent-routing layer were removed in the MCP-first cutover (#17, #8), and FreeCAD is not the runtime — build123d/OCP is.
> Where this disagrees with [`AGENTS.md`](../AGENTS.md), AGENTS.md is right.
> See [`README.md`](README.md) for what is current.

## What this is

The FreeCAD Action Agent is an **action-first** capability that lets a user type a CAD request in the Pilot Console and have the system propose, approve, and execute a real FreeCAD operation through a controlled runner.

This is **not** a metadata-only wrapper. It spawns FreeCAD, creates geometry, exports STEP/FCStd, and ingests the results into the `.aieng` package so that downstream observation layers can constrain and guide the next step.

## What this is not

- **Not guaranteed correct CAD.** The scripts are bounded templates; quality is heuristic.
- **Not solver automation.** No mesh generation, no CalculiX, no FEA.
- **Not an arbitrary Python agent.** Only bounded Part::Box / Part::Cylinder / Part::Cut scripts are generated.
- **Not a replacement for engineering judgment.** AIENG observes and constrains after action; it does not certify the geometry.

## Core flow

```
User types: "Create a cantilever beam 200 mm long"
→ Intent Planner detects CAD-creation intent
→ Planner picks template "create_cantilever_beam" + extracts dimensions
→ Planner proposes action: freecad.action.execute
→ UI shows proposed FreeCAD Python script
→ User approves
→ Runtime executes script via FreeCADCmd subprocess
→ Script exports STEP + FCStd
→ Action agent copies artifacts into .aieng package
→ Action agent writes geometry/freecad_snapshot.json
→ Agent Observation + CAD Observation run automatically
→ UI shows: what was created, what is missing, what should happen next
```

## Safety boundaries

| Boundary | Implementation |
|---|---|
| Approval gate | `freecad.action.execute` has `requires_approval=True` in the runtime registry. |
| Code visibility | The proposed script is rendered in the Pilot Console before approval. |
| Bounded execution | Scripts are generated from a small template library (box, beam, bracket, quadcopter frame, inspect). No arbitrary LLM code generation. |
| Subprocess sandbox | Execution runs inside `FreeCADCmd` via `freecad_bridge.run_macro()`. No shell execution. |
| Honest unavailable | If FreeCADCmd is missing, the tool returns `status: unavailable` with a clear message. |
| No solver/mesh | Templates use only `Part::Box`, `Part::Cylinder`, `Part::Cut`. No FEM, Gmsh, or CalculiX calls. |
| Artifact allow-list | Output is restricted to `geometry/output_*.step`, `geometry/output_*.fcstd`, and `geometry/freecad_snapshot.json`. |
| Audit trail | Every execution produces a runtime run record with stdout, stderr, and artifact list. |
| Claim boundary | All outputs carry `claim_advancement: "none"` and the standard FreeCAD wrapper claim boundary. |

## Template library

| Template ID | Parameters | Operations |
|---|---|---|
| `create_box` | `length`, `width`, `height`, `name` | Part::Box |
| `create_cantilever_beam` | `length`, `width`, `height` | Part::Box (semantic label) |
| `create_bracket` | `width`, `height`, `thickness`, `hole_diameter` | Part::Box + Part::Cylinder + Part::Cut |
| `create_quadcopter_frame` | `arm_length`, `arm_width`, `tube_diameter`, `motor_hole_spacing` | Part::Box (hub + arms) + Part::Cylinder cuts |
| `inspect_document` | — | Read active document objects and bounding boxes |

Parameter extraction is heuristic (regex for `N mm` patterns). Missing dimensions fall back to sensible defaults.

## Files added / changed

### New
- `backend/app/freecad_action_agent.py` — template library, execution wrapper, artifact ingest.
- `backend/tests/test_freecad_action_agent.py` — planner branch, approval, unavailable, mock artifact tests.
- `docs/freecad-action-agent-mvp.md` — this document.

### Changed
- `backend/app/intent_planner.py` — `_freecad_action_intents`, `_infer_cad_action_template`, `_freecad_action_actions`, additive mixing into plan.
- `backend/app/runtime_tools.py` — registration of `freecad.action.execute`.
- `frontend/src/components/panels/IntentPlannerCard.tsx` — render proposed script for `freecad.action.execute`.

## Future work (not in v0.38)

- Semantic labeling of faces/edges for load/BC application.
- CAE readiness check after geometry creation (mesh feasibility, solver deck preflight).
- More operations (fillet, chamfer, extrude, revolve).
- LLM-based script generation with bounded AST validation.
- Live FreeCAD XML-RPC connector for faster iteration.
- Parameter editing after creation (dimension changes without rebuild).
