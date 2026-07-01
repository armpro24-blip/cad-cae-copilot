import type { Notice } from "../../appTypes";
import type { LLMConfig, LocalAgentCapability, LocalAgentConfig, RuntimeConfig, RuntimeConfigSnapshot } from "../../types";
import { ActionIcon } from "../common";
import { LlmProviderSettings } from "./LlmProviderSettings";

type RuntimeSettingsDrawerProps = {
  open: boolean;
  runtime: RuntimeConfigSnapshot | null;
  runtimeDraft: RuntimeConfig | null;
  runtimeBusy: boolean;
  runtimeNotice: Notice | null;
  runtimeProvider: string;
  runtimeReady: boolean;
  llmConfig: LLMConfig;
  llmReady: boolean;
  apiKey: string;
  onApiKeyChange(key: string): void;
  onClose(): void;
  onDraftChange<K extends keyof RuntimeConfig>(key: K, value: RuntimeConfig[K]): void;
  onLlmChange<K extends keyof LLMConfig>(key: K, value: LLMConfig[K]): void;
  onLlmPreset(provider: string): void;
  onLlmRestore(): void;
  onLlmTestResult?(status: "config_ok" | "conn_ok" | "error", message: string): void;
  onTest(): void;
  onSave(): void;
  onRestore(): void;
  localAgentConfig?: LocalAgentConfig;
  localAdapters?: LocalAgentCapability[];
  onLocalAgentChange?(key: keyof LocalAgentConfig, value: LocalAgentConfig[typeof key]): void;
  onProbeLocalAgents?(): void;
};

function LocalAgentSection({ localAdapters, localAgentConfig, onLocalAgentChange, onProbeLocalAgents }: {
  localAdapters?: LocalAgentCapability[];
  localAgentConfig?: LocalAgentConfig;
  onLocalAgentChange?(key: keyof LocalAgentConfig, value: LocalAgentConfig[typeof key]): void;
  onProbeLocalAgents?(): void;
}) {
  const hasAvailable = localAdapters?.some((a) => a.status === "available");

  return (
    <section className="drawer-section">
      <div className="drawer-section-heading">
        <div>
          <h3>Local Agent</h3>
          <p>Claude Code or Codex CLI must be installed and in PATH.</p>
        </div>
        <div className={`llm-readiness-pill ${hasAvailable ? "ready" : "degraded"}`}>
          {hasAvailable ? "Available" : "Not ready"}
        </div>
      </div>

      {localAdapters?.length ? (
        <>
          <div className="runtime-config-grid">
            <label className="form-field runtime-config-span">
              <span>Preferred Adapter</span>
              <select
                value={localAgentConfig?.preferredAdapterId ?? ""}
                onChange={(e) => onLocalAgentChange?.("preferredAdapterId", e.target.value || null)}
              >
                <option value="">Auto</option>
                {localAdapters.map((adapter) => (
                  <option key={adapter.adapter_id} value={adapter.adapter_id}>
                    {adapter.label} {adapter.status === "available" ? "" : `(${adapter.status})`}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="local-agent-adapter-list">
            {localAdapters.map((adapter) => (
              <div key={adapter.adapter_id} className={`local-agent-adapter-item ${adapter.status}`}>
                <div className="local-agent-adapter-head">
                  <strong>{adapter.label}</strong>
                  <span className={`local-agent-status ${adapter.status}`}>{adapter.status}</span>
                </div>
                <div className="local-agent-adapter-meta">
                  {adapter.command_path ? <code>{adapter.command_path}</code> : <code>{adapter.command}</code>}
                  {adapter.version ? <span>v{adapter.version}</span> : null}
                  {adapter.supports_json ? <span className="local-agent-badge">JSON</span> : null}
                  {adapter.supports_json_schema ? <span className="local-agent-badge">Schema</span> : null}
                  {adapter.supports_non_interactive ? <span className="local-agent-badge">Non-interactive</span> : null}
                </div>
                {adapter.diagnostic ? (
                  <p className="local-agent-adapter-diagnostic">{adapter.diagnostic}</p>
                ) : null}
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="summary-note summary-muted">
          No local agent detected. Install Claude Code or Codex CLI.
        </p>
      )}

      <div className="action-row runtime-config-actions">
        <button type="button" className="ghost-button" onClick={() => onProbeLocalAgents?.()}>
          <ActionIcon name="test" />
          Refresh
        </button>
      </div>
    </section>
  );
}

function PreviewAdapterSection({
  runtimeDraft,
  runtimeBusy,
  runtime,
  runtimeProvider,
  runtimeReady,
  onDraftChange,
  onTest,
  onSave,
  onRestore,
}: {
  runtimeDraft: RuntimeConfig | null;
  runtimeBusy: boolean;
  runtime: RuntimeConfigSnapshot | null;
  runtimeProvider: string;
  runtimeReady: boolean;
  onDraftChange<K extends keyof RuntimeConfig>(key: K, value: RuntimeConfig[K]): void;
  onTest(): void;
  onSave(): void;
  onRestore(): void;
}) {
  return (
    <section className="drawer-section">
      <div className="drawer-section-heading">
        <div>
          <h3>Preview Adapter</h3>
          <p>build123d/OCP is the default. FreeCAD is optional.</p>
        </div>
      </div>

      <div className="runtime-config-grid">
        <label className="form-field">
          <span>Provider</span>
          <select
            value={runtimeDraft?.provider ?? "build123d"}
            disabled={runtimeBusy}
            onChange={(event) => onDraftChange("provider", event.target.value)}
          >
            <option value="build123d">build123d / OCP</option>
            <option value="freecad">FreeCAD</option>
          </select>
        </label>
        <label className="form-field">
          <span>Topology backend</span>
          <select
            value={runtimeDraft?.topology_backend ?? "auto"}
            disabled={runtimeBusy}
            onChange={(event) => onDraftChange("topology_backend", event.target.value)}
          >
            <option value="auto">auto</option>
            <option value="mock">mock</option>
            <option value="occ">occ</option>
          </select>
        </label>
        {runtimeDraft?.provider === "freecad" ? (
          <>
            <label className="form-field runtime-config-span">
              <span>FreeCAD Home</span>
              <input
                value={runtimeDraft?.freecad_home ?? ""}
                disabled={runtimeBusy}
                onChange={(event) => onDraftChange("freecad_home", event.target.value)}
                placeholder="FreeCAD installation directory"
              />
            </label>
            <label className="form-field runtime-config-span">
              <span>FREECAD_MCP_ROOT</span>
              <input
                value={runtimeDraft?.freecad_mcp_root ?? ""}
                disabled={runtimeBusy}
                onChange={(event) => onDraftChange("freecad_mcp_root", event.target.value)}
                placeholder="legacy/aieng-freecad-mcp repository directory"
              />
            </label>
          </>
        ) : null}
        <label className="form-field runtime-config-span">
          <span>AIENG_ROOT</span>
          <input
            value={runtimeDraft?.aieng_root ?? ""}
            disabled={runtimeBusy}
            onChange={(event) => onDraftChange("aieng_root", event.target.value)}
            placeholder="aieng repository directory"
          />
        </label>
      </div>

      <div className="action-row runtime-config-actions">
        <button disabled={!runtimeDraft || runtimeBusy} onClick={onTest}>
          <ActionIcon name="test" />
          Test
        </button>
        <button disabled={!runtimeDraft || runtimeBusy} onClick={onSave}>
          <ActionIcon name="save" />
          Save
        </button>
        <button disabled={!runtime?.defaults || runtimeBusy} onClick={onRestore}>
          <ActionIcon name="restore" />
          Reset
        </button>
      </div>

      <div className="runtime-probe-grid">
        <div>
          <span>Provider</span>
          <strong>{runtimeProvider}</strong>
        </div>
        <div>
          <span>Status</span>
          <strong>{runtimeReady ? "Ready" : "Needs config"}</strong>
        </div>
        <div>
          <span>Topology</span>
          <strong>{runtime?.probe.topology_backend_resolved ?? "-"}</strong>
        </div>
        <div>
          <span>{runtimeProvider === "freecad" ? "FreeCADCmd" : "build123d"}</span>
          <strong>
            {runtimeProvider === "freecad"
              ? (runtime?.probe.freecad_cmd_exists ? "Found" : "Not found")
              : (runtimeReady ? "Available" : "Not found")}
          </strong>
        </div>
      </div>
    </section>
  );
}

export function RuntimeSettingsDrawer({
  open,
  runtime,
  runtimeDraft,
  runtimeBusy,
  runtimeNotice,
  runtimeProvider,
  runtimeReady,
  llmConfig,
  llmReady,
  apiKey,
  onApiKeyChange,
  onClose,
  onDraftChange,
  onLlmChange,
  onLlmPreset,
  onLlmRestore,
  onLlmTestResult,
  onTest,
  onSave,
  onRestore,
  localAgentConfig,
  localAdapters,
  onLocalAgentChange,
  onProbeLocalAgents,
}: RuntimeSettingsDrawerProps) {
  if (!open) return null;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="settings-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Environment settings"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-header">
          <div>
            <h2>Settings</h2>
            <p>Environment — LLM provider, local agent, and preview adapter configuration.</p>
          </div>
          <button type="button" className="ghost-button drawer-close" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="drawer-body">
          <LlmProviderSettings
            llmConfig={llmConfig}
            llmReady={llmReady}
            apiKey={apiKey}
            onChange={onLlmChange}
            onApiKeyChange={onApiKeyChange}
            onPreset={onLlmPreset}
            onRestore={onLlmRestore}
            onTestResult={onLlmTestResult}
          />

          <LocalAgentSection
            localAdapters={localAdapters}
            localAgentConfig={localAgentConfig}
            onLocalAgentChange={onLocalAgentChange}
            onProbeLocalAgents={onProbeLocalAgents}
          />

          <PreviewAdapterSection
            runtimeDraft={runtimeDraft}
            runtimeBusy={runtimeBusy}
            runtime={runtime}
            runtimeProvider={runtimeProvider}
            runtimeReady={runtimeReady}
            onDraftChange={onDraftChange}
            onTest={onTest}
            onSave={onSave}
            onRestore={onRestore}
          />

          {runtime?.probe.issues?.length ? (
            <div className="summary-note">
              <strong>Issues</strong>
              <p>{runtime.probe.issues.join("; ")}</p>
            </div>
          ) : null}

          {runtime?.probe.bridge_error ? (
            <div className="summary-note">
              <strong>Bridge error</strong>
              <p>{runtime.probe.bridge_error}</p>
            </div>
          ) : null}

          {runtimeNotice ? (
            <div className={`result-banner result-${runtimeNotice.tone}`}>
              <strong>{runtimeNotice.title}</strong>
              <span>{runtimeNotice.detail}</span>
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
