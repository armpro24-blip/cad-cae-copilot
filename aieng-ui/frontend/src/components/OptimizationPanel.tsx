import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

import {
  acceptDraftForCandidate,
  FEASIBILITY_LABEL,
  formatMetricValue,
  groupCandidatesByFeasibility,
  isStudyMeaningful,
  type OptimizationCandidate,
  type OptimizationStudy,
} from "../app/optimizationStudy";
import { isConvergenceMeaningful, type OptimizationConvergence } from "../app/optimizationConvergence";
import {
  formatPredictionWithBand,
  type SurrogateProposals,
} from "../app/surrogatePredictions";
import { ConvergenceChart } from "./ConvergenceChart";
import { PanelShell } from "./PanelShell";
import { Sigma } from "lucide-react";

type OptimizationPanelProps = {
  study: OptimizationStudy | null;
  /** Advisory surrogate proposals — each rendered with its uncertainty band (#219). */
  surrogate?: SurrogateProposals | null;
  /** Iterative-loop convergence history; rendered as a chart when available. */
  convergence?: OptimizationConvergence | null;
  /** Execute candidate patches into derived workspaces; never accepts/promotes. */
  onRunCandidates?: () => void | Promise<void>;
  running?: boolean;
  /** Prefill the composer with an approval-gated accept draft for a candidate. */
  onUseInChat?: (draft: string) => void;
};

/**
 * Read-only optimization study surface (the agent-guided sizing loop behind
 * design-study) surfaced as a workbench panel: candidate ranking grouped by
 * feasibility, advisory recommendation with caveats, and study report summary.
 *
 * Honesty signals are visible, not hidden: advisory_only, safe_to_accept,
 * baseline_modified, and missing-metric unknown states are all rendered.
 *
 * The Accept action drafts a /design-study command into the composer — the actual
 * accept still flows through the existing approval-gated path; this panel never
 * mutates geometry directly.
 *
 * Deepening additions:
 * - Study overview (objective, constraints, feasibility tally, best candidate, next action, acceptance).
 * - Per-candidate expand/collapse for constraint violations, objective delta, reasons, missing metrics, execution status.
 * - Iteration history table when present.
 * - Missing-stages transparency.
 * - Failed-candidates transparency.
 */
export function OptimizationPanel({
  study,
  surrogate,
  convergence,
  onRunCandidates,
  running = false,
  onUseInChat,
}: OptimizationPanelProps) {
  const candidates = study?.candidates ?? [];
  const groups = useMemo(() => groupCandidatesByFeasibility(candidates), [candidates]);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (!study || !isStudyMeaningful(study)) return null;

  const hasDeepData =
    study.problem != null ||
    study.ranking != null ||
    study.acceptance != null ||
    study.report?.feasibility_summary != null;

  return (
    <PanelShell
      storageKey="optimization"
      title="Optimization study"
      icon={<Sigma className="h-4 w-4" aria-hidden="true" />}
      summary={`${study.candidates.length} candidate${study.candidates.length !== 1 ? "s" : ""}`}
    >
      {onRunCandidates && (
        <div className="insp-panel-actions">
          <button
            type="button"
            className="optimization-run"
            onClick={() => { void onRunCandidates(); }}
            disabled={running}
            title="Run design-study candidate patches into derived workspaces only; baseline is not promoted."
          >
            {running ? "Running..." : "Run candidates"}
          </button>
        </div>
      )}

      {/* Honesty banner */}
      {study.warnings.length > 0 && (
        <div className="optimization-warnings" role="alert">
          {study.warnings.map((w, i) => (
            <span key={i} className="optimization-warning">⚠ {w}</span>
          ))}
        </div>
      )}
      {study.baseline_modified === true && (
        <div className="optimization-honesty optimization-honesty-bad">
          Baseline was modified — unexpected for a sizing study
        </div>
      )}
      {study.baseline_modified === false && (
        <div className="optimization-honesty optimization-honesty-good">
          Baseline untouched
        </div>
      )}
      {/* Explicit accept-readiness signal — advisory, human-approval-gated. */}
      <div
        className={`optimization-honesty ${
          study.safe_to_accept ? "optimization-honesty-good" : "optimization-honesty-neutral"
        }`}
        role="status"
        aria-live="polite"
      >
        {study.safe_to_accept
          ? "A candidate is safe to accept (still requires approval)"
          : "No candidate is accept-ready yet — advisory only"}
      </div>

      {isConvergenceMeaningful(convergence ?? null) && (
        <ConvergenceChart convergence={convergence!} title="Incumbent objective over iterations" />
      )}

      {surrogate?.hasProposals && <SurrogateSection surrogate={surrogate} />}

      {/* ── Study overview (deepening) ── */}
      {hasDeepData && (
        <div className="optimization-overview">
          <strong className="optimization-overview-title">Study overview</strong>
          <div className="optimization-overview-grid">
            {study.problem?.objective != null && (
              <div className="optimization-overview-item">
                <span className="optimization-overview-key">Objective</span>
                <span className="optimization-overview-value">
                  {typeof study.problem.objective === "object"
                    ? String((study.problem.objective as Record<string, unknown>).metric ?? "—")
                    : String(study.problem.objective)}
                </span>
              </div>
            )}
            {typeof study.problem?.variable_count === "number" && (
              <div className="optimization-overview-item">
                <span className="optimization-overview-key">Variables</span>
                <span className="optimization-overview-value">{study.problem.variable_count}</span>
              </div>
            )}
            {Array.isArray(study.problem?.constraints) && (
              <div className="optimization-overview-item">
                <span className="optimization-overview-key">Constraints</span>
                <span className="optimization-overview-value">{study.problem.constraints.length}</span>
              </div>
            )}
            {study.ranking?.best_candidate_id && (
              <div className="optimization-overview-item">
                <span className="optimization-overview-key">Best candidate</span>
                <code className="optimization-overview-value">{study.ranking.best_candidate_id}</code>
              </div>
            )}
            {study.ranking?.next_action && (
              <div className="optimization-overview-item">
                <span className="optimization-overview-key">Next action</span>
                <span className="optimization-overview-value">{study.ranking.next_action}</span>
              </div>
            )}
            {study.acceptance?.status && (
              <div className="optimization-overview-item">
                <span className="optimization-overview-key">Acceptance</span>
                <span className="optimization-overview-value">{study.acceptance.status}</span>
              </div>
            )}
          </div>
          {study.report?.feasibility_summary && (
            <div className="optimization-feasibility-summary">
              {Object.entries(study.report.feasibility_summary).map(([key, count]) => (
                <span key={key} className={`optimization-feasibility-pill optimization-feasibility-pill-${key}`}>
                  {key}: {count}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Candidate ranking */}
      {groups.map((group) => (
        <div key={group.feasibility} className="optimization-group">
          <div className={`optimization-feasibility optimization-feasibility-${group.feasibility}`}>
            {FEASIBILITY_LABEL[group.feasibility]}
            <span className="optimization-feasibility-count">{group.candidates.length}</span>
          </div>

          {group.candidates.map((candidate) => (
            <CandidateRow
              key={candidate.candidate_id}
              candidate={candidate}
              safeToAccept={study.safe_to_accept}
              onUseInChat={onUseInChat}
              expanded={expandedIds.has(candidate.candidate_id)}
              onToggleExpand={() => toggleExpand(candidate.candidate_id)}
            />
          ))}
        </div>
      ))}

      {/* Failed candidates (deepening) */}
      {study.report?.failed_candidates && study.report.failed_candidates.length > 0 && (
        <div className="optimization-group">
          <div className="optimization-feasibility optimization-feasibility-failed">
            Failed
            <span className="optimization-feasibility-count">{study.report.failed_candidates.length}</span>
          </div>
          {study.report.failed_candidates.map((fc) => (
            <div key={fc.candidate_id} className="optimization-row optimization-row-failed">
              <div className="optimization-row-main">
                <code className="optimization-candidate-id">{fc.candidate_id}</code>
                {fc.execution_status && <span className="optimization-execution-status">{fc.execution_status}</span>}
              </div>
              {fc.reasons && fc.reasons.length > 0 && (
                <div className="optimization-failed-reasons">
                  {fc.reasons.map((r, i) => (
                    <span key={i} className="optimization-failed-reason">{r}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Recommendation */}
      {study.recommendation && (
        <div className="optimization-recommendation">
          <div className="optimization-recommendation-head">
            <strong>Recommendation</strong>
            {study.recommendation.advisory_only !== false && (
              <span className="optimization-advisory-badge">Advisory only</span>
            )}
          </div>
          {study.recommendation.headline ? (
            <p className="optimization-recommendation-text">{study.recommendation.headline}</p>
          ) : null}
          {study.recommendation.reason_codes.length > 0 ? (
            <ul className="optimization-reason-list">
              {study.recommendation.reason_codes.map((code, i) => (
                <li key={i}>{code}</li>
              ))}
            </ul>
          ) : null}
          {study.recommendation.caveats.length > 0 ? (
            <div className="optimization-caveats">
              <strong>Caveats</strong>
              {study.recommendation.caveats.map((c, i) => (
                <span key={i} className="optimization-caveat">{c}</span>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {/* Iteration history (deepening) */}
      {study.report?.iteration_history && study.report.iteration_history.length > 0 && (
        <div className="optimization-iteration-history">
          <strong className="optimization-iteration-history-title">Iteration history</strong>
          <div className="optimization-iteration-table-wrap">
            <table className="optimization-iteration-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Incumbent</th>
                  <th>Objective</th>
                  <th>Feasible</th>
                  <th>Evals</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {study.report.iteration_history.map((it, idx) => (
                  <tr key={idx}>
                    <td>{it.index ?? idx + 1}</td>
                    <td>
                      {it.incumbent_candidate_id ? (
                        <code>{it.incumbent_candidate_id}</code>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{it.incumbent_objective != null ? formatMetricValue(it.incumbent_objective) : "—"}</td>
                    <td>{it.feasible === true ? "Yes" : it.feasible === false ? "No" : "—"}</td>
                    <td>{it.evaluations_total ?? "—"}</td>
                    <td>{it.convergence_verdict ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Report summary */}
      {study.report?.summary ? (
        <div className="optimization-report">
          <strong>Report</strong>
          <p className="optimization-report-text">{study.report.summary}</p>
        </div>
      ) : null}

      {/* Missing stages (deepening) */}
      {study.report?.missing_stages && study.report.missing_stages.length > 0 && (
        <div className="optimization-missing-stages">
          <strong>Missing stages</strong>
          <div className="optimization-missing-list">
            {study.report.missing_stages.map((stage, i) => (
              <span key={i} className="optimization-missing-stage">{stage}</span>
            ))}
          </div>
        </div>
      )}

      <div className="optimization-foot">
        <strong>Run candidates</strong> writes derived evidence only.{" "}
        Click <strong>Accept</strong> to draft a <code>/design-study</code> — acceptance stays approval-gated.
      </div>
    </PanelShell>
  );
}

function SurrogateSection({ surrogate }: { surrogate: SurrogateProposals }) {
  const { predictions, validation, withheld } = surrogate;
  return (
    <div className="surrogate-section" aria-label="Surrogate proposals">
      <div className="surrogate-head">
        <strong>Surrogate proposals</strong>
        <span className="optimization-advisory-badge">Advisory — not solver evidence</span>
      </div>

      {validation ? (
        <div className="surrogate-validation" role="status">
          Leave-one-out check vs {validation.nPoints} evaluated point
          {validation.nPoints !== 1 ? "s" : ""}:{" "}
          {validation.rmse != null && <>RMSE {validation.rmse.toFixed(3)}</>}
          {validation.relativeRmse != null && <> · rel {(validation.relativeRmse * 100).toFixed(1)}%</>}
          {validation.pearsonR != null && <> · r={validation.pearsonR.toFixed(2)}</>}
        </div>
      ) : (
        <div className="surrogate-validation surrogate-validation-absent">
          No solver/evaluated points yet — prediction error band not established.
        </div>
      )}

      <ul className="surrogate-list">
        {predictions.map((p) => (
          <li key={p.rank} className="surrogate-row">
            <span className="surrogate-rank">#{p.rank}</span>
            {/* The number is never shown without its ± envelope. */}
            <span className="surrogate-score" title={`band [${p.band[0].toFixed(3)}, ${p.band[1].toFixed(3)}]`}>
              {formatPredictionWithBand(p)}
            </span>
            {p.confidence && (
              <span className={`optimization-confidence optimization-confidence-${p.confidence}`}>
                {p.confidence}
              </span>
            )}
            {p.variableChanges.length > 0 && (
              <span className="surrogate-changes">
                {p.variableChanges.map((c) => `${c.variableId}=${c.value}`).join(", ")}
              </span>
            )}
          </li>
        ))}
      </ul>

      {withheld > 0 && (
        <div className="surrogate-withheld">
          {withheld} prediction{withheld !== 1 ? "s" : ""} withheld — no uncertainty band available.
        </div>
      )}
    </div>
  );
}

function CandidateRow({
  candidate,
  safeToAccept,
  onUseInChat,
  expanded,
  onToggleExpand,
}: {
  candidate: OptimizationCandidate;
  safeToAccept: boolean;
  onUseInChat?: (draft: string) => void;
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  const metricEntries = useMemo(() => Object.entries(candidate.metrics ?? {}), [candidate.metrics]);
  const hasDetails =
    (candidate.constraint_violations && candidate.constraint_violations.length > 0) ||
    candidate.objective_delta != null ||
    (candidate.reasons && candidate.reasons.length > 0) ||
    (candidate.metrics_missing && candidate.metrics_missing.length > 0) ||
    candidate.execution_status != null;

  const draft = acceptDraftForCandidate(candidate.candidate_id);
  const canAccept = safeToAccept && candidate.feasibility === "feasible";

  return (
    <div className={`optimization-row ${candidate.has_unknown_metrics ? "optimization-row-unknown" : ""}`}>
      <div className="optimization-row-main">
        <button
          type="button"
          className="optimization-expand-toggle"
          onClick={onToggleExpand}
          disabled={!hasDetails}
          title={hasDetails ? (expanded ? "Collapse details" : "Expand details") : "No additional details"}
          aria-expanded={expanded}
        >
          {hasDetails ? (
            expanded ? (
              <ChevronUp size={14} />
            ) : (
              <ChevronDown size={14} />
            )
          ) : (
            <span className="optimization-expand-placeholder" />
          )}
        </button>
        <span className="optimization-rank">#{candidate.rank}</span>
        <code className="optimization-candidate-id" title={`candidate ${candidate.candidate_id}`}>
          {candidate.candidate_id}
        </code>
        {candidate.score != null ? (
          <span className="optimization-score" title="score">
            {formatMetricValue(candidate.score)}
          </span>
        ) : null}
        {candidate.confidence ? (
          <span className={`optimization-confidence optimization-confidence-${candidate.confidence}`}>
            {candidate.confidence}
          </span>
        ) : null}
        {candidate.has_unknown_metrics && (
          <span className="optimization-unknown-badge" title="Some metrics are missing or unknown">
            unknown
          </span>
        )}
      </div>

      {metricEntries.length > 0 && (
        <div className="optimization-metrics">
          {metricEntries.map(([key, value]) => (
            <span key={key} className="optimization-metric" title={key}>
              <span className="optimization-metric-key">{key}</span>
              <span className="optimization-metric-value">{formatMetricValue(value)}</span>
            </span>
          ))}
        </div>
      )}

      {/* Expanded candidate details (deepening) */}
      {expanded && hasDetails && (
        <div className="optimization-candidate-details">
          {candidate.execution_status && (
            <div className="optimization-detail-row">
              <span className="optimization-detail-key">Execution</span>
              <span className="optimization-detail-value">{candidate.execution_status}</span>
            </div>
          )}
          {candidate.objective_delta && (
            <div className="optimization-detail-row">
              <span className="optimization-detail-key">Objective delta</span>
              <span className="optimization-detail-value">
                {candidate.objective_delta.metric ?? "—"}: {" "}
                {candidate.objective_delta.delta_percent != null
                  ? `${candidate.objective_delta.delta_percent > 0 ? "+" : ""}${formatMetricValue(candidate.objective_delta.delta_percent)}%`
                  : "—"}
                {candidate.objective_delta.delta_absolute != null && (
                  <span className="optimization-delta-absolute">
                    {" "}({candidate.objective_delta.delta_absolute > 0 ? "+" : ""}
                    {formatMetricValue(candidate.objective_delta.delta_absolute)}
                    {candidate.objective_delta.unit ? ` ${candidate.objective_delta.unit}` : ""})
                  </span>
                )}
              </span>
            </div>
          )}
          {candidate.constraint_violations && candidate.constraint_violations.length > 0 && (
            <div className="optimization-detail-row">
              <span className="optimization-detail-key">Violations</span>
              <div className="optimization-violations">
                {candidate.constraint_violations.map((v, i) => (
                  <span key={i} className="optimization-violation">{v}</span>
                ))}
              </div>
            </div>
          )}
          {candidate.metrics_missing && candidate.metrics_missing.length > 0 && (
            <div className="optimization-detail-row">
              <span className="optimization-detail-key">Missing metrics</span>
              <div className="optimization-missing-metrics">
                {candidate.metrics_missing.map((m, i) => (
                  <span key={i} className="optimization-missing-metric">{m}</span>
                ))}
              </div>
            </div>
          )}
          {candidate.reasons && candidate.reasons.length > 0 && (
            <div className="optimization-detail-row">
              <span className="optimization-detail-key">Notes</span>
              <ul className="optimization-reason-list">
                {candidate.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {canAccept ? (
        <button
          type="button"
          className="optimization-accept"
          onClick={() => onUseInChat?.(draft)}
          disabled={!onUseInChat}
          title={`Draft a /design-study accept for ${candidate.candidate_id}`}
        >
          Accept
        </button>
      ) : (
        <button
          type="button"
          className="optimization-accept optimization-accept-disabled"
          disabled
          title={
            candidate.feasibility !== "feasible"
              ? "Only feasible candidates can be accepted"
              : "Not safe to accept — review warnings"
          }
        >
          Accept
        </button>
      )}
    </div>
  );
}
