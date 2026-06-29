import { useMemo } from "react";

import { TrendingUp } from "lucide-react";

import {
  isSizingSweepReportMeaningful,
  sizingSweepRunDraft,
  sizingSweepRows,
  sizingSweepSummary,
  sizingSweepWinnerDraft,
} from "../app/sizingSweepReport";
import type { SizingSweepReport } from "../types";
import { PanelShell } from "./PanelShell";

type SizingSweepPanelProps = {
  report: SizingSweepReport | null;
  /** Prefill the composer with a "/modify set <param> to <winner>" draft. */
  onUseInChat?: (draft: string) => void;
};

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (Math.abs(value) < 1e-6) return value.toExponential(2);
  return value.toPrecision(4);
}

const STATUS_CLASS: Record<string, string> = {
  feasible: "sizing-status-ok",
  infeasible: "sizing-status-bad",
  unknown: "sizing-status-warn",
  error: "sizing-status-warn",
};

export function SizingSweepPanel({ report, onUseInChat }: SizingSweepPanelProps) {
  const rows = useMemo(() => (report ? sizingSweepRows(report) : []), [report]);
  const winnerDraft = useMemo(() => (report ? sizingSweepWinnerDraft(report) : null), [report]);
  const runDraft = useMemo(() => (report ? sizingSweepRunDraft(report) : null), [report]);

  if (!isSizingSweepReportMeaningful(report)) return null;

  return (
    <PanelShell
      storageKey="sizingsweep"
      title="Sizing sweep"
      icon={<TrendingUp className="h-4 w-4" aria-hidden="true" />}
      summary={sizingSweepSummary(report!)}
    >
      {runDraft || winnerDraft ? (
        <div className="insp-panel-actions">
          {winnerDraft ? (
            <button
              type="button"
              className="sizing-sweep-action"
              onClick={() => onUseInChat?.(winnerDraft)}
              disabled={!onUseInChat}
              title="Draft the winning value into the composer"
            >
              Apply winner
            </button>
          ) : null}
          {runDraft ? (
            <button
              type="button"
              className="sizing-sweep-action"
              onClick={() => onUseInChat?.(runDraft)}
              disabled={!onUseInChat}
              title="Draft an approval-gated opt.sizing_sweep run with the same parameter and values"
            >
              Run again
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="sizing-sweep-table-wrap">
        <table className="sizing-sweep-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Value</th>
              <th>Status</th>
              <th>Objective</th>
              <th>Stress</th>
              <th>Disp.</th>
              <th>Mass</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.rank}
                className={row.isRecommended ? "sizing-sweep-recommended" : undefined}
              >
                <td>{row.rank}</td>
                <td>{fmt(row.value)}</td>
                <td>
                  <span className={`sizing-sweep-status ${STATUS_CLASS[row.status] ?? "sizing-status-warn"}`}>
                    {row.status}
                  </span>
                </td>
                <td>{fmt(row.objectiveValue)}</td>
                <td>{fmt(row.stress)}</td>
                <td>{fmt(row.displacement)}</td>
                <td>{fmt(row.mass)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="sizing-sweep-foot">
        Read-only ranking; applying the winner still flows through the approval-gated <code>cad.edit_parameter</code> path.
      </div>
    </PanelShell>
  );
}
