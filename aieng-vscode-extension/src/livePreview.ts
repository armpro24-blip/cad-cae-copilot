import * as vscode from "vscode";

export type Project = {
  id: string;
  name?: string;
  status?: string;
  updated_at?: string;
  named_parts?: string[];
};

export function backendUrl(): string {
  return vscode.workspace.getConfiguration("aieng").get<string>("backendUrl", "http://127.0.0.1:8000").replace(/\/+$/, "");
}

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function responseError(response: Response): Promise<Error> {
  const text = await response.text().catch(() => "");
  return new HttpError(response.status, text || `${response.status} ${response.statusText}`);
}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${backendUrl()}${path}`, init);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function listProjects(): Promise<Project[]> {
  const raw = await fetchJson("/api/projects");
  if (Array.isArray(raw)) return raw as Project[];
  if (raw && typeof raw === "object" && Array.isArray((raw as { projects?: unknown }).projects)) {
    return (raw as { projects: Project[] }).projects;
  }
  return [];
}

export async function createProject(name = "Untitled project"): Promise<Project> {
  return await fetchJson("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  }) as Project;
}

/**
 * Fetch the project's advisory next actions via the READ-ONLY `cae.prepare_solver_run`
 * preflight (#341). This tool executes nothing — it only reports solver readiness —
 * so calling it to surface advisory `next_actions` does not run CAD/CAE/solver work.
 */
export async function fetchAdvisoryNextActions(projectId: string): Promise<unknown> {
  return await fetchJson("/api/agent/invoke-tool", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool: "cae.prepare_solver_run", input: { project_id: projectId } }),
  });
}

export async function chooseLiveProject(): Promise<Project | undefined> {
  const configured = vscode.workspace.getConfiguration("aieng").get<string>("liveProjectId", "").trim();
  const projects = await listProjects();
  if (configured) return projects.find((item) => item.id === configured) ?? { id: configured, name: configured };
  const selected = await vscode.window.showQuickPick(
    projects.map((project) => ({
      label: project.name || project.id,
      description: project.status,
      detail: [project.id, ...(project.named_parts ?? [])].join(" - "),
      project,
    })),
    { placeHolder: "Select an AIENG Workbench project to preview" },
  );
  return selected?.project;
}
