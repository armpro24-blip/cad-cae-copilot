import { useCallback, useState } from "react";

import type { CadGenerationProgress } from "../appTypes";

export function useEngineeringActions() {
  const [cadPreviewUrl, setCadPreviewUrl] = useState<string | null>(null);
  const [cadPreviewFormat, setCadPreviewFormat] = useState<string | null>(null);
  const [cadGenerationProgress, setCadGenerationProgress] = useState<CadGenerationProgress | null>(null);

  // Stable identity: a useEffect in useWorkbenchApp depends on this to reset
  // derived preview state on project switch. It only calls stable setters, so
  // an empty dep list is correct and keeps that effect from over-firing.
  const resetProjectDerivedState = useCallback(() => {
    setCadPreviewUrl(null);
    setCadPreviewFormat(null);
  }, []);

  function refreshViewerAsset(projectId: string, previewUrl?: string | null, previewFormat?: string | null) {
    setCadPreviewUrl(`${previewUrl || `/api/projects/${projectId}/cad-preview`}${previewUrl?.includes("?") ? "&" : "?"}ts=${Date.now()}`);
    setCadPreviewFormat(previewFormat ?? "glb");
  }

  return {
    cadPreviewUrl,
    cadPreviewFormat,
    cadGenerationProgress,
    setCadGenerationProgress,
    refreshViewerAsset,
    resetProjectDerivedState,
  };
}
