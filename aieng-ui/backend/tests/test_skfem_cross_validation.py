"""Real product C3D10/FRD versus an independently assembled scikit-fem model.

Layer 1 replays the actual generated deck. Layer 2 ignores its node sets and
nodal loads: boundary facets are selected from CAD topology and setup intent,
then loaded by FacetBasis integration. No fake solver and no saved benchmark.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip('skfem', reason='install backend[verification]; the real gate rejects skips')
import numpy as np

from skfem_reference import Reference, read_deck, von_mises
from test_product_path_benchmarks import (
    _STEEL, _build, _faces, _member, _plane, _roller, _solve, _write_setup, bench,
)


_BRACKET = '''from build123d import *
with BuildPart() as bp:
    Box(120, 80, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations((45, 25, 0), (-45, 25, 0), (45, -25, 0), (-45, -25, 0)):
        Hole(radius=5, depth=8)
    fillet(bp.edges().filter_by(Axis.Z), radius=4)
base_plate = bp.part
base_plate.label = "base_plate"
rib = Box(60, 5, 25, align=(Align.CENTER, Align.CENTER, Align.MIN))
rib = rib.moved(Location((0, 0, 8)))
rib.label = "rib_main"
result = Compound(children=[base_plate, rib])
'''


@pytest.fixture(scope='module', params=['tension', 'cantilever', 'bracket'])
def case(bench, request):
    name = request.param
    young, poisson = 210000.0, 0.3
    if name == 'bracket':
        code, force, mesh_size = _BRACKET, np.array([0., 0., -500.]), 6.
    else:
        length, width, height = (100., 20., 10.) if name == 'tension' else (200., 20., 20.)
        code = ("from build123d import *\n"
                f"body = Box({length}, {width}, {height}, align=(Align.MIN, Align.MIN, Align.MIN))\n"
                "body.label = 'base_plate'\nresult = Compound(children=[body])\n")
        force = np.array([2000., 0., 0.]) if name == 'tension' else np.array([0., 0., -100.])
        mesh_size = 5.
    pid, pkg = _build(bench, code, f'skfem_{name}')
    faces = {f['id']: f for f in _faces(pkg)}
    if name == 'tension':
        support_intent = [(_plane(pkg, d, 0.), (d,)) for d in range(3)]
        load_face = _plane(pkg, 0, length)
        _write_setup(bench, pid, _STEEL,
                     [_roller(f'sym_{d}', face, d + 1) for face, (d,) in support_intent],
                     [{'id': 'pull', 'type': 'force', 'target': f'@face:{load_face}',
                       'value_n': 2000., 'direction': [1, 0, 0]}])
    else:
        support_intent = [(_plane(pkg, 2 if name == 'bracket' else 0, 0.), (0, 1, 2))]
        load_face = _plane(pkg, 2, 33.) if name == 'bracket' else _plane(pkg, 0, length)
        setup = bench['runtime'].invoke_tool('cae.setup_static', {
            'project_id': pid,
            'material': {'name': 'STEEL', 'youngs_modulus_mpa': young, 'poisson_ratio': poisson},
            'fix': 'bottom' if name == 'bracket' else 'left',
            'load': {'at': 'rib_main top' if name == 'bracket' else 'right',
                     'force_n': float(np.linalg.norm(force)), 'direction': '-Z'},
        })
        assert setup['status'] == 'ok', setup
    _solve(bench, pid, mesh_size)
    deck = read_deck(_member(pkg, 'simulation/runs/run_001/solver_input.inp').decode())
    reference = Reference(deck, deck.young, deck.poisson)
    yield {
        'name': name, 'pkg': pkg, 'reference': reference, 'force': force,
        'supports': [(faces[face], dofs) for face, dofs in support_intent],
        'load_face': faces[load_face], 'young': young, 'poisson': poisson,
    }


def _product_fields(case, tmp_path):
    from aieng.simulation.frd_result_extractor import parse_frd
    from aieng.simulation.field_derivation import stress_tensor_from_frd, derive_stress_value

    path = tmp_path / 'result.frd'
    path.write_bytes(_member(case['pkg'], 'simulation/runs/run_001/outputs/result.frd'))
    fields = parse_frd(path)
    disp, stress = fields['DISP'], fields['STRESS']
    ids = case['reference'].ids
    assert set(disp['node_data']) == set(ids), 'FRD must contain every mesh node, including midsides'
    assert set(stress['node_data']) == set(ids)
    u = np.array([disp['node_data'][n][:3] for n in ids], dtype=float)
    tensors = [stress_tensor_from_frd(stress['components'], stress['node_data'][n]) for n in ids]
    # Exercise the product field conversion too. VM alone cannot detect the
    # historical SYZ/SZX label swap because the invariant squares both shears.
    components = ('sxx', 'syy', 'szz', 'sxy', 'syz', 'sxz')
    s = np.array([[derive_stress_value(c, t) for c in components] for t in tensors], dtype=float)
    vm = np.array([derive_stress_value('von_mises', t) for t in tensors])
    assert u.shape == (len(ids), 3) and s.shape == (len(ids), 6)
    assert np.all(np.isfinite(u)) and np.all(np.isfinite(s)) and np.all(np.isfinite(vm))
    return u, s, vm


def _errors(actual, expected):
    delta = actual - expected
    scale = np.max(np.abs(expected))
    assert scale > 0
    return {'max_scaled': float(np.max(np.abs(delta)) / scale),
            'relative_l2': float(np.linalg.norm(delta) / np.linalg.norm(expected))}


def _compare(case, layer, actual, expected, record_property):
    u, s, vm = actual
    ref_u, ref_s = expected
    metrics = {'displacement': _errors(u, ref_u), 'stress_tensor': _errors(s, ref_s),
               'von_mises': _errors(vm, von_mises(ref_s)), 'nodes': len(u)}
    print(f"\nSKFEM {case['name']} {layer}: {json.dumps(metrics, sort_keys=True)}")
    record_property(f"{case['name']}_{layer}", json.dumps(metrics))
    # Measured with ccx 2.23 / scikit-fem 11.0: max-scaled U <= 2.14e-6,
    # tensor <= 1.01e-5, VM <= 1.18e-5. Bounds allow measured nodal-recovery
    # and ASCII-output differences, not percent-level mesh error:
    # these are the SAME mesh, not two differently discretized continuum models.
    for metric, tolerance in (('displacement', 1e-5), ('stress_tensor', 5e-5), ('von_mises', 5e-5)):
        for norm, error in metrics[metric].items():
            assert error < tolerance, f"{case['name']} {layer} {metric} {norm}: {error} >= {tolerance}"
    return metrics


def test_generated_deck_matches_product_frd_at_every_node(case, tmp_path, record_property):
    ref = case['reference']
    expected = ref.solve(ref.deck_rhs(), ref.deck.supports)
    _compare(case, 'deck', _product_fields(case, tmp_path), expected, record_property)


def test_topology_and_facet_traction_match_setup_intent(case, tmp_path, record_property):
    ref = case['reference']
    assert ref.deck.young == case['young'] and ref.deck.poisson == case['poisson']
    independent_supports = [item for face, dofs in case['supports'] for item in ref.face_supports(face, dofs)]
    assert set(ref.deck.supports) == set(independent_supports), 'product face binding differs from topology'
    rhs, area = ref.traction_rhs(case['load_face'], case['force'])
    assert area == pytest.approx(case['load_face']['area'], rel=1e-7)
    resultant = np.array([rhs[d].sum() for d in ref.dofs])
    np.testing.assert_allclose(resultant, case['force'], atol=1e-9)
    # Inspect the whole nodal force vector, not only its sum: the historical
    # equal-node split has exactly the correct total and the wrong traction.
    metrics = _errors(ref.deck_rhs(), rhs)
    print(f"\nSKFEM {case['name']} traction: {json.dumps(metrics)}")
    record_property('traction', json.dumps(metrics))
    # Observed max-scaled residual <= 2.34e-8; deck CLOADs use eight digits.
    assert metrics['max_scaled'] < 1e-6, f"nodal traction mismatch: {metrics}"
    assert metrics['relative_l2'] < 1e-6, f"nodal traction mismatch: {metrics}"
    expected = ref.solve(rhs, independent_supports)
    _compare(case, 'intent', _product_fields(case, tmp_path), expected, record_property)


def test_crosscheck_has_an_independent_physics_anchor(case, tmp_path, record_property):
    u, s, vm = _product_fields(case, tmp_path)
    ref = case['reference']
    xyz = np.array([ref.deck.nodes[n] for n in ref.ids])
    if case['name'] == 'tension':
        exact_stress = 2000. / (20. * 10.)
        exact_u = xyz * np.array([1., -.3, -.3]) * exact_stress / 210000.
        np.testing.assert_allclose(u, exact_u, rtol=1e-3, atol=1e-9)
        np.testing.assert_allclose(vm, exact_stress, rtol=1e-3)
    elif case['name'] == 'cantilever':
        inertia, area, length, force, young = 20. ** 4 / 12., 400., 200., 100., 210000.
        shear_modulus = young / (2 * (1 + .3))
        theory = force * length ** 3 / (3 * young * inertia) + force * length / ((5 / 6) * shear_modulus * area)
        tip = -u[np.isclose(xyz[:, 0], length), 2].mean()
        error = float(tip / theory - 1)
        print(f'\nSKFEM beam theory relative error: {error}')
        record_property('beam_theory_error', error)
        assert tip == pytest.approx(theory, rel=.02)
    else:
        # Engineering Mode geometry is the documented base_plate, four protected
        # mounting holes and named rib. A rigid underside support is an explicit
        # idealization, not a claim to model bolt preload or contact physics.
        assert case['load_face']['center'][2] == pytest.approx(33.)
        assert np.max(vm) > 0 and np.min(u[:, 2]) < 0
