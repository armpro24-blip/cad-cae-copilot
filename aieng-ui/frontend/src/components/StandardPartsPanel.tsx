import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { humanizeApiError } from "../app/apiError";
import type { StandardPartCategory, StandardPartSpec, InsertResult } from "../types/standards";
import { StandardPartCard } from "./StandardPartCard";

type StandardPartsPanelProps = {
  projectId?: string | null;
  onNotice?: (title: string, detail: string) => void;
};

const CATEGORY_ORDER = ["fastener", "bearing", "shaft", "profile", "hole"];

export function StandardPartsPanel({ projectId, onNotice }: StandardPartsPanelProps) {
  const [categories, setCategories] = useState<StandardPartCategory[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState<string>("fastener");
  const [specs, setSpecs] = useState<Record<string, StandardPartSpec>>({});
  const [selectedPresets, setSelectedPresets] = useState<Record<string, string>>({});
  const [currentParams, setCurrentParams] = useState<Record<string, Record<string, number>>>({});
  const [inserting, setInserting] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .listStandardParts()
      .then((data) => {
        if (!cancelled) {
          setCategories(data);
          const first = data.find((c) => CATEGORY_ORDER.includes(c.id)) ?? data[0];
          if (first) setActiveCategory(first.id);
        }
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

  useEffect(() => {
    let cancelled = false;
    const cat = categories.find((c) => c.id === activeCategory);
    if (!cat) return;

    cat.partTypes.forEach((pt) => {
      api
        .getStandardPartSpecs(pt.name)
        .then((spec) => {
          if (!cancelled) {
            setSpecs((prev) => ({ ...prev, [pt.name]: spec }));
            setCurrentParams((prev) => ({
              ...prev,
              [pt.name]: { ...spec.defaultParameters },
            }));
          }
        })
        .catch(() => undefined);
    });
    return () => {
      cancelled = true;
    };
  }, [activeCategory, categories]);

  const activePartTypes = useMemo(() => {
    const cat = categories.find((c) => c.id === activeCategory);
    return cat?.partTypes ?? [];
  }, [categories, activeCategory]);

  const handlePresetSelect = useCallback(
    (partType: string, presetName: string) => {
      setSelectedPresets((prev) => ({ ...prev, [partType]: presetName }));
      const spec = specs[partType];
      const preset = spec?.presets.find((p) => p.name === presetName);
      if (preset) {
        setCurrentParams((prev) => ({ ...prev, [partType]: { ...preset.parameters } }));
      }
    },
    [specs],
  );

  const handleParamChange = useCallback((partType: string, key: string, value: number) => {
    setCurrentParams((prev) => ({
      ...prev,
      [partType]: { ...prev[partType], [key]: value },
    }));
  }, []);

  const handleInsert = useCallback(
    async (partType: string, params: Record<string, number>) => {
      if (!projectId) {
        onNotice?.("No project", "Select or create a project before inserting a standard part.");
        return;
      }
      setInserting(partType);
      setError(null);
      try {
        const result: InsertResult = await api.insertStandardPart(projectId, partType, params);
        if (result.ok) {
          onNotice?.("Part inserted", `${partType} inserted into project ${projectId.slice(0, 8)}…`);
        } else {
          setError(result.message ?? "Insert failed");
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setInserting(null);
      }
    },
    [projectId, onNotice],
  );

  return (
    <section className="standard-parts-panel" aria-label="Standard parts library">
      <div className="panel-head" style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <strong style={{ fontSize: "14px", color: "var(--text-primary, #e5e5e5)" }}>Standard Parts</strong>
          <span style={{ fontSize: "11px", color: "var(--text-secondary, #a3a3a3)" }}>
            {categories.length} categories
          </span>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
          {categories.map((cat) => (
            <button
              key={cat.id}
              type="button"
              className={cat.id === activeCategory ? "compact-button" : "ghost-button compact-button"}
              onClick={() => setActiveCategory(cat.id)}
              style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "999px" }}
            >
              {cat.displayName}
            </button>
          ))}
        </div>
      </div>

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
          {humanizeApiError(error, "Couldn't load standard parts.")}
        </div>
      )}

      {loading ? (
        <div style={{ padding: "20px", textAlign: "center", color: "var(--text-tertiary, #737373)", fontSize: "12px" }}>
          Loading categories…
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {activePartTypes.map((pt) => {
            const spec = specs[pt.name];
            if (!spec) {
              return (
                <div
                  key={pt.name}
                  style={{
                    padding: "16px",
                    borderRadius: "10px",
                    border: "1px solid var(--glass-line, rgba(180, 196, 218, 0.16))",
                    background: "rgba(255, 255, 255, 0.04)",
                    color: "var(--text-tertiary, #737373)",
                    fontSize: "12px",
                    textAlign: "center",
                  }}
                >
                  Loading {pt.displayName}…
                </div>
              );
            }
            return (
              <StandardPartCard
                key={pt.name}
                spec={spec}
                selectedPreset={selectedPresets[pt.name] ?? null}
                onPresetSelect={(name) => handlePresetSelect(pt.name, name)}
                onParameterChange={(key, value) => handleParamChange(pt.name, key, value)}
                currentParameters={currentParams[pt.name]}
                onInsert={(params) => void handleInsert(pt.name, params)}
              />
            );
          })}
          {activePartTypes.length === 0 && (
            <div style={{ textAlign: "center", color: "var(--text-tertiary, #737373)", fontSize: "12px", padding: "16px" }}>
              No part types in this category.
            </div>
          )}
        </div>
      )}
    </section>
  );
}
