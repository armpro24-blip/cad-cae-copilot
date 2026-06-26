import type { Notice } from "../appTypes";
import { CommandChip } from "./CommandChip";

export function NoticeCenter({ notice, onDismiss }: { notice: Notice | null; onDismiss(): void }) {
  if (!notice) return null;

  const icon = notice.tone === "success" ? "✓" : notice.tone === "error" ? "!" : "i";

  return (
    <div className="notification-center" role="status" aria-live="polite">
      <article className={`notification-card notification-${notice.tone}`}>
        <div className="notification-icon" aria-hidden="true">
          {icon}
        </div>
        <div className="notification-copy">
          <strong>{notice.title}</strong>
          {notice.detail ? <span>{notice.detail}</span> : null}
          {notice.command ? <CommandChip command={notice.command} /> : null}
        </div>
        <button type="button" className="notification-close" onClick={onDismiss} aria-label="Dismiss notification">
          ×
        </button>
      </article>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div className="confirm-dialog-backdrop" onClick={onCancel}>
      <div
        className="confirm-dialog-panel"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h3 className="confirm-dialog-title">{title}</h3>
        <p className="confirm-dialog-message">{message}</p>
        <div className="confirm-dialog-actions">
          <button type="button" className="confirm-dialog-btn" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button type="button" className="confirm-dialog-btn" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export type ActionIconName =
  | "settings"
  | "global"
  | "refresh"
  | "plus"
  | "sample"
  | "upload"
  | "import"
  | "preview"
  | "validate"
  | "tools"
  | "send"
  | "run"
  | "view"
  | "approve"
  | "reject"
  | "report"
  | "restore"
  | "test"
  | "save";

export function ActionIcon({ name }: { name: ActionIconName }) {
  return (
    <svg className="button-icon" viewBox="0 0 24 24" aria-hidden="true">
      {name === "settings" ? (
        <>
          <path d="M12 8.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Z" />
          <path d="m18.1 10.1 1.5-1.2-1.7-2.9-1.9.8a7 7 0 0 0-1.7-1L14 3.7h-4l-.3 2.1a7 7 0 0 0-1.7 1L6.1 6 4.4 8.9l1.5 1.2a7.6 7.6 0 0 0 0 3.8l-1.5 1.2 1.7 2.9 1.9-.8a7 7 0 0 0 1.7 1l.3 2.1h4l.3-2.1a7 7 0 0 0 1.7-1l1.9.8 1.7-2.9-1.5-1.2a7.6 7.6 0 0 0 0-3.8Z" />
        </>
      ) : name === "global" ? (
        <>
          <path d="M12 3.8a8.2 8.2 0 1 0 0 16.4 8.2 8.2 0 0 0 0-16.4Z" />
          <path d="M4.2 12h15.6M12 3.8c2.1 2.2 3.1 4.9 3.1 8.2s-1 6-3.1 8.2M12 3.8C9.9 6 8.9 8.7 8.9 12s1 6 3.1 8.2" />
        </>
      ) : name === "refresh" ? (
        <>
          <path d="M19 7.2v4.2h-4.2" />
          <path d="M18 11.4a6.4 6.4 0 1 0-1.9 4.5" />
        </>
      ) : name === "plus" ? (
        <>
          <path d="M12 5v14M5 12h14" />
        </>
      ) : name === "sample" ? (
        <>
          <path d="M5 5.8h14v12.4H5V5.8Z" />
          <path d="M8 9h8M8 12h5M8 15h7" />
        </>
      ) : name === "upload" ? (
        <>
          <path d="M12 16V5.5" />
          <path d="m8.2 9.2 3.8-3.8 3.8 3.8" />
          <path d="M5 17.8h14" />
        </>
      ) : name === "import" ? (
        <>
          <path d="M5 5.5h8.5l3.5 3.5v9.5H5V5.5Z" />
          <path d="M13.5 5.5V9H17" />
          <path d="M8 13h7M12 9.8V16" />
        </>
      ) : name === "preview" ? (
        <>
          <path d="M3.8 12s3-5.2 8.2-5.2S20.2 12 20.2 12s-3 5.2-8.2 5.2S3.8 12 3.8 12Z" />
          <path d="M12 9.4a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2Z" />
        </>
      ) : name === "validate" ? (
        <>
          <path d="m5 12 4 4 10-10" />
        </>
      ) : name === "tools" ? (
        <>
          <path d="M14.8 5.2a4 4 0 0 0 4.1 4.1l-8.4 8.4a2.2 2.2 0 0 1-3.1-3.1l8.4-8.4Z" />
          <path d="m5.7 18.3 2 2" />
        </>
      ) : name === "send" ? (
        <>
          <path d="m4.2 12 15.6-7-4.3 15.2-3.3-6.1-8-2.1Z" />
          <path d="m12.2 14.1 3.1-3.7" />
        </>
      ) : name === "run" ? (
        <>
          <path d="M8 5.8v12.4L18 12 8 5.8Z" />
        </>
      ) : name === "view" ? (
        <>
          <path d="M5 5h10l4 4v10H5V5Z" />
          <path d="M15 5v4h4M8 13h8M8 16h5" />
        </>
      ) : name === "approve" ? (
        <>
          <path d="M6 12.5 10.2 16 18 7.8" />
        </>
      ) : name === "reject" ? (
        <>
          <path d="M7 7 17 17M17 7 7 17" />
        </>
      ) : name === "report" ? (
        <>
          <path d="M6 4.8h9.5L18 7.3v11.9H6V4.8Z" />
          <path d="M15.5 4.8v2.5H18M8.5 10h7M8.5 13h7M8.5 16h4.5" />
        </>
      ) : name === "restore" ? (
        <>
          <path d="M6 8H3.8V5.8" />
          <path d="M4.3 8A7.2 7.2 0 1 1 6 17.8" />
        </>
      ) : name === "test" ? (
        <>
          <path d="M9 4.8v4.6l-3.7 6.4A2.2 2.2 0 0 0 7.2 19h9.6a2.2 2.2 0 0 0 1.9-3.2L15 9.4V4.8" />
          <path d="M8 4.8h8M8.1 14.5h7.8" />
        </>
      ) : (
        <>
          <path d="M6 5h10l2 2v12H6V5Z" />
          <path d="M9 5v5h6V5M9 16h6" />
        </>
      )}
    </svg>
  );
}
