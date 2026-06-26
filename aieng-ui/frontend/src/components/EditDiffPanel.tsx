import { useMemo } from "react";

import { shapeEditDiff } from "../app/editDiff";
import type { EntitySurvivalView } from "../app/editDiff";
import type { EditDiffResponse } from "../types";
import { InfoTip } from "./InfoTip";
import { glossaryText, regressionVerdictKey } from "../app/glossary";

type EditDiffPanelProps = {
  editDiff: EditDiffResponse | null;
};

/**
 * First-class "this edit changed X" panel (#226): re-surfaces the last CAD
 * edit's topology `regression_diff` + manufacturability `critique_diff` verdicts
 * (persisted to package state) so the trust signal isn't buried in raw tool
 * output. Collateral changes — parts that moved but were NOT the edit's target —
 * are rendered visually distinct. Read-only; renders nothing until an edit exists.
 */
export function EditDiffPanel({ editDiff }: EditDiffPanelProps) {
  const view = useMemo(() => shapeEditDiff(editDiff), [editDiff]);
  if (!view.hasData) return null;

  return (
    <section className="editdiff-card" aria-label="Last edit diff">
      <div className="editdiff-head">
        <strong>Last edit</strong>
        {view.tool ? <code className="editdiff-tool">{view.tool}</code> : null}
        {view.needsAttention ? (
          <span className="editdiff-flag editdiff-flag-bad">needs review</span>
        ) : (
          <span className="editdiff-flag editdiff-flag-good">clean</span>
        )}
      </div>

      {view.regression ? (
        <div className="editdiff-section">
          <div className={`editdiff-verdict editdiff-${view.regression.tone}`}>
            Topology: {view.regression.verdict}
            {regressionVerdictKey(view.regression.verdict) ? (
              <InfoTip text={glossaryText(regressionVerdictKey(view.regression.verdict)!)} />
            ) : null}
          </div>
          <p className="editdiff-headline">{view.regression.headline}</p>
          {view.regression.changed.length > 0 ? (
            <ul className="editdiff-parts">
              {view.regression.changed.map((part) => (
                <li
                  key={part.name}
                  className={`editdiff-part ${part.collateral ? "editdiff-part-collateral" : ""}`}
                  title={part.collateral ? "Collateral — this part was NOT the edit's target" : "Intended change"}
                >
                  <code>{part.name}</code>
                  {part.maxChangeMm != null ? (
                    <span className="editdiff-delta">{part.maxChangeMm.toFixed(2)}mm</span>
                  ) : null}
                  {part.collateral ? <span className="editdiff-collateral-tag">collateral</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
          {(view.regression.added.length > 0 || view.regression.removed.length > 0) ? (
            <div className="editdiff-topo-changes">
              {view.regression.added.length > 0 ? <span>+ {view.regression.added.join(", ")}</span> : null}
              {view.regression.removed.length > 0 ? <span>− {view.regression.removed.join(", ")}</span> : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {view.critique ? (
        <div className="editdiff-section">
          <div className={`editdiff-verdict editdiff-${view.critique.tone}`}>
            Manufacturability: {view.critique.verdict}
          </div>
          <p className="editdiff-headline">{view.critique.headline}</p>
          {view.critique.introducedCount > 0 ? (
            <span className="editdiff-flag editdiff-flag-bad">
              {view.critique.introducedCount} new finding{view.critique.introducedCount !== 1 ? "s" : ""}
            </span>
          ) : view.critique.resolvedCount > 0 ? (
            <span className="editdiff-flag editdiff-flag-good">
              {view.critique.resolvedCount} resolved
            </span>
          ) : null}
        </div>
      ) : null}

      {view.geometryVerification ? (
        <div className="editdiff-section">
          <div className={`editdiff-verdict editdiff-${view.geometryVerification.tone}`}>
            Geometry verification: {view.geometryVerification.status}
          </div>
          <p className="editdiff-headline">{view.geometryVerification.headline}</p>

          {view.geometryVerification.topologyPreserved === false || view.geometryVerification.staleReferenceRisk ? (
            <div className="editdiff-topo-changes">
              {view.geometryVerification.topologyPreserved === false ? (
                <span>Topology changed</span>
              ) : null}
              {view.geometryVerification.staleReferenceRisk ? (
                <span>Stale reference risk</span>
              ) : null}
            </div>
          ) : null}

          <EntitySurvivalRow label="Faces" survival={view.geometryVerification.faceSurvival} />
          <EntitySurvivalRow label="Edges" survival={view.geometryVerification.edgeSurvival} />

          {view.geometryVerification.brepStatus ? (
            <p className="editdiff-headline" title={view.geometryVerification.brepDetail ?? undefined}>
              BRep validity: {view.geometryVerification.brepStatus}
            </p>
          ) : null}

          {view.geometryVerification.exportStatus ? (
            <p className="editdiff-headline">
              Export sanity: {view.geometryVerification.exportStatus}
              {view.geometryVerification.exportDetail ? ` — ${view.geometryVerification.exportDetail}` : ""}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function EntitySurvivalRow({
  label,
  survival,
}: {
  label: string;
  survival: EntitySurvivalView | null;
}) {
  if (!survival) return null;
  return (
    <div className="editdiff-topo-changes">
      <span>
        {label}: {survival.before} → {survival.after}
      </span>
      {survival.survived > 0 ? <span>{survival.survived} survived</span> : null}
      {survival.added > 0 ? <span>+ {survival.added} added</span> : null}
      {survival.removed > 0 ? <span>− {survival.removed} removed</span> : null}
      {survival.referenced.length > 0 ? (
        <span className="editdiff-referenced">
          {survival.referenced.map((ref) => {
            const statusClass = ["lost", "survived", "new", "unknown"].includes(ref.status)
              ? `editdiff-ref-${ref.status}`
              : "editdiff-ref-unknown";
            return (
              <code key={`${ref.id}:${ref.status}`} className={`editdiff-ref ${statusClass}`}>
                {ref.id}: {ref.status}
              </code>
            );
          })}
        </span>
      ) : null}
    </div>
  );
}
