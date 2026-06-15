:warning: **For AI agents only** — This file is the entry point for an AI agent running the regression benchmark. Human readers should start with `README.md`.

---

# Workbench Regression Benchmark — Agent Runbook

## Your goal

Execute a fixed set of prompts against the workbench through MCP and produce a run directory that can be compared against a previous baseline. Do not change the prompts. Do not skip prompts unless explicitly instructed by the user. Record honest results, including failures.

## Prerequisites

1. You are connected to the workbench MCP server (`aieng-workbench`).
2. The workbench backend is running and reachable.
3. You can use tools such as `aieng.create_project`, `cad.execute_build123d`, `cad.critique`, `cae.prepare_solver_run`, `cae.run_solver`, `opt.run_topology_optimization`, etc.

## Quick workflow

```bash
# 1. Initialize a run directory
cd aieng/benchmarks/regression
python init_run.py --tags core --output runs/run_$(date -u +%Y%m%dT%H%M%SZ)

# 2. Execute each prompt through MCP, then record the result for each one.
#    Example after running prompt 001:
python record.py \
  --run runs/run_20260615T083000Z \
  --prompt 001_cad_create_bracket \
  --status passed \
  --metrics runs/run_20260615T083000Z/001_cad_create_bracket/metrics.json \
  --artifacts package.aieng,generated.step

# 3. After all prompts are recorded, compare against a baseline:
python compare.py \
  --baseline runs/run_20260610T000000Z \
  --current runs/run_20260615T083000Z
```

## Prompt categories and expected MCP tools

### CAD create prompts (001–005)

Expected tool sequence:
1. `aieng.create_project` or equivalent to create a fresh project.
2. `cad.execute_build123d` with build123d code that satisfies the prompt.
3. Capture:
   - `package.aieng`
   - `generated.step`
   - thumbnail image if available
   - `metrics.json` with volumes and bounding boxes (you may compute these with build123d)

### CAD modify prompts (006–008)

Each prompt has `seed_package` in its front-matter. Start from that seed package (or recreate the base geometry yourself).

Expected tool sequence:
1. Load/import seed project.
2. `cad.edit_parameter` or `cad.replace_part` or `cad.execute_build123d` in `append` mode.
3. Capture modified artifacts and metric deltas.

### CAE prompts (009–012)

Expected tool sequence:
1. Ensure the model has material, loads, and constraints configured.
2. `cae.prepare_solver_run`.
3. `cae.run_solver`.
4. `cae.extract_solver_results`.
5. Capture:
   - solver status
   - `computed_metrics.json` or extracted metrics
   - `.frd` result file if available
   - For `012_cae_missing_load`, the expected outcome is a readiness/honesty report stating that required inputs are missing.

### Optimization / design study prompts (013–015)

Expected tool sequence:
1. `opt.run_topology_optimization` or `opt.sizing_sweep` or `opt.propose_candidates` + `opt.run_candidates` + `opt.rank_candidates`.
2. Capture the optimized geometry and ranking metrics.

### Critique prompts (016–017)

Expected tool sequence:
1. `cad.critique` or `cad.design_review`.
2. Capture the findings list.

### Autopilot intent prompts (018–022)

These prompts are short and ambiguous by design. Do **not** execute CAD/CAE tools for them. Instead:
1. Submit the prompt to the workbench chat/autopilot path.
2. Record the resolved `intent_type` (e.g. `create_geometry`, `modify_geometry`, `plan_simulation`, `critique`, `explain_project`) and the tool sequence the agent selected.
3. Expected mappings:
   - "Create an aluminum L-bracket..." → `create_geometry` → `/build`
   - "Make the wall thicker." → `modify_geometry` → `/modify`
   - "Run a stress analysis..." → `plan_simulation` → `/simulate`
   - "Is this bracket manufacturable?" → `critique` → `/critique`
   - "Explain this part." → `explain_project` → `/explain`

## Recording a result

For every prompt you execute, call `record.py`:

```bash
python record.py \
  --run <run_directory> \
  --prompt <prompt_id> \
  --status {passed|failed|skipped} \
  [--metrics <path/to/metrics.json>] \
  [--artifacts <comma-separated filenames relative to prompt dir>]
```

Create `metrics.json` as a plain object, for example:

```json
{
  "volumes": {"bracket": 21748.67},
  "bounding_boxes": {"bracket": {"x": 40.0, "y": 110.0, "z": 5.0}},
  "part_count": 1
}
```

If a prompt fails, set `--status failed` and include an `error` field in `metrics.json`:

```json
{"error": "cad.execute_build123d returned solver_status: failed"}
```

## Tag conventions

- `core`: fastest, most stable prompts — run these first
- `cad_create`, `cad_modify`, `cae`, `optimization`, `critique`, `intent`: capability groups
- `mechanical`: mechanical engineering prompts

Default tag for a quick check: `--tags core`.

## Approval handling

The workbench gates mutation tools (`cad.execute_build123d`, `cae.run_solver`, etc.) behind approval. In a benchmark context, you may auto-approve your own tool calls if your MCP client supports it. Record honestly if a tool was blocked by approval.

## What to capture per prompt

In each prompt subdirectory, ensure these files exist when applicable:

- `prompt.md` — written by `init_run.py`
- `result.json` — written by `record.py`
- `metrics.json` — optional, referenced by `record.py`
- `package.aieng` — resulting package
- `generated.step` — exported STEP
- `thumbnail.png` — render if available
- `tool_trace.jsonl` — list of tool calls and responses (highly recommended)

## Final step: compare

After all prompts are recorded:

```bash
python compare.py \
  --baseline runs/<previous_run> \
  --current runs/<this_run>
```

The report is written to `<this_run>/diff_against_baseline.md`. A human will review it.
