import { useMemo, useState } from "react";

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
import type { EditableParameter } from "../types";
import { ParametricEditProposalPanel } from "./ParametricEditProposalPanel";

type EditableParametersPanelProps = {
  parameters: EditableParameter[];
  /** Prefill the composer with a "/modify set <name> to <value>" draft for a parameter. */
  onUseInChat?: (draft: string) => void;
  /** Project ID for proposal preview; when omitted the Preview button is hidden. */
  projectId?: string;
};

/**
 * The "point" half of point-and-shoot editing: the CAD parameters editable fast
 * via cad.edit_parameter, grouped by editing scope (local = safe single-part,
 * global = shared/ripples). Each row has an inline value field (#223) that drafts
 * a complete `/modify set <name> to <value>` into the composer; the edit itself
 * still flows through the existing plan-confirmed, approval-gated path — this
 * panel never mutates geometry. Out-of-range / global edits show an honest,
 * non-blocking warning (the backend routes them to confirmation).
 */
export function EditableParametersPanel({ parameters, onUseInChat, projectId }: EditableParametersPanelProps) {
  const safeParameters = parameters ?? [];
  const groups = useMemo(() => groupParametersByScope(safeParameters), [safeParameters]);

  if (!safeParameters.length) return null;

  return (
    <section className="editparams-card" aria-label="Editable parameters">
      <div className="editparams-head">
        <strong>Editable parameters</strong>
        <span>{parameters.length}</span>
      </div>

      {groups.map((group) => (
        <div key={group.scope} className="editparams-group">
          <div
            className={`editparams-scope editparams-scope-${group.scope as ParameterScope}`}
            title={SCOPE_HINT[group.scope]}
          >
            {SCOPE_LABEL[group.scope]}
            <span className="editparams-scope-count">{group.parameters.length}</span>
          </div>

          {group.parameters.map((param: EditableParameter) => (
            <ParameterRow
              key={`${param.feature_id ?? "f"}:${param.parameter_name}`}
              param={param}
              onUseInChat={onUseInChat}
              projectId={projectId}
            />
          ))}
        </div>
      ))}

      <div className="editparams-foot">
        Set a value and click <strong>Set</strong> to draft a <code>/modify</code>, or <strong>Preview</strong> to review a structured proposal.
      </div>
    </section>
  );
}

function ParameterRow({
  param,
  onUseInChat,
  projectId,
}: {
  param: EditableParameter;
  onUseInChat?: (draft: string) => void;
  projectId?: string;
}) {
  const [value, setValue] = useState<string>(formatNumber(param.current_value));
  const [showPreview, setShowPreview] = useState(false);
  const range = formatRange(param.min_value, param.max_value);
  const numeric = Number(value);
  const isNumber = value.trim() !== "" && Number.isFinite(numeric);
  const warning = isNumber ? parameterEditWarning(param, numeric) : null;

  const commit = () => {
    if (!onUseInChat || !isNumber) return;
    onUseInChat(editDraftForParameterValue(param, numeric));
  };

  return (
    <div className="editparams-row">
      <button
        type="button"
        className="editparams-name"
        onClick={() => onUseInChat?.(editDraftForParameter(param))}
        disabled={!onUseInChat}
        title={onUseInChat ? `Draft a /modify for ${param.parameter_name}` : param.parameter_name}
      >
        {param.parameter_name.replace(/_/g, " ")}
      </button>
      <code className="editparams-const" title={`constant ${param.cad_parameter_name}`}>
        {param.cad_parameter_name}
      </code>
      <input
        type="number"
        className="editparams-input"
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
        className="editparams-set"
        onClick={commit}
        disabled={!onUseInChat || !isNumber}
        title={onUseInChat ? `Draft /modify set ${param.parameter_name} to ${value}` : "Connect a composer to edit"}
      >
        Set
      </button>
      {projectId && isNumber ? (
        <button
          type="button"
          className="editparams-preview"
          onClick={() => setShowPreview((s) => !s)}
          title="Review a structured parametric edit proposal before applying"
        >
          {showPreview ? "Hide" : "Preview"}
        </button>
      ) : null}
      {range ? <span className="editparams-range" title="allowed range">{range}</span> : null}
      <span className="editparams-feature" title={`feature ${param.feature_name}`}>
        {param.feature_name}
      </span>
      {warning ? (
        <span className="editparams-warning" role="note">⚠ {warning}</span>
      ) : null}
      {showPreview && projectId && isNumber ? (
        <div className="editparams-proposal">
          <ParametricEditProposalPanel
            projectId={projectId}
            param={param}
            value={numeric}
            onApplied={() => setShowPreview(false)}
            onCancelled={() => setShowPreview(false)}
          />
        </div>
      ) : null}
    </div>
  );
}
