import { describe, expect, it } from "vitest";
import {
  EMPTY_PROJECT_COMMAND,
  resolveOnboarding,
  STARTER_STEPS,
} from "./onboarding";

describe("resolveOnboarding", () => {
  it("shows the welcome on a fresh, undismissed install with no projects", () => {
    expect(
      resolveOnboarding({ hasProjects: false, hasViewerAsset: false, welcomeDismissed: false }),
    ).toEqual({ kind: "welcome" });
  });

  it("suppresses the welcome once dismissed (no projects)", () => {
    expect(
      resolveOnboarding({ hasProjects: false, hasViewerAsset: false, welcomeDismissed: true }),
    ).toEqual({ kind: "none" });
  });

  it("guides an empty (geometry-less) project regardless of dismiss state", () => {
    expect(
      resolveOnboarding({
        hasProjects: true,
        hasViewerAsset: false,
        selectedProjectName: "Bracket A",
        welcomeDismissed: true,
      }),
    ).toEqual({ kind: "empty-project", projectName: "Bracket A" });
  });

  it("shows nothing once a geometry preview exists", () => {
    expect(
      resolveOnboarding({
        hasProjects: true,
        hasViewerAsset: true,
        selectedProjectName: "Bracket A",
        welcomeDismissed: false,
      }),
    ).toEqual({ kind: "none" });
  });

  it("treats a blank/whitespace project name as no selection (falls back to welcome)", () => {
    expect(
      resolveOnboarding({
        hasProjects: true,
        hasViewerAsset: false,
        selectedProjectName: "   ",
        welcomeDismissed: false,
      }),
    ).toEqual({ kind: "welcome" });
  });
});

describe("onboarding content", () => {
  it("offers a copy-able starter command on the first step and for empty projects", () => {
    expect(STARTER_STEPS[0]?.command).toBeTruthy();
    expect(EMPTY_PROJECT_COMMAND).toContain("/build");
    // every step has a title + detail; commands are optional
    for (const step of STARTER_STEPS) {
      expect(step.title.length).toBeGreaterThan(0);
      expect(step.detail.length).toBeGreaterThan(0);
    }
  });
});
