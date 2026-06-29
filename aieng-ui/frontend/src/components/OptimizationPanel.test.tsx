/**
 * @vitest-environment happy-dom
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

import { OptimizationPanel } from "./OptimizationPanel";
import { openPanel } from "../test/openPanel";
import type { OptimizationConvergence } from "../app/optimizationConvergence";
import type { OptimizationStudy } from "../app/optimizationStudy";

afterEach(cleanup);

function makeStudy(overrides: Partial<OptimizationStudy> = {}): OptimizationStudy {
  return {
    has_study: true,
    candidates: [],
    recommendation: null,
    report: null,
    safe_to_accept: false,
    baseline_modified: null,
    warnings: [],
    ...overrides,
  };
}

function makeConvergence(
  iterations: Array<{ index: number; objective: number | null; feasible: boolean }>,
): OptimizationConvergence {
  return {
    has_data: iterations.length > 0,
    iterations: iterations.map((it) => ({
      index: it.index,
      incumbent_candidate_id: `c${it.index}`,
      incumbent_objective: it.objective,
      feasible: it.feasible,
      evaluations_total: it.index * 4,
      failures_this_round: 0,
      convergence_verdict: "continue",
      safe_to_accept: false,
    })),
    latest_verdict: {
      converged: false,
      verdict: "continue",
      reason_codes: [],
      iteration_count: iterations.length,
    },
    config_used: { max_iterations: 20 },
  };
}

describe("OptimizationPanel", () => {
  it("returns null when study is not meaningful", () => {
    const { container } = render(<OptimizationPanel study={makeStudy({ has_study: false })} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders candidate groups and accept button", () => {
    const study = makeStudy({
      candidates: [
        { candidate_id: "c1", rank: 1, feasibility: "feasible", score: 0.9, confidence: "high" },
      ],
      safe_to_accept: true,
    });
    render(<OptimizationPanel study={study} onUseInChat={vi.fn()} />);
    expect(screen.getByText("Optimization study")).toBeTruthy();
    openPanel(/Optimization study/i);
    expect(screen.getByText("#1")).toBeTruthy();
    expect(screen.getByText("c1")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Accept/i })).toBeTruthy();
  });

  it("renders study overview with objective, constraints, and ranking summary", () => {
    const study = makeStudy({
      candidates: [
        { candidate_id: "c1", rank: 1, feasibility: "feasible" },
      ],
      problem: {
        objective: { metric: "mass", sense: "minimize" },
        constraints: [{ type: "max_stress" }],
        variable_count: 3,
      },
      ranking: { best_candidate_id: "c1", next_action: "accept_candidate" },
      acceptance: { status: "pending" },
      report: {
        feasibility_summary: { feasible: 1, infeasible: 0 },
      },
    });
    const { container } = render(<OptimizationPanel study={study} />);
    openPanel(/Optimization study/i);
    expect(screen.getByText("Study overview")).toBeTruthy();
    expect(screen.getByText("Objective")).toBeTruthy();
    expect(screen.getByText("mass")).toBeTruthy();
    expect(screen.getByText("Variables")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("Constraints")).toBeTruthy();
    // "1" appears in multiple places; scope to the overview grid
    expect(container.querySelector(".optimization-overview-grid")?.textContent).toContain("1");
    expect(screen.getByText("Best candidate")).toBeTruthy();
    expect(screen.getByText("Next action")).toBeTruthy();
    expect(screen.getByText("accept_candidate")).toBeTruthy();
    expect(screen.getByText("Acceptance")).toBeTruthy();
    expect(screen.getByText("pending")).toBeTruthy();
    expect(screen.getByText("feasible: 1")).toBeTruthy();
  });

  it("renders surrogate predictions with their uncertainty band + LOO validation (#219)", () => {
    const study = makeStudy({ candidates: [{ candidate_id: "c1", rank: 1, feasibility: "feasible" }] });
    const surrogate = {
      hasProposals: true,
      status: "ok",
      predictions: [
        {
          rank: 1,
          variableChanges: [{ variableId: "wall_thickness", value: 4.5 }],
          predictedScore: 0.62,
          uncertaintyStd: 0.08,
          band: [0.54, 0.7] as [number, number],
          confidence: "medium",
        },
      ],
      validation: {
        method: "leave_one_out_cv",
        nPoints: 4,
        rmse: 0.05,
        mae: 0.04,
        maxAbsError: 0.09,
        relativeRmse: 0.07,
        pearsonR: 0.96,
        note: null,
      },
      withheld: 1,
      reasonCodes: [],
    };
    render(<OptimizationPanel study={study} surrogate={surrogate} />);
    openPanel(/Optimization study/i);
    expect(screen.getByText("Surrogate proposals")).toBeTruthy();
    // the predicted number is rendered WITH its ± band, never bare
    expect(screen.getByText("0.620 ± 0.080")).toBeTruthy();
    // leave-one-out validation summary is shown
    expect(screen.getByText(/Leave-one-out check vs 4 evaluated points/)).toBeTruthy();
    expect(screen.getByText(/RMSE 0\.050/)).toBeTruthy();
    // withheld (band-less) predictions are disclosed, not silently dropped
    expect(screen.getByText(/1 prediction withheld/)).toBeTruthy();
  });

  it("expands candidate details on toggle click", () => {
    const study = makeStudy({
      candidates: [
        {
          candidate_id: "c1",
          rank: 1,
          feasibility: "infeasible",
          constraint_violations: ["stress too high"],
          objective_delta: { metric: "mass", delta_percent: -10, delta_absolute: -0.12, unit: "kg" },
          reasons: ["violated"],
          metrics_missing: ["deflection"],
          execution_status: "completed",
        },
      ],
    });
    const { container } = render(<OptimizationPanel study={study} />);
    openPanel(/Optimization study/i);

    // Details hidden initially
    expect(screen.queryByText("Violations")).toBeNull();

    const toggle = screen.getByRole("button", { name: /Expand details/i });
    fireEvent.click(toggle);

    expect(screen.getByText("Violations")).toBeTruthy();
    expect(screen.getByText("stress too high")).toBeTruthy();
    expect(screen.getByText("Objective delta")).toBeTruthy();
    expect(screen.getByText(/mass:/)).toBeTruthy();
    // objective delta shows the absolute change WITH its unit (#224)
    expect(container.querySelector(".optimization-delta-absolute")?.textContent).toContain("-0.12 kg");
    expect(screen.getByText("Notes")).toBeTruthy();
    expect(screen.getByText("violated")).toBeTruthy();
    expect(screen.getByText("Missing metrics")).toBeTruthy();
    expect(screen.getByText("deflection")).toBeTruthy();
    expect(screen.getByText("Execution")).toBeTruthy();
    expect(screen.getByText("completed")).toBeTruthy();

    // Collapse
    fireEvent.click(toggle);
    expect(screen.queryByText("Violations")).toBeNull();
  });

  it("renders iteration history when present", () => {
    const study = makeStudy({
      candidates: [],
      report: {
        iteration_history: [
          { index: 0, incumbent_candidate_id: "c1", incumbent_objective: 1.2, feasible: true, evaluations_total: 3, convergence_verdict: "continue" },
        ],
      },
    });
    const { container } = render(<OptimizationPanel study={study} />);
    openPanel(/Optimization study/i);
    expect(screen.getByText("Iteration history")).toBeTruthy();
    expect(screen.getByText("#")).toBeTruthy();
    expect(screen.getByText("Incumbent")).toBeTruthy();
    // "Objective" also appears in the overview when present; here it's just the table header
    expect(container.querySelector(".optimization-iteration-table")?.textContent).toContain("Objective");
    expect(screen.getByText("Feasible")).toBeTruthy();
    expect(screen.getByText("Evals")).toBeTruthy();
    expect(screen.getByText("Verdict")).toBeTruthy();
    expect(screen.getByText("continue")).toBeTruthy();
    expect(screen.getByText("Yes")).toBeTruthy();
  });

  it("renders missing stages transparency", () => {
    const study = makeStudy({
      candidates: [],
      report: { missing_stages: ["acceptance", "recommendation"] },
    });
    render(<OptimizationPanel study={study} />);
    openPanel(/Optimization study/i);
    expect(screen.getByText("Missing stages")).toBeTruthy();
    expect(screen.getByText("acceptance")).toBeTruthy();
    expect(screen.getByText("recommendation")).toBeTruthy();
  });

  it("renders failed candidates from report", () => {
    const study = makeStudy({
      candidates: [],
      report: {
        failed_candidates: [
          { candidate_id: "c2", execution_status: "compile_failed", feasibility: "failed", reasons: ["build error"] },
        ],
      },
    });
    render(<OptimizationPanel study={study} />);
    openPanel(/Optimization study/i);
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.getByText("c2")).toBeTruthy();
    expect(screen.getByText("compile_failed")).toBeTruthy();
    expect(screen.getByText("build error")).toBeTruthy();
  });

  it("drafts accept command via onUseInChat", () => {
    const onUseInChat = vi.fn();
    const study = makeStudy({
      candidates: [{ candidate_id: "c1", rank: 1, feasibility: "feasible" }],
      safe_to_accept: true,
    });
    render(<OptimizationPanel study={study} onUseInChat={onUseInChat} />);
    openPanel(/Optimization study/i);
    // There is only one enabled Accept button in this single-feasible-candidate study
    const acceptBtn = screen.getAllByRole("button", { name: /Accept/i }).find((b) => !b.hasAttribute("disabled"));
    expect(acceptBtn).toBeTruthy();
    fireEvent.click(acceptBtn!);
    expect(onUseInChat).toHaveBeenCalledWith("/design-study accept candidate c1");
  });

  it("runs candidate patches through the explicit derived-workspace action", () => {
    const onRunCandidates = vi.fn();
    const study = makeStudy({
      candidates: [{ candidate_id: "c1", rank: 1, feasibility: "unknown" }],
    });
    render(<OptimizationPanel study={study} onRunCandidates={onRunCandidates} />);
    openPanel(/Optimization study/i);

    fireEvent.click(screen.getByRole("button", { name: /Run candidates/i }));
    expect(onRunCandidates).toHaveBeenCalledTimes(1);
  });

  it("disables the run-candidates action while busy", () => {
    const study = makeStudy({
      candidates: [{ candidate_id: "c1", rank: 1, feasibility: "unknown" }],
    });
    render(<OptimizationPanel study={study} onRunCandidates={vi.fn()} running />);
    openPanel(/Optimization study/i);

    expect(screen.getByRole("button", { name: /Running/i }).hasAttribute("disabled")).toBe(true);
  });

  it("renders the convergence chart when convergence data is provided", () => {
    const study = makeStudy({
      candidates: [{ candidate_id: "c1", rank: 1, feasibility: "feasible" }],
      safe_to_accept: true,
    });
    const convergence = makeConvergence([
      { index: 1, objective: 1.0, feasible: true },
      { index: 2, objective: 0.9, feasible: true },
    ]);

    render(<OptimizationPanel study={study} convergence={convergence} />);
    openPanel(/Optimization study/i);
    expect(screen.getByRole("img", { name: "Incumbent objective over iterations" })).toBeTruthy();
  });

  it("omits the convergence chart when convergence has no iterations", () => {
    const study = makeStudy({
      candidates: [{ candidate_id: "c1", rank: 1, feasibility: "feasible" }],
    });
    const convergence: OptimizationConvergence = {
      has_data: false,
      iterations: [],
      latest_verdict: null,
      config_used: null,
    };

    const { container } = render(<OptimizationPanel study={study} convergence={convergence} />);
    openPanel(/Optimization study/i);
    expect(container.querySelector('[role="img"]')).toBeNull();
  });
});
