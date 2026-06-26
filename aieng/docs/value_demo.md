# Value Demo — CAD → CAE → result explanation (the canonical chain)

The one reproducible case that shows, end to end, what the workbench is for:

> natural language → editable CAD → `@face:id` pick → CAE setup
> (material / fixed support / load) → `cae.prepare_solver_run` →
> approved `cae.run_solver` → **real** solved fields in the viewer →
> plain-language result explanation (max von Mises / displacement +
> credibility tier + honesty boundaries).

This is the user-facing counterpart to the real-ccx integration test
(`aieng-ui/backend/tests/test_cae_solve_integration.py`, #360). It is issue #368.

## Fixture

A single connected solid — an aluminium **cantilever beam**, the simplest part
that produces a textbook bending result:

| Property | Value |
|----------|-------|
| Geometry | box **100 × 20 × 10 mm** (single solid, `beam`) |
| Material | **Al-6061** — E = 69 000 MPa, ν = 0.33, ρ = 2.7×10⁻⁹ t/mm³ |
| Fixed support | the −X end face (`@face:face_001`, role `support_candidate`) |
| Load | **50 N** in −Z on the +X end face (`@face:face_002`, role `load_candidate`), distributed over the face's nodes (total-force semantics) |
| Analysis | linear `static`, CalculiX |
| Mesh | gmsh C3D4, ~593 nodes / ~1883 elements, target 4 mm |

The reference project in this workbench is **`FEA Validation Cantilever`**
(`6bd4cbe32c00`), already built and set up.

## Reproducible sequence

Each step is one MCP tool call (or the natural-language prompt that routes to it).
Build/setup mirror the existing fixture; the solve is the part that produces
**real** evidence.

```
# 1. Onboard
aieng.agent_readme
aieng.list_projects
aieng.agent_context { project_id }            # geometry + @face pointers + readiness

# 2. CAD — natural language → editable solid (approve the modeling plan once)
cad.execute_build123d { project_id, code }    # a 100x20x10 beam, .label="beam"
#   "Build a 100x20x10 mm aluminium cantilever beam."

# 3. CAE setup — material / fixed support / load on picked faces
cae.apply_setup_patch { project_id, patch }   # Al-6061; fix @face:face_001;
#   "Fix the -X face and apply a 50 N downward load on the +X face."
#                                               # 50 N -Z load on @face:face_002

# 4. Mesh
cae.generate_mesh { project_id }              # writes simulation/mesh.inp

# 5. Preflight (no execution) — confirms ccx_available + ready_to_run
cae.prepare_solver_run { project_id }

# 6. Generate the solver deck
cae.generate_solver_input { project_id, run_id: "run_001", overwrite: true }

# 7. Run the solver  [APPROVAL REQUIRED — real CalculiX]
cae.run_solver { project_id, run_id: "run_001",
                 input_deck_path: "simulation/runs/run_001/solver_input.inp",
                 extract_results: true, refresh_summary: true }

# 8. Post-process (if not auto-run in step 7)
cae.extract_solver_results { project_id }
cae.extract_field_regions  { project_id, field: "stress" }
postprocess.refresh_cae_summary { project_id }
```

The viewer then shows the **real FRD-derived** von Mises / displacement fields
(field picker → "Von Mises (MPa)" / "Magnitude (mm)"), and the results hero
states the verdict + credibility tier.

## Verified result (real ccx)

Running the chain through real CalculiX (`conda run -n calculix-env ccx`) on the
fixture deck produces, for the 50 N tip load:

| Metric | Value |
|--------|-------|
| Max displacement | **0.0746 mm** |
| Max von Mises stress | **8.47 MPa** |
| Credibility tier | **executed_solver_result** (a solved, non-error ccx run) |

Sanity: this scales linearly with load — exactly 50/300 of the 300 N reference
run's 0.4478 mm — and is well below Al-6061 yield (~276 MPa), so the part is safe
in this load case with a large margin.

### Plain-language explanation (the agent's summary)

> The aluminium cantilever deflects **0.075 mm** at the loaded tip under the 50 N
> downward load, and the peak **von Mises stress is 8.5 MPa**, concentrated at
> the fixed −X root (the expected bending hot-spot). That is ~33× below the
> Al-6061 yield strength (276 MPa), so the part is comfortably safe for this load
> case. **Credibility: executed-solver result** — produced by an actual CalculiX
> static run on this mesh, not a surrogate or imported metric.

## Honesty boundaries (state these with the result)

- **Linear static** only — no plasticity, contact, large deflection, or dynamics.
- **Mesh-dependent** — these are single-mesh values; run `cae.mesh_convergence`
  to bound the discretization error (GCI) before trusting them quantitatively.
- **Linear tetrahedra (C3D4)** are stiff in bending, so displacement is a *lower*
  bound; the analytic Euler-Bernoulli tip deflection (point load, ~0.145 mm) is
  higher. Use C3D10 / a finer mesh for tighter displacement accuracy.
- Not production-certified; the credibility tier reports trust, not sign-off.

## Notes

- Use the `conda run -n calculix-env ccx` form for ccx on Windows — a bare
  `ccx.exe` path crashes on missing runtime DLLs (#356/#359).
- The deck assembler is solid-only and tolerant of raw-gmsh source decks
  (comma-attached `*ELSET` and CPS3 surface elements are handled — #417/#418),
  so imported meshes solve without manual cleanup.
- Deliverable still open: a 60–90 s screen capture (GIF) of this chain for the
  README hero slot (#345) — a human screen-recording step.
