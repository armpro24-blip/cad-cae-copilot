# Packaging and reproducibility audit

## Executive assessment

**PyPI readiness: not ready for a clean alpha release without follow-up packaging work.**

The repo tests are strong in a source checkout, but installed-wheel/sdist behavior is not demonstrated by this pass and there are concrete reasons to expect breakage.

## What was inspected

- `pyproject.toml`
- package layout under `src/aieng/`
- repo-root `schemas/`
- entrypoint declaration
- optional dependencies and extras
- test configuration (`pythonpath = ["src", "."]`)
- runtime code paths that resolve repo-root resources

## Release risks

### 1. Installed wheel likely misses required schema files

This is the largest packaging blocker.

Observed facts:
- `pyproject.toml` wheel target only declares `packages = ["src/aieng"]`.
- runtime code resolves schemas from repo-root paths such as:
  - `src/aieng/validate.py` -> `Path(__file__).resolve().parents[2] / "schemas" / ...`
  - `src/aieng/definition.py` -> repo-root `schemas/model_definition.schema.json`
  - `src/aieng/modeling_plan/validate.py` -> repo-root `schemas/modeling_plan.schema.json`
- `schemas/` lives outside `src/aieng/`.

Risk:
- An installed wheel may not contain the required schema files at those locations.
- Source-checkout tests can still pass because pytest adds `.` to `pythonpath` and the repo-root assets exist locally.

Recommended fix:
- Ship schemas as package data under `src/aieng/...` or explicitly include root-level schema assets in wheel/sdist builds.
- Add an installed-wheel smoke test that exercises `aieng.validate` and `aieng.definition` from a clean venv.

### 2. Alpha tag/version metadata drift

Observed facts:
- intended release tag: `v0.1.0-alpha.1`
- package metadata version: `0.1.0`
- `aieng.__version__`: `0.1.0`
- classifier: `Development Status :: 4 - Beta`
- README badge: `status-beta`

Risk:
- The published artifact would look like a stable/final `0.1.0` while the repo is trying to communicate an alpha release.

Recommended fix:
- Align package versioning and release metadata before tagging.
- If using Python package prerelease semantics, use a canonical prerelease version (for example `0.1.0a1`) for the distribution while keeping format-version decisions explicit.

### 3. Tests mostly validate source-tree behavior, not install behavior

Observed facts:
- pytest config injects both `src` and `.` into `pythonpath`.
- many tests read schemas via relative repo-root paths.

Risk:
- Passing tests do not prove wheel/sdist reproducibility.

Recommended fix:
- Add CI jobs for:
  - `python -m build`
  - fresh-venv install of wheel
  - smoke tests against installed artifact only

### 4. Optional extras are not reproducibility-oriented

Observed facts:
- `benchmark` extra includes unbounded `anthropic`, `openai`, and `inspect-ai>=0.3.220`
- `mcp` extra includes `mcp>=1.0,<3`
- `geometry = []` is an empty placeholder extra

Risk:
- Optional environments are not locked and may drift significantly across time.
- `geometry` may imply a supported install path that does not actually install anything.

Recommended fix:
- Keep extras, but document them as best-effort/experimental.
- Consider pin ranges or constraints files for benchmark reproducibility.
- Remove or document the empty `geometry` extra more clearly.

### 5. Distribution name vs import name requires explicit explanation

Observed facts:
- project name: `aieng-format`
- import package: `aieng`

Risk:
- This is legal, but can confuse evaluators and PyPI users.

Recommended fix:
- Document `pip install aieng-format` / `import aieng` explicitly if this package is published.

## Packaging inconsistencies

- Release target says alpha; metadata and badges say beta/final-ish `0.1.0`.
- Core semantics docs say no claim maps; repo still ships claim-map schemas and writeback helpers.
- Packaging scope focuses on `src/aieng`, while runtime/test/doc flows still depend on repo-root assets.

## Python compatibility

Declared:
- `requires-python = ">=3.11"`
- classifiers for Python 3.11 and 3.12

Observed in this pass:
- tested on Python `3.11.5`

Assessment:
- Python floor is clear.
- This pass did not independently verify 3.12.

## Wheel/sdist readiness

### Wheel

Current assessment: **at risk** because of external schema/resource dependencies.

### sdist

Current assessment: **uncertain**. An sdist may include repo-root files more naturally than a wheel, but this was not validated in the current pass.

## Recommended fixes before release

1. Include required schemas/resources in the installable artifact.
2. Add installed-artifact smoke tests.
3. Align package version/classifier/badges with the intended alpha tag.
4. Clarify which extras are experimental and which are expected to work reproducibly.
5. Consider a minimal release checklist item for `python -m build` plus clean-venv import/CLI checks.

## Conservative conclusion

The source tree is testable and reasonably reproducible **as a checkout**. The installable-package story is not yet strong enough to call PyPI-ready for `v0.1.0-alpha.1`.
