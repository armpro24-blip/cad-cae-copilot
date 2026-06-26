import { useState } from "react";
import { Check, Copy } from "lucide-react";

type CommandChipProps = {
  /** The full `/command` text to copy. */
  command: string;
  /**
   * Base class. The icon span uses `${className}-icon`, so each host can theme
   * the chip via its own scoped CSS (`command-chip` default, `onboarding-cmd`
   * inside the onboarding overlay).
   */
  className?: string;
};

/**
 * One-click copy chip for a drafted `/command` — the heart of the Read+Handoff
 * model (#398). The GUI never executes; it hands a copy-able command to the
 * user's agent. Copies via the clipboard API with a transient "Copied" state and
 * degrades gracefully when the clipboard is unavailable (the text stays
 * selectable).
 */
export function CommandChip({ command, className = "command-chip" }: CommandChipProps) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable (e.g. insecure context) — selecting the text still works */
    }
  };
  return (
    <button
      type="button"
      className={className}
      onClick={copy}
      title="Copy command for your agent"
      aria-label={`Copy command: ${command}`}
    >
      <code>{command}</code>
      <span className={`${className}-icon`}>
        {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
        {copied ? "Copied" : "Copy"}
      </span>
    </button>
  );
}
