# `docs/` index

31 files with no index made this directory unreadable to anyone new: a plan
abandoned in May sat beside the contract the runtime enforces today, with
nothing to tell them apart. Two of them still described **FreeCAD as the CAD
backend** — it has been build123d/OCP since the MCP-first cutover, and FreeCAD
now lives under `legacy/`.

**Start here instead:** [`../AGENTS.md`](../AGENTS.md) is the canonical guide for
both agents and humans. [`prompt-guide.md`](prompt-guide.md) is the user-facing
entry: the sentences that drive the workbench.

Every file below is listed. `scripts/check_agent_docs.py` fails when one is
missing from this index or listed but absent, so this page cannot quietly rot
the way the directory it describes did.

---

## Current — describes the system as it is

| Doc | What it covers |
|---|---|
| [prompt-guide.md](prompt-guide.md) | **User entry point** — the sentences that drive the workbench |
| [one-prompt-agent-setup.md](one-prompt-agent-setup.md) | Copy-paste prompt to bring an MCP-capable agent up |
| [mcp-first-vscode-workflow.md](mcp-first-vscode-workflow.md) | The recommended editor-first path |
| [aieng-agent-workflow.md](aieng-agent-workflow.md) | The reusable evidence-backed agent workflow |
| [package_contract.md](package_contract.md) | What a `.aieng` package is and guarantees |
| [aieng-package-handoff.md](aieng-package-handoff.md) | Handing a package to someone else |
| [cae-credibility-ladder.md](cae-credibility-ladder.md) | "a file exists" vs "a solver ran" vs "an engineer can rely on it" |
| [cae-deck-assembly-contract.md](cae-deck-assembly-contract.md) | The CalculiX deck-generation boundary |
| [cad-cae-value-demo.md](cad-cae-value-demo.md) | The canonical reproducible value demo (#368) |
| [demo-vertical-cae-workflow.md](demo-vertical-cae-workflow.md) | End-to-end agent-run CAE lifecycle demo |
| [canonical_engineering_scenarios.md](canonical_engineering_scenarios.md) | The adoption-first scenario catalog |
| [parametric-edit-governance.md](parametric-edit-governance.md) | CAD edits as reviewable engineering changes |
| [cad_execution_boundary.md](cad_execution_boundary.md) | What runs locally, and the execution sandbox |
| [adapter_capability_preflight_contract.md](adapter_capability_preflight_contract.md) | How local tool availability is reported before a run |
| [runtime_and_agents.md](runtime_and_agents.md) | Runtime lifecycle, REST API, event model, approval gates |
| [system_architecture.md](system_architecture.md) | The three sibling repositories and their responsibilities |
| [repo_boundaries.md](repo_boundaries.md) | Who owns what across the repositories |
| [phase1_authoring_pipeline.md](phase1_authoring_pipeline.md) | The `.aieng` authoring pipeline |
| [project-timeline.md](project-timeline.md) | The workbench's read-only project timeline panel |
| [review-handoff-workflow.md](review-handoff-workflow.md) | Local-first review handoff |
| [workbench_ui_comfort_benchmark.md](workbench_ui_comfort_benchmark.md) | The product UX benchmark the UI is held to |
| [roadmap.md](roadmap.md) | The live workspace-level roadmap |

## Historical — kept for context, do not build from

Each carries a banner at the top saying so. They record what was intended at a
point in time; where they disagree with `AGENTS.md`, `AGENTS.md` is right.

| Doc | Why it is historical |
|---|---|
| [project-direction-review-2026-09-06.md](project-direction-review-2026-09-06.md) | The owner's direction review — a dated assessment, not a contract |
| [freecad-action-agent-mvp.md](freecad-action-agent-mvp.md) | The FreeCAD action agent and its Pilot Console; both removed in the MCP-first cutover |
| [copilot_direction_curated_plan.md](copilot_direction_curated_plan.md) | 2026-05-19 execution plan, written around the old UI |
| [strategic_analysis_aieng_copilot_2026.md](strategic_analysis_aieng_copilot_2026.md) | 2026-05-19 strategy assessment |
| [text-to-cad-learning-roadmap.md](text-to-cad-learning-roadmap.md) | A contributor learning path, last updated 2026-05-27 |
| [phase-32-roadmap-recommendation.md](phase-32-roadmap-recommendation.md) | Superseded; the recommendation was acted on |
| [new_features_summary.md](new_features_summary.md) | A dated capability snapshot (2026-06) |
| [ui-ux-audit-workbench.md](ui-ux-audit-workbench.md) | A dated UI audit (2026-07-01) |

## Removed

- **`cad_adapter_strategy.md`** — deleted rather than marked. It stated that
  "FreeCAD is the first and currently only connected CAD backend", which is not
  a stale emphasis but a false statement about the runtime, and
  `aieng-ui/README.md` pointed newcomers at it as the guide to *adding a CAD
  backend*. The provider boundary that does exist is described in
  [`../AGENTS.md`](../AGENTS.md) (Workspace layout, and the CAD execution
  sections).
