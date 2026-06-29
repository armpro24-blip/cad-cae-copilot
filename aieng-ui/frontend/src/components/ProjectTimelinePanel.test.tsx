/**
 * @vitest-environment happy-dom
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ProjectTimelinePanel } from "./ProjectTimelinePanel";
import { openPanel } from "../test/openPanel";
import type { ProjectTimeline } from "../app/projectTimeline";

function makeTimeline(): ProjectTimeline {
  return {
    runCount: 1,
    activityCount: 0,
    warningCount: 0,
    diagnosticCount: 0,
    snapshotCount: 1,
    unstructuredFailureCount: 0,
    entries: [
      {
        id: "run_1:approval",
        timestamp: "2026-06-25T10:00:00Z",
        kind: "approval",
        status: "approval_required",
        title: "approval required: cad.restore_snapshot",
        detail: "cad.restore_snapshot requires explicit approval before execution",
        diagnostic: null,
        snapshots: [],
        artifacts: [],
        nextActions: [],
        sourceRunId: "run_1",
        actionableApproval: true,
      },
      {
        id: "run_1:result:cad.list_snapshots",
        timestamp: "2026-06-25T09:59:00Z",
        kind: "snapshot",
        status: "success",
        title: "cad.list_snapshots: success",
        detail: "Restore any of these with cad.restore_snapshot { snapshot_id } (approval-gated).",
        diagnostic: null,
        snapshots: [{
          id: "snap_0001",
          createdAt: "2026-06-25T09:58:00Z",
          toolName: "cad.execute_build123d",
          partCount: 2,
          namedParts: ["bracket", "rib"],
          restored: false,
        }],
        artifacts: [],
        nextActions: [],
        sourceRunId: "run_1",
      },
      {
        id: "run_1:result:cae.prepare_solver_run",
        timestamp: "2026-06-25T09:57:00Z",
        kind: "next_action",
        status: "blocked",
        title: "cae.prepare_solver_run: blocked",
        detail: "Solver is not ready.",
        diagnostic: null,
        snapshots: [],
        artifacts: [],
        nextActions: [{
          label: "Generate solver input",
          tool: "cae.generate_solver_input",
          availableNow: false,
          blockedReason: "Mesh is missing.",
          blockedReasonCodes: ["missing_mesh"],
          blockedReasonCodeDetails: [{
            code: "missing_mesh",
            label: "Missing mesh",
            description: "No FE mesh exists.",
            recommendedAction: "Run cae.generate_mesh before generating the solver deck.",
          }],
          resolvesBlockedReasonCodes: ["deck_not_prepared"],
          resolvesBlockedReasonCodeDetails: [{
            code: "deck_not_prepared",
            label: "Deck not prepared",
            description: "The solver input deck is missing.",
            recommendedAction: "Generate the solver input deck.",
          }],
          safetyFlags: ["mutates package"],
        }],
        sourceRunId: "run_1",
      },
    ],
  };
}

afterEach(() => cleanup());

describe("ProjectTimelinePanel", () => {
  test("wires snapshot restore and runtime approval actions through callbacks", () => {
    const onRestoreSnapshot = vi.fn();
    const onApproveRun = vi.fn();
    const onRejectRun = vi.fn();
    const onCopyNextAction = vi.fn();

    render(
      <ProjectTimelinePanel
        timeline={makeTimeline()}
        onRestoreSnapshot={onRestoreSnapshot}
        onApproveRun={onApproveRun}
        onRejectRun={onRejectRun}
        onCopyNextAction={onCopyNextAction}
      />,
    );

    openPanel(/Project timeline/i);
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(onRestoreSnapshot).toHaveBeenCalledWith("snap_0001");

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApproveRun).toHaveBeenCalledWith("run_1");

    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(onRejectRun).toHaveBeenCalledWith("run_1");

    fireEvent.click(screen.getByRole("button", { name: /Copy next action:/ }));
    expect(onCopyNextAction).toHaveBeenCalledWith(expect.stringContaining("Tool: cae.generate_solver_input"));
    expect(onCopyNextAction).toHaveBeenCalledWith(expect.stringContaining("Status: blocked or not available yet"));
    expect(onCopyNextAction).toHaveBeenCalledWith(expect.stringContaining("missing_mesh"));
    expect(onCopyNextAction).toHaveBeenCalledWith(expect.stringContaining("Never bypass approval"));
  });
});
