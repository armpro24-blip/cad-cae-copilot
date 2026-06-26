import { useEffect, useState } from "react";
import { Settings, Database, Puzzle, List, FileText, Terminal } from "lucide-react";

import { api } from "../api";
import { NoticeCenter } from "../components/common";
import { PointerProvider } from "../components/PointerText";
import { PendingApprovals } from "../components/PendingApprovals";
import { ProjectTimelinePanel } from "../components/ProjectTimelinePanel";
import { SessionsSidebar } from "../components/SessionsSidebar";
import { ViewerPane } from "../components/ViewerPane";
import { CommandReference } from "../components/CommandReference";
import { MaterialLibraryPanel } from "../components/MaterialLibraryPanel";
import { StandardPartsPanel } from "../components/StandardPartsPanel";
import { BOMPanel } from "../components/BOMPanel";
import { OptimizationPanel } from "../components/OptimizationPanel";
import { EditDiffPanel } from "../components/EditDiffPanel";
import { SizingSweepPanel } from "../components/SizingSweepPanel";
import { MeshConvergencePanel } from "../components/MeshConvergencePanel";
import { GlobalSettingsDrawer } from "../components/settings/GlobalSettingsDrawer";
import { RuntimeSettingsDrawer } from "../components/settings/RuntimeSettingsDrawer";
import { isEmbedMode } from "./embed";
import { useBrowserStorageState } from "./useBrowserStorageState";
import type { useWorkbenchApp } from "./useWorkbenchApp";

type LibraryTab = "materials" | "standards" | "bom";

type AppChromeProps = {
  app: ReturnType<typeof useWorkbenchApp>;
};

export function AppChrome({ app }: AppChromeProps) {
  const embed = isEmbedMode();
  const [libraryTab, setLibraryTab] = useState<LibraryTab | null>(null);
  const [commandRefOpen, setCommandRefOpen] = useState(false);
  const [welcomeDismissed, setWelcomeDismissed] = useBrowserStorageState<boolean>(
    "aieng.onboarding.welcomeDismissed",
    false,
    { storage: "local" },
  );

  // In embed mode (VS Code webview iframe), relay the picked faces to the host
  // shell so its "Copy modify" handoff can target the selected @face: pointers.
  useEffect(() => {
    if (!embed || typeof window === "undefined" || window.parent === window) return;
    window.parent.postMessage(
      { kind: "selectionChanged", pointers: app.pickedFaces.map((face) => face.pointer) },
      "*",
    );
  }, [embed, app.pickedFaces]);

  const mainClass = embed
    ? "app-main embed-main"
    : [
        "app-main",
        app.sidebarCollapsed ? "sidebar-collapsed" : "",
        libraryTab ? "library-open" : "",
      ]
        .filter(Boolean)
        .join(" ");
  const reportUrl = app.selectedId ? api.projectReportUrl(app.selectedId) : null;

  // Read+Handoff: a drafted /command becomes a copy-able chip in the notice
  // (the GUI never executes — the user runs it with their connected agent).
  const draftNotice = (title: string) => (draft: string) =>
    app.setNotice({ tone: "info", title, detail: "Copy and run this with your agent.", command: draft });

  function openEngineeringReport() {
    if (!reportUrl) {
      app.setNotice({
        tone: "info",
        title: "Select a project first",
        detail: "Choose a project before opening the engineering report.",
      });
      return;
    }
    window.open(reportUrl, "_blank", "noopener,noreferrer");
  }

  return (
    <PointerProvider value={app.pointerContextValue}>
      <NoticeCenter
        notice={app.notice ?? app.runtimeNotice}
        onDismiss={() => {
          if (app.notice) {
            app.setNotice(null);
          } else {
            app.setRuntimeNotice(null);
          }
        }}
      />
      <div className={embed ? "app-shell embed-mode" : "app-shell"}>
        {!embed && (
          <header className="app-topbar">
            <div className="app-topbar-brand">
              <span className="app-logo">AIENG</span>
              <span className="app-topbar-divider" />
              <span className="app-topbar-project">{app.selectedProject?.name || "Workbench"}</span>
              <span className="app-topbar-mcp">Live CAD/CAE workbench + MCP server</span>
            </div>
            <div className="app-topbar-actions">
              <button
                type="button"
                className={libraryTab === "materials" ? "app-topbar-btn active" : "app-topbar-btn"}
                onClick={() => setLibraryTab((t) => (t === "materials" ? null : "materials"))}
                title="Materials"
              >
                <Database className="h-4 w-4" />
                <span className="app-topbar-btn-label">Materials</span>
              </button>
              <button
                type="button"
                className={libraryTab === "standards" ? "app-topbar-btn active" : "app-topbar-btn"}
                onClick={() => setLibraryTab((t) => (t === "standards" ? null : "standards"))}
                title="Standard Parts"
              >
                <Puzzle className="h-4 w-4" />
                <span className="app-topbar-btn-label">Parts</span>
              </button>
              <button
                type="button"
                className={libraryTab === "bom" ? "app-topbar-btn active" : "app-topbar-btn"}
                onClick={() => setLibraryTab((t) => (t === "bom" ? null : "bom"))}
                title="Bill of materials"
              >
                <List className="h-4 w-4" />
                <span className="app-topbar-btn-label">BOM</span>
              </button>
              <button
                type="button"
                className="app-topbar-btn"
                onClick={openEngineeringReport}
                disabled={!reportUrl}
                title="Open engineering report"
              >
                <FileText className="h-4 w-4" />
                <span className="app-topbar-btn-label">Report</span>
              </button>
              <button
                type="button"
                className={commandRefOpen ? "app-topbar-btn active" : "app-topbar-btn"}
                onClick={() => setCommandRefOpen((v) => !v)}
                title="Commands — what to type to your agent"
              >
                <Terminal className="h-4 w-4" />
                <span className="app-topbar-btn-label">Commands</span>
              </button>
              <button
                type="button"
                className="app-topbar-btn"
                onClick={() => app.setSettingsOpen(true)}
                title="Settings"
              >
                <Settings className="h-4 w-4" />
                <span className="app-topbar-btn-label">Settings</span>
              </button>
            </div>
          </header>
        )}

        <div className={mainClass}>
          {!embed && (
            <SessionsSidebar
              collapsed={app.sidebarCollapsed}
              onCollapsedChange={app.setSidebarCollapsed}
              projectName={app.projectName}
              onProjectNameChange={app.setProjectName}
              busy={app.busy}
              selectedFile={app.selectedFile}
              onSelectedFileChange={app.setSelectedFile}
              selectedId={app.selectedId}
              selectedProject={app.selectedProject}
              projects={app.projects}
              stages={app.stages}
              runBusyTask={app.runBusyTask}
              refreshProjects={app.refreshProjects}
              setNotice={app.setNotice}
              runWorkbenchImportFlow={app.runWorkbenchImportFlow}
            />
          )}

          <ViewerPane
            runtimeReady={app.runtimeReady}
            runtimeProvider={app.runtimeProvider}
            selectedProject={app.selectedProject}
            effectiveViewerFormat={app.effectiveViewerFormat}
            activeFieldDescriptor={app.activeFieldDescriptor}
            selectedCaeField={app.selectedCaeField}
            onSelectCaeField={app.setSelectedCaeField}
            selectedLoadCaseId={app.selectedLoadCaseId}
            loadCases={app.loadCases}
            onSelectLoadCase={app.setSelectedLoadCaseId}
            caeSetupOverlay={app.caeSetupOverlay}
            caeResultsAvailable={app.caeResultsAvailable}
            caeSetupComplete={app.caeSetupComplete}
            resultsHero={app.resultsHero}
            effectiveViewerUrl={app.effectiveViewerUrl}
            fieldOverlayConfig={app.fieldOverlayConfig}
            onFieldOverlayConfigChange={app.setFieldOverlayConfig}
            pickedFaces={app.pickedFaces}
            onAddPickedFace={app.addPickedFace}
            onClearPickedFaces={app.clearPickedFaces}
            onCopyPointer={app.copyPointerText}
            cadGenerationProgress={app.cadGenerationProgress}
            highlightedFaceIds={app.highlightedFaceIds}
            brepSnapshot={app.brepSnapshot}
            onClearHighlightedFaces={app.clearHighlightedFaces}
            hasProjects={app.projects.length > 0}
            welcomeDismissed={welcomeDismissed}
            onDismissWelcome={() => setWelcomeDismissed(true)}
          />

          <PendingApprovals
            approvals={app.pendingApprovals.filter(
              (item) => !item.projectId || item.projectId === app.selectedId,
            )}
            onResolve={app.resolveApproval}
          />

          <ProjectTimelinePanel
            timeline={app.projectTimeline}
            onRestoreSnapshot={app.restoreCadSnapshot}
            onApproveRun={app.approveTimelineRun}
            onRejectRun={app.rejectTimelineRun}
            onCopyNextAction={app.copyPointerText}
          />

          {!embed && <CommandReference open={commandRefOpen} onClose={() => setCommandRefOpen(false)} />}

          <EditDiffPanel editDiff={app.editDiff} />

          <OptimizationPanel
            study={app.optimizationStudy}
            surrogate={app.surrogateProposals}
            convergence={app.optimizationConvergence}
            onRunCandidates={app.runDesignStudyCandidates}
            running={app.busy}
            onUseInChat={draftNotice("Draft ready")}
          />

          <SizingSweepPanel
            report={app.sizingSweepReport}
            onUseInChat={draftNotice("Sizing sweep draft")}
          />

          <MeshConvergencePanel
            report={app.meshConvergenceReport}
            onUseInChat={draftNotice("Mesh convergence draft")}
          />

          {libraryTab && !embed && (
            <aside className="library-pane" aria-label="Library panel">
              <div className="library-pane-tabs">
                <button
                  type="button"
                  className={libraryTab === "materials" ? "library-pane-tab active" : "library-pane-tab"}
                  onClick={() => setLibraryTab("materials")}
                >
                  <Database className="h-4 w-4" />
                  <span>Materials</span>
                </button>
                <button
                  type="button"
                  className={libraryTab === "standards" ? "library-pane-tab active" : "library-pane-tab"}
                  onClick={() => setLibraryTab("standards")}
                >
                  <Puzzle className="h-4 w-4" />
                  <span>Standard Parts</span>
                </button>
                <button
                  type="button"
                  className={libraryTab === "bom" ? "library-pane-tab active" : "library-pane-tab"}
                  onClick={() => setLibraryTab("bom")}
                >
                  <List className="h-4 w-4" />
                  <span>BOM</span>
                </button>
              </div>

              {libraryTab === "materials" && (
                <MaterialLibraryPanel
                  projectId={app.selectedId}
                  onApplyAssignment={draftNotice("Material assignment drafted")}
                  onNotice={(title, detail) => app.setNotice({ tone: "info", title, detail })}
                />
              )}
              {libraryTab === "standards" && (
                <StandardPartsPanel
                  projectId={app.selectedId}
                  onNotice={(title, detail) => app.setNotice({ tone: "info", title, detail })}
                />
              )}
              {libraryTab === "bom" && (
                <BOMPanel
                  projectId={app.selectedId}
                  onNotice={(title, detail) => app.setNotice({ tone: "info", title, detail })}
                />
              )}
            </aside>
          )}
        </div>
      </div>

      <RuntimeSettingsDrawer
        open={app.settingsOpen}
        runtime={app.runtime}
        runtimeDraft={app.runtimeDraft}
        runtimeBusy={app.runtimeBusy}
        runtimeNotice={null}
        runtimeProvider={app.runtimeProvider}
        runtimeReady={app.runtimeReady}
        llmConfig={app.llmConfig}
        llmReady={app.llmReady}
        apiKey={app.apiKey}
        onApiKeyChange={app.updateApiKey}
        onClose={() => app.setSettingsOpen(false)}
        onDraftChange={app.updateRuntimeDraft}
        onLlmChange={app.updateLlmConfig}
        onLlmPreset={app.applyLlmProviderPreset}
        onLlmRestore={app.restoreDefaultLlmConfig}
        onLlmTestResult={app.handleLlmTestResult}
        onTest={() => void app.runRuntimeTask("test", () => api.testRuntimeConfig(app.runtimeDraft!))}
        onSave={() => void app.runRuntimeTask("save", () => api.updateRuntimeConfig(app.runtimeDraft!))}
        onRestore={app.restoreRuntimeDefaults}
        localAgentConfig={app.localAgentConfig}
        localAdapters={[]}
        onLocalAgentChange={(key, value) => app.setLocalAgentConfig((prev) => ({ ...prev, [key]: value }))}
        onProbeLocalAgents={() => undefined}
      />
      <GlobalSettingsDrawer open={app.globalSettingsOpen} onClose={() => app.setGlobalSettingsOpen(false)} />
    </PointerProvider>
  );
}
