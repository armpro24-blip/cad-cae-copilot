import { expect, test } from "vitest";

import {
  isValueDemoCheckMeaningful,
  normalizeValueDemoStatus,
  valueDemoCheckRows,
  valueDemoFirstMissing,
  valueDemoHeadline,
} from "./valueDemoCheck";

test("missing package value-demo checks are hidden as non-meaningful", () => {
  expect(isValueDemoCheckMeaningful({ status: "error", code: "missing_package", checks: [] })).toBe(false);
  expect(isValueDemoCheckMeaningful(null)).toBe(false);
});

test("blocked value-demo checks expose rows and first missing evidence", () => {
  const check = {
    status: "blocked",
    checks: [
      { id: "real_frd_result", status: "fail", required: true, message: "FRD result is missing." },
      { id: "report_html", status: "pass", required: false, message: "Report exists." },
    ],
    missing_evidence: ["simulation/runs/value_demo_run_001/outputs/result.frd"],
  };
  expect(isValueDemoCheckMeaningful(check)).toBe(true);
  expect(valueDemoCheckRows(check).map((row) => row.status)).toEqual(["fail", "pass"]);
  expect(valueDemoFirstMissing(check)).toBe("simulation/runs/value_demo_run_001/outputs/result.frd");
  expect(valueDemoHeadline(check)).toBe("demo evidence incomplete");
});

test("value-demo status normalization is conservative", () => {
  expect(normalizeValueDemoStatus("pass")).toBe("pass");
  expect(normalizeValueDemoStatus("surprising")).toBe("unknown");
});
