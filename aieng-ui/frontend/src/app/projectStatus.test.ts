import { describe, expect, it } from "vitest";

import { projectStatusLabel } from "./projectStatus";

describe("projectStatusLabel", () => {
  it("maps viewer-ready variants to a single readable label", () => {
    expect(projectStatusLabel("viewer_ready_glb")).toBe("Model ready");
    expect(projectStatusLabel("viewer_ready_stl")).toBe("Model ready");
  });

  it("maps in-progress and error states", () => {
    expect(projectStatusLabel("importing")).toBe("Processing…");
    expect(projectStatusLabel("converting_step")).toBe("Processing…");
    expect(projectStatusLabel("error")).toBe("Error");
    expect(projectStatusLabel("import_failed")).toBe("Error");
  });

  it("treats new/empty projects as empty", () => {
    expect(projectStatusLabel("created")).toBe("Empty");
  });

  it("humanizes unknown statuses instead of showing raw tokens", () => {
    expect(projectStatusLabel("some_custom_state")).toBe("Some custom state");
    expect(projectStatusLabel("")).toBe("Unknown");
    expect(projectStatusLabel(null)).toBe("Unknown");
  });
});
