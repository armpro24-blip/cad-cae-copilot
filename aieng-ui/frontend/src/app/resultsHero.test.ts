import { describe, expect, it } from "vitest";
import { resolveResultsHero } from "./resultsHero";

// Minimal CAE summary builder — only the fields resolveResultsHero reads.
function cae(overrides: any = {}): any {
  return {
    present: true,
    results_available: true,
    result_summary: {
      status: { has_results: true },
      solver_settings: { analysis_type: "static" },
      computed_values: {
        extrema_computed: true,
        max_von_mises_stress: { value: 237e6, unit: "Pa" },
        max_displacement: { value: 2.09, unit: "mm" },
        minimum_safety_factor: { value: 1.05, basis: "yield" },
      },
      llm_summary: { one_line: "Static run completed.", limitations: ["Linear elastic only."] },
      ...overrides.result_summary,
    },
    simulation_run_summary: overrides.simulation_run_summary,
  };
}

describe("resolveResultsHero", () => {
  it("returns null with no CAE block", () => {
    expect(resolveResultsHero(null)).toBeNull();
    expect(resolveResultsHero(undefined)).toBeNull();
  });

  it("returns null when there are no results", () => {
    const c = cae({ result_summary: { status: { has_results: false }, computed_values: { extrema_computed: false } } });
    expect(resolveResultsHero(c)).toBeNull();
  });

  it("extracts the headline metrics that are present", () => {
    const hero = resolveResultsHero(cae())!;
    expect(hero.metrics.map((m) => m.key)).toEqual(["stress", "displacement", "safety_factor"]);
    expect(hero.metrics[0]).toMatchObject({ value: 237e6, unit: "Pa" });
    expect(hero.analysisType).toBe("static");
  });

  it("omits metrics that are absent", () => {
    const c = cae({
      result_summary: {
        status: { has_results: true },
        computed_values: { extrema_computed: true, max_displacement: { value: 1.0, unit: "mm" } },
        llm_summary: { one_line: "x", limitations: [] },
      },
    });
    const hero = resolveResultsHero(c)!;
    expect(hero.metrics.map((m) => m.key)).toEqual(["displacement"]);
  });

  it("derives a safety verdict from the minimum safety factor", () => {
    expect(resolveResultsHero(cae())!.verdict.kind).toBe("marginal"); // SF 1.05
    const safe = cae({ result_summary: { computed_values: { extrema_computed: true, minimum_safety_factor: { value: 3 } }, status: { has_results: true }, llm_summary: { one_line: "", limitations: [] } } });
    expect(resolveResultsHero(safe)!.verdict.kind).toBe("safe");
    const over = cae({ result_summary: { computed_values: { extrema_computed: true, minimum_safety_factor: { value: 0.7 } }, status: { has_results: true }, llm_summary: { one_line: "", limitations: [] } } });
    expect(resolveResultsHero(over)!.verdict.kind).toBe("over_limit");
  });

  it("HONESTY: stays unverified when no solved solver run is recorded", () => {
    const hero = resolveResultsHero(cae())!;
    expect(hero.credibility.tier).toBe("unverified");
    expect(hero.credibility.rank).toBe(0);
    expect(hero.credibility.production_ready).toBe(false);
  });

  it("HONESTY: only an executed, solved run earns the executed_solver_result tier", () => {
    const c = cae({
      simulation_run_summary: {
        runs: [{ run_id: "r1", solver: "ccx", software: "CalculiX", analysis_type: "static", state: "completed", solved: true }],
      },
    });
    const hero = resolveResultsHero(c)!;
    expect(hero.credibility.tier).toBe("executed_solver_result");
    expect(hero.credibility.rank).toBe(4);
    expect(hero.credibility.production_ready).toBe(false); // never auto-certified
  });

  it("HONESTY: a failed run does NOT upgrade the tier", () => {
    const c = cae({
      simulation_run_summary: {
        runs: [{ run_id: "r1", solver: "ccx", software: "CalculiX", analysis_type: "static", state: "failed", solved: false }],
      },
    });
    expect(resolveResultsHero(c)!.credibility.tier).toBe("unverified");
  });

  it("carries the not-modeled limitations + one-line summary", () => {
    const hero = resolveResultsHero(cae())!;
    expect(hero.limitations).toEqual(["Linear elastic only."]);
    expect(hero.oneLine).toBe("Static run completed.");
  });
});
