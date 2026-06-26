import { useState } from "react";
import { X } from "lucide-react";
import { CommandChip } from "./CommandChip";
import { COMMAND_KIND_LABEL, filterCommandDocs } from "../app/commandReference";

type CommandReferenceProps = {
  open: boolean;
  onClose(): void;
};

/**
 * Command cheat-sheet (#398): the routed `/command` vocabulary with a one-line
 * purpose + a copy-able example each, so a new user can learn what to type to
 * their agent without external docs. Read+Handoff — it teaches the handoff, it
 * never executes anything.
 */
export function CommandReference({ open, onClose }: CommandReferenceProps) {
  const [query, setQuery] = useState("");
  if (!open) return null;
  const docs = filterCommandDocs(query);

  return (
    <aside className="command-reference" aria-label="Command reference">
      <div className="command-reference-head">
        <div>
          <strong>Commands</strong>
          <span>Type these to your connected agent — the workbench shows results, the agent does the work.</span>
        </div>
        <button
          type="button"
          className="command-reference-close"
          onClick={onClose}
          aria-label="Close command reference"
        >
          <X size={16} aria-hidden />
        </button>
      </div>
      <input
        type="search"
        className="command-reference-search"
        placeholder="Search commands…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-label="Search commands"
      />
      <ul className="command-reference-list">
        {docs.map((doc) => (
          <li key={doc.command} className="command-reference-item">
            <div className="command-reference-item-head">
              <code className="command-reference-name">{doc.command}</code>
              <span className={`command-reference-kind kind-${doc.kind}`}>{COMMAND_KIND_LABEL[doc.kind]}</span>
            </div>
            <p className="command-reference-purpose">{doc.purpose}</p>
            <CommandChip command={doc.example} />
          </li>
        ))}
        {docs.length === 0 ? <li className="command-reference-empty">No matching commands.</li> : null}
      </ul>
    </aside>
  );
}
