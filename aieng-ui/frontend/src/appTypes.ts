import type { ComposerIntentMetadata } from "./components/chat/composerIntent";
import type { ArtifactDiff, AutopilotRunState, ChatResponse } from "./types";

export type StageState = "idle" | "active" | "done" | "error";

export type StageItem = {
  key: string;
  label: string;
  detail: string;
  state: StageState;
};

export type Notice = {
  tone: "success" | "error" | "info";
  title: string;
  detail: string;
  /** When set, a drafted `/command` rendered as a one-click copy chip (Read+Handoff). */
  command?: string;
};

export type ChatHistoryItem = {
  id: string;
  role: "user" | "assistant";
  body: string;
  createdAt: string;
  mode?: "plan" | "execute" | "runtime";
  plan?: ChatResponse["plan"];
  errors?: string[];
  auditLogUrl?: string | null;
  artifactPaths?: string[];
  artifactDiffs?: ArtifactDiff[];
  autopilotRun?: AutopilotRunState;
  /** Parsed slash-command intent metadata, persisted in the message `extra`. */
  composerIntent?: ComposerIntentMetadata;
  advisoryItems?: string[];
  cadResult?: { face_count: number; feature_count: number; code: string };
  simulationResult?: {
    status: string;
    returncode?: number;
    von_mises_max_mpa?: number | null;
    displacement_max_mm?: number | null;
    node_count?: number;
    mesh_size_mm?: number;
    written_artifacts?: string[];
    warnings?: string[];
    missing_tools?: string[];
    message?: string;
    diagnosis?: string[];
    verdict?: {
      overall: "pass" | "fail" | "partial" | "no_targets" | "unknown";
      pass_count: number;
      fail_count: number;
      items: Array<{
        target_id: string;
        label: string;
        metric: string;
        status: "pass" | "fail" | "unknown" | "not_evaluated";
        actual_value: number | null;
        threshold: number | null;
        operator: string;
        unit: string;
      }>;
      suggestions: string[];
      fos_advisory?: string[];
      fos?: {
        fos: number | null;
        yield_strength_mpa: number | null;
        rating: "safe" | "marginal" | "critical" | "unknown";
      };
    };
  };
  targetResult?: {
    action: "added" | "updated";
    label: string;
    metric: string;
    operator: string;
    value: number;
    unit: string;
    total_targets: number;
  };
  preprocessResult?: {
    material: string;
    bc_count: number;
    load_count: number;
    mesh_size_mm: number;
    written_artifacts: string[];
    warnings: string[];
  };
};

export type ViewerLoadState = "idle" | "loading" | "ready" | "error";

export type PickedFace = {
  pointer: string;
  label: string;
  surface_type: string;
  roles: string[];
};

export type PickedEdge = {
  pointer: string;
  label: string;
  curve_type?: string;
  roles: string[];
};

export type AgentTurnStatus =
  | "draft"
  | "planning"
  | "planned"
  | "awaiting_approval"
  | "running"
  | "completed"
  | "failed"
  | "rejected";

export type SelectedGeometryContext = {
  pointers: string[];
  faces: PickedFace[];
  highlightedFaceIds: string[];
};

export type AgentTurn = {
  id: string;
  userMessage: string;
  createdAt: string;
  status: AgentTurnStatus;
  projectId: string | null;
  selectedGeometry?: SelectedGeometryContext;
  plan?: ChatHistoryItem["plan"];
  runId?: string;
  summary?: string;
  errors?: string[];
};

export type BrepFaceEntity = {
  id: string;
  pointer?: string;
  surface_type?: string;
  area_mm2?: number | null;
  radius_mm?: number | null;
  normal?: number[] | null;
  center?: number[] | null;
  bounding_box?: number[];
  roles?: string[];
  // Owning solid id (e.g. "body_007"). Lets the viewer scope primitive↔face
  // matching to a single part instead of guessing against every face globally.
  body_id?: string | null;
};

export type BrepSelectionGroup = {
  id: string;
  label?: string;
  members: string[];      // face_id list
  role?: string;
  source?: string;
};

// Streaming state for the CAD generation flow. Replaces the previous flat
// `{step, message}` snapshot — the panel needs to render *all* steps the
// stream has visited so the user can scrub back through the lineage even
// after a later step has become active.

export type CadStageId =
  | "planning"
  | "coding"
  | "building"
  | "retrying"
  | "writing"
  | "done";

export type CadStageStatus = "pending" | "active" | "completed" | "failed";

export type CadStageState = {
  id: CadStageId;
  label: string;
  status: CadStageStatus;
  message?: string;
  elapsedS?: number;
  attempt?: number;
};

export type CadGenerationProgress = {
  stages: CadStageState[];
  activeStage: CadStageId | null;
  codePreview: string | null;
  errorPreview: string | null;
  fatalError: string | null;
};

export type BrepGraphSnapshot = {
  faces: Record<string, BrepFaceEntity>;
  groups: Record<string, BrepSelectionGroup>;
  // feature_id -> list of face_ids (for @feature: pointer expansion)
  featureFaces: Record<string, string[]>;
};

// Shape IR object registry (registry/object_registry.json): maps a Shape IR node
// to its generated artifacts and viewer-selectable entities.
export type ShapeIrObject = {
  node_id: string;
  source_pointer?: string;
  node_type?: string;
  runtime?: string | null;
  backend?: string | null;
  representation_kind?: string;
  capability_level?: string | null;
  lossiness?: string | null;
  cad_editable?: boolean | null;
  editable_parameters?: Record<string, unknown>;
  topology_entities?: string[];
  viewer_selectable_ids?: string[];
  mesh_entities?: string[];
  linkage?: string;
  verification_status_ref?: string;
};

export type ShapeIrObjectRegistry = {
  representation?: string;
  representation_kind?: string;
  runtime?: string | null;
  backend?: string | null;
  executed?: boolean;
  fallback?: boolean;
  geometry_kind?: string;
  node_count?: number;
  resolved_count?: number;
  objects: ShapeIrObject[];
  notes?: string[];
};

export type ShapeIrVerification = {
  status?: string;
  representation_kind?: string;
  lossiness?: string;
  cad_editable?: boolean;
  capability_level?: string;
  warnings?: string[];
  nodes?: Array<Record<string, unknown>>;
};

export type ObjectRegistryResponse = {
  object_registry: ShapeIrObjectRegistry;
  verification: ShapeIrVerification | null;
};
