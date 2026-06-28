/**
 * @vitest-environment happy-dom
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ParametricEditProposalPanel } from "./ParametricEditProposalPanel";
import type { EditableParameter, ParametricEditProposal } from "../types";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    createParametricEditProposal: vi.fn(),
    applyParametricEditProposal: vi.fn(),
    getParametricEditProposal: vi.fn(),
  },
}));

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
});

const param = (overrides: Partial<EditableParameter> = {}): EditableParameter => ({
  feature_id: "feat_001",
  feature_name: "base_plate",
  feature_type: "named_part",
  scope: "local",
  parameter_name: "length_mm",
  cad_parameter_name: "BODY_LENGTH",
  current_value: 120,
  min_value: 10,
  max_value: 500,
  ...overrides,
});

const makeProposal = (overrides: Partial<ParametricEditProposal> = {}): ParametricEditProposal => ({
  status: "ok",
  proposal_id: "pep_abc123",
  project_id: "proj_001",
  approval_required: true,
  target: {
    feature_id: "feat_001",
    parameter_name: "length_mm",
    cad_parameter_name: "BODY_LENGTH",
    feature_name: "base_plate",
    feature_type: "named_part",
    pointer: "@feature:feat_001",
  },
  change: { old_value: 120, new_value: 200, unit: "mm", reason: "Make it longer" },
  scope: "local",
  scope_risk: null,
  risks: {
    protected_features: [],
    design_target_impacts: [],
  },
  expected_impact: {
    mass: { status: "unknown", note: "requires recompute after edit" },
    stress: { status: "unknown", note: "requires new static solver run after edit" },
    design_targets: { affected_count: 0, note: "No known design-target overlap." },
    summary: "Geometry will change; downstream CAE evidence must be revalidated.",
  },
  preview: { status: "ok", regression_diff: { verdict: "clean" } },
  ...overrides,
});

describe("ParametricEditProposalPanel", () => {
  it("renders the initial diff and builds a proposal on preview", async () => {
    const proposal = makeProposal();
    vi.mocked(api.createParametricEditProposal).mockResolvedValueOnce(proposal);

    render(<ParametricEditProposalPanel projectId="proj_001" param={param()} value={200} />);

    expect(screen.getByText(/120 → 200/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Preview change/i }));

    await waitFor(() => {
      expect(document.body.textContent).toContain("Approve and apply");
    });
    expect(document.body.textContent).toMatch(/BODY_LENGTH/);
    expect(document.body.textContent).toMatch(/Make it longer/i);
    expect(api.createParametricEditProposal).toHaveBeenCalledWith("proj_001", {
      featureId: "feat_001",
      parameterName: "length_mm",
      newValue: 200,
      reason: "",
    });
  });

  it("displays protected-feature risks and design-target impacts", async () => {
    const proposal = makeProposal({
      risks: {
        protected_features: [
          { kind: "protected_geometry_signal", matched_tokens: ["hole"], message: "Editing a hole parameter may break fits." },
        ],
        design_target_impacts: [
          { target_id: "t1", label: "Max mass", metric: "mass", reason: "Geometry change affects mass." },
        ],
      },
    });
    vi.mocked(api.createParametricEditProposal).mockResolvedValueOnce(proposal);

    render(<ParametricEditProposalPanel projectId="proj_001" param={param()} value={200} />);
    fireEvent.click(screen.getByRole("button", { name: /Preview change/i }));

    await waitFor(() => {
      expect(document.body.textContent).toContain("Editing a hole parameter may break fits");
    });
    expect(document.body.textContent).toMatch(/Max mass/);
    expect(document.body.textContent).toMatch(/Geometry change affects mass/);
  });

  it("calls onApplied after the proposal is approved and applied", async () => {
    const proposal = makeProposal();
    vi.mocked(api.createParametricEditProposal).mockResolvedValueOnce(proposal);
    vi.mocked(api.applyParametricEditProposal).mockResolvedValueOnce({ status: "ok" } as unknown as ParametricEditProposal);
    const onApplied = vi.fn();

    render(<ParametricEditProposalPanel projectId="proj_001" param={param()} value={200} onApplied={onApplied} />);
    fireEvent.click(screen.getByRole("button", { name: /Preview change/i }));
    await waitFor(() => {
      expect(document.body.textContent).toContain("Approve and apply");
    });
    fireEvent.click(screen.getByText(/Approve and apply/i));

    await waitFor(() => {
      expect(onApplied).toHaveBeenCalled();
    });
    expect(api.applyParametricEditProposal).toHaveBeenCalledWith("proj_001", "pep_abc123", false);
  });

  it("does not call onApplied when the apply endpoint returns an error status", async () => {
    const proposal = makeProposal();
    vi.mocked(api.createParametricEditProposal).mockResolvedValueOnce(proposal);
    vi.mocked(api.applyParametricEditProposal).mockResolvedValueOnce({
      status: "error",
      message: "Scope risk confirmation required",
    } as unknown as ParametricEditProposal);
    const onApplied = vi.fn();

    render(<ParametricEditProposalPanel projectId="proj_001" param={param()} value={200} onApplied={onApplied} />);
    fireEvent.click(screen.getByRole("button", { name: /Preview change/i }));
    await waitFor(() => {
      expect(document.body.textContent).toContain("Approve and apply");
    });
    fireEvent.click(screen.getByText(/Approve and apply/i));

    await waitFor(() => {
      expect(document.body.textContent).toContain("Scope risk confirmation required");
    });
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("calls onCancelled when the user rejects the proposal after preview", async () => {
    const proposal = makeProposal();
    vi.mocked(api.createParametricEditProposal).mockResolvedValueOnce(proposal);
    const onCancelled = vi.fn();

    render(<ParametricEditProposalPanel projectId="proj_001" param={param()} value={200} onCancelled={onCancelled} />);
    fireEvent.click(screen.getByRole("button", { name: /Preview change/i }));
    await waitFor(() => {
      expect(document.body.textContent).toContain("Approve and apply");
    });
    fireEvent.click(screen.getByRole("button", { name: /Reject/i }));
    expect(onCancelled).toHaveBeenCalled();
  });

  it("passes confirmScopeRisk=true for global parameters", async () => {
    const proposal = makeProposal({
      scope: "global",
      scope_risk: { scope: "global", reason: "shared", confirmation_field: "confirmScopeRisk" },
    });
    vi.mocked(api.createParametricEditProposal).mockResolvedValueOnce(proposal);
    vi.mocked(api.applyParametricEditProposal).mockResolvedValueOnce({ status: "ok" } as unknown as ParametricEditProposal);

    render(<ParametricEditProposalPanel projectId="proj_001" param={param({ scope: "global" })} value={200} />);
    fireEvent.click(screen.getByRole("button", { name: /Preview change/i }));
    await waitFor(() => {
      expect(document.body.textContent).toContain("Approve and apply");
    });
    fireEvent.click(screen.getByText(/Approve and apply/i));

    await waitFor(() => {
      expect(api.applyParametricEditProposal).toHaveBeenCalledWith("proj_001", "pep_abc123", true);
    });
  });
});
