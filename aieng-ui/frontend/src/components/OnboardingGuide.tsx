import { Sparkles, Upload } from "lucide-react";
import { CommandChip } from "./CommandChip";
import {
  EMPTY_PROJECT_COMMAND,
  ONBOARDING_COPY,
  resolveOnboarding,
  STARTER_STEPS,
  type OnboardingInputs,
} from "../app/onboarding";

type OnboardingGuideProps = OnboardingInputs & {
  onDismissWelcome(): void;
};

/**
 * First-run welcome / empty-project guidance, rendered as a centered overlay in
 * the empty viewer. Read + Handoff: every action is a copy-able `/command` the
 * user runs with their connected agent. Renders nothing once geometry exists.
 */
export function OnboardingGuide({ onDismissWelcome, ...inputs }: OnboardingGuideProps) {
  const guide = resolveOnboarding(inputs);
  if (guide.kind === "none") return null;

  if (guide.kind === "empty-project") {
    return (
      <div className="onboarding-overlay" role="note">
        <div className="onboarding-card onboarding-card--compact">
          <div className="onboarding-head">
            <Sparkles size={18} aria-hidden className="onboarding-spark" />
            <h2>
              <span className="onboarding-project">{guide.projectName}</span> {ONBOARDING_COPY.emptyTitlePrefix}
            </h2>
          </div>
          <p className="onboarding-lede">{ONBOARDING_COPY.emptyLede}</p>
          <CommandChip command={EMPTY_PROJECT_COMMAND} className="onboarding-cmd" />
          <p className="onboarding-alt">
            <Upload size={13} aria-hidden /> or drop a STEP / .aieng file onto a project in the sidebar
          </p>
          <p className="onboarding-agent-hint">{ONBOARDING_COPY.agentHint}</p>
        </div>
      </div>
    );
  }

  // welcome
  return (
    <div className="onboarding-overlay" role="note">
      <div className="onboarding-card">
        <div className="onboarding-head">
          <Sparkles size={18} aria-hidden className="onboarding-spark" />
          <h2>{ONBOARDING_COPY.welcomeTitle}</h2>
        </div>
        <p className="onboarding-lede">{ONBOARDING_COPY.welcomeLede}</p>
        <ol className="onboarding-steps">
          {STARTER_STEPS.map((step, i) => (
            <li key={step.title}>
              <span className="onboarding-step-num">{i + 1}</span>
              <div className="onboarding-step-body">
                <strong>{step.title}</strong>
                <span>{step.detail}</span>
                {step.command ? <CommandChip command={step.command} className="onboarding-cmd" /> : null}
              </div>
            </li>
          ))}
        </ol>
        <div className="onboarding-foot">
          <span className="onboarding-agent-hint">{ONBOARDING_COPY.agentHint}</span>
          <button type="button" className="onboarding-dismiss" onClick={onDismissWelcome}>
            {ONBOARDING_COPY.dismiss}
          </button>
        </div>
      </div>
    </div>
  );
}
