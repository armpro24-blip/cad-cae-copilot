import type { HomeProject, HomeStateMessage, HomeToWebviewMessage, HomeWebviewMessage } from "../src/protocol";
import { buildHomeHandoff } from "../src/homeHandoff";

declare function acquireVsCodeApi(): { postMessage(message: HomeWebviewMessage): void };
const vscode = acquireVsCodeApi();

const style = document.createElement("style");
style.textContent = `
:root {
  color-scheme: light dark;
  --line: var(--vscode-panel-border, rgba(128,128,128,0.25));
  --panel: var(--vscode-editorWidget-background, var(--vscode-input-background, transparent));
  --soft: color-mix(in srgb, var(--vscode-editorWidget-background, var(--vscode-editor-background)) 72%, transparent);
  --muted: var(--vscode-descriptionForeground);
  --focus: var(--vscode-focusBorder);
  --ok: var(--vscode-testing-iconPassed, var(--vscode-charts-green, var(--vscode-foreground)));
  --warn: var(--vscode-testing-iconQueued, var(--vscode-charts-yellow, var(--vscode-descriptionForeground)));
  --bad: var(--vscode-testing-iconFailed, var(--vscode-errorForeground, var(--vscode-descriptionForeground)));
}
* { box-sizing:border-box; }
html,body,#app { min-height:100%; margin:0; }
body { font-family:var(--vscode-font-family); font-size:var(--vscode-font-size,13px); color:var(--vscode-foreground); background:var(--vscode-editor-background); }
button { font:inherit; color:inherit; cursor:pointer; }
button:focus-visible { outline:1px solid var(--focus); outline-offset:2px; }
code { font-family:var(--vscode-editor-font-family,monospace); }
.page { width:min(100%,900px); margin:0 auto; padding:16px; display:grid; gap:12px; }
.busy { opacity:.6; pointer-events:none; }
.hero { display:grid; gap:6px; padding:0; }
.hero-top { display:flex; justify-content:space-between; align-items:center; gap:12px; min-width:0; }
.eyebrow { font-size:10px; letter-spacing:0; text-transform:uppercase; color:var(--muted); }
.mode { color:var(--muted); font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
h1 { margin:0; font-size:18px; line-height:1.22; font-weight:600; letter-spacing:0; }
p { margin:0; color:var(--muted); line-height:1.45; }
.hero p { max-width:760px; font-size:12px; }
.layout { display:grid; grid-template-columns:minmax(0,1fr) 240px; gap:12px; align-items:start; }
.main-stack, .side-stack { display:grid; gap:12px; min-width:0; }
.panel { border:1px solid var(--line); border-radius:0; background:var(--panel); }
.steps { display:grid; padding:0 12px; }
.step { display:grid; grid-template-columns:20px minmax(0,1fr); gap:10px; align-items:start; padding:11px 0; border-top:1px solid var(--line); }
.step:first-child { border-top:0; }
.step-n { width:20px; height:20px; border-radius:0; display:grid; place-content:center; font-size:11px; color:var(--muted); border:1px solid var(--line); background:var(--vscode-editor-background); }
.step-body { display:grid; gap:4px; min-width:0; }
.step-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.step-head strong { font-size:12px; font-weight:600; }
.step-body span { color:var(--muted); font-size:11px; line-height:1.45; overflow-wrap:anywhere; }
.step-body code { font-family:var(--vscode-editor-font-family,monospace); }
.step-actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:3px; }
.status { display:inline-flex; align-items:center; gap:4px; font-size:11px; color:var(--muted); }
.status .ok { color:var(--ok); }
.btn { border:1px solid var(--vscode-button-border,transparent); border-radius:0; background:var(--vscode-button-secondaryBackground,rgba(128,128,128,0.14)); color:var(--vscode-button-secondaryForeground,var(--vscode-foreground)); padding:5px 10px; font-size:12px; }
.btn:hover { background:var(--vscode-button-secondaryHoverBackground,rgba(128,128,128,0.22)); }
.btn.primary { background:var(--vscode-button-background); color:var(--vscode-button-foreground); }
.btn.primary:hover { background:var(--vscode-button-hoverBackground); }
.section-title { font-size:10px; letter-spacing:0; text-transform:uppercase; color:var(--muted); margin-bottom:6px; }
.projects { display:flex; flex-direction:column; gap:6px; }
.project { border:1px solid var(--line); border-radius:0; padding:10px; display:grid; gap:6px; background:transparent; }
.project:hover { border-color:var(--vscode-focusBorder); }
.project strong { font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.project span { color:var(--muted); font-size:11px; overflow-wrap:anywhere; }
.row { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
.project-head { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:center; }
.badge { font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:0; color:var(--vscode-descriptionForeground); background:transparent; border:1px solid var(--line); border-radius:0; padding:1px 5px; }
.mini { border:1px solid var(--vscode-button-border,transparent); border-radius:0; background:var(--vscode-button-secondaryBackground,rgba(128,128,128,0.14)); color:var(--vscode-button-secondaryForeground,var(--vscode-foreground)); padding:4px 8px; font-size:11px; }
.mini:hover { background:var(--vscode-button-secondaryHoverBackground,rgba(128,128,128,0.22)); }
.handoff { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:center; padding:12px; border:1px solid var(--line); border-radius:0; background:var(--panel); }
.handoff strong { font-size:12px; }
.handoff p { font-size:11px; }
.handoff-actions { display:grid; gap:6px; min-width:140px; }
.handoff-actions .btn { width:100%; }
.readiness { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
.readiness-card { display:grid; gap:5px; min-height:72px; padding:9px; border:1px solid var(--line); border-radius:0; background:transparent; }
.readiness-top { display:flex; justify-content:space-between; align-items:center; gap:8px; min-width:0; }
.readiness-card strong { font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.readiness-detail { color:var(--muted); font-size:10px; line-height:1.35; overflow:hidden; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; }
.state-pill { display:inline-flex; flex:0 0 auto; border:1px solid var(--line); border-radius:0; padding:1px 5px; font-size:10px; line-height:1.4; color:var(--muted); background:transparent; }
.readiness-card.ready { border-color:var(--line); }
.readiness-card.ready .state-pill { color:var(--ok); border-color:var(--line); }
.readiness-card.blocked { border-color:var(--line); }
.readiness-card.blocked .state-pill { color:var(--bad); border-color:var(--line); }
.readiness-card.missing { border-style:dashed; }
.readiness-card.missing .state-pill { color:var(--warn); }
.readiness-card.checking { opacity:.76; }
.empty { border:1px dashed var(--line); border-radius:0; padding:12px; color:var(--muted); font-size:11px; }
.quick-actions { display:grid; gap:6px; }
.quick-actions .btn { width:100%; text-align:left; }
.boundary-panel { padding:10px; display:grid; gap:5px; }
.boundary-panel .section-title { margin-bottom:1px; }
.boundary-panel p { font-size:11px; }
.toast { position:fixed; left:50%; bottom:16px; transform:translateX(-50%); background:var(--vscode-editorWidget-background); color:var(--vscode-foreground); border:1px solid var(--line); border-radius:0; padding:6px 10px; font-size:11px; opacity:0; transition:opacity .18s; max-width:min(560px,calc(100vw - 32px)); }
.toast.show { opacity:1; }
@media (max-width:760px) {
  .layout { grid-template-columns:1fr; }
  .readiness { grid-template-columns:1fr; }
  .handoff { grid-template-columns:1fr; }
  .handoff-actions { display:flex; flex-wrap:wrap; gap:6px; min-width:0; }
  .handoff-actions .btn { width:auto; flex:1 1 120px; }
}
@media (max-width:560px) {
  .page { padding:12px; }
  .hero-top { align-items:start; flex-direction:column; }
  .project-head { grid-template-columns:1fr; align-items:start; }
  .handoff-actions .btn { width:100%; flex:1 1 100%; }
}
`;
document.head.appendChild(style);

const app = document.querySelector<HTMLElement>("#app")!;
let currentState: HomeStateMessage | null = null;
let busy = false;

function send(message: HomeWebviewMessage): void {
  vscode.postMessage(message);
}

function render(): void {
  const state = currentState;
  const checking = !state;
  const connected = state?.status === "connected";
  const backend = state?.backendUrl ?? "http://127.0.0.1:8000";
  const projects = state?.projects ?? [];
  const backendMode = state?.backendMode ?? "stopped";
  const mcp = state?.agentMcp;
  const handoff = buildHomeHandoff(state);

  const backendBadge = checking ? `<span class="status">checking...</span>`
    : connected ? `<span class="status"><span class="ok">running</span></span>`
    : `<span class="status">stopped</span>`;
  const backendCtl = connected
    ? (backendMode === "managed"
      ? `<button class="btn" data-action="stop-backend">Stop backend</button>`
      : "")
    : `<button class="btn primary" data-action="start-backend">Start backend</button><button class="btn" data-action="retry">Retry</button>`;
  const backendDesc = connected
    ? `Connected - ${escapeHtml(backend)}${backendMode === "managed" ? " - managed by this extension" : backendMode === "external" ? " - existing backend" : ""}`
    : `AIENG needs its backend running to build and store CAD - ${escapeHtml(backend)}`;

  const mcpReady = !!mcp?.configured;
  const mcpBadge = !mcp ? `<span class="status">checking...</span>`
    : mcpReady ? `<span class="status"><span class="ok">ready</span></span>`
    : `<span class="status">not found</span>`;
  const mcpDesc = mcpReady
    ? escapeHtml(mcp!.detail)
    : `Your agent (Claude Code / Codex) needs the AIENG tools. Add the <b>aieng-workbench</b> MCP server - see <code>.mcp.json</code> in the repo root - then reopen your agent.`;

  app.innerHTML = `<div class="page ${busy ? "busy" : ""}">
    <section class="hero">
      <div class="hero-top">
        <div class="eyebrow">AIENG Home</div>
        <div class="mode">VS Code launch and agent handoff</div>
      </div>
      <h1>Start cleanly. Hand off with evidence.</h1>
      <p>Use this panel to verify backend and MCP readiness, pick the current project, and copy a bounded agent prompt. Detailed 3D review, approval, simulation status, and evidence stay in the Web Workbench.</p>
    </section>

    <section class="readiness" aria-label="AIENG readiness">
      ${renderReadinessCard(handoff.backend.label, handoff.backend.detail, handoff.backend.state)}
      ${renderReadinessCard(handoff.mcp.label, handoff.mcp.detail, handoff.mcp.state)}
      ${renderReadinessCard(handoff.project.label, handoff.project.detail, handoff.project.state)}
    </section>

    <section class="handoff" aria-label="Recommended next step">
      <div>
        <div class="section-title">Recommended next step</div>
        <strong>${escapeHtml(handoff.nextAction.label)}</strong>
        <p>${escapeHtml(handoff.nextAction.detail)}</p>
      </div>
      <div class="handoff-actions">
        ${handoff.project.selected ? `<button class="btn primary" data-action="open-project" data-project="${escapeAttr(handoff.project.selected.id)}">Open Workbench</button>` : ""}
        ${handoff.nextAction.prompt ? `<button class="btn" data-action="copy-home-prompt">Copy agent prompt</button>` : ""}
      </div>
    </section>

    <div class="layout">
      <div class="main-stack">
        <section class="steps panel" aria-label="Setup path">
          <div class="step">
            <div class="step-n">1</div>
            <div class="step-body">
              <div class="step-head"><strong>Backend</strong> ${backendBadge}</div>
              <span>${backendDesc}</span>
              ${backendCtl ? `<div class="step-actions">${backendCtl}</div>` : ""}
            </div>
          </div>
          <div class="step">
            <div class="step-n">2</div>
            <div class="step-body">
              <div class="step-head"><strong>Agent MCP</strong> ${mcpBadge}</div>
              <span>${mcpDesc}</span>
            </div>
          </div>
          <div class="step">
            <div class="step-n">3</div>
            <div class="step-body">
              <div class="step-head"><strong>Project entry</strong></div>
              <span>Create/import a project here, then hand the evidence-aware prompt to your MCP-capable agent.</span>
            </div>
          </div>
        </section>

        <section class="projects" aria-label="Recent projects">
          <div class="section-title">Recent projects</div>
          ${projects.length ? projects.map(renderProject).join("") : renderEmpty(connected ? "connected" : "unreachable")}
        </section>
      </div>

      <aside class="side-stack" aria-label="Quick actions">
        <section class="quick-actions">
          <div class="section-title">Project actions</div>
          <button class="btn primary" data-action="create">Start new project</button>
          <button class="btn" data-action="open-live">Open existing</button>
          <button class="btn" data-action="open-package">Open .aieng file</button>
        </section>
        <section class="panel boundary-panel">
          <div class="section-title">Boundary</div>
          <p>This page only starts, connects, opens, and copies prompts. Solver runs and claim changes remain approval-gated in the Workbench.</p>
        </section>
      </aside>
    </div>
    <div class="toast"></div>
  </div>`;
  bind();
}

function renderProject(project: HomeProject): string {
  const parts = project.namedParts.length ? `${project.namedParts.slice(0, 4).join(", ")}${project.namedParts.length > 4 ? "..." : ""}` : "No named parts yet";
  const updated = formatUpdated(project.updatedAt);
  return `<article class="project">
    <div class="project-head"><strong>${escapeHtml(project.name)}</strong><span class="badge">${escapeHtml(project.status ?? "unknown")}</span></div>
    <span>${escapeHtml(project.id)}${updated ? ` - ${escapeHtml(updated)}` : ""}</span>
    <span>${escapeHtml(parts)}</span>
    <div class="row">
      <button class="mini" data-action="open-project" data-project="${escapeAttr(project.id)}">Open workbench</button>
      <button class="mini" data-action="copy-project-prompt" data-project="${escapeAttr(project.id)}" data-name="${escapeAttr(project.name)}">Copy prompt</button>
    </div>
  </article>`;
}

function renderReadinessCard(label: string, detail: string, state: string): string {
  return `<article class="readiness-card ${escapeAttr(state)}">
    <div class="readiness-top">
      <strong>${escapeHtml(label)}</strong>
      <span class="state-pill">${escapeHtml(readinessLabel(state))}</span>
    </div>
    <span class="readiness-detail">${escapeHtml(detail)}</span>
  </article>`;
}

function readinessLabel(state: string): string {
  if (state === "ready") return "ready";
  if (state === "blocked") return "blocked";
  if (state === "missing") return "missing";
  return "checking";
}

function formatUpdated(value?: string): string {
  if (!value) return "";
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return "";
  const date = new Date(time);
  return `Updated ${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

function renderEmpty(status: string): string {
  if (status === "connected") {
    return `<div class="empty">No projects yet. Start a blank AIENG project, then ask your VS Code agent to generate the first CAD model.</div>`;
  }
  return `<div class="empty">Start the AIENG backend from this panel, or start your own backend separately and retry the connection.</div>`;
}

function bind(): void {
  app.querySelector<HTMLElement>('[data-action="create"]')?.addEventListener("click", () => send({ kind: "createProject" }));
  app.querySelector<HTMLElement>('[data-action="open-live"]')?.addEventListener("click", () => send({ kind: "openLiveProject" }));
  app.querySelector<HTMLElement>('[data-action="open-package"]')?.addEventListener("click", () => send({ kind: "openPackage" }));
  app.querySelector<HTMLElement>('[data-action="start-backend"]')?.addEventListener("click", () => send({ kind: "startBackend" }));
  app.querySelector<HTMLElement>('[data-action="stop-backend"]')?.addEventListener("click", () => send({ kind: "stopBackend" }));
  app.querySelector<HTMLElement>('[data-action="retry"]')?.addEventListener("click", () => send({ kind: "retry" }));
  app.querySelectorAll<HTMLElement>('[data-action="copy-home-prompt"]').forEach((element) => {
    element.addEventListener("click", () => {
      const prompt = buildHomeHandoff(currentState).nextAction.prompt;
      if (prompt) send({ kind: "copyHomePrompt", text: prompt });
    });
  });
  app.querySelectorAll<HTMLElement>("[data-project]").forEach((element) => {
    element.addEventListener("click", () => {
      const projectId = element.dataset.project ?? "";
      if (element.dataset.action === "open-project") send({ kind: "openLiveProject", projectId });
      if (element.dataset.action === "copy-project-prompt") {
        send({ kind: "copyStarterPrompt", projectId, projectName: element.dataset.name });
      }
    });
  });
}

function showToast(detail: string, tone: "info" | "error" = "info"): void {
  const toast = app.querySelector<HTMLElement>(".toast");
  if (!toast) return;
  toast.textContent = detail;
  toast.style.background = tone === "error" ? "var(--vscode-inputValidation-errorBackground, var(--vscode-errorForeground))" : "";
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 1600);
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!);
}

function escapeAttr(value: string): string {
  return escapeHtml(value);
}

window.addEventListener("message", (event: MessageEvent<HomeToWebviewMessage>) => {
  if (event.data.kind === "homeState") {
    currentState = event.data;
    render();
  }
  if (event.data.kind === "homeBusy") {
    busy = event.data.busy;
    render();
    if (event.data.detail) showToast(event.data.detail);
  }
  if (event.data.kind === "homeToast") showToast(event.data.detail, event.data.tone);
});

render();
send({ kind: "ready" });
