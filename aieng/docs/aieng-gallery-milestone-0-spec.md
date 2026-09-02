# `aieng-gallery` — Milestone 0 Implementation Spec

> **Status: archived design exploration. This is not the active roadmap.**

> **Status note (May 2026):** This spec was written before `aieng-gallery/` was created. It is retained as the Milestone 0 design record.

> The project direction has shifted to building real product capability first (`.aieng` format + adapters), with future demonstrations expected to use real end-to-end runs, focused scripts, reproducible reports, or videos.

> **Status:** Documentation only. The `aieng-gallery/` repository does not yet exist. This spec is detailed enough for a future implementation agent to scaffold the repo with minimal ambiguity.
>
> **Companion docs:** [`public-positioning.md`](public-positioning.md) (outward-facing narrative), [`core_position.md`](core_position.md) (inward-facing positioning), [`../../Agent.md`](../../Agent.md) (workspace boundaries), [`../../aieng_freecad_mcp/docs/product_boundary.md`](../../aieng_freecad_mcp/docs/product_boundary.md), [`../../aieng_freecad_mcp/docs/evidence_and_claim_policy.md`](../../aieng_freecad_mcp/docs/evidence_and_claim_policy.md).

---

## 1. Milestone 0 goal

The smallest useful public demo of `.aieng`:

- **One** repo (new sibling).
- **One** scenario (`bracket-thickness`).
- **One** command: `python -m aieng_gallery run bracket-thickness`.
- **One** static HTML report on disk, side-by-side.
- **Two** lanes: raw lane vs `.aieng` lane.
- **Visible** evidence pane and trace pane from the `.aieng` lane.
- **Visible** `claims_advanced: false`.
- **Visible** "Evidence ≠ claim" banner.
- **No** real LLM API required. Deterministic replay mode is the default.
- **No** real FreeCAD required. The stub adapter path is the default.

If a casual GitHub visitor runs the quickstart on a clean machine and opens the HTML, they see the same prompt produce two materially different responses, with the `.aieng` lane producing a schema-valid patch proposal, real evidence/trace appends to a copied package, and a banner explaining what those records do (and do not) mean.

---

## 2. Recommended repo name

**Working name: `aieng-gallery`.**

| Candidate | Verdict | Reason |
|---|---|---|
| **`aieng-gallery`** | **Chosen.** | "Gallery" reads as a curated showcase. Maps cleanly to "scenarios as cards." Doesn't overpromise. |
| `aieng-demos` | Acceptable fallback. | Slightly generic; harder to distinguish from internal demo scripts already in the two existing repos. |
| `aieng-arena` | Reject. | Implies head-to-head combat / leaderboard, raising expectations the project should not own. |
| `aieng-playground` | Reject. | Sounds like an interactive sandbox; visitors won't know what to do. |
| `aieng-bench` | Reject. | Implies a formal benchmark suite. The benchmark angle is secondary and not Milestone-0 scope. |

The chosen name should appear in the repo description as:
*"Public demos for `.aieng`. Same CAD task, raw lane vs `.aieng` lane, side by side."*

---

## 3. Repo relationship

```
agent / Skill
    ↓
aieng-gallery  (this repo — public demos, reports)
    ↓
aieng_freecad_mcp  ─►  FreeCAD / FreeCADCmd / CalculiX  (optional runtime)
    ↓
aieng  (semantic / evidence package format, schemas)
```

Rules:

- The gallery **depends on** `aieng` (for schemas and validators) and on `aieng_freecad_mcp` (for the stub/real adapter path). Both as installable Python packages. Neither depends on the gallery.
- The gallery **owns**:
  - scenario fixtures (`scenarios/<name>/`),
  - prompts and replay files,
  - lane orchestration,
  - HTML report generation,
  - pre-baked preview images.
- The gallery **must not own**:
  - canonical `.aieng` schemas (they live in `aieng/`),
  - adapter logic (it lives in `aieng_freecad_mcp/`),
  - claim policy (defined in `aieng_freecad_mcp/docs/evidence_and_claim_policy.md`; enforced in code there),
  - any FreeCAD-specific path resolution or executable detection.
- A future `aieng-cad-copilot` Skill **depends on** the gallery for its evaluation surface (Lane C), not the other way around. The gallery must be usable before the Skill exists.

---

## 4. Scenario choice — `bracket-thickness`

**Scenario name:** `bracket-thickness`

**User prompt** (shown in the report):
> *"Make this mounting bracket stiffer near the load path. Don't change the mounting holes."*

**Input assets** (vendored into the scenario directory, derived once from `aieng_freecad_mcp/examples/parametric_bracket/`):

- `input.step` — the unmodified bracket STEP.
- `package.aieng/` — pre-built `.aieng` package with:
  - `manifest.json`,
  - `graph/feature_graph.json` (declares `feat_base_plate_001` with editable `thickness_mm`, and `feat_hole_pattern_001` marked as protected),
  - `graph/constraints.json` (mounting holes have `protect_geometry`),
  - `task/task_spec.yaml`,
  - `results/claim_map.json` (with one claim: stiffness target unsupported),
  - `results/evidence_index.json` (empty list at fixture time),
  - `provenance/tool_trace.json` (empty list at fixture time).
- `previews/before.png` — pre-baked thumbnail.
- `previews/after.png` — pre-baked thumbnail of the expected post-edit geometry.

**`.aieng` context used by Lane B:**

- feature graph (to identify the editable parameter and the protected pattern),
- constraints (to honor `protect_geometry`),
- task spec (to read forbidden claims and intent),
- claim map (read-only — never modified).

**Expected raw-lane behavior** (canned, but representative):

A free-form natural-language response. Suggests thickening "the base" without naming a feature, may invent a dimension, may casually mention the holes. Does **not** produce a structured patch proposal. Does not respect protected regions because it does not know which features are protected.

**Expected `.aieng`-lane behavior:**

A structured patch proposal:

```json
{
  "operation": "modify_parameter",
  "target": "feat_base_plate_001",
  "parameter": "thickness_mm",
  "from": 4.0,
  "to": 5.0,
  "unit": "mm",
  "reason": "increase stiffness near mounting load path"
}
```

Validated against [`aieng/src/aieng/schemas/patch_proposal.schema.json`](../src/aieng/schemas/patch_proposal.schema.json). Executed via the stub adapter (no FreeCAD needed). One evidence record and one trace record appended. `claim_map.json` unchanged. `claims_advanced: false`.

**Expected visual before/after result:**

Two pre-baked PNG thumbnails rendered side by side. The "after" image differs only in plate thickness; hole pattern is identical.

**Expected evidence/trace result:**

- One new entry in the run workspace's `results/evidence_index.json` referencing the stub-executed `modify_parameter` step.
- One new entry in `provenance/tool_trace.json` with `producer: "aieng_freecad_mcp"`, `claims_advanced: false`, `exit_status: "ok"` (or stub equivalent).
- `results/claim_map.json` byte-identical to the fixture.

**What not to claim** (must be visible in the report):

- Not "the design is stiffer."
- Not "the part is safe."
- Not "the new thickness satisfies any structural target."
- Not "the modified bracket is manufacturable."

The report should explicitly say: *the edit produced evidence that a parameter was changed inside a guarded boundary. The design is not validated.*

---

## 5. Lane model

### Lane A — raw lane

- **Input:** the prompt + a one-line description of the input file (e.g. "STEP file `input.step`, ~120 KB, no further context"). No `.aieng` package.
- **Implementation:** deterministic canned output read from `scenarios/bracket-thickness/replays/raw.json`.
- **Output shape:** a single free-form text block, captured into the report under the "Raw lane" card.
- **Honesty rule:** the canned output must be a *plausible* response from a competent general-purpose LLM with no engineering context — not a strawman. If the canned response is later replaced by a real LLM (post-Milestone-0), the replay file remains the default so tests stay deterministic.

### Lane B — `.aieng` lane

- **Input:** the same prompt + path to `package.aieng/` + a description of the MCP tools available.
- **Implementation (Milestone 0):**
  1. Read the canned model output from `scenarios/bracket-thickness/replays/aieng.json`. This is the agent's *decision* — which feature, which parameter, which value — encoded as a structured patch proposal.
  2. Validate the proposal against `aieng/src/aieng/schemas/patch_proposal.schema.json`.
  3. Invoke the existing adapter's stub-executor path (via `aieng_freecad_mcp`) to **really** apply the patch to the copied workspace package: writes evidence, writes trace, marks references as `needs_review`, leaves `claim_map.json` untouched.
- **Output shape:** structured patch JSON + adapter result + appended evidence/trace files on disk.
- **Why use the real stub adapter, not pure replay:** the demo's credibility comes from showing real evidence/trace files. Pure replay would mean the gallery is faking the artifacts the trust story depends on. The stub executor exists in the adapter precisely to make this cheap.

### Lane C — reserved for future Skill (NOT Milestone 0)

Document the interface so a later `aieng-cad-copilot` Skill drops in without restructuring:

```python
# Conceptual sketch only — not part of Milestone 0 implementation.

class Lane(Protocol):
    name: str               # "raw" | "aieng" | "skill"
    def run(self,
            prompt: str,
            scenario: ScenarioPaths,
            workspace: Path) -> LaneResult: ...

class LaneResult:
    text_output: str | None
    structured_output: dict | None
    artifacts: list[Path]
    evidence_ids: list[str]
    trace_ids: list[str]
    claim_policy: dict      # always {"claims_advanced": False, ...} for Milestone 0
    notes: list[str]
```

A future Skill lane implements the same `Lane` protocol. The HTML template should be N-column ready (CSS grid keyed on number of lanes), so adding Lane C is a config change.

---

## 6. Mock / replay policy

- **Mode:** deterministic file-replay. No network. No API key.
- **Storage:** one JSON file per lane per scenario, checked in:
  - `scenarios/bracket-thickness/replays/raw.json`
  - `scenarios/bracket-thickness/replays/aieng.json`
- **Shape:** each replay file is a small JSON object with `prompt_hash`, `model_id` (e.g. `"mock-deterministic-v1"`), and `output` (a string for the raw lane; a patch proposal dict for the `.aieng` lane).
- **Determinism:** lanes hash the prompt + scenario id and assert it matches `prompt_hash` in the replay. If they diverge, the test fails loudly — the scenario maintainer must re-record.
- **No API keys required by default.** The default `LLMClient` is `MockReplayClient`. A `pyproject.toml` extra (e.g. `[llm-anthropic]`, `[llm-openai]`) can later add real clients; selecting them is a CLI flag, never the default.
- **Adding a real LLM later** must not change Milestone 0 behavior. The canned replay remains the default and is what tests and the README screenshot exercise.

---

## 7. Fixture and workspace policy

- **Checked-in fixtures are read-only.** `scenarios/<name>/package.aieng/` is never written to by a run.
- **Per-run workspace.** Each `run` command creates `results/<scenario>/<timestamp>/workspace/` and copies the fixture package into it. All evidence/trace writes go into the copy.
- **Outputs:** `results/<scenario>/<timestamp>/`
  - `workspace/` — mutated copy of the package (the source of truth for the evidence/trace pane).
  - `report.html` — the static report.
  - `lane_a.json`, `lane_b.json` — raw lane outputs.
  - `summary.json` — high-level run summary.
- **`.gitignore`:** all of `results/*` **except** one committed snapshot directory `results/_example/bracket-thickness/` used by the README and by docs screenshots.
- **Snapshot integrity:** a `tests/test_example_snapshot.py` regenerates and compares structural fields (e.g. patch target, evidence kind, claim_policy) — never byte equality of HTML or PNG. The example snapshot is updated by a `scripts/refresh_example.py` script and committed deliberately.

---

## 8. Visual rendering policy

- **Default:** pre-baked PNG thumbnails committed in `scenarios/<name>/previews/`. No rendering at run time. No OCP/CadQuery/FreeCAD dependency in the default install.
- **Why:** Milestone 0 must run in under 5 minutes on a clean machine with `pip install -e .` and nothing else. Adding a CAD renderer would inflate install size, hit Python/ABI issues, and discourage casual contributors.
- **Future paths** (out of scope for Milestone 0):
  - Optional live render via OCP / CadQuery (`pyproject.toml` extra `[render]`).
  - Optional real FreeCAD / FreeCADCmd path (`[freecad]` extra), which gives real artifact STEP files in addition to canned thumbnails.
- **Rule:** previews are illustrative. The trust story rests on the JSON in the evidence and trace panes, not on the picture.

---

## 9. HTML report structure

One static `report.html` (no JS framework, no server). Sections, in order:

1. **Title and scenario summary** — scenario name, one-line description, run timestamp, lane list.
2. **"Evidence ≠ claim" banner** — pinned at the top of the page. Same wording every scenario. Brief and unavoidable.
3. **User prompt card** — verbatim prompt, both lanes received the same one.
4. **Side-by-side visual** — `previews/before.png` vs `previews/after.png`. Two columns.
5. **Lane A — raw result card** — model id, text output, scorecard row (see below).
6. **Lane B — `.aieng` result card** — model id, structured patch proposal pretty-printed, scorecard row.
7. **Feature/constraint diff card** — what feature was touched; was any protected feature touched; was the patch schema-valid.
8. **Evidence pane** — pretty-printed new entries from `evidence_index.json`, with `evidence_id` highlighted.
9. **Trace pane** — pretty-printed new entries from `tool_trace.json`, with `claims_advanced: false` highlighted in green.
10. **Claim discipline pane** — explicit row: `claim_map.json` byte hash before vs after the run, side by side. If they differ, the report renders an error block.
11. **Artifacts links** — relative links to `workspace/`, `lane_a.json`, `lane_b.json`, `summary.json`, the two PNGs.
12. **Footer** — link back to `aieng/docs/public-positioning.md`, repo README, evidence/claim policy doc.

Scorecard row (per lane) shows four boolean badges:

- Produced schema-valid patch proposal? Y/N
- Respected protected features? Y/N
- Wrote evidence and trace? Y/N
- Claims auto-advanced? must be **N** for both lanes.

---

## 10. CLI UX

**Primary command:**

```bash
python -m aieng_gallery run bracket-thickness
```

Behavior:

- Creates `results/bracket-thickness/<timestamp>/`.
- Runs both lanes.
- Writes the HTML report and per-lane JSON.
- Prints to stdout:
  ```
  aieng-gallery: bracket-thickness
  Lane A (raw): replay (mock-deterministic-v1)
  Lane B (aieng): replay → adapter stub → evidence written
  Patch proposal: VALID against patch_proposal.schema.json
  Claim map: unchanged (sha256 match)
  Report: results/bracket-thickness/20260514-120000/report.html
  Open the report in a browser to view side-by-side output.
  ```
- Exit 0 on success.

**Non-Milestone-0 commands** (document, do not implement):

- `python -m aieng_gallery list` — list available scenarios.
- `python -m aieng_gallery run <scenario> --lane skill` — once a Skill lane exists.
- `python -m aieng_gallery run <scenario> --llm anthropic` — once a real LLM client is wired.
- `python -m aieng_gallery run <scenario> --backend freecad` — once the optional real-FreeCAD path is wired.
- `python -m aieng_gallery report <run-dir>` — re-render a report from an existing run.
- `python -m aieng_gallery refresh-example <scenario>` — regenerate the committed example snapshot.

---

## 11. Proposed repo layout

```
aieng-gallery/
├── README.md                     # value prop + animated GIF + 5-min quickstart
├── AGENTS.md                     # boundary rules (mirrors workspace boundaries)
├── LICENSE
├── pyproject.toml                # extras: [demo], [llm-anthropic], [llm-openai],
│                                 #         [render], [freecad]
├── .gitignore                    # ignores results/* except results/_example/
├── src/
│   └── aieng_gallery/
│       ├── __init__.py
│       ├── __main__.py           # `python -m aieng_gallery`
│       ├── cli.py
│       ├── runner.py             # orchestrates lanes + report + workspace setup
│       ├── lanes/
│       │   ├── __init__.py
│       │   ├── base.py           # Lane protocol, LaneResult
│       │   ├── raw_lane.py
│       │   └── aieng_lane.py
│       ├── llm/
│       │   ├── __init__.py
│       │   └── mock_replay.py    # MockReplayClient (default)
│       ├── adapter/
│       │   └── stub_bridge.py    # thin wrapper around aieng_freecad_mcp stub path
│       ├── schema_check.py       # validate patch proposal against aieng schemas
│       ├── diff.py               # build the feature/constraint diff card
│       ├── workspace.py          # copy fixtures into per-run workspace
│       ├── report.py             # render HTML from a Jinja-like template
│       └── templates/
│           └── report.html.j2
├── scenarios/
│   └── bracket-thickness/
│       ├── scenario.yaml         # prompt, lane configs, expected fingerprints
│       ├── input.step
│       ├── package.aieng/        # canonical fixture (read-only)
│       │   ├── manifest.json
│       │   ├── graph/feature_graph.json
│       │   ├── graph/constraints.json
│       │   ├── task/task_spec.yaml
│       │   ├── results/claim_map.json
│       │   ├── results/evidence_index.json
│       │   └── provenance/tool_trace.json
│       ├── prompts/
│       │   ├── raw.md
│       │   └── aieng.md
│       ├── replays/
│       │   ├── raw.json
│       │   └── aieng.json
│       └── previews/
│           ├── before.png
│           └── after.png
├── results/
│   └── _example/
│       └── bracket-thickness/    # one committed example run
│           ├── report.html
│           ├── lane_a.json
│           ├── lane_b.json
│           ├── summary.json
│           └── workspace/        # post-run package state
├── scripts/
│   ├── build_fixtures.py         # rebuild scenario package.aieng from aieng CLI
│   └── refresh_example.py        # regenerate results/_example/
├── tests/
│   ├── test_runner.py
│   ├── test_lane_aieng.py        # schema validity, claims_advanced=false
│   ├── test_evidence_discipline.py  # claim_map hash unchanged before/after
│   ├── test_example_snapshot.py  # structural snapshot (no byte equality)
│   └── conftest.py
└── docs/
    ├── add-a-scenario.md
    ├── evidence-vs-claim.md      # plain-language explainer
    └── lane-protocol.md          # the Lane / LaneResult contract for future lanes
```

---

## 12. Reuse from existing repos

**Reuse as dependencies (declared in `pyproject.toml`):**

- `aieng` — for `patch_proposal.schema.json` and other schemas, plus `aieng.validate` helpers if exposed. The schemas are the source of truth; the gallery never copies them.
- `aieng_freecad_mcp` — for the stub executor path (`stub_executor.py`), `persist_standard_result_to_aieng`, and `aieng_bridge` context loaders. The gallery never reimplements these.

**Reuse conceptually (as inspiration, not copy):**

- The five-path composability story from [`release_v1_demo.md`](../../aieng_freecad_mcp/docs/release_v1_demo.md) — the gallery's report panes mirror the same boundary shapes (CAD vs CAE vs reference vs claim).
- The acceptance pattern from [`scripts/run_milestone1_acceptance.py`](../../aieng_freecad_mcp/scripts/run_milestone1_acceptance.py) — structured JSON output, deterministic, no FreeCAD needed.
- The example output style of [`example_v1_demo_output.md`](../../aieng_freecad_mcp/docs/example_v1_demo_output.md) — terse, banner-led, no overpromising.

**Reuse as fixture source:**

- The parametric bracket fixture at [`../../aieng_freecad_mcp/examples/parametric_bracket/`](../../aieng_freecad_mcp/examples/parametric_bracket/) — copied (via `scripts/build_fixtures.py`) into the scenario as a frozen snapshot. The gallery does **not** symlink; it vendors a frozen copy so demos remain stable when the adapter repo evolves.
- The patch JSON in [`examples/parametric_bracket/patches/reduce_base_plate_thickness.json`](../../aieng_freecad_mcp/examples/parametric_bracket/patches/reduce_base_plate_thickness.json) — used as the basis for the `.aieng` lane's replay output.

**Must not copy or duplicate:**

- Canonical schemas from `aieng/src/aieng/schemas/`. Always read from the installed `aieng` package.
- Adapter logic from `aieng_freecad_mcp/src/`. Always call into the installed package.
- Claim-policy enforcement. The gallery relies on the adapter's existing `persistence.py` and `claims.py` to enforce immutability — it must not add a second claim layer.
- FreeCAD-specific path resolution. The gallery has no FreeCAD knowledge.

---

## 13. Tests and acceptance criteria

Acceptance criteria for Milestone 0:

1. **Quickstart works clean.** A `git clone` + `pip install -e .` + `python -m aieng_gallery run bracket-thickness` succeeds on a machine with **no FreeCAD installed** and **no LLM API key set**.
2. **Report HTML is generated** at `results/bracket-thickness/<timestamp>/report.html` and contains all 12 sections from §9.
3. **Patch proposal validates** against `aieng/src/aieng/schemas/patch_proposal.schema.json` (via `jsonschema`). Test: `test_lane_aieng.py::test_patch_proposal_is_schema_valid`.
4. **Evidence and trace are visible** in the report and in the workspace package: at least one new entry each in `results/evidence_index.json` and `provenance/tool_trace.json`.
5. **`claim_map.json` is unchanged.** Test: `test_evidence_discipline.py::test_claim_map_hash_unchanged` asserts byte-equal SHA-256 of `results/claim_map.json` before and after the run.
6. **`claims_advanced: false`** is present in every trace entry written by the lane, and is rendered as a highlighted badge in the report.
7. **Zero changes** required to `aieng/` or `aieng_freecad_mcp/`. CI runs `git -C ../aieng diff --exit-code` and the equivalent for the adapter repo when run in the local workspace context (a documentation note suffices when CI doesn't have the sibling repos checked out).
8. **No schema changes** in `aieng/`. No new resource types. No new required fields.
9. **Default test suite passes** with `pytest -m "not freecad and not realllm"`. No tests require optional extras.

Tests bundled in Milestone 0 (and only these):

- `test_runner.py` — end-to-end smoke: run produces all expected output files.
- `test_lane_aieng.py` — schema validity of `.aieng` lane output; `claims_advanced` is `false`.
- `test_evidence_discipline.py` — claim_map hash unchanged; evidence and trace counts incremented by exactly 1.
- `test_example_snapshot.py` — structural fields of `results/_example/` match the committed snapshot. Not byte equality.

---

## 14. Risks and guardrails

| Risk | Mitigation |
|---|---|
| **Demo becomes too complex.** | One scenario, one command, no extras in the default install. Reject any PR that adds a required dependency to the demo path. |
| **`.aieng` becomes too FreeCAD-specific via gallery fixtures.** | Scenario `package.aieng/` fixtures must validate against `aieng/src/aieng/schemas/`. Any FreeCAD-shaped string (e.g. `Body.Pad.Length`) appears only inside optional adapter-local provenance fields, never in required canonical fields. CI lint: regex check on canonical resource files. |
| **Visual demo hides evidence/claim discipline.** | The "Evidence ≠ claim" banner is part of the report template and cannot be styled away. The claim-discipline pane shows the SHA-256 hash before/after. If hashes differ, the report renders an error block. |
| **Raw lane is made artificially bad to flatter the `.aieng` lane.** | Replay files are reviewed for plausibility. Document a "raw lane fairness rule" in `docs/add-a-scenario.md`: the raw response must be what a competent general-purpose LLM might plausibly output without engineering context — not an obvious strawman. |
| **Users misunderstand evidence as validation.** | Banner + explicit per-scenario "what not to claim" list, surfaced both in the report and in the scenario README. |
| **Third repo starts owning adapter logic.** | Gallery has no `bridge/`, no `executor.py`, no FreeCAD detection code. All adapter operations go through `from freecad_mcp...import ...`. A code-review checklist item enforces this. |
| **Future Skill lane reshapes the gallery architecture.** | The Lane protocol in `src/aieng_gallery/lanes/base.py` is fixed at Milestone 0 and intentionally minimal. A future Skill lane implements the same protocol; it does not introduce a new lane API. |
| **Replay rot.** | Each replay file has a `prompt_hash`. A test fails if the live prompt no longer matches the recorded hash, forcing intentional re-recording rather than silent drift. |

---

## 15. Non-goals (Milestone 0)

The following are **out of scope** for Milestone 0. Each is reachable later without re-architecting:

- No real LLM API required or used by default.
- No real FreeCAD required or used by default.
- No live CAD editing — only stub-adapter evidence writes.
- No solver execution.
- No claim update workflow. No invocation of `aieng_update_claim`.
- No hosted app, no web service, no GitHub Pages publishing pipeline.
- No benchmark leaderboard, no aggregate scoring across runs.
- No new `.aieng` schema fields or resource types.
- No second scenario.
- No Skill lane (Lane C).
- No third-repo creation as part of this spec — that is the next prompt.

---

## 16. Open questions

The following deliberately remain open and should be resolved in the next prompt (repo-creation) or in the first PR after the repo exists:

1. **Final repo name.** Confirm `aieng-gallery`, or pick an alternative from §2.
2. **Exact bracket assets to vendor.** Decide whether `scripts/build_fixtures.py` rebuilds the scenario package from the adapter repo at scaffold time, or whether the JSON files are hand-curated and committed as-is. Recommendation: rebuild via `aieng` CLI for reproducibility.
3. **Lane B implementation depth.** Confirm Milestone 0 uses the **adapter stub via `aieng_freecad_mcp`** (recommended) rather than pure in-gallery replay (simpler but less honest about the evidence path).
4. **Whether to commit `results/_example/` snapshot.** Recommendation: yes — one committed example, used by README screenshots and by `test_example_snapshot.py`. Without this, the README has nothing to show before a first run.
5. **Future real-LLM gating.** Recommendation: extras-based — `pip install aieng-gallery[llm-anthropic]` plus a `--llm anthropic` CLI flag. Default never reaches network.
6. **Future Skill lane gating.** Recommendation: an explicit `--lane skill` flag once the Skill exists. The Skill lane is opt-in, not default, so casual readers see the two-lane comparison first.
7. **Where the `Lane` protocol lives long-term.** If multiple consumers (gallery, benchmarks, eval harnesses) need it, it may eventually graduate from `aieng-gallery` into a small shared package. Defer.

---

## See also

- [`public-positioning.md`](public-positioning.md) — outward-facing narrative; this spec is its Milestone 0 expansion.
- [`core_position.md`](core_position.md) — why `.aieng` exists.
- [`../../Agent.md`](../../Agent.md) — workspace boundaries.
- [`../../aieng_freecad_mcp/docs/agentic-cad-cae-blueprint.md`](../../aieng_freecad_mcp/docs/agentic-cad-cae-blueprint.md) §14 — future Skill direction (Lane C target).
- [`../../aieng_freecad_mcp/docs/evidence_and_claim_policy.md`](../../aieng_freecad_mcp/docs/evidence_and_claim_policy.md) — the discipline the gallery inherits.
- [`../../aieng_freecad_mcp/docs/mvp-1-plan.md`](../../aieng_freecad_mcp/docs/mvp-1-plan.md) — the patch lifecycle Lane B reuses.
