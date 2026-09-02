"""The documented material line must survive either recorded shape (#513).

AGENTS.md documents what `cae.prepare_solver_run` reads back:

    material: Al6061-T6 (E=69 GPa)

That line read `youngs_modulus_pa` directly. The moment `cae.setup_static`
started recording the canonical mm-N-MPa-tonne form — which is what the deck
generator wants, and which removes a two-shape divergence — the line silently
emptied for every new package. CodeRabbit caught it before it shipped.

Both shapes are in circulation (legacy packages and `apply_setup_patch`'s
documented SI example carry the flat form), so the reader must handle both
rather than the writer being reverted to the shape the reader happened to know.
"""

from __future__ import annotations

import pytest

from app.runtime_registry.cae import _material_display

_SI_FLAT = {
    "name": "Al6061-T6",
    "youngs_modulus_pa": 69_000_000_000.0,
    "poisson_ratio": 0.33,
    "density_kg_m3": 2700,
    "yield_strength_pa": 276_000_000.0,
}
_CANONICAL = {
    "name": "Al6061-T6",
    "elastic": {"youngs_modulus": 69_000.0, "poisson_ratio": 0.33},
    "density": 2.7e-09,
    "yield_strength": 276.0,
}


@pytest.mark.parametrize("material", [_SI_FLAT, _CANONICAL], ids=["si_flat", "canonical"])
def test_both_recorded_shapes_report_the_same_modulus(material: dict) -> None:
    name, modulus_gpa, poisson = _material_display(material)

    assert name == "Al6061-T6"
    assert modulus_gpa == pytest.approx(69.0, rel=1e-3), (
        "the documented line reports GPa; a shape the reader does not know "
        "empties it instead of failing loudly"
    )
    assert poisson == pytest.approx(0.33)


def test_a_material_with_no_modulus_reports_none_rather_than_zero() -> None:
    """The caller omits the `(E=…)` clause; it must not print `E=0 GPa`."""
    name, modulus_gpa, poisson = _material_display({"name": "Unknown"})

    assert name == "Unknown"
    assert modulus_gpa is None
    assert poisson is None or poisson == pytest.approx(0.3), (
        "a default poisson is acceptable; a fabricated modulus is not"
    )
