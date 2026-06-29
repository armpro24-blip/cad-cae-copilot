/**
 * @vitest-environment happy-dom
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { MeshConvergencePanel } from "./MeshConvergencePanel";
import { openPanel } from "../test/openPanel";
import type { MeshConvergenceReport } from "../types";

afterEach(cleanup);

const makeReport = (overall: string = "converged"): MeshConvergenceReport => ({
  status: "ok",
  tool: "cae.mesh_convergence",
  mesh_sizes: [1.0, 0.5, 0.25],
  solved_count: 3,
  convergence: {
    max_von_mises_stress: {
      metric: "max_von_mises_stress",
      level_count: 3,
      gci_fine_percent: 0.8,
      converged: overall === "converged",
      verdict: overall === "converged" ? "converged" : "not_converged",
    },
  },
  verdicts: { max_von_mises_stress: overall === "converged" ? "converged" : "not_converged" },
  overall_verdict: overall,
});

describe("MeshConvergencePanel", () => {
  it("renders nothing without a meaningful report", () => {
    const { container } = render(<MeshConvergencePanel report={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the metric table when converged", () => {
    render(<MeshConvergencePanel report={makeReport("converged")} />);
    expect(screen.getByText("Mesh convergence")).toBeTruthy();
    openPanel(/Mesh convergence/i);
    expect(screen.getByText("max_von_mises_stress")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Finer mesh" })).toBeNull();
  });

  it("offers a finer-mesh draft when not converged", () => {
    const onUseInChat = vi.fn();
    render(<MeshConvergencePanel report={makeReport("not_converged")} onUseInChat={onUseInChat} />);
    openPanel(/Mesh convergence/i);
    fireEvent.click(screen.getByRole("button", { name: "Finer mesh" }));
    expect(onUseInChat).toHaveBeenCalledWith("/simulate mesh_size_mm=0.125");
  });
});
