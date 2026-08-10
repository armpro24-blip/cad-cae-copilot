# Backend Capability Matrix

**Snapshot date: 2026-08-10** (update this stamp when the matrix changes — an
undated "authoritative snapshot" is worse than none).

This document is the current capability snapshot for the reference backend/runtime
used in this workspace. It covers the implemented CAD/CAE/topology-optimization/
mesh-reconstruction/assembly pipeline spread across `aieng/src/aieng/converters/`
and `aieng-ui/backend/`.

Scope note: this is not a claim that the `aieng` core CLI alone performs every
operation below. Some items are shared converter/runtime capabilities surfaced by
the reference workbench. When in doubt, treat this file as the authoritative
"what exists today, with what limits" snapshot.

## Status Legend

| Status | Meaning |
| --- | --- |
| `stable` | Implemented, exercised by focused tests, and intended as a current supported path |
| `experimental` | Implemented but still bounded, reference-grade, or intentionally conservative |
| `diagnostic-only` | Produces analysis/diagnostics/evidence, not editable CAD or direct execution claims |
| `proxy-only` | Uses simplified proxy physics/modeling rather than full production behavior |
| `future` | Not implemented; no current capability claim |

## Geometry Generation And Shape IR

| Capability | Status | Primary artifacts | STEP output | CAD editability | Current boundary |
| --- | --- | --- | --- | --- | --- |
| Shape IR -> build123d B-Rep compile (`brep_build123d`) | `stable` | `geometry/shape_ir.json`, `geometry/source.py`, `geometry/generated.step`, `geometry/topology_map.json`, `graph/feature_graph.json`, `diagnostics/shape_ir_verification.json`, `registry/object_registry.json` | Yes | B-Rep | Default reference runtime path for pickable geometry, feature extraction, and downstream CAE/topopt linkage |
| Shape IR -> OCP NURBS B-Rep compile (`nurbs_brep`) | `experimental` | `geometry/shape_ir.json`, `geometry/generated.step`, `geometry/topology_map.json`, `diagnostics/shape_ir_verification.json` | Yes | B-Rep | Exact per-patch bspline faces are supported, but this is not a general "editable freeform CAD from anything" claim |
| Shape IR -> implicit SDF mesh (`implicit_sdf`) | `experimental` | `geometry/shape_ir.json`, `geometry/sdf_source.py`, mesh/preview artifacts, verification/object-registry outputs where available | No | mesh-only | Produces mesh-style runtime geometry; not analytic CAD |
| Shape IR -> manifold mesh (`manifold_mesh`) | `experimental` | `geometry/shape_ir.json`, `geometry/manifold_source.py`, mesh/preview artifacts, verification/object-registry outputs where available | No | mesh-only | Produces fused mesh geometry with region-level/topology evidence, not analytic CAD |
| Shape IR verification and node/object cross-reference | `stable` | `diagnostics/shape_ir_verification.json`, `registry/object_registry.json` | Not applicable | Not applicable | Verification is evidence about generated geometry linkage; it is not manufacturing or certification approval |

## CAE Result Contracts

| Capability | Status | Primary artifacts | STEP output | CAD editability | Current boundary |
| --- | --- | --- | --- | --- | --- |
| Solver-neutral CAE normalization | `stable` | `analysis/computed_metrics.json`, `analysis/field_regions.json` | Not applicable | Not applicable | Normalizes solver output into a neutral contract; does not prove solver correctness by itself |
| CAE result mapping back to geometry/source nodes | `stable` | `analysis/cae_result_map.json` plus topology/object-registry inputs | Not applicable | Not applicable | Unmapped or weakly mapped regions stay explicit; no silent geometric certainty is invented |
| CalculiX-backed reference runtime loop | `stable` | generated deck/result artifacts plus normalized `analysis/*` outputs in the workbench runtime | Not applicable | Not applicable | Real solver execution exists in the reference runtime, but post-processing remains evidence-oriented rather than certification-oriented |
| FE mesh generation (Gmsh), quadratic default | `stable` | `simulation/mesh.inp`, `simulation/mesh_metadata.json` | Not applicable | Not applicable | Defaults to second-order tets (C3D10; linear tets measured ~2× non-conservative in bending); every mesh carries a measured `accuracy` band (reliable/marginal/unreliable) — a heuristic elements-through-thickness check, not a convergence proof |
| Mesh accuracy verdict → claim-tier downgrade | `stable` | `accuracy` block in mesh metadata; `claim_tier` in the result summary | Not applicable | Not applicable | A completed solver run on an `unreliable` mesh is stamped `unreliable_mesh`, NOT `executed_solver_result` — enforced in `classify_credibility` and the result contract |
| GCI mesh-convergence study (`cae.mesh_convergence`) | `stable` | `analysis/mesh_convergence_report.json` | Not applicable | Not applicable | Richardson extrapolation + apparent order + per-metric verdict; the real answer to mesh dependence (the accuracy band is only a screen) |

## Sizing Optimization (real solver in the loop)

| Capability | Status | Primary artifacts | STEP output | CAD editability | Current boundary |
| --- | --- | --- | --- | --- | --- |
| Single-parameter sizing sweep (`opt.sizing_sweep`) | `stable` | `analysis/sizing_sweep_report.json` | No | winner applied via audited `cad.edit_parameter` (opt-in) | Each variant solved with REAL static FEA; failed variants reported honestly, never recommended; approval-gated |
| Multi-parameter DOE study (`opt.doe_sizing_study`) | `stable` | DOE report artifacts | No | recommend-only by default | Full-factorial/LHS within a 64-point budget; every point solved with real static FEA; baseline never modified |

## Topology Optimization

| Capability | Status | Primary artifacts | STEP output | CAD editability | Current boundary |
| --- | --- | --- | --- | --- | --- |
| 2D SIMP topology optimization | `stable` | `analysis/topology_optimization_problem.json`, `analysis/topology_optimization.json` | No direct STEP | Not applicable | Current mainline optimizer path |
| Result-guided optimization field | `stable` | `analysis/topology_optimization_guidance_field.json` | No | Not applicable | Guidance is advisory weighting from prior CAE evidence, not a proof that the new design is safe |
| 2D contour writeback (`extruded_region` / `density_contour`) | `stable` | updated `geometry/shape_ir.json`, `diagnostics/shape_ir_verification.json`, `registry/object_registry.json`, derived geometry artifacts | Yes | B-Rep | Best current path for CAD-friendly optimized geometry when the design is fundamentally 2D/extrudable |
| Voxel writeback (`density_voxels`) | `stable` | updated `geometry/shape_ir.json`, verification/object-registry outputs | Conditional | candidate-only | Honest discrete proxy of the thresholded field; useful for review and some downstream compilation paths, but not smooth CAD |
| 3D SIMP structured-voxel optimization | `experimental` | `analysis/topology_optimization.json` with `dimension=3d` and honest capability block | No direct STEP | Not applicable | Reference implementation only; `production_ready:false` remains the intended claim |
| 3D smooth mesh writeback (`smooth_mesh_proxy`) | `experimental` | updated `geometry/shape_ir.json`, `diagnostics/smooth_mesh_reconstruction.json` | No | mesh-only | Mesh proxy is review/output geometry, not reconstructed B-Rep; STEP only exists after a separate validated reconstruction ladder |

## Mesh-To-CAD Reconstruction

| Capability | Status | Primary artifacts | STEP output | CAD editability | Current boundary |
| --- | --- | --- | --- | --- | --- |
| Mesh region segmentation + analytic candidate detection | `stable` | `graph/mesh_region_graph.json`, `diagnostics/mesh_region_segmentation.json` | No | Not applicable | Diagnostic analysis of mesh evidence only |
| Mesh segmentation quality + re-segmentation hints v0 | `diagnostic-only` | `diagnostics/mesh_segmentation_quality.json`, `analysis/mesh_resegmentation_hints.json` | No | Not applicable | Assesses fragmentation/undersegmentation/fit-coverage/boundary quality; advisory-only, does not rerun segmentation or modify geometry |
| Plane/cylinder fitting over segmented mesh regions | `stable` | `graph/mesh_surface_fit.json`, `diagnostics/mesh_surface_fitting.json` | No | Not applicable | Fit evidence is not CAD; freeform/noisy areas remain explicit |
| Freeform/BSpline surface fitting evidence v0 | `experimental` | `graph/mesh_freeform_surface_fit.json`, `diagnostics/mesh_freeform_surface_fitting.json` | No | Not applicable | Approximate BSpline-like fits for freeform regions; evidence-only, not B-Rep faces, not STEP, not CAD-editable |
| Freeform surface readiness/quality scoring v0 | `diagnostic-only` | `diagnostics/mesh_freeform_reconstruction_readiness.json` | No | Not applicable | Advisory readiness assessment per freeform surface; does NOT generate B-Rep faces or export STEP |
| Freeform B-Rep face candidate generation v0 | `experimental` | `geometry/partial_freeform_brep_faces.json`, `diagnostics/freeform_brep_face_generation.json` | No | candidate_face_only | Validated OCC BSpline face candidates from ready freeform fits; candidate-only, not stitched, not STEP-exported |
| Freeform face trimming readiness v0 | `diagnostic-only` | `diagnostics/freeform_face_trimming_readiness.json` | No | Not applicable | Boundary/adjacency readiness assessment per freeform face; diagnostic-only, does not generate trimmed faces or stitch |
| Mesh-to-CAD reconstruction status aggregator v0 | `diagnostic-only` | `diagnostics/mesh_to_cad_reconstruction_status.json` | No | Not applicable | Aggregates all pipeline diagnostics into one status report; summary/decision layer only, does not create geometry or export STEP |

| Reconstruction readiness scoring | `stable` | `diagnostics/mesh_reconstruction_readiness.json`, `graph/mesh_reconstruction_plan.json` | No | Not applicable | Readiness is an evidence report only; it does not reconstruct geometry |
| Partial B-Rep surface candidate generation | `experimental` | `graph/mesh_brep_reconstruction_plan.json`, `geometry/partial_brep_surfaces.json`, `diagnostics/partial_brep_reconstruction.json` | No | candidate-only | Plane/cylinder surface candidates are intermediate reconstruction evidence |
| OCC face generation from analytic candidates | `experimental` | `geometry/partial_brep_faces.json`, `diagnostics/partial_brep_face_generation.json` | No | candidate-only | Validated candidate faces exist in memory/JSON evidence only; they are not yet a closed solid |
| Stitching planning and readiness | `experimental` | `graph/mesh_brep_stitching_plan.json`, `diagnostics/mesh_brep_stitching_readiness.json` | No | candidate-only | Planning/readiness never implies sewing/export success |
| OCC sewing / solidification diagnostics | `experimental` | `diagnostics/mesh_brep_sewing.json`, `diagnostics/mesh_brep_step_export.json`, `diagnostics/mesh_brep_roundtrip_verification.json` | Conditional | candidate-only until exported | Diagnostics are always written; STEP export is blocked unless a closed OCC-valid solid exists |
| Reconstructed STEP export | `experimental` | `geometry/reconstructed.step`, `geometry/reconstructed_topology_map.json`, preserved `geometry/mesh_topology_map.json`, updated `provenance/conversion_manifest.json` | Conditional validated-only | B-Rep | Reconstructed STEP is mesh-derived/lossy, never overwrites source/generated STEP, and is not production CAD certified |

## Assembly And Assembly-Aware Optimization

| Capability | Status | Primary artifacts | STEP output | CAD editability | Current boundary |
| --- | --- | --- | --- | --- | --- |
| Assembly IR processing + interface/connection resolution | `stable` | `assembly/assembly_ir.json`, `assembly/part_registry.json`, `assembly/interface_resolution.json`, `assembly/connection_graph.json`, `diagnostics/assembly_validation.json`, `diagnostics/assembly_connection_geometry.json` | No | Not applicable | Geometric plausibility/resolution is explicit; unresolved interfaces stay unresolved |
| Assembly CAE model drafting | `proxy-only` | `simulation/assembly_cae_model.json`, `diagnostics/assembly_cae_model_diagnostics.json` | No | Not applicable | Simplified connection proxies only; not nonlinear assembly contact |
| Optional assembly CalculiX deck generation / execution diagnostics | `proxy-only` | `simulation/assembly_calculix.inp`, `diagnostics/assembly_solver_deck_generation.json`, `diagnostics/assembly_solver_execution.json` | No | Not applicable | Deck generation and solver execution are best-effort/conditional on prerequisites |
| Assembly result normalization and mapping | `proxy-only` | `analysis/assembly_computed_metrics.json`, `analysis/assembly_field_regions.json`, `analysis/assembly_result_map.json`, `diagnostics/assembly_result_mapping.json` | No | Not applicable | Results remain tied to the simplified proxy model |
| Assembly-aware topology optimization setup | `experimental` | `analysis/assembly_topology_optimization_problem.json`, `diagnostics/assembly_topopt_derivation.json`, conditional `analysis/topology_optimization_problem.json` | No | Not applicable | Explicit selected design-part only; may return `needs_user_input` instead of guessing unsafe mappings |
| Assembly-aware topology optimization execution | `experimental` | `analysis/assembly_topology_optimization.json`, `diagnostics/assembly_topopt_execution.json`, selected-part `parts/<selected_part_id>/analysis/topology_optimization.json` and conditional `parts/<selected_part_id>/geometry/optimized_shape_ir.json` | Conditional on writeback path | selected-part only | Does not overwrite package-level geometry or frozen/reference parts |
| Post-optimization verification | `stable` | `diagnostics/assembly_post_optimization_verification.json`, `analysis/assembly_optimization_summary.json` | No | Not applicable | Verifies scope/preserve traceability/proxy honesty; does not certify physical equivalence |
| Recommendation and next-action postprocess | `diagnostic-only` | `analysis/assembly_design_recommendations.json`, `diagnostics/assembly_postprocess_report.json`, `analysis/assembly_next_actions.json` | No | Not applicable | Advisory only; never reruns optimization or mutates geometry automatically |

## Agent-Guided Design Studies

> The "no solver run" boundaries below apply to the **design-study contract
> layer** (validation/hints/ranking are deliberately solver-free). They do NOT
> mean variants are never solved — `opt.sizing_sweep` / `opt.doe_sizing_study`
> (see *Sizing Optimization* above) run real static FEA per variant.

| Capability | Status | Primary artifacts | STEP output | CAD editability | Current boundary |
| --- | --- | --- | --- | --- | --- |
| Design-study problem/candidate validation | `stable` | `analysis/design_study_problem.json`, `patches/design_candidates/<candidate_id>.json`, `diagnostics/design_study_problem_diagnostics.json`, `diagnostics/design_study_candidate_validation.json` | No | Not applicable | Contract + validation only; no search, execution, compile, CAE, or baseline mutation |
| Candidate execution into derived workspace | `stable` | `analysis/design_study_iterations.json`, `diagnostics/design_study_report.json`, `candidates/<candidate_id>/patch.json`, `candidates/<candidate_id>/geometry/shape_ir.json`, `candidates/<candidate_id>/provenance/candidate.json` | No package-level STEP | candidate-only | Explicit single-shot derived workspace; baseline geometry is never overwritten or promoted |
| Candidate-local evidence evaluation | `stable` | `candidates/<candidate_id>/analysis/evaluation.json`, `candidates/<candidate_id>/diagnostics/evaluation_report.json` | No | Not applicable | Reads only candidate-local static/neutral/proxy evidence; no solver run, no geometry recompile, proxy assembly evidence is lower-confidence |
| Candidate proposal hints | `stable` | `analysis/design_study_candidate_hints.json`, `diagnostics/design_study_candidate_hints_report.json` | No | Not applicable | Advisory variable-scoped hints only; no patch generation, optimizer/search, execution, CAE, ranking, acceptance, or geometry mutation |
| Candidate ranking and scoring | `stable` | `analysis/design_study_candidate_ranking.json`, `diagnostics/design_study_scoring_report.json` | No | Not applicable | Advisory ranking only; missing metrics remain `needs_more_evaluation`, no optimizer/search |
| Candidate acceptance as derived artifact | `stable` | `analysis/design_study_acceptance.json`, `diagnostics/design_study_acceptance_report.json`, `accepted/<candidate_id>/geometry/shape_ir.json` | No package-level STEP | derived-only | Explicit gated copy to `accepted/`; production approval and baseline promotion are not claimed |
| Candidate CAE evaluation request | `stable` | `candidates/<candidate_id>/analysis/cae_evaluation_request.json`, `candidates/<candidate_id>/diagnostics/cae_evaluation_request.json`, `candidates/<candidate_id>/simulation/setup.yaml`, `candidates/<candidate_id>/analysis/evaluation.json` | No | Not applicable | Explicit candidate-local CAE setup derivation + neutral metric normalization; solver execution disabled by default and best-effort when enabled; baseline artifacts never overwritten |

## Not Currently Claimed

| Capability | Status | Reason no stronger claim is made |
| --- | --- | --- |
| Nonlinear contact / friction modeling in assembly CAE | `future` | Current assembly CAE remains simplified proxy modeling |
| Bolt preload modeling | `future` | Explicitly not modeled in current assembly CAE/topopt outputs |
| Simultaneous multi-part assembly optimization | `future` | Current execution targets one selected design part only |
| General freeform mesh -> production NURBS/B-Rep reconstruction | `future` | Current reconstruction ladder is conservative and analytic-first (plane/cylinder oriented) |
| Automatic recommendation execution | `future` | Recommendations are intentionally advisory only |
| Manufacturing signoff / production certification | `future` | No current artifact or validation layer claims this |

## Focused Validation Coverage

The current snapshot is grounded in focused suites that cover the slices above:

- `aieng/tests/test_topology_optimization.py`
- `aieng/tests/test_topopt_result_guidance.py`
- `aieng/tests/test_mesh_reconstruction_readiness.py`
- `aieng/tests/test_mesh_brep_reconstruction.py`
- `aieng/tests/test_mesh_brep_stitching.py`
- `aieng/tests/test_mesh_brep_face_generation.py`
- `aieng/tests/test_mesh_brep_solidification.py`
- `aieng/tests/test_assembly_topopt.py`
- `aieng-ui/backend/tests/test_assembly_topopt_demo.py`
- `aieng/tests/test_design_study_evaluation.py`
- `aieng/tests/test_design_study_hints.py`
- `aieng/tests/test_design_study_ranking.py`
- `aieng-ui/backend/tests/test_api.py -k assembly_topology_optimization_run_endpoint_is_explicit_and_part_scoped`

This file is intentionally not a claim that every workspace-wide test suite is green.
Broader backend/API/runtime suites remain outside this snapshot and can be sensitive
to current working directory, optional dependencies, or unrelated in-progress areas.

## Backend stability gate

A lightweight smoke-test gate keeps this matrix, the demo catalog, and the showcase gallery in sync:

```bash
pytest aieng/tests/test_backend_stability_gate.py -q
```

It checks:
- Canonical demo/test files exist on disk
- Key artifact names are still referenced in docs
- Honesty boundary language has not been accidentally removed
- The showcase gallery JSON and markdown stay aligned

This is a consistency gate, not a production certification suite.

## Related Documentation

- [`demo_catalog.md`](demo_catalog.md) — Canonical backend demos with run commands, expected artifacts, and honesty boundaries for each capability area.
- [`showcase_gallery.md`](showcase_gallery.md) — Showcase gallery with demo talking points and visual guidance.
- [`showcase_gallery.json`](showcase_gallery.json) — Machine-readable gallery manifest.
