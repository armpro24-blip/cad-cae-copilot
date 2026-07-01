import { useState } from "react";
import { ChevronsLeft, ChevronsRight, Folder, Plus, Trash2 } from "lucide-react";

import { api } from "../api";
import { projectStatusInfo, formatRelativeTime } from "../app/projectStatus";
import { ConfirmDialog } from "./common";
import type { Notice, StageItem } from "../appTypes";
import type { ProjectRecord } from "../types";

type SessionsSidebarProps = {
  collapsed: boolean;
  onCollapsedChange(value: boolean): void;
  projectName: string;
  onProjectNameChange(value: string): void;
  busy: boolean;
  selectedFile: File | null;
  onSelectedFileChange(file: File | null): void;
  selectedId: string | null;
  selectedProject: ProjectRecord | null;
  projects: ProjectRecord[];
  stages: StageItem[];
  runBusyTask(task: () => Promise<void>): Promise<void>;
  refreshProjects(nextSelectedId?: string | null): Promise<void>;
  setNotice(notice: Notice | null): void;
  runWorkbenchImportFlow(): Promise<void>;
};

export function SessionsSidebar({
  collapsed,
  onCollapsedChange,
  projectName,
  onProjectNameChange,
  busy,
  selectedFile,
  onSelectedFileChange,
  selectedId,
  selectedProject,
  projects,
  runBusyTask,
  refreshProjects,
  setNotice,
  runWorkbenchImportFlow,
}: SessionsSidebarProps) {
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
  } | null>(null);

  if (collapsed) {
    return (
      <aside className="sessions-sidebar collapsed">
        <button
          type="button"
          className="sessions-icon-btn"
          onClick={() => onCollapsedChange(false)}
          title="Expand sidebar"
        >
          <ChevronsRight className="h-4 w-4" />
        </button>
        <button
          type="button"
          className="sessions-icon-btn"
          disabled={busy}
          onClick={() =>
            void runBusyTask(async () => {
              const created = await api.createProject(projectName);
              await refreshProjects(created.id);
              setNotice({ tone: "success", title: "Project created", detail: `Created ${created.name}.` });
            })
          }
          title="New project"
        >
          <Plus className="h-4 w-4" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="sessions-sidebar">
      <div className="sessions-sidebar-header">
        <div>
          <strong>Projects</strong>
          <span>{selectedProject?.name ?? "No project selected"}</span>
        </div>
        <button
          type="button"
          className="sessions-icon-btn"
          onClick={() => onCollapsedChange(true)}
          title="Collapse sidebar"
        >
          <ChevronsLeft className="h-4 w-4" />
        </button>
      </div>

      <div className="sessions-sidebar-actions">
        <button
          className="sessions-new-btn"
          disabled={busy}
          onClick={() =>
            void runBusyTask(async () => {
              const created = await api.createProject(projectName);
              await refreshProjects(created.id);
              setNotice({ tone: "success", title: "Project created", detail: `Created ${created.name}.` });
            })
          }
        >
          <Plus className="h-4 w-4" />
          <span>New project</span>
        </button>
      </div>

      <div className="sessions-list">
        {projects.length === 0 ? (
          <p className="sessions-empty-hint">
            No projects yet. Import a STEP file below, or ask your agent to build
            one — e.g. <code>/build a 80×60×8 mm bracket</code>.
          </p>
        ) : null}
        {projects.map((project) => {
          const statusInfo = projectStatusInfo(project.status, project.last_error);
          const updated = formatRelativeTime(project.updated_at);
          return (
          <div key={project.id} className="session-item-wrap">
            <button
              className={project.id === selectedId ? "session-item active" : "session-item"}
              onClick={() => void refreshProjects(project.id)}
            >
              <span className="session-item-row">
                <Folder className="h-4 w-4" />
                <span className="session-item-name">{project.name}</span>
              </span>
              <span className="session-item-meta" title={`Status: ${project.status}`}>
                <span className={`session-status-dot session-status-${statusInfo.tone}`} aria-hidden="true" />
                <span className="session-item-status">{statusInfo.label}</span>
                {updated ? <span className="session-item-updated">· {updated}</span> : null}
              </span>
            </button>
            <button
              type="button"
              className="session-item-delete"
              title="Delete project"
              aria-label={`Delete project ${project.name}`}
              onClick={(event) => {
                event.stopPropagation();
                setConfirmDialog({
                  open: true,
                  title: `Delete project "${project.name}"?`,
                  message: "This removes its geometry, package artifacts, and project records and cannot be undone.",
                  onConfirm: () => {
                    setConfirmDialog(null);
                    void runBusyTask(async () => {
                      await api.deleteProject(project.id);
                      await refreshProjects(project.id === selectedId ? null : selectedId);
                      setNotice({ tone: "success", title: "Project deleted", detail: `Deleted ${project.name}.` });
                    });
                  },
                });
              }}
            >
              <Trash2 style={{ width: 12, height: 12 }} />
            </button>
          </div>
          );
        })}
      </div>

      <div className="sessions-import">
        <label className="sessions-dropzone"
        >
          <input
            type="file"
            accept=".step,.stp,.aieng"
            onChange={(event) => onSelectedFileChange(event.target.files?.[0] ?? null)}
          />
          <span>{selectedFile ? selectedFile.name : "Drop or click to import STEP"}</span>
        </label>
        <button
          className="sessions-import-btn"
          disabled={busy || !selectedFile}
          onClick={() => void runWorkbenchImportFlow()}
        >
          Import
        </button>
      </div>

      {confirmDialog ? (
        <ConfirmDialog
          open={confirmDialog.open}
          title={confirmDialog.title}
          message={confirmDialog.message}
          onConfirm={confirmDialog.onConfirm}
          onCancel={() => setConfirmDialog(null)}
        />
      ) : null}
    </aside>
  );
}
