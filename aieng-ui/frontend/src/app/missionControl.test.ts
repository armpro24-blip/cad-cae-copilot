import { describe, expect, it } from "vitest";

import { buildMissionControl } from "./missionControl";
import type { PendingApproval } from "./pendingApprovals";
import type { ProjectTimeline } from "./projectTimeline";
import type { ProjectRecord, ProjectSummary } from "../types";

const project: ProjectRecord = {
  id: "p1",
  name: "Bracket",
  status: "ready",
  created_at: "2026-06-26T00:00:00Z",
  updated_at: "2026-06-26T00:00:00Z",
  aieng_file: "/tmp/bracket.aieng",
  web_asset: "/assets/bracket.glb",
  web_asset_format: "glb",
};

function summary(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    project,
    members: [
      "manifest.json",
      "geometry/topology_map.json",
      "graph/feature_graph.json",
      "provenance/tool_trace.json",
    ],
    manifest: { name: "Bracket" },
    feature_graph: { features: [] },
    topology: { faces: [] },
    validation: null,
    viewer: null,
    ai_summary: null,
    derived: {},
    ...overrides,
  };
}

function emptyTimeline(overrides: Partial<ProjectTimeline> = {}): ProjectTimeline {
  return {
    entries: [],
    runCount: 0,
    activityCount: 0,
    warningCount: 0,
    diagnosticCount: 0,
    snapshotCount: 0,
    unstructuredFailureCount: 0,
    ...overrides,
  };
}

function build(overrides: Partial<Parameters<typeof buildMissionControl>[0]> = {}) {
  return buildMissionControl({
    selectedProject: project,
    summary: summary(),
    pendingApprovals: [],
    projectTimeline: emptyTimeline(),
    simulationReadiness: null,
    meshDiagnostics: null,
    meshConvergenceReport: null,
    ...overrides,
  });
}

describe("buildMissionControl", () => {
  it("surfaces empty project state without claiming evidence", () => {
    const model = buildMissionControl({
      selectedProject: null,
      summary: null,
      pendingApprovals: [],
      projectTimeline: null,
      simulationReadiness: null,
      meshDiagnostics: null,
      meshConvergenceReport: null,
    });

    expect(model.packageStatus).toBe("missing");
    expect(model.headline).toContain(".aieng");
    expect(model.nextAction.label).toBe("Create or open a project");
    expect(model.evidenceNotes.join(" ")).toContain("No result evidence");
  });

  it("treats a CAD-only package as package/CAD evidence but missing CAE setup", () => {
    const model = build();

    expect(model.packageName).toBe("bracket.aieng");
    expect(model.cards.find((card) => card.key === "package")?.status).toBe("ready");
    expect(model.cards.find((card) => card.key === "cad")?.status).toBe("ready");
    expect(model.cards.find((card) => card.key === "cae")?.status).toBe("missing");
    expect(model.packageIdentity.find((item) => item.key === "geometry")?.members).toEqual([
      "geometry/topology_map.json",
      "graph/feature_graph.json",
      "manifest.json",
    ]);
    expect(model.packageIdentity.find((item) => item.key === "provenance")?.status).toBe("ready");
    expect(model.packageIdentity.find((item) => item.key === "claims")?.status).toBe("unknown");
    expect(model.nextAction.label).toBe("Define CAE setup");
    expect(model.nextAction.draft).toContain("Do not run the solver");
    expect(model.trustBadges.map((badge) => badge.label)).toEqual([
      "Draft / setup",
      "Results unknown",
      "Claim not advanced",
    ]);
    expect(model.workflowSteps.map((step) => [step.key, step.status])).toEqual([
      ["package", "ready"],
      ["geometry", "ready"],
      ["cae_setup", "missing"],
      ["mesh", "unknown"],
      ["solver", "missing"],
      ["report", "missing"],
    ]);
  });

  it("blocks on missing required CAE inputs", () => {
    const model = build({
      summary: summary({
        members: ["manifest.json", "task/design_targets.yaml"],
        cae: {
          present: true,
          constraints_count: 0,
          constraint_types: {},
          materials_count: 0,
          boundary_conditions_count: 0,
          loads_count: 0,
          evidence_count: 0,
          result_evidence_count: 0,
          results_available: false,
          available_fields: [],
          simulation_targets: [],
          protected_regions: [],
          materials: [],
          boundary_conditions: [],
          loads: [],
          evidence: [],
        },
      }),
      simulationReadiness: {
        setup_source: "package",
        ready_for_solver: false,
        missing_required_inputs: ["material", "loads"],
      },
    });

    expect(model.cards.find((card) => card.key === "cae")?.status).toBe("blocked");
    expect(model.nextAction.label).toBe("Fill required CAE inputs");
    expect(model.nextAction.detail).toContain("material");
    expect(model.cards.find((card) => card.key === "cae")?.meta).toBe("design targets present");
    expect(model.workflowSteps.find((step) => step.key === "cae_setup")?.status).toBe("blocked");
    expect(model.workflowSteps.find((step) => step.key === "cae_setup")?.draft).toContain("approval-gated");
  });

  it("shows approval gates as the next safest action", () => {
    const approval: PendingApproval = {
      permissionId: "perm1",
      toolName: "cae.run_solver",
      projectId: "p1",
      explanation: "Run CalculiX",
      codePreview: null,
    };
    const model = build({ pendingApprovals: [approval] });

    expect(model.cards.find((card) => card.key === "approval")?.status).toBe("blocked");
    expect(model.nextAction.label).toBe("Review pending approval");
    expect(model.nextAction.draft).toBeNull();
    expect(model.workflowSteps.find((step) => step.key === "solver")?.status).toBe("blocked");
    expect(model.trustBadges.find((badge) => badge.kind === "approval")?.label).toBe("Approval blocked");
  });

  it("does not present solver result evidence as claim advancement", () => {
    const model = build({
      summary: summary({
        members: [
          "manifest.json",
          "results/evidence_index.json",
          "results/result_summary.json",
          "results/computed_metrics.json",
          "ai/claim_map.json",
          "ai/summary.md",
        ],
        cae: {
          present: true,
          constraints_count: 1,
          constraint_types: {},
          materials_count: 1,
          boundary_conditions_count: 1,
          loads_count: 1,
          evidence_count: 1,
          result_evidence_count: 1,
          results_available: true,
          available_fields: ["von_mises"],
          simulation_targets: [],
          protected_regions: [],
          materials: [],
          boundary_conditions: [],
          loads: [],
          evidence: [],
          result_summary: {
            schema_version: "0.1",
            summary_type: "cae_result_summary",
            source: { package_path: "/tmp/bracket.aieng", solver: "CalculiX", software: null, source_files: [] },
            status: {
              mode: "cae_result",
              has_cae_setup: true,
              has_mesh: true,
              has_results: true,
              has_fields: false,
              has_validation: false,
              warnings: [],
            },
            artifacts: {
              mesh_files: [],
              field_files: [],
              result_summary_files: ["results/result_summary.json"],
              evidence_files: ["results/evidence_index.json"],
              validation_files: [],
              setup_files: [],
            },
            solver_settings: null,
            load_cases: [],
            field_metadata: null,
            computed_values: {
              extrema_computed: true,
              max_displacement: null,
              max_von_mises_stress: null,
              minimum_safety_factor: null,
            },
            llm_summary: {
              one_line: "",
              key_findings: [],
              risks: [],
              recommended_next_actions: [],
              limitations: [],
            },
          },
        },
      }),
    });

    expect(model.cards.find((card) => card.key === "results")?.status).toBe("ready");
    expect(model.cards.find((card) => card.key === "results")?.meta).toBe("claims not auto-advanced");
    expect(model.nextAction.draft).toContain("Do not advance claims automatically");
    expect(model.workflowSteps.find((step) => step.key === "solver")?.status).toBe("ready");
    expect(model.workflowSteps.find((step) => step.key === "report")?.draft).toContain("Do not advance claims automatically");
    expect(model.trustBadges.map((badge) => badge.label)).toContain("Computed metrics");
    expect(model.trustBadges.map((badge) => badge.label)).toContain("Result summary");
    expect(model.trustBadges.find((badge) => badge.kind === "claim_boundary")?.detail).toContain("does not advance");
    expect(model.packageIdentity.find((item) => item.key === "results")?.status).toBe("ready");
    expect(model.packageIdentity.find((item) => item.key === "claims")?.members).toEqual(["ai/claim_map.json"]);
    expect(model.packageIdentity.find((item) => item.key === "handoff")?.members).toEqual(["ai/summary.md"]);
  });

  it("surfaces stale evidence only when revalidation is explicitly required", () => {
    const model = build({
      summary: summary({
        derived: {
          revalidation_status: {
            requires_revalidation: true,
          },
        },
      }),
    });

    expect(model.trustBadges.find((badge) => badge.kind === "stale")?.label).toBe("Needs rerun");
  });

  it("blocks the mesh workflow step on failing mesh diagnostics", () => {
    const model = build({
      summary: summary({
        members: ["manifest.json", "simulation/mesh/mesh.inp"],
        cae: {
          present: true,
          constraints_count: 1,
          constraint_types: {},
          materials_count: 1,
          boundary_conditions_count: 1,
          loads_count: 1,
          evidence_count: 1,
          result_evidence_count: 0,
          results_available: false,
          available_fields: [],
          simulation_targets: [],
          protected_regions: [],
          materials: [],
          boundary_conditions: [],
          loads: [],
          evidence: [],
        },
      }),
      meshDiagnostics: { available: true, overall_verdict: "fail" },
    });

    const mesh = model.workflowSteps.find((step) => step.key === "mesh");
    expect(mesh?.status).toBe("blocked");
    expect(mesh?.detail).toContain("fail");
    expect(mesh?.draft).toContain("Do not claim solver results");
  });

  it("derives a plain-language lifecycle checklist from evidence signals", () => {
    // CAD-only package: model present, everything downstream not done yet.
    const model = build();
    const byKey = Object.fromEntries(model.lifecycle.map((item) => [item.key, item]));

    expect(model.lifecycle.map((item) => item.key)).toEqual(["model", "setup", "result", "metrics", "report"]);
    expect(byKey.model.status).toBe("ready");
    expect(byKey.setup.status).toBe("missing");
    expect(byKey.setup.detail).toMatch(/material, loads, and constraints/);
    expect(byKey.result.status).toBe("unknown");
    expect(byKey.metrics.status).toBe("unknown");
    expect(byKey.report.status).toBe("unknown");
    // Consequence-worded, never a raw file path.
    model.lifecycle.forEach((item) => expect(item.detail).not.toMatch(/\.(yaml|json|inp|frd)/));
  });

  it("never reports the engineering report as 'done' (it is generated on demand)", () => {
    // Even with result evidence, the report item stays actionable, not 'ready'.
    const model = build({
      summary: summary({
        members: ["manifest.json", "results/result_summary.json", "results/computed_metrics.json"],
        cae: {
          present: true,
          constraints_count: 1,
          constraint_types: {},
          materials_count: 1,
          boundary_conditions_count: 1,
          loads_count: 1,
          evidence_count: 1,
          result_evidence_count: 1,
          results_available: true,
          available_fields: ["von_mises"],
          simulation_targets: [],
          protected_regions: [],
          materials: [],
          boundary_conditions: [],
          loads: [],
          evidence: [],
        },
      }),
    });
    const report = model.lifecycle.find((item) => item.key === "report");
    expect(report?.status).not.toBe("ready");
    // With results present, the primary action is the real Generate report path.
    expect(model.primaryAction.kind).toBe("report");
    expect(model.primaryAction.label).toBe("Generate report");
  });

  it("tags the primary action as a handoff draft when the agent must act", () => {
    const model = build(); // CAD-only → define CAE setup via the agent
    expect(model.primaryAction.kind).toBe("draft");
    expect(model.primaryAction.draft).toBeTruthy();
    expect(model.primaryAction.label).toBe("Define CAE setup");
  });

  it("tags the primary action 'none' on an empty project", () => {
    const model = buildMissionControl({
      selectedProject: null,
      summary: null,
      pendingApprovals: [],
      projectTimeline: null,
      simulationReadiness: null,
      meshDiagnostics: null,
      meshConvergenceReport: null,
    });
    expect(model.primaryAction.kind).toBe("none");
  });
});
