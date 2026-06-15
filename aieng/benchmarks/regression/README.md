# Workbench Regression Benchmark

A regression benchmark kit for the CAD/CAE workbench. Unlike a self-contained
script that calls an LLM internally, this kit is designed to be executed by an
external AI agent (Claude Code, Codex, etc.) through its own MCP connection.
This makes the benchmark test the real agent path: tool choice, intent routing,
approval handling, and final artifacts.

## For agents

Start here: [`AGENT_BENCHMARK_RUNBOOK.md`](AGENT_BENCHMARK_RUNBOOK.md)

## For humans

### What is included

- `prompts/` — ~22 fixed Markdown prompts with YAML front-matter (`id`, `tags`, optional `seed_package`).
- `AGENT_BENCHMARK_RUNBOOK.md` — step-by-step guide for an AI agent.
- `init_run.py` — creates a run directory with one subdirectory per prompt.
- `record.py` — records the result of a single prompt after the agent executes it.
- `compare.py` — diffs two runs and emits a Markdown report.
- `tests/test_regression.py` — smoke tests for the kit plumbing.

### Typical workflow

1. An agent initializes a run:
   ```bash
   cd aieng/benchmarks/regression
   python init_run.py --tags core --output runs/run_$(date -u +%Y%m%dT%H%M%SZ)
   ```

2. The agent reads `AGENT_BENCHMARK_RUNBOOK.md` and executes each prompt through MCP.

3. After each prompt, the agent records the result:
   ```bash
   python record.py \
     --run runs/run_20260615T083000Z \
     --prompt 001_cad_create_bracket \
     --status passed \
     --metrics runs/run_20260615T083000Z/001_cad_create_bracket/metrics.json \
     --artifacts package.aieng,generated.step
   ```

4. Compare against a baseline:
   ```bash
   python compare.py \
     --baseline runs/run_20260610T000000Z \
     --current runs/run_20260615T083000Z
   ```

5. Review `runs/run_20260615T083000Z/diff_against_baseline.md`.

### Tags

- `core` — fastest, most stable prompts (run these first)
- `cad_create`, `cad_modify`, `cae`, `optimization`, `critique`, `intent` — capability groups
- `mechanical` — mechanical engineering prompts

### Local smoke tests

```bash
cd aieng
python -m pytest benchmarks/regression/tests/test_regression.py -v
```

The tests verify `init_run.py`, `record.py`, and `compare.py` plumbing without
requiring a live MCP connection or LLM.
