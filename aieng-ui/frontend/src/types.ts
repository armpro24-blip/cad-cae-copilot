export type CadRecommendationProposal = {
  proposal_id: string;
  rank?: number;
  feature_ref: string;
  action_type: string;
  parameter_change?: {
    name?: string;
    from?: number | string;
    to?: number | string;
  };
  rationale?: string;
  expected_impact?: string;
  confidence?: "high" | "medium" | "low";
  targets_addressed?: string[];
  risks?: string[];
};

export type CadVerificationCheck = {
  check_id: string;
  category: "schema" | "manufacturability" | "regression";
  status: "pass" | "warn" | "fail" | "skipped";
  message?: string;
  evidence_refs?: string[];
};

export type CadVerificationVerdict = {
  proposal_id?: string;
  feature_ref?: string;
  action_type?: string;
  verdict: "pass" | "warn" | "fail";
  strictness?: string;
  checks?: CadVerificationCheck[];
  blockers?: CadVerificationCheck[];
  warnings_from_checks?: CadVerificationCheck[];
};

export type CadRecommendationsResponse = {
  ok: boolean;
  package_path?: string;
  strictness?: string;
  recommendations?: {
    schema_version?: string;
    ok?: boolean;
    proposals?: CadRecommendationProposal[];
    skipped_features?: Array<{ feature_ref?: string; reason?: string }>;
    modification_vocabulary?: string[];
    evidence?: Record<string, unknown>;
    warnings?: string[];
    llm_summary?: {
      one_line?: string;
      key_findings?: string[];
      risks?: string[];
      limitations?: string[];
    };
  };
  verification?: {
    schema_version?: string;
    verdicts?: CadVerificationVerdict[];
    summary?: { pass?: number; warn?: number; fail?: number; total?: number };
    warnings?: string[];
  };
  claim_policy?: Record<string, boolean>;
  errors?: string[];
};

export type CopilotLoopStepStatus =
  | "not_started"
  | "running"
  | "waiting_for_approval"
  | "completed"
  | "skipped"
  | "partial"
  | "error";

export type CopilotLoopStepKind = "read_only" | "mutation" | "expensive" | "postprocess" | "review";

export type CopilotLoopArtifact = {
  path: string;
  label?: string;
  mediaType?: string;
  kind?: string;
  role?: string;
};

export type CopilotLoopToolCall = {
  toolName: string;
  status: string;
  runId?: string | null;
};

export type CopilotLoopStep = {
  id: string;
  title: string;
  status: CopilotLoopStepStatus;
  kind: CopilotLoopStepKind;
  requiresApproval: boolean;
  summary?: string;
  limitation?: string;
  artifacts?: CopilotLoopArtifact[];
  warnings?: string[];
  errors?: string[];
  toolCalls?: CopilotLoopToolCall[];
  data?: Record<string, unknown>;
};

export type CopilotLoop = {
  schema_version: string;
  loop_id: string;
  project_id: string;
  package_path?: string;
  status: string;
  created_at: string;
  updated_at: string;
  strictness?: string;
  selected_proposal_id?: string | null;
  current_step_id?: string | null;
  steps: CopilotLoopStep[];
  context?: Record<string, unknown>;
};

export type CopilotLoopReport = {
  schema_version: string;
  loop_id: string;
  generated_at: string;
  artifact_path?: string | null;
  claim_boundary?: {
    claims_advanced?: boolean;
    design_certified?: boolean;
    statement?: string;
  };
  apply_rejected?: boolean;
  stale_artifacts?: string[];
  markdown: string;
};

export type CopilotLoopDecision =
  | "approved"
  | "rejected"
  | "pending"
  | "blocked"
  | "error"
  | "none";

export type CopilotLoopProposalSummary = {
  proposal_id?: string | null;
  feature_ref?: string | null;
  action_type?: string | null;
  parameter_name?: string | null;
  parameter_from?: unknown;
  parameter_to?: unknown;
  rationale?: string | null;
};

export type CopilotLoopMetricSummary = {
  improved: number;
  regressed: number;
  unchanged: number;
  unknown: number;
  total: number;
};

export type CopilotLoopTargetSummary = {
  pass: number;
  fail: number;
  unknown: number;
  not_evaluated: number;
  total: number;
};

export type CopilotLoopSummary = {
  loop_id: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  current_step_id?: string | null;
  step_total: number;
  step_terminal_count?: number;
  waiting_for_approval: boolean;
  decision?: CopilotLoopDecision;
  proposal_summary?: CopilotLoopProposalSummary | null;
  verification_status?: string | null;
  report_path?: string | null;
  stale_artifact_count?: number;
  warning_count?: number;
  error_count?: number;
  metric_summary?: CopilotLoopMetricSummary | null;
  target_summary?: CopilotLoopTargetSummary | null;
  strictness?: string | null;
};

export type CopilotLoopList = {
  loops: CopilotLoopSummary[];
};

export type CopilotLoopReportDiffHighlight = {
  id: string;
  label: string;
  status: "changed" | "unchanged" | "unknown" | "missing";
  severity?: "info" | "warning" | "critical";
  left?: string | null;
  right?: string | null;
  summary: string;
};

export type CopilotLoopReportDiff = {
  schema_version?: string;
  left_loop_id: string;
  right_loop_id: string;
  left_report_path?: string | null;
  right_report_path?: string | null;
  left_report_exists: boolean;
  right_report_exists: boolean;
  left_report_truncated?: boolean;
  right_report_truncated?: boolean;
  left_text?: string | null;
  right_text?: string | null;
  unified_diff?: string | null;
  added_lines: number;
  removed_lines: number;
  highlights?: CopilotLoopReportDiffHighlight[];
  warnings: string[];
  claim_boundary: string;
};

export type CopilotLoopExportRequest = {
  loop_ids: string[];
  include_reports?: boolean;
  include_diff?: boolean;
  include_highlights?: boolean;
};

export type CopilotLoopExportResponse = {
  schema_version?: string;
  project_id: string;
  loop_ids: string[];
  export_path: string;
  export_local_path?: string;
  export_text: string;
  warnings: string[];
  claim_boundary: string;
  included?: {
    reports?: boolean;
    diff?: boolean;
    highlights?: boolean;
  };
};

export type CopilotLoopDemoSeedResponse = {
  schema_version?: string;
  project_id: string;
  project_name: string;
  package_path: string;
  demo_kind?: string;
  reused?: boolean;
  loops: Array<{
    loop_id: string;
    decision: string;
    description: string;
  }>;
  next_action?: string;
  notice: string;
};

export type CopilotLoopDemoSmokeCheckItem = {
  id: string;
  label: string;
  status: "passed" | "failed" | "skipped";
  summary: string;
  details?: string[];
};

export type CopilotLoopDemoSmokeCheckResponse = {
  ok: boolean;
  project_id?: string;
  reused?: boolean;
  checks: CopilotLoopDemoSmokeCheckItem[];
  export_path?: string | null;
  warnings: string[];
  claim_boundary: string;
};

export type ProjectHealthReadiness = "ready" | "partial" | "not_ready" | "unknown";

export type ProjectHealthCheckStatus = "passed" | "warning" | "failed" | "unknown" | "skipped";

export type ProjectHealthCheckCategory = "package" | "evidence" | "cad" | "cae" | "targets" | "claims" | "loops" | "demo";

export type ProjectHealthCheckItem = {
  id: string;
  category: ProjectHealthCheckCategory;
  label: string;
  status: ProjectHealthCheckStatus;
  summary: string;
  details?: string[];
  next_action?: string | null;
};

export type ProjectHealthActionPriority = "high" | "medium" | "low";

export type ProjectHealthActionType = "manual" | "navigate" | "run_read_only_tool" | "start_loop" | "compare_loops" | "export_review";

export type ProjectHealthRecommendedAction = {
  id: string;
  priority: ProjectHealthActionPriority;
  label: string;
  summary: string;
  source_check_ids: string[];
  action_type: ProjectHealthActionType;
  target?: {
    tab?: string | null;
    section?: string | null;
    intent?: string | null;
    endpoint?: string | null;
    project_id?: string | null;
  } | null;
  safety: {
    mutates_package: boolean;
    runs_solver: boolean;
    advances_claim: boolean;
  };
};

export type ProjectHealthCheckResponse = {
  ok: boolean;
  readiness: ProjectHealthReadiness;
  project_id: string;
  project_name?: string | null;
  package_path?: string | null;
  checks: ProjectHealthCheckItem[];
  warnings: string[];
  limitations: string[];
  claim_boundary: string;
  recommended_actions: ProjectHealthRecommendedAction[];
  overall_next_action?: string | null;
};

export type DesignTargetPriority = "required" | "preferred" | "informational" | "high" | "medium" | "low" | "critical";

export type DesignTargetOperator = "<=" | ">=" | "<" | ">" | "==" | "within_range" | "preserve" | "priority" | "reduce_by_at_least" | "increase_by_at_least" | "reduce_by_percent" | "increase_by_percent";

export type DesignTarget = {
  target_id: string;
  label: string;
  metric: string;
  operator: DesignTargetOperator;
  value: number;
  threshold_min?: number | null;
  threshold_max?: number | null;
  unit?: string | null;
  scope?: string | null;
  load_case_id?: string | null;
  priority?: DesignTargetPriority;
  rationale?: string | null;
};

export type DesignTargetsDocument = {
  schema_version: string;
  targets: DesignTarget[];
  warnings?: string[];
};

export type DesignTargetsResponse = {
  ok: boolean;
  project_id: string;
  artifact_path?: string | null;
  document?: DesignTargetsDocument | null;
  targets: DesignTarget[];
  warnings: string[];
};

export type ComputedMetricValue = {
  value: number;
  unit?: string | null;
  source?: string | null;
};

export type ComputedMetricLoadCase = {
  load_case_id: string;
  metrics: Record<string, ComputedMetricValue>;
};

export type ComputedMetricsDocument = {
  schema_version: string;
  metrics_source?: {
    tool?: string | null;
    format?: string | null;
    imported_by?: string | null;
  };
  global_metrics?: Record<string, ComputedMetricValue>;
  load_cases?: ComputedMetricLoadCase[];
  warnings?: string[];
};

export type ComputedMetricsImportPayload = {
  format: "json" | "csv";
  text?: string;
  document?: unknown;
};

export type ComputedMetricsValidationError = {
  code: string;
  message: string;
  row?: number | null;
  field?: string | null;
};

export type ComputedMetricTargetMapping = {
  target_id: string;
  target_label: string;
  metric: string;
  load_case_id?: string | null;
  status: "mapped" | "missing_metric" | "ambiguous" | "unknown";
  matched_metric?: string | null;
  summary: string;
};

export type ComputedMetricsResponse = {
  ok: boolean;
  project_id: string;
  artifact_path?: string | null;
  changed_artifact_path?: string | null;
  document?: ComputedMetricsDocument | null;
  metrics_count: number;
  load_case_count: number;
  warnings: string[];
  errors: ComputedMetricsValidationError[];
  target_mapping: ComputedMetricTargetMapping[];
  claim_boundary: string;
};

export type DesignTargetComparisonStatus = "pass" | "fail" | "unknown" | "not_evaluated";

export type DesignTargetComparisonItem = {
  target_id: string;
  target_label?: string | null;
  target_type?: string;
  metric?: string | null;
  load_case_id?: string | null;
  expected?: unknown;
  actual?: unknown;
  comparator?: string;
  status: DesignTargetComparisonStatus;
  reason_code?: string | null;
  metric_mapping_status?: ComputedMetricTargetMapping["status"] | null;
  evidence_refs?: string[];
  source_artifacts?: string[];
  notes?: string;
};

export type DesignTargetComparisons = {
  present?: boolean;
  target_set_id?: string;
  evaluated_at?: string;
  summary?: {
    total?: number;
    pass?: number;
    fail?: number;
    unknown?: number;
    not_evaluated?: number;
  };
  items?: DesignTargetComparisonItem[];
};

export type TargetComparisonResponse = {
  ok: boolean;
  project_id: string;
  package_path?: string | null;
  source: string;
  comparison: DesignTargetComparisons;
  summary: NonNullable<DesignTargetComparisons["summary"]>;
  items: DesignTargetComparisonItem[];
  warnings: string[];
  claim_boundary: string;
};

export type CaeMode = "cad_only" | "cae_setup" | "cae_result" | "cae_validation";

export type CaePreprocessingSummary = {
  schema_version: string;
  summary_type: string;
  status: {
    has_cae_setup: boolean;
    has_materials: boolean;
    has_loads: boolean;
    has_boundary_conditions: boolean;
    has_constraints: boolean;
    has_mesh: boolean;
    has_load_cases: boolean;
    has_solver_settings: boolean;
    has_cae_mapping: boolean;
    ready_for_solver: boolean;
    missing_items: string[];
    warnings: string[];
  };
  llm_summary: {
    one_line: string;
    key_findings: string[];
    risks: string[];
    recommended_next_actions: string[];
    limitations: string[];
  };
};

export type CaeSimulationRunSummary = {
  schema_version: string;
  summary_type: string;
  status: {
    has_simulation_runs: boolean;
    run_count: number;
    latest_run_id: string | null;
    has_completed_run: boolean;
    has_converged_run: boolean;
    has_failed_run: boolean;
    warnings: string[];
  };
  runs: Array<{
    run_id: string;
    solver: string;
    software: string;
    analysis_type: string;
    state: string;
    solved: boolean | null;
    converged: boolean | null;
    warnings: string[];
    errors: string[];
    log_file: string | null;
  }>;
  llm_summary: {
    one_line: string;
    key_findings: string[];
    risks: string[];
    recommended_next_actions: string[];
    limitations: string[];
  };
};

export type CaeArtifactDetection = {
  mode: CaeMode;
  artifacts: Record<string, boolean>;
  has_cae_setup: boolean;
  has_mesh: boolean;
  has_solver_settings: boolean;
  has_results: boolean;
  has_fields: boolean;
  has_validation: boolean;
  detected_count: number;
  total_count: number;
};

export type CaeReviewReport = {
  schema_version: string;
  report_type: "cae_review_report" | string;
  project_id: string;
  package_name: string;
  generated_at: string;
  summary: string;
  sections: {
    available_evidence: {
      facts: string[];
      evidence_count: number;
      source_artifacts: string[];
    };
    missing_information: {
      items: string[];
      warnings: string[];
    };
    unsupported_information: {
      items: string[];
      limitations: string[];
    };
    stale_evidence: {
      requires_revalidation: boolean;
      reason?: string | null;
      triggering_tool?: string | null;
      domains: string[];
      artifacts: string[];
    };
    design_targets: {
      present: boolean;
      summary: Record<string, unknown>;
      items: DesignTargetComparisonItem[];
      note: string;
    };
    claim_boundary: {
      claims_advanced: boolean;
      claim_maps_present: string[];
      status: string;
      message: string;
    };
    next_actions: {
      items: string[];
    };
  };
  markdown: string;
  source_summaries?: Record<string, unknown>;
};

export type ProjectRecord = {
  id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  source_step?: string | null;
  aieng_file?: string | null;
  web_asset?: string | null;
  web_asset_format?: string | null;
  last_error?: string | null;
};

export type RuntimeConfig = {
  provider: string;
  aieng_root: string;
  freecad_mcp_root: string;
  freecad_home: string;
  topology_backend: "auto" | "mock" | "occ" | string;
};

export type RuntimeProbe = {
  provider: string;
  topology_backend_requested: string;
  topology_backend_resolved: string;
  aieng_root: string;
  aieng_src_exists: boolean;
  freecad_mcp_root: string;
  freecad_mcp_src_exists: boolean;
  freecad_home: string;
  freecad_cmd: string;
  freecad_python: string;
  freecad_cmd_exists: boolean;
  freecad_python_exists: boolean;
  build123d_available?: boolean;
  ocp_available?: boolean;
  ready: boolean;
  issues: string[];
  bridge?: Record<string, unknown>;
  bridge_error?: string;
  whitelisted_tools?: string[];
};

export type RuntimeConfigSnapshot = {
  config: RuntimeConfig;
  defaults: RuntimeConfig;
  probe: RuntimeProbe;
  config_path: string;
  persisted_exists: boolean;
};

export type ProjectSummary = {
  project: ProjectRecord;
  files?: Record<string, unknown>;
  members: string[];
  manifest?: Record<string, unknown> | null;
  feature_graph?: Record<string, unknown> | null;
  topology?: Record<string, unknown> | null;
  constraints?: Record<string, unknown> | null;
  validation?: Record<string, unknown> | null;
  viewer?: Record<string, unknown> | null;
  viewer_url?: string | null;
  ai_summary?: string | null;
  derived?: Record<string, unknown>;
  summary_error?: string | null;
  summary_mode?: string | null;
  cae?: {
    present: boolean;
    constraints_count: number;
    constraint_types: Record<string, number>;
    materials_count: number;
    boundary_conditions_count: number;
    loads_count: number;
    evidence_count: number;
    result_evidence_count: number;
    results_available: boolean;
    available_fields: string[];
    simulation_targets: Array<Record<string, unknown>>;
    protected_regions: Array<Record<string, unknown>>;
    materials: Array<Record<string, unknown>>;
    boundary_conditions: Array<Record<string, unknown>>;
    loads: Array<Record<string, unknown>>;
    evidence: Array<Record<string, unknown>>;
    mapping?: Record<string, unknown> | null;
    solver_status?: Record<string, unknown>;
    solver_fields?: Array<{
      field_name: string;
      descriptor_url: string;
      min_value: number;
      max_value: number;
      unit?: string | null;
      format: string;
      available: boolean;
    }> | null;
    artifact_detection?: CaeArtifactDetection | null;
    preprocessing_summary?: {
      schema_version: string;
      summary_type: string;
      status: {
        has_cae_setup: boolean;
        has_materials: boolean;
        has_loads: boolean;
        has_boundary_conditions: boolean;
        has_constraints: boolean;
        has_mesh: boolean;
        has_load_cases: boolean;
        has_solver_settings: boolean;
        has_cae_mapping: boolean;
        ready_for_solver: boolean;
        missing_items: string[];
        warnings: string[];
      };
      llm_summary: {
        one_line: string;
        key_findings: string[];
        risks: string[];
        recommended_next_actions: string[];
        limitations: string[];
      };
    } | null;
    simulation_run_summary?: {
      schema_version: string;
      summary_type: string;
      status: {
        has_simulation_runs: boolean;
        run_count: number;
        latest_run_id: string | null;
        has_completed_run: boolean;
        has_converged_run: boolean;
        has_failed_run: boolean;
        warnings: string[];
      };
      runs: Array<{
        run_id: string;
        solver: string;
        software: string;
        analysis_type: string;
        state: string;
        solved: boolean | null;
        converged: boolean | null;
        warnings: string[];
        errors: string[];
        log_file: string | null;
      }>;
      llm_summary: {
        one_line: string;
        key_findings: string[];
        risks: string[];
        recommended_next_actions: string[];
        limitations: string[];
      };
    } | null;
    result_summary?: {
      schema_version: string;
      summary_type: string;
      source: {
        package_path: string;
        solver: string;
        software: string | null;
        source_files: string[];
      };
      status: {
        mode: CaeMode;
        has_cae_setup: boolean;
        has_mesh: boolean;
        has_results: boolean;
        has_fields: boolean;
        has_validation: boolean;
        warnings: string[];
      };
      artifacts: {
        mesh_files: string[];
        field_files: string[];
        result_summary_files: string[];
        evidence_files: string[];
        validation_files: string[];
        setup_files: string[];
      };
      solver_settings: {
        solver_type?: string | null;
        analysis_type?: string | null;
        parameters?: Record<string, unknown>;
      } | null;
      load_cases: Array<{
        id: string;
        name: string;
        type: string;
        magnitude?: number | null;
        unit?: string | null;
        description?: string | null;
        source_file: string;
      }>;
      field_metadata: {
        fields: Array<Record<string, unknown>>;
        format?: string | null;
        count: number;
      } | null;
      computed_values: {
        extrema_computed: boolean;
        source?: string | null;
        computed_by?: string | null;
        max_displacement: {
          value: number;
          unit: string | null;
          field?: string | null;
          location?: Record<string, unknown> | null;
        } | null;
        max_von_mises_stress: {
          value: number;
          unit: string | null;
          field?: string | null;
          location?: Record<string, unknown> | null;
        } | null;
        minimum_safety_factor: {
          value: number;
          unit: string | null;
          basis?: string | null;
          location?: Record<string, unknown> | null;
        } | null;
        by_load_case?: Array<{
          id: string;
          metrics: Record<string, unknown>;
        }> | null;
      };
      design_target_comparisons?: DesignTargetComparisons | null;
      llm_summary: {
        one_line: string;
        key_findings: string[];
        risks: string[];
        recommended_next_actions: string[];
        limitations: string[];
      };
    } | null;
  } | null;
  integration?: RuntimeConfigSnapshot | Record<string, unknown>;
};

export type SolverFieldDescriptor = {
  field_name: string;
  project_id: string;
  load_case_id?: string | null;
  format: "vertex_synthetic" | "vertex_json" | string;
  basis?: string | null;
  min_value: number;
  max_value: number;
  unit?: string | null;
  colormap?: string | null;
  source?: string | null;
  values?: number[] | null;
  node_coords?: [number, number, number][] | null;
  /** Per-node displacement vectors [ux, uy, uz] in model units. Present for displacement-sourced fields. */
  vectors?: [number, number, number][] | null;
  warnings?: string[] | null;
  bbox_status?: "aligned" | "suspicious" | null;
};

export type FieldProbe = {
  value: number;
  unit: string | null;
  coord: [number, number, number];
  pointer: string | null;
  screenX: number;
  screenY: number;
};

export type FieldOverlayConfig = {
  /** Active colormap name. `null` / omitted = use the descriptor default. */
  colormap?: string | null;
  /** Manual range minimum. `null` / omitted = auto (descriptor min_value). */
  clampMin?: number | null;
  /** Manual range maximum. `null` / omitted = auto (descriptor max_value). */
  clampMax?: number | null;
  /** Number of discrete contour bands. `null` / omitted / <2 = continuous. */
  bands?: number | null;
  /** Hide values below this threshold (isolate everything >= threshold). */
  thresholdMin?: number | null;
  /** Hide values above this threshold (isolate everything <= threshold). */
  thresholdMax?: number | null;
};

export type MeshPreviewResponse = {
  available: boolean;
  node_count?: number;
  element_count?: number;
  element_type?: string | null;
  target_size_mm?: number | null;
  nodes?: [number, number, number][];
  edges?: [number, number][];
  quality?: {
    coarse_flag?: boolean;
    note?: string | null;
  };
};

export type MeshDiagnosticsResponse = {
  available: boolean;
  overall_verdict?: string | null;
  verdict?: string | null;
  node_count?: number;
  element_count?: number;
  tet_count?: number;
  degenerate_element_count?: number;
  poor_element_count?: number;
  broken_element_count?: number;
  degenerate_element_ids?: number[];
  poor_element_ids?: number[];
  broken_element_ids?: number[];
  max_aspect_ratio?: number | null;
  mean_aspect_ratio?: number | null;
  worst_element_id?: number | null;
  findings?: {
    severity?: string;
    code?: string;
    count?: number;
    element_ids?: number[];
    threshold?: number;
    thresholds?: Record<string, number>;
    max_aspect_ratio?: number | null;
    worst_element_id?: number | null;
    message?: string;
  }[];
  set_coverage?: {
    verdict?: string | null;
    set_count?: number;
    empty_set_count?: number;
    unresolved_set_count?: number;
    sparse_set_count?: number;
    note?: string | null;
  } | null;
  note?: string | null;
};

export type RuntimeEventType =
  | "run_started"
  | "plan_created"
  | "tool_started"
  | "tool_succeeded"
  | "tool_failed"
  | "approval_required"
  | "approval_granted"
  | "approval_rejected"
  | "run_completed"
  | "run_failed"
  | "run_rejected"
  | "run_cancelled";

export type RuntimeEvent = {
  id: string;
  run_id: string;
  type: RuntimeEventType;
  timestamp: string;
  payload?: unknown;
};

export type RuntimeToolCall = {
  id: string;
  name: string;
  input: unknown;
  requires_approval: boolean;
};

export type RuntimeToolError = {
  code: string;
  message: string;
  tool_name?: string | null;
  details?: Record<string, unknown> | null;
};

export type RuntimeToolResult = {
  id: string;
  status: "success" | "error" | "needs_approval" | "rejected";
  output?: unknown;
  error?: string | null;
  artifacts?: unknown[];
};

export type RuntimeRun = {
  run_id: string;
  message: string;
  created_at: string;
  status: "pending" | "running" | "completed" | "failed" | "awaiting_approval" | "rejected" | "cancelled";
  plan: Array<{ name: string; description: string; input: Record<string, unknown> }>;
  events: RuntimeEvent[];
  tool_calls: RuntimeToolCall[];
  tool_results: RuntimeToolResult[];
  tool_errors: RuntimeToolError[];
  errors: string[];
  project_id?: string | null;
  package_path?: string | null;
  summary: string;
  pending_step_index?: number | null;
};

export type RuntimeRunSummary = {
  run_id: string;
  created_at: string;
  status: RuntimeRun["status"];
  message: string;
  project_id?: string | null;
  event_count: number;
  last_event_type?: string | null;
  error_summary?: string | null;
};

export type RuntimeToolInfo = {
  name: string;
  requires_approval: boolean;
  description: string;
};

// ── Intent Planner (v0.35.1) ───────────────────────────────────────────────

export type IntentActionMode = "read_only" | "metadata_write" | "mutation" | "expensive";

export type IntentAction = {
  id: string;
  label: string;
  description: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  mode: IntentActionMode;
  requires_approval: boolean;
  expected_artifacts: string[];
  stale_impacts: string[];
  risk_notes: string[];
};

export type IntentConstraint = {
  kind: string;
  [key: string]: unknown;
};

export type IntentRefusal = {
  tool_name: string | null;
  reason: string;
};

export type IntentPlan = {
  schema_version: string;
  plan_id: string;
  planner_mode: "heuristic" | "llm";
  message: string;
  project_id: string | null;
  task_summary: string;
  inferred_engineering_domain: string;
  inferred_template_id: string | null;
  extracted_constraints: IntentConstraint[];
  extracted_parameters: Record<string, unknown>;
  missing_information: string[];
  assumptions: string[];
  actions: IntentAction[];
  required_approvals: string[];
  evidence_scope: string[];
  refusals: IntentRefusal[];
  warnings: string[];
  claim_advancement: "none";
  claim_boundary: string;
};

export type IntentObservationStatus =
  | "submitted_for_approval"
  | "approved_executed"
  | "completed"
  | "rejected"
  | "failed";

export type IntentObservationArtifactChange = {
  path: string | null;
  kind: string;
  operation: string;
};

export type IntentObservationReadinessSnapshot = {
  ready_to_run: boolean;
  missing_items: string[];
};

export type IntentObservationReadinessDelta = {
  evaluated: boolean;
  before: IntentObservationReadinessSnapshot | null;
  after: IntentObservationReadinessSnapshot | null;
  resolved_items?: string[];
  newly_missing_items?: string[];
  note?: string;
};

export type IntentObservationRecommendation = {
  kind: string;
  label: string;
  rationale: string;
  reference?: string;
  details?: string[];
};

export type CadObservationStatus =
  | "available"
  | "metadata_only"
  | "missing"
  | "invalid"
  | "unknown";

export type CadGeometryEvidenceLevel =
  | "none"
  | "metadata"
  | "exported_geometry"
  | "live_cad_snapshot";

export type CadObservationNamedRegion = {
  id?: string;
  name?: string;
  role?: string;
  description?: string;
  [key: string]: unknown;
};

export type CadObservationCaeReadinessHints = {
  mesh_evidence: boolean;
  solver_input_evidence: boolean;
  computed_metrics_evidence: boolean;
  present_paths: string[];
  has_design_targets?: boolean;
};

export type CadObservation = {
  schema_version: string;
  status: CadObservationStatus;
  source_artifacts: string[];
  geometry_evidence_level: CadGeometryEvidenceLevel;
  summary: string;
  known_geometry: Record<string, unknown>;
  known_parameters: Record<string, unknown>;
  known_materials: Record<string, unknown>;
  known_load_candidates: CadObservationNamedRegion[];
  known_support_candidates: CadObservationNamedRegion[];
  known_named_regions: CadObservationNamedRegion[];
  semantic_labels: string[];
  topology_references: Record<string, unknown>;
  missing_information: string[];
  cae_readiness_hints: CadObservationCaeReadinessHints;
  warnings: string[];
  claim_advancement: "none";
  claim_boundary: string;
  next_recommended_actions: IntentObservationRecommendation[];
};

export type IntentObservation = {
  schema_version: string;
  plan_id: string | null;
  action_id: string;
  run_id: string | null;
  tool_name: string;
  mode: IntentActionMode;
  status: IntentObservationStatus;
  summary: string;
  artifact_changes: IntentObservationArtifactChange[];
  evidence_refs: string[];
  audit_event_ids: string[];
  stale_changes: string[];
  readiness_delta: IntentObservationReadinessDelta;
  warnings: string[];
  errors: string[];
  claim_advancement: "none";
  claim_boundary: string;
  next_recommended_actions: IntentObservationRecommendation[];
  cad_observation?: CadObservation | null;
};

export type IntentActionExecuteResponse = {
  plan_id: string;
  action: IntentAction;
  run: RuntimeRun;
  observation: IntentObservation;
};

export type IntentObserveResponse = IntentActionExecuteResponse;

export type CapabilityDescriptor = {
  name: string;
  source: string;
  category: string;
  purpose: string;
  required_inputs: string[];
  optional_inputs: string[];
  mutates_cad: boolean;
  mutates_package: boolean;
  may_update_claim_map: boolean;
  runtime_requirements: string[];
  dry_run_support: string;
  side_effects: string[];
  claim_policy: Record<string, unknown>;
  available: boolean;
  unavailable_reason?: string | null;
};

export type CapabilityPreview = {
  status: string;
  operation_name: string;
  capability?: CapabilityDescriptor;
  approval_required?: boolean;
  blocked?: boolean;
  preview?: {
    operation_name: string;
    would_write_artifacts: string[];
    would_update_evidence: boolean;
    would_update_traces: boolean;
    would_touch_claims: boolean;
    guard_checks_required: string[];
    unavailable_runtime_blocks: string[];
    expected_duration_estimate: string;
    warnings: string[];
  } | null;
  errors?: string[];
};

export type WorkflowStep = {
  id: string;
  kind: "tool" | "mcp_tool" | "llm" | "approval" | "benchmark" | "artifact" | string;
  tool_name?: string;
  description?: string;
  input?: Record<string, unknown>;
  status: string;
  preview?: Record<string, unknown> | null;
  approval_required?: boolean;
  artifacts?: unknown[];
  errors?: string[];
};

export type WorkflowDefinition = {
  id: string;
  title: string;
  description: string;
  required_context: string[];
  steps: WorkflowStep[];
};

export type LLMConfig = {
  provider: string;
  model: string;
  base_url?: string | null;
  api_key?: string | null;
  api_key_env?: string | null;
  temperature: number;
  top_p: number;
  max_output_tokens: number;
  seed?: number | null;
  input_price_per_million_tokens?: number | null;
  output_price_per_million_tokens?: number | null;
};

export type LocalAgentConfig = {
  preferredAdapterId: string | null;
};

export type BenchmarkScenario = {
  id: string;
  name: string;
  path: string;
  question_file: string;
  condition_a_path: string;
  condition_b_index: string;
  condition_b_source: string;
  has_condition_b_package: boolean;
  has_condition_b_contents: boolean;
  rubric_file: string;
  schema_file: string;
};

export type BenchmarkRun = {
  run_id: string;
  status: string;
  scenario_id: string;
  dry_run: boolean;
  created_at: string;
  result: Record<string, unknown>;
  result_path?: string | null;
  events: Array<{ id: string; type: string; timestamp: string; payload?: unknown }>;
  warnings: string[];
  errors?: string[];
};

export type AgentPlan = {
  reply: string;
  mode: "llm" | "heuristic" | string;
  message: string;
  project_id?: string | null;
  steps: WorkflowStep[];
  requires_approval: boolean;
  preview: {
    step_count: number;
    tools: string[];
    would_execute: string[];
    approval_gated: string[];
    side_effects: string[];
    warnings: string[];
  };
  warnings: string[];
  errors: string[];
  llm_raw?: string | null;
  llm_config?: Record<string, unknown>;
};

export type AgentRunResponse = {
  agent: AgentPlan;
  run: RuntimeRun;
};

export type LocalAgentCapability = {
  adapter_id: string;
  label: string;
  status: "available" | "blocked" | "missing" | "error" | string;
  command: string;
  command_path?: string | null;
  version?: string | null;
  supports_non_interactive: boolean;
  supports_json: boolean;
  supports_json_schema: boolean;
  supports_tool_disable: boolean;
  diagnostic: string;
  probe_duration_ms: number;
};

export type AutopilotObservation = {
  id: string;
  kind: string;
  summary: string;
  data: Record<string, unknown>;
  created_at: string;
};

export type AgentNextActionType =
  | "answer_user"
  | "create_plan"
  | "update_plan"
  | "execute_step"
  | "ask_user"
  | "summarize_context"
  | "wait_for_user"
  | "finish_task";

export type AgentNextAction = {
  type: AgentNextActionType;
  reason: string;
  target_step_id?: string | null;
  payload: Record<string, unknown>;
};

export type AutopilotAgentMode = "assist" | "autopilot" | "full_agent";
export type ApprovalMode = "strict" | "balanced" | "manual";

export type AutopilotApproval = {
  id: string;
  tool_name: string;
  input: Record<string, unknown>;
  level: string;
  explanation: string;
  side_effect_summary?: string | null;
  risk_summary?: string | null;
  target_project_id?: string | null;
  code_preview?: string | null;
  artifact_preview?: string | null;
  recommended_action?: string | null;
  skill_plan_brief?: string | null;
  skill_plan_assumptions?: string[];
  skill_plan_warnings?: string[];
  skill_plan_verification_targets?: string[];
  created_at: string;
};

export type AutopilotAgentPlanStep = {
  id: string;
  title: string;
  kind: "observe" | "skill" | "tool" | "approval" | "verify" | "repair" | "summarize" | string;
  status: "pending" | "running" | "completed" | "blocked" | "failed" | "skipped" | string;
  tool_name?: string | null;
  skill_name?: string | null;
  summary: string;
  evidence: Record<string, unknown>;
};

export type AutopilotAgentPlan = {
  id: string;
  objective: string;
  status: "pending" | "running" | "completed" | "blocked" | "failed" | "cancelled" | string;
  steps: AutopilotAgentPlanStep[];
  current_step_id?: string | null;
  created_at: string;
  updated_at: string;
};

export type AutopilotWorkingState = {
  objective: string;
  current_mode: string;
  accepted_assumptions: string[];
  open_questions: string[];
  latest_evidence: Array<Record<string, unknown>>;
  current_blockers: string[];
  last_successful_tool?: string | null;
  recommended_next_action?: string | null;
  updated_at: string;
};

export type AutopilotRunState = {
  run_id: string;
  status: "running" | "awaiting_approval" | "completed" | "failed" | "cancelled" | "blocked" | "chatting" | string;
  message: string;
  project_id?: string | null;
  session_id?: string | null;
  adapter_id: string;
  mode: "assist" | "autopilot" | "full_agent" | string;
  dry_run: boolean;
  llm_config?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  observations: AutopilotObservation[];
  steps: Array<{
    index: number;
    adapter_id: string;
    action: Record<string, unknown>;
    policy?: Record<string, unknown> | null;
    created_at: string;
  }>;
  pending_approval?: AutopilotApproval | null;
  plan?: AutopilotAgentPlan | null;
  working_state?: AutopilotWorkingState;
  final_message?: string | null;
  errors: string[];
  queued_user_messages?: string[];
  /** Parsed slash-command intent metadata carried from the create request. */
  composer_intent?: Record<string, unknown> | null;
};

/** One CAD parameter editable fast/deterministically via cad.edit_parameter. */
export type EditableParameter = {
  feature_id: string | null;
  feature_name: string;
  feature_type: string;
  /** local = one part (safe local edit); global = shared (edits ripple); unscoped. */
  scope: "local" | "global" | "unscoped" | string;
  parameter_name: string;
  cad_parameter_name: string;
  current_value: number | null;
  min_value: number | null;
  max_value: number | null;
};

export type EditableParametersResponse = {
  parameters: EditableParameter[];
  summary: { total: number; by_scope: { local: number; global: number; unscoped: number } };
};

/** Structured parametric edit proposal for review before mutating CAD (#432). */
export type ParametricEditProposalTarget = {
  feature_id: string;
  parameter_name: string;
  cad_parameter_name: string;
  feature_name?: string;
  feature_type?: string;
  pointer?: string;
};

export type ParametricEditProposalChange = {
  old_value: number | string | boolean | null;
  new_value: number | string | boolean | null;
  unit?: string;
  reason?: string;
};

export type ParametricEditProposalRisk = {
  protected_features: Array<{
    kind: string;
    message: string;
    [key: string]: unknown;
  }>;
  design_target_impacts: Array<{
    target_id?: string;
    label?: string;
    metric?: string;
    reason?: string;
  }>;
};

export type ParametricEditProposal = {
  status: "ok" | string;
  proposal_id: string;
  project_id: string;
  approval_required: boolean;
  target: ParametricEditProposalTarget;
  change: ParametricEditProposalChange;
  scope: "local" | "global" | "unscoped" | string;
  scope_risk?: { scope: string; reason: string; confirmation_field: string } | null;
  risks: ParametricEditProposalRisk;
  expected_impact: {
    mass: { status: string; [key: string]: unknown };
    stress: { status: string; [key: string]: unknown };
    design_targets: { affected_count: number; note: string };
    summary: string;
  };
  preview: Record<string, unknown>;
  next_action?: {
    tool: string;
    input: Record<string, unknown>;
    reason: string;
  };
  warnings?: string[];
};

export type CreateParametricEditProposalRequest = {
  featureId: string;
  parameterName: string;
  newValue: number | string | boolean;
  reason?: string;
  timeout?: number;
};

/** Geometry assembly-check report for the viewer overlay (from /geometry-report). */
export type GeometrySymmetryPair = {
  pair?: [string, string] | string[];
  ok?: boolean;
  status?: string;
  part?: string;
  expected_partner?: string;
  align_residual_mm?: number;
  mirror_axis?: string;
};

export type GeometryGap = {
  part?: string;
  nearest?: string;
  gap_mm?: number;
  status?: "touching" | "near" | "floating" | string;
};

export type GeometryReportResponse = {
  available: boolean;
  reason?: string;
  units?: string;
  floating_parts?: string[];
  symmetry?: GeometrySymmetryPair[];
  gaps?: GeometryGap[];
  /** Per-named-part bounding box [xmin, ymin, zmin, xmax, ymax, zmax] in mm. */
  part_boxes?: Record<string, number[]>;
};

/** A part that moved/resized in the topology regression diff (from a CAD edit). */
export type RegressionChangedPart = {
  part: string;
  max_change_mm?: number;
  /** false → this part changed but was NOT the edit's target (collateral). */
  expected?: boolean;
};

/** Topology drift verdict from a CAD edit (`regression_diff` in the tool output). */
export type RegressionDiff = {
  verdict: string; // clean | collateral_change | topology_changed | identical
  headline?: string;
  changed?: RegressionChangedPart[];
  added?: string[];
  removed?: string[];
  collateral_parts?: string[];
  unchanged_count?: number;
};

/** Manufacturability diff verdict from a CAD edit (`critique_diff`). */
export type CritiqueDiff = {
  verdict: string; // clean | warn | fail | improved | skipped
  headline?: string;
  delta?: { high?: number; medium?: number; low?: number };
  introduced?: Array<{ rule?: string; severity?: string; feature?: string; observation?: string }>;
  introduced_count?: number;
  resolved_count?: number;
};

/** Survival summary for one topology entity kind after a CAD edit (#311). */
export type EntitySurvivalSummary = {
  before_count: number;
  after_count: number;
  survived_count: number;
  added_count: number;
  removed_count: number;
  sample_added?: string[];
  sample_removed?: string[];
  referenced?: Array<{ id: string; status: string }>;
};

/** Topology / export survival evidence from a CAD edit (#309, #311). */
export type GeometryVerification = {
  topology_preserved?: boolean;
  stale_reference_risk?: boolean;
  topology_change?: {
    topology_changed: boolean;
    added_count?: number;
    removed_count?: number;
  } | null;
  face_edge_survival?: {
    solid?: EntitySurvivalSummary;
    face?: EntitySurvivalSummary;
    edge?: EntitySurvivalSummary;
  } | null;
  brep_validity?: {
    status: string;
    detail: string;
  } | null;
  export_sanity?: {
    step_exported: boolean;
    stl_exported: boolean;
    glb_exported: boolean;
    status: string;
    detail: string;
  } | null;
};

/** The most recent edit's diff (#226), from GET /api/projects/{id}/edit-diff. */
export type EditDiffResponse = {
  available: boolean;
  reason?: string;
  tool?: string;
  regression_diff?: RegressionDiff | null;
  critique_diff?: CritiqueDiff | null;
  geometry_verification?: GeometryVerification | null;
};

/** One deterministic engineering-critique finding (from cad.critique). */
export type CritiqueFinding = {
  severity: "high" | "medium" | "low" | string;
  category?: string;
  rule?: string;
  feature?: string;
  observation?: string;
  suggested_fix?: string;
};

/**
 * Shared V&V-40 credibility stamp (#218) emitted by every result-bearing tool
 * output. `tier` is one of critique_finding < surrogate_prediction <
 * proxy_assembly_result < executed_solver_result (or `unverified`); `rank`
 * orders them (higher = more credible, 0 = unverified).
 */
export type CredibilityStamp = {
  tier: string;
  rank: number;
  label?: string;
  evidence_basis?: string;
  production_ready?: boolean;
  tier_order?: string[];
  downgrade_reason?: string;
  signals?: Record<string, unknown>;
};

export type CritiqueResponse = {
  status?: string;
  findings?: CritiqueFinding[];
  summary?: { by_severity?: { high?: number; medium?: number; low?: number } };
  fail_first_objections?: string[];
  credibility?: CredibilityStamp;
};

/** One core simulation input's readiness (from build_simulation_readiness_report). */
export type SimulationReadinessInput = {
  status: "present" | "missing" | "defaultable" | "unknown" | string;
  detail?: string;
};

export type SimulationReadinessResponse = {
  ready_for_solver?: boolean;
  setup_source?: string;
  inputs?: Record<string, SimulationReadinessInput>;
  missing_required_inputs?: string[];
  defaultable_inputs?: string[];
  summary?: string;
};

/** One variant from a sizing sweep (from analysis/sizing_sweep_report.json). */
export type SizingSweepVariant = {
  value: number;
  metrics?: Record<string, number | null>;
  solver_executed?: boolean;
  status?: "feasible" | "infeasible" | "unknown" | "error" | string;
  reason?: string;
  rank?: number;
  objective_value?: number | null;
  credibility?: CredibilityStamp;
};

export type SizingSweepReport = {
  status?: string;
  tool?: string;
  project_id?: string;
  feature_id?: string;
  parameter_name?: string | null;
  objective?: string;
  objective_metric?: string;
  swept_values?: number[];
  constraint?: {
    stress_limit?: number | null;
    safety_factor?: number;
    allowable_stress?: number | null;
    displacement_limit?: number | null;
  };
  variants?: SizingSweepVariant[];
  variant_count?: number;
  feasible_count?: number;
  recommended?: SizingSweepVariant | null;
  recommendation_reason?: string;
  safe_to_apply?: boolean;
  next_step?: string;
  credibility?: CredibilityStamp;
  honesty?: Record<string, unknown>;
  artifact_path?: string;
};

export type SizingSweepReportResponse = {
  available: boolean;
  reason?: string;
  report?: SizingSweepReport;
  error?: string;
};

/** Per-metric convergence report from analysis/mesh_convergence_report.json. */
export type MeshConvergenceMetricReport = {
  metric?: string;
  levels?: Array<{ size: number; value: number; node_count?: number }>;
  level_count?: number;
  apparent_order?: number | null;
  extrapolated_value?: number | null;
  gci_fine_percent?: number | null;
  gci_coarse_percent?: number | null;
  asymptotic_range?: boolean | null;
  converged?: boolean | null;
  verdict?: string;
  message?: string;
  relative_change_finest_pair_percent?: number | null;
  refinement_ratio_finest_pair?: number | null;
  honesty?: Record<string, unknown>;
};

export type MeshConvergenceReport = {
  status?: string;
  tool?: string;
  project_id?: string;
  mesh_sizes?: number[];
  solved_count?: number;
  solves?: Array<{
    size: number;
    metrics?: Record<string, number | null>;
    solver_executed?: boolean;
    solve_status?: string;
    error?: string | null;
  }>;
  convergence?: Record<string, MeshConvergenceMetricReport>;
  verdicts?: Record<string, string | null>;
  overall_verdict?: string;
  next_step?: string;
  honesty?: Record<string, unknown>;
  artifact_path?: string;
};

export type MeshConvergenceReportResponse = {
  available: boolean;
  reason?: string;
  report?: MeshConvergenceReport;
  error?: string;
};

export type CaeSetupOverlayFace = {
  face_id: string;
  center_mm?: number[] | null;
  normal?: number[] | null;
  bounding_box_mm?: number[] | null;
  surface_type?: string | null;
  stale?: boolean;
};

export type CaeSetupOverlayLoad = {
  id?: string;
  type?: string;
  target_feature?: string;
  target_pointers?: string[];
  face_ids?: string[];
  faces?: CaeSetupOverlayFace[];
  value_n?: number | null;
  direction?: number[] | null;
  magnitude_n?: number | null;
};

export type CaeSetupOverlayConstraint = {
  id?: string;
  type?: string;
  target_feature?: string;
  target_pointers?: string[];
  face_ids?: string[];
  faces?: CaeSetupOverlayFace[];
};

export type CaeSetupOverlayResponse = {
  available: boolean;
  reason?: string;
  error?: string;
  project_id?: string;
  units?: string;
  setup_source?: string;
  loads?: CaeSetupOverlayLoad[];
  constraints?: CaeSetupOverlayConstraint[];
  cae_mapping?: Record<string, unknown>;
  stale_references?: Array<Record<string, unknown>>;
  topology_hash_status?: string;
  topology_stale?: boolean;
};

export type FieldRegionCluster = {
  id: string;
  location: { x: number; y: number; z: number };
  magnitude: { value: number; unit: string };
  node_count: number;
  feature_ref?: string | null;
};

export type FieldRegionsDocument = {
  schema_version: string;
  format_version: string;
  source_frd: string;
  field: string;
  metric: string;
  cluster_count: number;
  clusters: FieldRegionCluster[];
  warnings: string[];
  claim_policy?: {
    observational_only: boolean;
    physical_correctness_not_claimed: boolean;
    solver_execution_not_performed_by_aieng: boolean;
  };
};

export type ChatConnection = {
  id: "llm-api" | "local-agent" | string;
  label: string;
  transport: string;
  status: "ready" | "configurable" | "degraded" | "blocked" | string;
  detail: string;
  requires_project: boolean;
  supports_llm: boolean;
  supports_execution: boolean;
  approval_gated: boolean;
  tool_count: number;
  registry_count?: number;
  adapters?: LocalAgentCapability[];
};

export type ChatStep = {
  tool: string;
  description: string;
  status: string;
  inputs?: Record<string, unknown>;
  output?: Record<string, unknown> | null;
};

export type ArtifactResponse = {
  path: string;
  exists: boolean;
  media_type: string;
  size_bytes?: number | null;
  parsed_json?: unknown | null;
  text?: string | null;
  warnings: string[];
};

export type ArtifactDiffResponse = {
  changed_paths: string[];
  added_paths: string[];
  removed_paths: string[];
};

export type ArtifactDiff = {
  path: string;
  operation: string;
  json_pointer: string;
  before: unknown;
  after: unknown;
  changed_paths: string[];
  added_paths: string[];
  removed_paths: string[];
};

export type ChatResponse = {
  reply: string;
  plan: ChatStep[];
  executed: boolean;
  audit_id: string;
  audit_log_url?: string | null;
  errors?: string[];
  intent?: Record<string, unknown>;
  patch_json?: Record<string, unknown> | null;
};

// ---------------------------------------------------------------------------
// v0.14 / v0.15 / v0.16 FreeCAD adapter + inspection — read-only.
// ---------------------------------------------------------------------------

export type FreeCadInspectStatus = "completed" | "skipped" | "partial" | "error";
export type FreeCadPreflightStatus = "ready" | "partial" | "not_ready" | "unavailable";

export type StructuralAdapterPreflightStatus = "ready" | "partial" | "not_ready" | "unavailable" | "unknown";

export type StructuralAdapterCapability = {
  id: string;
  label: string;
  category: string;
  mutates_package: boolean;
  mutates_external_model: boolean;
  runs_external_process: boolean;
  expensive: boolean;
  requires_approval: boolean;
  input_artifacts: string[];
  output_artifacts: string[];
  stale_artifacts_on_success: string[];
  claim_advancement: "none";
};

export type StructuralAdapterCapabilityStatus = {
  capability_id: string;
  label: string;
  category: string;
  status: "ready" | "blocked";
  required_tools: Array<{
    name: string;
    available: boolean;
    path?: string | null;
    reason?: string | null;
  }>;
  missing_tools: string[];
  blocked_reason?: string | null;
  requires_approval: boolean;
  mutates_package: boolean;
  runs_external_process: boolean;
  claim_advancement: "none";
  estimated_outputs: string[];
  stale_artifacts_on_success: string[];
};

export type StructuralAdapterPreflightResponse = {
  schema_version?: string;
  adapter_id?: string;
  adapter_label?: string;
  preflight: {
    ok: boolean;
    status: StructuralAdapterPreflightStatus;
    missing_dependencies: string[];
    warnings: string[];
    errors: string[];
    estimated_outputs?: string[];
    requires_approval: boolean;
  };
  capabilities: StructuralAdapterCapability[];
  capability_status?: StructuralAdapterCapabilityStatus[];
  environment?: Record<string, unknown>;
  checked_paths?: Record<string, { path: string; present: boolean }>;
  safety_note: string;
  claim_boundary: string;
};

export type StructuralSolverInputImportResponse = {
  ok: boolean;
  package_path: string;
  run_id: string;
  artifact: {
    path: string;
    kind?: string;
    role?: string;
    size_bytes?: number;
  };
  keyword_count: number;
  keywords: string[];
  warnings: string[];
};

export type EngineeringTemplateParameter = {
  id: string;
  label: string;
  kind: "number" | "string" | "select" | "boolean";
  unit?: string | null;
  default?: number | string | boolean | null;
  min?: number | null;
  max?: number | null;
  required: boolean;
  description: string;
  choices?: string[];
};

export type EngineeringTemplateSummary = {
  id: string;
  label: string;
  description: string;
  category: "structural";
  parameter_count: number;
  outputs: {
    cad_script_preview: boolean;
    fea_setup_draft: boolean;
    design_target_suggestions: boolean;
  };
  safety_note: string;
  claim_advancement: "none";
};

export type EngineeringTemplateMaterial = {
  id: string;
  name: string;
  youngs_modulus_MPa: number;
  poisson_ratio: number;
  density_kg_m3: number;
  yield_stress_MPa?: number;
};

export type EngineeringTemplateDetail = EngineeringTemplateSummary & {
  schema_version: string;
  parameters: EngineeringTemplateParameter[];
  materials: EngineeringTemplateMaterial[];
  claim_boundary: string;
};

export type EngineeringTemplateValidationError = {
  code: string;
  message: string;
  field?: string | null;
};

export type EngineeringTemplateTargetSuggestion = {
  target_id: string;
  label: string;
  metric: string;
  operator: string;
  value: number;
  unit: string;
  priority: string;
  rationale: string;
};

export type EngineeringTemplatePreviewResponse = {
  ok: boolean;
  template_id: string;
  project_id: string;
  package_path?: string | null;
  parameters: Record<string, unknown>;
  errors: EngineeringTemplateValidationError[];
  warnings: string[];
  cad_script_preview?: string;
  fea_setup_draft?: Record<string, unknown>;
  design_target_suggestions?: EngineeringTemplateTargetSuggestion[];
  claim_advancement: "none";
  claim_boundary: string;
  safety_note: string;
};

export type EngineeringTemplateSaveDraftResponse = EngineeringTemplatePreviewResponse & {
  artifacts?: Array<{ path: string; kind?: string; role?: string }>;
  draft_paths?: string[];
};

export type EngineeringTemplateAdoptTargetsResponse = {
  ok: boolean;
  template_id: string;
  project_id: string;
  artifact_path?: string | null;
  document?: DesignTargetsDocument | null;
  targets?: DesignTarget[];
  adopted_count: number;
  skipped_duplicate_ids: string[];
  errors: EngineeringTemplateValidationError[];
  warnings: string[];
  safety_note?: string;
  claim_advancement: "none";
  claim_boundary: string;
};

export type EngineeringTemplateCadFixtureResponse = {
  ok: boolean;
  template_id: string;
  project_id: string;
  status: "waiting_for_approval" | "completed" | "error";
  requires_approval: boolean;
  artifact?: { path: string; kind?: string; role?: string };
  artifact_path?: string | null;
  revalidation_status_path?: string | null;
  fixture?: Record<string, unknown>;
  stale_artifacts?: string[];
  cad_execution_performed: false;
  external_tool_execution_performed: false;
  real_cad_file?: false;
  errors: EngineeringTemplateValidationError[];
  warnings: string[];
  safety_note?: string;
  claim_advancement: "none";
  claim_boundary: string;
};

export type ReviewSupportPacketSectionStatus = "included" | "missing" | "partial" | "error";

export type ReviewSupportPacketSection = {
  id: string;
  title: string;
  status: ReviewSupportPacketSectionStatus;
  warnings: string[];
  artifact_paths: string[];
};

export type ReviewSupportPacketResponse = {
  ok: boolean;
  packet_id: string;
  markdown_path?: string | null;
  manifest_path?: string | null;
  preview_markdown?: string | null;
  sections: ReviewSupportPacketSection[];
  warnings: string[];
  errors: string[];
  claim_advancement: "none";
  claim_boundary: string;
};

export type StructuralPreparePreviewResponse = {
  ok: boolean;
  tool: "structural.prepare_solver_run";
  status: "completed" | "error";
  code?: string;
  message?: string;
  project_id?: string;
  package_path?: string;
  solver?: string;
  run_id?: string;
  load_case_id?: string;
  requires_approval?: boolean;
  solver_execution_performed?: boolean;
  ready_to_run?: boolean;
  input_deck_artifact?: string;
  preflight?: {
    has_mesh: boolean;
    has_solver_settings: boolean;
    has_load_case: boolean;
    has_input_deck: boolean;
    ccx_available: boolean;
    missing_items: string[];
  };
  planned_artifacts?: Array<{ path: string; kind?: string; role?: string }>;
  warnings: string[];
  errors?: string[];
  safety_note?: string;
  claim_advancement: "none";
  claim_boundary: string;
};

export type FreeCadAdapterCapability = {
  id: string;
  label: string;
  category: string;
  mutates_package: boolean;
  mutates_external_model: boolean;
  runs_external_process: boolean;
  expensive: boolean;
  requires_approval: boolean;
  input_artifacts: string[];
  output_artifacts: string[];
  stale_artifacts_on_success: string[];
  claim_advancement: "none";
};

export type FreeCadAdapterPreflightResponse = {
  schema_version?: string;
  adapter_id: string;
  adapter_label: string;
  preflight: {
    ok: boolean;
    status: FreeCadPreflightStatus;
    missing_dependencies: string[];
    warnings: string[];
    errors: string[];
    estimated_outputs: string[];
    requires_approval: boolean;
  };
  capabilities: FreeCadAdapterCapability[];
  environment?: Record<string, unknown>;
  checked_paths?: Record<string, { path: string; present: boolean }>;
  safety_note: string;
  claim_boundary: string;
};

export type FreeCadInspectFeaturesResponse = {
  schema_version?: string;
  tool?: string;
  status: FreeCadInspectStatus;
  preflight_status: FreeCadPreflightStatus;
  ok: boolean;
  reason?: string | null;
  bridge?: string | null;
  feature_count?: number;
  editable_parameter_count?: number;
  evidence_written: string[];
  changed_artifacts: string[];
  missing_dependencies: string[];
  warnings: string[];
  errors: string[];
  claim_advancement: "none";
  requires_approval?: boolean;
  safety_note?: string;
  claim_boundary: string;
  source_path?: string | null;
  parsed_features?: Record<string, unknown> | null;
  feature_graph?: Record<string, unknown> | null;
};

export type FreeCadInspectFeaturesRequest = {
  source_path?: string;
  write_evidence?: boolean;
};

export type FreeCadInspectionEvidenceStatus = "available" | "missing" | "invalid" | "partial";

export type FreeCadInspectionEvidenceResponse = {
  exists: boolean;
  status: FreeCadInspectionEvidenceStatus;
  feature_count?: number | null;
  editable_parameter_count?: number | null;
  bridge?: string | null;
  source?: string | null;
  generated_at?: string | null;
  evidence_artifacts: string[];
  warnings: string[];
  errors: string[];
  claim_advancement: "none";
  claim_boundary: string;
  feature_graph?: Record<string, unknown> | null;
};

export type FreeCadEditParameterRequest = {
  feature_id: string;
  parameter_name: string;
  new_value: unknown;
  approved: boolean;
  input_fcstd?: string | null;
};

export type FreeCadEditParameterResponse = {
  ok: boolean;
  tool: "cad.edit_parameter";
  status: "completed" | "partial" | "error" | "rejected";
  code?: string;
  message?: string;
  package_path?: string;
  feature_id?: string;
  parameter_name?: string;
  new_value?: unknown;
  freecad_object_name?: string;
  freecad_parameter_name?: string;
  package_geometry_path?: string | null;
  stale_artifacts?: string[];
  revalidation_required?: boolean;
  warnings?: string[];
  errors?: string[];
  artifacts?: Array<{ path: string; kind?: string; role?: string }>;
  source?: string;
  claim_advancement: "none";
  claim_boundary?: string;
};
