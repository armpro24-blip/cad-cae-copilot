import type { ObjectRegistryResponse, SelectedGeometryContext } from "./appTypes";
import type { AgentPlan, AgentRunResponse, ArtifactDiffResponse, ArtifactResponse, AutopilotRunState, BenchmarkRun, BenchmarkScenario, CadRecommendationsResponse, CaeArtifactDetection, CaePreprocessingSummary, CaeReviewReport, CaeSetupOverlayResponse, CaeSimulationRunSummary, CapabilityDescriptor, CapabilityPreview, ChatConnection, ChatResponse, ComputedMetricsDocument, ComputedMetricsImportPayload, ComputedMetricsResponse, CopilotLoop, CopilotLoopDemoSeedResponse, CopilotLoopDemoSmokeCheckResponse, CopilotLoopExportRequest, CopilotLoopExportResponse, CopilotLoopList, CopilotLoopReport, CopilotLoopReportDiff, CreateParametricEditProposalRequest, CritiqueResponse, DesignTarget, DesignTargetsDocument, DesignTargetsResponse, EditableParametersResponse, EditDiffResponse, GeometryReportResponse, EngineeringTemplateAdoptTargetsResponse, MeshConvergenceReportResponse, MeshDiagnosticsResponse, MeshPreviewResponse, ParametricEditProposal, SimulationReadinessResponse, SizingSweepReportResponse, EngineeringTemplateCadFixtureResponse, EngineeringTemplateDetail, EngineeringTemplatePreviewResponse, EngineeringTemplateSaveDraftResponse, EngineeringTemplateSummary, FreeCadAdapterPreflightResponse, FreeCadEditParameterRequest, FreeCadEditParameterResponse, FreeCadInspectionEvidenceResponse, FreeCadInspectFeaturesRequest, FreeCadInspectFeaturesResponse, IntentActionExecuteResponse, IntentObserveResponse, IntentPlan, LLMConfig, LocalAgentCapability, ProjectHealthCheckResponse, ProjectRecord, ProjectSummary, ReviewSupportPacketResponse, RuntimeConfig, RuntimeConfigSnapshot, RuntimeEvent, RuntimeRun, RuntimeRunSummary, RuntimeToolInfo, SolverFieldDescriptor, StructuralAdapterPreflightResponse, StructuralPreparePreviewResponse, StructuralSolverInputImportResponse, TargetComparisonResponse, WorkflowDefinition, WorkflowStep } from "./types";

import type { Material, MaterialComparison, MaterialProperties } from "./types/materials";
import type { BOMData } from "./types/bom";
import type { InsertResult, StandardPartCategory, StandardPartSpec } from "./types/standards";
import type { AgentActivityEvent } from "./appUtils";
import { resolveApiBase } from "./apiBase";

// Production serves the SPA and API from the same backend origin. Keep the
// explicit override for the Vite dev server and split deployments.
const API = resolveApiBase(import.meta.env.VITE_API_BASE);

export type PersistedChatMessage = {
  id: number;
  session_id?: string | null;
  project_id: string;
  role: string;
  content: string;
  mode?: string | null;
  created_at: string;
  extra?: Record<string, unknown> | null;
};

export type ChatSession = {
  id: string;
  project_id: string;
  title: string;
  status: string;
  active_run_id?: string | null;
  approval_mode: "strict" | "balanced" | "manual" | string;
  context_summary_json?: string | null;
  context_summary?: Record<string, unknown> | null;
  context_summary_updated_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type PersistedAgentEvent = {
  id: number;
  event_id: string;
  run_id?: string | null;
  project_id?: string | null;
  session_id?: string | null;
  type: string;
  status?: string | null;
  content?: string | null;
  payload?: Record<string, unknown> | null;
  created_at: string;
};

export type RecentActivityResponse = {
  project_id?: string | null;
  count: number;
  events: AgentActivityEvent[];
  latest_ts?: number | null;
};

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const timeoutMs = init?.timeoutMs ?? 30000;
  const timeoutController = new AbortController();
  const timer = setTimeout(() => timeoutController.abort(), timeoutMs);

  let signal: AbortSignal = timeoutController.signal;
  const externalSignal = init?.signal;
  const onExternalAbort = () => timeoutController.abort();

  if (externalSignal) {
    if (externalSignal.aborted) {
      clearTimeout(timer);
      throw new Error("Request aborted");
    }
    externalSignal.addEventListener("abort", onExternalAbort, { once: true });
  }

  try {
    const response = await fetch(`${API}${path}`, {
      headers: {
        ...(init?.headers ?? {}),
      },
      ...init,
      signal,
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }

    return response.json() as Promise<T>;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      if (externalSignal?.aborted) {
        throw new Error("Request aborted");
      }
      throw new Error(`Request timed out after ${timeoutMs / 1000}s`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
    if (externalSignal) {
      externalSignal.removeEventListener("abort", onExternalAbort);
    }
  }
}

export const api = {
  base: API,
  runtime: () => request<RuntimeConfigSnapshot>("/api/runtime"),
  getRuntimeConfig: () => request<RuntimeConfigSnapshot>("/api/runtime-config"),
  updateRuntimeConfig: (payload: RuntimeConfig) =>
    request<RuntimeConfigSnapshot>("/api/runtime-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  testRuntimeConfig: (payload: RuntimeConfig) =>
    request<RuntimeConfigSnapshot>("/api/runtime-config/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getSettings: (signal?: AbortSignal) => request<Record<string, unknown>>("/api/settings", { signal }),
  getSetting: (key: string, signal?: AbortSignal) =>
    request<{ key: string; value: unknown }>(`/api/settings/${encodeURIComponent(key)}`, { signal }),
  updateSetting: (key: string, value: unknown) =>
    request<{ key: string; value: unknown; updated_at: string }>(`/api/settings/${encodeURIComponent(key)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    }),
  deleteSetting: (key: string) =>
    request<{ deleted: boolean; key: string }>(`/api/settings/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),
  testLlmProvider: (config: LLMConfig, apiKey: string, verifyConnection: boolean) =>
    request<{
      config_ready: boolean;
      connection_verified: boolean;
      provider: string;
      model: string;
      base_url?: string | null;
      api_key_present: boolean;
      error_message: string | null;
    }>("/api/llm/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm_config: config, api_key: apiKey, verify_connection: verifyConnection }),
    }),
  listCapabilities: () => request<CapabilityDescriptor[]>("/api/capabilities"),
  previewCapability: (operationName: string, inputs: Record<string, unknown> = {}, approved = false) =>
    request<CapabilityPreview>("/api/capabilities/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation_name: operationName, inputs, approved }),
    }),
  listWorkflows: () => request<WorkflowDefinition[]>("/api/runtime/workflows"),
  planAgent: (payload: {
    message: string;
    project_id?: string | null;
    llm_config?: LLMConfig;
    api_key?: string;
    patch_json?: Record<string, unknown> | null;
    dry_run?: boolean;
    selected_geometry?: SelectedGeometryContext | null;
  }) =>
    request<AgentPlan>("/api/agent/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  runAgent: (payload: {
    message?: string;
    project_id?: string | null;
    llm_config?: LLMConfig;
    api_key?: string;
    patch_json?: Record<string, unknown> | null;
    dry_run?: boolean;
    selected_geometry?: SelectedGeometryContext | null;
    plan?: AgentPlan;
  }) =>
    request<AgentRunResponse>("/api/agent/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  listAgentConnections: () => request<ChatConnection[]>("/api/agent/connections"),
  listLocalAgentCapabilities: () =>
    request<{ adapters: LocalAgentCapability[]; available: LocalAgentCapability[] }>("/api/local-agents/capabilities"),
  getAutopilotRun: (runId: string, signal?: AbortSignal) =>
    request<AutopilotRunState>(`/api/agent/autopilot/runs/${runId}`, { signal }),
  // Approach A: resolve a gated-tool approval for an agentic Claude session.
  resolveAgenticPermission: (permissionId: string, approved: boolean, message?: string) =>
    request<{ status: string; approved: boolean; decision: Record<string, unknown> }>(
      `/api/agent/agentic/permission/${permissionId}/resolve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, message: message || undefined }),
        timeoutMs: 60000,
      },
    ),
  planIntent: (payload: { message: string; project_id?: string | null }) =>
    request<IntentPlan>("/api/intent-planner/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  executeIntentAction: (actionId: string, plan: IntentPlan) =>
    request<IntentActionExecuteResponse>(`/api/intent-planner/actions/${actionId}/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    }),
  observeIntentAction: (payload: { plan: IntentPlan; action_id: string; run_id: string }) =>
    request<IntentObserveResponse>(`/api/intent-planner/observe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  listBenchmarkScenarios: () => request<BenchmarkScenario[]>("/api/benchmarks/scenarios"),
  startBenchmarkRun: (payload: {
    scenario_id: string;
    condition?: string;
    dry_run?: boolean;
    llm_config: LLMConfig;
    api_key?: string;
  }) =>
    request<BenchmarkRun>("/api/benchmarks/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getBenchmarkRun: (runId: string) => request<BenchmarkRun>(`/api/benchmarks/runs/${runId}`),
  listProjects: (signal?: AbortSignal) => request<ProjectRecord[]>("/api/projects", { signal }),
  createProject: (name: string, signal?: AbortSignal) =>
    request<ProjectRecord>("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
      signal,
    }),
  createSampleProject: () => request<ProjectRecord>("/api/projects/sample", { method: "POST" }),
  getProject: (projectId: string, signal?: AbortSignal) => request<ProjectSummary>(`/api/projects/${projectId}`, { signal }),
  deleteProject: (projectId: string) =>
    request<{ deleted: boolean; project_id: string; chat_rows_removed: number }>(
      `/api/projects/${projectId}`,
      { method: "DELETE" },
    ),
  getObjectRegistry: (projectId: string, signal?: AbortSignal) =>
    request<ObjectRegistryResponse>(`/api/projects/${projectId}/object-registry`, { signal }),
  getEditableParameters: (projectId: string, signal?: AbortSignal) =>
    request<EditableParametersResponse>(`/api/projects/${projectId}/editable-parameters`, { signal }),
  createParametricEditProposal: (projectId: string, payload: CreateParametricEditProposalRequest) =>
    request<ParametricEditProposal>(`/api/projects/${projectId}/parametric-edit-proposals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getParametricEditProposal: (projectId: string, proposalId: string, signal?: AbortSignal) =>
    request<ParametricEditProposal>(`/api/projects/${projectId}/parametric-edit-proposals/${proposalId}`, { signal }),
  applyParametricEditProposal: (projectId: string, proposalId: string, confirmScopeRisk?: boolean) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/parametric-edit-proposals/${proposalId}/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmScopeRisk }),
    }),
  getGeometryReport: (projectId: string, signal?: AbortSignal) =>
    request<GeometryReportResponse>(`/api/projects/${projectId}/geometry-report`, { signal }),
  getCaeSetupOverlay: (projectId: string, signal?: AbortSignal) =>
    request<CaeSetupOverlayResponse>(`/api/projects/${projectId}/cae-setup-overlay`, { signal }),
  getMeshPreview: (projectId: string, signal?: AbortSignal) =>
    request<MeshPreviewResponse>(`/api/projects/${projectId}/mesh-preview`, { signal }),
  getMeshDiagnostics: (projectId: string, signal?: AbortSignal) =>
    request<MeshDiagnosticsResponse>(`/api/projects/${projectId}/mesh-diagnostics`, { signal }),
  getEditDiff: (projectId: string, signal?: AbortSignal) =>
    request<EditDiffResponse>(`/api/projects/${projectId}/edit-diff`, { signal }),
  getProjectCritique: (projectId: string, signal?: AbortSignal) =>
    request<CritiqueResponse>(`/api/projects/${projectId}/critique`, { signal }),
  getSimulationReadiness: (projectId: string, signal?: AbortSignal) =>
    request<SimulationReadinessResponse>(`/api/projects/${projectId}/simulation-readiness`, { signal }),
  getSizingSweepReport: (projectId: string, signal?: AbortSignal) =>
    request<SizingSweepReportResponse>(`/api/projects/${projectId}/sizing-sweep-report`, { signal }),
  getMeshConvergenceReport: (projectId: string, signal?: AbortSignal) =>
    request<MeshConvergenceReportResponse>(`/api/projects/${projectId}/mesh-convergence-report`, { signal }),
  getDesignStudySummary: (projectId: string, signal?: AbortSignal) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/design-study/summary`, { signal }),
  runDesignStudyCandidates: (
    projectId: string,
    payload: { candidate_ids?: string[]; compile?: boolean; max_candidates?: number } = {},
  ) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/design-study/run-candidates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeoutMs: 120000,
    }),
  getProjectHealthCheck: (projectId: string) =>
    request<ProjectHealthCheckResponse>(`/api/projects/${projectId}/health-check`),
  getFreeCadAdapterPreflight: () =>
    request<FreeCadAdapterPreflightResponse>(`/api/adapters/freecad/preflight`),
  getStructuralAdapterPreflight: () =>
    request<StructuralAdapterPreflightResponse>(`/api/adapters/structural/preflight`),
  importStructuralSolverInput: (projectId: string, payload: { text: string; run_id?: string; overwrite?: boolean }) =>
    request<StructuralSolverInputImportResponse>(`/api/projects/${projectId}/solver-input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getStructuralPreparePreview: (projectId: string, payload: { run_id?: string; load_case_id?: string; extract_results?: boolean; refresh_summary?: boolean } = {}) =>
    request<StructuralPreparePreviewResponse>(`/api/projects/${projectId}/structural/prepare-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  listEngineeringTemplates: () =>
    request<{ schema_version: string; templates: EngineeringTemplateSummary[]; claim_advancement: "none"; claim_boundary: string }>(
      `/api/engineering-templates`,
    ),
  getEngineeringTemplate: (templateId: string) =>
    request<EngineeringTemplateDetail>(`/api/engineering-templates/${templateId}`),
  previewEngineeringTemplate: (projectId: string, templateId: string, parameters: Record<string, unknown>) =>
    request<EngineeringTemplatePreviewResponse>(
      `/api/projects/${projectId}/engineering-templates/${templateId}/preview`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parameters }),
      },
    ),
  saveEngineeringTemplateDraft: (projectId: string, templateId: string, parameters: Record<string, unknown>) =>
    request<EngineeringTemplateSaveDraftResponse>(
      `/api/projects/${projectId}/engineering-templates/${templateId}/save-draft`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parameters }),
      },
    ),
  adoptEngineeringTemplateTargets: (projectId: string, templateId: string, suggestions?: unknown[]) =>
    request<EngineeringTemplateAdoptTargetsResponse>(
      `/api/projects/${projectId}/engineering-templates/${templateId}/adopt-targets`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(suggestions ? { suggestions } : {}),
      },
    ),
  generateEngineeringTemplateCadFixture: (projectId: string, templateId: string, parameters?: Record<string, unknown>) =>
    request<EngineeringTemplateCadFixtureResponse>(
      `/api/projects/${projectId}/engineering-templates/${templateId}/generate-cad-fixture`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: true, ...(parameters ? { parameters } : {}) }),
      },
    ),
  getReviewSupportPacketPreview: (projectId: string) =>
    request<ReviewSupportPacketResponse>(`/api/projects/${projectId}/review-support-packet/preview`),
  exportReviewSupportPacket: (projectId: string, payload: { packet_id?: string; include_preview_markdown?: boolean } = {}) =>
    request<ReviewSupportPacketResponse>(`/api/projects/${projectId}/review-support-packet/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getFreeCadInspectionEvidence: (projectId: string) =>
    request<FreeCadInspectionEvidenceResponse>(`/api/projects/${projectId}/freecad/inspection-evidence`),
  inspectFreeCadFeatures: (projectId: string, payload: FreeCadInspectFeaturesRequest = {}) =>
    request<FreeCadInspectFeaturesResponse>(
      `/api/projects/${projectId}/freecad/inspect-features`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    ),
  editFreeCadParameter: (projectId: string, payload: FreeCadEditParameterRequest) =>
    request<FreeCadEditParameterResponse>(`/api/projects/${projectId}/freecad/edit-parameter`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getDesignTargets: (projectId: string) =>
    request<DesignTargetsResponse>(`/api/projects/${projectId}/design-targets`),
  saveDesignTargets: (projectId: string, payload: DesignTargetsDocument | DesignTarget[]) =>
    request<DesignTargetsResponse>(`/api/projects/${projectId}/design-targets`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getComputedMetrics: (projectId: string) =>
    request<ComputedMetricsResponse>(`/api/projects/${projectId}/computed-metrics`),
  getTargetComparison: (projectId: string) =>
    request<TargetComparisonResponse>(`/api/projects/${projectId}/target-comparison`),
  previewComputedMetrics: (projectId: string, payload: ComputedMetricsImportPayload) =>
    request<ComputedMetricsResponse>(`/api/projects/${projectId}/computed-metrics/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  saveComputedMetrics: (projectId: string, payload: ComputedMetricsImportPayload | ComputedMetricsDocument) =>
    request<ComputedMetricsResponse>(`/api/projects/${projectId}/computed-metrics`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  uploadFile: async (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ProjectRecord>(`/api/projects/${projectId}/upload`, {
      method: "POST",
      body: form,
    });
  },
  importAieng: (projectId: string) => request(`/api/projects/${projectId}/import-aieng`, { method: "POST" }),
  validate: (projectId: string) => request(`/api/projects/${projectId}/validate`, { method: "POST" }),
  convert: (projectId: string) => request(`/api/projects/${projectId}/convert`, { method: "POST" }),
  chat: (projectId: string, message: string, execute: boolean, sessionId?: string | null) =>
    request<ChatResponse>(`/api/projects/${projectId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, execute, ...(sessionId ? { session_id: sessionId } : {}) }),
    }),
  getFieldDescriptor: (projectId: string, fieldName: string, loadCaseId?: string | null) =>
    request<SolverFieldDescriptor>(
      `/api/projects/${projectId}/fields/${fieldName}${loadCaseId ? `?load_case_id=${encodeURIComponent(loadCaseId)}` : ""}`,
    ),
  listRuns: () => request<RuntimeRunSummary[]>("/api/runtime/runs"),
  startRun: (message: string, projectId?: string | null, toolInput?: Record<string, unknown> | null, extras?: { workflow_id?: string; steps?: WorkflowStep[]; llm_config?: LLMConfig }) =>
    request<RuntimeRun>("/api/runtime/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        project_id: projectId ?? null,
        ...(toolInput ? { tool_input: toolInput } : {}),
        ...(extras?.workflow_id ? { workflow_id: extras.workflow_id } : {}),
        ...(extras?.steps ? { steps: extras.steps } : {}),
        ...(extras?.llm_config ? { llm_config: extras.llm_config } : {}),
      }),
    }),
  getRun: (runId: string) => request<RuntimeRun>(`/api/runtime/runs/${runId}`),
  getRunEvents: (runId: string) => request<RuntimeEvent[]>(`/api/runtime/runs/${runId}/events`),
  getRecentActivity: (projectId?: string | null, limit = 50, sinceTs?: number | null) => {
    const params = new URLSearchParams();
    if (projectId) params.set("project_id", projectId);
    params.set("limit", String(limit));
    if (typeof sinceTs === "number" && Number.isFinite(sinceTs)) params.set("since_ts", String(sinceTs));
    return request<RecentActivityResponse>(`/api/agent-activity/recent?${params.toString()}`);
  },
  approveRun: (runId: string) =>
    request<RuntimeRun>(`/api/runtime/runs/${runId}/approve`, { method: "POST" }),
  rejectRun: (runId: string) =>
    request<RuntimeRun>(`/api/runtime/runs/${runId}/reject`, { method: "POST" }),
  listTools: () => request<RuntimeToolInfo[]>("/api/runtime/tools"),
  getCaeArtifacts: (projectId: string) =>
    request<CaeArtifactDetection>(`/api/projects/${projectId}/cae-artifacts`),
  getCaePreprocessingSummary: (projectId: string) =>
    request<CaePreprocessingSummary>(`/api/projects/${projectId}/cae-preprocessing-summary`),
  getCaeSimulationRunSummary: (projectId: string) =>
    request<CaeSimulationRunSummary>(`/api/projects/${projectId}/cae-simulation-run-summary`),
  getCaeReviewReport: (projectId: string) =>
    request<CaeReviewReport>(`/api/projects/${projectId}/cae-review-report`),
  getCadRecommendations: (projectId: string, strictness: "lenient" | "default" | "strict" = "default") =>
    request<CadRecommendationsResponse>(
      `/api/projects/${projectId}/cad-recommendations?strictness=${strictness}`,
    ),
  startCopilotLoop: (projectId: string, payload: Record<string, unknown> = {}) =>
    request<CopilotLoop>(`/api/projects/${projectId}/copilot-loop/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  listCopilotLoops: (projectId: string) =>
    request<CopilotLoopList>(`/api/projects/${projectId}/copilot-loops`),
  compareCopilotLoopReports: (projectId: string, leftLoopId: string, rightLoopId: string) =>
    request<CopilotLoopReportDiff>(
      `/api/projects/${projectId}/copilot-loops/compare-reports?left=${encodeURIComponent(leftLoopId)}&right=${encodeURIComponent(rightLoopId)}`,
    ),
  exportCopilotLoopReview: (projectId: string, payload: CopilotLoopExportRequest) =>
    request<CopilotLoopExportResponse>(
      `/api/projects/${projectId}/copilot-loops/export-review`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    ),
  seedCopilotLoopDemo: (payload: { name?: string; reset?: boolean } = {}) =>
    request<CopilotLoopDemoSeedResponse>(`/api/demo/copilot-loop/seed`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  resetCopilotLoopDemo: () =>
    request<{ removed: Array<{ project_id: string; name?: string }>; notice: string }>(
      `/api/demo/copilot-loop/reset`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ),
  runCopilotLoopDemoSmokeCheck: (payload: { reset?: boolean } = {}) =>
    request<CopilotLoopDemoSmokeCheckResponse>(`/api/demo/copilot-loop/smoke-check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getCopilotLoop: (projectId: string, loopId: string) =>
    request<CopilotLoop>(`/api/projects/${projectId}/copilot-loop/${loopId}`),
  advanceCopilotLoop: (projectId: string, loopId: string) =>
    request<CopilotLoop>(`/api/projects/${projectId}/copilot-loop/${loopId}/advance`, { method: "POST" }),
  approveCopilotLoop: (projectId: string, loopId: string) =>
    request<CopilotLoop>(`/api/projects/${projectId}/copilot-loop/${loopId}/approve`, { method: "POST" }),
  rejectCopilotLoop: (projectId: string, loopId: string) =>
    request<CopilotLoop>(`/api/projects/${projectId}/copilot-loop/${loopId}/reject`, { method: "POST" }),
  getCopilotLoopReport: (projectId: string, loopId: string) =>
    request<CopilotLoopReport>(`/api/projects/${projectId}/copilot-loop/${loopId}/report`),
  generateCad: (projectId: string, body: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/generate-cad`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  generateCadStream: async (projectId: string, body: Record<string, unknown>): Promise<Response> => {
    const response = await fetch(`${API}/api/projects/${projectId}/generate-cad-stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }
    return response;
  },
  refineCad: (projectId: string, body: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/refine-cad`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  aiPreprocessing: (projectId: string, body: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/ai-preprocessing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  engineeringActionPlan: (projectId: string, message: string) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/engineering-action-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    }),
  pickFace: (projectId: string, x: number, y: number, z: number) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/brep/pick-face`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x, y, z }),
    }),
  getBrepGraph: (projectId: string, signal?: AbortSignal) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/brep-graph`, { signal }),
  buildBrepGraph: (projectId: string, signal?: AbortSignal) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/brep-graph/build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal,
    }),
  getSimulationTools: () =>
    request<Record<string, unknown>>("/api/simulation/tools"),
  chatSetTarget: (projectId: string, message: string) =>
    request<{
      ok: boolean;
      target: {
        target_id: string; label: string; metric: string;
        operator: string; value: number; unit: string;
      };
      action: "added" | "updated";
      total_targets: number;
    }>(`/api/projects/${projectId}/chat-set-target`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    }),
  contextualChat: (
    projectId: string,
    message: string,
    history: Array<{ role: string; content: string }>,
    apiKey?: string,
    sessionId?: string | null,
  ) =>
    request<{ reply: string; context_used: boolean; project_id: string }>(
      `/api/projects/${projectId}/contextual-chat`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history, ...(apiKey ? { api_key: apiKey } : {}), ...(sessionId ? { session_id: sessionId } : {}) }),
      },
    ),
  getProjectArtifact: (projectId: string, path: string, signal?: AbortSignal) =>
    request<ArtifactResponse>(`/api/projects/${projectId}/artifact?path=${encodeURIComponent(path)}`, { signal }),
  diffArtifactJson: (projectId: string, before: unknown, after: unknown) =>
    request<ArtifactDiffResponse>(`/api/projects/${projectId}/artifact/diff`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ before, after }),
    }),

  // ── Material Library ───────────────────────────────────────────────────────
  listMaterials: (category?: string, query?: string) => {
    const params = new URLSearchParams();
    if (category) params.append("category", category);
    if (query) params.append("query", query);
    const qs = params.toString();
    return request<Material[]>(`/api/materials${qs ? `?${qs}` : ""}`);
  },
  getMaterialDetails: (name: string) =>
    request<MaterialProperties>(`/api/materials/${encodeURIComponent(name)}`),
  compareMaterials: (names: string[]) =>
    request<MaterialComparison>("/api/materials/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names }),
    }),

  // ── Standard Parts ─────────────────────────────────────────────────────────
  listStandardParts: (category?: string) =>
    request<StandardPartCategory[]>(
      `/api/standards/parts${category ? `?category=${encodeURIComponent(category)}` : ""}`,
    ),
  getStandardPartSpecs: (partType: string, presetName?: string) =>
    request<StandardPartSpec>(
      `/api/standards/parts/${encodeURIComponent(partType)}/specs${presetName ? `?preset=${encodeURIComponent(presetName)}` : ""}`,
    ),
  insertStandardPart: (projectId: string, partType: string, params: object) =>
    request<InsertResult>(`/api/projects/${projectId}/standards/insert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ part_type: partType, parameters: params }),
    }),

  // ── BOM ────────────────────────────────────────────────────────────────────
  generateBOM: (projectId: string, format?: string) =>
    request<BOMData>(`/api/projects/${projectId}/bom${format ? `?format=${encodeURIComponent(format)}` : ""}`),
  exportBOM: async (projectId: string, format: "csv" | "json" | "xlsx") => {
    const response = await fetch(`${API}/api/projects/${projectId}/bom?format=${encodeURIComponent(format)}`);
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }
    return response.blob();
  },
  projectReportUrl: (projectId: string) => `${API}/api/projects/${encodeURIComponent(projectId)}/report`,
};
