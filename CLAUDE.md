# Claude Code — aieng Workspace

The full agent guide is in [AGENTS.md](AGENTS.md) — read it before acting.

@AGENTS.md

## TL;DR
- Drive the workbench via the **`aieng-workbench` MCP server** (configured in
  `.mcp.json` in this repo — auto-loaded by Claude Code).
- First calls: `aieng.agent_readme` → `aieng.list_projects` → `aieng.agent_context`.
- Do NOT read `aieng/src/` for capabilities, and do NOT run code to diagnose the
  backend. Use the MCP tools.
- In `aieng-ui/frontend/`, keep `App.tsx` lightweight and modular. Split large
  responsibilities into focused hooks/components, preserve UI behavior during
  refactors, and remove confirmed-dead frontend code instead of accumulating
  another monolith.
- **Before reviewing a PR — including your own, before opening it — read
  `aieng.guide {topic: "review"}`.** It names the six defect patterns this
  codebase actually produces, each with the question that detects it and the
  measured instance behind it. Every one of them shipped through green CI.
