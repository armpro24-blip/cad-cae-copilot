import { useMemo } from "react";

import {
  isMeshConvergenceReportMeaningful,
  meshConvergenceRows,
  meshConvergenceRefineDraft,
  meshConvergenceSummary,
} from "../app/meshConvergenceReport";
import { Grid3x3 } from "lucide-react";

import type { MeshConvergenceReport } from "../types";
import { InfoTip } from "./InfoTip";
import { PanelShell } from "./PanelShell";
import { glossaryText } from "../app/glossary";

type MeshConvergencePanelProps = {
  report: MeshConvergenceReport | null;
  /** Prefill the composer with a re-run at a finer mesh draft. */
  onUseInChat?: (draft: string) => void;
};

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (Math.abs(value) < 1e-6) return value.toExponential(2);
  return value.toPrecision(4);
}

const VERDICT_CLASS: Record<string, string> = {
  converged: "mesh-verdict-ok",
  not_converged: "mesh-verdict-bad",
  indeterminate: "mesh-verdict-warn",
  no_solves: "mesh-verdict-warn",
  insufficient_grids: "mesh-verdict-warn",
  two_grid_relative_change_only: "mesh-verdict-warn",
};

export function MeshConvergencePanel({ report, onUseInChat }: MeshConvergencePanelProps) {
  const rows = useMemo(() => (report ? meshConvergenceRows(report) : []), [report]);
  const refineDraft = useMemo(() => (report ? meshConvergenceRefineDraft(report) : null), [report]);
  const allConverged = report?.overall_verdict === "converged";

  if (!isMeshConvergenceReportMeaningful(report)) return null;

  return (
    <PanelShell
      storageKey="meshconvergence"
      title="Mesh convergence"
      icon={<Grid3x3 className="h-4 w-4" aria-hidden="true" />}
      status={
        <span className={allConverged ? "mesh-convergence-ok" : "mesh-convergence-caution"}>
          {meshConvergenceSummary(report!)}
        </span>
      }
    >
      {refineDraft && !allConverged ? (
        <div className="insp-panel-actions">
          <button
            type="button"
            className="mesh-convergence-action"
            onClick={() => onUseInChat?.(refineDraft)}
            disabled={!onUseInChat}
            title="Draft a re-run at a finer mesh into the composer"
          >
            Finer mesh
          </button>
        </div>
      ) : null}

      <div className="mesh-convergence-table-wrap">
        <table className="mesh-convergence-table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Verdict</th>
              <th>
                <span className="th-with-info">
                  GCI fine % <InfoTip text={glossaryText("gci")} label="What is GCI?" />
                </span>
              </th>
              <th>Order</th>
              <th>Extrapolated</th>
              <th>Change %</th>
              <th>Grids</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.metric}>
                <td>{row.metric}</td>
                <td>
                  <span
                    className={`mesh-convergence-verdict ${VERDICT_CLASS[row.verdict] ?? "mesh-verdict-warn"}`}
                  >
                    {row.verdict}
                  </span>
                </td>
                <td>{fmt(row.gciFinePercent)}</td>
                <td>{fmt(row.apparentOrder)}</td>
                <td>{fmt(row.extrapolatedValue)}</td>
                <td>{fmt(row.relativeChangePercent)}</td>
                <td>{row.levelCount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mesh-convergence-foot">
        GCI is a discretization-uncertainty estimate for this geometry/refinement only — not a model-validity claim.
      </div>
    </PanelShell>
  );
}
