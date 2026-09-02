"""Fill in a CAE mapping document's provenance, whoever authored it.

`simulation/cae_mapping.json` has three producers — the CAE import path, the
workbench's `cae.setup_static` -> `normalize_cae_bindings` authoring path, and
AI preprocessing — and until #513 the last two wrote a lean document that
omitted every field the schema requires: no `format`, no `source_files`, and per
mapping no `cae_type` / `mapping_status` / `mapping_method` / `confidence`.

Those fields are not bureaucracy. They are the answer to "how did this binding
come to exist, and how much should I trust it" — the same question the
credibility stamp answers for results. An authored mapping can answer it
honestly: a binding taken from an explicit `@face:` pointer involved no
inference; one resolved from an engineering phrase did; an LLM-proposed one is
weaker than both. Writing that down costs nothing and is strictly more
informative than leaving it blank.

One implementation so the producers cannot drift apart again.
"""

from __future__ import annotations

from typing import Any

CAE_MAPPING_FORMAT = "aieng.cae_mapping"
CAE_MAPPING_FORMAT_VERSION = "0.1.0"

#: `mapping_method` values, and what each one claims about the binding.
METHOD_POINTER = "resolved_from_pointer"      # an explicit @face: id, no inference
METHOD_INTENT = "resolved_from_intent"        # an engineering phrase, resolved geometrically
METHOD_AI = "ai_generated"                    # proposed by a language model
METHOD_USER = "user_provided"                 # stated by hand

#: How much a method's binding can be trusted, absent a better measurement.
#: A pointer binding is exact by construction; a phrase was resolved and could
#: have picked the wrong face; an LLM proposal is weaker still.
_METHOD_CONFIDENCE = {
    METHOD_POINTER: "high",
    METHOD_USER: "high",
    METHOD_INTENT: "medium",
    METHOD_AI: "medium",
}

#: Every method this writer will stamp. A caller typo would otherwise reach the
#: artifact and be reported as a schema failure three steps later.
_KNOWN_METHODS = frozenset({METHOD_POINTER, METHOD_INTENT, METHOD_AI, METHOD_USER,
                            "not_inferred_phase_10a"})

_VALID_CONFIDENCE = {"none", "low", "medium", "high"}
#: Statuses for which "we bound nothing" is the truthful confidence. NOT
#: `partially_mapped`: something WAS bound, and `validate.py` rejects that
#: combination outright — so defaulting it to "none" would make this finalizer
#: produce a document the validator refuses.
_UNBOUND_STATUSES = {"unmapped", "unresolved"}
_ROLE_TO_TYPE = {
    "fixed_support": "boundary_condition_target",
    "load_application": "load_target",
}
#: Emitted by a legacy writer that used `confidence` to record provenance. It is
#: a method, not a confidence, so it moves.
_LEGACY_CONFIDENCE_AS_METHOD = {"ai_generated": METHOD_AI}

_DROP_KEYS = ("schema_version",)


def _cae_type(mapping: dict[str, Any]) -> str:
    maps_to = mapping.get("maps_to")
    role = str((maps_to or {}).get("role") or "") if isinstance(maps_to, dict) else ""
    if role in _ROLE_TO_TYPE:
        return _ROLE_TO_TYPE[role]
    # Fall back on the NSET naming convention the binder uses (`*_L` for loads).
    entity = str(mapping.get("cae_entity") or "")
    return "load_target" if entity.upper().endswith("_L") else "boundary_condition_target"


def finalize_mapping_entry(mapping: dict[str, Any], *, method: str) -> dict[str, Any]:
    """Return one mapping with its provenance filled in. Idempotent."""
    entry = {k: v for k, v in mapping.items() if k not in _DROP_KEYS}

    stated = str(entry.get("confidence") or "")
    if stated in _LEGACY_CONFIDENCE_AS_METHOD:
        # The value described HOW the binding was made, not how sure we are.
        method = _LEGACY_CONFIDENCE_AS_METHOD[stated]
        stated = ""

    entry.setdefault("cae_type", _cae_type(entry))
    entry.setdefault("mapping_method", method)
    entry["mapping_status"] = entry.get("mapping_status") or (
        "mapped" if entry.get("face_ids") else "unresolved"
    )
    if stated in _VALID_CONFIDENCE:
        entry["confidence"] = stated
    else:
        entry["confidence"] = (
            "none"
            if entry["mapping_status"] in _UNBOUND_STATUSES
            else _METHOD_CONFIDENCE.get(str(entry.get("mapping_method")), "medium")
        )
    return entry


def finalize_cae_mapping(
    document: dict[str, Any] | None,
    *,
    method: str,
    source_files: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Stamp the document envelope and every mapping's provenance.

    `source_files` is empty for an authored setup — no external deck was parsed,
    and saying so is the honest answer rather than a missing key. `notes` must be
    non-empty per the schema, which suits: the note records what produced the
    mapping.
    """
    if method not in _KNOWN_METHODS:
        raise ValueError(
            f"unknown mapping_method {method!r}; expected one of {sorted(_KNOWN_METHODS)}"
        )
    doc = {k: v for k, v in (document or {}).items() if k not in _DROP_KEYS}
    doc["format"] = CAE_MAPPING_FORMAT
    doc["format_version"] = CAE_MAPPING_FORMAT_VERSION
    doc["source_files"] = list(source_files if source_files is not None else doc.get("source_files") or [])

    mappings = doc.get("mappings")
    if isinstance(mappings, list):
        # A malformed entry is PASSED THROUGH, not dropped. Silently discarding
        # it would delete a load or boundary-condition binding to make a
        # validator happy — a helper that fills fields in must never be the
        # thing that loses one.
        doc["mappings"] = [
            finalize_mapping_entry(m, method=method) if isinstance(m, dict) else m
            for m in mappings
        ]
    elif mappings is None:
        doc["mappings"] = []  # genuinely absent; the schema requires the key
    # else: present but the wrong type — left exactly as it is, for the
    # validator to report. Coercing it would hide whatever the writer meant.

    notes = [n for n in (doc.get("notes") or []) if isinstance(n, str) and n.strip()]
    if not notes:
        notes = [note or _default_note(method)]
    doc["notes"] = notes
    return doc


def _default_note(method: str) -> str:
    if method == METHOD_POINTER:
        return (
            "Authored in the workbench: each NSET was bound to an explicit "
            "@face: pointer, so no face was inferred. No external CAE deck was parsed."
        )
    if method == METHOD_INTENT:
        return (
            "Authored in the workbench from engineering intent (cae.setup_static): "
            "the named faces were resolved geometrically. No external CAE deck was parsed."
        )
    if method == METHOD_AI:
        return (
            "Bindings proposed by AI preprocessing from the task description; "
            "review the face selection before trusting a result. No external CAE "
            "deck was parsed."
        )
    return f"Mapping produced by {method}."
