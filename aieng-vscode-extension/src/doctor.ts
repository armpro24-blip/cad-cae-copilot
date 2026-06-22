import * as vscode from "vscode";

import { backendUrl } from "./livePreview";

const HEALTH_TIMEOUT_MS = 3000;

type DoctorResult = {
  backendReachable: boolean;
  backendIdentity?: string;
  backendError?: string;
  hasMcpConfig: boolean;
  mcpSource?: string;
  workspaceFolder?: string;
};

async function fetchHealth(): Promise<{ ok: true; identity: string } | { ok: false; error: string }> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
    const response = await fetch(`${backendUrl()}/api/health`, { signal: controller.signal });
    clearTimeout(timeout);
    if (!response.ok) {
      return { ok: false, error: `HTTP ${response.status}` };
    }
    const data = (await response.json()) as Record<string, unknown>;
    const identity = typeof data.registry_identity === "string" ? data.registry_identity : "unknown";
    return { ok: true, identity };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, error: message };
  }
}

async function findMcpConfig(workspaceUri: vscode.Uri): Promise<{ hasConfig: boolean; source?: string }> {
  const vscodeDir = vscode.Uri.joinPath(workspaceUri, ".vscode");
  const mcpJson = vscode.Uri.joinPath(vscodeDir, "mcp.json");
  const settingsJson = vscode.Uri.joinPath(vscodeDir, "settings.json");
  try {
    await vscode.workspace.fs.stat(mcpJson);
    return { hasConfig: true, source: ".vscode/mcp.json" };
  } catch {
    // fall through
  }
  try {
    const content = await vscode.workspace.fs.readFile(settingsJson);
    const text = Buffer.from(content).toString("utf-8");
    if (text.includes("mcp") && text.includes("servers")) {
      return { hasConfig: true, source: ".vscode/settings.json" };
    }
  } catch {
    // fall through
  }
  return { hasConfig: false };
}

export async function runDoctor(): Promise<DoctorResult> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  const health = await fetchHealth();

  let mcp: { hasConfig: boolean; source?: string } = { hasConfig: false };
  if (folder) {
    mcp = await findMcpConfig(folder.uri);
  }

  const result: DoctorResult = {
    backendReachable: health.ok,
    backendIdentity: health.ok ? health.identity : undefined,
    backendError: health.ok ? undefined : health.error,
    hasMcpConfig: mcp.hasConfig,
    mcpSource: mcp.source,
    workspaceFolder: folder?.uri.fsPath,
  };

  const lines: string[] = [];
  lines.push(`Backend ${health.ok ? "reachable" : "not reachable"} at ${backendUrl()}`);
  if (health.ok) {
    lines.push(`Registry identity: ${health.identity}`);
  } else {
    lines.push(`Error: ${health.error}`);
  }
  lines.push(`MCP config: ${mcp.hasConfig ? (mcp.source ?? "found") : "not found in .vscode"}`);

  const status = health.ok ? "info" : "warning";
  const message = lines.join("\n");
  if (status === "info") {
    await vscode.window.showInformationMessage("AIENG Doctor", { detail: message, modal: false }, "OK");
  } else {
    await vscode.window.showWarningMessage(
      "AIENG Doctor",
      { detail: `${message}\n\nStart the backend or check aieng.backendUrl in settings.`, modal: false },
      "Open Settings",
    );
  }

  return result;
}
