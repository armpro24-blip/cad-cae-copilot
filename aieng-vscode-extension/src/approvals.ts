import { EventSource } from "eventsource";
import * as vscode from "vscode";

import {
  decisionBody,
  parseApprovalEvent,
  parseApprovalResolvedId,
  truncatePreview,
  type ApprovalRequest,
} from "./approvalModel";
import { backendUrl } from "./livePreview";

export type { ApprovalRequest };

async function postApprovalDecision(
  permissionId: string,
  approved: boolean,
  projectId?: string,
  message?: string,
): Promise<void> {
  const response = await fetch(
    `${backendUrl()}/api/agent/agentic/permission/${encodeURIComponent(permissionId)}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(decisionBody(approved, projectId, message)),
    },
  );
  if (!response.ok) throw new Error(`resolve failed: ${response.status}`);
}

export class ApprovalCoordinator implements vscode.Disposable {
  private source: EventSource | undefined;
  private readonly seen = new Set<string>();
  private readonly subscriptions: vscode.Disposable[] = [];
  private disposed = false;

  constructor() {
    this.connect();
    this.subscriptions.push(
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration("aieng.backendUrl")) this.reconnect();
      }),
    );
  }

  dispose(): void {
    this.disposed = true;
    this.source?.close();
    this.source = undefined;
    this.subscriptions.splice(0).forEach((item) => item.dispose());
  }

  private connect(): void {
    if (this.disposed) return;
    try {
      const source = new EventSource(`${backendUrl()}/api/agent-activity/stream`);
      source.onmessage = (message) => this.handle(message.data);
      this.source = source;
    } catch (error) {
      console.warn("AIENG: could not subscribe to backend for approvals.", error);
    }
  }

  private reconnect(): void {
    this.source?.close();
    this.source = undefined;
    this.seen.clear();
    this.connect();
  }

  private handle(data: unknown): void {
    const resolvedId = parseApprovalResolvedId(data);
    if (resolvedId) {
      // Resolved elsewhere (e.g. the web viewer) — drop it so we don't re-prompt.
      this.seen.delete(resolvedId);
      return;
    }
    const request = parseApprovalEvent(data);
    if (!request || this.seen.has(request.permissionId)) return;
    this.seen.add(request.permissionId);
    void this.prompt(request);
  }

  private async prompt(request: ApprovalRequest): Promise<void> {
    const detail = request.codePreview
      ? `${request.explanation}\n\n${truncatePreview(request.codePreview)}`
      : request.explanation;
    const choice = await vscode.window.showInformationMessage(
      `AIENG approval: ${request.toolName}`,
      { modal: true, detail },
      "Approve",
      "Deny",
    );
    // No explicit Approve → deny (fail-safe; a dismissed modal never auto-allows).
    const approved = choice === "Approve";
    try {
      await postApprovalDecision(
        request.permissionId,
        approved,
        request.projectId,
        approved ? undefined : "Denied in VS Code.",
      );
      vscode.window.setStatusBarMessage(
        `AIENG: ${approved ? "Approved" : "Denied"} ${request.toolName}`,
        2500,
      );
    } catch (error) {
      vscode.window.showErrorMessage(
        `AIENG: could not submit approval decision: ${error instanceof Error ? error.message : String(error)}`,
      );
      // Allow a retry if the next matching event arrives.
      this.seen.delete(request.permissionId);
    }
  }
}
