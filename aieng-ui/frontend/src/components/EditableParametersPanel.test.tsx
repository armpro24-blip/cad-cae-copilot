/**
 * @vitest-environment happy-dom
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { EditableParametersPanel } from "./EditableParametersPanel";
import type { EditableParameter } from "../types";

afterEach(cleanup);

function param(overrides: Partial<EditableParameter> = {}): EditableParameter {
  return {
    feature_id: "f1",
    feature_name: "base_plate",
    feature_type: "named_part",
    scope: "local",
    parameter_name: "wall_thickness",
    cad_parameter_name: "WALL_THICKNESS",
    current_value: 3,
    min_value: 1,
    max_value: 10,
    ...overrides,
  };
}

describe("EditableParametersPanel inline edit (#223)", () => {
  it("renders nothing without parameters", () => {
    const { container } = render(<EditableParametersPanel parameters={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("Set drafts a full /modify command with the entered value", () => {
    const onUseInChat = vi.fn();
    render(<EditableParametersPanel parameters={[param()]} onUseInChat={onUseInChat} />);
    const input = screen.getByLabelText("New value for wall_thickness") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Set" }));
    expect(onUseInChat).toHaveBeenCalledWith("/modify set wall thickness to 5");
  });

  it("shows an honest, non-blocking warning for an out-of-range value", () => {
    const onUseInChat = vi.fn();
    render(<EditableParametersPanel parameters={[param({ min_value: 1, max_value: 10 })]} onUseInChat={onUseInChat} />);
    const input = screen.getByLabelText("New value for wall_thickness") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "99" } });
    expect(screen.getByText(/above the allowed maximum 10/)).toBeTruthy();
    // non-blocking: the draft still fires (backend routes out-of-range to confirmation)
    fireEvent.click(screen.getByRole("button", { name: "Set" }));
    expect(onUseInChat).toHaveBeenCalledWith("/modify set wall thickness to 99");
  });

  it("flags a global-scope parameter as rippling", () => {
    render(<EditableParametersPanel parameters={[param({ scope: "global" })]} onUseInChat={vi.fn()} />);
    expect(screen.getByText(/ripples across parts/)).toBeTruthy();
  });

  it("Preview invokes onPreview with the parameter and entered value", () => {
    const onPreview = vi.fn();
    render(<EditableParametersPanel parameters={[param()]} onUseInChat={vi.fn()} onPreview={onPreview} />);
    const input = screen.getByLabelText("New value for wall_thickness") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /Preview/i }));
    expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({ parameter_name: "wall_thickness" }), 7);
  });
});
