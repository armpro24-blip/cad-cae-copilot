# v0.1.0-alpha.1

## Summary

`v0.1.0-alpha.1` is the first alpha tag for `.aieng` as an auditable CAD/CAE
context package format. It is a release of package semantics, evidence
discipline, provenance, freshness tracking, and review readiness — not of
CAD automation, solver authority, or engineering validation.

This release is experimental. Treat its outputs as review material that
requires human engineering judgment.

## What is included

- Pure helpers for artifact manifests, evidence references, package
  consistency diagnostics, review readiness, claim proposals, audit events,
  revalidation status, support packets, and AI-readable summaries.
- A CLI (`aieng`) for ingesting CAD/CAE artifacts into `.aieng` packages and
  for evidence-only writebacks. Evidence imports do not advance claim state.
- JSON Schemas for every structured package resource, shipped as the
  `aieng.schemas` subpackage and loaded via `importlib.resources` so an
  installed wheel works outside the source tree.
- An MCP server (`aieng serve`) exposing read/inspect tools to agent clients.
- A focused-core release test suite covering core semantics modules, with
  golden examples under `tests/golden/`.

## Explicit non-goals

This release does **not** provide and does **not** claim to provide:

- engineering certification,
- automatic engineering validation,
- solver authority,
- autonomous engineering decisions,
- automatic claim advancement,
- claim acceptance/rejection workflows,
- claim maps as part of the intended alpha contract,
- a CAD kernel,
- a solver replacement,
- a guarantee that STEP conversion captures design intent.

## Safety / review semantics

The intended safety posture is conservative:

- `claim_advancement: "none"` is the default invariant.
- Evidence is not claim.
- Proposal is not acceptance.
- Freshness is not validation.
- Diagnostics are not certification.
- Tool execution must not silently advance engineering claims.

Where the package records solver outputs, topology, or imported artifacts,
those records are there to support human review, not to assert engineering
correctness by themselves.

## Installation

`v0.1.0-alpha.1` is a pre-release. Pip will not install it by default unless
you opt in.

```bash
pip install --pre aieng-format==0.1.0a1
# or, from the source tree:
pip install -e .
```

Python 3.11 or newer is required.

## Verification

After installing the wheel, the following should all succeed:

```bash
python -c "import aieng; print(aieng.FORMAT_VERSION)"
python -c "from importlib.resources import files; print(files('aieng.schemas').joinpath('manifest.schema.json').is_file())"
aieng --help
aieng init --model-id smoke_001 --out smoke.aieng
aieng validate smoke.aieng
```

The CLI should list the alpha command set and must not contain an
`update-claim` subcommand. `aieng validate` on a newly initialised package
should exit 0 with a mix of `PASS` and `WARN` messages.

Repository test status at tag time:

- `python -m pytest -q`: `1860 passed, 14 skipped, 10 warnings`.
- Installed-wheel smoke test (`tests/smoke/test_installed_wheel.py`): 4/4 passed.

## Known warnings

- A handful of internal modules still read `results/claim_map.json` when it
  is present in a legacy package. These code paths are quarantined: no alpha
  CLI or MCP surface exposes them, and no evidence import advances claim
  state. They will be retired in a follow-up alpha.
- The package ships `aieng/src/aieng/schemas/claim_map.schema.json` for backwards
  compatibility with packages already containing a `claim_map.json`. It is
  not promoted as part of the alpha contract.
- Benchmark fixtures under `benchmark_runs/` still contain
  `results/claim_map.json` files. They are evaluator fixtures and are
  outside the release contract.
- Real-geometry STEP extraction via OCP/CadQuery is experimental, platform
  dependent, and review-required. The default backend is the mock backend.
- `pytest -q` emits 10 zipfile duplicate-name `UserWarning`s from tests that
  intentionally rewrite ZIP members. Cosmetic; no test failures.

## Next cleanup targets

The following are explicitly out of scope for this tag and are tracked for a
follow-up alpha:

- Retire dormant claim-map reads in `completeness_writer.py`,
  `evidence_report_writer.py`, `validate.py`,
  `simulation/mesh_handoff_writer.py`, and `ai/summary_writer.py`.
- Move `claim_map.schema.json` out of the packaged schemas once the dormant
  reads are gone.
- Clean claim-map references from `benchmark_runs/` fixtures or move them
  behind an explicit legacy-compat marker.
- Tighten the public API surface around the pure semantics modules and add
  a stability table to `docs/package-semantics.md`.
- Eliminate the zipfile duplicate-name warnings by rewriting ZIPs atomically
  in the affected tests.

---

`.aieng` is an auditable engineering context format. It is not an engineering
authority. Outputs from this release are review material.
