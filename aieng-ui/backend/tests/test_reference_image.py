"""`cad.set_reference_image` — the calibration loop, previously untested.

AGENTS.md documents this tool in fifteen places: attach a reference once and
"every subsequent `cad.execute_build123d` thumbnail tiles it next to the 4 views
... the layout becomes 2x3 with the reference filling the rightmost column".
No test named it. That is the `undocumented-path` shape, so it was dogfooded.

Most of it held up — the contact sheet really does widen from 480 to 720 px, the
reference really is drawn in the new column, it survives a rebuild, and every
failure mode returns a specific code. What did not hold up was the tool's own
stated contract for `image_url`:

* the docstring said "HTTP(S)" and the error message said "(HTTP/HTTPS)", but
  nothing checked the scheme — `urlopen` also speaks `file://`, so the parameter
  read local files (measured: a `file://` URL returned `status: ok`). This
  function is reached from `cad.search_reference_image`, whose URLs come from a
  web search, i.e. untrusted data.
* the body was read unbounded, while the downscale that keeps the package small
  happens only after decoding.
* passing both `image_url` and `image_path` silently discarded the path and then
  reported a DNS failure for a local file that was sitting right there.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("build123d", reason="the reference-image loop needs the CAD stack")
Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

from app import cad_generation  # noqa: E402

_CODE = (
    "from build123d import *\n"
    "base = Box(60, 40, 8)\n"
    "base.label = 'base_plate'\n"
    "result = Compound(children=[base])\n"
)
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

#: Deliberately garish, so "is the reference actually drawn" is a pixel count
#: rather than a guess. A grey render cannot produce these.
_MAGENTA = (255, 0, 255)
_GREEN = (0, 255, 0)


@pytest.fixture
def project(tmp_path: Path):
    """A built project on its own settings, returned as (settings, project_id)."""
    from app.app_factory import create_app
    from app.config import Settings
    from app.main import default_project, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    create_app(settings)
    project_id = save_project(settings, default_project("reference"))["id"]
    built = cad_generation.execute_build123d_code(
        settings, project_id, {"code": _CODE, "timeout": 180}
    )
    assert built.get("status") == "ok", built
    return settings, project_id


@pytest.fixture
def reference_png(tmp_path: Path) -> Path:
    path = tmp_path / "reference.png"
    image = Image.new("RGB", (400, 300), _MAGENTA)
    ImageDraw.Draw(image).ellipse((50, 50, 350, 250), fill=_GREEN)
    image.save(path)
    return path


def _contact_sheet(result: dict) -> Image.Image:
    blob = result.get("thumbnail") or result.get("thumbnail_png_base64") or ""
    assert blob, f"no thumbnail in response; keys={sorted(result)}"
    return Image.open(io.BytesIO(base64.b64decode(blob))).convert("RGB")


def _build(settings, project_id: str) -> dict:
    return cad_generation.execute_build123d_code(
        settings, project_id, {"code": _CODE, "timeout": 180, "thumbnail": True}
    )


def test_the_sheet_gains_a_column_and_the_reference_is_in_it(project, reference_png) -> None:
    """The documented 2x2 -> 2x3 layout, checked at the pixels.

    Checking only the width would pass on a blank column, which is exactly the
    failure this affordance is supposed to make impossible: the whole point is
    comparing proportions against the real thing.
    """
    settings, project_id = project

    before = _contact_sheet(_build(settings, project_id))
    attached = cad_generation.set_reference_image(
        settings, project_id, {"image_path": str(reference_png)}
    )
    assert attached["status"] == "ok", attached
    after = _contact_sheet(_build(settings, project_id))

    assert after.height == before.height
    assert after.width == pytest.approx(before.width * 1.5, rel=0.02), (
        f"expected a third column: {before.size} -> {after.size}"
    )

    column = after.crop((after.width * 2 // 3, 0, after.width, after.height))
    counts = column.getcolors(maxcolors=1 << 20) or []
    reference_pixels = sum(
        n for n, (r, g, b) in counts
        if (r > 200 and g < 80 and b > 200) or (g > 200 and r < 80 and b < 80)
    )
    assert reference_pixels > 1000, (
        "the new column does not contain the reference image — a widened sheet "
        f"with no reference in it is worse than none ({reference_pixels} px)"
    )


def test_the_reference_survives_a_rebuild(project, reference_png) -> None:
    """It is attached once and used for every later build, per the docs."""
    settings, project_id = project
    cad_generation.set_reference_image(settings, project_id, {"image_path": str(reference_png)})

    from app.project_io import project_dir

    rebuilt = cad_generation.execute_build123d_code(
        settings, project_id,
        {"code": _CODE.replace("Box(60, 40, 8)", "Box(70, 45, 9)"), "timeout": 180,
         "thumbnail": True},
    )
    assert rebuilt.get("status") == "ok", rebuilt
    assert _contact_sheet(rebuilt).width == 720

    with zipfile.ZipFile(project_dir(settings, project_id) / f"{project_id}.aieng") as zf:
        stored = set(zf.namelist())
    assert {"geometry/reference.png", "geometry/reference.json"} <= stored


def test_a_local_file_is_stored_downscaled(project, tmp_path: Path) -> None:
    """The docstring promises a fit-within-800x800 copy so packages stay small."""
    settings, project_id = project
    big = tmp_path / "big.png"
    Image.new("RGB", (2400, 1600), _MAGENTA).save(big)

    attached = cad_generation.set_reference_image(settings, project_id, {"image_path": str(big)})
    assert attached["status"] == "ok", attached
    assert max(attached["width"], attached["height"]) <= 800

    from app.project_io import project_dir

    with zipfile.ZipFile(project_dir(settings, project_id) / f"{project_id}.aieng") as zf:
        stored = Image.open(io.BytesIO(zf.read("geometry/reference.png")))
        meta = json.loads(zf.read("geometry/reference.json"))
    assert max(stored.size) <= 800
    assert meta


class TestTheStatedContract:
    """Each of these returned something other than an honest refusal before."""

    def test_a_file_url_is_refused(self, project, reference_png) -> None:
        """`urlopen` speaks file://; the parameter says HTTP(S) and now means it.

        Measured before the fix: this returned `status: ok` and attached the
        local file through the URL parameter.
        """
        settings, project_id = project
        result = cad_generation.set_reference_image(
            settings, project_id, {"image_url": f"file:///{reference_png.as_posix()}"}
        )
        assert result["status"] == "error"
        assert result["code"] == "unsupported_scheme"
        assert "image_path" in result["message"], "say what the caller should use instead"

    @pytest.mark.parametrize("url", [
        "ftp://example.invalid/x.png",
        "data:image/png;base64,iVBORw0KGgo=",
        "/etc/passwd",
    ])
    def test_only_http_and_https_are_accepted(self, project, url: str) -> None:
        settings, project_id = project
        result = cad_generation.set_reference_image(settings, project_id, {"image_url": url})
        assert result["code"] == "unsupported_scheme", result

    def test_giving_both_sources_is_refused_rather_than_guessed(
        self, project, reference_png
    ) -> None:
        """Before: the path was dropped and a DNS error reported for a local file."""
        settings, project_id = project
        result = cad_generation.set_reference_image(
            settings, project_id,
            {"image_path": str(reference_png), "image_url": "https://example.invalid/x.png"},
        )
        assert result["code"] == "ambiguous_input", result

    def test_an_oversized_local_file_is_refused_before_decoding(
        self, project, tmp_path: Path
    ) -> None:
        settings, project_id = project
        blob = tmp_path / "huge.png"
        blob.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * (26 * 1024 * 1024))

        result = cad_generation.set_reference_image(settings, project_id, {"image_path": str(blob)})
        assert result["code"] == "image_too_large", result

    @pytest.mark.parametrize("payload,code", [
        ({}, "missing_input"),
        ({"image_path": "definitely/not/here.png"}, "file_not_found"),
    ])
    def test_the_other_refusals_stay_specific(self, project, payload: dict, code: str) -> None:
        settings, project_id = project
        assert cad_generation.set_reference_image(settings, project_id, payload)["code"] == code

    def test_a_non_image_file_is_refused(self, project, tmp_path: Path) -> None:
        settings, project_id = project
        text = tmp_path / "notes.txt"
        text.write_text("this is not a PNG", encoding="utf-8")
        result = cad_generation.set_reference_image(settings, project_id, {"image_path": str(text)})
        assert result["code"] == "invalid_image", result
