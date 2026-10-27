# Analytical FEA benchmark corpus

This directory is the seed of the quantitative benchmark corpus requested in
issue #257. It contains a curated set of closed-form / analytical structural
reference cases plus a machine-readable `reference.json` per case.

## Scope

| Layer | Content |
|-------|---------|
| `fea_accuracy` | Linear-static and linear-eigenvalue cases (beam, rod, plate, column) with analytical reference answers |
| `mesh_convergence_quality` | Optional multi-refinement studies that feed the ASME V&V-20 / Roache GCI analyzer |

The cases are **not** official NAFEMS certification benchmarks and are **not**
ASME V&V-10 certified. The only claim made is:

> Computed results agree with each gating metric's documented reference value
within its documented tolerance band.

Most references are closed-form; a few gating metrics are real-ccx regression
anchors instead, and their `note` says so (the fixed-fixed beams' maximum
displacement, where Euler-Bernoulli gives the neutral-axis value and the
metric is the surface maximum). A metric with `"gate": false` is reported for evidence but does not decide the
case verdict. On these coarse C3D8 fixtures every peak von Mises stress is
non-gating: a fully clamped end puts a triaxial concentration at the support
(tension rod ~2x the nominal stress) and coarse bending under-predicts the root
stress by 10-20%. The `reference.json` metrics are copied verbatim from
`aieng.nafems_verification.REFERENCE_CASES`, and a test compares every field.

## Layout

```text
analytical_fea/
├── corpus.json              # manifest: case list + analysis type + reference paths
├── README.md                # this file
├── build_packages.py        # helper: build runnable .aieng packages from the fixtures
└── <case_id>/
    └── reference.json       # ground-truth answer + tolerance for the case
```

## Cases

| Case ID | Analysis | Reference physics |
|---------|----------|-------------------|
| `tension_rod` | static | axial stress / displacement of a square rod |
| `cantilever_end_load` | static | end-loaded cantilever (strong axis) |
| `cantilever_udl` | static | uniformly distributed load on a cantilever |
| `fixed_fixed_udl` | static | fixed-fixed beam under UDL |
| `fixed_fixed_center_load` | static | fixed-fixed beam with center point load |

## Provenance / license

All geometry, loads, and reference values are authored from first principles
(beam/rod/column theory). No external dataset is vendored. Any future external
benchmarks added here must record their license / source in the case
`reference.json` under `provenance`.

## Running the harness

```bash
# Build the runnable .aieng packages
python benchmarks/datasets/analytical_fea/build_packages.py --out-dir build/analytical_fea

# Run the scorer (from the aieng directory)
cd aieng
python -m aieng.benchmarks.analytical_fea \
    --packages-dir build/analytical_fea \
    --out analytical_fea_scorecard.json
```

The scorer produces a machine-readable scorecard (`aieng.benchmark.analytical_fea.scorecard`)
with per-case verdict, deviation percent, and tolerance. It is designed to be
consumed by CI and by the regression runner from issue #237.
