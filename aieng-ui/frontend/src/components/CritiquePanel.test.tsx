/**
 * @vitest-environment happy-dom
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CritiquePanel } from "./CritiquePanel";
import { openPanel } from "../test/openPanel";
import type { CritiqueFinding, StandardFastenerPlanSummary } from "../types";

afterEach(cleanup);

function finding(overrides: Partial<CritiqueFinding> = {}): CritiqueFinding {
  return {
    severity: "medium",
    rule: "min_wall_thickness",
    observation: "wall is thin",
    suggested_fix: "increase wall thickness",
    ...overrides,
  };
}

function fastenerPlan(overrides: Partial<StandardFastenerPlanSummary> = {}): StandardFastenerPlanSummary {
  return {
    status: "ok",
    advisory_only: true,
    mutates_geometry: false,
    matched_count: 2,
    plan_count: 2,
    ...overrides,
  };
}

describe("CritiquePanel manufacturing hints", () => {
  it("renders nothing without findings or standard fastener matches", () => {
    const { container } = render(<CritiquePanel findings={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("surfaces advisory fastener plans without mutating geometry", () => {
    const onUseInChat = vi.fn();
    render(<CritiquePanel findings={[]} standardFastenerPlan={fastenerPlan()} onUseInChat={onUseInChat} />);
    openPanel(/Engineering critique/i);
    expect(screen.getByText("Standard fasteners")).toBeTruthy();
    expect(screen.getByText("2 matched holes; 2 advisory fastener plans")).toBeTruthy();
    expect(screen.getByText(/approval-gated/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Draft insertion" }));
    expect(onUseInChat).toHaveBeenCalledWith("/modify insert standard fasteners for matched mounting holes");
  });

  it("keeps existing critique fix drafting intact", () => {
    const onUseInChat = vi.fn();
    render(<CritiquePanel findings={[finding()]} onUseInChat={onUseInChat} />);
    openPanel(/Engineering critique/i);
    fireEvent.click(screen.getByRole("button", { name: "Fix" }));
    expect(onUseInChat).toHaveBeenCalledWith("/modify increase wall thickness");
  });
});
