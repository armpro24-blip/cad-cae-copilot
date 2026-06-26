import { describe, expect, it } from "vitest";
import { GLOSSARY, glossaryText, regressionVerdictKey } from "./glossary";

describe("glossary content", () => {
  it("has a non-empty explanation for every key", () => {
    for (const [key, text] of Object.entries(GLOSSARY)) {
      expect(text.length, key).toBeGreaterThan(10);
    }
  });

  it("covers the four credibility tiers", () => {
    expect(GLOSSARY.credibility_critique_finding).toBeTruthy();
    expect(GLOSSARY.credibility_surrogate_prediction).toBeTruthy();
    expect(GLOSSARY.credibility_proxy_assembly_result).toBeTruthy();
    expect(GLOSSARY.credibility_executed_solver_result).toBeTruthy();
  });

  it("keeps honesty caveats on lower tiers (not a solver / not certified)", () => {
    expect(GLOSSARY.credibility_surrogate_prediction.toLowerCase()).toContain("not a solver");
    expect(GLOSSARY.credibility_proxy_assembly_result.toLowerCase()).toContain("not certified");
    expect(GLOSSARY.gci.toLowerCase()).toContain("not a model-validity claim");
  });

  it("glossaryText returns the mapped string", () => {
    expect(glossaryText("regression_clean")).toBe(GLOSSARY.regression_clean);
  });
});

describe("regressionVerdictKey", () => {
  it("maps the four known verdicts", () => {
    expect(regressionVerdictKey("clean")).toBe("regression_clean");
    expect(regressionVerdictKey("collateral_change")).toBe("regression_collateral_change");
    expect(regressionVerdictKey("topology_changed")).toBe("regression_topology_changed");
    expect(regressionVerdictKey("identical")).toBe("regression_identical");
  });

  it("returns null for an unknown verdict", () => {
    expect(regressionVerdictKey("something_else")).toBeNull();
    expect(regressionVerdictKey("")).toBeNull();
  });
});
