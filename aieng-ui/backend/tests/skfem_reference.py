"""Test-only C3D10 oracle; deliberately imports no product FEM helpers.

PIRA: covers only the generated single-step, homogeneous isotropic small-strain
cases below, with zero supports and nodal forces; not a general Abaqus reader.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree
from skfem import Basis, ElementTetP2, ElementVector, FacetBasis, LinearForm, MeshTet2, asm, condense, solve
from skfem.helpers import dot, sym_grad
from skfem.models.elasticity import linear_elasticity


@dataclass
class Deck:
    nodes: dict[int, list[float]]
    elements: list[list[int]]
    sets: dict[str, list[int]]
    supports: list[tuple[int, int]]
    forces: list[tuple[int, int, float]]
    young: float
    poisson: float


def read_deck(text: str) -> Deck:
    """Read the narrow, self-contained product-generated static deck contract."""
    nodes, elements, sets, boundaries, loads, elastic = {}, [], {}, [], [], []
    keyword, params, pending = '', {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('**'):
            continue
        if line.startswith('*'):
            assert not pending, 'unterminated element connectivity'
            fields = [f.strip().upper() for f in line.split(',')]
            keyword = fields[0]
            params = dict(f.split('=', 1) for f in fields[1:] if '=' in f)
            assert keyword not in {'*INCLUDE', '*DLOAD', '*MPC', '*EQUATION', '*TEMPERATURE'}, keyword
            if keyword == '*ELEMENT':
                assert params.get('TYPE') == 'C3D10', params
            if keyword == '*NSET':
                assert 'GENERATE' not in fields, 'explicit node lists expected'
                sets.setdefault(params['NSET'], [])
            continue
        row = [f.strip() for f in line.split(',') if f.strip()]
        if keyword == '*NODE':
            assert len(row) == 4
            nodes[int(row[0])] = [float(f) for f in row[1:]]
        elif keyword == '*ELEMENT':
            pending.extend(int(f) for f in row)
            if not line.endswith(','):
                assert len(pending) == 11, pending
                elements.append(pending[1:])
                pending = []
        elif keyword == '*NSET':
            sets[params['NSET']].extend(int(f) for f in row)
        elif keyword == '*BOUNDARY':
            assert float(row[3]) == 0, 'only homogeneous supports are covered'
            boundaries.append((row[0], int(row[1]), int(row[2])))
        elif keyword == '*CLOAD':
            loads.append((row[0], int(row[1]), float(row[2])))
        elif keyword == '*ELASTIC':
            elastic.append(tuple(float(f) for f in row))
    assert nodes and elements and len(elastic) == 1

    def members(target: str) -> list[int]:
        return [int(target)] if target.isdecimal() else sets[target.upper()]

    supports = [(n, d - 1) for target, first, last in boundaries
                for n in members(target) for d in range(first, last + 1)]
    forces = [(n, d - 1, value) for target, d, value in loads for n in members(target)]
    assert all(0 <= d < 3 for _, d in supports)
    assert all(0 <= d < 3 for _, d, _ in forces)
    return Deck(nodes, elements, sets, supports, forces, *elastic[0])


class Reference:
    """Quadratic vector elasticity with independent assembly and nodal recovery."""

    def __init__(self, deck: Deck, young: float, poisson: float):
        self.deck = deck
        ids = np.array(sorted(deck.nodes))
        points = np.array([deck.nodes[n] for n in ids])
        lookup = {n: i for i, n in enumerate(ids)}
        connectivity = np.array([[lookup[n] for n in e] for e in deck.elements]).T
        # Abaqus C3D10 and ElementTetP2 share edges 01,12,20,03,13,23.
        self.mesh = MeshTet2(points.T, connectivity)
        self.basis = Basis(self.mesh, ElementVector(ElementTetP2()), intorder=2)
        scalar = Basis(self.mesh, ElementTetP2(), intorder=2)
        distances, original = cKDTree(points).query(scalar.doflocs.T)
        assert np.max(distances) < 1e-8
        assert len(set(original)) == len(ids), 'mesh node mapping must be bijective'
        self.ids = ids[original]
        self.node_index = {n: i for i, n in enumerate(self.ids)}
        self.dofs = self.basis.split_indices()
        self.lam = young * poisson / ((1 + poisson) * (1 - 2 * poisson))
        self.mu = young / (2 * (1 + poisson))
        self.stiffness = asm(linear_elasticity(self.lam, self.mu), self.basis)

    def support_dofs(self, supports: list[tuple[int, int]]) -> np.ndarray:
        return np.unique([self.dofs[d][self.node_index[n]] for n, d in supports])

    def deck_rhs(self) -> np.ndarray:
        rhs = np.zeros(self.basis.N)
        for n, d, force in self.deck.forces:
            rhs[self.dofs[d][self.node_index[n]]] += force
        return rhs

    def planar_facets(self, face: dict) -> np.ndarray:
        """Select boundary facets geometrically, never from a product node set."""
        assert face['surface_type'] == 'plane'
        normal, center = np.array(face['normal']), np.array(face['center'])
        bounds = np.array(face['bounding_box'])
        candidates = self.mesh.boundary_facets()
        # Include all six P2 nodes, not only the corners or the face centroid.
        selected = []
        for facet in candidates:
            indices = self.mesh.dofs.get_facet_dofs(np.array([facet])).all()
            xyz = self.mesh.doflocs[:, indices].T
            if (np.max(np.abs((xyz - center) @ normal)) < 1e-6
                    and np.all(xyz >= bounds[:3] - 1e-6)
                    and np.all(xyz <= bounds[3:] + 1e-6)):
                selected.append(facet)
        assert selected, face
        return np.array(selected)

    def face_supports(self, face: dict, components: tuple[int, ...]) -> list[tuple[int, int]]:
        facets = self.planar_facets(face)
        indices = self.mesh.dofs.get_facet_dofs(facets).all()
        return [(int(self.ids[i]), d) for i in indices for d in components]

    def traction_rhs(self, face: dict, force: np.ndarray) -> tuple[np.ndarray, float]:
        fb = FacetBasis(self.mesh, self.basis.elem, facets=self.planar_facets(face), intorder=4)
        area = float(np.sum(fb.dx))
        traction = force / area

        @LinearForm
        def loading(v, w):
            return dot(traction, v)

        return asm(loading, fb), area

    def solve(self, rhs: np.ndarray, supports: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
        u = solve(*condense(self.stiffness, rhs, D=self.support_dofs(supports)))
        assert np.all(np.isfinite(u))
        displacement = np.array([u[d] for d in self.dofs]).T
        return displacement, self.nodal_stress(u)

    def nodal_stress(self, u: np.ndarray) -> np.ndarray:
        # Four-point C3D10 quadrature. Linear extrapolation of the six stress
        # components to corners, then edge means; average adjacent elements
        # BEFORE deriving von Mises, as ccx's nodal output does.
        strain = sym_grad(self.basis.interpolate(u))  # (3,3,element,quadrature)
        stress = 2 * self.mu * strain
        trace = np.einsum('iieq->eq', strain)
        for d in range(3):
            stress[d, d] += self.lam * trace
        components = stress[(0, 1, 2, 0, 1, 0), (0, 1, 2, 1, 2, 2)]
        q = self.basis.X.T
        interpolation = np.column_stack([np.ones(len(q)), q])
        locations = ElementTetP2().doflocs
        extrapolation = np.column_stack([np.ones(10), locations]) @ np.linalg.inv(interpolation)
        local = np.einsum('nq,ceq->enc', extrapolation, components)
        scalar = Basis(self.mesh, ElementTetP2(), intorder=2)
        total = np.zeros((len(self.ids), 6))
        count = np.zeros(len(self.ids))
        for local_node in range(10):
            indices = scalar.element_dofs[local_node]
            np.add.at(total, indices, local[:, local_node])
            np.add.at(count, indices, 1)
        assert np.all(count > 0)
        return total / count[:, None]


def von_mises(stress: np.ndarray) -> np.ndarray:
    """Deviatoric tensor norm, independent of the product scalar formula."""
    normal = stress[:, :3]
    deviator = normal - normal.mean(axis=1, keepdims=True)
    return np.sqrt(1.5 * ((deviator ** 2).sum(axis=1) + 2 * (stress[:, 3:] ** 2).sum(axis=1)))
