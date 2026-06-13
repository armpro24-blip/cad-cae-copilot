"""Backend stability gate — lightweight smoke tests for canonical demos, artifacts, and honesty boundaries.

This file does NOT run heavy subprocess tests. It checks:
1. Canonical demo/test files and docs exist on disk.
2. Key artifact names are consistently referenced in docs.
3. Honesty boundary text remains present in canonical docs.
4. A lightweight stability report can be emitted.

Run: pytest aieng/tests/test_backend_stability_gate.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (relative to repo root, which is aieng/../..)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AIENG_ROOT = _REPO_ROOT / "aieng"
_BACKEND_TESTS = _REPO_ROOT / "aieng-ui" / "backend" / "tests"
_DOCS = _AIENG_ROOT / "docs"

# Canonical demo/test files the gate checks for existence.
CANONICAL_DEMO_FILES = [
    _AIENG_ROOT / "tests" / "test_topology_optimization.py",
    _AIENG_ROOT / "tests" / "test_mesh_brep_solidification.py",
    _AIENG_ROOT / "tests" / "test_mesh_freeform_surface_fitting.py",
    _AIENG_ROOT / "tests" / "test_mesh_freeform_surface_readiness.py",
    _AIENG_ROOT / "tests" / "test_mesh_freeform_brep_faces.py",
    _AIENG_ROOT / "tests" / "test_freeform_face_trimming_readiness.py",
    _AIENG_ROOT / "tests" / "test_mesh_to_cad_reconstruction_status.py",
    _AIENG_ROOT / "tests" / "test_mesh_segmentation_quality.py",
    _BACKEND_TESTS / "test_assembly_topopt_demo.py",
    _BACKEND_TESTS / "test_design_study_demo.py",
    _AIENG_ROOT / "tests" / "test_showcase_gallery_docs.py",
    _AIENG_ROOT / "tests" / "test_design_study.py",
    _AIENG_ROOT / "tests" / "test_design_study_execution.py",
    _AIENG_ROOT / "tests" / "test_design_study_ranking.py",
    _AIENG_ROOT / "tests" / "test_design_study_acceptance.py",
    _AIENG_ROOT / "tests" / "test_design_study_evaluation.py",
    _AIENG_ROOT / "tests" / "test_design_study_hints.py",
    _AIENG_ROOT / "tests" / "test_design_study_cae_evaluation.py",
]

# Canonical docs the gate checks for existence.
CANONICAL_DOCS = [
    _DOCS / "demo_catalog.md",
    _DOCS / "showcase_gallery.md",
    _DOCS / "backend_capability_matrix.md",
    _DOCS / "backend_artifact_reference.md",
    _DOCS / "showcase_gallery.json",
    _REPO_ROOT / "AGENTS.md",
]

# Artifact names that must be referenced in at least one canonical doc.
# This is a contract check: if an artifact name changes, docs must stay in sync.
KEY_ARTIFACT_NAMES = [
    "analysis/topology_optimization.json",
    "analysis/topology_optimization_problem.json",
    "geometry/reconstructed.step",
    "geometry/reconstructed_topology_map.json",
    "graph/mesh_brep_stitching_plan.json",
    "diagnostics/mesh_brep_roundtrip_verification.json",
    "assembly/assembly_ir.json",
    "analysis/assembly_topology_optimization.json",
    "diagnostics/assembly_post_optimization_verification.json",
    "analysis/assembly_design_recommendations.json",
    # Design study PR1–PR6 artifacts
    "analysis/design_study_problem.json",
    "diagnostics/design_study_problem_diagnostics.json",
    "patches/design_candidates/",
    "diagnostics/design_study_candidate_validation.json",
    "candidates/candidate_good/analysis/evaluation.json",
    "candidates/candidate_good/diagnostics/evaluation_report.json",
    "analysis/design_study_iterations.json",
    "diagnostics/design_study_report.json",
    "analysis/design_study_candidate_hints.json",
    "diagnostics/design_study_candidate_hints_report.json",
    "analysis/design_study_candidate_ranking.json",
    "diagnostics/design_study_scoring_report.json",
    "analysis/design_study_acceptance.json",
    "diagnostics/design_study_acceptance_report.json",
    "accepted/candidate_good/geometry/shape_ir.json",
    # Candidate CAE evaluation request v0 artifacts
    "candidates/<candidate_id>/analysis/cae_evaluation_request.json",
    "candidates/<candidate_id>/diagnostics/cae_evaluation_request.json",
    "candidates/<candidate_id>/simulation/setup.yaml",
    # Freeform surface fitting v0 artifacts
    "graph/mesh_freeform_surface_fit.json",
    "diagnostics/mesh_freeform_surface_fitting.json",
    "diagnostics/mesh_freeform_reconstruction_readiness.json",
    "geometry/partial_freeform_brep_faces.json",
    "diagnostics/freeform_brep_face_generation.json",
    "diagnostics/freeform_face_trimming_readiness.json",
    "diagnostics/mesh_to_cad_reconstruction_status.json",
    "diagnostics/mesh_segmentation_quality.json",
    "analysis/mesh_resegmentation_hints.json",
]

# Honesty boundary phrases that must appear across the canonical docs.
# These are lightweight doc smoke tests, not runtime assertions.
HONESTY_PHRASES = [
    "not production-certified",
    "experimental",
    "proxy",
    "no real nonlinear contact",
    "no bolt preload",
    "mesh-derived",
    "lossy",
    "not production CAD",
    "no autonomous optimization",
    "baseline",
    "derived artifact",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_doc_text() -> str:
    """Concatenate all canonical docs for phrase-searching."""
    parts = []
    for p in CANONICAL_DOCS:
        if p.exists():
            parts.append(_read_text(p))
    return "\n".join(parts)


def _write_stability_report(path: Path, findings: dict) -> None:
    path.write_text(json.dumps(findings, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Task A — Canonical demo/test file existence
# ---------------------------------------------------------------------------


def test_canonical_demo_files_exist() -> None:
    """Every canonical demo/test file referenced by the stability gate must exist."""
    missing = [str(p.relative_to(_REPO_ROOT)) for p in CANONICAL_DEMO_FILES if not p.exists()]
    assert not missing, f"Missing canonical demo/test files: {missing}"


def test_canonical_docs_exist() -> None:
    """Every canonical doc referenced by the stability gate must exist."""
    missing = [str(p.relative_to(_REPO_ROOT)) for p in CANONICAL_DOCS if not p.exists()]
    assert not missing, f"Missing canonical docs: {missing}"


# ---------------------------------------------------------------------------
# Task B — Artifact contract checks
# ---------------------------------------------------------------------------


def test_key_artifact_names_referenced_in_docs() -> None:
    """Each key artifact name must appear in at least one canonical doc."""
    text = _all_doc_text()
    missing = [name for name in KEY_ARTIFACT_NAMES if name not in text]
    assert not missing, (
        f"Key artifact names not referenced in canonical docs: {missing}. "
        "If an artifact was renamed, update docs and this gate."
    )


def test_showcase_gallery_json_valid_and_covers_entries() -> None:
    """showcase_gallery.json must be valid JSON and cover the 4 canonical capability areas."""
    gallery_path = _DOCS / "showcase_gallery.json"
    raw = _read_text(gallery_path)
    data = json.loads(raw)
    assert data.get("format") == "aieng.showcase_gallery.v0"
    entries = data.get("entries", [])
    ids = {e["id"] for e in entries}
    expected = {
        "single_part_topopt_bracket",
        "mesh_to_cad_step_reconstruction",
        "assembly_aware_topopt_bracket",
        "agent_guided_design_study_bracket",
    }
    assert expected <= ids, f"Missing showcase entries: {expected - ids}"


def test_demo_catalog_references_all_canonical_demos() -> None:
    """demo_catalog.md must reference each of the 4 canonical demo capability areas."""
    text = _read_text(_DOCS / "demo_catalog.md")
    for area in [
        "Single-part topology optimization",
        "Mesh-to-CAD B-Rep reconstruction",
        "Assembly-aware topology optimization",
        "Agent-guided parameter design study",
    ]:
        assert area in text, f"demo_catalog.md missing section for: {area}"


def test_backend_artifact_reference_covers_key_paths() -> None:
    """backend_artifact_reference.md must list the core conditional artifacts."""
    text = _read_text(_DOCS / "backend_artifact_reference.md")
    # Spot-check a few critical conditional paths
    assert "geometry/reconstructed.step" in text
    assert "diagnostics/mesh_brep_roundtrip_verification.json" in text
    assert "assembly/assembly_ir.json" in text
    assert "analysis/assembly_topology_optimization.json" in text


# ---------------------------------------------------------------------------
# Task C — Honesty boundary checks
# ---------------------------------------------------------------------------


def test_honesty_boundary_phrases_present_in_docs() -> None:
    """Canonical docs must still include honesty-boundary language."""
    text = _all_doc_text().lower()
    missing = []
    for phrase in HONESTY_PHRASES:
        if phrase.lower() not in text:
            missing.append(phrase)
    assert not missing, (
        f"Honesty-boundary phrases missing from canonical docs: {missing}"
    )


def test_demo_catalog_records_known_limitations_table() -> None:
    """demo_catalog.md must keep a Known Limitations table."""
    text = _read_text(_DOCS / "demo_catalog.md")
    assert "## Known Limitations" in text
    assert "3D SIMP" in text and "experimental" in text.lower()
    assert "Proxy connections" in text or "proxy" in text.lower()


def test_agents_md_includes_honesty_boundaries() -> None:
    """AGENTS.md must include explicit honesty boundary language for key capabilities."""
    text = _read_text(_REPO_ROOT / "AGENTS.md")
    lower = text.lower()
    assert "production_ready:false" in lower or "production_ready: false" in lower
    assert "contact_physics_modeled:false" in lower or "contact_physics_modeled: false" in lower
    assert "bolt_preload_modeled:false" in lower or "bolt_preload_modeled: false" in lower
    assert "not production cad" in lower or "not production-certified" in lower


def test_agents_md_documents_prepare_solver_run_guided_workflow() -> None:
    """AGENTS.md must document the prepare-solver guided workflow contract."""
    text = _read_text(_REPO_ROOT / "AGENTS.md")
    lower = text.lower()
    assert "cae.prepare_solver_run" in text
    assert "recommended_next_calls" in text
    assert "{tool, input, reason}" in text
    assert "{action, reason}" in text
    anchor = lower.find("### c — cad → cae simulation pipeline")
    assert anchor != -1
    next_section = lower.find("\n### ", anchor + 1)
    if next_section == -1:
        next_section = len(lower)
    prepare_solver_section = lower[anchor:next_section]
    assert "approval-gated" in prepare_solver_section
    assert "evidence artifacts" in prepare_solver_section


# ---------------------------------------------------------------------------
# Task D — Stability report helper
# ---------------------------------------------------------------------------


def test_stability_report_generation(tmp_path: Path) -> None:
    """The gate can write a lightweight stability report summarizing its checks."""
    findings = {
        "gate_name": "backend_stability_gate",
        "version": "0.1.0",
        "checked_demo_files": [str(p.relative_to(_REPO_ROOT)) for p in CANONICAL_DEMO_FILES],
        "checked_docs": [str(p.relative_to(_REPO_ROOT)) for p in CANONICAL_DOCS],
        "checked_artifacts": KEY_ARTIFACT_NAMES,
        "checked_honesty_phrases": HONESTY_PHRASES,
        "demo_files_present": {str(p.relative_to(_REPO_ROOT)): p.exists() for p in CANONICAL_DEMO_FILES},
        "docs_present": {str(p.relative_to(_REPO_ROOT)): p.exists() for p in CANONICAL_DOCS},
        "test_command_suggestions": [
            "pytest aieng/tests/test_backend_stability_gate.py -q",
            "pytest aieng/tests/test_showcase_gallery_docs.py -q",
            "pytest aieng/tests/test_topology_optimization.py -q",
            "pytest aieng/tests/test_mesh_brep_solidification.py -q",
            "pytest aieng-ui/backend/tests/test_design_study_demo.py -q",
            "pytest aieng-ui/backend/tests/test_assembly_topopt_demo.py -q",
        ],
        "disclaimer": (
            "This gate checks file existence, doc consistency, and honesty-boundary coverage. "
            "It is NOT a full production certification suite."
        ),
    }
    report_path = tmp_path / "backend_stability_report.json"
    _write_stability_report(report_path, findings)
    assert report_path.exists()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded["gate_name"] == "backend_stability_gate"
    assert loaded["disclaimer"]
