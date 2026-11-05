"""cad.edit_parameter addresses a dimension the way the agent wrote it.

Measured on 2026-11-05 headless trials: every workbench run first called
`cad.edit_parameter {featureId: "beam", parameterName: "THICKNESS"}` — its own
part label and constant — was refused, and needed a cad.list_editable_parameters
round trip to learn `feat_body_001` / `thickness_mm`.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.project_io import _validate_cad_parameter_edit_contract

BEAM = {
    "id": "feat_body_001", "type": "named_part", "name": "beam",
    "parameters": {"thickness_mm": {"cad_parameter_name": "THICKNESS", "current_value": 10.0}},
}
FACE = {"id": "feat_001", "type": "base_face", "name": "Base face", "parameters": {}}


def _package(tmp_path: Path, *features: dict) -> Path:
    path = tmp_path / "model.aieng"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("graph/feature_graph.json", json.dumps({"features": list(features)}))
    return path


@pytest.mark.parametrize(("feature_id", "parameter_name"), [
    ("feat_body_001", "thickness_mm"),  # the graph's own ids
    ("beam", "THICKNESS"),              # part label + declared constant
    ("beam", "thickness_mm"),
    ("", "THICKNESS"),                  # the constant alone, declared on one feature
])
def test_an_unambiguous_name_resolves_to_the_graph_ids(tmp_path, feature_id, parameter_name) -> None:
    contract = _validate_cad_parameter_edit_contract(
        _package(tmp_path, BEAM, FACE), feature_id, parameter_name, 20.0,
    )
    assert contract["feature"]["id"] == "feat_body_001"
    assert contract["cad_parameter_name"] == "THICKNESS"


def test_a_constant_declared_on_several_features_is_refused_not_guessed(tmp_path) -> None:
    other = {**BEAM, "id": "feat_body_002", "name": "rib"}
    with pytest.raises(ValueError, match="feature_id not found"):
        _validate_cad_parameter_edit_contract(_package(tmp_path, BEAM, other), "", "THICKNESS", 20.0)


def test_a_part_label_without_that_parameter_does_not_redirect_the_edit(tmp_path) -> None:
    with pytest.raises(ValueError, match="feature_id not found"):
        _validate_cad_parameter_edit_contract(_package(tmp_path, BEAM, FACE), "Base face", "WIDTH", 20.0)
