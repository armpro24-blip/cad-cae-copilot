/**
 * Pure data model for the in-editor approval surface (#230).
 *
 * This file contains no VS Code dependencies so it can be unit-tested in Node.
 */

export type ApprovalRequest = {
  permissionId: string;
  toolName: string;
  explanation: string;
  codePreview?: string;
  projectId?: string;
};

/** Parse an `approval_requested` SSE event into a request, or null. Pure. */
export function parseApprovalEvent(data: unknown): ApprovalRequest | null {
  if (typeof data !== "string") return null;
  let event: Record<string, unknown>;
  try {
    event = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (!event || typeof event !== "object" || event.type !== "approval_requested") return null;
  const payload = (event.payload && typeof event.payload === "object" ? event.payload : {}) as Record<string, unknown>;
  const permissionId = String(payload.agentic_permission_id ?? payload.id ?? "").trim();
  if (!permissionId) return null;
  const toolName = String(payload.tool_name ?? "a workbench tool");
  const explanation = String(payload.explanation ?? event.content ?? `Approve ${toolName}?`);
  const codePreview = typeof payload.code_preview === "string" ? payload.code_preview : undefined;
  const eventProject = typeof event.project_id === "string" ? event.project_id.trim() : "";
  const payloadProject = typeof payload.target_project_id === "string" ? payload.target_project_id.trim() : "";
  const projectId = eventProject || payloadProject || undefined;
  return { permissionId, toolName, explanation, codePreview, projectId };
}

/** Extract the permission id from an `approval_resolved` event, or null. Pure. */
export function parseApprovalResolvedId(data: unknown): string | null {
  if (typeof data !== "string") return null;
  try {
    const event = JSON.parse(data) as Record<string, unknown>;
    if (!event || event.type !== "approval_resolved") return null;
    const payload = (event.payload && typeof event.payload === "object" ? event.payload : {}) as Record<string, unknown>;
    const id = String(payload.agentic_permission_id ?? "").trim();
    return id || null;
  } catch {
    return null;
  }
}

/** Build the resolve POST body. Pure. */
export function decisionBody(approved: boolean, projectId?: string, message?: string): Record<string, unknown> {
  const body: Record<string, unknown> = { approved };
  if (projectId) body.project_id = projectId;
  if (message) body.message = message;
  return body;
}

/** Truncate a code preview so the modal stays readable. Pure. */
export function truncatePreview(text: string, max = 1200): string {
  return text.length > max ? `${text.slice(0, max)}\n...(truncated)` : text;
}
