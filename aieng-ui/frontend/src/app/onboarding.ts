// Onboarding + empty-state guidance (#395).
//
// The workbench is a Read + Handoff surface: the user drives actions through a
// connected agent (Claude Code, Codex, …) via `/commands`, and the UI's job is to
// make the very first action obvious. This module is the *pure* decision + copy
// layer so it can be unit-tested without rendering; OnboardingGuide.tsx renders it.

export interface OnboardingInputs {
  /** Are there any projects at all? */
  hasProjects: boolean;
  /** Does the selected project have a geometry/preview asset loaded? */
  hasViewerAsset: boolean;
  /** Name of the selected project, if one is selected. */
  selectedProjectName?: string | null;
  /** Has the user dismissed the first-run welcome (persisted)? */
  welcomeDismissed: boolean;
}

export type OnboardingGuide =
  | { kind: "welcome" }
  | { kind: "empty-project"; projectName: string }
  | { kind: "none" };

/**
 * Decide which guidance (if any) to show. Geometry present → never show; no
 * projects → first-run welcome (until dismissed); a selected project without
 * geometry → actionable empty-project guidance (always, it's transient + useful).
 */
export function resolveOnboarding(inputs: OnboardingInputs): OnboardingGuide {
  if (inputs.hasViewerAsset) return { kind: "none" };
  const name = inputs.selectedProjectName?.trim();
  if (inputs.hasProjects && name) {
    return { kind: "empty-project", projectName: name };
  }
  if (!inputs.welcomeDismissed) return { kind: "welcome" };
  return { kind: "none" };
}

export interface StarterStep {
  /** Short imperative label. */
  title: string;
  /** One line on what happens / how. */
  detail: string;
  /** A copy-able starter command for the user's agent, when applicable. */
  command?: string;
}

/** The three-step loop shown on first run — written from the user's side. */
export const STARTER_STEPS: readonly StarterStep[] = [
  {
    title: "Create a part",
    detail: "Ask your agent to model it — or upload a STEP file.",
    command: "/build a CNC aluminium bracket, 80×60×8 mm, with 4 × M6 bolt holes",
  },
  {
    title: "Set up & simulate",
    detail: "Describe the load case; the agent meshes it and runs CalculiX.",
    command: "/simulate fix the base, 500 N down on the wall, material aluminium 6061",
  },
  {
    title: "Read verified results",
    detail: "Inspect stress and displacement — each result shows its credibility tier, so there is no false confidence.",
  },
] as const;

/** Command that gets an empty project unblocked. */
export const EMPTY_PROJECT_COMMAND =
  "/build a CNC aluminium bracket, 80×60×8 mm, with 4 × M6 bolt holes";

export const ONBOARDING_COPY = {
  welcomeTitle: "Welcome to the AIENG workbench",
  welcomeLede:
    "Design and simulate mechanical parts with your AI agent — CAD → CAE → verified results.",
  agentHint: "These run through your connected agent (Claude Code, Codex, …).",
  emptyTitlePrefix: "has no geometry yet",
  emptyLede: "Create geometry to get started — ask your agent, or upload a STEP / .aieng file:",
  dismiss: "Got it",
} as const;
