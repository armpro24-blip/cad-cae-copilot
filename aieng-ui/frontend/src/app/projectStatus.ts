/**
 * Map a raw backend project status (e.g. "viewer_ready_glb") to a short,
 * engineer-readable label for the project list. The raw value is still kept in
 * a tooltip for traceability — this only changes the at-a-glance label.
 */
export function projectStatusLabel(status: string | null | undefined): string {
  const raw = (status ?? "").trim();
  if (!raw) return "Unknown";
  const lower = raw.toLowerCase();

  if (lower.startsWith("viewer_ready")) return "Model ready";
  // Error first: an "import_failed" is an error, not an in-progress import.
  if (lower.includes("error") || lower.includes("fail")) return "Error";
  if (lower.includes("import") || lower.includes("convert") || lower.includes("process") || lower.includes("pending")) {
    return "Processing…";
  }
  if (lower === "created" || lower === "empty" || lower === "new") return "Empty";

  // Fallback: humanize the raw token (underscores → spaces, sentence case).
  const words = lower.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
