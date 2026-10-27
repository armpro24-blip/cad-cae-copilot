/**
 * Pure shaping for the Edit Diff panel (#226). No React, no I/O.
 *
 * Re-surfaces the trust signal removed in the MCP-first chat cutover: after a
 * CAD edit, "what changed and is it safe?" The backend persists the last edit's
 * `regression_diff` (topology drift) + `critique_diff` (manufacturability); this
 * turns that payload into a display model where each changed part is tagged
 * `collateral` (changed but NOT the edit's target) so the UI can render it
 * visually distinct, per Zoo's "an edit reads like a diff" framing.
 */

import type { CritiqueDiff, EditDiffResponse, EntitySurvivalSummary, GeometryVerification, RegressionDiff } from "../types";

export type DiffTone = "good" | "neutral" | "caution" | "bad";

export type ChangedPartRow = {
  name: string;
  /** changed but not the edit's intended target → render visually distinct. */
  collateral: boolean;
  maxChangeMm: number | null;
};

export type RegressionView = {
  verdict: string;
  tone: DiffTone;
  headline: string;
  changed: ChangedPartRow[];
  added: string[];
  removed: string[];
  collateralCount: number;
};

export type CritiqueView = {
  verdict: string;
  tone: DiffTone;
  headline: string;
  introducedCount: number;
  resolvedCount: number;
};

export type GeometryVerificationView = {
  status: string;
  tone: DiffTone;
  headline: string;
  topologyPreserved: boolean | null;
  staleReferenceRisk: boolean | null;
  faceSurvival: EntitySurvivalView | null;
  edgeSurvival: EntitySurvivalView | null;
  brepStatus: string | null;
  brepDetail: string | null;
  exportStatus: string | null;
  exportDetail: string | null;
};

export type EntitySurvivalView = {
  before: number;
  after: number;
  survived: number;
  added: number;
  removed: number;
  /** Referenced ids checked individually (e.g. face_ids from the edited feature). */
  referenced: Array<{ id: string; status: string }>;
};

export type EditDiffView = {
  hasData: boolean;
  tool: string | null;
  regression: RegressionView | null;
  critique: CritiqueView | null;
  geometryVerification: GeometryVerificationView | null;
  /** true when any diff or verification signal needs the user's attention. */
  needsAttention: boolean;
};

const REGRESSION_TONE: Record<string, DiffTone> = {
  clean: "good",
  identical: "neutral",
  topology_changed: "caution",
  collateral_change: "bad",
};

const CRITIQUE_TONE: Record<string, DiffTone> = {
  clean: "good",
  improved: "good",
  skipped: "neutral",
  warn: "caution",
  fail: "bad",
};

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function shapeRegression(diff: RegressionDiff | null | undefined): RegressionView | null {
  if (!diff || typeof diff.verdict !== "string") return null;
  const collateralSet = new Set((diff.collateral_parts ?? []).map(String));
  const changed: ChangedPartRow[] = (diff.changed ?? []).map((c) => {
    // Collateral = explicitly listed, or flagged expected:false by the backend.
    const collateral = collateralSet.has(c.part) || c.expected === false;
    return { name: String(c.part), collateral, maxChangeMm: num(c.max_change_mm) };
  });
  return {
    verdict: diff.verdict,
    tone: REGRESSION_TONE[diff.verdict] ?? "neutral",
    headline: diff.headline ?? diff.verdict,
    changed,
    added: (diff.added ?? []).map(String),
    removed: (diff.removed ?? []).map(String),
    collateralCount: changed.filter((c) => c.collateral).length,
  };
}

function shapeCritique(diff: CritiqueDiff | null | undefined): CritiqueView | null {
  if (!diff || typeof diff.verdict !== "string") return null;
  return {
    verdict: diff.verdict,
    tone: CRITIQUE_TONE[diff.verdict] ?? "neutral",
    headline: diff.headline ?? diff.verdict,
    introducedCount: num(diff.introduced_count) ?? (diff.introduced?.length ?? 0),
    resolvedCount: num(diff.resolved_count) ?? 0,
  };
}

/** Turn one backend entity-survival summary into a display-ready view. */
function shapeEntitySurvival(
  summary: EntitySurvivalSummary | undefined | null,
): EntitySurvivalView | null {
  if (!summary || typeof summary !== "object") return null;
  // An unknown survival carries no counts; rendering it as "0 survived" would
  // state a verdict the backend explicitly did not reach.
  if (summary.status === "unknown") return null;
  return {
    before: num(summary.before_count) ?? 0,
    after: num(summary.after_count) ?? 0,
    survived: num(summary.survived_count) ?? 0,
    added: num(summary.added_count) ?? 0,
    removed: num(summary.removed_count) ?? 0,
    referenced: (summary.referenced ?? []).map((r) => ({
      id: String(r.id ?? "unknown"),
      status: String(r.status ?? "unknown"),
    })),
  };
}

/**
 * Derive a read-only geometry-verification view from the backend block.
 *
 * Honest states:
 *   - ``pass``  : topology preserved, no stale-reference risk, no lost refs, exports OK.
 *   - ``warn``  : topology changed, stale-reference risk, a referenced id was lost,
 *                 or export sanity degraded.
 *   - ``fail``  : export sanity failed.
 *   - ``unknown``: the payload is missing key booleans (e.g. topology_preserved).
 *
 * BRep validity is always surfaced as the backend reports it and never used to
 * force pass/fail.
 */
function shapeGeometryVerification(
  gv: GeometryVerification | null | undefined,
): GeometryVerificationView | null {
  if (!gv || typeof gv !== "object") return null;

  const topologyPreserved =
    typeof gv.topology_preserved === "boolean" ? gv.topology_preserved : null;
  const staleReferenceRisk =
    typeof gv.stale_reference_risk === "boolean" ? gv.stale_reference_risk : null;
  const faceSurvival = shapeEntitySurvival(gv.face_edge_survival?.face);
  const edgeSurvival = shapeEntitySurvival(gv.face_edge_survival?.edge);

  const exportSanity = gv.export_sanity;
  const exportStatus = exportSanity?.status ?? null;
  const brep = gv.brep_validity;

  const lostReferenced =
    (faceSurvival?.referenced.some((r) => r.status === "lost") || false) ||
    (edgeSurvival?.referenced.some((r) => r.status === "lost") || false);

  let status: string;
  let tone: DiffTone;
  if (exportStatus === "fail") {
    status = "fail";
    tone = "bad";
  } else if (
    topologyPreserved === false ||
    staleReferenceRisk === true ||
    lostReferenced
  ) {
    status = "warn";
    tone = "caution";
  } else if (exportStatus === "warn") {
    status = "warn";
    tone = "caution";
  } else if (topologyPreserved === true && exportStatus === "pass") {
    status = "pass";
    tone = "good";
  } else {
    status = "unknown";
    tone = "neutral";
  }

  let headline: string;
  if (status === "pass") {
    headline = "Topology and exports survived the edit.";
  } else if (status === "fail") {
    headline =
      exportSanity?.detail ??
      "Geometry export failed; the rebuild did not produce usable artifacts.";
  } else if (lostReferenced) {
    headline = "A referenced face or edge was lost during the edit.";
  } else if (staleReferenceRisk === true) {
    headline = "Referenced topology may be stale; downstream selections could dangle.";
  } else if (topologyPreserved === false) {
    headline = "Topology changed; some faces/edges were added or removed.";
  } else {
    headline =
      exportSanity?.detail ??
      "Geometry verification data is incomplete; topology/export status cannot be determined.";
  }

  return {
    status,
    tone,
    headline,
    topologyPreserved,
    staleReferenceRisk,
    faceSurvival,
    edgeSurvival,
    brepStatus: brep?.status ?? null,
    brepDetail: brep?.detail ?? null,
    exportStatus,
    exportDetail: exportSanity?.detail ?? null,
  };
}

/** Shape the edit-diff payload into the panel's display model. Pure and total. */
export function shapeEditDiff(resp: EditDiffResponse | null | undefined): EditDiffView {
  const empty: EditDiffView = {
    hasData: false,
    tool: null,
    regression: null,
    critique: null,
    geometryVerification: null,
    needsAttention: false,
  };
  if (!resp || resp.available !== true) return empty;

  const regression = shapeRegression(resp.regression_diff);
  const critique = shapeCritique(resp.critique_diff);
  const geometryVerification = shapeGeometryVerification(resp.geometry_verification);
  if (!regression && !critique && !geometryVerification) return empty;

  const needsAttention =
    regression?.tone === "bad" ||
    regression?.tone === "caution" ||
    critique?.tone === "bad" ||
    critique?.tone === "caution" ||
    geometryVerification?.tone === "bad" ||
    geometryVerification?.tone === "caution";

  return {
    hasData: true,
    tool: typeof resp.tool === "string" ? resp.tool : null,
    regression,
    critique,
    geometryVerification,
    needsAttention,
  };
}
