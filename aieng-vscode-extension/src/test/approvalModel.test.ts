import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  decisionBody,
  parseApprovalEvent,
  parseApprovalResolvedId,
  truncatePreview,
} from "../approvalModel";

describe("parseApprovalEvent", () => {
  it("returns null for non-string data", () => {
    assert.equal(parseApprovalEvent(null), null);
    assert.equal(parseApprovalEvent({}), null);
  });

  it("returns null for invalid JSON", () => {
    assert.equal(parseApprovalEvent("not json"), null);
  });

  it("returns null for non-approval events", () => {
    assert.equal(parseApprovalEvent(JSON.stringify({ type: "project_changed" })), null);
  });

  it("parses a minimal approval_requested event", () => {
    const event = {
      type: "approval_requested",
      payload: {
        agentic_permission_id: "perm-123",
        tool_name: "cad.execute_build123d",
        explanation: "Build a bracket.",
      },
    };
    const request = parseApprovalEvent(JSON.stringify(event));
    assert.ok(request);
    assert.equal(request!.permissionId, "perm-123");
    assert.equal(request!.toolName, "cad.execute_build123d");
    assert.equal(request!.explanation, "Build a bracket.");
  });

  it("prefers event.project_id over payload.target_project_id", () => {
    const event = {
      type: "approval_requested",
      project_id: "from-event",
      payload: {
        agentic_permission_id: "perm-123",
        target_project_id: "from-payload",
      },
    };
    const request = parseApprovalEvent(JSON.stringify(event));
    assert.equal(request!.projectId, "from-event");
  });

  it("includes code preview when present", () => {
    const event = {
      type: "approval_requested",
      payload: {
        agentic_permission_id: "perm-123",
        code_preview: "box = Box(10, 10, 10)",
      },
    };
    const request = parseApprovalEvent(JSON.stringify(event));
    assert.equal(request!.codePreview, "box = Box(10, 10, 10)");
  });

  it("returns null when permission id is missing", () => {
    const event = { type: "approval_requested", payload: {} };
    assert.equal(parseApprovalEvent(JSON.stringify(event)), null);
  });
});

describe("parseApprovalResolvedId", () => {
  it("extracts the permission id from a resolution event", () => {
    const event = {
      type: "approval_resolved",
      payload: { agentic_permission_id: "perm-123" },
    };
    assert.equal(parseApprovalResolvedId(JSON.stringify(event)), "perm-123");
  });

  it("returns null for other events", () => {
    assert.equal(parseApprovalResolvedId(JSON.stringify({ type: "approval_requested" })), null);
  });
});

describe("decisionBody", () => {
  it("returns approved flag only by default", () => {
    assert.deepEqual(decisionBody(true), { approved: true });
    assert.deepEqual(decisionBody(false), { approved: false });
  });

  it("includes optional project_id and message", () => {
    assert.deepEqual(decisionBody(true, "p1", "Looks good."), {
      approved: true,
      project_id: "p1",
      message: "Looks good.",
    });
  });
});

describe("truncatePreview", () => {
  it("leaves short text unchanged", () => {
    assert.equal(truncatePreview("short"), "short");
  });

  it("truncates long text and appends ellipsis", () => {
    const long = "a".repeat(2000);
    const result = truncatePreview(long, 1200);
    assert.ok(result.endsWith("\n...(truncated)"));
    assert.equal(result.length, 1200 + "\n...(truncated)".length);
  });
});
