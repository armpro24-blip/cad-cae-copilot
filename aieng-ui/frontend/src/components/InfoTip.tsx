import { Info } from "lucide-react";

type InfoTipProps = {
  /** Plain-language explanation shown in the popover. */
  text: string;
  /** Accessible label for the trigger (defaults to a generic one). */
  label?: string;
};

/**
 * Inline help affordance (#399): a small ⓘ that reveals a one-line, plain-language
 * explanation of a domain term on hover or keyboard focus. Read-only; purely
 * explanatory. The popover is shown via CSS (`:hover` / `:focus-within`) so it
 * needs no state and stays accessible to keyboard users.
 */
export function InfoTip({ text, label }: InfoTipProps) {
  return (
    <span className="info-tip">
      <button type="button" className="info-tip-trigger" aria-label={label ?? `Explain: ${text}`}>
        <Info size={13} aria-hidden />
      </button>
      <span className="info-tip-pop" role="tooltip">
        {text}
      </span>
    </span>
  );
}
