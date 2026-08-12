# GitHub Copilot — aieng Workspace

The full agent guide is the canonical [`AGENTS.md`](../AGENTS.md) at the workspace
root. Read it before acting.

## Essentials
- Drive the workbench via the **`aieng-workbench` MCP server** (configured in
  `.vscode/mcp.json` in this repo). If it is not in your tool list, connect to it
  before doing anything else.
- First calls: `aieng.agent_readme` → `aieng.list_projects` → `aieng.agent_context`.
- Do NOT browse `aieng/src/` to learn capabilities — it is a legacy `FakeBackend`.
  The real CAD engine is `cad.execute_build123d` (build123d / OpenCASCADE).
- Do NOT diagnose the backend with `pylanceRunCodeSnippet`, subprocess, or psutil.
  If the backend at `http://127.0.0.1:8000` is down, ask the user to start it
  (`conda activate aieng311 && uvicorn app.main:app --reload --port 8000` from `aieng-ui/backend`).
- **Before reviewing a PR — including your own, before opening it — read
  `aieng.guide {topic: "review"}`.** It names the six defect patterns this
  codebase actually produces, each with the question that detects it and the
  measured instance behind it. Every one of them shipped through green CI.
