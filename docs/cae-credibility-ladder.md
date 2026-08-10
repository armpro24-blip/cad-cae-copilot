# CAE Credibility Ladder

AIENG separates "a file exists" from "a solver ran" from "an engineer can rely
on this result for a reviewed claim." This ladder is review guidance only; it is
not certification or production sign-off.

## Ordered Levels

| Level | Meaning | Typical next evidence |
|---|---|---|
| `no_result_artifact` | No solver/result artifact is present. | Create or import result artifacts. |
| `artifact_present` | A result-like file exists, but solver completion is not established. | Solver run record. |
| `solver_completed` | The solver run record indicates completion. | Parsed numerical metrics. |
| `numerical_result_parsed` | Computed metrics or extracted result fields exist. | Plausibility checks. |
| `plausibility_checked` | Units, signs, magnitudes, and expected field locations were checked. | Design-target comparison. |
| `design_target_compared` | Metrics were compared to explicit design targets. | Benchmark or calibration match. |
| `benchmark_calibrated` | A known analytical/reference case or calibration pack agrees within documented tolerance. | Human review. |
| `human_review_supported` | A reviewer supports a claim using the evidence chain. | External release/sign-off process. |

The helper `app.cae_credibility.assess_cae_credibility` implements this first
version as a pure function. It never mutates packages and always returns
`certified: false`.

## Mesh and Calibration Discipline

Mesh quality or convergence evidence changes the language. Unknown mesh quality
keeps a limitation on the result. Failed or not-converged mesh evidence caps the
result before benchmark-calibrated credit, even if a numerical metric exists.

The enforcement is stronger than a caveat: meshes default to quadratic tets
(C3D10 — linear tets measured ~2× non-conservative in bending during
calibration), every mesh carries a measured `accuracy` band
(reliable / marginal / unreliable), and a completed solver run on an
`unreliable` mesh is knocked out of `executed_solver_result` entirely into a
dedicated `unreliable_mesh` claim tier (`cae_result_summary.py` /
`classify_credibility`). A `cae.mesh_convergence` GCI study is the real answer
to discretization uncertainty; the accuracy band is only a screen.

Analytical FEA benchmark scorecards can raise a result to
`benchmark_calibrated` only when the comparison passes within documented
tolerance. The analytical corpus is NAFEMS-style and ASME V&V-10 inspired, but
it is not an official certification suite.

## Honesty Boundaries

- Solver completion is not physical correctness.
- Parsed metrics are not design-target satisfaction.
- Design-target satisfaction is not claim approval.
- Benchmark agreement is regression evidence, not production safety.
- Human review can support a claim, but AIENG still does not certify the design.

Useful regression commands:

```bash
python -m pytest aieng-ui/backend/tests/test_cae_credibility.py -q
python -m pytest aieng/tests/test_benchmarks_analytical_fea.py -q
```
