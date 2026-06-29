import { useMemo, useState } from "react";
import { SlidersHorizontal } from "lucide-react";

import {
  editDraftForParameter,
  editDraftForParameterValue,
  formatNumber,
  formatRange,
  groupParametersByScope,
  parameterEditWarning,
  SCOPE_HINT,
  SCOPE_LABEL,
  type ParameterScope,
} from "../app/editableParameters";
import { PanelShell } from "./PanelShell";
import type { EditableParameter } from "../types";

type EditableParametersPanelProps = {
  parameters: EditableParameter[];
  /** Prefill the composer with a "/modify set <name> to <value>" draft for a parameter. */
  onUseInChat?: (draft: string) => void;
  /** Open a structured parametric edit proposal for review (kept outside this panel). */
  onPreview?: (param: EditableParameter, value: number) => void;
};

/**
 * Parametric editing, simplified to its essence: type a new value, click Set.
 * Each row drafts a complete `/modify set <name> to <value>` for the connected
 * agent (the edit still flows through the plan-confirmed, approval-gated path —
 * this panel never mutates geometry). The technical constant name and the
 * structured-proposal path are demoted to secondary affordances so the common
 * "just change a dimension" case stays uncluttered. Out-of-range / global edits
 * show an honest, non-blocking warning.
 */
export function EditableParametersPanel({ parameters, onUseInChat, onPreview }: EditableParametersPanelProps) {
  const safeParameters = parameters ?? [];
  const groups = useMemo(() => groupParametersByScope(safeParameters), [safeParameters]);

  if (!safeParameters.length) return null;

  return (
    <PanelShell
      storageKey="editparams"
      title="Edit dimensions"
      icon={<SlidersHorizontal className="h-4 w-4" aria-hidden="true" />}
      status={<span className="editparams-count">{safeParameters.length}</span>}
      summary={`${safeParameters.length} editable`}
    >
      {groups.map((group) => (
        <div key={group.scope} className="editparams-group">
          {groups.length > 1 || group.scope !== "local" ? (
            <div
              className={`editparams-scope editparams-scope-${group.scope as ParameterScope}`}
              title={SCOPE_HINT[group.scope]}
            >
              {SCOPE_LABEL[group.scope]}
              <span className="editparams-scope-count">{group.parameters.length}</span>
            </div>
          ) : null}

          {group.parameters.map((param: EditableParameter) => (
            <ParameterRow
              key={`${param.feature_id ?? "f"}:${param.parameter_name}`}
              param={param}
              onUseInChat={onUseInChat}
              onPreview={onPreview}
            />
          ))}
        </div>
      ))}

      <div className="editparams-foot">
        Set a value to draft a <code>/modify</code> for your agent — it applies through the usual approval step.
      </div>
    </PanelShell>
  );
}

function ParameterRow({
  param,
  onUseInChat,
  onPreview,
}: {
  param: EditableParameter;
  onUseInChat?: (draft: string) => void;
  onPreview?: (param: EditableParameter, value: number) => void;
}) {
  const [value, setValue] = useState<string>(formatNumber(param.current_value));
  const range = formatRange(param.min_value, param.max_value);
  const numeric = Number(value);
  const isNumber = value.trim() !== "" && Number.isFinite(numeric);
  const changed = isNumber && numeric !== param.current_value;
  const warning = isNumber ? parameterEditWarning(param, numeric) : null;

  const commit = () => {
    if (!onUseInChat || !isNumber) return;
    onUseInChat(editDraftForParameterValue(param, numeric));
  };

  return (
    <div className="ep-row">
      <div className="ep-row-main">
        <button
          type="button"
          className="ep-name"
          onClick={() => onUseInChat?.(editDraftForParameter(param))}
          disabled={!onUseInChat}
          title={`${param.parameter_name} (constant ${param.cad_parameter_name})`}
        >
          {param.parameter_name.replace(/_/g, " ")}
        </button>
        <div className="ep-edit">
          <input
            type="number"
            className="ep-input"
            value={value}
            min={param.min_value ?? undefined}
            max={param.max_value ?? undefined}
            step="any"
            aria-label={`New value for ${param.parameter_name}`}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
            }}
          />
          <button
            type="button"
            className={changed ? "ep-set is-changed" : "ep-set"}
            onClick={commit}
            disabled={!onUseInChat || !isNumber}
            title={onUseInChat ? `Draft /modify set ${param.parameter_name} to ${value}` : "Connect a composer to edit"}
          >
            Set
          </button>
        </div>
      </div>
      <div className="ep-row-meta">
        {range ? <span title="allowed range">{range}</span> : null}
        <span title={`feature ${param.feature_name}`}>{param.feature_name}</span>
        {onPreview && isNumber && changed ? (
          <button
            type="button"
            className="ep-preview"
            onClick={() => onPreview(param, numeric)}
            title="Review a structured proposal (range check + change preview) before applying"
          >
            Preview…
          </button>
        ) : null}
      </div>
      {warning ? (
        <span className="ep-warning" role="note">⚠ {warning}</span>
      ) : null}
    </div>
  );
}
