import { ModelViewer } from "./ModelViewer";
import { OnboardingGuide } from "./OnboardingGuide";
import { WorkflowStepper } from "./WorkflowStepper";
import { ResultsHero } from "./ResultsHero";
import { FieldPicker } from "./FieldPicker";
import { LoadCasePicker } from "./LoadCasePicker";
import { FieldLegend } from "./FieldLegend";
import { resultFieldLabel } from "./viewer/resultFields";
import type { BrepGraphSnapshot, CadGenerationProgress, PickedFace } from "../appTypes";
import type { CaeSetupOverlayResponse, FieldOverlayConfig, ProjectRecord, SolverFieldDescriptor } from "../types";
import type { ResultsHeroView } from "../app/resultsHero";

type LoadCaseOption = {
  id: string;
  name?: string | null;
  type?: string | null;
};

type ViewerPaneProps = {
  runtimeReady: boolean;
  runtimeProvider: string;
  selectedProject: ProjectRecord | null;
  effectiveViewerFormat: string | null;
  activeFieldDescriptor: SolverFieldDescriptor | null;
  selectedCaeField: string;
  onSelectCaeField(name: string): void;
  selectedLoadCaseId: string | null;
  loadCases: LoadCaseOption[];
  onSelectLoadCase(id: string): void;
  caeSetupOverlay?: CaeSetupOverlayResponse | null;
  caeResultsAvailable: boolean;
  caeSetupComplete: boolean;
  resultsHero: ResultsHeroView | null;
  effectiveViewerUrl?: string | null;
  pickedFaces: PickedFace[];
  onAddPickedFace(face: PickedFace): void;
  onClearPickedFaces(): void;
  onCopyPointer(text: string): void;
  cadGenerationProgress: CadGenerationProgress | null;
  highlightedFaceIds: Set<string>;
  brepSnapshot: BrepGraphSnapshot | null;
  onClearHighlightedFaces(): void;
  fieldOverlayConfig?: FieldOverlayConfig | null;
  onFieldOverlayConfigChange?(config: FieldOverlayConfig | null): void;
  hasProjects: boolean;
  welcomeDismissed: boolean;
  onDismissWelcome(): void;
};

export function ViewerPane({
  runtimeReady,
  runtimeProvider,
  selectedProject,
  effectiveViewerFormat,
  activeFieldDescriptor,
  selectedCaeField,
  onSelectCaeField,
  selectedLoadCaseId,
  loadCases,
  onSelectLoadCase,
  caeSetupOverlay,
  caeResultsAvailable,
  caeSetupComplete,
  resultsHero,
  effectiveViewerUrl,
  pickedFaces,
  onAddPickedFace,
  onClearPickedFaces,
  onCopyPointer,
  cadGenerationProgress,
  highlightedFaceIds,
  brepSnapshot,
  onClearHighlightedFaces,
  fieldOverlayConfig,
  onFieldOverlayConfigChange,
  hasProjects,
  welcomeDismissed,
  onDismissWelcome,
}: ViewerPaneProps) {
  return (
    <section className="viewer-pane">
      <div className="viewer-header">
        <div className="viewer-heading">
          <h1>Workbench</h1>
          <div className="viewer-header-status" aria-label="Model status">
            <span>{selectedProject?.name ?? "No project"}</span>
            {runtimeReady ? (
              <span className="status-ready">{runtimeProvider} ready</span>
            ) : (
              <span>Waiting for model</span>
            )}
          </div>
        </div>
        <WorkflowStepper
          hasProject={Boolean(selectedProject)}
          hasGeometry={Boolean(effectiveViewerUrl)}
          hasCaeSetup={caeSetupComplete}
          hasResults={caeResultsAvailable}
        />
        <ResultsHero hero={resultsHero} />
      </div>

      <div className="viewer-stage-shell">
        <div className="viewer-stage-head">
          <div>
            <strong>Model Preview</strong>
            <span>{effectiveViewerFormat ? `${effectiveViewerFormat.toUpperCase()} preview` : "Import a model to preview it here"}</span>
          </div>
          <div className="viewer-stage-badge">
            {activeFieldDescriptor ? `${resultFieldLabel(activeFieldDescriptor.field_name)} field` : effectiveViewerUrl ? "Ready" : "Waiting"}
          </div>
        </div>
        <div className="viewer-canvas-shell" style={{ position: "relative" }}>
          {caeResultsAvailable ? (
            <FieldPicker value={selectedCaeField} onChange={onSelectCaeField} />
          ) : null}
          {caeResultsAvailable && loadCases.length > 0 ? (
            <LoadCasePicker
              value={selectedLoadCaseId}
              loadCases={loadCases}
              onChange={onSelectLoadCase}
            />
          ) : null}
          <FieldLegend
            descriptor={activeFieldDescriptor}
            config={fieldOverlayConfig}
            onChange={onFieldOverlayConfigChange}
          />
          <ModelViewer
            assetUrl={effectiveViewerUrl}
            assetFormat={effectiveViewerFormat}
            fieldDescriptor={activeFieldDescriptor}
            fieldOverlayConfig={fieldOverlayConfig}
            caeSetupOverlay={caeSetupOverlay}
            projectId={selectedProject?.id ?? null}
            pickedFaces={pickedFaces}
            onAddPickedFace={onAddPickedFace}
            onClearPickedFaces={onClearPickedFaces}
            onCopyPointer={onCopyPointer}
            cadGenerationProgress={cadGenerationProgress}
            highlightedFaceIds={highlightedFaceIds}
            brepSnapshot={brepSnapshot}
            onClearHighlightedFaces={onClearHighlightedFaces}
          />
          <OnboardingGuide
            hasProjects={hasProjects}
            hasViewerAsset={Boolean(effectiveViewerUrl)}
            selectedProjectName={selectedProject?.name ?? null}
            welcomeDismissed={welcomeDismissed}
            onDismissWelcome={onDismissWelcome}
          />
        </div>
      </div>

    </section>
  );
}
