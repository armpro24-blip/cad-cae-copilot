import { AlertTriangle, CheckCircle2, CircleHelp, ClipboardCheck, ShieldCheck } from "lucide-react";

import { PanelShell } from "./PanelShell";
import {
  isValueDemoCheckMeaningful,
  normalizeValueDemoStatus,
  valueDemoCheckRows,
  valueDemoFirstMissing,
  valueDemoHeadline,
  type ValueDemoCheckRow,
} from "../app/valueDemoCheck";
import type { ValueDemoCheckResponse } from "../types";

type ValueDemoCheckPanelProps = {
  check: ValueDemoCheckResponse | null;
};

function rowIcon(row: ValueDemoCheckRow) {
  if (row.status === "pass") return <CheckCircle2 className="h-3 w-3" aria-hidden="true" />;
  if (row.status === "fail") return <AlertTriangle className="h-3 w-3" aria-hidden="true" />;
  if (row.status === "warning") return <ShieldCheck className="h-3 w-3" aria-hidden="true" />;
  return <CircleHelp className="h-3 w-3" aria-hidden="true" />;
}

export function ValueDemoCheckPanel({ check }: ValueDemoCheckPanelProps) {
  if (!isValueDemoCheckMeaningful(check)) return null;

  const status = normalizeValueDemoStatus(check?.status);
  const rows = valueDemoCheckRows(check);
  const missing = valueDemoFirstMissing(check);
  const boundary = check?.honesty_boundaries?.[0] ?? "Synthetic fallback fields do not count as a passing demo.";

  return (
    <PanelShell
      storageKey="valuedemo"
      title="Value demo check"
      icon={<ClipboardCheck className="h-4 w-4" aria-hidden="true" />}
      status={<span className={`value-demo-status value-demo-status-${status}`}>{valueDemoHeadline(check)}</span>}
      summary={missing ? `missing ${missing}` : undefined}
    >
      {missing ? (
        <div className="value-demo-missing">
          <span>First missing evidence</span>
          <code>{missing}</code>
        </div>
      ) : null}

      <div className="value-demo-list">
        {rows.map((row) => (
          <div key={row.id} className={`value-demo-row value-demo-row-${row.status}`}>
            <span className="value-demo-row-icon">{rowIcon(row)}</span>
            <span className="value-demo-row-id">{row.id.replace(/_/g, " ")}</span>
            <span className="value-demo-row-message">{row.message}</span>
          </div>
        ))}
      </div>

      <div className="value-demo-foot">
        {boundary} <code>claim_advancement={check?.claim_advancement ?? "none"}</code>
      </div>
    </PanelShell>
  );
}
