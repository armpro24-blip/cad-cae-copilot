"""CAE payload profiling — measure and optionally compact CAE response sizes.

Issue B4: Profile CAE tool response sizes; optimize only if flagged.
"""
from __future__ import annotations

import json
import logging
from typing import Any

LOGGER = logging.getLogger("app.cae_payload_profile")

# Heuristic: one token ≈ 4 UTF-8 characters for English/JSON text.
_TOKEN_ESTIMATE_DIVISOR = 4

# Warn when a CAE payload exceeds ~2 k tokens (already considered "lean" upper bound).
WARN_TOKENS = 2048

# Compact when a CAE payload exceeds ~4 k tokens.
COMPACT_TOKENS = 4096

# Maximum list length before truncation during compaction.
_MAX_LIST_LENGTH = 10


def estimate_tokens(obj: Any) -> int:
    """Estimate LLM token count from a JSON-serializable object."""
    try:
        text = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(obj)
    return max(1, len(text) // _TOKEN_ESTIMATE_DIVISOR)


def profile_payload(data: Any, *, label: str = "cae_payload") -> dict[str, Any]:
    """Profile a payload and return a metrics dict.

    Logs a warning when the estimated token count exceeds :data:`WARN_TOKENS`.
    """
    try:
        raw_bytes = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raw_bytes = len(str(data).encode("utf-8"))
    tokens = estimate_tokens(data)
    result = {
        "label": label,
        "bytes": raw_bytes,
        "estimated_tokens": tokens,
    }
    if tokens >= WARN_TOKENS:
        LOGGER.warning(
            "CAE payload %s is large: %d bytes (~%d tokens)",
            label,
            raw_bytes,
            tokens,
        )
    return result


def compact_cae_block(
    cae: dict[str, Any],
    *,
    max_tokens: int = COMPACT_TOKENS,
    label: str = "cae_block",
) -> dict[str, Any]:
    """Return a compacted CAE block if it exceeds the token threshold.

    Compaction steps (applied in order until the block fits):

    1. Replace large optional draft/fixture objects with a compacted stub.
    2. Truncate long lists (materials, loads, boundary_conditions,
       available_fields) while preserving count metadata.

    When compaction occurs, a ``_payload_profile`` key is injected recording
    the original size and the reason.
    """
    original_tokens = estimate_tokens(cae)
    if original_tokens <= max_tokens:
        return cae

    compacted = dict(cae)
    profile = profile_payload(cae, label=label)
    profile["compacted"] = True
    profile["compaction_reason"] = "exceeded_token_threshold"

    # Step 1: drop large optional objects that are often verbose.
    for key in ("fea_setup_draft", "template_fixture"):
        if key in compacted and compacted[key] is not None:
            compacted[key] = {
                "_compacted": True,
                "reason": "payload_size",
                "original_present": True,
            }

    if estimate_tokens(compacted) <= max_tokens:
        profile["post_compaction_tokens"] = estimate_tokens(compacted)
        compacted["_payload_profile"] = profile
        LOGGER.info("CAE block %s compacted (draft/fixture) from ~%d to ~%d tokens", label, original_tokens, estimate_tokens(compacted))
        return compacted

    # Step 2: truncate long lists.
    for key in ("materials", "loads", "boundary_conditions", "available_fields",
                "parsed_metric_names"):
        if key in compacted and isinstance(compacted[key], list):
            original = compacted[key]
            if len(original) > _MAX_LIST_LENGTH:
                compacted[key] = original[:_MAX_LIST_LENGTH] + [
                    {"_truncated": True, "original_count": len(original)}
                ]

    profile["post_compaction_tokens"] = estimate_tokens(compacted)
    compacted["_payload_profile"] = profile
    LOGGER.info(
        "CAE block %s compacted (draft/fixture + list truncation) from ~%d to ~%d tokens",
        label,
        original_tokens,
        profile["post_compaction_tokens"],
    )
    return compacted
