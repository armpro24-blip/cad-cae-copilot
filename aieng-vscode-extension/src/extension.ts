import * as vscode from "vscode";

import { AgentActivitySubscriber, type ProjectActivityEvent } from "./agentActivity";
import { ApprovalCoordinator } from "./approvals";
import { BackendManager } from "./backendManager";
import { runDoctor } from "./doctor";
import { HomePanel } from "./homePanel";
import { backendUrl, chooseLiveProject, fetchAdvisoryNextActions, listProjects, type Project } from "./livePreview";
import { formatActionDetail, parseNextActions, toHandoffPrompt, toToolCallSnippet } from "./nextActions";
import { readAiengPackage } from "./packageReader";
import { modifyPrompt, projectContextPrompt, starterPrompt } from "./prompts";
import type { WebviewToHostMessage } from "./protocol";
import { bindWebviewMessages, configureLiveWorkbenchWebview, configureWebview, postMessage } from "./webview";

class AiengDocument implements vscode.CustomDocument {
  constructor(readonly uri: vscode.Uri) {}
  dispose(): void {}
}

class AiengPreviewProvider implements vscode.CustomReadonlyEditorProvider<AiengDocument> {
  constructor(private readonly extensionUri: vscode.Uri) {}

  openCustomDocument(uri: vscode.Uri): AiengDocument {
    return new AiengDocument(uri);
  }

  async resolveCustomEditor(document: AiengDocument, panel: vscode.WebviewPanel): Promise<void> {
    configureWebview(panel.webview, this.extensionUri);
    const subscriptions: vscode.Disposable[] = [];
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        await postMessage(panel.webview, await readAiengPackage(document.uri));
      } catch (error) {
        await postMessage(panel.webview, {
          kind: "status",
          tone: "error",
          detail: error instanceof Error ? error.message : String(error),
        });
      } finally {
        loading = false;
      }
    };
    bindWebviewMessages(panel.webview, subscriptions, refresh, refresh);

    const watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(vscode.Uri.joinPath(document.uri, ".."), document.uri.path.split("/").pop() ?? "*.aieng"),
    );
    subscriptions.push(watcher);
    subscriptions.push(watcher.onDidChange(refresh));
    subscriptions.push(watcher.onDidCreate(refresh));
    subscriptions.push(panel.onDidDispose(() => subscriptions.splice(0).forEach((item) => item.dispose())));
  }
}

class LivePreviewPanel {
  private static current: LivePreviewPanel | undefined;
  private static opening: Promise<void> | undefined;

  /** The project of the open live preview, if any — lets commands target it. */
  static activeProject(): Project | undefined {
    const current = LivePreviewPanel.current;
    return current ? { id: current.projectId, name: current.projectName } : undefined;
  }

  static async handleActivity(extensionUri: vscode.Uri, backend: BackendManager, event: ProjectActivityEvent): Promise<void> {
    const current = LivePreviewPanel.current;
    if (current?.projectId === event.projectId) {
      current.refreshFromActivity(event);
      return;
    }
    // Keep the current project stable for read-only/background activity, but
    // follow a different project once it actually publishes updated geometry.
    if (current && !isProjectSwitchEvent(event)) return;
    if (LivePreviewPanel.opening) {
      await LivePreviewPanel.opening;
      const opened = LivePreviewPanel.current;
      if (opened?.projectId === event.projectId) {
        opened.refreshFromActivity(event);
      } else if (!opened || isProjectSwitchEvent(event)) {
        await LivePreviewPanel.open(extensionUri, backend, await projectFromActivity(event));
      }
      return;
    }
    LivePreviewPanel.opening = (async () => {
      await LivePreviewPanel.open(extensionUri, backend, await projectFromActivity(event));
    })();
    try {
      await LivePreviewPanel.opening;
    } finally {
      LivePreviewPanel.opening = undefined;
    }
  }

  static async open(
    extensionUri: vscode.Uri,
    backend: BackendManager,
    project?: Project,
  ): Promise<void> {
    const selectedProject = project ?? await chooseLiveProject();
    if (!selectedProject) return;
    if (LivePreviewPanel.current) {
      if (LivePreviewPanel.current.projectId !== selectedProject.id) {
        LivePreviewPanel.current.panel.dispose();
      } else {
        LivePreviewPanel.current.panel.reveal(vscode.ViewColumn.Beside);
        return;
      }
    }
    const panel = vscode.window.createWebviewPanel(
      "aieng.liveCadPreview",
      `AIENG: ${selectedProject.name || selectedProject.id}`,
      vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true },
    );
    LivePreviewPanel.current = new LivePreviewPanel(panel, extensionUri, backend, selectedProject.id, selectedProject.name || selectedProject.id);
  }

  private readonly subscriptions: vscode.Disposable[] = [];
  private refreshTimer: ReturnType<typeof setTimeout> | undefined;

  private constructor(
    readonly panel: vscode.WebviewPanel,
    extensionUri: vscode.Uri,
    private readonly backend: BackendManager,
    private readonly projectId: string,
    private readonly projectName: string,
  ) {
    // The live panel embeds the backend-served React workbench in an iframe, so
    // the model, panels, face-picking, and live refresh come from the single SPA
    // (no bespoke viewer, no payload plumbing). The shell only relays handoff.
    configureLiveWorkbenchWebview(panel.webview, backendUrl(), projectId, projectName);
    bindWebviewMessages(
      panel.webview,
      this.subscriptions,
      () => undefined,
      () => undefined,
      (message) => this.handlePreviewAction(message, extensionUri),
    );
    this.subscriptions.push(panel.onDidDispose(() => {
      if (this.refreshTimer) clearTimeout(this.refreshTimer);
      this.subscriptions.splice(0).forEach((item) => item.dispose());
      if (LivePreviewPanel.current === this) LivePreviewPanel.current = undefined;
    }));
  }

  private refreshFromActivity(event: ProjectActivityEvent): void {
    if (event.projectId !== this.projectId || !["project_changed", "viewer_asset_changed"].includes(event.type)) return;
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = undefined;
      void this.panel.webview.postMessage({ kind: "refreshLivePreview" });
    }, 300);
  }

  private async handlePreviewAction(message: WebviewToHostMessage, extensionUri: vscode.Uri): Promise<void> {
    if (message.kind === "copyStarterPrompt") {
      await this.copyPrompt(starterPrompt({ projectId: this.projectId, projectName: this.projectName }));
      return;
    }
    if (message.kind === "copyModifyPrompt") {
      await this.copyPrompt(modifyPrompt({ projectId: this.projectId, projectName: this.projectName, pointers: message.pointers }));
      return;
    }
    if (message.kind === "copyProjectContext") {
      await this.copyPrompt(projectContextPrompt({ projectId: this.projectId, projectName: this.projectName }));
      return;
    }
    if (message.kind === "openHome") {
      HomePanel.open(extensionUri, {
        openLiveProject: (project) => LivePreviewPanel.open(extensionUri, this.backend, project),
        openPackage,
        backend: this.backend,
      });
    }
  }

  private async copyPrompt(text: string): Promise<void> {
    await vscode.env.clipboard.writeText(text);
    vscode.window.setStatusBarMessage("AIENG: Copied agent handoff prompt", 2500);
  }
}

function isProjectSwitchEvent(event: ProjectActivityEvent): boolean {
  return event.type === "viewer_asset_changed";
}

async function projectFromActivity(event: ProjectActivityEvent): Promise<Project> {
  const fallback = { id: event.projectId, name: event.projectId };
  try {
    return (await listProjects()).find((item) => item.id === event.projectId) ?? fallback;
  } catch {
    // The SSE event is authoritative enough to open by id if project listing fails.
    return fallback;
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const backend = new BackendManager();
  context.subscriptions.push(backend);
  context.subscriptions.push(new AgentActivitySubscriber(
    (event) => LivePreviewPanel.handleActivity(context.extensionUri, backend, event),
  ));
  // In-editor approval surface (#230): resolve workbench-managed approvals via a
  // native modal so agentic-session mutations can be approved without leaving VS Code.
  context.subscriptions.push(new ApprovalCoordinator());
  const provider = new AiengPreviewProvider(context.extensionUri);
  context.subscriptions.push(vscode.window.registerCustomEditorProvider("aieng.cadPreview", provider, {
    supportsMultipleEditorsPerDocument: false,
  }));
  context.subscriptions.push(vscode.commands.registerCommand("aieng.openLivePreview", () => LivePreviewPanel.open(context.extensionUri, backend)));
  context.subscriptions.push(vscode.commands.registerCommand("aieng.openPackagePreview", openPackage));
  context.subscriptions.push(vscode.commands.registerCommand("aieng.openHome", () => HomePanel.open(context.extensionUri, {
    openLiveProject: (project) => LivePreviewPanel.open(context.extensionUri, backend, project),
    openPackage,
    backend,
  })));
  context.subscriptions.push(vscode.commands.registerCommand("aieng.doctor", () => void runDoctor()));
  context.subscriptions.push(vscode.commands.registerCommand("aieng.copyNextActions", () => void copyNextActionHandoff()));
}

export function deactivate(): void {}

/**
 * Surface advisory operation receipts / `next_actions` as copy-only quick actions
 * (#341). Reads the project's read-only `cae.prepare_solver_run` preflight, shows the
 * advisory actions with their safety flags and blocked status preserved, and copies a
 * tool-call snippet or a natural-language handoff prompt. Never executes a tool.
 */
async function copyNextActionHandoff(): Promise<void> {
  const project = LivePreviewPanel.activeProject() ?? await chooseLiveProject();
  if (!project) return;

  let response: unknown;
  try {
    response = await fetchAdvisoryNextActions(project.id);
  } catch (error) {
    vscode.window.showErrorMessage(
      `AIENG: Could not load next actions — ${error instanceof Error ? error.message : String(error)}`,
    );
    return;
  }

  const actions = parseNextActions(response);
  if (!actions.length) {
    vscode.window.showInformationMessage("AIENG: No advisory next actions available for this project yet.");
    return;
  }

  const picked = await vscode.window.showQuickPick(
    actions.map((action) => ({
      label: `${action.availableNow ? "$(check)" : "$(error)"} ${action.label}`,
      description: action.tool,
      detail: formatActionDetail(action),
      action,
    })),
    { placeHolder: "Advisory next actions — copy only, nothing runs", matchOnDetail: true },
  );
  if (!picked) return;

  const format = await vscode.window.showQuickPick(
    [
      { label: "$(json) Copy tool-call snippet", copyAs: "snippet" as const },
      { label: "$(comment) Copy natural-language handoff prompt", copyAs: "prompt" as const },
    ],
    { placeHolder: `Copy "${picked.action.label}" as…` },
  );
  if (!format) return;

  const text = format.copyAs === "snippet" ? toToolCallSnippet(picked.action) : toHandoffPrompt(picked.action);
  await vscode.env.clipboard.writeText(text);
  vscode.window.setStatusBarMessage("AIENG: Copied next-action handoff", 2500);
}

async function openPackage(): Promise<void> {
  const selected = await vscode.window.showOpenDialog({
    canSelectMany: false,
    filters: { "AIENG package": ["aieng"] },
  });
  if (selected?.[0]) await vscode.commands.executeCommand("vscode.openWith", selected[0], "aieng.cadPreview");
}
