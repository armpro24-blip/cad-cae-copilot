"""JSON Schemas for high-frequency runtime tools.

Hand-written so MCP clients (Claude Code, Cursor, Cline, etc.) and the
in-process agent harness can produce valid tool calls. Schemas follow JSON
Schema draft-7 + the MCP convention of returning a single object at the
top level.

A tool not listed here falls back to a permissive ``{"type": "object"}``
schema in ``runtime.list_tools_for_mcp()``.

Adding a new schema is a one-line entry in ``TOOL_SCHEMAS``; keep schemas
minimal and pragmatic — describe the parameters the LLM actually needs to
get right, not every internal flag.
"""

from __future__ import annotations

from typing import Any


def _project_id_schema(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reusable schema for tools that only need a project_id."""
    schema: dict[str, Any] = {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {
                "type": "string",
                "description": "Workbench project ID (UUID-style).",
            },
        },
        "additionalProperties": True,
    }
    if extra:
        schema["properties"].update(extra.get("properties", {}))
        schema["required"] = list(set(schema["required"] + (extra.get("required") or [])))
    return schema


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    # ── agent onboarding ──────────────────────────────────────────────────────
    "aieng.list_projects": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
        "description": "No parameters required. Returns all known projects.",
    },
    "aieng.create_project": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Human-readable project name (optional). Defaults to 'Untitled project' if omitted or empty.",
            },
        },
        "additionalProperties": False,
        "description": "Create a new empty project and return its id, name, and status. Use this when the user wants to start modeling from scratch and no suitable project exists yet.",
    },
    "aieng.agent_readme": {
        "type": "object",
        "properties": {
            "detail": {
                "type": "string",
                "enum": ["quickstart", "full"],
                "description": "Return compact onboarding by default; use full only when the canonical complete guide is required.",
            },
        },
        "additionalProperties": False,
        "description": "No parameters required. Returns compact onboarding; detail=full returns canonical AGENTS.md.",
    },
    "aieng.guide": {
        "type": "object",
        "required": ["topic"],
        "properties": {
            "topic": {
                "type": "string",
                "enum": ["cad", "cae", "pointers", "tools", "workflows", "package", "fallback", "frontend", "approvals", "operators", "full"],
                "description": "Detailed guide topic to read from the canonical AGENTS.md.",
            },
        },
        "additionalProperties": False,
        "description": "Return one detailed guide topic without loading the full AGENTS.md.",
    },
    "aieng.delete_project": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string", "description": "Project id to permanently delete."},
        },
        "additionalProperties": True,
        "description": "Permanently delete a project (its directory + chat sessions). Approval required.",
    },
    "aieng.apply_shape_ir_patch": {
        "type": "object",
        "required": ["project_id", "patch"],
        "properties": {
            "project_id": {"type": "string"},
            "patch": {
                "type": "object",
                "description": (
                    "Shape IR patch: {operations: [...]}. Each op has 'op' (set_parameter | "
                    "move_control_point | add_node | remove_node | replace_node | connect | "
                    "disconnect | change_representation_backend) plus its fields (target, "
                    "parameter, path, value/delta, node, connection, value) and optional 'reason'. "
                    "Applied atomically against geometry/shape_ir.json; on success the package is "
                    "recompiled through runtime routing."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": "Validate + report the patch without writing or recompiling (default false).",
            },
        },
        "additionalProperties": True,
        "description": "Apply a surgical patch to a project's Shape IR (atomic, validated, recompiled).",
    },
    "opt.run_topology_optimization": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "problem": {
                "type": "object",
                "description": (
                    "Topology optimization problem: grid {nelx, nely}, volfrac, penalty, rmin, "
                    "max_iters, bcs {preset: cantilever|mbb_beam, OR supports/loads cell lists}, "
                    "optional design_space_node. For optimizer=precomputed, pass a 2D 'density' "
                    "grid. Omit problem (or set auto_derive) to derive it from the CAE setup."
                ),
            },
            "auto_derive": {
                "type": "boolean",
                "description": (
                    "Derive supports/loads/design-space from the project's CAE setup + geometry "
                    "(via opt.derive_problem_from_cae) before solving. Implied when problem is omitted; "
                    "any provided problem fields override the derived ones."
                ),
            },
            "dimension": {
                "type": "string",
                "enum": ["2d", "3d"],
                "description": (
                    "Problem dimension for auto_derive (default 2d). 3d uses the experimental "
                    "structured-voxel simp_3d optimizer and a full-3D derivation (no projection); "
                    "if BCs can't be safely mapped it returns status=needs_user_input. Implied 3d "
                    "when optimizer=simp_3d."
                ),
            },
            "optimizer": {
                "type": "string",
                "description": (
                    "Optimizer backend (default simp_2d, or simp_3d when dimension=3d). simp_3d is "
                    "experimental/structured-voxel/not-production. Unknown names fall back to simp_2d."
                ),
            },
        },
        "additionalProperties": True,
        "description": (
            "Run topology optimization (built-in self-contained 2D SIMP, compliance "
            "minimization). Writes analysis/topology_optimization.json. No external solver."
        ),
    },
    "opt.cae_evaluate_candidate": {
        "type": "object",
        "required": ["project_id", "candidate_id"],
        "properties": {
            "project_id": {"type": "string"},
            "candidate_id": {"type": "string", "description": "An executed design-study candidate (run opt.run_candidates first)."},
            "allow_solver_execution": {"type": "boolean", "description": "When true, compile the candidate geometry and run the real static solver (Gmsh + CalculiX). Default false = normalize existing candidate-local metrics only."},
            "allow_ranking_refresh": {"type": "boolean", "description": "Re-rank candidates after this evaluation (default false)."},
            "mesh_size_mm": {"type": "number", "description": "Gmsh target element size for the candidate solve (mm); defaults to the CAE setup value."},
            "timeout": {"type": "integer", "description": "Solver timeout in seconds (default 180)."},
        },
        "additionalProperties": False,
        "description": (
            "[APPROVAL REQUIRED] CAE-evaluate ONE design-study candidate with the REAL static "
            "solver. Derives candidate-local CAE setup from the baseline, and when "
            "allow_solver_execution=true compiles the candidate geometry on a throwaway copy "
            "and solves it (Gmsh + CalculiX), writing candidate-local computed_metrics + a "
            "candidate evaluation whose honesty.solver_executed reflects reality. The baseline "
            "is NEVER modified; no candidate is accepted/promoted. Degrades honestly (no fake "
            "success) when tools are unavailable or the candidate topology is stale. With "
            "allow_solver_execution=false it only normalizes existing candidate-local metrics."
        ),
    },
    "cae.mesh_convergence": {
        "type": "object",
        "required": ["project_id", "mesh_sizes"],
        "properties": {
            "project_id": {"type": "string"},
            "mesh_sizes": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Gmsh target element sizes (mm) to solve at — 3+ progressively finer (smaller) sizes recommended for a Grid Convergence Index; 2-6 allowed.",
            },
            "metrics": {
                "type": "array",
                "items": {"type": "string", "enum": ["max_von_mises_stress", "max_displacement"]},
                "description": "Metrics to assess (default both).",
            },
            "safety_factor": {"type": "number", "description": "GCI factor of safety (default 1.25, per ASME V&V-20 for >=3 grids)."},
            "converged_gci_percent": {"type": "number", "description": "Finest-grid GCI threshold (%) for the converged verdict (default 5)."},
            "timeout": {"type": "integer", "description": "Per-solve timeout in seconds (default 180)."},
        },
        "additionalProperties": False,
        "description": (
            "[APPROVAL REQUIRED] Mesh-convergence study: solve the project's current static "
            "geometry at each mesh size and report, per metric, the apparent order of "
            "convergence, the Richardson-extrapolated mesh-independent value, and the Grid "
            "Convergence Index (ASME V&V-20 discretization uncertainty) with a converged / "
            "not-converged verdict. Read-only on the package (mutates nothing); runs one "
            "solver execution per mesh size. The GCI is discretization uncertainty for these "
            "metrics on this geometry only - not model validity or certification."
        ),
    },
    "opt.sizing_sweep": {
        "type": "object",
        "required": ["project_id", "featureId", "parameterName"],
        "properties": {
            "project_id": {"type": "string"},
            "featureId": {"type": "string", "description": "Feature id carrying the editable parameter (from cad.list_editable_parameters / feature_graph)."},
            "parameterName": {"type": "string", "description": "Parameter name on that feature (its UPPER_SNAKE_CASE constant is what gets swept)."},
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Explicit list of dimension values to try (max 25). Mutually exclusive with range.",
            },
            "range": {
                "type": "object",
                "required": ["min", "max"],
                "properties": {
                    "min": {"type": "number", "description": "Start value (inclusive)."},
                    "max": {"type": "number", "description": "End value (inclusive)."},
                    "steps": {"type": "integer", "minimum": 2, "maximum": 25, "description": "Number of evenly spaced values to generate (e.g. steps=5 for 2/3/4/5/6)."},
                    "step": {"type": "number", "exclusiveMinimum": 0, "description": "Fixed increment between values (max 25 generated)."},
                },
                "additionalProperties": False,
                "description": "Range-based sweep specification. Mutually exclusive with values. Values are clamped to the parameter's declared min/max.",
            },
            "objective": {
                "type": "string",
                "enum": ["min_mass", "min_displacement", "min_stress"],
                "description": "What to minimize among feasible variants (default min_mass; mass is proportional to solid volume for a single material).",
            },
            "stress_limit": {"type": "number", "description": "Allowable-stress numerator, e.g. material yield (MPa). Constraint: max_von_mises_stress <= stress_limit / safety_factor."},
            "safety_factor": {"type": "number", "description": "Divides stress_limit to set the allowable stress (default 1.0)."},
            "displacement_limit": {"type": "number", "description": "Optional max-displacement constraint (mm)."},
            "mesh_size_mm": {"type": "number", "description": "Gmsh target element size (mm); defaults to the CAE setup value or 2.5."},
            "density": {"type": "number", "description": "Optional material density to convert solid volume to mass; omitted -> mass ranks by volume (equivalent for one material)."},
            "timeout": {"type": "integer", "description": "Per-variant solver timeout in seconds (default 180)."},
            "apply_winner": {"type": "boolean", "default": False, "description": "If true, apply the recommended value to the baseline through the audited cad.edit_parameter path (still one approval; reports regression_diff). Default false = recommend-only."},
        },
        "additionalProperties": False,
        "description": (
            "[APPROVAL REQUIRED] Parametric sizing sweep: vary ONE editable dimension across values "
            "or a {min,max,steps/step} range, solve EACH variant with the real static solver "
            "(Gmsh + CalculiX), and rank by objective subject to a stress/displacement constraint. "
            "Baseline is never modified unless apply_winner=true, in which case the winning value "
            "is applied through the approval-gated cad.edit_parameter path. Runs N solver executions."
        ),
    },

    "opt.doe_sizing_study": {
        "type": "object",
        "required": ["project_id", "parameters"],
        "properties": {
            "project_id": {"type": "string"},
            "parameters": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "object",
                    "required": ["featureId", "parameterName"],
                    "properties": {
                        "featureId": {"type": "string", "description": "Feature id carrying the editable parameter."},
                        "parameterName": {"type": "string", "description": "Parameter name on that feature (its UPPER_SNAKE_CASE constant is swept)."},
                        "values": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Explicit list of values for this parameter (max 25). Mutually exclusive with range.",
                        },
                        "range": {
                            "type": "object",
                            "required": ["min", "max"],
                            "properties": {
                                "min": {"type": "number"},
                                "max": {"type": "number"},
                                "steps": {"type": "integer", "minimum": 2, "maximum": 25},
                                "step": {"type": "number", "exclusiveMinimum": 0},
                            },
                            "additionalProperties": False,
                            "description": "Range specification; provide steps or step. Values clamped to declared parameter bounds.",
                        },
                    },
                    "additionalProperties": False,
                },
                "description": "List of 2+ editable parameters to vary jointly.",
            },
            "method": {
                "type": "string",
                "enum": ["full_factorial", "lhs"],
                "default": "full_factorial",
                "description": "Design-point generation strategy. full_factorial enumerates the Cartesian product (must fit inside budget). lhs draws a Latin-hypercube-style sample up to budget.",
            },
            "budget": {"type": "integer", "minimum": 2, "maximum": 64, "default": 64, "description": "Maximum number of design points to solve."},
            "objective": {"type": "string", "enum": ["min_mass", "min_displacement", "min_stress"], "default": "min_mass"},
            "stress_limit": {"type": "number"},
            "safety_factor": {"type": "number", "default": 1.0},
            "displacement_limit": {"type": "number"},
            "mesh_size_mm": {"type": "number"},
            "timeout": {"type": "integer", "default": 180},
            "density": {"type": "number"},
            "seed": {"type": "integer", "description": "Optional random seed for lhs reproducibility."},
        },
        "additionalProperties": False,
        "description": (
            "[APPROVAL REQUIRED] Multi-parameter DOE sizing study: jointly vary 2+ editable "
            "parameters, solve each design point with the real static solver (Gmsh + CalculiX), "
            "and rank by objective subject to stress/displacement constraints. Baseline is never "
            "modified; recommendation is applied through approval-gated cad.edit_parameter. "
            "Budget capped at 64 solver executions."
        ),
    },

    "opt.derive_problem_from_cae": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "dimension": {"type": "string", "enum": ["2d", "3d"],
                          "description": "2d (default) projects to a plane; 3d builds a structured voxel grid (no projection). 3d returns status=needs_user_input if BCs can't be safely mapped."},
            "resolution": {"type": "integer", "description": "2D: cells along the longest design-space axis (default 48)."},
            "resolution_3d": {"type": "integer", "description": "3D: voxels along the longest axis (default 16) — keep small."},
            "volfrac": {"type": "number", "description": "Target volume fraction (default 0.5; 0.3 for 3d)."},
            "penalty": {"type": "number", "description": "SIMP penalization exponent (default 3.0)."},
            "rmin": {"type": "number", "description": "Sensitivity filter radius in cells (default 1.5)."},
            "max_iters": {"type": "integer", "description": "Optimizer iteration cap (default 40; 30 for 3d)."},
        },
        "additionalProperties": True,
        "description": (
            "Derive a topology-optimization problem (grid + supports + loads + design space) from "
            "the project's CAE setup + geometry. dimension=2d (default) projects to a plane; 3d "
            "keeps the full 3D layout. Read-only; returns problem + derivation (or needs_user_input)."
        ),
    },
    "opt.writeback_to_shape_ir": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "method": {
                "type": "string",
                "enum": ["contour", "voxels", "surface", "smooth_mesh", "marching_cubes"],
                "description": (
                    "Geometry for the optimized body. 2D: 'contour' (default) = smooth "
                    "marching-squares boundary, extruded; 'voxels' = blocky cells. 3D: "
                    "'surface'/'smooth_mesh'/'marching_cubes' (default) = smooth marching-cubes "
                    "mesh proxy (a smooth_mesh_proxy node); 'voxels' = blocky voxel union. 3D "
                    "outputs are mesh / lossy / preview-only / not production CAD (no B-Rep)."
                ),
            },
            "boundary": {
                "type": "string",
                "enum": ["spline", "polygon"],
                "description": (
                    "For method=contour, how boundary loops are interpreted: 'spline' "
                    "(default) = closed periodic spline (CAD-friendly curve / clean NURBS "
                    "edge in B-Rep, densified smooth polygon in mesh); 'polygon' = straight "
                    "segments."
                ),
            },
            "representation": {
                "type": "string",
                "description": (
                    "Compile target for the optimized body. Default brep_build123d -> "
                    "analytic faces an engineer can pick / export to STEP / feed back into "
                    "CAD/CAE; manifold_mesh -> watertight mesh (also the auto-fallback if "
                    "the B-Rep build fails to execute)."
                ),
            },
            "cell_size": {
                "type": "array",
                "items": {"type": "number"},
                "description": "In-plane voxel cell size [sx, sy] in mm (default [1, 1]).",
            },
            "thickness": {"type": "number", "description": "Extrusion depth in Z (default = larger cell edge)."},
            "origin": {"type": "array", "items": {"type": "number"}, "description": "Field origin [x, y, z]."},
            "node_id": {"type": "string", "description": "Override the generated Shape IR node id."},
        },
        "additionalProperties": True,
        "description": (
            "Author the topology-optimization result back into geometry/shape_ir.json as one "
            "density_voxels node and recompile. Run opt.run_topology_optimization first."
        ),
    },
    "opt.topology_to_sizing": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
        },
        "additionalProperties": False,
        "description": (
            "Bridge a 2D contour topology writeback to a sizing study. Verifies the "
            "project has a 2D topology result and a contour writeback (extruded_region), "
            "auto-parameterizes the recovered thickness, and writes the full optimization-"
            "study envelope plus a chain-linkage decision-log entry. Refuses 3D / voxel "
            "inputs with needs_user_input. Run opt.writeback_to_shape_ir (method=contour) first."
        ),
    },
    "opt.propose_candidates": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "algorithm": {
                "type": "string",
                "enum": ["grid", "random", "latin_hypercube", "lhs"],
                "description": "Sampling algorithm (default: read from optimization_study.json or 'grid').",
            },
            "count": {
                "type": "integer", "minimum": 1,
                "description": "Number of candidates to generate (for random/LHS; auto-computed for grid).",
            },
            "seed": {
                "type": "integer",
                "description": "Random seed for reproducibility (default: 0 or from study config).",
            },
            "max_candidates": {
                "type": "integer", "minimum": 1,
                "description": "Hard cap on emitted candidates (default: 50 or from study).",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Overwrite existing candidate patches with the same IDs (default: false).",
            },
        },
        "additionalProperties": False,
        "description": (
            "Generate candidate parameter sets from optimization variables. "
            "Reads analysis/optimization_variables.json and optionally "
            "analysis/optimization_study.json, runs the sampler, and writes "
            "candidates to patches/design_candidates/<cid>.json. Does NOT "
            "execute candidates, recompile geometry, run CAE, or modify baseline."
        ),
    },
    "opt.run_candidates": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "candidate_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Explicit, ordered candidate ids to execute. Omit to run all "
                    "candidates discovered in the package."
                ),
            },
            "compile": {
                "type": "boolean",
                "description": (
                    "Recompile each candidate in a throwaway copy (default true). "
                    "false applies the patch only, with honestly partial evaluation."
                ),
            },
            "max_candidates": {
                "type": "integer", "minimum": 0,
                "description": "Hard cap on candidates executed this call; remainder is reported as skipped.",
            },
        },
        "additionalProperties": False,
        "description": (
            "Execute proposed design-study candidates into derived workspaces "
            "(candidates/<cid>/). Applies each patch to a DERIVED copy of the "
            "baseline Shape IR, optionally recompiling in a throwaway copy. "
            "Failures are recorded per-candidate and the batch continues. Does "
            "NOT run CAE, accept any candidate, or modify the baseline."
        ),
    },
    "opt.evaluate_candidates": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "candidate_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Explicit, ordered candidate ids to evaluate. Omit to evaluate "
                    "all executed candidates (those with a derived workspace)."
                ),
            },
            "cae": {
                "type": "boolean",
                "description": (
                    "Derive each candidate's CAE setup and normalize CAE evidence "
                    "before evaluating (default false). Solver execution stays "
                    "disabled unless allow_solver_execution is set."
                ),
            },
            "mode": {
                "type": "string",
                "description": "CAE evaluation mode (forwarded when cae=true; e.g. prepare_only).",
            },
            "allow_solver_execution": {
                "type": "boolean",
                "description": "Permit candidate-local solver execution when cae=true (best-effort/skipped in v0).",
            },
            "max_candidates": {
                "type": "integer", "minimum": 0,
                "description": "Hard cap on candidates evaluated this call; remainder is reported as skipped.",
            },
        },
        "additionalProperties": False,
        "description": (
            "Evaluate executed design-study candidates from candidate-local "
            "evidence. Normalizes mass / volume / max_stress / max_deflection / "
            "min_safety_factor, evaluates constraints, and classifies feasibility. "
            "Missing CAE metrics are recorded honestly as unknown. Writes "
            "candidates/<cid>/analysis/evaluation.json. Does NOT accept any "
            "candidate or modify the baseline."
        ),
    },
    "opt.rank_candidates": {
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
        "additionalProperties": False,
        "description": (
            "Rank evaluated design-study candidates (advisory). Classifies "
            "feasibility, scores against the objective + constraints, and writes "
            "analysis/design_study_candidate_ranking.json. best_candidate_id is set "
            "only for a feasible, improving, high-confidence candidate. Does NOT "
            "accept a candidate, run CAE, or modify the baseline."
        ),
    },
    "opt.explain_recommendation": {
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
        "additionalProperties": False,
        "description": (
            "Explain the candidate ranking as an advisory, reason-coded "
            "recommendation. Reads the ranking (run opt.rank_candidates first) and "
            "writes analysis/optimization_recommendation.json. Advisory only — does "
            "NOT accept a candidate, run CAE, or modify the baseline."
        ),
    },
    "opt.accept_candidate": {
        "type": "object",
        "required": ["project_id", "candidate_id"],
        "properties": {
            "project_id": {"type": "string"},
            "candidate_id": {"type": "string"},
            "accepted_by": {
                "type": "string",
                "description": "Who is accepting (recorded in provenance; default 'agent').",
            },
            "reasoning": {
                "type": "string",
                "description": "Optional human-readable rationale recorded with the acceptance.",
            },
            "override_unsafe": {
                "type": "boolean",
                "description": (
                    "Force acceptance of a candidate that is not best_candidate_id or "
                    "not safe_to_accept (default false). Recorded as a warning."
                ),
            },
        },
        "additionalProperties": False,
        "description": (
            "[APPROVAL REQUIRED] Accept one ranked design-study candidate into a "
            "derived accepted workspace (accepted/<cid>/). Eligible only when feasible "
            "and best_candidate_id + safe_to_accept, unless override_unsafe is set. "
            "Does NOT overwrite baseline geometry, auto-promote, or claim production "
            "approval."
        ),
    },
    "opt.write_report": {
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
        "additionalProperties": False,
        "description": (
            "Aggregate the optimization study into diagnostics/optimization_report.json "
            "from existing package artifacts (problem, variables/objectives/constraints, "
            "candidates + metrics, ranking, failed candidates, recommendation, "
            "acceptance, decision log). Read-only with respect to engineering state; "
            "does NOT execute/evaluate/rank/accept candidates, run CAE, or modify the "
            "baseline."
        ),
    },
    "opt.propose_next": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "algorithm": {"type": "string", "enum": ["trust_region", "slsqp", "bayesian", "genetic"],
                          "description": "Proposer algorithm: trust_region (default), slsqp, bayesian, or genetic."},
            "count": {"type": "integer", "minimum": 1,
                      "description": "Number of candidates to propose this round (default 4)."},
            "shrink": {"type": "number", "exclusiveMinimum": 0, "maximum": 1,
                       "description": "Trust-region radius shrink factor per iteration (default 0.5)."},
            "seed": {"type": "integer", "description": "Random seed for reproducibility (default 0)."},
        },
        "additionalProperties": False,
        "description": (
            "Propose the next batch of candidates by trust-region local refinement, "
            "SLSQP local step, Bayesian surrogate, or genetic algorithm. Writes candidate patches; does NOT run/evaluate/"
            "accept candidates or modify the baseline."
        ),
    },
    "opt.check_convergence": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "evaluations_total": {"type": "integer", "minimum": 0,
                                  "description": "Override the total-evaluations count (default: candidate count)."},
            "failures_this_round": {"type": "integer", "minimum": 0,
                                    "description": "Number of failed candidates this round (default 0)."},
            "had_success": {"type": "boolean",
                            "description": "Whether this round produced any successful candidate (default true)."},
            "proposer_exhausted": {"type": "boolean",
                                   "description": "Signal that the proposer cannot suggest a new point (default false)."},
        },
        "additionalProperties": False,
        "description": (
            "Record the current iteration's incumbent into "
            "analysis/optimization_iterations.json and return a deterministic, advisory "
            "convergence verdict. Advisory only — never accepts a candidate, runs CAE, "
            "or modifies the baseline."
        ),
    },
    "opt.select_optimizer": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "optimizer": {
                "type": "string",
                "enum": ["trust_region", "slsqp", "bayesian", "genetic"],
                "description": (
                    "Optional explicit override. When omitted the chooser reads "
                    "optimization_variables.json + optimization_study.json and picks "
                    "deterministically."
                ),
            },
        },
        "additionalProperties": False,
        "description": (
            "Deterministically select an optimizer for the design study and append a "
            "reason-coded entry to analysis/optimization_decision_log.json. Chooses "
            "trust_region (default), slsqp, bayesian, or genetic based on variable types, "
            "dimensionality, and CAE-availability. Honors explicit optimizer override. "
            "No search runs inside the call."
        ),
    },
    "opt.run_assembly_topology_optimization": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "optimizer": {
                "type": "string",
                "description": "Optimizer backend. Defaults to simp_2d or simp_3d from the derived problem dimension.",
            },
            "writeback": {
                "type": "boolean",
                "description": "Write a selected-part derived optimized Shape IR artifact when safe (default true).",
            },
            "method": {
                "type": "string",
                "enum": ["contour", "voxels", "surface", "smooth_mesh", "marching_cubes"],
                "description": "Selected-part writeback geometry method. Defaults to contour for 2D and smooth_mesh for 3D.",
            },
            "representation": {
                "type": "string",
                "description": "Selected-part derived Shape IR representation. Defaults to brep_build123d for 2D and manifold_mesh for 3D.",
            },
            "boundary": {
                "type": "string",
                "enum": ["spline", "polygon"],
                "description": "2D contour boundary style for selected-part writeback (default spline).",
            },
        },
        "additionalProperties": True,
        "description": (
            "Explicitly run assembly-aware topology optimization for one selected design part. "
            "Consumes analysis/assembly_topopt_problem.json and analysis/topology_optimization_problem.json, "
            "calls the existing optimizer, writes assembly diagnostics/provenance, and creates selected-part "
            "derived artifacts without overwriting package-level geometry or reference parts."
        ),
    },
    "cae.map_results": {
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
        "additionalProperties": True,
        "description": (
            "Map CAE results (computed_metrics + field_regions) back to topology "
            "entities, object_registry objects, and source_ir_node. Writes "
            "analysis/cae_result_map.json. Read-only analysis (no solver)."
        ),
    },
    "aieng.find_projects_by_part": {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Substring matched case-insensitively against named-part labels, "
                    "e.g. 'optimus', 'bracket', 'mounting_hole'."
                ),
            },
        },
        "additionalProperties": True,
        "description": "Find projects whose geometry contains a named part matching the query.",
    },

    # ── read-only inspection ──────────────────────────────────────────────────
    "aieng.inspect_package": _project_id_schema(),
    "aieng.agent_context": _project_id_schema(),
    "aieng.read_audit_log": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Max number of audit entries to return (default 50).",
            },
        },
        "additionalProperties": True,
    },
    "aieng.validate": _project_id_schema(),
    "aieng.write_completeness_report": _project_id_schema(),
    "aieng.update_validation_status": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "status": {
                "type": "object",
                "description": "Per-category validation status fields to merge.",
            },
        },
        "additionalProperties": True,
    },

    # ── conversion ────────────────────────────────────────────────────────────
    "aieng.convert": {
        "type": "object",
        "required": ["project_id", "sourcePath"],
        "properties": {
            "project_id": {"type": "string"},
            "sourcePath": {
                "type": "string",
                "description": "Absolute path to a .step / .stp / .FCStd / .shape.json / .shape_ir.json source file to import.",
            },
            "executeShapeIr": {
                "type": "boolean",
                "description": "For Shape IR sources, execute the generated build123d source and publish a viewer preview (default true).",
            },
        },
        "additionalProperties": True,
    },

    # ── CAD generation (agent writes the code, we execute) ───────────────────
    "cad.confirm_modeling_plan": {
        "type": "object",
        "required": ["project_id", "summary", "steps"],
        "properties": {
            "project_id": {"type": "string"},
            "summary": {
                "type": "string",
                "description": "Concise user-facing summary of the proposed modeling plan.",
            },
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
                "description": "Ordered CAD creation/edit and review steps covered by this approval.",
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Important dimensions, defaults, and interpretation assumptions.",
            },
            "scope": {
                "type": "string",
                "description": "Boundary of the approved work; material scope changes require another confirmation.",
            },
        },
        "additionalProperties": False,
    },
    "cad.execute_build123d": {
        "type": "object",
        "required": ["project_id", "code"],
        "properties": {
            "project_id": {"type": "string"},
            "name": {
                "type": "string",
                "description": (
                    "Optional human-recognizable project name (e.g. 'Optimus + Bumblebee'). "
                    "Set this so the project is findable in list_projects instead of staying "
                    "the default 'STEP workbench project'. If omitted, a placeholder-named "
                    "project is auto-named from its part labels."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "Full build123d Python script. Must bind the final model to a "
                    "variable named `result`. Do NOT include export calls — the runner "
                    "adds them. To name parts so you can reference them later, set "
                    "`.label` on shapes and combine with Compound, e.g. "
                    "`fl = Cylinder(3, 30); fl.label = 'motor_pod_FL'; "
                    "result = Compound(children=[body, fl])` — labels appear as named "
                    "parts in topology_map and feature_graph. "
                    "Also set `.color = Color(r, g, b)` (RGB in 0..1) on each part — "
                    "colors render in the multi-view thumbnail AND travel through to "
                    "the GLB that the UI viewer shows, so a user looking at the model "
                    "sees the colors you assigned. "
                    "In mode=append, the previous model is available as `previous_result`."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["replace", "append"],
                "description": (
                    "replace (default): the script defines the whole model. "
                    "append: the previously-stored script runs first and its model is "
                    "exposed as `previous_result`; your code then adds to it and must "
                    "still reassign `result` (e.g. "
                    "`result = Compound(children=[previous_result, new_part])`). "
                    "Append requires an existing model — run once with replace first."
                ),
            },
            "write_files": {
                "type": "boolean",
                "description": "Write artifacts into the .aieng package (default true).",
            },
            "model_kind": {
                "type": "string",
                "enum": ["auto", "organic", "mechanical"],
                "description": (
                    "Gates the feature-graph heuristics (default auto). "
                    "'mechanical' runs bolt-pattern + base-plate detection; "
                    "'organic' skips them (use for characters/vehicles/products, "
                    "where those heuristics mislabel limb cylinders as mounting holes). "
                    "'auto' infers from part labels and whether the organic helpers "
                    "(lofted_stack/capsule/…) are used."
                ),
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600,
                "description": "Subprocess timeout in seconds (default 60).",
            },
            "thumbnail": {
                "type": "boolean",
                "description": (
                    "Return a rendered PNG so you can visually verify the geometry "
                    "(default true). The image is a 2x2 contact sheet with four "
                    "labelled views: front, side, top, iso — each catches problems "
                    "the others hide (alignment in front, depth in side, layout in "
                    "top, overall form in iso). Per-part `.color` values applied to "
                    "build123d shapes are honored. The MCP client receives this as an "
                    "image content block. Set false to skip rendering."
                ),
            },
            "response_detail": {
                "type": "string",
                "enum": ["full", "compact"],
                "description": (
                    "Response verbosity. full (default) returns the full geometry_report "
                    "and thumbnail unless thumbnail=false. compact returns a one-line "
                    "geometry_report summary and omits the thumbnail unless thumbnail=true; "
                    "use compact for iterative MCP loops to reduce tokens."
                ),
            },
        },
        "additionalProperties": True,
    },

    # ── CAD skill planner (read-only, agent-orchestrated) ───────────────────
    "cad.plan_build123d_skill": {
        "type": "object",
        "required": ["project_id", "message"],
        "properties": {
            "project_id": {"type": "string"},
            "message": {
                "type": "string",
                "description": "Natural-language CAD request to route through a deterministic CAD skill template.",
            },
            "outer_diameter_mm": {
                "type": "number",
                "description": "Optional explicit flange outside diameter override.",
            },
            "thickness_mm": {
                "type": "number",
                "description": "Optional explicit flange thickness override.",
            },
            "center_bore_diameter_mm": {
                "type": "number",
                "description": "Optional explicit center bore diameter override.",
            },
            "bolt_circle_diameter_mm": {
                "type": "number",
                "description": "Optional explicit bolt pitch-circle diameter override.",
            },
            "bolt_hole_diameter_mm": {
                "type": "number",
                "description": "Optional explicit bolt through-hole diameter override.",
            },
            "bolt_hole_count": {
                "type": "integer",
                "minimum": 2,
                "maximum": 16,
                "description": "Optional explicit bolt-hole count override.",
            },
        },
        "additionalProperties": True,
    },

    # ── Reference image attach (per-project, used by thumbnails) ─────────────
    "cad.set_reference_image": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "image_url": {
                "type": "string",
                "description": (
                    "HTTP(S) URL of a reference image (jpg/png/webp). Fetched "
                    "server-side, downscaled to fit 800x800, and stored as "
                    "geometry/reference.png in the .aieng package. Either "
                    "image_url or image_path is required."
                ),
            },
            "image_path": {
                "type": "string",
                "description": (
                    "Local file path to a reference image. Use when the image "
                    "is on the workbench host, e.g. /tmp/optimus_ref.jpg."
                ),
            },
            "description": {
                "type": "string",
                "description": "Short caption for the reference, stored in geometry/reference.json.",
            },
        },
        "additionalProperties": True,
    },

    "cad.search_reference_image": {
        "type": "object",
        "required": ["project_id", "query"],
        "properties": {
            "project_id": {"type": "string"},
            "query": {
                "type": "string",
                "description": (
                    "Free-text image search, e.g. 'Boeing 747 side view' or "
                    "'Eames lounge chair'. Searched against Wikimedia Commons; "
                    "the best-ranked raster match is fetched and attached as "
                    "the project's reference image."
                ),
            },
            "source": {
                "type": "string",
                "description": "Reference source; only 'wikimedia' is supported (default).",
            },
            "description": {
                "type": "string",
                "description": "Optional caption override stored in geometry/reference.json.",
            },
        },
        "additionalProperties": True,
    },

    # ── Critique: deterministic engineering audit (read-only) ───────────────
    "cad.critique": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["auto", "engineering", "geometry"],
                "description": (
                    "auto (default): geometry sanity + engineering audit when the "
                    "model has canonically-labelled engineering features (rib/"
                    "base_plate/mounting_hole/...). engineering: force the "
                    "manufacturing audit. geometry: only basic sanity checks "
                    "(component counts, floating components)."
                ),
            },
            "process": {
                "type": "string",
                "enum": ["cnc", "sheet_metal", "fdm", "sla"],
                "description": (
                    "Target manufacturing process rule pack. cnc (default) uses "
                    "3mm wall / 2mm corner / standard drill checks. sheet_metal "
                    "uses 2mm / 0.5mm / standard drills. fdm and sla relax wall "
                    "and corner limits and skip standard-hole checking."
                ),
            },
            "min_wall_mm": {
                "type": "number",
                "description": "Override the selected process rule pack's min wall thickness.",
            },
            "min_corner_radius_mm": {
                "type": "number",
                "description": "Override the selected process rule pack's min internal corner radius.",
            },
        },
        "additionalProperties": True,
    },

    # ── Design review: critique + structure + fix targets (read-only) ───────
    "cad.design_review": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["auto", "engineering", "geometry"],
                "description": "Forwarded to cad.critique (default auto).",
            },
            "min_wall_mm": {
                "type": "number",
                "description": "Forwarded to cad.critique (default 3mm = CNC aluminium).",
            },
            "min_corner_radius_mm": {
                "type": "number",
                "description": "Forwarded to cad.critique (default 2mm).",
            },
            "response_detail": {
                "type": "string",
                "enum": ["compact", "full"],
                "description": (
                    "compact: prioritized actions + summary only. full (default): "
                    "also includes every enriched finding."
                ),
            },
        },
        "additionalProperties": True,
    },

    "cad.tolerance_stackup": {
        "type": "object",
        "required": ["contributors"],
        "properties": {
            "contributors": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["nominal", "plus", "minus"],
                    "properties": {
                        "name": {"type": "string", "description": "Human-readable label for this contributor (e.g. \"wall_a\", \"bolt_spacing\")."},
                        "nominal": {"type": "number", "description": "Central dimension value."},
                        "plus": {"type": "number", "description": "Upper-side tolerance (added to nominal)."},
                        "minus": {"type": "number", "description": "Lower-side tolerance. May be positive (subtracted from nominal) or negative (interpreted as a magnitude)."},
                        "distribution": {"type": "string", "enum": ["normal", "uniform"], "description": "Distribution assumption for RSS (default normal)."},
                    },
                    "additionalProperties": False,
                },
                "description": "Ordered 1D dimension chain. Each contributor contributes its nominal and tolerance to the stack-up.",
            },
            "confidence_level": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 0.9999,
                "default": 0.95,
                "description": "Confidence level for the RSS band (default 0.95).",
            },
        },
        "additionalProperties": False,
        "description": (
            "Read-only 1D tolerance stack-up: given an ordered chain of dimensions "
            "with tolerances, compute worst-case (arithmetic) and statistical RSS "
            "min/max. Reports controlling contributors and honesty notes about "
            "independence and the +/- 3-sigma assumption. No geometry mutation, no solver."
        ),
    },

    "cad.validate_subpart": {
        "type": "object",
        "required": ["code"],
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "A build123d fragment to validate in isolation. Same contract as "
                    "cad.execute_build123d: assign the final shape to `result`, no export "
                    "calls; the high-level helpers (lofted_stack, capsule, ...) are available."
                ),
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600,
                "description": "Subprocess timeout in seconds (default 60).",
            },
            "project_id": {
                "type": "string",
                "description": "Optional / ignored — validation runs in isolation and never touches a project.",
            },
        },
        "additionalProperties": False,
        "description": (
            "Read-only: execute a build123d fragment in an isolated subprocess (no "
            "package write, no project mutation) and report whether it builds into a "
            "usable solid — build success or the exact error, a non-empty-solid check, "
            "solid/face counts, per-part + total volume/area, and the union bounding "
            "box. Use it to verify a sub-structure (a sketch->solid, a boolean, one "
            "sub-assembly) BEFORE committing it via cad.execute_build123d or "
            "cad.replace_part, instead of one-shotting a whole complex model. "
            "'valid' means it builds into a non-empty solid — NOT a manifold/watertight "
            "or manufacturability guarantee."
        ),
    },

    # ── Assembly authoring (write parts/mates into Assembly IR v0) ───────────
    "cad.define_part": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "part_id": {"type": "string", "description": "Stable id for the part; defaults to geometry_ref, then name."},
            "name": {"type": "string", "description": "Human-readable part name."},
            "role": {
                "type": "string",
                "enum": ["design_part", "reference_part", "fixture", "load_source", "fastener", "external_context"],
                "description": "Assembly role (default design_part).",
            },
            "geometry_ref": {
                "type": "string",
                "description": (
                    "Named part label (.label in the CAD Compound) to link this assembly part to. "
                    "Verified against the model topology when present; an unresolved ref is reported "
                    "honestly (geometry_ref_known=false), never fabricated."
                ),
            },
            "transform": {
                "type": "object",
                "additionalProperties": True,
                "description": "Optional placement, e.g. {\"translation\": [x,y,z], \"rotation_euler_deg\": [rx,ry,rz]}.",
            },
            "material": {"description": "Material name or reference."},
            "editable": {"type": "boolean", "description": "Whether this part is editable (defaults by role)."},
        },
        "additionalProperties": False,
        "description": (
            "Author one part into the project's Assembly IR v0 (assembly/assembly_ir.json), "
            "initialising the IR if absent and refreshing the derived registry / connection "
            "graph / CAE setup draft. Link it to a named CAD part via geometry_ref. "
            "Representation + validation only — no contact physics, no preload, no solver."
        ),
    },
    "cad.define_mate": {
        "type": "object",
        "required": ["project_id", "connection_type", "part_a", "part_b"],
        "properties": {
            "project_id": {"type": "string"},
            "connection_type": {
                "type": "string",
                "enum": ["rigid_tie", "bonded", "bolted_proxy", "welded_proxy", "contact_proxy", "spring_proxy", "unknown"],
                "description": (
                    "Connection (mate) type. rigid_tie/bonded are exact ties; *_proxy types model "
                    "SIMPLIFIED physics and automatically carry honest limitations."
                ),
            },
            "part_a": {"type": "string", "description": "id of the first part — must already be defined via cad.define_part."},
            "part_b": {"type": "string", "description": "id of the second part — must already be defined."},
            "connection_id": {"type": "string", "description": "Stable id; derived from the parts + type if omitted."},
            "interface_a": {"type": "string", "description": "Optional interface id on part_a."},
            "interface_b": {"type": "string", "description": "Optional interface id on part_b."},
            "behavior": {"description": "One value or a list from {load_transfer, positioning_only, preserve_interface}."},
            "parameters": {"type": "object", "additionalProperties": True, "description": "Optional simplified parameters (e.g. stiffness)."},
            "confidence": {"description": "high | medium | low or a number (default low)."},
            "limitations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What the simplified proxy does NOT capture; auto-filled with an honest default for proxy types if omitted.",
            },
            "mate_predicate": {
                "type": "string",
                "enum": ["concentric", "tangent", "coincident", "clearance"],
                "description": (
                    "Optional engineering relationship to VERIFY against the resolved interface "
                    "geometry: concentric (shaft-in-bore coaxial), tangent (gear pitch circles "
                    "mesh), coincident (mating faces flush), clearance (a target gap). Requires "
                    "interface_a/interface_b. A violated predicate marks the connection geometry invalid."
                ),
            },
            "mate_tolerance_mm": {"type": "number", "description": "Tolerance for the mate predicate (mm)."},
            "expected_clearance_mm": {"type": "number", "description": "Target gap for the 'clearance' predicate (mm)."},
        },
        "additionalProperties": False,
        "description": (
            "Author one connection (mate) between two already-defined assembly parts in the "
            "Assembly IR. Refuses dangling connections (both parts must exist — call cad.define_part "
            "first) and never records a proxy connection without limitations. v0 proxy honesty: "
            "positioning + simplified load transfer only; no contact mechanics, no bolt preload, no solver."
        ),
    },

    "cad.define_interface": {
        "type": "object",
        "required": ["project_id", "part_id", "semantic_role"],
        "properties": {
            "project_id": {"type": "string"},
            "part_id": {"type": "string", "description": "id of the part (must be defined via cad.define_part) the interface belongs to."},
            "semantic_role": {
                "type": "string",
                "enum": ["mounting_face", "bolt_hole", "contact_face", "weld_face", "load_face", "support_face"],
                "description": "What the interface region is for.",
            },
            "interface_id": {"type": "string", "description": "Stable id; auto-generated from part + role if omitted."},
            "face_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "B-Rep face ids (e.g. face_013 from the brep graph / agent_context) this interface binds to. Verified against the model topology.",
            },
            "edge_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional B-Rep edge ids."},
            "vertex_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional B-Rep vertex ids."},
        },
        "additionalProperties": False,
        "description": (
            "Author one interface that binds an assembly part to specific B-Rep entities "
            "(@face:*). This is what makes a mate geometric: once both parts carry interfaces, a "
            "cad.define_mate that references interface_a/interface_b is resolved to world "
            "coordinates and gets a geometry_status (plausible / warning / invalid). Face refs are "
            "verified against the model topology and reported honestly (face_ids_known true/false/"
            "null), never fabricated. Geometry validation only — no contact, no preload, no solver."
        ),
    },

    "cad.validate_targets": {
        "type": "object",
        "required": ["project_id", "targets"],
        "properties": {
            "project_id": {"type": "string"},
            "targets": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["kind"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "named_part_present", "feature_present", "part_count",
                                "overall_size", "part_size", "part_center",
                                "no_floating_parts", "no_deep_overlap",
                            ],
                        },
                        "id": {"type": "string", "description": "Optional stable id for this target."},
                        "part": {"type": "string", "description": "Named part (for *_present / part_size / part_center)."},
                        "feature_type": {"type": "string", "description": "Feature type for feature_present (e.g. mounting_hole_pattern, fillet)."},
                        "size_mm": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "[x,y,z] expected size."},
                        "center_mm": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "[x,y,z] expected center."},
                        "tolerance_mm": {"type": "number", "description": "Size/position tolerance (default 1mm)."},
                        "count": {"type": "integer"},
                        "min": {"type": "integer"},
                        "max": {"type": "integer"},
                        "max_overlap_fraction": {"type": "number", "description": "no_deep_overlap threshold (default 0.5)."},
                    },
                    "additionalProperties": True,
                },
                "description": "Geometry targets to verify (the modeling brief's promises).",
            },
        },
        "additionalProperties": False,
        "description": (
            "Read-only deterministic geometry target validator (#291): check the exact "
            "geometric promises of a build against its topology + feature graph — named parts "
            "present, feature present, part count, overall/part bbox size + center within "
            "tolerance, no floating parts, no deep bbox overlap. Each target returns pass / "
            "fail / unknown (unknown = data absent, never guessed) with measured vs expected. "
            "Catches plausible-looking but mispositioned / over-modeled results. Bbox-level, "
            "not a GD&T solver; mutates nothing."
        ),
    },

    "cad.author_brief": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "request": {"type": "string", "description": "The user's modeling request (recorded verbatim)."},
            "units": {"type": "string", "description": "mm (default) / cm / inch / m."},
            "coordinate_convention": {"type": "string", "description": "e.g. z_up (default)."},
            "model_type": {
                "type": "string",
                "enum": ["single_part", "assembly", "product", "organic", "fixture"],
                "description": "Inferred from part count if omitted.",
            },
            "parts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                        "size_mm": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                        "key_dimensions": {"type": "object", "additionalProperties": True},
                    },
                    "additionalProperties": True,
                },
                "description": "Planned named parts; each yields a named_part_present (and part_size if size_mm) target.",
            },
            "overall_size_mm": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            "key_dimensions": {"type": "object", "additionalProperties": True},
            "tolerance_mm": {"type": "number", "description": "Default tolerance for derived size targets (1mm)."},
            "validation_targets": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
                "description": "Optional explicit targets (cad.validate_targets kinds), merged with the derived ones.",
            },
            "assumptions": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
        "description": (
            "Author the pre-code CAD brief + validation-target list (#290): declare units, "
            "model type, parts, and key dimensions BEFORE cad.execute_build123d. Stored as a "
            "project sidecar; auto-derives validation_targets (named_part_present, part_count, "
            "overall_size, part_size, no_floating_parts) that cad.validate_targets then checks "
            "the built model against — closing the plan→build→verify loop. A brief is a plan, "
            "not a guarantee."
        ),
    },
    "cad.get_brief": _project_id_schema(),
    "cad.diagnose": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "request_is_real_target": {
                "type": "boolean",
                "description": "Set true when the user asked for an accurate representation of a real product/character/vehicle (adds a risk trigger).",
            },
        },
        "additionalProperties": False,
        "description": (
            "Read-only diagnostic snapshot + repair verdict (#293): composes design_review "
            "(critique + symmetry + modeling fidelity + fix targets), structural geometry "
            "checks (no floating / no deep overlap), and the CAD brief's validation_targets "
            "into ONE snapshot with risk triggers, a ready / needs_repair verdict, and a "
            "prioritized repair_actions list. Repair-loop contract: for a high-risk build, fix "
            "every blocking issue and re-diagnose until 'ready' before presenting. Mutates nothing."
        ),
    },

    # ── CAD source readback (read-only) ──────────────────────────────────────
    "cad.get_source": _project_id_schema(),
    "cad.list_editable_parameters": _project_id_schema(),

    # ── Snapshots / undo (list read-only, restore approval-gated) ────────────
    "cad.list_snapshots": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "limit": {
                "type": "integer",
                "description": "Max snapshots to return, newest first (default 20).",
            },
        },
        "additionalProperties": True,
    },
    "cad.restore_snapshot": {
        "type": "object",
        "required": ["project_id", "snapshot_id"],
        "properties": {
            "project_id": {"type": "string"},
            "snapshot_id": {
                "type": "string",
                "description": "Snapshot to restore (e.g. 'snap_0003'), from cad.list_snapshots.",
            },
        },
        "additionalProperties": True,
    },
    "cad.get_named_part_bbox": {
        "type": "object",
        "required": ["project_id", "part_name"],
        "properties": {
            "project_id": {"type": "string"},
            "part_name": {
                "type": "string",
                "description": "Exact named-part label from geometry/topology_map.json, e.g. 'thigh_L'.",
            },
        },
        "additionalProperties": True,
    },
    "cad.insert_fasteners": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "hole_feature_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Explicit hole feature IDs to populate. Required unless auto_select_holes is true. "
                    "Each ID must match a feature in graph/feature_graph.json that carries hole_metadata."
                ),
            },
            "auto_select_holes": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true, select every feature in the feature graph that carries hole_metadata. "
                    "Equivalent to passing all such feature IDs in hole_feature_ids."
                ),
            },
            "material": {
                "type": "string",
                "default": "Steel-1045",
                "description": "Material name for inserted fasteners (default Steel-1045).",
            },
            "overwrite_existing": {
                "type": "boolean",
                "default": False,
                "description": "Allow re-inserting fasteners for holes that already have inserted features.",
            },
        },
        "additionalProperties": True,
    },
    "cad.refine": {
        "type": "object",
        "required": ["project_id", "feedback"],
        "properties": {
            "project_id": {"type": "string"},
            "feedback": {
                "type": "string",
                "description": "Natural-language change request, e.g. 'move thigh_L down by 20mm'.",
            },
            "write_files": {
                "type": "boolean",
                "description": "Write refined geometry/source/topology artifacts back into the package (default true).",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600,
                "description": "Subprocess timeout in seconds for the refined build123d execution (default 60).",
            },
        },
        "additionalProperties": True,
    },

    # ── CAD edit (approval-gated) ────────────────────────────────────────────
    "cad.edit_parameter": {
        "type": "object",
        "required": ["project_id", "featureId", "parameterName", "newValue"],
        "properties": {
            "project_id": {"type": "string"},
            "featureId": {
                "type": "string",
                "description": "Feature ID (matches @feature: pointers, e.g. 'feat_hole_pattern_001').",
            },
            "parameterName": {
                "type": "string",
                "description": "Parameter name on the feature, e.g. 'hole_diameter_mm'.",
            },
            "newValue": {
                "description": "Replacement value. Type follows the parameter's declared schema (number, string, bool).",
            },
            "confirmScopeRisk": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Required for global/unscoped parameters whose edit may ripple across "
                    "multiple parts or unbound geometry. Leave false for ordinary local edits."
                ),
            },
            "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
            "thumbnail": {
                "type": "boolean",
                "description": "Return a rendered PNG thumbnail. Defaults to true in full responses and false in compact responses.",
            },
            "response_detail": {
                "type": "string",
                "enum": ["full", "compact"],
                "description": "full (default) returns the full geometry_report; compact returns a one-line summary and omits the thumbnail unless thumbnail=true.",
            },
        },
        "additionalProperties": True,
    },
    "cad.remove_part": {
        "type": "object",
        "required": ["project_id", "label"],
        "properties": {
            "project_id": {"type": "string"},
            "label": {
                "type": "string",
                "description": "build123d .label of the named part to remove (e.g. 'chest_plate').",
            },
            "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
            "thumbnail": {
                "type": "boolean",
                "description": "Return a rendered PNG thumbnail. Defaults to true in full responses and false in compact responses.",
            },
            "response_detail": {
                "type": "string",
                "enum": ["full", "compact"],
                "description": "full (default) returns the full geometry_report; compact returns a one-line summary and omits the thumbnail unless thumbnail=true.",
            },
        },
        "additionalProperties": True,
    },
    "cad.replace_part": {
        "type": "object",
        "required": ["project_id", "label", "code"],
        "properties": {
            "project_id": {"type": "string"},
            "label": {
                "type": "string",
                "description": "build123d .label of the named part to replace (e.g. 'head').",
            },
            "code": {
                "type": "string",
                "description": (
                    "Replacement build123d code. Must reassign `result` to the new "
                    "part and set its .label (normally back to the same name). "
                    "Omit export calls. The high-level helpers (lofted_stack, capsule, "
                    "etc.) are available."
                ),
            },
            "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
            "thumbnail": {
                "type": "boolean",
                "description": "Return a rendered PNG thumbnail. Defaults to true in full responses and false in compact responses.",
            },
            "response_detail": {
                "type": "string",
                "enum": ["full", "compact"],
                "description": "full (default) returns the full geometry_report; compact returns a one-line summary and omits the thumbnail unless thumbnail=true.",
            },
        },
        "additionalProperties": True,
    },

    # ── CAE setup / solver pipeline ──────────────────────────────────────────
    "cae.apply_setup_patch": {
        "type": "object",
        "required": ["project_id"],
        "description": (
            "Apply incremental patches to the project's CAE setup artifacts "
            "(solver settings, materials, boundary conditions, loads, and the face-to-NSET mapping). "
            "Each patch specifies an action_type and a target path within the package. "
            "For a linear-static solve: create simulation/solver_settings.json "
            "({solver: CalculiX, analysis_type: linear_static}), set materials in "
            "simulation/cae_imports/parsed_materials.json, and write boundary conditions/loads "
            "to simulation/setup.yaml (preferred) or simulation/cae_imports/parsed_boundary_conditions.json "
            "and simulation/cae_imports/parsed_loads.json. Load/BC targets must be NSET names such as "
            "'FIXED_BASE' or 'LOAD_TOP', not @face: pointers. Bind those NSETs to actual faces by "
            "writing simulation/cae_mapping.json, then call cae.generate_solver_input."
        ),
        "properties": {
            "project_id": {"type": "string"},
            "patches": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "description": (
                        "A single patch operation. Required fields: "
                        "action_type (create_file|replace_json|merge_object|append_array_item), "
                        "path (allowed target file path in the package, e.g. "
                        "'simulation/cae_imports/parsed_materials.json'), and value "
                        "(payload, content, or value are accepted aliases: payload/content for "
                        "create_file; value/content/payload for JSON operations). "
                        "Example material patch: "
                        '{"action_type": "merge_object", '
                        '"path": "simulation/cae_imports/parsed_materials.json", '
                        '"content": {"materials": [{"name": "aluminum_6061", '
                        '"density_kg_m3": 2700, "youngs_modulus_pa": 69e9, '
                        '"poisson_ratio": 0.33, "yield_strength_pa": 276e6}]}}. '
                        "Example boundary-condition patch (target is an NSET name): "
                        '{"action_type": "create_file", '
                        '"path": "simulation/cae_imports/parsed_boundary_conditions.json", '
                        '"content": {"boundary_conditions": [{"id": "bc_001", "type": "fixed", '
                        '"target": "FIXED_BASE", "dof_start": 1, "dof_end": 3, "value": 0}]}}. '
                        "Example face-to-NSET mapping patch: "
                        '{"action_type": "create_file", '
                        '"path": "simulation/cae_mapping.json", '
                        '"content": {"schema_version": "0.1", "mappings": [{"cae_entity": "FIXED_BASE", '
                        '"maps_to": {"role": "fixed_support", "target_pointers": ["@face:face_003"]}, '
                        '"face_ids": ["face_003"]}]}}. '
                        "Use NSET names for load/BC targets; @face: pointers belong only inside cae_mapping.json."
                    ),
                },
                "description": (
                    "Preferred input. Array of CAE setup patch operations. "
                    "Either 'patches' or 'patch' must be provided."
                ),
            },
            "patch": {
                "type": "object",
                "description": (
                    "Legacy compatibility input. Either one CAE setup patch object, or "
                    "a map of operation-name keys to patch objects. Prefer 'patches'. "
                    "Either 'patches' or 'patch' must be provided."
                ),
            },
        },
        "additionalProperties": True,
    },
    "cae.prepare_solver_run": {
        "type": "object",
        "required": ["project_id"],
        "description": (
            "Inspect a .aieng package and return a reviewable solver-run preflight plan. "
            "Checks mesh, solver settings, load case, input deck, and CalculiX availability. "
            "No solver is executed. The response includes recommended_next_calls so an "
            "external agent knows exactly which cae.* tool to call next."
        ),
        "properties": {
            "project_id": {"type": "string"},
            "run_id": {"type": "string", "description": "Solver run id, default run_001."},
            "load_case_id": {"type": "string", "description": "Load case id, default load_case_001."},
            "input_deck_path": {"type": "string", "description": "Optional external input deck path."},
            "extract_results": {"type": "boolean", "description": "Plan computed_metrics extraction."},
            "refresh_summary": {"type": "boolean", "description": "Plan result/evidence summary refresh."},
            "solver": {"type": "string", "description": "Solver name, default CalculiX."},
        },
        "additionalProperties": True,
    },
    "cae.generate_solver_input": {
        "type": "object",
        "required": ["project_id"],
        "description": (
            "Generate a runnable CalculiX linear-static solver input deck from existing "
            ".aieng setup artifacts. Typical input: {project_id, run_id: 'run_001', overwrite: true}."
        ),
        "properties": {
            "project_id": {"type": "string"},
            "run_id": {"type": "string", "description": "Solver run id, default run_001."},
            "overwrite": {"type": "boolean", "description": "Overwrite an existing input deck."},
        },
        "additionalProperties": True,
    },
    "cae.run_solver": {
        "type": "object",
        "required": ["project_id", "input_deck_path"],
        "description": (
            "[APPROVAL REQUIRED] Execute an external CalculiX solver run on an existing input deck. "
            "Typical input: {project_id, run_id: 'run_001', load_case_id: 'load_case_001', "
            "input_deck_path: 'simulation/runs/run_001/solver_input.inp', timeout_seconds: 120}."
        ),
        "properties": {
            "project_id": {"type": "string"},
            "run_id": {"type": "string", "description": "Solver run id, default run_001."},
            "load_case_id": {"type": "string", "description": "Load case id, default load_case_001."},
            "input_deck_path": {"type": "string", "description": "Relative path to the .inp file inside the package."},
            "solver": {"type": "string", "description": "Solver name, default CalculiX."},
            "extract_results": {"type": "boolean", "description": "Extract metrics after a successful run."},
            "refresh_summary": {"type": "boolean", "description": "Refresh result summaries after a successful run."},
            "overwrite": {"type": "boolean", "description": "Overwrite existing run artifacts."},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "description": "Solver timeout in seconds."},
            "auto_import_evidence": {"type": "boolean", "description": "Import .dat evidence after a successful run."},
        },
        "additionalProperties": True,
    },
    "cae.generate_mesh": {
        "type": "object",
        "required": ["project_id"],
        "description": (
            "Mesh the project's STEP geometry in-process with Gmsh and persist the FE mesh "
            "(simulation/mesh/mesh.inp + simulation/mesh/mesh_metadata.json) into the .aieng package. "
            "Produces only the mesh — does not bind loads/BCs, assemble a solver deck, or run a solver. "
            "Typical input: {project_id, mesh_size_mm: 2.5}."
        ),
        "properties": {
            "project_id": {"type": "string"},
            "mesh_size_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Target element size (mm). Defaults to the package's configured size, else 2.5 mm.",
            },
        },
        "additionalProperties": True,
    },
    "cae.write_mesh_handoff": {
        "type": "object",
        "required": ["project_id"],
        "description": (
            "Write a mesh handoff contract (simulation/mesh_handoff_contract.json) into a .aieng package. "
            "Typical input: {project_id, handoff_id: 'mesh_handoff_001', overwrite: false}."
        ),
        "properties": {
            "project_id": {"type": "string"},
            "handoff_id": {"type": "string", "description": "Handoff identifier, default mesh_handoff_001."},
            "overwrite": {"type": "boolean", "description": "Overwrite an existing handoff contract."},
        },
        "additionalProperties": True,
    },
    "cae.import_solver_evidence": {
        "type": "object",
        "required": ["project_id", "result_file"],
        "description": (
            "Import an external solver result file as evidence into a .aieng package. "
            "Scans the file for known numeric observations and appends them to results/evidence_index.json. "
            "Does not auto-advance claim status."
        ),
        "properties": {
            "project_id": {"type": "string"},
            "result_file": {"type": "string", "description": "Absolute path to the external solver result file."},
            "result_format": {"type": "string", "description": "Result format, default calculix_dat."},
            "producer_tool": {"type": "string", "description": "Tool that produced the result, default calculix."},
            "claim_support": {"type": "array", "items": {"type": "string"}, "description": "Claim IDs this evidence may support."},
            "verification_status": {"type": "string", "description": "Evidence verification status, default unverified."},
            "evidence_id": {"type": "string", "description": "Optional explicit evidence id."},
            "auto_scaffold": {"type": "boolean", "description": "Auto-create evidence scaffold if missing."},
        },
        "additionalProperties": True,
    },
    "cae.extract_solver_results": {
        "type": "object",
        "required": ["project_id"],
        "description": (
            "Parse a CalculiX FRD result file and write computed_metrics.json into a .aieng package. "
            "Pass package_path/project_id and frd_path."
        ),
        "properties": {
            "project_id": {"type": "string"},
            "frd_path": {"type": "string", "description": "Absolute path to the CalculiX .frd file."},
            "load_case_id": {"type": "string", "description": "Load case id, default load_case_001."},
            "software": {"type": "string", "description": "Solver software, default CalculiX."},
            "overwrite": {"type": "boolean", "description": "Overwrite existing computed_metrics.json."},
            "refresh_result_summary": {"type": "boolean", "description": "Refresh the human-readable result summary."},
        },
        "additionalProperties": True,
    },
    "cae.extract_field_regions": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "field": {
                "type": "string",
                "enum": ["stress", "displacement"],
                "description": "Field to cluster (default 'stress').",
            },
            "max_clusters": {"type": "integer", "minimum": 1, "maximum": 64},
        },
        "additionalProperties": True,
    },

    # ── post-processing ──────────────────────────────────────────────────────
    "postprocess.generate_computed_metrics": {
        "type": "object",
        "required": ["project_id", "inputPath"],
        "properties": {
            "project_id": {"type": "string"},
            "inputPath": {
                "type": "string",
                "description": "Absolute path to a CSV or JSON file with the computed metrics.",
            },
            "loadCaseId": {"type": "string"},
            "software": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "postprocess.refresh_cae_summary": _project_id_schema(),

    # ── preview / runtime introspection ──────────────────────────────────────
    "aieng.generate_preview": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "format": {"type": "string", "enum": ["glb", "stl"]},
        },
        "additionalProperties": True,
    },
    "aieng.refresh_semantics": _project_id_schema(),

    # ── materials ─────────────────────────────────────────────────────────────
    "list_materials": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Filter by material category, e.g. 'Aluminum Alloy', 'Stainless Steel', 'Engineering Plastic'.",
            },
            "query": {
                "type": "string",
                "description": "Search query matched against material names and descriptions (case-insensitive).",
            },
        },
        "additionalProperties": False,
        "description": "List available engineering materials with optional category or search filter.",
    },
    "get_material_details": {
        "type": "object",
        "required": ["material_name"],
        "properties": {
            "material_name": {
                "type": "string",
                "description": "Exact material name, e.g. 'Al6061-T6' or 'Steel-316L'.",
            },
        },
        "additionalProperties": False,
        "description": "Return full properties for a specific material (E, nu, density, yield, ultimate, thermal expansion).",
    },
    "compare_materials": {
        "type": "object",
        "required": ["material_names"],
        "properties": {
            "material_names": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "description": "List of material names to compare, e.g. ['Al6061-T6', 'Steel-316L', 'Ti-6Al-4V'].",
            },
        },
        "additionalProperties": False,
        "description": "Compare properties of two or more materials side by side with normalized scores.",
    },

    # ── standard parts ────────────────────────────────────────────────────────
    "list_standard_parts": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["fastener", "bearing", "shaft", "structural_profile", "hole"],
                "description": "Filter by standard part category.",
            },
        },
        "additionalProperties": False,
        "description": "List available standard part categories and types (fasteners, bearings, shafts, profiles, holes).",
    },
    "get_standard_part_specs": {
        "type": "object",
        "required": ["part_type"],
        "properties": {
            "part_type": {
                "type": "string",
                "description": "Standard part type identifier, e.g. 'hex_bolt', 'deep_groove_ball_bearing', 'i_beam_profile'.",
            },
            "preset_name": {
                "type": "string",
                "description": "Optional preset name (e.g. 'M8', '6204') to return preset parameters.",
            },
        },
        "additionalProperties": False,
        "description": "Return Shape IR spec and available presets for a standard part type.",
    },
    "insert_standard_part": {
        "type": "object",
        "required": ["project_id", "part_type", "parameters"],
        "properties": {
            "project_id": {"type": "string"},
            "part_type": {
                "type": "string",
                "description": "Standard part type, e.g. 'hex_bolt', 'socket_head_cap_screw', 'deep_groove_ball_bearing'.",
            },
            "parameters": {
                "type": "object",
                "description": "Part-specific parameters dict. Use get_standard_part_specs to discover editable parameters.",
            },
            "position": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Optional [x, y, z] translation applied to the generated Shape IR node.",
            },
            "orientation": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Optional [rx, ry, rz] rotation in degrees applied to the generated Shape IR node.",
            },
            "part_name": {
                "type": "string",
                "description": "Optional human-readable name for the inserted part (sets node id and name).",
            },
            "preset_name": {
                "type": "string",
                "description": "Optional preset to use as base parameters (e.g. 'M8', '6204'). Caller parameters override preset values.",
            },
        },
        "additionalProperties": True,
        "description": "Insert a standard part into the current project as Shape IR. Recompiles the package on success.",
    },
    "set_part_material": {
        "type": "object",
        "required": ["project_id", "part_name", "material_name"],
        "properties": {
            "project_id": {"type": "string"},
            "part_name": {
                "type": "string",
                "description": "Exact named-part label from the feature graph / topology map.",
            },
            "material_name": {
                "type": "string",
                "description": "Material name, e.g. 'Al6061-T6'. Must be a known material from list_materials.",
            },
            "override_properties": {
                "type": "object",
                "description": "Optional dict of property overrides to store alongside the material assignment.",
            },
        },
        "additionalProperties": False,
        "description": "Assign a material to a named part in the current project. Updates graph/feature_graph.json.",
    },
    "generate_bom": {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "format": {
                "type": "string",
                "enum": ["json", "markdown", "csv", "xlsx"],
                "description": "Output format. Structured `items` are always returned; markdown adds a table string, csv adds an ERP-style line-item CSV (result.csv), json adds a serialized ERP line-item document (result.json), and xlsx adds a base64-encoded minimal workbook (result.xlsx_base64).",
            },
        },
        "additionalProperties": False,
        "description": "Generate a Bill of Materials from the current project parts, including standard parts and quantities.",
    },
}


def get_schema(tool_name: str) -> dict[str, Any] | None:
    """Lookup helper; returns None if no curated schema exists for the tool."""
    return TOOL_SCHEMAS.get(tool_name)
