// Turn a raw API/fetch error into a human-readable message (#394 polish).
// The `request` helper rejects with an Error whose message is the raw response
// body — often a FastAPI `{"detail": "..."}` JSON string. Showing that verbatim
// (e.g. `{"detail":"Not Found"}`) in a panel reads as a broken app. This pulls
// out the human part and falls back to a friendly sentence for unhelpful errors.

export function humanizeApiError(raw: unknown, fallback = "Something went wrong."): string {
  const text = raw instanceof Error ? raw.message : typeof raw === "string" ? raw : String(raw ?? "");
  const trimmed = text.trim();
  if (!trimmed) return fallback;

  // FastAPI-style {"detail": ...} bodies.
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      const detail =
        parsed && typeof parsed === "object" && "detail" in parsed
          ? (parsed as { detail: unknown }).detail
          : undefined;
      if (detail !== undefined) {
        const flat =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail
                  .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
                  .join("; ")
              : JSON.stringify(detail);
        // A bare "Not Found" / "Internal Server Error" is noise — prefer the fallback.
        if (/^\s*(not found|internal server error)\s*$/i.test(flat)) return fallback;
        return flat || fallback;
      }
    } catch {
      /* not JSON — fall through */
    }
  }

  if (/^\s*(not found|internal server error)\s*$/i.test(trimmed)) return fallback;
  return trimmed;
}
