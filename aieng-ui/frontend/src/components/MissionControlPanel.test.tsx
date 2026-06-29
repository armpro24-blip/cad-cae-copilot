/**
 * @vitest-environment happy-dom
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MissionControlPanel } from "./MissionControlPanel";
import type { MissionControlModel } from "../app/missionControl";

afterEach(cleanup);

function model(overrides: Partial<MissionControlModel> = {}): MissionControlModel {
  return {
    projectName: "Bracket",
    packageName: "bracket.aieng",
    packageStatus: "ready",
    packageDetail: "bracket.aieng preserves CAD, CAE, provenance, and review evidence.",
    headline: "CAD evidence loaded; CAE setup not complete",
    packageIdentity: [
      {
        key: "geometry",
        label: "Geometry passport",
        status: "ready",
        detail: "CAD identity, topology, or feature evidence is inside the package.",
        members: ["manifest.json", "geometry/topology_map.json"],
      },
      {
        key: "claims",
        label: "Claim boundary",
        status: "unknown",
        detail: "No claim map is visible; claims must remain unadvanced.",
        members: [],
      },
    ],
    cards: [
      { key: "package", label: ".aieng package", status: "ready", detail: "bracket.aieng", meta: "4/5 key evidence members" },
      { key: "results", label: "Result evidence", status: "missing", detail: "No solver/result evidence is available.", meta: "claims not auto-advanced" },
    ],
    trustBadges: [
      { key: "draft", kind: "draft", label: "Draft / setup", detail: "No solver result evidence is available yet." },
      { key: "unknown-results", kind: "unknown", label: "Results unknown", detail: "Stress/displacement values must not be claimed." },
      { key: "claim-boundary", kind: "claim_boundary", label: "Claim not advanced", detail: "AIENG does not advance engineering claims automatically." },
    ],
    workflowSteps: [
      { key: "package", label: "Create package", status: "ready", detail: ".aieng package is available.", draft: null },
      { key: "cae_setup", label: "Bind CAE setup", status: "missing", detail: "Materials are missing.", draft: "Propose missing CAE setup. Do not run solver." },
      { key: "solver", label: "Run solver", status: "blocked", detail: "Solver run must go through existing approval gates.", draft: "Prepare approval-gated solver run. Do not claim validation." },
    ],
    nextAction: {
      label: "Define CAE setup",
      detail: "Ask the agent to inspect package evidence.",
      draft: "Inspect package evidence. Do not run solver.",
    },
    lifecycle: [
      { key: "model", label: "Model", status: "ready", detail: "A CAD model is available to inspect and simulate." },
      { key: "setup", label: "Simulation setup", status: "missing", detail: "No simulation setup yet — define material, loads, and constraints." },
      { key: "result", label: "Solver result", status: "unknown", detail: "Complete the simulation setup before running the solver." },
    ],
    primaryAction: {
      kind: "draft",
      label: "Define CAE setup",
      detail: "Ask the agent to inspect package evidence.",
      draft: "Inspect package evidence. Do not run solver.",
    },
    evidenceNotes: [
      ".aieng is the package evidence source of truth.",
      "No result evidence is present; solver values must not be claimed.",
    ],
    ...overrides,
  };
}

describe("MissionControlPanel", () => {
  it("leads with a plain-language status and lifecycle checklist, advanced detail collapsed", () => {
    render(<MissionControlPanel model={model()} />);

    // The at-a-glance answer is visible without expanding anything.
    expect(screen.getByText("Project status")).toBeTruthy();
    expect(screen.getByText("CAD evidence loaded; CAE setup not complete")).toBeTruthy();
    expect(screen.getByText("Model")).toBeTruthy();
    expect(screen.getByText("Simulation setup")).toBeTruthy();
    expect(screen.getByText(/A CAD model is available/)).toBeTruthy();
    // The technical evidence catalogue stays behind Advanced details.
    expect(screen.queryByText(".aieng evidence package")).toBeNull();
    expect(screen.queryByText("Package passport")).toBeNull();
  });

  it("renders package identity, evidence cards, and claim boundary text under Advanced details", () => {
    render(<MissionControlPanel model={model()} />);

    fireEvent.click(screen.getByRole("button", { name: /Advanced details/i }));

    expect(screen.getByText(".aieng evidence package")).toBeTruthy();
    expect(screen.getAllByText("bracket.aieng").length).toBeGreaterThan(0);
    expect(screen.getByText("Package passport")).toBeTruthy();
    expect(screen.getByText("Geometry passport")).toBeTruthy();
    expect(screen.getByText("2 members")).toBeTruthy();
    expect(screen.getByText("Claim boundary")).toBeTruthy();
    expect(screen.getByText("Result evidence")).toBeTruthy();
    expect(screen.getByText("claims not auto-advanced")).toBeTruthy();
    expect(screen.getByText(/solver values must not be claimed/)).toBeTruthy();
    expect(screen.getByText("Draft / setup")).toBeTruthy();
    expect(screen.getByText("CAD to CAE workflow")).toBeTruthy();
    expect(screen.getByText("Bind CAE setup")).toBeTruthy();
  });

  it("copies the bounded agent prompt for a handoff (draft) action", () => {
    const onCopyDraft = vi.fn();
    render(<MissionControlPanel model={model()} onCopyDraft={onCopyDraft} />);

    fireEvent.click(screen.getByRole("button", { name: /Copy prompt/i }));

    expect(onCopyDraft).toHaveBeenCalledWith("Inspect package evidence. Do not run solver.");
  });

  it("offers a real Generate report action when results exist", () => {
    const onOpenReport = vi.fn();
    render(
      <MissionControlPanel
        model={model({
          primaryAction: {
            kind: "report",
            label: "Generate report",
            detail: "Open a traceable engineering summary of these results.",
            draft: null,
          },
        })}
        onOpenReport={onOpenReport}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Generate report/i }));
    expect(onOpenReport).toHaveBeenCalledTimes(1);
  });

  it("shows no copy action when the action is a non-handoff hint", () => {
    render(
      <MissionControlPanel
        model={model({
          primaryAction: { kind: "review_approval", label: "Review pending approval", detail: "Use the approval UI.", draft: null },
        })}
      />,
    );

    expect(screen.queryByRole("button", { name: /Copy prompt/i })).toBeNull();
    expect(screen.getByText(/Review the pending approval shown below/i)).toBeTruthy();
  });

  it("copies step-level prompts without executing the workflow (Advanced details)", () => {
    const onCopyDraft = vi.fn();
    render(<MissionControlPanel model={model()} onCopyDraft={onCopyDraft} />);

    fireEvent.click(screen.getByRole("button", { name: /Advanced details/i }));
    fireEvent.click(screen.getByRole("button", { name: "Copy Run solver prompt" }));

    expect(onCopyDraft).toHaveBeenCalledWith("Prepare approval-gated solver run. Do not claim validation.");
  });
});
