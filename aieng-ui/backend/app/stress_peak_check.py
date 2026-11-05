"""Is a reported peak stress a singularity?

Linear elasticity has no finite stress at a sharp re-entrant (concave) corner
or at the edge of a fully fixed face: refining the mesh there raises the nodal
peak without limit. A number read off such a node is not a result, however
converged the displacement next to it is. Measured on the canonical bracket (a
rib on a plate, bolt holes fixed): the peak von Mises sat 0.75 mm from the
rib-to-plate junction and rose 11.1 -> 12.9 -> 14.8 -> 17.6 MPa over four mesh
sizes, while the displacement agreed to 0.2 %. Agents without the workbench
found that with a three-mesh study; this says it from the geometry, at no solve.

It flags; it does not certify. A peak away from both causes may still need
`cae.mesh_convergence` to be trusted.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

Point = tuple[float, float, float]


def concave_edge_samples(step_path: str | Path, spacing_mm: float) -> list[Point]:
    """Points at most `spacing_mm` apart along every sharp concave edge of the model.

    Separate solids are fused first, so the re-entrant corner where a rib meets
    a plate is an edge of the result. A fillet meets its faces tangentially and
    a hole rim is convex: neither is returned.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepOffset import BRepOffset_Analyse
    from OCP.ChFiDS import ChFiDS_Concave
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_ListOfShape

    reader = STEPControl_Reader()
    if reader.ReadFile(str(step_path)) != IFSelect_RetDone:
        raise ValueError(f"cannot read STEP: {step_path}")
    reader.TransferRoots()
    shape = reader.OneShape()

    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    if len(solids) > 1:
        arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
        arguments.Append(solids[0])
        for solid in solids[1:]:
            tools.Append(solid)
        fuse = BRepAlgoAPI_Fuse()
        fuse.SetArguments(arguments)
        fuse.SetTools(tools)
        fuse.Build()
        if fuse.IsDone():
            fuse.SimplifyResult()
            shape = fuse.Shape()

    analysis = BRepOffset_Analyse(shape, 0.01)  # radians: below this an edge is tangent
    samples: list[Point] = []
    seen: set[int] = set()
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        explorer.Next()
        if hash(edge) in seen:  # each edge is visited once per adjacent face
            continue
        seen.add(hash(edge))
        if not any(interval.Type() == ChFiDS_Concave for interval in analysis.Type(edge)):
            continue
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        length = GCPnts_AbscissaPoint.Length_s(curve)
        count = max(2, math.ceil(length / spacing_mm) + 1)
        for i in range(count):
            p = curve.Value(first + (last - first) * i / (count - 1))
            samples.append((p.X(), p.Y(), p.Z()))
    return samples


def judge_stress_peak(
    peak: dict[str, Any],
    *,
    concave_samples: Iterable[Point] | None,
    element_size_mm: float | None,
    restraint_id: str | None,
) -> dict[str, Any]:
    """Name what makes the peak at `peak` ({x, y, z}) singular, if anything.

    `concave_samples` None means the edges could not be examined, which is said
    rather than read as "no sharp edge". `restraint_id` is the fully fixed
    boundary condition whose node set holds the peak node, if any.
    """
    point = (float(peak["x"]), float(peak["y"]), float(peak["z"]))
    causes: list[str] = []
    notes: list[str] = []
    distance = None
    if concave_samples is not None and element_size_mm:
        distance = min((math.dist(point, s) for s in concave_samples), default=None)
        if distance is not None and distance <= element_size_mm:
            causes.append("concave_edge")
            notes.append(
                f"it sits {distance:.2f} mm from a sharp concave edge (a re-entrant corner, "
                f"within one {element_size_mm:g} mm element), where linear-elastic stress has no "
                "finite value; a fillet there would make it converge"
            )
    if restraint_id:
        causes.append("restrained_face")
        notes.append(
            f"it sits on the fully fixed face of {restraint_id}, and a rigid restraint makes the "
            "stress at the edge of that face singular"
        )
    verdict: dict[str, Any] = {
        "location": {k: round(v, 4) for k, v in zip("xyz", point)},
        "singular": bool(causes),
        "causes": causes,
    }
    if distance is not None:
        verdict["distance_to_concave_edge_mm"] = round(distance, 3)
    if causes:
        verdict["note"] = (
            "Do not quote this peak as a converged stress: " + "; ".join(notes)
            + ". Refining the mesh raises it without limit. Quote the displacement, a stress "
            "away from the singular point, or change the model (fillet, realistic support)."
        )
    elif concave_samples is None:
        verdict["note"] = ("The sharp edges could not be examined, so whether this peak is a "
                           "singularity is unknown; cae.mesh_convergence answers it.")
    else:
        verdict["note"] = ("No sharp concave edge or fixed face at this peak, so it can converge; "
                           "only cae.mesh_convergence establishes that it has.")
    return verdict
