/**
 * @vitest-environment happy-dom
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ValueDemoCheckPanel } from "./ValueDemoCheckPanel";
import { openPanel } from "../test/openPanel";
import type { ValueDemoCheckResponse } from "../types";

afterEach(cleanup);

function check(overrides: Partial<ValueDemoCheckResponse> = {}): ValueDemoCheckResponse {
  return {
    ok: false,
    status: "blocked",
    claim_advancement: "none",
    checks: [
      { id: "real_frd_result", status: "fail", required: true, message: "FRD result is missing." },
      { id: "computed_metrics", status: "pass", required: true, message: "Computed metrics are present." },
    ],
    missing_evidence: ["simulation/runs/value_demo_run_001/outputs/result.frd"],
    honesty_boundaries: ["Synthetic fallback fields are a failed demo condition."],
    ...overrides,
  };
}

describe("ValueDemoCheckPanel", () => {
  it("renders nothing when no package exists yet", () => {
    const { container } = render(<ValueDemoCheckPanel check={{ status: "error", code: "missing_package" }} />);
    expect(container.firstChild).toBeNull();
  });

  it("surfaces incomplete demo evidence without offering execution", () => {
    render(<ValueDemoCheckPanel check={check()} />);
    // Scoped, non-alarming title/status — this is a demo-evidence diagnostic,
    // not a project-readiness blocker.
    expect(screen.getByText("Demo evidence diagnostics")).toBeTruthy();
    expect(screen.getByText("demo evidence incomplete")).toBeTruthy();
    openPanel(/Demo evidence diagnostics/i);
    expect(screen.getByText(/separate from/i)).toBeTruthy();
    expect(screen.getByText("simulation/runs/value_demo_run_001/outputs/result.frd")).toBeTruthy();
    expect(screen.getByText("claim_advancement=none")).toBeTruthy();
    // The panel reports evidence only — no run/execute/solve affordance.
    expect(screen.queryByRole("button", { name: /run|execute|solve|simulate|apply/i })).toBeNull();
  });

  it("labels passing evidence as complete demo evidence", () => {
    render(<ValueDemoCheckPanel check={check({ ok: true, status: "pass", missing_evidence: [] })} />);
    expect(screen.getByText("demo evidence complete")).toBeTruthy();
  });
});
