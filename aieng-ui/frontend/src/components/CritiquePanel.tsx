import { useMemo } from "react";

import {
  fastenerPlanDraft,
  fastenerPlanHasMatches,
  fastenerPlanSummary,
  fixDraftForFinding,
  groupFindingsBySeverity,
  SEVERITY_LABEL,
  type Severity,
} from "../app/critiqueFindings";
import { ClipboardList } from "lucide-react";

import type { CredibilityStamp, CritiqueFinding, StandardFastenerPlanSummary } from "../types";
import { CredibilityBadge } from "./CredibilityBadge";
import { PanelShell } from "./PanelShell";

type CritiquePanelProps = {
  findings: CritiqueFinding[];
  standardFastenerPlan?: StandardFastenerPlanSummary | null;
  /** Shared V&V-40 credibility stamp for this critique (#218). */
  credibility?: CredibilityStamp | null;
  /** Prefill the composer with a "/modify <suggested_fix>" draft. */
  onUseInChat?: (draft: string) => void;
};

/**
 * Read-only manufacturability/engineering critique (the audit behind cad.critique)
 * surfaced as an actionable panel: findings grouped by severity, each with a one-
 * click "Fix" that drafts a /modify from the finding's suggested_fix into the
 * composer. The edit itself still runs through the approval-gated path — the panel
 * never mutates geometry.
 */
export function CritiquePanel({ findings, standardFastenerPlan, credibility, onUseInChat }: CritiquePanelProps) {
  const safeFindings = findings ?? [];
  const groups = useMemo(() => groupFindingsBySeverity(safeFindings), [safeFindings]);
  const fastenerPlan = fastenerPlanHasMatches(standardFastenerPlan) ? standardFastenerPlan : null;
  const fastenerSummary = fastenerPlanSummary(fastenerPlan);
  const fastenerDraft = fastenerPlanDraft(fastenerPlan);

  if (!safeFindings.length && !fastenerPlan) return null;

  return (
    <PanelShell
      storageKey="critique"
      title="Engineering critique"
      icon={<ClipboardList className="h-4 w-4" aria-hidden="true" />}
      status={
        <span className="critique-headstatus">
          <CredibilityBadge credibility={credibility} />
          <span className="critique-count">
            {safeFindings.length} finding{safeFindings.length !== 1 ? "s" : ""}
          </span>
        </span>
      }
      summary={`${safeFindings.length} finding${safeFindings.length !== 1 ? "s" : ""}`}
    >
      {fastenerPlan ? (
        <div className="critique-fastener-plan">
          <div className="critique-fastener-head">
            <strong>Standard fasteners</strong>
            {fastenerSummary ? <span>{fastenerSummary}</span> : null}
          </div>
          <div className="critique-fastener-note">
            Advisory only. Geometry changes still require an approval-gated <code>/modify</code>.
          </div>
          {fastenerDraft ? (
            <button
              type="button"
              className="critique-fix critique-fastener-action"
              onClick={() => onUseInChat?.(fastenerDraft)}
              disabled={!onUseInChat}
              title="Draft standard fastener insertion for review"
            >
              Draft insertion
            </button>
          ) : null}
        </div>
      ) : null}

      {groups.map((group) => (
        <div key={group.severity} className="critique-group">
          <div className={`critique-sev critique-sev-${group.severity as Severity}`}>
            {SEVERITY_LABEL[group.severity]}
            <span className="critique-sev-count">{group.findings.length}</span>
          </div>

          {group.findings.map((finding: CritiqueFinding, index) => {
            const draft = fixDraftForFinding(finding);
            return (
              <div key={`${finding.rule ?? "rule"}-${finding.feature ?? index}`} className="critique-row">
                <div className="critique-row-main">
                  <span className="critique-rule">{finding.rule ?? finding.category ?? "finding"}</span>
                  {finding.feature ? <code className="critique-feature">{finding.feature}</code> : null}
                  <span className="critique-observation">{finding.observation}</span>
                </div>
                {draft ? (
                  <button
                    type="button"
                    className="critique-fix"
                    onClick={() => onUseInChat?.(draft)}
                    disabled={!onUseInChat}
                    title={finding.suggested_fix}
                  >
                    Fix
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      ))}

      <div className="critique-foot">
        Click <strong>Fix</strong> to draft a <code>/modify</code> — edits run through approval.
      </div>
    </PanelShell>
  );
}
