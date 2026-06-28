import { useEffect, useState } from "react";

import { api } from "../api";
import type { EditableParameter, ParametricEditProposal } from "../types";

type ParametricEditProposalPanelProps = {
  projectId: string;
  param: EditableParameter;
  value: number;
  reason?: string;
  onApplied?: () => void;
  onCancelled?: () => void;
};

function paramKey(param: EditableParameter, value: number, reason: string) {
  return `${param.feature_id ?? ""}:${param.parameter_name}:${value}:${reason}`;
}

/**
 * Review card for a structured parametric edit proposal (#432).
 *
 * Displays the old/new value diff, scope, protected-feature risks, design-target
 * impacts, and expected-impact summary before the user approves the edit. The
 * actual CAD mutation is applied only after explicit approval via the backend
 * apply endpoint, which records a pre-edit snapshot and marks downstream CAE
 * evidence stale.
 */
export function ParametricEditProposalPanel({
  projectId,
  param,
  value,
  reason = "",
  onApplied,
  onCancelled,
}: ParametricEditProposalPanelProps) {
  const [proposal, setProposal] = useState<ParametricEditProposal | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Discard a stale proposal when the target parameter or proposed value changes.
  useEffect(() => {
    setProposal(null);
    setError(null);
  }, [paramKey(param, value, reason)]);

  const buildProposal = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.createParametricEditProposal(projectId, {
        featureId: param.feature_id ?? "",
        parameterName: param.parameter_name,
        newValue: value,
        reason,
      });
      if (data.status !== "ok") {
        setError(typeof data === "object" && "message" in data ? String(data.message) : "Proposal failed");
        return;
      }
      setProposal(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create proposal");
    } finally {
      setLoading(false);
    }
  };

  const apply = async () => {
    if (!proposal) return;
    setApplying(true);
    setError(null);
    try {
      const result = await api.applyParametricEditProposal(
        projectId,
        proposal.proposal_id,
        proposal.scope_risk?.scope === "global" || proposal.scope_risk?.scope === "unscoped",
      );
      if (result && typeof result === "object" && result.status !== "ok") {
        setError(String(result.message ?? "Applying the edit failed"));
        return;
      }
      onApplied?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to apply proposal");
    } finally {
      setApplying(false);
    }
  };

  if (!proposal) {
    return (
      <div className="parametric-proposal-card">
        <div className="parametric-proposal-row">
          <span className="parametric-proposal-label">Parameter</span>
          <code>{param.parameter_name}</code>
        </div>
        <div className="parametric-proposal-row">
          <span className="parametric-proposal-label">Change</span>
          <span>
            {param.current_value} → {value} {param.scope === "global" ? "(global)" : ""}
          </span>
        </div>
        {reason ? (
          <div className="parametric-proposal-row">
            <span className="parametric-proposal-label">Reason</span>
            <span>{reason}</span>
          </div>
        ) : null}
        <div className="parametric-proposal-actions">
          <button type="button" onClick={buildProposal} disabled={loading}>
            {loading ? "Previewing…" : "Preview change"}
          </button>
          <button type="button" onClick={onCancelled} disabled={loading}>
            Cancel
          </button>
        </div>
        {error ? <div className="parametric-proposal-error">{error}</div> : null}
      </div>
    );
  }

  const risks = proposal.risks.protected_features;
  const impacts = proposal.risks.design_target_impacts;
  const scopeLabel = proposal.scope_risk
    ? `${proposal.scope_risk.scope} — ${proposal.scope_risk.reason}`
    : proposal.scope;

  return (
    <div className="parametric-proposal-card">
      <div className="parametric-proposal-row">
        <span className="parametric-proposal-label">Target</span>
        <code>
          {proposal.target.feature_name ?? proposal.target.feature_id} / {proposal.target.parameter_name}
          {proposal.target.cad_parameter_name ? ` (${proposal.target.cad_parameter_name})` : null}
        </code>
      </div>
      <div className="parametric-proposal-row">
        <span className="parametric-proposal-label">Diff</span>
        <span className="parametric-proposal-diff">
          {String(proposal.change.old_value ?? "—")} → {String(proposal.change.new_value ?? "—")}{" "}
          {proposal.change.unit}
        </span>
      </div>
      <div className="parametric-proposal-row">
        <span className="parametric-proposal-label">Scope</span>
        <span>{scopeLabel}</span>
      </div>
      {proposal.change.reason ? (
        <div className="parametric-proposal-row">
          <span className="parametric-proposal-label">Reason</span>
          <span>{proposal.change.reason}</span>
        </div>
      ) : null}
      {risks.length > 0 ? (
        <div className="parametric-proposal-section">
          <strong>Protected-feature risks</strong>
          <ul>
            {risks.map((risk, i) => (
              <li key={i}>{risk.message}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {impacts.length > 0 ? (
        <div className="parametric-proposal-section">
          <strong>Design-target impacts</strong>
          <ul>
            {impacts.map((impact, i) => (
              <li key={i}>
                {impact.label ?? impact.target_id} ({impact.metric}): {impact.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="parametric-proposal-section">
        <strong>Expected impact</strong>
        <p>{proposal.expected_impact.summary}</p>
      </div>
      {proposal.warnings?.length ? (
        <div className="parametric-proposal-warnings">
          {proposal.warnings.map((w, i) => (
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      ) : null}
      <div className="parametric-proposal-actions">
        <button type="button" onClick={apply} disabled={applying}>
          {applying ? "Applying…" : "Approve and apply"}
        </button>
        <button type="button" onClick={onCancelled} disabled={applying}>
          Reject
        </button>
      </div>
      {error ? <div className="parametric-proposal-error">{error}</div> : null}
    </div>
  );
}
