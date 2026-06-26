// Workflow stepper logic (#397): turn the implicit CAD→CAE flow into an explicit
// "Model → Setup → Solve → Results" progress signal. Pure + unit-tested; the
// WorkflowStepper component just renders the result.

export type WorkflowStageKey = "model" | "setup" | "solve" | "results";
export type WorkflowStageStatus = "done" | "active" | "todo";

export interface WorkflowStageView {
  key: WorkflowStageKey;
  label: string;
  /** One-line hint shown for the active stage — what to do next. */
  hint: string;
  status: WorkflowStageStatus;
}

export interface WorkflowSignals {
  /** A project is selected. */
  hasProject: boolean;
  /** The selected project has a geometry/preview asset. */
  hasGeometry: boolean;
  /** Material + load + constraint are defined (physics setup complete). */
  hasCaeSetup: boolean;
  /** Solver result artifacts exist. */
  hasResults: boolean;
}

const STAGES: ReadonlyArray<{ key: WorkflowStageKey; label: string; hint: string }> = [
  { key: "model", label: "Model", hint: "Build geometry — ask your agent or upload a STEP." },
  { key: "setup", label: "Setup", hint: "Define material, loads and constraints." },
  { key: "solve", label: "Solve", hint: "Run the solver once setup is complete." },
  { key: "results", label: "Results", hint: "Inspect stress / displacement and credibility." },
];

/**
 * Resolve the four workflow stages with per-stage status. Monotonic: results
 * imply the earlier stages happened, so a finished project reads cleanly even if
 * an intermediate signal is missing. Returns an empty list when no project is
 * selected (the onboarding guide covers that case instead).
 */
export function resolveWorkflowStages(signals: WorkflowSignals): WorkflowStageView[] {
  if (!signals.hasProject) return [];

  const geometry = signals.hasGeometry || signals.hasResults;
  const setup = signals.hasCaeSetup || signals.hasResults;
  const solved = signals.hasResults;
  const done = [geometry, setup, solved, solved];

  let active = done.findIndex((d) => !d);
  if (active === -1) active = STAGES.length - 1; // all done → sitting on Results

  return STAGES.map((stage, i) => ({
    ...stage,
    status: i < active ? "done" : i === active ? "active" : "todo",
  }));
}

/** Derive the "physics setup complete" signal from the CAE preprocessing status. */
export function isCaeSetupComplete(
  status:
    | {
        has_materials?: boolean;
        has_loads?: boolean;
        has_boundary_conditions?: boolean;
        has_constraints?: boolean;
        ready_for_solver?: boolean;
      }
    | null
    | undefined,
): boolean {
  if (!status) return false;
  if (status.ready_for_solver) return true;
  return Boolean(
    status.has_materials &&
      status.has_loads &&
      (status.has_boundary_conditions || status.has_constraints),
  );
}
