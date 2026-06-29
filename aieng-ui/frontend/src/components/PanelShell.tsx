import { ChevronDown } from "lucide-react";
import { type ReactNode } from "react";

import { useBrowserStorageState } from "../app/useBrowserStorageState";

type PanelShellProps = {
  /** Stable key for persisting the open/closed state across sessions. */
  storageKey: string;
  title: string;
  icon?: ReactNode;
  /** Right-aligned status badge shown in the header (visible while collapsed). */
  status?: ReactNode;
  /** Optional one-line summary shown under the title while collapsed. */
  summary?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
};

/**
 * Unified collapsible inspector card. The header is the toggle and always shows
 * the title + status, so the user can scan what's available without a wall of
 * detail — they open only the sections they care about. Open/closed state is
 * remembered per section. Gives the whole inspector rail one consistent,
 * "windowed" visual language instead of a stack of differently-styled cards.
 */
export function PanelShell({
  storageKey,
  title,
  icon,
  status,
  summary,
  defaultOpen = false,
  children,
}: PanelShellProps) {
  const [open, setOpen] = useBrowserStorageState<boolean>(
    `aieng.inspector.${storageKey}`,
    defaultOpen,
    { storage: "local" },
  );

  return (
    <section className={open ? "insp-panel is-open" : "insp-panel"}>
      <button
        type="button"
        className="insp-panel-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {icon ? (
          <span className="insp-panel-icon" aria-hidden="true">
            {icon}
          </span>
        ) : null}
        <span className="insp-panel-titlewrap">
          <strong className="insp-panel-title">{title}</strong>
          {!open && summary ? <span className="insp-panel-summary">{summary}</span> : null}
        </span>
        {status ? <span className="insp-panel-status">{status}</span> : null}
        <ChevronDown className="insp-panel-chevron" aria-hidden="true" />
      </button>
      {open ? <div className="insp-panel-body">{children}</div> : null}
    </section>
  );
}
