import { Check } from "lucide-react";
import { resolveWorkflowStages, type WorkflowSignals } from "../app/workflowStage";

type WorkflowStepperProps = WorkflowSignals;

/**
 * Compact "Model → Setup → Solve → Results" progress strip — the single
 * "where am I / what's next" signal. Renders nothing until a project is selected
 * (the onboarding guide covers the empty case).
 */
export function WorkflowStepper(signals: WorkflowStepperProps) {
  const stages = resolveWorkflowStages(signals);
  if (stages.length === 0) return null;
  const active = stages.find((s) => s.status === "active");

  return (
    <div className="workflow-stepper" aria-label="Project workflow">
      <ol className="workflow-stepper-track">
        {stages.map((stage, i) => (
          <li key={stage.key} className={`workflow-step is-${stage.status}`}>
            <span className="workflow-step-marker" aria-hidden>
              {stage.status === "done" ? <Check size={12} /> : i + 1}
            </span>
            <span className="workflow-step-label">{stage.label}</span>
          </li>
        ))}
      </ol>
      {active ? <p className="workflow-step-hint">{active.hint}</p> : null}
    </div>
  );
}
