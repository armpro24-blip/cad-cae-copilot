"""STEP interoperability regression.

What leaves this product for another CAD seat is the STEP file, so the file — not
just our in-memory model — is what has to stay correct. Three separate things
are pinned here, with deliberately different evidence strength:

1. **Structural conformance** — checked by reading the file as plain
   ISO 10303-21 text. This is the only part that is *independent* of our
   geometry kernel: no OCCT involved, so a kernel bug cannot mask a malformed
   file.
2. **Geometry round-trip fidelity** — export, re-import, compare. Honest
   limitation: this runs through the SAME OCCT kernel we export with, so it
   proves "we do not lose geometry", NOT "SolidWorks/NX will read it". Only a
   third-party reader can prove the latter, and no such reader is in CI.
3. **Semantic survival** — part labels and colours are the product's core
   engineering meaning (labels drive topology_map / feature_graph and every
   `@face:` pointer). If STEP silently dropped them, anyone opening our file
   elsewhere would get anonymous solids. Worth a guard precisely because a
   refactor could break it invisibly.

Adding FreeCAD here would NOT strengthen (2): FreeCAD is OCCT too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def _export_two_part_model(tmp_path: Path) -> Path:
    """A labelled, coloured, multi-solid model — the shape the CAD guide teaches."""
    from build123d import Box, Color, Compound, Cylinder, Location, export_step

    plate = Box(120, 80, 8)
    plate.label = "base_plate"
    plate.color = Color(0.55, 0.62, 0.70)

    boss = Cylinder(10, 20).moved(Location((30, 0, 14)))
    boss.label = "bearing_boss"
    boss.color = Color(0.85, 0.20, 0.20)

    step = tmp_path / "model.step"
    export_step(Compound(children=[plate, boss]), str(step))
    return step


def test_exported_step_is_structurally_conformant(tmp_path: Path) -> None:
    """Read the export as ISO 10303-21 TEXT — no CAD kernel in the loop.

    This is the one check here that a kernel bug cannot hide: if OCCT wrote a
    malformed or mis-declared file, re-importing it with OCCT could still
    succeed while a third-party reader choked.
    """
    pytest.importorskip("build123d")
    step = _export_two_part_model(tmp_path)
    text = step.read_text(errors="replace")

    assert text.lstrip().startswith("ISO-10303-21;")
    assert text.rstrip().endswith("END-ISO-10303-21;")
    assert "HEADER;" in text and "DATA;" in text and "ENDSEC;" in text

    schema = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text)
    assert schema is not None, "FILE_SCHEMA is missing — no reader can interpret this"
    # AP214 (automotive_design) or AP203 (config_control_design) are the two
    # application protocols mainstream CAD reliably imports.
    assert "10303" in schema.group(1)
    assert ("AUTOMOTIVE_DESIGN" in schema.group(1)
            or "CONFIG_CONTROL_DESIGN" in schema.group(1)), schema.group(1)

    # A real solid model, not an empty shell of a file.
    assert len(re.findall(r"^#\d+\s*=", text, re.M)) > 100
    assert re.search(r"MANIFOLD_SOLID_BREP", text)
    assert re.search(r"CLOSED_SHELL", text)


def test_step_round_trip_preserves_geometry(tmp_path: Path) -> None:
    """Export -> re-import must not lose solids, faces, or volume.

    Same-kernel check (see module docstring): it guards our export path against
    regressions, and does not claim third-party interoperability.
    """
    pytest.importorskip("build123d")
    from build123d import export_step, import_step

    step = _export_two_part_model(tmp_path)
    first = import_step(str(step))

    again = tmp_path / "again.step"
    export_step(first, str(again))
    second = import_step(str(again))

    assert len(second.solids()) == len(first.solids()) == 2
    assert len(second.faces()) == len(first.faces())
    # plate 120*80*8 = 76800 + cylinder pi*10^2*20 = 6283.2
    assert first.volume == pytest.approx(83083.2, rel=1e-4)
    assert second.volume == pytest.approx(first.volume, rel=1e-9)

    bb1, bb2 = first.bounding_box(), second.bounding_box()
    for a, b in ((bb1.min, bb2.min), (bb1.max, bb2.max)):
        assert a.X == pytest.approx(b.X, abs=1e-6)
        assert a.Y == pytest.approx(b.Y, abs=1e-6)
        assert a.Z == pytest.approx(b.Z, abs=1e-6)


def test_step_carries_part_names_and_colours(tmp_path: Path) -> None:
    """Labels and colours are the engineering meaning, not decoration.

    Labels become named parts in topology_map/feature_graph and back every
    `@face:` pointer the CAE setup binds to. A STEP that drops them hands the
    next tool anonymous solids.
    """
    pytest.importorskip("build123d")
    from build123d import import_step

    step = _export_two_part_model(tmp_path)
    text = step.read_text(errors="replace")

    # names travel as STEP PRODUCT entities — the standard place a reader looks
    products = set(re.findall(r"PRODUCT\s*\(\s*'([^']*)'", text))
    assert "base_plate" in products
    assert "bearing_boss" in products

    # colours travel as COLOUR_RGB bound through STYLED_ITEM, one per part
    assert len(re.findall(r"COLOUR_RGB", text)) >= 2
    assert len(re.findall(r"STYLED_ITEM", text)) >= 2

    # ...and the names come back on import, so a re-imported package keeps its
    # semantics instead of degrading to body_001/body_002
    back = import_step(str(step))
    labels = [getattr(child, "label", None) for child in (back.children or [])]
    assert "base_plate" in labels
    assert "bearing_boss" in labels
