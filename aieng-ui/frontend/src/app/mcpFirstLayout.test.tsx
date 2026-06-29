/**
 * @vitest-environment happy-dom
 */
import { test, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { api } from "../api";
import { AppChrome } from "./AppChrome";

// Source-level assertions for MCP-first architecture constraints
import appChrome from "./AppChrome.tsx?raw";
import workbenchApp from "./useWorkbenchApp.ts?raw";
import sessionsSidebar from "../components/SessionsSidebar.tsx?raw";

// Mock child components so the layout test focuses on embed-mode behaviour,
// not the internals of heavy subtrees (ViewerPane pulls Three.js, etc.)
vi.mock("../components/common", () => ({ NoticeCenter: () => null }));
vi.mock("../components/PointerText", () => ({
  PointerProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("../components/PendingApprovals", () => ({ PendingApprovals: () => null }));
vi.mock("../components/SessionsSidebar", () => ({
  SessionsSidebar: () => <aside data-testid="sessions-sidebar">Sidebar</aside>,
}));
vi.mock("../components/ViewerPane", () => ({
  ViewerPane: () => <div data-testid="viewer-pane">Viewer</div>,
}));
vi.mock("../components/MaterialLibraryPanel", () => ({
  MaterialLibraryPanel: () => null,
}));
vi.mock("../components/StandardPartsPanel", () => ({
  StandardPartsPanel: () => null,
}));
vi.mock("../components/BOMPanel", () => ({
  BOMPanel: () => null,
}));
vi.mock("../components/OptimizationPanel", () => ({
  OptimizationPanel: () => null,
}));
vi.mock("../components/settings/GlobalSettingsDrawer", () => ({
  GlobalSettingsDrawer: () => null,
}));
vi.mock("../components/settings/RuntimeSettingsDrawer", () => ({
  RuntimeSettingsDrawer: () => null,
}));

function createMockApp(overrides: Partial<Parameters<typeof AppChrome>[0]["app"]> = {}): Parameters<typeof AppChrome>[0]["app"] {
  return {
    pointerContextValue: {},
    notice: null,
    runtimeNotice: null,
    setNotice: vi.fn(),
    setRuntimeNotice: vi.fn(),
    selectedProject: null,
    sidebarCollapsed: false,
    setSidebarCollapsed: vi.fn(),
    setSettingsOpen: vi.fn(),
    projectName: "Test",
    setProjectName: vi.fn(),
    busy: false,
    selectedFile: null,
    setSelectedFile: vi.fn(),
    selectedId: null,
    projects: [],
    stages: [],
    runBusyTask: vi.fn(),
    refreshProjects: vi.fn(),
    runWorkbenchImportFlow: vi.fn(),
    refreshGeometry: vi.fn(),
    runtimeReady: false,
    runtimeProvider: null,
    liveSyncStatus: "",
    liveSyncDetail: null,
    liveSyncLastEventAt: null,
    pendingApprovals: [],
    resolveApproval: vi.fn(),
    effectiveViewerFormat: null,
    activeFieldDescriptor: null,
    effectiveViewerUrl: null,
    pickedFaces: [],
    addPickedFace: vi.fn(),
    clearPickedFaces: vi.fn(),
    copyPointerText: vi.fn(),
    cadGenerationProgress: null,
    highlightedFaceIds: new Set<string>(),
    brepSnapshot: null,
    clearHighlightedFaces: vi.fn(),
    settingsOpen: false,
    runtime: null,
    runtimeDraft: null,
    runtimeBusy: false,
    llmConfig: null,
    llmReady: false,
    apiKey: "",
    apiKeyHydrated: false,
    updateApiKey: vi.fn(),
    updateRuntimeDraft: vi.fn(),
    updateLlmConfig: vi.fn(),
    applyLlmProviderPreset: vi.fn(),
    restoreDefaultLlmConfig: vi.fn(),
    handleLlmTestResult: vi.fn(),
    runRuntimeTask: vi.fn(),
    restoreRuntimeDefaults: vi.fn(),
    localAgentConfig: {},
    setLocalAgentConfig: vi.fn(),
    globalSettingsOpen: false,
    setGlobalSettingsOpen: vi.fn(),
    optimizationStudy: null,
    optimizationConvergence: null,
    ...overrides,
  } as unknown as Parameters<typeof AppChrome>[0]["app"];
}

test("active workbench shell is MCP-first and does not render embedded chat", () => {
  expect(!appChrome.includes("ChatPanel")).toBe(true);
  expect(!appChrome.includes("chat-pane")).toBe(true);
  expect(!workbenchApp.includes("useAgentRuns")).toBe(true);
  expect(!workbenchApp.includes("DEFAULT_CHAT_CONNECTIONS")).toBe(true);
  expect(!sessionsSidebar.includes("chatSessions")).toBe(true);
});

test("embedded VS Code workbench omits the project sidebar", () => {
  // Embed mode: sidebar absent, main area gets embed-main class
  window.history.replaceState({}, "", "/?embed=vscode");
  const { container: embedContainer } = render(<AppChrome app={createMockApp()} />);
  expect(embedContainer.querySelector('[data-testid="sessions-sidebar"]')).toBeNull();
  const embedMain = embedContainer.querySelector(".app-main");
  expect(embedMain?.classList.contains("embed-main")).toBe(true);

  // Normal mode: sidebar present, main area does NOT get embed-main class
  window.history.replaceState({}, "", "/");
  const { container: normalContainer } = render(<AppChrome app={createMockApp()} />);
  expect(normalContainer.querySelector('[data-testid="sessions-sidebar"]')).not.toBeNull();
  const normalMain = normalContainer.querySelector(".app-main");
  expect(normalMain?.classList.contains("embed-main")).toBe(false);
});

test("topbar report button opens the read-only engineering report", () => {
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  const { container } = render(<AppChrome app={createMockApp({
    selectedId: "project 42",
    selectedProject: {
      id: "project 42",
      name: "Bracket",
      status: "ready",
      created_at: "2026-06-25T10:00:00Z",
      updated_at: "2026-06-25T10:00:00Z",
    },
  })} />);

  const button = container.querySelector('button[title^="Report"]') as HTMLButtonElement | null;
  expect(button).not.toBeNull();
  expect(button?.disabled).toBe(false);
  fireEvent.click(button!);
  expect(open).toHaveBeenCalledWith("/api/projects/project%2042/report", "_blank", "noopener,noreferrer");
});

test("normal workbench topbar summarizes package, approvals, and next action", () => {
  const { container } = render(<AppChrome app={createMockApp({
    selectedId: "project 42",
    selectedProject: {
      id: "project 42",
      name: "Bracket",
      status: "ready",
      created_at: "2026-06-25T10:00:00Z",
      updated_at: "2026-06-25T10:00:00Z",
    },
    pendingApprovals: [
      {
        permissionId: "perm-1",
        toolName: "run_solver",
        projectId: "project 42",
        explanation: "Needs review",
        codePreview: null,
      },
    ],
  })} />);

  const context = container.querySelector('[aria-label="Workbench project status"]');
  expect(context).not.toBeNull();
  expect(context?.textContent).toContain("Package");
  expect(context?.textContent).toContain("Approvals");
  expect(context?.textContent).toContain("1 waiting");
  expect(context?.textContent).toContain("Next");
});

test("topbar packet button exports a review support packet", async () => {
  const exportSpy = vi.spyOn(api, "exportReviewSupportPacket").mockResolvedValue({
    ok: true,
    packet_id: "packet_001",
    markdown_path: "reports/review_support/packet_001.md",
    manifest_path: "reports/review_support/packet_001.json",
    preview_markdown: null,
    sections: [],
    warnings: [],
    errors: [],
    claim_advancement: "none",
    claim_boundary: "review support only",
  });
  const setNotice = vi.fn();
  const refreshGeometry = vi.fn();
  const { container } = render(<AppChrome app={createMockApp({
    selectedId: "project 42",
    selectedProject: {
      id: "project 42",
      name: "Bracket",
      status: "ready",
      created_at: "2026-06-25T10:00:00Z",
      updated_at: "2026-06-25T10:00:00Z",
    },
    setNotice,
    refreshGeometry,
  })} />);

  const button = container.querySelector('button[title^="Evidence packet"]') as HTMLButtonElement | null;
  expect(button).not.toBeNull();
  expect(button?.disabled).toBe(false);
  fireEvent.click(button!);

  await waitFor(() => {
    expect(exportSpy).toHaveBeenCalledWith("project 42", { include_preview_markdown: true });
  });
  expect(setNotice).toHaveBeenCalledWith(expect.objectContaining({
    tone: "success",
    title: "Review packet exported",
  }));
  expect(refreshGeometry).toHaveBeenCalled();
});

beforeEach(() => {
  // Reset URL between tests so embed state is deterministic
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
});
