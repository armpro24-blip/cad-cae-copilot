> **Experimental pre-release.** This is the first published release of the
> AIENG Workbench line. `v0.1.0-alpha.1` / `v0.1.0-alpha.2` were internal
> drafts that were never tagged or published anywhere; the version history
> starts here in public. The Python packages carry version `0.1.0a2`.

## What this is

An **agent-driven CAD/CAE workbench**: you connect an MCP-capable coding agent
(Claude Code, Codex, Cursor, …) and drive real 3D CAD modeling, static
structural FEA, and sizing optimization by prompt — the workbench enforces its
boundaries at the tool layer (modeling-plan confirmation, approval gates on
solver runs and destructive operations) instead of asking you to learn a
command vocabulary.

Two Python packages plus a Docker image:

| Artifact | Where | What it contains |
|---|---|---|
| `ghcr.io/armpro24-blip/aieng-workbench:latest` | **GHCR (published)** | All-in-one image (backend + built web workbench + CalculiX); every published tag is smoke-validated on `main` |
| `aieng-format` `0.1.0a2` | this repo (`aieng/`); PyPI publication planned | The `.aieng` package format library: Shape IR, schemas, validation, evidence/credibility model, CLI |
| `aieng-workbench-mcp` `0.1.0a2` | this repo (`aieng-ui/backend/`); PyPI publication planned | The MCP server + FastAPI backend: CAD/CAE/optimization tools, approval gating, web viewer API |

## Capabilities (evidence-backed)

- **Real CAD, no API key** — `cad.execute_build123d` runs your build123d code
  against the OpenCASCADE kernel and produces actual STEP/STL/GLB geometry,
  with named parts, colors, a 4-view contact-sheet thumbnail, incremental
  `append`/`replace_part`/`remove_part` editing, and a deterministic
  quantitative geometry report (proportions, symmetry, floating parts).
- **Fast parametric edits** — `cad.edit_parameter` replaces a named constant
  and re-executes: sub-second to seconds, no LLM, with a `regression_diff`
  verdict (clean / collateral / topology-changed) on every edit.
- **Static structural FEA (CalculiX)** — setup patch → preflight → deck
  generation → approval-gated solve → result extraction → field regions.
  Meshes default to **quadratic tetrahedra (C3D10)**; every mesh carries a
  measured `accuracy` band, and a completed run on an unreliable mesh is
  **downgraded, not presented as a result** (verified against beam theory:
  linear-tet default was ~2× off on stress; quadratic lands within ~3%).
  Modal, buckling, and steady-state thermal analysis types exist with
  explicit linear-analysis honesty boundaries.
- **Mesh convergence** — `cae.mesh_convergence` runs a GCI (Richardson) study
  with per-metric apparent order and verdicts.
- **Sizing optimization with the real solver in the loop** —
  `opt.sizing_sweep` (one parameter) and `opt.doe_sizing_study`
  (multi-parameter, full-factorial/LHS) solve each variant with real static
  FEA and rank honestly; failed variants are reported, never recommended.
- **Topology optimization** — built-in SIMP (2D default, experimental 3D)
  with honest coarse-limitations recording and Shape-IR writeback.
- **Binding durability** — CAE loads/constraints bind to `@face:` pointers
  that now **survive dimensional edits, hole cuts, and unrelated part
  replacements** (stable face identity + evidence-based re-verification);
  a genuinely ambiguous change (e.g. a face split in two) refuses honestly
  instead of guessing.
- **Credibility tiers on every result-bearing output** —
  `critique_finding < surrogate_prediction < proxy_assembly_result <
  executed_solver_result`, with automatic downgrade when evidence does not
  support the claim.

## Install

**Docker all-in-one** (recommended — published on GHCR, bundles the full CAD
stack and CalculiX):

```bash
docker pull ghcr.io/armpro24-blip/aieng-workbench:latest
docker run --rm -it -p 8000:8000 -p 8765:8765 -v aieng-data:/data \
  ghcr.io/armpro24-blip/aieng-workbench:latest
```

**MCP server without cloning**, straight from this repository (Python 3.11+):

```bash
uvx \
  --from "aieng-workbench-mcp[full] @ git+https://github.com/armpro24-blip/cad-cae-copilot.git@v0.1.0-alpha.3#subdirectory=aieng-ui/backend" \
  --with "aieng-format @ git+https://github.com/armpro24-blip/cad-cae-copilot.git@v0.1.0-alpha.3#subdirectory=aieng" \
  aieng-workbench-mcp \
  --approval-mode client \
  --data-dir ~/.aieng-workbench
```

PyPI publication of `aieng-format` / `aieng-workbench-mcp` is planned; the
`pip install --pre` path activates once the packages are on the index (the
release workflow that publishes them is already in the repository,
owner-gated).

Full wiring for Claude Code / VS Code / Codex: `aieng-ui/backend/MCP_SETUP.md`.
Prompt phrasing that works: `docs/prompt-guide.md`.

The optional real-CAD/FEA stack (build123d/OCP, gmsh, CalculiX) is heavy; the
Docker image bundles all of it, while the Python path runs with honest
degradation (stubbed CAD smoke, preflight reports the missing solver) until
you install the extras.

## Honesty boundary (read before trusting any output)

`.aieng` records evidence and context; it does **not** certify engineering
correctness and does **not** advance engineering claims automatically. The
workbench is **not production-certified CAD/CAE software**; every
result-bearing output carries a credibility stamp and `production_ready:
false` unless explicitly certified by a human. Solver claims are only true
when `cae.run_solver` actually executed and result artifacts exist. Assembly
connections are proxies (no contact physics, no bolt preload). No
stability/semver guarantee at alpha.

## Verification at tag time

- Backend suite: **1701 passed, 0 failed** (Windows, full run; ubuntu CI green).
- `aieng` core suite: ~3068 passed (full-suite CI).
- Packaging smokes (installed wheel/sdist for both packages, clean venv): green
  locally on Windows and in CI on ubuntu.
- Real-ccx verification gate (NAFEMS cases + CAD→mesh→deck→ccx→FRD
  integration): green in CI.
- Canonical value demo (50 N cantilever: CAD → CAE → sizing): reproducible via
  `aieng.value_demo_check`, with mesh-convergence step (GCI 1.31%,
  extrapolated tip deflection at 99.2% of beam theory).

## Known limitations

- Real-geometry work needs the optional CAD stack (build123d/OCP); the pip
  package without extras runs the stubbed smoke path only.
- Assembly CAE is a simplified proxy model (v0): no real contact, no
  preload, solver deck generation best-effort.
- Mesh-to-CAD reconstruction (topology-optimization writeback) is lossy and
  explicitly not production CAD.
- No parametric history / constraint solver — edits are source-level
  (named-constant substitution), which is a deliberate alpha scope choice.
