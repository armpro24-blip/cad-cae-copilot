import * as vscode from "vscode";

import { backendUrl } from "./livePreview";
import { detectAgentMcp } from "./mcpStatus";

const HEALTH_TIMEOUT_MS = 3000;

type DoctorResult = {
  backendReachable: boolean;
  registryHash?: string;
  backendError?: string;
  hasMcpConfig: boolean;
  mcpSource?: string;
  workspaceFolder?: string;
};

async function fetchHealth(): Promise<{ ok: true; registryHash: string } | { ok: false; error: string }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
  try {
    const response = await fetch(`${backendUrl()}/api/health`, { signal: controller.signal });
    if (!response.ok) {
      return { ok: false, error: `HTTP ${response.status}` };
    }
    const data = (await response.json()) as Record<string, unknown>;
    const registryHash = typeof data.registry_hash === "string" ? data.registry_hash : "unknown";
    return { ok: true, registryHash };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, error: message };
  } finally {
    clearTimeout(timeout);
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
  const agentMcp = detectAgentMcp();
  const fallbackMcp = !agentMcp.configured && folder ? await findMcpConfig(folder.uri) : { hasConfig: false };
  const hasMcpConfig = agentMcp.configured || fallbackMcp.hasConfig;
  const mcpSource = agentMcp.configured ? agentMcp.sources.join(", ") : fallbackMcp.source;

  const result: DoctorResult = {
    backendReachable: health.ok,
    registryHash: health.ok ? health.registryHash : undefined,
    backendError: health.ok ? undefined : health.error,
    hasMcpConfig,
    mcpSource,
    workspaceFolder: folder?.uri.fsPath,
  };

  const lines: string[] = [];
  lines.push(`Backend ${health.ok ? "reachable" : "not reachable"} at ${backendUrl()}`);
  if (health.ok) {
    lines.push(`Registry hash: ${health.registryHash}`);
  } else {
    lines.push(`Error: ${health.error}`);
  }
  lines.push(`MCP config: ${hasMcpConfig ? (mcpSource ?? "found") : "not found in this workspace"}`);

  const status = health.ok ? "info" : "warning";
  const message = lines.join("\n");
  if (status === "info") {
    await vscode.window.showInformationMessage("AIENG Doctor", { detail: message, modal: false }, "OK");
  } else {
    const choice = await vscode.window.showWarningMessage(
      "AIENG Doctor",
      { detail: `${message}\n\nStart the backend or check aieng.backendUrl in settings.`, modal: false },
      "Open Settings",
    );
    if (choice === "Open Settings") {
      await vscode.commands.executeCommand("workbench.action.openSettings", "aieng.backendUrl");
    }
  }

  return result;
}
