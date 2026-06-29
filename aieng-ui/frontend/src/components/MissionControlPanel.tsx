import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Circle,
  CircleDashed,
  Clipboard,
  Download,
  FileText,
  PackageCheck,
  ShieldCheck,
} from "lucide-react";

import type {
  LifecycleItem,
  MissionControlModel,
  MissionControlStatus,
  MissionPackageIdentityItem,
} from "../app/missionControl";

type MissionControlPanelProps = {
  model: MissionControlModel;
  onCopyDraft?: (draft: string) => void;
  /** Open the engineering report (real wired action). */
  onOpenReport?: () => void;
  /** Export the review/evidence packet (real wired action). */
  onExportPacket?: () => void;
  /** True while the packet export is in flight. */
  packetExporting?: boolean;
};

const STATUS_LABEL: Record<MissionControlStatus, string> = {
  ready: "ready",
  missing: "missing",
  blocked: "blocked",
  unknown: "unknown",
};

function statusIcon(status: MissionControlStatus) {
  if (status === "ready") return <CheckCircle2 className="h-4 w-4" aria-hidden="true" />;
  if (status === "blocked") return <AlertTriangle className="h-4 w-4" aria-hidden="true" />;
  return <ShieldCheck className="h-4 w-4" aria-hidden="true" />;
}

/** Lifecycle checklist icons: done / needs-attention / not-done / locked. */
function lifecycleIcon(status: MissionControlStatus) {
  if (status === "ready") return <CheckCircle2 className="h-4 w-4" aria-hidden="true" />;
  if (status === "blocked") return <AlertTriangle className="h-4 w-4" aria-hidden="true" />;
  if (status === "missing") return <Circle className="h-4 w-4" aria-hidden="true" />;
  return <CircleDashed className="h-4 w-4" aria-hidden="true" />;
}

function packageMemberPreview(item: MissionPackageIdentityItem): string {
  if (!item.members.length) return "No member visible";
  const preview = item.members.slice(0, 2).join(", ");
  return item.members.length > 2 ? `${preview}, +${item.members.length - 2}` : preview;
}

function LifecycleRow({ item }: { item: LifecycleItem }) {
  return (
    <li className={`mission-life mission-life-${item.status}`}>
      <span className="mission-life-icon" aria-hidden="true">
        {lifecycleIcon(item.status)}
      </span>
      <div className="mission-life-body">
        <strong>{item.label}</strong>
        <span>{item.detail}</span>
      </div>
    </li>
  );
}

/**
 * Mission Control — the at-a-glance project status surface. The default view
 * answers, in plain engineering language: where the project is (status line),
 * what is done vs missing (lifecycle checklist), and the one recommended next
 * action. The full evidence catalogue (package passport, trust badges, raw
 * checks, workflow, file references) is preserved under "Advanced details" so
 * traceability is intact but not dominant.
 */
export function MissionControlPanel({
  model,
  onCopyDraft,
  onOpenReport,
  onExportPacket,
  packetExporting,
}: MissionControlPanelProps) {
  const [detailOpen, setDetailOpen] = useState(false);
  const detailCount = model.cards.length + model.workflowSteps.length;
  const action = model.primaryAction;

  return (
    <section className="mission-control-card" aria-label="Project status">
      <div className="mission-control-head">
        <div className="mission-control-title">
          <PackageCheck className="h-4 w-4" aria-hidden="true" />
          <div>
            <strong>Project status</strong>
            <span>{model.projectName}</span>
          </div>
        </div>
        <span className={`mission-status mission-status-${model.packageStatus}`}>
          {STATUS_LABEL[model.packageStatus]}
        </span>
      </div>

      <p className="mission-headline-line">{model.headline}</p>

      <ul className="mission-lifecycle" aria-label="Project lifecycle checklist">
        {model.lifecycle.map((item) => (
          <LifecycleRow key={item.key} item={item} />
        ))}
      </ul>

      <div className="mission-action">
        <div className="mission-action-text">
          <span>Recommended next step</span>
          <strong>{action.label}</strong>
          <p>{action.detail}</p>
        </div>
        <div className="mission-action-buttons">
          {action.kind === "report" ? (
            <>
              <button
                type="button"
                className="mission-action-primary"
                onClick={() => onOpenReport?.()}
                disabled={!onOpenReport}
                title="Open the engineering report in a new tab"
              >
                <FileText className="h-4 w-4" aria-hidden="true" />
                <span>Generate report</span>
              </button>
              {onExportPacket ? (
                <button
                  type="button"
                  className="mission-action-secondary"
                  onClick={() => onExportPacket()}
                  disabled={packetExporting}
                  title="Export the raw traceability/evidence packet"
                >
                  <Download className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{packetExporting ? "Exporting…" : "Export evidence packet"}</span>
                </button>
              ) : null}
              {action.draft ? (
                <button
                  type="button"
                  className="mission-action-secondary"
                  onClick={() => onCopyDraft?.(action.draft as string)}
                  disabled={!onCopyDraft}
                  title="Copy a bounded prompt for your connected agent"
                >
                  <Clipboard className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Copy agent prompt</span>
                </button>
              ) : null}
            </>
          ) : action.kind === "review_approval" ? (
            <span className="mission-action-hint">Review the pending approval shown below.</span>
          ) : action.kind === "draft" && action.draft ? (
            <button
              type="button"
              className="mission-action-primary"
              onClick={() => onCopyDraft?.(action.draft as string)}
              disabled={!onCopyDraft}
              title="Copy a bounded prompt for your connected agent"
            >
              <Clipboard className="h-4 w-4" aria-hidden="true" />
              <span>Copy prompt</span>
            </button>
          ) : (
            <span className="mission-action-hint">{action.detail}</span>
          )}
        </div>
      </div>

      <button
        type="button"
        className="mission-detail-toggle"
        aria-expanded={detailOpen}
        onClick={() => setDetailOpen((v) => !v)}
      >
        <ChevronDown className={detailOpen ? "h-4 w-4 is-open" : "h-4 w-4"} aria-hidden="true" />
        <span>{detailOpen ? "Hide advanced details" : "Advanced details"}</span>
        <em>
          {model.packageName} · {detailCount} checks
        </em>
      </button>

      {detailOpen ? (
        <div className="mission-detail">
          <div className="mission-package">
            <div>
              <span>.aieng evidence package</span>
              <strong>{model.packageName}</strong>
            </div>
            <p>{model.packageDetail}</p>
          </div>

          <div className="mission-passport" aria-label=".aieng package passport">
            <div className="mission-passport-head">
              <strong>Package passport</strong>
              <span>evidence inside this .aieng</span>
            </div>
            <div className="mission-passport-list">
              {model.packageIdentity.map((item) => (
                <article
                  key={item.key}
                  className={`mission-passport-item mission-passport-${item.status}`}
                  title={item.detail}
                >
                  <div>
                    <strong>{item.label}</strong>
                    <span>{packageMemberPreview(item)}</span>
                  </div>
                  <em>
                    {item.members.length
                      ? `${item.members.length} member${item.members.length === 1 ? "" : "s"}`
                      : STATUS_LABEL[item.status]}
                  </em>
                </article>
              ))}
            </div>
          </div>

          <div className="mission-trust-badges" aria-label="Evidence trust status">
            {model.trustBadges.map((badge) => (
              <span key={badge.key} className={`mission-trust-badge mission-trust-${badge.kind}`} title={badge.detail}>
                <strong>{badge.label}</strong>
                <em>{badge.detail}</em>
              </span>
            ))}
          </div>

          <div className="mission-grid">
            {model.cards.map((card) => (
              <article key={card.key} className={`mission-tile mission-tile-${card.status}`}>
                <div className="mission-tile-head">
                  <span>{statusIcon(card.status)}</span>
                  <strong>{card.label}</strong>
                  <em>{STATUS_LABEL[card.status]}</em>
                </div>
                <p>{card.detail}</p>
                {card.meta ? <small>{card.meta}</small> : null}
              </article>
            ))}
          </div>

          <div className="mission-workflow" aria-label="Guided CAD to CAE workflow">
            <div className="mission-workflow-head">
              <strong>CAD to CAE workflow</strong>
              <span>ready / missing evidence / blocked</span>
            </div>
            <ol className="mission-workflow-list">
              {model.workflowSteps.map((step, index) => (
                <li key={step.key} className={`mission-workflow-step mission-workflow-${step.status}`}>
                  <div className="mission-workflow-index">{index + 1}</div>
                  <div className="mission-workflow-body">
                    <div className="mission-workflow-row">
                      <strong>{step.label}</strong>
                      <em>{STATUS_LABEL[step.status]}</em>
                    </div>
                    <p>{step.detail}</p>
                  </div>
                  {step.draft ? (
                    <button
                      type="button"
                      className="mission-workflow-copy"
                      onClick={() => onCopyDraft?.(step.draft as string)}
                      disabled={!onCopyDraft}
                      title={`Copy ${step.label} prompt`}
                      aria-label={`Copy ${step.label} prompt`}
                    >
                      <Clipboard className="h-3 w-3" aria-hidden="true" />
                    </button>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>

          <div className="mission-notes">
            {model.evidenceNotes.map((note) => (
              <span key={note}>{note}</span>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
