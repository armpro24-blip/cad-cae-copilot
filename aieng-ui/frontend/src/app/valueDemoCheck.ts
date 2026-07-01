import type { ValueDemoCheckItem, ValueDemoCheckResponse } from "../types";

export type ValueDemoStatus = "pass" | "warning" | "blocked" | "error" | "unknown";

export type ValueDemoCheckRow = {
  id: string;
  status: "pass" | "warning" | "fail" | "skip" | "unknown";
  required: boolean;
  message: string;
};

export function normalizeValueDemoStatus(value: string | undefined): ValueDemoStatus {
  if (value === "pass" || value === "warning" || value === "blocked" || value === "error") return value;
  return "unknown";
}

export function isValueDemoCheckMeaningful(check: ValueDemoCheckResponse | null | undefined): boolean {
  if (!check || check.code === "missing_package" || check.code === "project_not_found") return false;
  return Boolean((check.checks ?? []).length || (check.missing_evidence ?? []).length || check.status === "blocked");
}

function normalizeRowStatus(status: string | undefined): ValueDemoCheckRow["status"] {
  if (status === "pass") return "pass";
  if (status === "warn" || status === "warning") return "warning";
  if (status === "fail") return "fail";
  if (status === "skip") return "skip";
  return "unknown";
}

export function valueDemoCheckRows(check: ValueDemoCheckResponse | null | undefined): ValueDemoCheckRow[] {
  return (check?.checks ?? []).map((item: ValueDemoCheckItem) => ({
    id: item.id,
    status: normalizeRowStatus(item.status),
    required: item.required !== false,
    message: item.message || item.id,
  }));
}

// Scoped to the reproducible demo-evidence chain — NOT the project's engineering
// readiness (that lives in Project status). Wording avoids "blocked", which read
// as the whole project being invalid even on a solved project.
export function valueDemoHeadline(check: ValueDemoCheckResponse | null | undefined): string {
  const status = normalizeValueDemoStatus(check?.status);
  if (status === "pass") return "demo evidence complete";
  if (status === "warning") return "demo evidence warnings";
  if (status === "blocked") return "demo evidence incomplete";
  if (status === "error") return "diagnostic unavailable";
  return "demo evidence unknown";
}

export function valueDemoFirstMissing(check: ValueDemoCheckResponse | null | undefined): string | null {
  const missing = check?.missing_evidence ?? [];
  return missing.length ? missing[0] : null;
}
