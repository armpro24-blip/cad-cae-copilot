# Workbench Regression Benchmark

A lightweight, fixed-prompt regression suite for the CAD/CAE workbench. It runs a
collection of representative prompts, captures the resulting artifacts, and can
diff two runs to spot regressions.

## Quick start

```bash
# Run the core subset (fastest path)
python aieng/benchmarks/regression/runner.py --tags core

# Run all prompts
python aieng/benchmarks/regression/runner.py --tags all

# Run only CAE prompts
python aieng/benchmarks/regression/runner.py --tags cae
```

Each run creates a directory under `runs/`:

```
runs/run_20260615T083000Z/
├── manifest.json
├── 001_cad_create_bracket/
│   ├── prompt.md
│   ├── generated.step
│   ├── package.aieng
│   └── metrics.json
└── ...
```

## Compare two runs

```bash
python aieng/benchmarks/regression/compare.py \
  --baseline runs/run_20260610T000000Z \
  --current runs/run_20260615T083000Z
```

The diff report is written to `runs/run_20260615T083000Z/diff_against_baseline.md`.

## Prompts

Prompts live in `prompts/` as Markdown files with a small YAML front-matter block:

```markdown
---
id: 001_cad_create_bracket
tags: [core, cad_create, mechanical]
---

Create an aluminum L-bracket...
```

| Category | Count | IDs |
|---|---|---|
| CAD create | 5 | 001-005 |
| CAD modify | 3 | 006-008 |
| CAE | 4 | 009-012 |
| Optimization / design study | 3 | 013-015 |
| Critique | 2 | 016-017 |
| Autopilot intent | 5 | 018-022 |

## Current runner coverage

The initial `runner.py` focuses on the CAD-create prompts because they can be
executed deterministically without an LLM. Other prompts are loaded and reported
as `skipped` until the backend adapter is extended to handle modify, CAE,
optimization, critique, and intent-routing workflows.

Run `runner.py --tags core` to exercise only the implemented CAD-create prompts.

## Exit codes

- `0`: no prompts failed
- `1`: at least one prompt failed, or no prompts matched the requested tags
