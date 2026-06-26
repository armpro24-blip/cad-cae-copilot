import { describe, expect, it } from "vitest";
import { isCaeSetupComplete, resolveWorkflowStages } from "./workflowStage";

describe("resolveWorkflowStages", () => {
  it("returns no stages without a project (onboarding covers that case)", () => {
    expect(
      resolveWorkflowStages({
        hasProject: false,
        hasGeometry: false,
        hasCaeSetup: false,
        hasResults: false,
      }),
    ).toEqual([]);
  });

  it("marks Model active for a fresh project with no geometry", () => {
    const stages = resolveWorkflowStages({
      hasProject: true,
      hasGeometry: false,
      hasCaeSetup: false,
      hasResults: false,
    });
    expect(stages.map((s) => s.key)).toEqual(["model", "setup", "solve", "results"]);
    expect(stages.find((s) => s.status === "active")?.key).toBe("model");
    expect(stages.every((s) => s.status !== "done")).toBe(true);
  });

  it("advances to Setup once geometry exists", () => {
    const stages = resolveWorkflowStages({
      hasProject: true,
      hasGeometry: true,
      hasCaeSetup: false,
      hasResults: false,
    });
    expect(stages[0].status).toBe("done");
    expect(stages.find((s) => s.status === "active")?.key).toBe("setup");
  });

  it("advances to Solve once setup is complete", () => {
    const stages = resolveWorkflowStages({
      hasProject: true,
      hasGeometry: true,
      hasCaeSetup: true,
      hasResults: false,
    });
    expect(stages.find((s) => s.status === "active")?.key).toBe("solve");
    expect(stages[0].status).toBe("done");
    expect(stages[1].status).toBe("done");
  });

  it("sits on Results with everything done when results exist", () => {
    const stages = resolveWorkflowStages({
      hasProject: true,
      hasGeometry: true,
      hasCaeSetup: true,
      hasResults: true,
    });
    expect(stages.slice(0, 3).every((s) => s.status === "done")).toBe(true);
    expect(stages[3].status).toBe("active");
  });

  it("is monotonic: results imply earlier stages even if their signals are missing", () => {
    const stages = resolveWorkflowStages({
      hasProject: true,
      hasGeometry: false,
      hasCaeSetup: false,
      hasResults: true,
    });
    expect(stages[0].status).toBe("done"); // geometry inferred from results
    expect(stages[1].status).toBe("done"); // setup inferred from results
    expect(stages[3].status).toBe("active");
  });
});

describe("isCaeSetupComplete", () => {
  it("is false for null/undefined status", () => {
    expect(isCaeSetupComplete(null)).toBe(false);
    expect(isCaeSetupComplete(undefined)).toBe(false);
  });

  it("is true when ready_for_solver", () => {
    expect(isCaeSetupComplete({ ready_for_solver: true })).toBe(true);
  });

  it("is true with material + load + boundary conditions", () => {
    expect(
      isCaeSetupComplete({
        has_materials: true,
        has_loads: true,
        has_boundary_conditions: true,
      }),
    ).toBe(true);
  });

  it("accepts has_constraints as the constraint signal", () => {
    expect(
      isCaeSetupComplete({ has_materials: true, has_loads: true, has_constraints: true }),
    ).toBe(true);
  });

  it("is false when a required input is missing", () => {
    expect(isCaeSetupComplete({ has_materials: true, has_loads: true })).toBe(false);
    expect(isCaeSetupComplete({ has_loads: true, has_constraints: true })).toBe(false);
  });
});
