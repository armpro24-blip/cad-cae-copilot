# `.aieng` — AI-Readable Engineering Context Packages

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)]()
[![Tests](https://img.shields.io/badge/tests-focused%20suites%20tracked-yellow.svg)]()

> **Better CAD agents start with better engineering context.**
>
> `.aieng` is an auditable engineering context package for AI-assisted CAD/CAE workflows. It records what is present, inferred, missing, unsupported, uncertain, stale, and where evidence came from.

**Benchmark highlight:** On four CAE reasoning scenarios, structured `.aieng` access made engineering context cheaper to use and easier to audit. In one missing-setup audit task, Kimi accuracy improved from 0.45 with raw dumps to 0.95 with structured access (n=10, T=0); on the other three scenarios, benchmark answer correctness (under the task rubric) stayed at ceiling while structured access reduced token cost on larger packages. See [`Benchmarks`](#benchmarks) for the full table and limits.

**Keywords:** CAD · CAE · FEA · STEP · CalculiX · FreeCAD · LLM · agent · MCP · evidence layer · AI-readable engineering · AGI · design automation

## What `.aieng` is (and is not)

**`.aieng` is an auditable engineering context package format for AI-assisted CAD/CAE workflows.**

| What `.aieng` does | What `.aieng` does NOT do |
|--------------------|--------------------------|
| Packages CAD/CAE artifacts with evidence, provenance, freshness, diagnostics, and claim-safety semantics | Automate CAD operations |
| Records geometry, topology, features, parameters, and provenance | Automate CAE workflows |
| Explicitly records missing, unsupported, and uncertain information | Run solvers |
| Provides AI-readable structured resources for general AI reasoning | Generate meshes |
| Tracks what external tools need to do and what they produced | Optimize designs |
| Keeps evidence, proposals, diagnostics, and claim advancement separate (`claim_advancement: "none"`) | Make engineering decisions |

External CAD/CAE tools remain responsible for exact geometry editing, meshing, solving, manufacturing checks, and export. Converters and adapters are ingestion paths into `.aieng`; the higher-value layer is evidence, provenance, freshness, diagnostics, and claim-safety semantics. `.aieng` makes engineering state legible to humans and agents, but it does not replace CAD/CAE execution and does not establish engineering validity or certification status. This is an experimental alpha release; treat outputs as review material requiring human engineering judgment.

See [`docs/public-positioning.md`](docs/public-positioning.md) for outward-facing product messaging.

## Project Thesis

Most current AI-for-CAD/CAE approaches adapt AI to CAD/CAE through specialized training, RAG, MCP tools, plugins, workflow agents, or skills. `.aieng` focuses on adapting CAD/CAE context to AI review by making package evidence, provenance, freshness, and limitations explicit.

`.aieng` is a **self-describing engineering context package for general AI review**. It should carry enough geometry references, feature semantics, constraints, simulation context, freshness state, visual mappings, allowed operations, traceable IDs, evidence references, and assumptions for a general AI to inspect the model context before calling external tools.

Context should come from the package. Execution and engineering validation still use external CAD kernels, CAE preprocessors, meshers, solvers, manufacturing checkers, and human review.

## Quick Start

> **Not on PyPI, by decision.** `aieng-format` is distributed from this
> repository (source checkout, a release tag via `pip`/`uvx`, or the GHCR
> image) — publishing to PyPI is not planned. The name is unregistered there,
> so do **not** run `pip install aieng-format`: it would resolve to whatever
> someone else uploads under that name, not to this project.

```bash
# Install from a source checkout, editable
cd aieng
pip install -e .

# …or from a release tag, without cloning
pip install "aieng-format @ git+https://github.com/armpro24-blip/cad-cae-copilot.git@v0.1.0-alpha.4#subdirectory=aieng"

# Create a package from a STEP file
aieng import-step examples/bracket.step --out build/bracket_001.aieng

# Extract topology and recognize features
aieng extract-topology build/bracket_001.aieng
aieng recognize-features build/bracket_001.aieng

# Apply engineering context
aieng apply-context build/bracket_001.aieng --context examples/bracket_user_context.yaml

# Generate AI-readable summaries and patch proposals
aieng summarize build/bracket_001.aieng
aieng propose-patch build/bracket_001.aieng --intent "Reduce mass by 15% while keeping mounting holes unchanged."

# Validate the package
aieng validate build/bracket_001.aieng
```

For the full vertical CAE demo (including solver deck export, evidence recording, and workbench visualization), see [`../aieng-ui/docs/quickstart-vertical-cae-demo.md`](../aieng-ui/docs/quickstart-vertical-cae-demo.md).

For real-geometry extraction with CadQuery/OCP:

```bash
pip install cadquery
aieng import-step path/to/real_model.step --out build/real_model.aieng
aieng extract-topology build/real_model.aieng --backend occ
```

## 10-minute evaluator path

1. Read [`docs/package-semantics.md`](docs/package-semantics.md) for the core evidence/claim/freshness contract.
2. Run the in-memory cookbook: `python examples/package_semantics_cookbook.py`.
3. Inspect the golden examples in [`tests/golden/`](tests/golden/).
4. Run the focused core checks from [`docs/release-v0.1-alpha-checklist.md`](docs/release-v0.1-alpha-checklist.md).
5. Optionally inspect `aieng-ui` as the reference runtime/workbench; it delegates package semantics back to this repo.

## Architecture

The `.aieng` workspace consists of three repositories:

| Repo | Role |
|------|------|
| **`aieng`** (this repo) | Core package semantics and CLI. Owns stable-ish pure semantics for manifests, evidence resolution, consistency diagnostics, proposals, audit events, revalidation state, support packets, summaries, and schema-like helpers. |
| **`aieng-ui`** | Reference runtime/workbench. Owns HTTP APIs, project/ZIP I/O, tool execution, orchestration, and visualization; delegates package semantics to `aieng` core. |
| **`aieng_freecad_mcp`** | MCP adapter. Thin HTTP wrappers exposing `aieng` capabilities to MCP clients (e.g. Claude Desktop, FreeCAD). |

For system-wide architecture, see [`../docs/system_architecture.md`](../docs/system_architecture.md). For repo boundaries, see [`../docs/repo_boundaries.md`](../docs/repo_boundaries.md).

## Features

- **Package semantics** - pure helpers for artifact manifests, evidence references, consistency diagnostics, review readiness, claim proposals, audit events, revalidation state, and support packets
- **Topology extraction** — Mock backend (default, no dependencies) or experimental OCP/CadQuery backend for real STEP parsing
- **Feature recognition** — Deterministic rule-based heuristics with explicit candidate/uncertain status
- **Engineering context** — User-provided YAML for constraints, materials, loads, and simulation setup
- **AI summaries** — Deterministic Markdown generation from structured resources (no LLM required)
- **Patch proposals** — Structured, auditable parameter edit proposals with guarded execution
- **CAE integration** — CalculiX deck import, mapping, and scaffold export (no solver execution)
- **Evidence layer** - evidence indexes, audit events, freshness/revalidation status, diagnostics, and explicit non-advancement of claims
- **MCP server** — `aieng serve` exposes agent-callable tools over stdio or SSE
- **FRD parsing** — Pure-Python CalculiX result parser (DISP, S fields; no VTK dependency)
- **Converter framework** - ingestion adapters that bring CAD/CAE artifacts into `.aieng`; conversion is plumbing, not the semantic source of truth
- **Reference backend capability snapshot** - see [`docs/backend_capability_matrix.md`](docs/backend_capability_matrix.md) and [`docs/backend_artifact_reference.md`](docs/backend_artifact_reference.md) for the current workspace-level CAD/CAE/topopt/reconstruction/assembly status and artifact paths

## Benchmarks

`.aieng` is evaluated against two complementary benchmarks.

### Manual AI usefulness benchmark

Human-conducted A/B comparison of an LLM's ability to reason about a CAD/CAE
model when given raw input vs the structured `.aieng` package. Scored against
a 7-dimension rubric covering geometry understanding, feature identification,
referenceability, missingness honesty, and task success.

| Input | Honesty | Usefulness |
|---|---:|---:|
| Raw STEP only | 18 / 18 | 8 / 18 |
| `.aieng` package | 18 / 18 | 18 / 18 |

`.aieng` preserves non-hallucination behavior while making engineering models
substantially more useful to a general AI. See
[`benchmarks/ai_usefulness/`](benchmarks/ai_usefulness/) for methodology and
full per-dimension scoring.

### Automated A/B benchmark — `llm_engineering_usefulness`

Inspect-AI based harness that measures, across multiple trials, whether an LLM
equipped with `.aieng` evidence access produces better engineering proposals
than the same LLM without it. Same model, same prompt template, same scoring
rubric in both conditions — the only independent variable is access to the
evidence layer.

**Two scoring axes (orthogonal):**

| Axis | Measures | How |
|---|---|---|
| Correctness | Verdict in `{C, P, I}` | Deterministic substring rubric with hallucination penalties |
| Cost-efficiency | `min(1.0, token_budget / total_tokens)` | Per-scenario token budget |

**Two conditions:**

- **Condition A** — raw concatenated `.aieng` artifacts dumped into the prompt, no tools.
- **Condition B** — package path handle + read-only AIENG tool surface
  (`aieng_inspect_package`, `aieng_read_artifact`,
  `aieng_cae_preprocessing_summary`), multi-turn agentic execution.

#### Headline results

Four scenarios shipped to date, all evaluated against Kimi
`kimi-for-coding` (Anthropic-compatible endpoint) at temperature 0:

| Scenario | n | Correctness (A) | Correctness (B) | Mean eff. (A / B) |
|---|---:|---|---|---|
| Diagnose broken CAE setup *(cross-ref defect, 158 KB pkg)* | 5 | 5/5 **C** | 5/5 **C** | 0.038 / **0.285** |
| Mass-reduction recommendation *(multi-step judgement)* | 5 | 5/5 **C** | 5/5 **C** | 0.125 / **0.792** |
| Stress-concentrator recommendation *(open-ended)* | 10 | 10/10 **C** | 10/10 **C** | 0.129 / **0.741** |
| Setup-correction audit *(missing items + dangling ref)* | 10 | **3 C / 3 P / 4 I** &nbsp;(acc 0.450) | **9 C / 1 P / 0 I** &nbsp;(acc 0.950) | 0.114 / 0.153 |

`C` = correct, `P` = partial, `I` = incorrect under the deterministic
substring rubric. Per-scenario reproducibility commands and per-trial
verdict breakdowns live in the observation reports under
[`benchmarks/llm_engineering_usefulness/results/runs/`](benchmarks/llm_engineering_usefulness/results/runs/).

#### Token-efficiency finding (scenarios 1–3)

On scenarios 1, 2, and 3, both conditions reached ceiling correctness
(every trial scored C). The structured tool access (Condition B) reached
the same verdict using **3.5–7.5× fewer absolute tokens** than the raw
artifact dump (Condition A) on the scaled package size.

The mechanism is selective reading: Condition B's agent calls
`aieng_read_artifact` only on the artifacts it actually needs (typically
`stress_by_feature.json`, `parsed_features.json`, `parsed_materials.json`
— a few KB total), instead of consuming the entire ~30–50 KB package
dump that Condition A receives.

#### Measured correctness divergence (scenario 4)

On scenario 4 (setup-correction audit), the benchmark measured a
correctness gap:

- **Condition A — 3 C / 3 P / 4 I out of 10** trials (accuracy 0.450)
- **Condition B — 9 C / 1 P / 0 I out of 10** trials (accuracy 0.950)

The dominant Condition A failure modes were:

- Three trials overclaimed the setup as "ready for solver" / "the setup
  is complete" despite missing artifacts (the worst failure mode for an
  engineering audit).
- One trial invented a phantom missing artifact ("mesh is missing"),
  contradicting the package contents.
- Most partial trials missed `parsed_loads.json` as missing while
  identifying the other gaps.

Condition B produced none of these failures. With access to
`aieng_cae_preprocessing_summary`, the model reads
`missing_items: ["loads", "solver_settings"]` and
`ready_for_solver: false` directly from the package.

Token-efficiency on this scenario is comparable across conditions
(0.114 vs 0.153). The structural advantage here is **correctness, not
cost**.

Full failure-mode breakdown, per-trial verdicts, and the exact rubric
hits are in
[`results/runs/run_20260517T154937Z_kimi-for-coding_setup_correction_n10/observation_report.md`](benchmarks/llm_engineering_usefulness/results/runs/run_20260517T154937Z_kimi-for-coding_setup_correction_n10/observation_report.md).

#### Package-size crossover

A separate finding from running scenario 1 at two package sizes: the
efficiency advantage is **not unconditional**.

| Fixture | Cheaper condition |
|---|---|
| Small (~700 tokens — 8 artifacts) | Condition A by 6× — tool-call overhead dominates |
| Scaled (~52,000 tokens — 14 artifacts incl. realistic bulk) | Condition B by 4–7× — selective reads beat raw dump |

`.aieng` pays for itself when there is structure to navigate. On a
package that fits comfortably in a single prompt, the agentic overhead
of Condition B outweighs the cost of letting the model read everything
at once.

#### Honest limits

- **Correctness divergence is observed on one scenario, one model
  (Kimi `kimi-for-coding`), at n=10, temperature 0.** Generalising to
  other models, other task shapes, or higher temperatures is not
  supported by the data.
- **The benchmark does not show that `.aieng` always improves
  correctness.** On scenarios 1–3, both conditions reached ceiling and
  the benchmark cannot distinguish A from B on correctness.
- **`.aieng` does not prove engineering validity.** The rubric scores
  identification (does the model name the right artifact / feature /
  defect?), not engineering synthesis. A passing verdict on scenario 4
  does not mean the model's proposed correction plan would actually
  produce a runnable solver setup — only that it correctly identified
  what is missing.
- **The deterministic substring rubric is conservative.** A model that
  answers correctly in unusual phrasing may be undercounted. An
  LLM-graded rubric is later work.
- **Auditability is descriptive, not measured.** Condition B's
  per-tool-call transcript is more inspectable than Condition A's
  monolithic completion, but the benchmark does not score this directly.

Full reproducibility commands, per-trial verdicts, failure-mode
breakdowns, and the harness scenario-add instructions live in
[`benchmarks/llm_engineering_usefulness/README.md`](benchmarks/llm_engineering_usefulness/README.md)
and the per-run reports under
[`benchmarks/llm_engineering_usefulness/results/runs/`](benchmarks/llm_engineering_usefulness/results/runs/).

## Documentation

- [`docs/package-semantics.md`](docs/package-semantics.md) - Core package semantics, evidence/claim/freshness boundaries, and cookbook link
- [`docs/release-v0.1-alpha-checklist.md`](docs/release-v0.1-alpha-checklist.md) - v0.1-alpha readiness scope, blockers, and required checks
- [`docs/releases/v0.1.0-alpha.1.md`](docs/releases/v0.1.0-alpha.1.md) - Draft release notes and final tag decision checklist for `v0.1.0-alpha.1`
- [`docs/command_reference.md`](docs/command_reference.md) — Full CLI command reference
- [`docs/architecture.md`](docs/architecture.md) — Internal architecture
- [`docs/core_position.md`](docs/core_position.md) — Core positioning and product boundary
- [`docs/cad_cae_conversion_contract.md`](docs/cad_cae_conversion_contract.md) — CAD/CAE converter contract
- [`docs/geometry_backend_contract.md`](docs/geometry_backend_contract.md) — Geometry backend interface
- [`docs/backend_capability_matrix.md`](docs/backend_capability_matrix.md) — Current reference backend CAD/CAE/topopt/reconstruction/assembly status snapshot
- [`docs/backend_artifact_reference.md`](docs/backend_artifact_reference.md) — Current artifact/path reference for geometry, CAE, topopt, reconstruction, and assembly outputs
- [`docs/demo_catalog.md`](docs/demo_catalog.md) — Canonical backend demos and regression flows (topology optimization, mesh-to-CAD reconstruction, assembly-aware topopt, agent-guided design study)
- [`docs/showcase_gallery.md`](docs/showcase_gallery.md) — Showcase gallery with demo talking points and visual guidance
- `src/aieng/converters/shape_ir.py` — Shape IR converter for topology-first/organic modeling sources (`.shape.json` / `.shape_ir.json`); the workbench runtime can execute its generated build123d `geometry/source.py` into STEP/STL/GLB.
- [`docs/agi_handoff_walkthrough.md`](docs/agi_handoff_walkthrough.md) — End-to-end AGI handoff example
- [`docs/development_log.md`](docs/development_log.md) — Phase-by-phase development history
- [`docs/design_targets.md`](docs/design_targets.md) — Design target resource contract (`task/design_targets.yaml`)
- [`docs/roadmap.md`](docs/roadmap.md) — Roadmap

Workspace-level docs (all three repos):

- [`../docs/system_architecture.md`](../docs/system_architecture.md)
- [`../docs/repo_boundaries.md`](../docs/repo_boundaries.md)
- [`../docs/package_contract.md`](../docs/package_contract.md)
- [`../docs/roadmap.md`](../docs/roadmap.md)

## Contributing

This project is in active development. The default install has no heavy CAD/CAE dependencies. Optional geometry backends (CadQuery/OCP) are installed separately.

```bash
# Default install (no heavy dependencies)
pip install -e .

# With geometry backend support
pip install -e ".[geometry]"

# With MCP server support
pip install -e ".[mcp]"

# Run tests
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
