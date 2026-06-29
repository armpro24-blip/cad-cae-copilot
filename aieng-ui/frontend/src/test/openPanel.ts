import { fireEvent, screen } from "@testing-library/react";

/**
 * Inspector panels render inside a collapsed-by-default PanelShell, so tests
 * that assert on body content must open the section first. The PanelShell header
 * is a button whose accessible name starts with the panel title.
 */
export function openPanel(title: RegExp): void {
  const head = screen.getByRole("button", { name: title });
  if (head.getAttribute("aria-expanded") === "false") {
    fireEvent.click(head);
  }
}
