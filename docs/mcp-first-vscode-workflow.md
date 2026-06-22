# MCP-first VS Code workflow

This is the recommended editor-first path for using AIENG with an MCP-capable
agent. The React workbench remains available, but the primary productized flow is
now:

```text
VS Code extension + AIENG backend + aieng-workbench MCP server + your agent
```

The extension gives the human a live preview, approval surface, project starter
actions, and copyable context. The agent still performs all CAD/CAE work through
the MCP tools.

## What this workflow does

- Starts from a blank AIENG project or an existing `.aieng` package.
- Lets a VS Code user inspect generated GLB/STL CAD.
- Copies project context, starter prompts, modify prompts, and `@face:*`
  pointers into an MCP-capable agent chat.
- Surfaces managed approval requests as native VS Code modals.
- Keeps solver execution approval-gated.
- Avoids making the WebUI a required part of the main workflow.

## Install the VS Code extension

Use one of these paths:

1. Download the `aieng-vscode-extension-vsix` artifact from the VS Code extension
   CI job and install it with `Extensions: Install from VSIX...`.
2. Build from source:

   ```powershell
   cd aieng-vscode-extension
   npm install
   npm run vsix
   $version = node -p "require('./package.json').version"
   code --install-extension ".\aieng-cad-preview-$version.vsix"
   ```

   The VSIX filename follows the version in
   `aieng-vscode-extension/package.json`. If you downloaded a CI artifact, use
   the `.vsix` file inside that artifact instead.

Marketplace publishing is not claimed yet. Until that exists, `.vsix` is the
install path.

## Start or connect to a backend

The extension connects to:

```text
http://127.0.0.1:8000
```

by default. Change `aieng.backendUrl` in VS Code settings if your backend runs
elsewhere.

Recommended packaged path:

```powershell
docker run --rm -it -p 8000:8000 -p 8765:8765 -v aieng-data:/data ghcr.io/armpro24-blip/aieng-workbench:latest
```

Contributor path from a checkout:

```powershell
cd aieng-ui/backend
conda run -n aieng311 --no-capture-output python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

You can also run `AIENG: Open AIENG Home` and click `Start AIENG Backend` when
working from a repository checkout. The extension only stops a backend process
it started itself.

## Configure the MCP agent

The VS Code extension is not a chat client and does not execute tools itself.
Configure your MCP-capable agent to use the `aieng-workbench` MCP server.

For VS Code / Copilot-compatible MCP clients, use `.vscode/mcp.json`:

```json
{
  "servers": {
    "aieng-workbench": {
      "type": "sse",
      "url": "http://localhost:8765/sse"
    }
  }
}
```

For local stdio or other clients, see
[`aieng-ui/backend/MCP_SETUP.md`](../aieng-ui/backend/MCP_SETUP.md).

After connecting, the agent should call:

```text
1. aieng.agent_readme
2. aieng.list_projects
3. aieng.agent_context { project_id }
```

Task-specific guide reads still apply. CAD tools require the CAD guide; CAE and
solver workflows require the CAE guide.

## Create the first model

1. Run `AIENG: Open AIENG Home`.
2. Click `Start New Project`.
3. Click `Copy starter prompt`.
4. Paste it into your agent chat.
5. Let the agent use MCP tools such as `cad.execute_build123d`.
6. Watch the Live CAD Preview refresh when geometry is produced.

A blank preview is normal before the first CAD model exists.

## Modify or inspect an existing model

Use the Live CAD Preview actions:

- `Copy modify prompt` for an agent-guided edit.
- `Copy context` for project id and backend URL.
- `Copy selected` after face picking to hand stable `@face:*` pointers to the
  agent.

Local `.aieng` package preview is read-only. Live backend project preview is the
path for agent-driven CAD/CAE work.

## Approval behavior

In managed approval mode, approval-gated operations surface in VS Code as native
modals. The user can approve or deny without leaving the editor.

Important safety boundaries:

- Dismissing the modal denies the request.
- The extension never auto-approves a CAD, package, claim, or solver action.
- `cae.run_solver` remains explicitly approval-gated.
- A readiness or preflight report does not mean the solver ran.
- Advisory next actions and receipts are hints, not execution.

For Docker/full viewer mode, use managed approval. For pure headless clients,
use client-managed approval or MCP elicitation as documented in
[`MCP_SETUP.md`](../aieng-ui/backend/MCP_SETUP.md).

## Setup checks

For MCP wiring, run the MCP server doctor from a terminal:

```powershell
aieng-workbench-mcp --doctor --backend-url http://127.0.0.1:8000
```

It reports:

- backend reachability when a backend URL is supplied,
- whether an AIENG MCP config is present in the current workspace,
- actionable setup hints when the backend is unreachable.

The doctor check is read-only. It does not start a backend, modify MCP config,
run CAD tools, or run a solver. If the VS Code extension also provides an
in-editor Doctor command, treat it as the same kind of read-only setup check.

## What is intentionally not covered yet

- Marketplace publication is not claimed.
- The extension is not a replacement for an MCP-capable agent.
- The WebUI is not required for the main path, though it can still be useful for
  local development and compatibility.
- Solver execution is never automatic.
- Generated CAD/CAE output is alpha review material, not certification or a
  production-safety claim.
