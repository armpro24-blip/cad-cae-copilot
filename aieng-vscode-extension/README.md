# AIENG CAD Preview for VS Code

AIENG CAD Preview is the VS Code entry point for `aieng`: create or open an
AIENG project, inspect generated CAD, and copy agent-ready prompts or `@face:id`
pointers back into your existing VS Code agent chat.

You do **not** need an existing CAD file to begin. You can start from a blank
AIENG project and ask your agent to generate the first model.

## Quick start in 3 minutes

1. Install the extension from a local `.vsix` or build it from this folder.
2. In VS Code, run `AIENG: Open AIENG Home`.
3. Connect to a running AIENG backend. For users, the simplest path is the
   Docker all-in-one backend; contributors can also click `Start AIENG Backend`
   from a repo checkout.
4. Click `Start New Project`.
5. Click `Copy starter prompt` and paste it into your existing agent chat
   (Codex, Copilot Chat, Claude Code, or another MCP-capable agent).
6. When the agent generates CAD, the live preview refreshes.

You can also start the backend yourself before opening AIENG Home. If the
extension connects to an existing backend, it will not try to stop that process.

For the complete MCP-first workflow, including agent configuration and safety
boundaries, see
[`../docs/mcp-first-vscode-workflow.md`](../docs/mcp-first-vscode-workflow.md).

The extension subscribes to the backend agent-activity SSE stream after VS Code
starts. When an event includes a `project_id` and no Live CAD Preview is open,
the matching project preview opens automatically. If another project later
publishes a new viewer asset, the existing preview follows that project. The SSE
client keeps retrying when the backend starts after VS Code. Set
`aieng.autoOpenPreviewOnActivity` to `false` to disable this behavior.

## Approve agent mutations in-editor

When the workbench runs in **managed approval mode**
(`--approval-mode managed`, the backend owns approval), every gated agent
mutation — `cad.execute_build123d`, `cae.run_solver`, and the other
`[APPROVAL REQUIRED]` tools — is surfaced as a **native VS Code modal** with the
tool name and a code preview. Approve or Deny without leaving the editor; the
decision is posted straight back to the backend. Being subscribed also makes the
extension a connected approval surface, so managed-mode calls no longer fail fast
for want of a viewer. Dismissing the modal counts as **deny** — a gated mutation
is never auto-approved. (Other approval modes are unaffected: `client` lets your
agent prompt, `elicit` prompts a headless CLI client, `block` rejects mutations.)

## Backend lifecycle

AIENG CAD Preview can start a managed backend for the current workspace.

1. Run `AIENG: Open AIENG Home`.
2. Click `Start AIENG Backend` when the panel says the backend is not reachable.
3. Use `Stop Managed Backend` when you want to stop the process started by the
   extension.

The extension only stops the backend process it started itself. If you started
the backend manually, the extension treats it as an existing backend and leaves
it running.

The default managed-backend command is:

```powershell
conda run -n aieng311 --no-capture-output python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

It runs from `aieng-ui/backend` in the open workspace. If your environment is
different, change these VS Code settings:

- `aieng.backendCwd`
- `aieng.backendStartCommand`
- `aieng.backendStartArgs`

The default backend URL is:

```text
http://127.0.0.1:8000
```

Change `aieng.backendUrl` in VS Code settings if your backend runs elsewhere.

## Use with Docker, no repo checkout

The extension can connect to an already-running packaged AIENG backend. This is
the recommended path for users who installed the extension from a `.vsix` and do
not have a contributor checkout.

Start the all-in-one backend:

```powershell
docker run --rm -it -p 8000:8000 -p 8765:8765 -v aieng-data:/data ghcr.io/armpro24-blip/aieng-workbench:latest
```

Then in VS Code:

1. Set `aieng.backendUrl` to `http://127.0.0.1:8000`.
2. Run `AIENG: Open AIENG Home`.
3. Use `Start New Project` or open an existing project from the backend.

Do not use `Start AIENG Backend` in this Docker mode. That button is a
contributor convenience for starting a backend from `aieng-ui/backend` in a repo
checkout. The Docker container owns the backend process.

## Install it

### Option 1: Install from a local `.vsix`

If you already have a file such as `aieng-cad-preview-<version>.vsix`:

1. Open the Extensions view in VS Code.
2. Click the `...` menu in the Extensions panel.
3. Choose `Install from VSIX...`.
4. Select the `.vsix` file.
5. Reload VS Code if prompted.

On GitHub, the `VS Code extension (test + package + smoke)` CI job also
publishes a short-lived `aieng-vscode-extension-vsix` artifact for each
`main`/PR run.

### Option 2: Build and install from this repo

From `cad-cae-copilot/aieng-vscode-extension`:

```powershell
npm install
npm run vsix
$version = node -p "require('./package.json').version"
code --install-extension ".\aieng-cad-preview-$version.vsix"
```

Confirm installation by searching for `AIENG CAD Preview` in the Extensions
view. The extension ID is:

```text
aieng.aieng-cad-preview
```

## Start a new project

Run:

```text
AIENG: Open AIENG Home
```

Then click `Start New Project`. The extension will:

1. call the backend `POST /api/projects`,
2. create a blank AIENG project,
3. open a live CAD preview for that project,
4. show a blank-project state until the first model exists,
5. offer `Copy starter prompt` so your existing agent can generate the first
   model through the `aieng-workbench` MCP tools.

This is the normal zero-to-first-model flow. A blank preview is not an error.

## Open an existing `.aieng` package

Use either path:

- run `AIENG: Open .aieng CAD Preview`, or
- open a `.aieng` file in VS Code Explorer.

If VS Code opens it as a binary file, right-click the file and choose:

```text
Open With... -> AIENG CAD Preview
```

Local package mode reads the package without modifying it.

## Use it with your VS Code agent

This extension does not embed its own chat UI. It hands context to the agent you
already use in VS Code.

Available handoff actions:

- `Copy starter prompt` - ask an agent to generate the first model.
- `Copy modify prompt` - ask an agent to inspect and modify the current model.
- `Copy context` - copy the project id and backend URL.
- `Copy selected` - copy selected `@face:id` pointers after picking faces in
  the 3D preview.

Paste the copied text into Codex, Copilot Chat, Claude Code, or any other
MCP-capable agent configured for `aieng-workbench`.

## What you should expect to see

- GLB-backed projects show an interactive 3D model.
- If authoritative topology is available, clicking faces gives stable
  `@face:id` pointers.
- STL-only or topology-light previews still render, but face picking is disabled
  on purpose.
- Blank projects show `Project created` and prompt handoff actions until CAD is
  generated.

## Troubleshooting

Run `AIENG: Doctor — Check Backend and MCP Readiness` at any time. It reports:

- whether the configured backend is reachable and its registry identity,
- whether an MCP config exists in `.vscode/mcp.json` or `.vscode/settings.json`.

Common fixes:

- `Backend not reachable`: start the AIENG backend, then click retry in AIENG
  Home, or click `Start AIENG Backend`.
- `No projects yet`: click `Start New Project`.
- `No CAD preview yet`: paste the starter prompt into your agent chat and let
  the agent generate the first model.
- `Face pointers unavailable`: the preview is STL-only or the package does not
  include authoritative topology.
- Approvals do not appear in VS Code: the backend must be in managed approval
  mode (`--approval-mode managed`) and the extension must be able to connect to
  it. Use the Doctor command to verify connectivity.

## Development

```powershell
cd aieng-vscode-extension
npm install
npm run check
npm run build
```

To produce an installable package:

```powershell
npm run vsix
```
