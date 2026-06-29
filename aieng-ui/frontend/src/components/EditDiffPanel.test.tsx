/**
 * @vitest-environment happy-dom
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { EditDiffPanel } from "./EditDiffPanel";
import { openPanel } from "../test/openPanel";
import type { EditDiffResponse } from "../types";

afterEach(cleanup);

const baseResponse = (overrides: Partial<EditDiffResponse> = {}): EditDiffResponse => ({
  available: true,
  tool: "cad.edit_parameter",
  ...overrides,
});

describe("EditDiffPanel geometry verification surface (#311)", () => {
  it("renders nothing when no edit data exists", () => {
    const { container } = render(<EditDiffPanel editDiff={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows a clean geometry verification badge when topology is preserved", () => {
    render(
      <EditDiffPanel
        editDiff={baseResponse({
          geometry_verification: {
            topology_preserved: true,
            stale_reference_risk: false,
            face_edge_survival: {
              face: {
                before_count: 6,
                after_count: 6,
                survived_count: 6,
                added_count: 0,
                removed_count: 0,
              },
            },
            export_sanity: {
              step_exported: true,
              stl_exported: true,
              glb_exported: true,
              status: "pass",
              detail: "STEP and STL exports produced.",
            },
          },
        })}
      />,
    );

    openPanel(/Last edit/i);
    expect(screen.getByText(/geometry verification: pass/i)).toBeTruthy();
    expect(screen.getByText(/Topology and exports survived the edit/i)).toBeTruthy();
    expect(screen.getByText(/Faces: 6 → 6/i)).toBeTruthy();
  });

  it("highlights a warning when a referenced face is lost", () => {
    render(
      <EditDiffPanel
        editDiff={baseResponse({
          geometry_verification: {
            topology_preserved: false,
            stale_reference_risk: true,
            topology_change: { topology_changed: true, added_count: 1, removed_count: 1 },
            face_edge_survival: {
              face: {
                before_count: 6,
                after_count: 6,
                survived_count: 5,
                added_count: 1,
                removed_count: 1,
                referenced: [{ id: "face_003", status: "lost" }],
              },
            },
            export_sanity: {
              step_exported: true,
              stl_exported: true,
              glb_exported: true,
              status: "pass",
              detail: "",
            },
          },
        })}
      />,
    );

    openPanel(/Last edit/i);
    expect(screen.getByText(/geometry verification: warn/i)).toBeTruthy();
    expect(screen.getByText(/referenced face or edge was lost/i)).toBeTruthy();
    expect(screen.getByText(/Stale reference risk/i)).toBeTruthy();
    expect(screen.getByText(/Topology changed/i)).toBeTruthy();
    expect(screen.getByText(/face_003: lost/i)).toBeTruthy();
  });
});
