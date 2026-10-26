"""Contract tests for the build123d helpers injected into the CAD runner.

Each helper documents dimensions and a placement in AGENTS.md; these tests hold
the built solid to them against closed forms (volumes, across-corners, wall
thickness) and the NACA 4-digit thickness equation, and pin that impossible
inputs are refused by parameter name and that any substitution a helper makes
is REPORTED (``helper_adjustments``) rather than silently shipped.

The helpers are exec'd in-process from the runner template, so these run in
milliseconds instead of one subprocess per case; a handful of tests go through
``execute_build123d_code`` to pin the reporting channel and the STEP output.
"""
from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("build123d")

from app.config import Settings  # noqa: E402

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def h() -> dict:
    """The runner's helper namespace (everything up to the excepthook install)."""
    from app.cad_generation import _RUNNER_TEMPLATE

    start = _RUNNER_TEMPLATE.index("# ── aieng high-level modelling helpers")
    end = _RUNNER_TEMPLATE.index("_AIENG_PREV_EXCEPTHOOK = sys.excepthook")
    ns: dict = {}
    exec("import sys\nfrom build123d import *\n" + _RUNNER_TEMPLATE[start:end], ns)
    return ns


@pytest.fixture()
def adjustments(h: dict) -> list:
    h["_AIENG_HELPER_ADJUSTMENTS"].clear()
    return h["_AIENG_HELPER_ADJUSTMENTS"]


def _valid_single_solid(part) -> None:
    from OCP.BRepCheck import BRepCheck_Analyzer

    assert BRepCheck_Analyzer(part.wrapped).IsValid()
    assert len(part.solids()) == 1
    assert part.volume > 0


def _size(part) -> tuple[float, float, float]:
    bb = part.bounding_box()
    return (bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z)


# -- closed-form dimensions ----------------------------------------------------

def test_tube_volume_is_annulus_times_length(h: dict) -> None:
    part = h["tube"](12, 8, 40)
    _valid_single_solid(part)
    assert part.volume == pytest.approx(math.pi * (12**2 - 8**2) * 40, rel=1e-9)
    assert _size(part) == pytest.approx((24, 24, 40), abs=1e-6)


def test_tube_axis_x_runs_length_along_x(h: dict) -> None:
    assert _size(h["tube"](10, 6, 30, axis="X")) == pytest.approx((30, 20, 20), abs=1e-6)


def test_hex_prism_across_flats_and_corners(h: dict) -> None:
    af, height = 13.0, 8.0
    part = h["hex_prism"](af, height)
    _valid_single_solid(part)
    sx, sy, sz = _size(part)
    # across-corners = AF * 2/sqrt(3); flats are perpendicular to Y here
    assert sy == pytest.approx(af, abs=1e-6)
    assert sx == pytest.approx(af * 2 / math.sqrt(3), abs=1e-6)
    assert sz == pytest.approx(height, abs=1e-6)
    # regular hexagon area = (sqrt(3)/2) * AF^2
    assert part.volume == pytest.approx(math.sqrt(3) / 2 * af**2 * height, rel=1e-9)


def test_wheel_outer_radius_is_rim_plus_tire(h: dict) -> None:
    part = h["wheel"](30, 8, 20)
    _valid_single_solid(part)
    assert _size(part) == pytest.approx((76, 76, 20), abs=1e-6)
    bore = 0.3 * 30  # documented: bore = 30% of rim_radius
    assert part.volume == pytest.approx(math.pi * (38**2 - bore**2) * 20, rel=1e-9)


def test_boss_bore_diameter(h: dict) -> None:
    part = h["boss"](20, 10, hole_dia=8)
    _valid_single_solid(part)
    assert part.volume == pytest.approx(math.pi * (10**2 - 4**2) * 10, rel=1e-9)
    bb = part.bounding_box()
    assert bb.min.Z == pytest.approx(0, abs=1e-6)  # base at the origin


def test_rib_legs_and_volume(h: dict) -> None:
    part = h["rib"](40, 30, 5)
    _valid_single_solid(part)
    bb = part.bounding_box()
    assert (bb.min.X, bb.max.X, bb.min.Z, bb.max.Z) == pytest.approx((0, 40, 0, 30), abs=1e-6)
    assert (bb.min.Y, bb.max.Y) == pytest.approx((-2.5, 2.5), abs=1e-6)
    assert part.volume == pytest.approx(0.5 * 40 * 30 * 5, rel=1e-9)


def test_capsule_volume(h: dict) -> None:
    part = h["capsule"](5, 20)
    assert part.volume == pytest.approx(math.pi * 25 * 20 + 4 / 3 * math.pi * 125, rel=1e-9)
    assert _size(part) == pytest.approx((10, 10, 30), abs=1e-6)


def test_l_bracket_volume(h: dict) -> None:
    part = h["l_bracket"](80, 40, 60, 6)
    _valid_single_solid(part)
    assert part.volume == pytest.approx((80 * 6 + 6 * (60 - 6)) * 40, rel=1e-9)


def test_tapered_cylinder_with_equal_radii_is_a_cylinder(h: dict) -> None:
    # OCCT raises "cone with two identic radii"; the shape asked for exists.
    part = h["tapered_cylinder"](5, 5, 10)
    assert part.volume == pytest.approx(math.pi * 25 * 10, rel=1e-9)


@pytest.mark.parametrize("args", [(100, 60, 40, 3), (120, 80, 50, 4), (60, 40, 30, 2.5)])
def test_housing_wall_is_wall_thick_everywhere_including_corners(h: dict, args) -> None:
    """Rounded outer corners over a SHARP cavity corner thinned the wall to
    fr - sqrt(2)*(fr - wall): 1.76 mm for the default 3 mm wall."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    L, W, H, t = args
    part = h["housing"](L, W, H, wall=t)
    _valid_single_solid(part)
    eps = 1e-6

    def _vertical(face) -> bool:
        bb = face.bounding_box()
        return bb.max.Z - bb.min.Z > eps and abs(face.normal_at().Z) < 1e-6

    def _max_abs(face) -> tuple[float, float]:
        bb = face.bounding_box()
        return max(abs(bb.min.X), abs(bb.max.X)), max(abs(bb.min.Y), abs(bb.max.Y))

    inner = [f for f in part.faces() if _vertical(f)
             and _max_abs(f)[0] <= L / 2 - t + eps and _max_abs(f)[1] <= W / 2 - t + eps]
    outer = [f for f in part.faces() if _vertical(f)
             and (_max_abs(f)[0] >= L / 2 - eps or _max_abs(f)[1] >= W / 2 - eps)]
    assert inner and outer
    from build123d import Compound

    dist = BRepExtrema_DistShapeShape(Compound(children=inner).wrapped, Compound(children=outer).wrapped)
    dist.Perform()
    assert dist.Value() == pytest.approx(t, abs=1e-6)

    # Closed form: outer and cavity are rounded rectangles (corner r and r - t),
    # open top, floor of thickness t.
    fr = min(2 * t, 0.12 * min(L, W))
    outer_area = L * W - (4 - math.pi) * fr**2
    inner_area = (L - 2 * t) * (W - 2 * t) - (4 - math.pi) * (fr - t) ** 2
    assert part.volume == pytest.approx(outer_area * H - inner_area * (H - t), rel=1e-9)


# -- NACA 4-digit ----------------------------------------------------------------

def _naca_half_thickness(x: float, thickness: float) -> float:
    # Abbott & von Doenhoff (1959); same equation in NASA TM-4741 (Ladson et al.
    # 1996). yt/c = 5 (t/c) f(x), so in mm with T = t: yt = 5 T f(x).
    return 5 * thickness * (
        0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1015 * x**4
    )


def test_naca_thickness_distribution_matches_the_equation(h: dict) -> None:
    from build123d import Box, Location

    chord, thickness, span = 100.0, 12.0, 50.0
    wing = h["naca_airfoil"](chord, thickness, span=span)
    _valid_single_solid(wing)
    for x in (0.005, 0.05, 0.3, 0.6, 0.95):
        slab = wing & Box(1e-3, 3 * span, 3 * thickness).moved(Location((x * chord, span / 2, 0)))
        bb = slab.bounding_box()
        expected = 2 * _naca_half_thickness(x, thickness)
        assert bb.max.Z - bb.min.Z == pytest.approx(expected, rel=1e-3), x
        assert bb.max.Z == pytest.approx(-bb.min.Z, abs=1e-6)  # symmetric section
    # Max thickness is at x ~ 0.3c and equals t (the polynomial gives 1.0003 t).
    assert 2 * _naca_half_thickness(0.3, thickness) == pytest.approx(thickness, rel=5e-4)


def test_naca_section_area_and_placement(h: dict) -> None:
    chord, thickness, span = 100.0, 12.0, 50.0
    wing = h["naca_airfoil"](chord, thickness, span=span)
    # Area = 2 * integral_0^c yt dX = 10 T c * F, F = integral_0^1 f(x) dx
    #      = 0.2969*2/3 - 0.1260/2 - 0.3516/3 + 0.2843/4 - 0.1015/5 = 0.068508
    f_integral = 0.2969 * 2 / 3 - 0.1260 / 2 - 0.3516 / 3 + 0.2843 / 4 - 0.1015 / 5
    assert wing.volume / span == pytest.approx(10 * thickness * chord * f_integral, rel=1e-3)
    bb = wing.bounding_box()
    # Documented: chord along +X from the leading edge at X=0, span along +Y
    # from Y=0. It used to extrude along -Y, which put the airplane template's
    # "centred" wing entirely on one side of the fuselage.
    # (the natural-end spline overshoots the nose by ~6e-5 mm)
    assert (bb.min.X, bb.max.X) == pytest.approx((0, chord), abs=1e-4)
    assert (bb.min.Y, bb.max.Y) == pytest.approx((0, span), abs=1e-5)
    # open trailing edge: flat of height 2*yt(1) = 0.021 T
    te_faces = [f for f in wing.faces() if f.bounding_box().min.X > chord - 1e-6]
    assert len(te_faces) == 1
    te_bb = te_faces[0].bounding_box()
    assert te_bb.max.Z - te_bb.min.Z == pytest.approx(0.021 * thickness, rel=1e-6)


# -- every helper builds a valid single solid ----------------------------------

@pytest.mark.parametrize("call", [
    "lofted_stack([(0, 10), (20, 15), (40, 24, 16)])",
    "rounded_box(40, 30, 20, 3)",
    "rounded_box(100, 100, 10, 20, edges='vertical')",
    "capsule(5, 20, axis='X')",
    "tapered_cylinder(5, 8, 40, axis='Y')",
    "swept_tube([(0, 0, 0), (0, 20, 50), (0, 40, 100)], 5)",
    "revolved_profile([(10, 0), (12, 20), (8, 40)])",
    "organic_blend([Box(20, 20, 20), Box(10, 10, 40)], 3)",
    "naca_airfoil(120, 14, span=200)",
    "fuselage_profile(400, 60)",
    "wheel(30, 8, 20)",
    "ribbed_plate(120, 80, 6, rib_count=3)",
    "tube(12, 8, 40)",
    "hex_prism(13, 8)",
    "chamfered_box(60, 40, 20, 4)",
    "l_bracket(80, 40, 60, 6, fillet_radius=4)",
    "housing(100, 60, 40, wall=3, open_top=False)",
    "boss(20, 10, hole_dia=8, axis='X')",
    "rib(40, 30, 5, fillet_radius=1)",
    "mounting_tab(30, 20, 5, 6.6)",
])
def test_helper_builds_valid_single_solid(h: dict, call: str) -> None:
    _valid_single_solid(eval(call, h))


# -- impossible inputs are refused by name -------------------------------------

@pytest.mark.parametrize("call,param", [
    ("tube(12, 8, 0)", "length"),          # built a zero-volume, INVALID solid
    ("tube(12, 8, -10)", "length"),        # raised a bare Standard_ConstructionError
    ("hex_prism(13, -5)", "height"),       # silently built downward
    ("hex_prism(13, 0)", "height"),
    ("wheel(30, -40, 20)", "tire_radius"),
    ("wheel(30, 8, 0)", "width"),          # zero-volume invalid solid
    ("boss(20, 10, hole_dia=-3)", "hole_dia"),  # silently built without a bore
    ("capsule(0, 20)", "radius"),          # zero-volume invalid compound
    ("rounded_box(-40, 30, 20, 3)", "length"),
    ("naca_airfoil(100, 150)", "thickness"),    # built a 150 mm thick "airfoil"
    ("naca_airfoil(-100, 12)", "chord"),        # silently mirrored
    ("naca_airfoil(100, 0)", "thickness"),
    ("mounting_tab(30, 20, 5, 25)", "hole_dia"),  # silently dropped the bolt hole
    ("ribbed_plate(120, -80, 6)", "width"),
    ("lofted_stack([(0, -10), (20, 10)])", "radius"),
    ("lofted_stack([(20, 10), (0, 10)])", "z values"),
    ("tapered_cylinder(-5, 8, 40)", "bottom_radius"),
    ("swept_tube([(0, 0, 0), (0, 0, 50)], 0)", "radius"),
    ("revolved_profile([(-10, 0), (10, 20)])", "r"),
    ("fuselage_profile(-400, 60)", "length"),
    ("fuselage_profile(400, 60, nose_frac=0.7, tail_frac=0.7)", "nose_frac + tail_frac"),
    ("organic_blend([Box(10, 10, 10)], 0)", "radius"),
    ("l_bracket(80, 40, 60, 6, fillet_radius=-1)", "fillet_radius"),
    ("rib(40, 30, 5, fillet_radius=-1)", "fillet_radius"),  # silently built sharp
    ("rib(float('nan'), 30, 5)", "length"),                 # passed the `<= 0` check
    ("rib(40, 30, 0)", "thickness"),                        # message named no parameter
])
def test_invalid_input_is_refused_naming_the_parameter(h: dict, call: str, param: str) -> None:
    with pytest.raises(ValueError, match=param.replace("+", r"\+")):
        eval(call, h)


# -- substitutions are reported, not silent ------------------------------------

def test_organic_blend_reports_an_unfilleted_join(h: dict, adjustments: list) -> None:
    from build123d import Box

    part = h["organic_blend"]([Box(20, 20, 20), Box(10, 10, 40)], 50, label="body")
    assert part.volume == pytest.approx(20**3 + 10 * 10 * 20, rel=1e-9)  # no fillet at all
    assert adjustments == [{
        "helper": "organic_blend", "label": "body", "parameter": "radius",
        "requested": 50.0, "applied": None,
        "reason": adjustments[0]["reason"],
    }]
    assert "UNFILLETED" in adjustments[0]["reason"]


def test_feasible_requests_record_no_adjustment(h: dict, adjustments: list) -> None:
    from build123d import Box

    h["organic_blend"]([Box(20, 20, 20), Box(10, 10, 40)], 1)
    h["rounded_box"](40, 30, 20, 3)
    h["housing"](100, 60, 40, wall=3, fillet_radius=5)
    h["l_bracket"](80, 40, 60, 6, fillet_radius=4)
    assert adjustments == []


@pytest.mark.parametrize("call,helper,param", [
    ("rounded_box(40, 30, 20, 50)", "rounded_box", "radius"),
    ("chamfered_box(40, 30, 20, 50)", "chamfered_box", "chamfer_size"),
    ("housing(100, 60, 40, wall=3, fillet_radius=100)", "housing", "fillet_radius"),
    ("l_bracket(80, 40, 60, 6, fillet_radius=100)", "l_bracket", "fillet_radius"),
    ("lofted_stack([(0, 20, 10, 50), (20, 20, 10)])", "lofted_stack", "section[0] corner_r"),
])
def test_clamped_requests_are_reported(h: dict, adjustments: list, call: str, helper: str, param: str) -> None:
    eval(call, h)
    assert [(a["helper"], a["parameter"]) for a in adjustments] == [(helper, param)]
    assert adjustments[0]["applied"] < adjustments[0]["requested"]


def test_vertical_rounding_is_not_capped_by_height(h: dict, adjustments: list) -> None:
    # Vertical edges are as long as the height; their radius is limited by the
    # plan size only. It used to be clamped to height/2 (4.99 here).
    part = h["rounded_box"](100, 100, 10, 20, edges="vertical")
    assert part.volume == pytest.approx((100 * 100 - (4 - math.pi) * 20**2) * 10, rel=1e-9)
    assert adjustments == []


# -- positioning helpers are exact ---------------------------------------------

def test_stack_on_and_coaxial_are_exact(h: dict) -> None:
    from build123d import Box, Cylinder, Location

    base = Box(40, 30, 10).moved(Location((5, -3, 7)))
    cover = h["stack_on"](Box(20, 20, 4), base, gap=0.5)
    bb, rb = cover.bounding_box(), base.bounding_box()
    assert bb.min.Z == pytest.approx(rb.max.Z + 0.5, abs=1e-9)
    assert ((bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2) == pytest.approx((5, -3), abs=1e-9)

    shaft = h["coaxial"](Cylinder(3, 50).moved(Location((9, 9, 100))), base, axis="Z")
    sb = shaft.bounding_box()
    assert ((sb.min.X + sb.max.X) / 2, (sb.min.Y + sb.max.Y) / 2) == pytest.approx((5, -3), abs=1e-9)
    assert (sb.min.Z + sb.max.Z) / 2 == pytest.approx(100, abs=1e-9)  # position along axis kept

    moved = h["offset_from"](Box(2, 2, 2), base, dx=1, dy=2, dz=3)
    mb = moved.bounding_box()
    assert ((mb.min.X + mb.max.X) / 2, (mb.min.Y + mb.max.Y) / 2, (mb.min.Z + mb.max.Z) / 2) == \
        pytest.approx((6, -1, 10), abs=1e-9)


# -- geometry_report: symmetry / floating ---------------------------------------

def _box(name: str, cx: float, cy: float, cz: float, sx: float, sy: float, sz: float) -> dict:
    return {"type": "solid", "id": name, "name": name,
            "bounding_box": [cx - sx / 2, cy - sy / 2, cz - sz / 2, cx + sx / 2, cy + sy / 2, cz + sz / 2]}


def _report(entities: list) -> dict:
    from app.cad_generation import _compute_geometry_report

    return _compute_geometry_report({"entities": entities})


@pytest.mark.parametrize("scale", [0.1, 1.0, 10.0])
def test_correct_mirror_pair_is_symmetric_at_any_scale(scale: float) -> None:
    s = scale
    rep = _report([_box("torso", 0, 0, 0, 40 * s, 20 * s, 60 * s),
                   _box("arm_L", -25 * s, 0, 0, 10 * s, 10 * s, 40 * s),
                   _box("arm_R", 25 * s, 0, 0, 10 * s, 10 * s, 40 * s)])
    assert [item["ok"] for item in rep["symmetry"]] == [True]
    assert rep["gaps_summary"]["touching"] == 3


@pytest.mark.parametrize("scale", [0.1, 1.0, 10.0])
def test_coincident_left_right_pair_is_not_symmetric(scale: float) -> None:
    """The copy-paste typo (side coordinate never negated) left both parts in
    one place: align_residual 0 on every axis, which used to read ok:true."""
    s = scale
    rep = _report([_box("torso", 0, 0, 0, 40 * s, 20 * s, 60 * s),
                   _box("arm_L", -25 * s, 0, 0, 10 * s, 10 * s, 40 * s),
                   _box("arm_R", -25 * s, 0, 0, 10 * s, 10 * s, 40 * s)])
    (item,) = rep["symmetry"]
    assert item["ok"] is False
    assert item["status"] == "coincident"


def test_small_asymmetry_on_a_small_model_is_caught() -> None:
    # 0.8 mm length mismatch on 5 mm parts (16%) passed under the old 1 mm floor.
    rep = _report([_box("hub", 0, 0, 0, 4, 4, 4),
                   _box("ear_L", -4, 0, 0, 4, 4, 5),
                   _box("ear_R", 4, 0, 0, 4, 4, 5.8)])
    assert rep["symmetry"][0]["ok"] is False


def test_pair_size_mismatch_is_judged_against_the_pair_not_the_model() -> None:
    # A 10 mm / 20 mm "mirror" pair beside a 1 m body: 2% of the LARGEST part
    # (20 mm) used to swallow a 100% size mismatch.
    rep = _report([_box("frame", 0, 0, 0, 1000, 200, 200),
                   _box("lamp_L", -40, 0, 105, 10, 10, 10),
                   _box("lamp_R", 40, 0, 105, 20, 10, 10)])
    (item,) = rep["symmetry"]
    assert item["ok"] is False
    assert item.get("status") != "coincident"


def test_small_pair_near_the_centreline_is_not_coincident() -> None:
    # Two 10 mm parts 15 mm apart on a 1 m model are a real mirror pair; 2% of
    # the MODEL (20 mm) used to call them "coincident".
    rep = _report([_box("frame", 0, 0, 0, 1000, 200, 200),
                   _box("lamp_L", -7.5, 0, 105, 10, 10, 10),
                   _box("lamp_R", 7.5, 0, 105, 10, 10, 10)])
    (item,) = rep["symmetry"]
    assert item["ok"] is True
    assert "status" not in item


@pytest.mark.parametrize("scale", [0.1, 1.0, 10.0])
def test_floating_verdict_does_not_depend_on_unit_scale(scale: float) -> None:
    """A knob 35 units above a 40-unit base: was `near` at 1x and 0.1x but
    `floating` at 10x, because of an absolute 50 mm floor."""
    s = scale
    rep = _report([_box("base", 0, 0, 0, 40 * s, 40 * s, 10 * s),
                   _box("knob", 0, 0, 45 * s, 10 * s, 10 * s, 10 * s)])
    assert rep["gaps_summary"]["floating"] == 2
    assert rep["floating_parts"] == ["base", "knob"]


def test_hairline_gap_is_detached_not_touching() -> None:
    rep = _report([_box("base", 0, 0, 0, 40, 40, 10),
                   _box("knob", 0, 0, 10.01, 10, 10, 10)])
    assert {g["status"] for g in rep["gaps"]} == {"near"}
    rep = _report([_box("base", 0, 0, 0, 40, 40, 10),
                   _box("knob", 0, 0, 10.0, 10, 10, 10)])
    assert {g["status"] for g in rep["gaps"]} == {"touching"}


# -- through the product path ---------------------------------------------------

def _settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )


def _project(settings: Settings, name: str) -> str:
    from app.main import default_project, save_project

    return save_project(settings, default_project(name))["id"]


def _package_member(settings: Settings, project_id: str, member: str) -> bytes:
    from app.main import project_dir
    from app.project_io import get_project

    project = get_project(settings, project_id)
    with zipfile.ZipFile(project_dir(settings, project_id) / project["aieng_file"]) as zf:
        return zf.read(member)


def test_execute_response_surfaces_helper_adjustments(tmp_path: Path, monkeypatch) -> None:
    from app.cad_generation import execute_build123d_code

    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    pid = _project(settings, "blend")
    code = ("from build123d import *\n"
            "result = organic_blend([Box(20, 20, 20), Box(10, 10, 40)], 50, label='body')\n")
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    assert [(a["helper"], a["applied"]) for a in out["helper_adjustments"]] == [("organic_blend", None)]

    clean = execute_build123d_code(settings, _project(settings, "plain"), {
        "code": "from build123d import *\nresult = tube(12, 8, 40, label='t')\n", "thumbnail": False})
    assert "helper_adjustments" not in clean


def test_build_cache_key_changes_with_the_runner_template(monkeypatch) -> None:
    """Helpers live in the runner template: a cache keyed on user code alone
    kept serving geometry built by a superseded helper."""
    import app.cad_generation as cg

    before, _ = cg._build123d_cache_key(code="result = housing(100, 60, 40)", mode="replace", model_kind="auto")
    monkeypatch.setattr(cg, "_RUNNER_TEMPLATE_SHA256", "0" * 64)
    after, _ = cg._build123d_cache_key(code="result = housing(100, 60, 40)", mode="replace", model_kind="auto")
    assert before != after


def test_step_round_trip_keeps_labels_colours_and_volumes(tmp_path: Path, monkeypatch) -> None:
    """A representative helper-built, multi-part, labelled model: the package's
    STEP re-imports with every part name, its colour, and its volume as the
    topology map recorded it. Same-kernel check (see test_step_interop.py)."""
    from build123d import import_step

    from app.cad_generation import execute_build123d_code

    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    pid = _project(settings, "roundtrip")
    code = (
        "from build123d import *\n"
        "body = housing(120, 80, 50, wall=4, label='housing', color=(0.55, 0.62, 0.70))\n"
        "seat = stack_on(boss(30, 8, hole_dia=16), body, label='bearing_boss', color=(0.85, 0.2, 0.2))\n"
        "foot = mounting_tab(30, 20, 5, 6.6).moved(Location((-75, 0, 0)))\n"
        "foot.label = 'mounting_tab_L'; foot.color = Color(0.2, 0.3, 0.65)\n"
        "foot_r = mirror(foot, about=Plane.YZ); foot_r.label = 'mounting_tab_R'; foot_r.color = Color(0.2, 0.3, 0.65)\n"
        "result = Compound(children=[body, seat, foot, foot_r])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    expected_colours = {
        "housing": (0.55, 0.62, 0.70), "bearing_boss": (0.85, 0.2, 0.2),
        "mounting_tab_L": (0.2, 0.3, 0.65), "mounting_tab_R": (0.2, 0.3, 0.65),
    }
    assert sorted(out["named_parts"]) == sorted(expected_colours)
    assert out["geometry_report"]["gaps_summary"]["floating"] == 0
    assert all(item["ok"] for item in out["geometry_report"]["symmetry"])

    topo = json.loads(_package_member(settings, pid, "geometry/topology_map.json"))
    topo_volumes = {e["name"]: e["volume"] for e in topo["entities"] if e["type"] == "solid" and e.get("name")}
    step_path = tmp_path / "generated.step"
    step_path.write_bytes(_package_member(settings, pid, "geometry/generated.step"))
    back = import_step(str(step_path))

    by_label = {child.label: child for child in back.children}
    assert set(by_label) == set(expected_colours)
    for label, rgb in expected_colours.items():
        part = by_label[label]
        assert part.volume == pytest.approx(topo_volumes[label], rel=1e-6)
        leaves, stack = [], [part]
        while stack:  # colour sits on the leaf node of the imported assembly tree
            node = stack.pop()
            if node.children:
                stack.extend(node.children)
            else:
                leaves.append(node)
        assert leaves and all(leaf.color is not None for leaf in leaves), label
        for leaf in leaves:  # STEP stores colour as float32
            assert tuple(leaf.color)[:3] == pytest.approx(rgb, abs=1e-6), label


def test_airplane_template_wing_is_centred_and_fin_points_up(tmp_path: Path, monkeypatch) -> None:
    """The starter template centres the wing with moved(-span/2) and raises the
    fin with rotate(X, 90) — both assume naca_airfoil spans +Y as documented."""
    from app.cad_generation import execute_build123d_code
    from app.cad_skill_planner import plan_build123d_skill

    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    pid = _project(settings, "plane")
    plan = plan_build123d_skill({"project_id": pid, "message": "model an airplane, fuselage length 500mm"})
    out = execute_build123d_code(settings, pid, {"code": plan["proposed_input"]["code"], "thumbnail": False})
    assert out["status"] == "ok", out
    topo = json.loads(_package_member(settings, pid, "geometry/topology_map.json"))
    bbox = {e["name"]: e["bounding_box"] for e in topo["entities"] if e["type"] == "solid" and e.get("name")}
    wing, fin, fuselage = bbox["wing"], bbox["tail_fin"], bbox["fuselage"]
    assert wing[1] == pytest.approx(-wing[4], abs=1e-3)       # spans -span/2 .. +span/2
    assert fin[5] > fuselage[5]                               # fin rises above the body
