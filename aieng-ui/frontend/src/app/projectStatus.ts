/**
 * Map a raw backend project status (e.g. "viewer_ready_glb") to a short,
 * engineer-readable label for the project list. The raw value is still kept in
 * a tooltip for traceability — this only changes the at-a-glance label.
 *
 * IMPORTANT — honesty boundary: the project-LIST payload only distinguishes
 * `empty` / `viewer_ready_*` / error states. It does NOT carry per-project CAE
 * setup / solver / results / report signals (those live in the per-project
 * summary, fetched only for the open project). So the sidebar deliberately does
 * not claim "Setup needed" / "Results available" / "Report ready" — that would
 * overclaim from data it doesn't have.
 */
export type ProjectStatusTone = "empty" | "ready" | "processing" | "error";

export function projectStatusLabel(status: string | null | undefined): string {
  return projectStatusInfo(status).label;
}

/** Label + tone, derived only from reliably-available list fields. */
export function projectStatusInfo(
  status: string | null | undefined,
  lastError?: string | null,
): { label: string; tone: ProjectStatusTone } {
  if (lastError) return { label: "Needs attention", tone: "error" };

  const raw = (status ?? "").trim();
  if (!raw) return { label: "Unknown", tone: "empty" };
  const lower = raw.toLowerCase();

  if (lower.startsWith("viewer_ready")) return { label: "Model ready", tone: "ready" };
  // Error first: an "import_failed" is an error, not an in-progress import.
  if (lower.includes("error") || lower.includes("fail")) return { label: "Needs attention", tone: "error" };
  if (lower.includes("import") || lower.includes("convert") || lower.includes("process") || lower.includes("pending")) {
    return { label: "Processing…", tone: "processing" };
  }
  if (lower === "created" || lower === "empty" || lower === "new") return { label: "Empty", tone: "empty" };

  // Fallback: humanize the raw token (underscores → spaces, sentence case).
  const words = lower.replace(/[_-]+/g, " ").trim();
  return { label: words.charAt(0).toUpperCase() + words.slice(1), tone: "empty" };
}

/**
 * Compact relative-time label for a project's last update (e.g. "2d ago"),
 * giving the sidebar a project-manager feel rather than a bare file list.
 * `now` is injectable for deterministic tests.
 */
export function formatRelativeTime(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const secs = Math.round((now - then) / 1000);
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.round(days / 7);
  if (days < 30) return `${weeks}w ago`;
  const months = Math.round(days / 30);
  if (days < 365) return `${months}mo ago`;
  return `${Math.round(days / 365)}y ago`;
}
