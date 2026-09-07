"""An analysis this generator cannot emit is refused, not aliased to static.

Found by a cold-start agent trial. Asked whether a bracket would survive five
years of a 0-400 N cycle, it went looking for the capability boundary and noted
that `analysis_type` is a bare string on `cae.setup_static`, written verbatim by
`cae.apply_setup_patch`, with `normalize_analysis_type` ending in

    return _ANALYSIS_TYPE_ALIASES.get(key, "static")

So `analysis_type: "fatigue"` was accepted, silently became `static`, was emitted
as a `*STATIC` step, solved, and came back stamped `executed_solver_result` —
while `solver_settings.json` still recorded `fatigue`. A request for physics the
tool does not have became a static answer wearing the requested label. The
agent flagged it as a hazard rather than a finding because testing it writes to
a package; this is that test.

ABSENT and UNRECOGNIZED were sharing an answer. Absent still means static —
that is the documented default for a setup that never named one.
"""
from __future__ import annotations

import pytest

from aieng.simulation.deck_generator import (
    normalize_analysis_type,
    supported_analysis_types,
    unsupported_analysis_type,
)


def test_an_unrecognized_analysis_type_is_reported_unsupported() -> None:
    assert unsupported_analysis_type({"analysis_type": "fatigue"}) == "fatigue"
    assert unsupported_analysis_type({"analysis_type": "creep"}) == "creep"
    assert unsupported_analysis_type({"analysis_type": "nonlinear_contact"}) == (
        "nonlinear_contact"
    )


def test_an_absent_analysis_type_is_a_default_not_a_substitution() -> None:
    """The distinction the fix rests on: nothing named vs something unknown."""
    assert unsupported_analysis_type({}) is None
    assert unsupported_analysis_type(None) is None
    assert unsupported_analysis_type({"analysis_type": ""}) is None
    assert normalize_analysis_type({}) == "static"
    assert normalize_analysis_type(None) == "static"


@pytest.mark.parametrize(
    "authored,canonical",
    [
        ("static", "static"),
        ("linear_static", "static"),
        ("modal", "modal"),
        ("Frequency", "modal"),
        ("buckling", "buckling"),
        ("thermal", "thermal"),
        ("heat transfer", "thermal"),
        ("thermal-stress", "thermal_structural"),
    ],
)
def test_every_supported_spelling_still_passes(authored: str, canonical: str) -> None:
    """Aliases, case and separators keep working — this is not a tightening."""
    settings = {"analysis_type": authored}
    assert unsupported_analysis_type(settings) is None
    assert normalize_analysis_type(settings) == canonical


def test_the_setup_document_is_read_when_solver_settings_is_silent() -> None:
    assert unsupported_analysis_type({}, {"analysis_type": "fatigue"}) == "fatigue"
    assert unsupported_analysis_type({}, {"analysis_type": "modal"}) is None


def test_the_supported_set_is_reported_for_the_refusal_message() -> None:
    """The refusal names what IS available, so a caller can pick again."""
    supported = supported_analysis_types()

    assert supported == sorted(supported), "sorted, so the message is stable"
    assert set(supported) == {
        "static", "modal", "buckling", "thermal", "thermal_structural"
    }, supported


def test_deck_generation_refuses_an_unsupported_analysis_type(tmp_path) -> None:
    """The refusal must be WIRED, not merely available as a helper.

    The pure-function tests above pass even with the call site removed — which
    is exactly how a guard ends up written and not reached. This one drives
    `generate_solver_input_package` itself.
    """
    import json
    import zipfile

    from aieng.simulation.deck_generator import (
        DeckGenerationError,
        generate_solver_input_package,
    )

    pkg = tmp_path / "fatigue.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "bracket"}))
        zf.writestr(
            "simulation/solver_settings.json",
            json.dumps({"analysis_type": "fatigue", "solver": "CalculiX"}),
        )

    with pytest.raises(DeckGenerationError) as excinfo:
        generate_solver_input_package(pkg, run_id="run_001")

    message = str(excinfo.value)
    assert "fatigue" in message, message
    # The refusal names what IS available, so the caller can choose again.
    for supported in ("static", "modal", "buckling"):
        assert supported in message, message


def test_deck_generation_does_not_refuse_a_supported_type(tmp_path) -> None:
    """The guard must not fire on the four legitimate spellings.

    A package this minimal still cannot produce a deck — it has no mesh — so the
    assertion is that it fails for the RIGHT reason: missing setup, not an
    unsupported analysis.
    """
    import json
    import zipfile

    from aieng.simulation.deck_generator import (
        MissingSetupError,
        generate_solver_input_package,
    )

    for authored in ("static", "modal", "buckling", "thermal"):
        pkg = tmp_path / f"{authored}.aieng"
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"model_id": "bracket"}))
            zf.writestr(
                "simulation/solver_settings.json",
                json.dumps({"analysis_type": authored, "solver": "CalculiX"}),
            )

        with pytest.raises(MissingSetupError) as excinfo:
            generate_solver_input_package(pkg, run_id="run_001")

        assert "mesh_source_deck" in str(excinfo.value), authored


def test_an_unlisted_spelling_of_a_SUPPORTED_analysis_is_listed() -> None:
    """The refusal's first run found `static_structural` — 10 uses in this repo.

    It is a legitimate spelling of static analysis that had been riding the
    silent fallback, and refusing it broke six existing tests. That is the
    trade the refusal makes, stated plainly: an unlisted spelling of something
    supported costs one clear error naming the supported set, where an
    unsupported PHYSICS used to cost a silently wrong answer wearing the
    requested label. Failing closed is also how the gaps get found — this one
    surfaced within a single suite run.
    """
    settings = {"analysis_type": "static_structural"}

    assert unsupported_analysis_type(settings) is None
    assert normalize_analysis_type(settings) == "static"
