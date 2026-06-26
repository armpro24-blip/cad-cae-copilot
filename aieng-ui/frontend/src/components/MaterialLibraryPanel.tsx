import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { humanizeApiError } from "../app/apiError";
import {
  ALL_PARTS_TARGET,
  assignmentTargets,
  materialAssignmentDraft,
} from "../app/materialAssignment";
import type { Material, MaterialComparison } from "../types/materials";
import { MaterialCard } from "./MaterialCard";

type MaterialLibraryPanelProps = {
  projectId?: string | null;
  /** Named parts of the current project — assignment targets (besides "all"). */
  parts?: string[];
  onAssignMaterial?: (materialName: string) => void;
  /** Draft a plan-confirmed `/modify assign material …` into the composer (#225). */
  onApplyAssignment?: (draft: string) => void;
  onNotice?: (title: string, detail: string) => void;
};

export function MaterialLibraryPanel({
  projectId,
  parts,
  onAssignMaterial,
  onApplyAssignment,
  onNotice,
}: MaterialLibraryPanelProps) {
  const [materials, setMaterials] = useState<Material[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<string>("All");
  const [compareNames, setCompareNames] = useState<string[]>([]);
  const [comparison, setComparison] = useState<MaterialComparison | null>(null);
  const [comparing, setComparing] = useState(false);
  const [selectedMaterial, setSelectedMaterial] = useState<string | null>(null);
  const [assignTarget, setAssignTarget] = useState<string>(ALL_PARTS_TARGET);
  const [fetchedParts, setFetchedParts] = useState<string[]>([]);
  // Named parts come from the prop (tests) or, in the live app, the project's
  // geometry report (its part_boxes are keyed by named part).
  useEffect(() => {
    if (parts || !projectId) return;
    let cancelled = false;
    api
      .getGeometryReport(projectId)
      .then((data) => {
        if (!cancelled) setFetchedParts(Object.keys(data?.part_boxes ?? {}));
      })
      .catch(() => {
        if (!cancelled) setFetchedParts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [parts, projectId]);
  const targets = useMemo(() => assignmentTargets(parts ?? fetchedParts), [parts, fetchedParts]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .listMaterials()
      .then((data) => {
        if (!cancelled) setMaterials(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const categories = useMemo(() => {
    const set = new Set(materials.map((m) => m.category));
    return ["All", ...Array.from(set)];
  }, [materials]);

  const filtered = useMemo(() => {
    return materials.filter((m) => {
      const matchesCategory = activeCategory === "All" || m.category === activeCategory;
      const q = query.trim().toLowerCase();
      const matchesQuery =
        !q ||
        m.name.toLowerCase().includes(q) ||
        m.category.toLowerCase().includes(q) ||
        (m.description ?? "").toLowerCase().includes(q);
      return matchesCategory && matchesQuery;
    });
  }, [materials, activeCategory, query]);

  const toggleCompare = useCallback((material: Material) => {
    setCompareNames((prev) => {
      const exists = prev.includes(material.name);
      if (exists) return prev.filter((n) => n !== material.name);
      if (prev.length >= 3) return prev;
      return [...prev, material.name];
    });
    setComparison(null);
  }, []);

  const runCompare = useCallback(async () => {
    if (compareNames.length < 2) return;
    setComparing(true);
    setError(null);
    try {
      const result = await api.compareMaterials(compareNames);
      setComparison(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setComparing(false);
    }
  }, [compareNames]);

  return (
    <section className="material-library-panel" aria-label="Material library">
      <div className="panel-head" style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <strong style={{ fontSize: "14px", color: "var(--text-primary, #e5e5e5)" }}>Material Library</strong>
          <span style={{ fontSize: "11px", color: "var(--text-secondary, #a3a3a3)" }}>
            {materials.length} materials
          </span>
        </div>

        <input
          type="text"
          placeholder="Search materials…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ width: "100%", fontSize: "12px", padding: "8px 10px" }}
        />

        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
          {categories.map((cat) => (
            <button
              key={cat}
              type="button"
              className={cat === activeCategory ? "compact-button" : "ghost-button compact-button"}
              onClick={() => setActiveCategory(cat)}
              style={{
                fontSize: "11px",
                padding: "4px 10px",
                borderRadius: "999px",
              }}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {selectedMaterial && (
        <div className="material-assign-bar" role="group" aria-label="Assign material">
          <span className="material-assign-label">
            Assign <strong>{selectedMaterial}</strong> to
          </span>
          <select
            className="material-assign-target"
            aria-label="Assignment target"
            value={assignTarget}
            onChange={(e) => setAssignTarget(e.target.value)}
          >
            {targets.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
          <button
            type="button"
            className="compact-button material-assign-apply"
            disabled={!onApplyAssignment}
            onClick={() => {
              const draft = materialAssignmentDraft(selectedMaterial, assignTarget);
              if (draft) onApplyAssignment?.(draft);
            }}
            title={onApplyAssignment ? "Draft a /modify material assignment (runs through approval)" : "Connect a composer to apply"}
          >
            Apply
          </button>
        </div>
      )}

      {compareNames.length > 0 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "8px 10px",
            borderRadius: "8px",
            background: "rgba(56, 189, 248, 0.08)",
            border: "1px solid rgba(56, 189, 248, 0.25)",
          }}
        >
          <span style={{ fontSize: "11px", color: "var(--text-secondary, #a3a3a3)" }}>
            Compare ({compareNames.length}/3):
          </span>
          <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", flex: 1 }}>
            {compareNames.map((n) => (
              <span
                key={n}
                style={{
                  fontSize: "11px",
                  padding: "2px 8px",
                  borderRadius: "6px",
                  background: "rgba(56, 189, 248, 0.15)",
                  color: "#7dd3fc",
                }}
              >
                {n}
              </span>
            ))}
          </div>
          <button
            type="button"
            className="compact-button"
            disabled={compareNames.length < 2 || comparing}
            onClick={() => void runCompare()}
            style={{ fontSize: "11px", padding: "4px 10px" }}
          >
            {comparing ? "Comparing…" : "Run"}
          </button>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => {
              setCompareNames([]);
              setComparison(null);
            }}
            style={{ fontSize: "11px", padding: "4px 10px" }}
          >
            Clear
          </button>
        </div>
      )}

      {error && (
        <div
          style={{
            padding: "10px",
            borderRadius: "8px",
            background: "rgba(239, 68, 68, 0.08)",
            border: "1px solid rgba(239, 68, 68, 0.3)",
            color: "#fca5a5",
            fontSize: "12px",
          }}
        >
          {humanizeApiError(error, "Couldn't load the material library.")}
        </div>
      )}

      {loading ? (
        <div style={{ padding: "20px", textAlign: "center", color: "var(--text-tertiary, #737373)", fontSize: "12px" }}>
          Loading materials…
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {filtered.map((m) => (
            <MaterialCard
              key={m.name}
              material={m}
              isCompareCandidate={compareNames.includes(m.name)}
              onCompareToggle={toggleCompare}
              onSelect={(mat) => {
                setSelectedMaterial(mat.name);
                onAssignMaterial?.(mat.name);
                onNotice?.("Material selected", `${mat.name} — pick a target below and Apply.`);
              }}
            />
          ))}
          {filtered.length === 0 && !error && (
            <div style={{ textAlign: "center", color: "var(--text-tertiary, #737373)", fontSize: "12px", padding: "16px" }}>
              No materials match your search.
            </div>
          )}
        </div>
      )}

      {comparison && (
        <div
          style={{
            marginTop: "8px",
            padding: "12px",
            borderRadius: "10px",
            border: "1px solid var(--glass-line, rgba(180, 196, 218, 0.16))",
            background: "rgba(255, 255, 255, 0.04)",
          }}
        >
          <strong style={{ fontSize: "12px", color: "var(--text-primary, #e5e5e5)" }}>Comparison</strong>
          <div style={{ marginTop: "8px", overflowX: "auto" }}>
            <table style={{ width: "100%", fontSize: "11px", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "4px 6px", color: "var(--text-tertiary, #737373)" }}>Property</th>
                  {comparison.materials.map((m) => (
                    <th key={m.name} style={{ textAlign: "left", padding: "4px 6px", color: "var(--text-secondary, #a3a3a3)" }}>
                      {m.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparison.differences.map((diff) => (
                  <tr key={diff.property}>
                    <td style={{ padding: "4px 6px", color: "var(--text-tertiary, #737373)", fontWeight: 600 }}>
                      {diff.property}
                      {diff.unit ? <span style={{ fontWeight: 400, marginLeft: "4px" }}>({diff.unit})</span> : null}
                    </td>
                    {comparison.materials.map((m) => (
                      <td key={m.name} style={{ padding: "4px 6px", color: "var(--text-secondary, #a3a3a3)", fontFamily: "ui-monospace, monospace" }}>
                        {diff.values[m.name] != null ? diff.values[m.name]?.toLocaleString() : "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
