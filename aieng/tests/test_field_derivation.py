"""Tests for derived FE result fields — analytically-known stress states."""
from __future__ import annotations

import math

import pytest

from aieng.simulation.field_derivation import (
    available_fields,
    canonical_field_name,
    derive_displacement_value,
    derive_stress_value,
    max_shear,
    principal_stresses,
    safety_factor,
    tresca,
    von_mises,
)

# tensors as [Sxx, Syy, Szz, Sxy, Sxz, Syz]
UNIAXIAL = (100.0, 0.0, 0.0, 0.0, 0.0, 0.0)
PURE_SHEAR = (0.0, 0.0, 0.0, 50.0, 0.0, 0.0)
HYDROSTATIC = (100.0, 100.0, 100.0, 0.0, 0.0, 0.0)


def test_uniaxial_tension() -> None:
    assert von_mises(UNIAXIAL) == pytest.approx(100.0)
    s1, s2, s3 = principal_stresses(UNIAXIAL)
    assert (s1, s2, s3) == pytest.approx((100.0, 0.0, 0.0))
    assert tresca(UNIAXIAL) == pytest.approx(100.0)
    assert max_shear(UNIAXIAL) == pytest.approx(50.0)


def test_pure_shear() -> None:
    # VM = sqrt(3)*tau = sqrt(3)*50
    assert von_mises(PURE_SHEAR) == pytest.approx(math.sqrt(3) * 50.0, rel=1e-6)
    s1, s2, s3 = principal_stresses(PURE_SHEAR)
    assert s1 == pytest.approx(50.0) and s3 == pytest.approx(-50.0)
    assert s2 == pytest.approx(0.0, abs=1e-9)
    assert tresca(PURE_SHEAR) == pytest.approx(100.0)
    assert max_shear(PURE_SHEAR) == pytest.approx(50.0)


def test_hydrostatic_has_zero_vm_and_shear() -> None:
    assert von_mises(HYDROSTATIC) == pytest.approx(0.0, abs=1e-9)
    s1, s2, s3 = principal_stresses(HYDROSTATIC)
    assert (s1, s2, s3) == pytest.approx((100.0, 100.0, 100.0))
    assert tresca(HYDROSTATIC) == pytest.approx(0.0, abs=1e-9)


def test_principal_ordering_descending() -> None:
    s1, s2, s3 = principal_stresses((30.0, -10.0, 5.0, 15.0, 0.0, 0.0))
    assert s1 >= s2 >= s3


def test_safety_factor() -> None:
    assert safety_factor(100.0, 250.0) == pytest.approx(2.5)
    assert safety_factor(0.0, 250.0) is None        # unstressed node → no finite SF
    assert safety_factor(100.0, None) is None        # unknown yield
    assert safety_factor(100.0, 0.0) is None         # invalid yield


def test_derive_stress_value_dispatch() -> None:
    assert derive_stress_value("von_mises", UNIAXIAL) == pytest.approx(100.0)
    assert derive_stress_value("sxx", UNIAXIAL) == pytest.approx(100.0)
    assert derive_stress_value("syy", UNIAXIAL) == pytest.approx(0.0)
    assert derive_stress_value("s1", UNIAXIAL) == pytest.approx(100.0)
    assert derive_stress_value("safety_factor", UNIAXIAL, yield_strength=250.0) == pytest.approx(2.5)
    assert derive_stress_value("safety_factor", UNIAXIAL) is None  # no yield
    # legacy alias
    assert derive_stress_value("stress", UNIAXIAL) == pytest.approx(100.0)
    # incomplete tensor
    assert derive_stress_value("von_mises", (1.0, 2.0, None, 0, 0, 0)) is None
    # unknown field
    assert derive_stress_value("nope", UNIAXIAL) is None


def test_derive_displacement_value() -> None:
    assert derive_displacement_value("disp_magnitude", 3.0, 4.0, 0.0) == pytest.approx(5.0)
    assert derive_displacement_value("displacement", 3.0, 4.0, 0.0) == pytest.approx(5.0)  # alias
    assert derive_displacement_value("ux", 3.0, 4.0, 0.0) == pytest.approx(3.0)
    assert derive_displacement_value("uz", 3.0, 4.0, 7.0) == pytest.approx(7.0)
    assert derive_displacement_value("disp_magnitude", 3.0, None, 0.0) is None
    assert derive_displacement_value("nope", 1.0, 2.0, 3.0) is None


def test_canonical_field_name() -> None:
    assert canonical_field_name("stress") == "von_mises"
    assert canonical_field_name("displacement") == "disp_magnitude"
    assert canonical_field_name("S1") == "s1"
    assert canonical_field_name("von_mises") == "von_mises"


def test_available_fields_respects_data_presence() -> None:
    full = {f["name"] for f in available_fields(has_stress=True, has_displacement=True, has_yield=True)}
    assert {"von_mises", "s1", "tresca", "safety_factor", "disp_magnitude", "ux"} <= full

    no_yield = {f["name"] for f in available_fields(has_stress=True, has_displacement=True, has_yield=False)}
    assert "safety_factor" not in no_yield
    assert "von_mises" in no_yield

    disp_only = {f["name"] for f in available_fields(has_stress=False, has_displacement=True, has_yield=False)}
    assert disp_only and all(
        f in {"disp_magnitude", "ux", "uy", "uz"} for f in disp_only
    )


# --- Tensor order: CalculiX writes SXX, SYY, SZZ, SXY, SYZ, SZX -------------
#
# The order is not cosmetic. Reading the last two columns as (Sxz, Syz) swaps
# them inside the tensor, which leaves von Mises unchanged (it only sums the
# squares) but changes the third invariant — so every principal-stress field
# was wrong while the headline metric looked right.

import numpy as np

from aieng.simulation.field_derivation import STRESS_COMPONENT_ORDER, stress_tensor_from_frd


def _matrix(sxx, syy, szz, sxy, syz, szx):
    return np.array([[sxx, sxy, szx], [sxy, syy, syz], [szx, syz, szz]], dtype=float)


def test_canonical_order_is_the_ccx_frd_order() -> None:
    assert STRESS_COMPONENT_ORDER == ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")


def test_principals_of_a_real_ccx_node_match_eigvalsh() -> None:
    # Node 1 of a real CalculiX 2.x STRESS block (value-demo cantilever), in
    # file order SXX SYY SZZ SXY SYZ SZX. The pre-fix reader gave S1 = -1.8428.
    row = (-6.11160, -2.36858, -1.90452, -0.474276, 0.0486139, -0.164666)
    expected = np.linalg.eigvalsh(_matrix(*row))[::-1]
    assert principal_stresses(row) == pytest.approx(tuple(expected), abs=1e-9)
    assert principal_stresses(row)[0] == pytest.approx(-1.887574, abs=1e-6)


@pytest.mark.parametrize("seed", range(25))
def test_random_tensor_invariants(seed: int) -> None:
    rng = np.random.default_rng(seed)
    row = tuple(float(v) for v in rng.normal(0.0, 100.0, 6))
    m = _matrix(*row)
    s1, s2, s3 = principal_stresses(row)
    assert s1 >= s2 >= s3
    assert (s1, s2, s3) == pytest.approx(tuple(np.linalg.eigvalsh(m)[::-1]), abs=1e-8)
    # trace and determinant are invariants of the tensor
    assert s1 + s2 + s3 == pytest.approx(np.trace(m), abs=1e-8)
    assert s1 * s2 * s3 == pytest.approx(np.linalg.det(m), rel=1e-8, abs=1e-6)
    # von Mises from components == von Mises from principals
    vm_principal = math.sqrt(0.5 * ((s1 - s2) ** 2 + (s2 - s3) ** 2 + (s3 - s1) ** 2))
    assert von_mises(row) == pytest.approx(vm_principal, rel=1e-9)
    # Tresca is the principal-stress DIFFERENCE; max shear is half of it.
    assert tresca(row) == pytest.approx(s1 - s3, rel=1e-12)
    assert max_shear(row) == pytest.approx((s1 - s3) / 2.0, rel=1e-12)


@pytest.mark.parametrize(
    "row, field",
    [
        ((0.0, 0.0, 0.0, 0.0, 40.0, 0.0), "syz"),  # pure shear in the y-z plane
        ((0.0, 0.0, 0.0, 0.0, 0.0, 40.0), "sxz"),  # pure shear in the z-x plane
    ],
)
def test_pure_shear_on_each_plane(row, field) -> None:
    assert von_mises(row) == pytest.approx(math.sqrt(3.0) * 40.0)
    assert principal_stresses(row) == pytest.approx((40.0, 0.0, -40.0), abs=1e-9)
    assert derive_stress_value(field, row) == pytest.approx(40.0)


def test_shear_fields_read_the_right_columns() -> None:
    row = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)  # SXX SYY SZZ SXY SYZ SZX
    assert derive_stress_value("sxy", row) == 4.0
    assert derive_stress_value("syz", row) == 5.0
    assert derive_stress_value("sxz", row) == 6.0


def test_tensor_is_reordered_by_component_name() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 6.0, 5.0]
    # a writer that lists SXZ before SYZ: names, not positions, decide
    named = stress_tensor_from_frd(["SXX", "SYY", "SZZ", "SXY", "SXZ", "SYZ"], values)
    assert named == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    # no names at all: positional, in the CalculiX order
    assert stress_tensor_from_frd([], values) == tuple(values)
    # names that do not identify all six components: refuse, do not guess
    assert stress_tensor_from_frd(["SXX", "SYY", "SZZ", "SXY", "A", "B"], values) is None


def test_safety_factor_edges() -> None:
    hydro = (100.0, 100.0, 100.0, 0.0, 0.0, 0.0)  # vM = 0
    assert derive_stress_value("safety_factor", hydro, yield_strength=250.0) is None
    assert derive_stress_value("safety_factor", UNIAXIAL, yield_strength=-1.0) is None
    assert derive_stress_value("safety_factor", UNIAXIAL, yield_strength=275.0) == pytest.approx(2.75)
