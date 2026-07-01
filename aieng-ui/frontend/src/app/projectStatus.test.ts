import { describe, expect, it } from "vitest";

import { projectStatusInfo, projectStatusLabel, formatRelativeTime } from "./projectStatus";

describe("projectStatusLabel", () => {
  it("maps viewer-ready variants to a single readable label", () => {
    expect(projectStatusLabel("viewer_ready_glb")).toBe("Model ready");
    expect(projectStatusLabel("viewer_ready_stl")).toBe("Model ready");
  });

  it("maps in-progress and error states", () => {
    expect(projectStatusLabel("importing")).toBe("Processing…");
    expect(projectStatusLabel("converting_step")).toBe("Processing…");
    expect(projectStatusLabel("error")).toBe("Needs attention");
    expect(projectStatusLabel("import_failed")).toBe("Needs attention");
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

describe("projectStatusInfo (label + tone, honest to list data)", () => {
  it("derives tone from the reliably-available status", () => {
    expect(projectStatusInfo("viewer_ready_glb")).toEqual({ label: "Model ready", tone: "ready" });
    expect(projectStatusInfo("empty")).toEqual({ label: "Empty", tone: "empty" });
    expect(projectStatusInfo("importing")).toEqual({ label: "Processing…", tone: "processing" });
  });

  it("prioritizes a recorded last_error over the status", () => {
    // Even a viewer-ready project with a stored error needs attention.
    expect(projectStatusInfo("viewer_ready_glb", "conversion crashed")).toEqual({
      label: "Needs attention",
      tone: "error",
    });
  });
});

describe("formatRelativeTime", () => {
  const now = Date.parse("2026-07-01T12:00:00Z");
  it("formats recent and older timestamps compactly", () => {
    expect(formatRelativeTime("2026-07-01T11:59:40Z", now)).toBe("just now");
    expect(formatRelativeTime("2026-07-01T11:30:00Z", now)).toBe("30m ago");
    expect(formatRelativeTime("2026-07-01T09:00:00Z", now)).toBe("3h ago");
    expect(formatRelativeTime("2026-06-29T12:00:00Z", now)).toBe("2d ago");
    expect(formatRelativeTime("2026-06-14T12:00:00Z", now)).toBe("2w ago");
    expect(formatRelativeTime("2026-05-01T12:00:00Z", now)).toBe("2mo ago");
  });
  it("returns null for missing or invalid input", () => {
    expect(formatRelativeTime(null, now)).toBeNull();
    expect(formatRelativeTime("not-a-date", now)).toBeNull();
  });
});
