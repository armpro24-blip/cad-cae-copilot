import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";

import type { ChatHistoryItem } from "../appTypes";

import { api } from "../api";
import {
  projectViewerUrl,
  resolveAssetFormat,
  withAssetVersion,
} from "../appUtils";
import type {
  FieldOverlayConfig,
  RuntimeConfigSnapshot,
  SolverFieldDescriptor,
  WorkflowStep,
} from "../types";
import { useAgentActivityStream } from "./useAgentActivityStream";
import { requestedProjectId } from "./embed";
import { buildFallbackSummary } from "./projectSummary";
import { isCaeSetupComplete } from "./workflowStage";
import { useEngineeringActions } from "./useEngineeringActions";
import { useGeometryPointers } from "./useGeometryPointers";
import { useRuntimeSettings } from "./useRuntimeSettings";
import { useProjectState } from "./useProjectState";
import { useSettingsUI } from "./useSettingsUI";
import { useApprovalState } from "./useApprovalState";
import { useWorkbenchStages } from "./useWorkbenchStages";
import { useOptimizationStudy } from "./useOptimizationStudy";
import { useEditDiff } from "./useEditDiff";
import { useOptimizationConvergence } from "./useOptimizationConvergence";
import { useSizingSweepReport } from "./useSizingSweepReport";
import { useMeshConvergenceReport } from "./useMeshConvergenceReport";
import { useCaeSetupOverlay } from "./useCaeSetupOverlay";
import { useProjectTimeline } from "./useProjectTimeline";

export function useWorkbenchApp() {
  const {
    projects,
    setProjects,
    selectedId,
    setSelectedId,
    summary,
    setSummary,
    projectName,
    setProjectName,
    busy,
    setBusy,
    selectedFile,
    setSelectedFile,
    notice,
    setNotice,
    selectedProject,
    refreshProjects,
  } = useProjectState();

  const {
    settingsOpen,
    setSettingsOpen,
    globalSettingsOpen,
    setGlobalSettingsOpen,
    sidebarCollapsed,
    setSidebarCollapsed,
  } = useSettingsUI();

  const {
    pendingApprovals,
    handleAgentEvent,
    resolveApproval,
  } = useApprovalState({ setNotice });

  const {
    stages,
    resetStages,
    patchStage,
  } = useWorkbenchStages();

  const runtimeSettings = useRuntimeSettings({ setSummary });
  const {
    runtime,
    runtimeDraft,
    runtimeNotice,
    runtimeBusy,
    llmConfig,
    llmReady,
    localAgentConfig,
    apiKey,
    apiKeyHydrated,
    runtimeReady,
    runtimeProvider,
    setRuntimeNotice,
    setLocalAgentConfig,
    applyRuntimeSnapshot,
    updateApiKey,
    updateRuntimeDraft,
    restoreRuntimeDefaults,
    runRuntimeTask,
    updateLlmConfig,
    handleLlmTestResult,
    applyLlmProviderPreset,
    restoreDefaultLlmConfig,
  } = runtimeSettings;
  const {
    cadPreviewUrl,
    cadPreviewFormat,
    cadGenerationProgress,
    setCadGenerationProgress,
    refreshViewerAsset,
    resetProjectDerivedState,
  } = useEngineeringActions();
  const {
    pickedFaces,
    highlightedFaceIds,
    brepSnapshot,
    addPickedFace,
    clearPickedFaces,
    clearHighlightedFaces,
    pointerContextValue,
  } = useGeometryPointers({
    selectedId,
    geometryVersion: projects.find((item) => item.id === selectedId)?.updated_at ?? null,
    setMessage: (() => undefined) as Dispatch<SetStateAction<string>>,
    setNotice,
    executePreprocessFromPrompt: async () => undefined,
  });

  const geometryVersion = projects.find((item) => item.id === selectedId)?.updated_at ?? null;
  const [timelineRefreshKey, setTimelineRefreshKey] = useState(0);

  const { optimizationStudy, surrogateProposals } = useOptimizationStudy({ selectedId, geometryVersion });
  const { editDiff } = useEditDiff({ selectedId, geometryVersion });
  const { optimizationConvergence } = useOptimizationConvergence({ selectedId, geometryVersion });
  const { sizingSweepReport } = useSizingSweepReport({ selectedId, geometryVersion });
  const { meshConvergenceReport } = useMeshConvergenceReport({ selectedId, geometryVersion });
  const { caeSetupOverlay } = useCaeSetupOverlay({ selectedId, geometryVersion });
  const { projectTimeline } = useProjectTimeline({ selectedId, geometryVersion, refreshKey: timelineRefreshKey });

  const fallbackViewerUrl = useMemo(() => projectViewerUrl(selectedProject), [selectedProject]);
  const rawViewerUrl = cadPreviewUrl ?? summary?.viewer_url ?? fallbackViewerUrl;
  const viewerVersion = summary?.project?.updated_at ?? selectedProject?.updated_at ?? null;
  const effectiveViewerUrl = useMemo(
    () => withAssetVersion(rawViewerUrl, viewerVersion),
    [rawViewerUrl, viewerVersion],
  );
  const summaryViewerFormat = typeof summary?.viewer?.asset_format === "string" ? summary.viewer.asset_format : null;
  const effectiveViewerFormat =
    (cadPreviewUrl ? cadPreviewFormat : null) ?? resolveAssetFormat(rawViewerUrl, summaryViewerFormat ?? selectedProject?.web_asset_format ?? null);

  const {
    liveSyncStatus,
    liveSyncDetail,
    liveSyncLastEventAt,
  } = useAgentActivityStream({
    selectedId,
    activeSessionId: null,
    activeRunId: null,
    agentBusy: false,
    cadGenerationProgress,
    refreshProjects,
    refreshViewerAsset,
    stopAutopilotPoll: () => undefined,
    onAutopilotRunUpdate: () => undefined,
    onChatMessage: () => undefined,
    onChatSessionChange: () => undefined,
    onChatSessionDelete: () => undefined,
    onAgentEvent: handleAgentEvent,
    setAgentBusy: (() => undefined) as Dispatch<SetStateAction<boolean>>,
    setNotice,
    setChatHistory: (() => undefined) as Dispatch<SetStateAction<ChatHistoryItem[]>>,
    setCadGenerationProgress,
    clearStreamingState: () => undefined,
  });

  useEffect(() => {
    if (!runtimeNotice) return;
    const timer = window.setTimeout(() => setRuntimeNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [runtimeNotice]);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const runtimeSnapshot = await api.runtime();
      if (cancelled) return;
      applyRuntimeSnapshot(runtimeSnapshot);
      const list = await api.listProjects();
      if (cancelled) return;
      setProjects(list);
      const requested = requestedProjectId();
      const candidate = (requested && list.some((item) => item.id === requested) ? requested : list[0]?.id) ?? null;
      setSelectedId(candidate);
      if (candidate) {
        try {
          const nextSummary = await api.getProject(candidate);
          if (!cancelled) setSummary(nextSummary);
        } catch {
          if (cancelled) return;
          const project = list.find((item) => item.id === candidate) ?? null;
          setSummary(project ? buildFallbackSummary(project, runtimeSnapshot) : null);
        }
      } else {
        setSummary(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    resetProjectDerivedState();
  }, [selectedId, resetProjectDerivedState]);

  async function runBusyTask(task: () => Promise<void>) {
    setBusy(true);
    try {
      await task();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setNotice({ tone: "error", title: "Operation failed", detail });
    } finally {
      setBusy(false);
    }
  }

  async function ensureProject() {
    if (selectedId) return selectedId;
    const baseName = selectedFile?.name.replace(/\.(step|stp|aieng)$/i, "") || projectName || "STEP workbench project";
    const created = await api.createProject(baseName);
    await refreshProjects(created.id, runtime);
    return created.id;
  }

  async function runWorkbenchImportFlow() {
    if (!selectedFile) {
      setNotice({ tone: "info", title: "Select a file first", detail: "Choose a .step, .stp, or .aieng package before importing." });
      return;
    }

    resetStages();
    setNotice(null);

    await runBusyTask(async () => {
      const projectId = await ensureProject();
      const isPackageUpload = /\.aieng$/i.test(selectedFile.name);

      patchStage("upload", "active", `Uploading ${selectedFile.name}`);
      await api.uploadFile(projectId, selectedFile);
      patchStage("upload", "done", `${selectedFile.name} uploaded`);

      if (isPackageUpload) {
        patchStage("import", "done", ".aieng package uploaded directly; skipped STEP -> package import.");
      } else {
        patchStage("import", "active", "Building .aieng package from STEP");
        await api.importAieng(projectId);
        patchStage("import", "done", "STEP package imported with topology, AAG, and feature graph artifacts.");
      }

      patchStage("preview", "active", "Generating web preview asset");
      const preview = await api.convert(projectId);
      const previewStatus =
        preview && typeof preview === "object" && "status" in preview && typeof preview.status === "string"
          ? preview.status
          : "ok";
      patchStage(
        "preview",
        previewStatus === "ok" ? "done" : "error",
        previewStatus === "ok"
          ? "Preview asset ready"
          : previewStatus === "unavailable"
            ? "Preview unavailable: no embedded GLB/STL preview was found, and STEP preview conversion is not available."
            : "Preview export failed; inspect the backend response for details.",
      );

      patchStage("semantic", "active", "Validating package semantics");
      await api.validate(projectId);
      await refreshProjects(projectId, runtime);
      patchStage("semantic", "done", "Semantic validation complete");

      setNotice({
        tone: previewStatus === "ok" ? "success" : "info",
        title: isPackageUpload ? ".aieng package ready" : "STEP import complete",
        detail: isPackageUpload
          ? "Package upload completed. If preview is unavailable, the package is still loaded and usable in the workbench."
          : previewStatus === "ok"
            ? "STEP imported, packaged, validated, and previewed successfully."
            : "STEP imported and validated, but preview export still needs an embedded GLB/STL asset or a STEP preview converter.",
      });
    });
  }

  async function runDesignStudyCandidates() {
    if (!selectedId) {
      setNotice({
        tone: "info",
        title: "Select a project first",
        detail: "Choose a project before running design-study candidates.",
      });
      return;
    }

    await runBusyTask(async () => {
      const result = await api.runDesignStudyCandidates(selectedId);
      await refreshProjects(selectedId, runtime);
      const executed = typeof result.executed === "number" ? result.executed : null;
      const succeeded = typeof result.succeeded === "number" ? result.succeeded : null;
      const failed = typeof result.failed === "number" ? result.failed : null;
      const baselineModified = result.baseline_modified === true ? "baseline modified" : "baseline unchanged";
      const detail = [
        executed != null ? `${executed} executed` : null,
        succeeded != null ? `${succeeded} succeeded` : null,
        failed != null ? `${failed} failed` : null,
        baselineModified,
      ].filter(Boolean).join("; ");
      setNotice({
        tone: failed && failed > 0 ? "info" : "success",
        title: "Design-study candidates ran",
        detail: detail || "Candidate patches were executed in derived workspaces; baseline remains unpromoted.",
      });
    });
  }

  async function restoreCadSnapshot(snapshotId: string) {
    if (!selectedId) {
      setNotice({
        tone: "info",
        title: "Select a project first",
        detail: "Choose a project before restoring a CAD snapshot.",
      });
      return;
    }
    const confirmed = window.confirm(
      `Restore CAD snapshot ${snapshotId}?\n\nThis starts an approval-gated restore. The current state is not auto-snapshotted before restore.`,
    );
    if (!confirmed) return;

    const steps: WorkflowStep[] = [{
      id: "cad.restore_snapshot",
      kind: "tool",
      tool_name: "cad.restore_snapshot",
      description: `Restore CAD snapshot ${snapshotId}`,
      input: { project_id: selectedId, snapshot_id: snapshotId },
      status: "pending",
      approval_required: true,
    }];
    try {
      const run = await api.startRun(
        `Restore CAD snapshot ${snapshotId}`,
        selectedId,
        null,
        { workflow_id: "cad_snapshot_restore", steps },
      );
      setTimelineRefreshKey((value) => value + 1);
      setNotice({
        tone: run.status === "awaiting_approval" ? "info" : run.status === "completed" ? "success" : "error",
        title: run.status === "awaiting_approval" ? "Restore approval required" : `Restore ${run.status}`,
        detail: run.status === "awaiting_approval"
          ? "Review the approval entry in the project timeline, then approve to restore."
          : run.summary || run.errors?.join("; ") || `Runtime run ${run.run_id}`,
      });
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Restore request failed",
        detail: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function approveTimelineRun(runId: string) {
    try {
      const run = await api.approveRun(runId);
      setTimelineRefreshKey((value) => value + 1);
      if (selectedId) await refreshProjects(selectedId, runtime);
      setNotice({
        tone: run.status === "completed" ? "success" : run.status === "failed" ? "error" : "info",
        title: `Runtime run ${run.status}`,
        detail: run.summary || run.errors?.join("; ") || runId,
      });
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Approval failed",
        detail: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function rejectTimelineRun(runId: string) {
    try {
      const run = await api.rejectRun(runId);
      setTimelineRefreshKey((value) => value + 1);
      setNotice({
        tone: "info",
        title: "Runtime approval denied",
        detail: run.summary || run.errors?.join("; ") || runId,
      });
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Reject failed",
        detail: error instanceof Error ? error.message : String(error),
      });
    }
  }

  // Selected post-processing result field (canonical names from resultFields catalog;
  // "" = no field overlay / plain geometry). Default to the most-used field.
  const [selectedCaeField, setSelectedCaeField] = useState("von_mises");
  const [fieldDescriptor, setFieldDescriptor] = useState<SolverFieldDescriptor | null>(null);
  const [fieldOverlayConfig, setFieldOverlayConfig] = useState<FieldOverlayConfig | null>(null);

  const caeSummary = summary?.cae ?? null;
  const hasCaeResultArtifacts = Boolean(
    caeSummary?.results_available ||
      (caeSummary?.result_evidence_count ?? 0) > 0 ||
      caeSummary?.solver_fields?.some((field) => field.available && field.format === "vertex_json") ||
      caeSummary?.artifact_detection?.has_results ||
      caeSummary?.artifact_detection?.has_fields ||
      caeSummary?.result_summary?.status.has_results ||
      caeSummary?.result_summary?.status.has_fields,
  );
  const caeSetupComplete = isCaeSetupComplete(caeSummary?.preprocessing_summary?.status);

  // Load-case / analysis-step selector state.  Defaults to the first load case
  // reported by the result summary (modal modes, multi-step static, etc.).
  const loadCases = useMemo(
    () => caeSummary?.result_summary?.load_cases ?? [],
    [caeSummary?.result_summary?.load_cases],
  );
  const [selectedLoadCaseId, setSelectedLoadCaseId] = useState<string | null>(null);

  useEffect(() => {
    if (loadCases.length === 0) {
      setSelectedLoadCaseId(null);
      return;
    }
    const valid = loadCases.some((lc) => lc.id === selectedLoadCaseId);
    if (!selectedLoadCaseId || !valid) {
      setSelectedLoadCaseId(loadCases[0].id);
    }
  }, [loadCases]);

  const activeFieldDescriptor = hasCaeResultArtifacts ? fieldDescriptor : null;

  // Fetch the descriptor for the picked field + load case. The picker offers the
  // full result-field catalog; the backend serves any of them from the FRD (or
  // falls back to synthetic).
  useEffect(() => {
    // Reset manual legend controls when the project, field, or load case changes
    // so the user starts from the solver-derived range/colormap for the new result.
    setFieldOverlayConfig(null);
  }, [selectedId, selectedCaeField, selectedLoadCaseId]);

  useEffect(() => {
    if (!selectedId || !hasCaeResultArtifacts || !selectedCaeField) {
      setFieldDescriptor(null);
      return;
    }
    let cancelled = false;
    void api.getFieldDescriptor(selectedId, selectedCaeField, selectedLoadCaseId)
      .then((desc) => {
        if (cancelled) return;
        setFieldDescriptor((current) => {
          if (
            current &&
            current.project_id === desc.project_id &&
            current.field_name === desc.field_name &&
            current.load_case_id === desc.load_case_id &&
            current.format === desc.format &&
            current.basis === desc.basis &&
            current.colormap === desc.colormap &&
            current.min_value === desc.min_value &&
            current.max_value === desc.max_value &&
            current.unit === desc.unit &&
            current.source === desc.source &&
            (current.values?.length ?? 0) === (desc.values?.length ?? 0)
          ) {
            return current;
          }
          return desc;
        });
      })
      .catch(() => { if (!cancelled) setFieldDescriptor(null); });
    return () => { cancelled = true; };
  }, [selectedId, selectedCaeField, selectedLoadCaseId, hasCaeResultArtifacts]);

  const copyPointerText = useCallback((text: string) => {
    if (!text.trim()) return;
    void navigator.clipboard?.writeText(text).catch(() => undefined);
    setNotice({ tone: "success", title: "Copied for MCP agent", detail: text });
  }, []);

  return {
    pointerContextValue,
    notice,
    runtimeNotice,
    setNotice,
    setRuntimeNotice,
    selectedProject,
    sidebarCollapsed,
    setSidebarCollapsed,
    setSettingsOpen,
    projectName,
    setProjectName,
    busy,
    selectedFile,
    setSelectedFile,
    selectedId,
    projects,
    stages,
    runBusyTask,
    refreshProjects,
    runWorkbenchImportFlow,
    runDesignStudyCandidates,
    restoreCadSnapshot,
    approveTimelineRun,
    rejectTimelineRun,
    runtimeReady,
    runtimeProvider,
    liveSyncStatus,
    liveSyncDetail,
    liveSyncLastEventAt,
    pendingApprovals,
    resolveApproval,
    effectiveViewerFormat,
    activeFieldDescriptor,
    selectedCaeField,
    setSelectedCaeField,
    selectedLoadCaseId,
    setSelectedLoadCaseId,
    loadCases,
    fieldOverlayConfig,
    setFieldOverlayConfig,
    caeResultsAvailable: hasCaeResultArtifacts,
    caeSetupComplete,
    effectiveViewerUrl,
    pickedFaces,
    addPickedFace,
    clearPickedFaces,
    copyPointerText,
    cadGenerationProgress,
    highlightedFaceIds,
    brepSnapshot,
    clearHighlightedFaces,
    settingsOpen,
    runtime,
    runtimeDraft,
    runtimeBusy,
    llmConfig,
    llmReady,
    apiKey,
    apiKeyHydrated,
    updateApiKey,
    updateRuntimeDraft,
    updateLlmConfig,
    applyLlmProviderPreset,
    restoreDefaultLlmConfig,
    handleLlmTestResult,
    runRuntimeTask,
    restoreRuntimeDefaults,
    localAgentConfig,
    setLocalAgentConfig,
    globalSettingsOpen,
    setGlobalSettingsOpen,
    optimizationStudy,
    surrogateProposals,
    optimizationConvergence,
    editDiff,
    sizingSweepReport,
    meshConvergenceReport,
    projectTimeline,
    caeSetupOverlay,
  };
}
