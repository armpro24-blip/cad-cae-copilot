import { Activity, AlertTriangle, CheckCircle2, Clock3, Copy, FileText, History, ListTree, ShieldCheck } from "lucide-react";

import type { ProjectTimeline, ProjectTimelineEntry, ProjectTimelineEntryKind, TimelineNextAction, TimelineSnapshot } from "../app/projectTimeline";
import { PanelShell } from "./PanelShell";

type ProjectTimelinePanelProps = {
  timeline: ProjectTimeline | null;
  onRestoreSnapshot?(snapshotId: string): void;
  onApproveRun?(runId: string): void;
  onRejectRun?(runId: string): void;
  onCopyNextAction?(text: string): void;
};

const KIND_ICON: Record<ProjectTimelineEntryKind, typeof Activity> = {
  run: Clock3,
  approval: ShieldCheck,
  tool: Activity,
  snapshot: History,
  artifact: FileText,
  next_action: Activity,
  failure: AlertTriangle,
};

function fmtTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function tone(entry: ProjectTimelineEntry): string {
  if (entry.kind === "failure") return "timeline-entry-failure";
  if (entry.kind === "approval") return "timeline-entry-approval";
  if (entry.kind === "snapshot") return "timeline-entry-snapshot";
  if (entry.kind === "artifact") return "timeline-entry-artifact";
  return "timeline-entry-neutral";
}

function nextActionTone(action: TimelineNextAction): string {
  if (action.availableNow === false) return "project-timeline-next-blocked";
  if (action.availableNow === true) return "project-timeline-next-available";
  return "project-timeline-next-neutral";
}

function nextActionTitle(action: TimelineNextAction): string {
  return action.tool ? `${action.tool}: ${action.label}` : action.label;
}

function codeText(details: TimelineNextAction["blockedReasonCodeDetails"], codes: string[]): string {
  const byCode = new Map(details.map((item) => [item.code, item]));
  return codes.map((code) => byCode.get(code)?.label || code).join(", ");
}

function codeTitle(details: TimelineNextAction["blockedReasonCodeDetails"]): string | undefined {
  const text = details
    .map((item) => item.recommendedAction || item.description)
    .filter(Boolean)
    .join(" ");
  return text || undefined;
}

function nextActionInstruction(action: TimelineNextAction): string {
  const lines = [
    "Use this advisory next action from the AIENG project timeline.",
    action.tool ? `Tool: ${action.tool}` : "Tool: not specified; inspect the project context first.",
    `Action: ${action.label}`,
  ];
  if (action.availableNow === false) {
    lines.push("Status: blocked or not available yet; do not run until the blocker is resolved.");
  } else if (action.availableNow === true) {
    lines.push("Status: available now, subject to normal AIENG approval and safety gates.");
  } else {
    lines.push("Status: advisory; verify current project context before acting.");
  }
  if (action.blockedReason) lines.push(`Blocked reason: ${action.blockedReason}`);
  if (action.blockedReasonCodeDetails.length) {
    lines.push("Blocked reason details:");
    for (const detail of action.blockedReasonCodeDetails) {
      lines.push(`- ${detail.code}: ${detail.recommendedAction || detail.description || detail.label}`);
    }
  } else if (action.blockedReasonCodes.length) {
    lines.push(`Blocked reason codes: ${action.blockedReasonCodes.join(", ")}`);
  }
  if (action.resolvesBlockedReasonCodeDetails.length) {
    lines.push("May resolve:");
    for (const detail of action.resolvesBlockedReasonCodeDetails) {
      lines.push(`- ${detail.code}: ${detail.recommendedAction || detail.description || detail.label}`);
    }
  } else if (action.resolvesBlockedReasonCodes.length) {
    lines.push(`May resolve codes: ${action.resolvesBlockedReasonCodes.join(", ")}`);
  }
  if (action.safetyFlags.length) lines.push(`Safety flags: ${action.safetyFlags.join(", ")}`);
  lines.push("Never bypass approval, never claim solver evidence without a completed solver run.");
  return lines.join("\n");
}

function snapshotMeta(snapshot: TimelineSnapshot): string {
  const parts = [
    snapshot.toolName,
    snapshot.partCount === null ? null : `${snapshot.partCount} part${snapshot.partCount === 1 ? "" : "s"}`,
    snapshot.createdAt ? fmtTime(snapshot.createdAt) : null,
  ].filter(Boolean);
  return parts.join(" · ");
}

export function ProjectTimelinePanel({
  timeline,
  onRestoreSnapshot,
  onApproveRun,
  onRejectRun,
  onCopyNextAction,
}: ProjectTimelinePanelProps) {
  if (!timeline || timeline.entries.length === 0) return null;
  const visible = timeline.entries.slice(0, 12);

  const runSummary = `${timeline.runCount} run${timeline.runCount === 1 ? "" : "s"}${
    timeline.activityCount ? ` · ${timeline.activityCount} event${timeline.activityCount === 1 ? "" : "s"}` : ""
  }`;
  const statusBadge = timeline.warningCount ? (
    <span className="project-timeline-warning">
      <AlertTriangle className="h-3.5 w-3.5" />
      {timeline.warningCount} malformed receipt
    </span>
  ) : timeline.unstructuredFailureCount ? (
    <span className="project-timeline-warning">
      <AlertTriangle className="h-3.5 w-3.5" />
      {timeline.unstructuredFailureCount} unstructured failure
    </span>
  ) : timeline.diagnosticCount ? (
    <span className="project-timeline-ok">
      <CheckCircle2 className="h-3.5 w-3.5" />
      {timeline.diagnosticCount} diagnostic{timeline.diagnosticCount === 1 ? "" : "s"}
    </span>
  ) : timeline.snapshotCount ? (
    <span className="project-timeline-ok">
      <History className="h-3.5 w-3.5" />
      {timeline.snapshotCount} snapshot{timeline.snapshotCount === 1 ? "" : "s"}
    </span>
  ) : (
    <span className="project-timeline-ok">
      <CheckCircle2 className="h-3.5 w-3.5" />
      Read-only
    </span>
  );

  return (
    <PanelShell
      storageKey="timeline"
      title="Project timeline"
      icon={<ListTree className="h-4 w-4" aria-hidden="true" />}
      status={statusBadge}
      summary={runSummary}
    >
      <ol className="project-timeline-list">
        {visible.map((entry) => {
          const Icon = KIND_ICON[entry.kind] ?? Activity;
          return (
            <li key={entry.id} className={`project-timeline-entry ${tone(entry)}`}>
              <div className="project-timeline-icon" aria-hidden="true">
                <Icon className="h-4 w-4" />
              </div>
              <div className="project-timeline-body">
                <div className="project-timeline-row">
                  <strong>{entry.title}</strong>
                  <time dateTime={entry.timestamp}>{fmtTime(entry.timestamp)}</time>
                </div>
                {entry.detail ? <p>{entry.detail}</p> : null}
                {entry.diagnostic ? (
                  <div className="project-timeline-diagnostic" title={entry.diagnostic.message}>
                    <small>{entry.diagnostic.code}</small>
                    {entry.diagnostic.remediation ? <p>{entry.diagnostic.remediation}</p> : null}
                  </div>
                ) : null}
                {entry.actionableApproval && entry.sourceRunId !== "activity" && (onApproveRun || onRejectRun) ? (
                  <div className="project-timeline-actions" aria-label="Runtime approval actions">
                    {onApproveRun ? (
                      <button type="button" onClick={() => onApproveRun(entry.sourceRunId)} title="Approve this runtime step">
                        Approve
                      </button>
                    ) : null}
                    {onRejectRun ? (
                      <button
                        type="button"
                        className="ghost-button"
                        onClick={() => onRejectRun(entry.sourceRunId)}
                        title="Deny this runtime step"
                      >
                        Deny
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {entry.snapshots.length ? (
                  <div className="project-timeline-snapshots" aria-label="CAD snapshots">
                    {entry.snapshots.slice(0, 4).map((snapshot) => (
                      <span key={`${snapshot.restored ? "restored" : "available"}:${snapshot.id}`}>
                        <strong>{snapshot.restored ? `Restored ${snapshot.id}` : snapshot.id}</strong>
                        {snapshotMeta(snapshot) ? <small>{snapshotMeta(snapshot)}</small> : null}
                        {snapshot.namedParts.length ? <em>{snapshot.namedParts.slice(0, 4).join(", ")}</em> : null}
                        {!snapshot.restored && onRestoreSnapshot ? (
                          <button
                            type="button"
                            className="project-timeline-snapshot-restore"
                            onClick={() => onRestoreSnapshot(snapshot.id)}
                            title="Start an approval-gated restore for this CAD snapshot"
                          >
                            Restore
                          </button>
                        ) : null}
                      </span>
                    ))}
                  </div>
                ) : null}
                {entry.artifacts.length ? (
                  <div className="project-timeline-chips" aria-label="Artifacts">
                    {entry.artifacts.slice(0, 4).map((artifact) => (
                      <code key={artifact}>{artifact}</code>
                    ))}
                  </div>
                ) : null}
                {entry.nextActions.length ? (
                  <div className="project-timeline-next" aria-label="Advisory next actions">
                    {entry.nextActions.slice(0, 3).map((action, index) => (
                      <span key={`${action.tool ?? "advisory"}:${action.label}:${index}`} className={nextActionTone(action)}>
                        <span className="project-timeline-next-head">
                          <strong>{nextActionTitle(action)}</strong>
                          {onCopyNextAction ? (
                            <button
                              type="button"
                              className="project-timeline-next-copy"
                              onClick={() => onCopyNextAction(nextActionInstruction(action))}
                              title="Copy MCP instruction for this next action"
                              aria-label={`Copy next action: ${nextActionTitle(action)}`}
                            >
                              <Copy className="h-3 w-3" />
                            </button>
                          ) : null}
                        </span>
                        {action.blockedReason ? <em>Blocked: {action.blockedReason}</em> : null}
                        {action.blockedReasonCodes.length ? (
                          <small title={codeTitle(action.blockedReasonCodeDetails)}>
                            {codeText(action.blockedReasonCodeDetails, action.blockedReasonCodes)}
                          </small>
                        ) : null}
                        {action.resolvesBlockedReasonCodes.length ? (
                          <small title={codeTitle(action.resolvesBlockedReasonCodeDetails)}>
                            resolves {codeText(action.resolvesBlockedReasonCodeDetails, action.resolvesBlockedReasonCodes)}
                          </small>
                        ) : null}
                        {action.safetyFlags.length ? <small>{action.safetyFlags.join(" · ")}</small> : null}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      <div className="project-timeline-foot">
        Next actions are advisory text only; solver claims require solver-run evidence.
      </div>
    </PanelShell>
  );
}
