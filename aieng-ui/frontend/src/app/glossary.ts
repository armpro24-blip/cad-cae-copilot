// Plain-language glossary for the domain terms the workbench surfaces (#399).
// A newcomer should be able to read every status/term without external docs.
// Pure content + a lookup helper; the InfoTip component renders an entry.
// Honesty caveats are baked in where a term implies more trust than it earns.

export type GlossaryKey =
  // V&V-40 credibility tiers (low → high)
  | "credibility_critique_finding"
  | "credibility_surrogate_prediction"
  | "credibility_proxy_assembly_result"
  | "credibility_executed_solver_result"
  // regression_diff verdicts (cad.edit_parameter)
  | "regression_clean"
  | "regression_collateral_change"
  | "regression_topology_changed"
  | "regression_identical"
  // simulation-readiness input statuses
  | "readiness_present"
  | "readiness_missing"
  | "readiness_defaultable"
  | "readiness_unknown"
  // mesh convergence
  | "gci";

export const GLOSSARY: Record<GlossaryKey, string> = {
  credibility_critique_finding:
    "Lowest trust: a deterministic rule-of-thumb design check, not a physics result.",
  credibility_surrogate_prediction:
    "A fast statistical/ML estimate with its own uncertainty — advisory, not a solver result.",
  credibility_proxy_assembly_result:
    "A simplified assembly model (no real contact physics or bolt preload) — directional, not certified.",
  credibility_executed_solver_result:
    "Highest trust available: produced by an actually-executed FEA solver run on this geometry.",
  regression_clean: "Only the part(s) you intended to change moved. Safe.",
  regression_collateral_change:
    "Warning: parts you did NOT target also moved — usually a shared constant. Review before trusting.",
  regression_topology_changed:
    "The set of parts changed (a part appeared or disappeared) — unexpected for a pure dimensional edit.",
  regression_identical: "Nothing changed — likely the wrong constant or a no-op value.",
  readiness_present: "This input is explicitly configured.",
  readiness_missing: "Required for the solver and not yet set — ask your agent to add it.",
  readiness_defaultable: "Not set, but a sensible default can be applied if you don't specify one.",
  readiness_unknown: "Explicitly unavailable for this analysis.",
  gci:
    "Grid Convergence Index — estimated remaining error from mesh coarseness. Lower % means more mesh-independent. A discretization-uncertainty estimate, not a model-validity claim.",
};

/** Look up a glossary explanation by key (undefined when absent). */
export function glossaryText(key: GlossaryKey): string {
  return GLOSSARY[key];
}

/** Map a raw regression_diff verdict string to its glossary key. */
export function regressionVerdictKey(verdict: string): GlossaryKey | null {
  switch (verdict) {
    case "clean":
      return "regression_clean";
    case "collateral_change":
      return "regression_collateral_change";
    case "topology_changed":
      return "regression_topology_changed";
    case "identical":
      return "regression_identical";
    default:
      return null;
  }
}

/** Map a credibility tier string to its glossary key (null for `unverified`). */
export function credibilityTierKey(tier: string): GlossaryKey | null {
  switch (tier) {
    case "critique_finding":
      return "credibility_critique_finding";
    case "surrogate_prediction":
      return "credibility_surrogate_prediction";
    case "proxy_assembly_result":
      return "credibility_proxy_assembly_result";
    case "executed_solver_result":
      return "credibility_executed_solver_result";
    default:
      return null;
  }
}
