// Results hero shaping (#400): turn the CAE result summary into a prominent,
// plain-language verdict — headline metric(s), an honestly-derived credibility
// tier, and a "what wasn't modeled" line. Pure + unit-tested; the ResultsHero
// component just renders it.
//
// Honesty rule (V&V-40): never present a result as solver-grade unless an
// executed, solved solver run is actually recorded. With results but no such
// run, the tier is "unverified" — never silently upgraded.

import type { CredibilityStamp, ProjectSummary } from "../types";

type CaeSummary = NonNullable<ProjectSummary["cae"]>;

export interface HeroMetric {
  key: "stress" | "displacement" | "safety_factor";
  label: string;
  value: number;
  unit: string | null;
}

export interface ResultsHeroView {
  /** Solver analysis type, e.g. "static" / "modal" — null if unknown. */
  analysisType: string | null;
  /** Headline metrics actually present in the result. */
  metrics: HeroMetric[];
  /** Pass/margin verdict derived from the minimum safety factor, when present. */
  verdict: { kind: "safe" | "marginal" | "over_limit" | "unknown"; text: string };
  /** Honestly-derived credibility stamp (reuses the shared badge formatter). */
  credibility: CredibilityStamp;
  /** One-line plain-language summary from the result's llm_summary. */
  oneLine: string | null;
  /** "What wasn't modeled" — limitations pulled from the result honesty fields. */
  limitations: string[];
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** A solved, completed solver run is the evidence that earns the top tier. */
function executedSolverRun(cae: CaeSummary): { solver: string; analysisType: string } | null {
  const runs = cae.simulation_run_summary?.runs ?? [];
  const solved = runs.find(
    (r) => r.solved === true || r.state === "completed" || r.state === "converged",
  );
  if (!solved) return null;
  return { solver: solved.solver || solved.software || "solver", analysisType: solved.analysis_type || "" };
}

function deriveCredibility(cae: CaeSummary, hasResults: boolean): CredibilityStamp {
  const run = executedSolverRun(cae);
  if (run && hasResults) {
    const at = run.analysisType ? ` ${run.analysisType}` : "";
    return {
      tier: "executed_solver_result",
      rank: 4,
      label: "Executed-solver result",
      evidence_basis: `Executed ${run.solver}${at} run on this geometry`,
      production_ready: false,
    } as CredibilityStamp;
  }
  return {
    tier: "unverified",
    rank: 0,
    label: "Unverified",
    evidence_basis: "Result artifacts present but no executed, solved solver run is recorded",
    production_ready: false,
  } as CredibilityStamp;
}

function safetyVerdict(sf: number | null): ResultsHeroView["verdict"] {
  if (sf === null) return { kind: "unknown", text: "No safety-factor basis" };
  if (sf >= 1.5) return { kind: "safe", text: `Safe — min safety factor ${sf.toPrecision(3)}` };
  if (sf >= 1.0) return { kind: "marginal", text: `Marginal — min safety factor ${sf.toPrecision(3)}` };
  return { kind: "over_limit", text: `Over limit — min safety factor ${sf.toPrecision(3)}` };
}

/**
 * Build the results hero view, or null when there is no result to summarize.
 * Pure and total — tolerates partial/missing summary blocks.
 */
export function resolveResultsHero(cae: ProjectSummary["cae"] | null | undefined): ResultsHeroView | null {
  if (!cae) return null;
  const summary = cae.result_summary;
  const computed = summary?.computed_values;
  const hasResults = Boolean(summary?.status?.has_results || computed?.extrema_computed);
  if (!hasResults) return null;

  const metrics: HeroMetric[] = [];
  const stress = computed?.max_von_mises_stress;
  const disp = computed?.max_displacement;
  const sfObj = computed?.minimum_safety_factor;

  const stressVal = num(stress?.value);
  if (stressVal !== null) {
    metrics.push({ key: "stress", label: "Max von Mises", value: stressVal, unit: stress?.unit ?? null });
  }
  const dispVal = num(disp?.value);
  if (dispVal !== null) {
    metrics.push({ key: "displacement", label: "Max displacement", value: dispVal, unit: disp?.unit ?? null });
  }
  const sfVal = num(sfObj?.value);
  if (sfVal !== null) {
    metrics.push({ key: "safety_factor", label: "Min safety factor", value: sfVal, unit: null });
  }

  return {
    analysisType: summary?.solver_settings?.analysis_type ?? null,
    metrics,
    verdict: safetyVerdict(sfVal),
    credibility: deriveCredibility(cae, hasResults),
    oneLine: summary?.llm_summary?.one_line ?? null,
    limitations: summary?.llm_summary?.limitations ?? [],
  };
}
