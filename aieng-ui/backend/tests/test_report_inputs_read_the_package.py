"""The engineering report's inputs read what the package records.

Found by a cold-agent trial of the promised bracket task: the report built
outside an app hit a NameError (evidence helpers relying on route-time symbol
sync), the review packet response dropped every section's body, and the
metrics reader stamped a solver run's metrics as a user import.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.computed_metrics import get_computed_metrics
from app.config import Settings
from app.main import default_project, project_dir, save_project

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _project(tmp_path: Path) -> tuple[Settings, str, Path]:
    settings = Settings(
        platform_root=tmp_path / "platform", workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data", aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("report-inputs"))
    pkg = project_dir(settings, project["id"]) / "p.aieng"
    project["aieng_file"] = "p.aieng"
    save_project(settings, project)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "p", "resources": {}}))
        zf.writestr("results/computed_metrics.json", json.dumps({
            "schema_version": "0.1",
            "metrics_source": {"tool": "frd_parser_v1", "software": "CalculiX",
                               "run_id": "run_002",
                               "source_files": ["simulation/runs/run_002/outputs/result.frd"]},
            "load_cases": [{"load_case_id": "lc1",
                            "metrics": {"max_displacement": {"value": 0.1, "unit": "mm"}}}],
        }))
    return settings, project["id"], pkg


def test_stored_metrics_keep_the_source_they_recorded(tmp_path: Path) -> None:
    settings, project_id, _pkg = _project(tmp_path)

    source = get_computed_metrics(settings, project_id)["document"]["metrics_source"]

    assert source["run_id"] == "run_002" and source["software"] == "CalculiX"
    assert "imported_by" not in source and "format" not in source


def test_evidence_helpers_work_without_an_app(tmp_path: Path) -> None:
    """They needed `app.main` to have been imported (which builds the app and
    injects their helpers). Run in a fresh interpreter that imports only them."""
    import subprocess
    import sys

    _settings, _project_id, pkg = _project(tmp_path)
    code = (
        "import sys; from pathlib import Path; "
        "from app.routers.evidence import _build_evidence_lifecycle_summary; "
        "out = _build_evidence_lifecycle_summary(Path(sys.argv[1]), project_id='p'); "
        "assert 'summary' in out, out; print('ok')"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(pkg)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=300,
    )
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-2000:]


def test_the_review_packet_response_carries_each_section_body(tmp_path: Path) -> None:
    from app.review_support_packet import preview_review_support_packet

    settings, project_id, _pkg = _project(tmp_path)

    sections = preview_review_support_packet(settings, project_id)["sections"]

    assert sections and all("body_md" in s for s in sections)
    assert any(s["body_md"].strip() for s in sections)
