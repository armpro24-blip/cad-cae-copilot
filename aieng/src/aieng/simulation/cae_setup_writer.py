"""Stamp an AUTHORED CAE setup document so it says what it is.

`simulation/cae_imports/parsed_{materials,loads,boundary_conditions}.json` were
designed for the CAE import direction: their schemas require a `parser` and the
`source_file` it read. The workbench's `cae.setup_static` writes the same members
from engineering intent, and until #513 wrote them bare — no format, no
provenance at all.

It must not claim a `source_file` either. The synthesised
`source_solver_deck.inp` in that directory supplies the *mesh*; the loads and
boundary conditions were never parsed from it, and pointing at it would be an
invented provenance rather than a missing one. So an authored document declares
`authored_by` instead, and the schema requires one or the other.

Same shape of answer as `cae_mapping_writer`: a document that records how its
content came to exist is strictly more useful than a bare one, and the honest
answer differs by producer.
"""

from __future__ import annotations

from typing import Any

FORMAT_VERSION = "0.1.0"

#: payload key -> the `format` string its schema pins.
_FORMATS = {
    "materials": "aieng.parsed_cae_materials",
    "loads": "aieng.parsed_cae_loads",
    "boundary_conditions": "aieng.parsed_cae_boundary_conditions",
}


def authored_setup_document(
    payload_key: str, items: list[dict[str, Any]], *, authored_by: str
) -> dict[str, Any]:
    """Build one authored setup document: `{format, format_version, authored_by, <items>}`.

    Raises for an unknown `payload_key` — a typo would otherwise reach the
    package and surface as a schema failure several steps later.
    """
    try:
        fmt = _FORMATS[payload_key]
    except KeyError:
        raise ValueError(
            f"unknown setup payload {payload_key!r}; expected one of {sorted(_FORMATS)}"
        ) from None
    if not authored_by:
        raise ValueError("authored_by is required: an authored document must say who wrote it")
    return {
        "format": fmt,
        "format_version": FORMAT_VERSION,
        "authored_by": authored_by,
        payload_key: items,
    }
