from __future__ import annotations

from typing import Any

from aieng import FORMAT_VERSION

NUMERIC_TYPES = (int, float)


class RuleBasedFeatureRecognizer:
    """Deterministic Phase 3 recognizer that works only from topology_map.json."""

    def recognize(self, topology_map: dict[str, Any], aag: dict[str, Any] | None = None) -> dict[str, Any]:
        entities = [entity for entity in topology_map.get("entities", []) if isinstance(entity, dict)]
        faces = [entity for entity in entities if entity.get("type") == "face"]
        edges = [entity for entity in entities if entity.get("type") == "edge"]
        aag_face_index = self._aag_face_index(aag)
        recognition_context = self._recognition_context(topology_map)

        features: list[dict[str, Any]] = []
        referenced_faces: set[str] = set()
        referenced_edges: set[str] = set()

        solids = [entity for entity in entities if entity.get("type") == "solid"]
        solid_bbox = self._bbox(solids[0].get("bounding_box")) if solids else None

        base_feature = self._base_plate_feature(faces, aag_face_index, recognition_context)
        if base_feature is not None:
            features.append(base_feature)
            referenced_faces.update(base_feature["geometry_refs"].get("faces", []))

        # Edge blends are recognized before holes so a quarter-round fillet face
        # (a cylinder/torus) is not mis-claimed as a mounting-hole candidate.
        fillet_features, fillet_faces = self._fillet_features(
            faces, referenced_faces, aag_face_index, solid_bbox, recognition_context
        )
        features.extend(fillet_features)
        referenced_faces.update(fillet_faces)

        chamfer_features, chamfer_faces = self._chamfer_features(
            faces, referenced_faces, aag_face_index, recognition_context
        )
        features.extend(chamfer_features)
        referenced_faces.update(chamfer_faces)

        hole_features = self._hole_features(
            faces, aag_face_index, recognition_context, exclude_faces=referenced_faces
        )
        features.extend(hole_features)
        for feature in hole_features:
            referenced_faces.update(feature["geometry_refs"].get("faces", []))

        pattern_feature = self._hole_pattern_feature(hole_features, recognition_context)
        if pattern_feature is not None:
            features.append(pattern_feature)
            referenced_faces.update(pattern_feature["geometry_refs"].get("faces", []))

        # Threads annotate holes whose diameter matches a standard tap-drill size.
        # They are ADDITIVE — the underlying hole feature still owns the face — so
        # thread faces are not added to ``referenced_faces``.
        features.extend(self._thread_features(hole_features, recognition_context))

        slot_features, slot_faces = self._slot_features(
            faces, referenced_faces, aag_face_index, solid_bbox, recognition_context
        )
        features.extend(slot_features)
        referenced_faces.update(slot_faces)

        pocket_features, pocket_faces = self._pocket_features(
            faces, referenced_faces, aag_face_index, solid_bbox, recognition_context
        )
        features.extend(pocket_features)
        referenced_faces.update(pocket_faces)

        rib_features, rib_faces = self._rib_features(
            faces, referenced_faces, aag_face_index, solid_bbox, recognition_context
        )
        features.extend(rib_features)
        referenced_faces.update(rib_faces)

        hollow_feature, hollow_faces = self._hollow_body_feature(
            solids, faces, referenced_faces, aag_face_index, recognition_context
        )
        if hollow_feature is not None:
            features.append(hollow_feature)
            referenced_faces.update(hollow_faces)

        unknown_feature = self._unknown_feature(faces, edges, referenced_faces, referenced_edges, recognition_context)
        if unknown_feature is not None:
            features.append(unknown_feature)

        return {
            "format_version": FORMAT_VERSION,
            "metadata": {
                "recognizer": "RuleBasedFeatureRecognizer",
                "source": "geometry/topology_map.json",
                "limitations": [
                    "Rule-based candidate recognition only.",
                    "Features are not guaranteed engineering truth.",
                    "No CAD kernel, STEP parsing, machine learning, or LLM calls were used.",
                ],
                "aag_used": bool(aag_face_index),
                "recognition_profile": (
                    "real_topology_quality_rules_v1"
                    if recognition_context["real_topology"]
                    else "mock_topology_baseline_rules_v1"
                ),
            },
            "features": features,
        }

    def _recognition_context(self, topology_map: dict[str, Any]) -> dict[str, bool]:
        metadata = topology_map.get("metadata") if isinstance(topology_map, dict) else None
        if not isinstance(metadata, dict):
            return {"real_topology": False}
        return {
            "real_topology": metadata.get("real_step_parsing") is True and metadata.get("extraction_backend") == "occ"
        }

    def _base_plate_feature(
        self,
        faces: list[dict[str, Any]],
        aag_face_index: dict[str, dict[str, Any]],
        recognition_context: dict[str, bool],
    ) -> dict[str, Any] | None:
        planar_faces = [
            face for face in faces
            if face.get("surface_type") == "plane" and isinstance(face.get("area"), NUMERIC_TYPES)
        ]
        if not planar_faces:
            return None
        largest = max(planar_faces, key=lambda face: (float(face.get("area", 0.0)), str(face.get("id", ""))))
        feature = {
            "id": "feat_base_plate_001",
            "type": "base_plate",
            "name": "Base plate candidate",
            "geometry_refs": {"faces": [largest["id"]]},
            "parameters": {"area_mm2": largest.get("area")},
            "parameter_source": "mock",
            "parameter_confidence": "low",
            "editable": True,
            "editability": "semantic_only",
            "writeback_strategy": "semantic_parameter_update_only",
            "editability_reason": (
                "Rule-based/mock parameter exposed for AI understanding and semantic patching only; "
                "no executable CAD regeneration source is attached."
            ),
            "intent": {"role": "structural_base_candidate"},
            "recognition": {
                "method": "rule_based_largest_planar_face",
                "confidence": "medium",
                "uncertainty_notes": [
                    "Largest planar face is a structural-base candidate, not confirmed design intent.",
                    "Candidate should be reviewed against user intent and CAD semantics before writeback.",
                ],
            },
        }
        if recognition_context.get("real_topology"):
            feature["recognition"]["signals"] = {
                "real_topology": True,
                "has_area": isinstance(largest.get("area"), NUMERIC_TYPES),
                "aag_neighbors": len(aag_face_index.get(str(largest.get("id")), {}).get("adjacent_face_ids", set())),
            }
        self._attach_aag_metadata(feature, largest.get("id"), aag_face_index)
        return feature

    def _hole_features(
        self,
        faces: list[dict[str, Any]],
        aag_face_index: dict[str, dict[str, Any]],
        recognition_context: dict[str, bool],
        exclude_faces: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        excluded = exclude_faces or set()
        cylindrical_faces = sorted(
            [
                face for face in faces
                if face.get("surface_type") == "cylinder"
                and isinstance(face.get("radius"), NUMERIC_TYPES)
                and str(face.get("id")) not in excluded
            ],
            key=lambda face: str(face.get("id", "")),
        )
        features: list[dict[str, Any]] = []
        for index, face in enumerate(cylindrical_faces, start=1):
            radius = float(face["radius"])
            feature = {
                "id": f"feat_hole_{index:03d}",
                "type": "mounting_hole",
                "name": "Cylindrical hole candidate",
                "geometry_refs": {"faces": [face["id"]]},
                "parameters": {
                    "radius_mm": radius,
                    "diameter_mm": radius * 2.0,
                },
                "parameter_source": "mock",
                "parameter_confidence": "low",
                "editable": True,
                "editability": "semantic_only",
                "writeback_strategy": "semantic_parameter_update_only",
                "editability_reason": (
                    "Cylinder radius was exposed as a semantic candidate parameter; "
                    "editing it does not imply arbitrary STEP/OCP geometry write-back."
                ),
                "intent": {"role": "mounting_or_passage_candidate"},
                "recognition": {
                    "method": "rule_based_cylindrical_face",
                    "confidence": "medium" if self._is_numeric_vec3(face.get("axis")) else "low",
                    "uncertainty_notes": [
                        "Cylindrical face may represent mounting hole, passage, or other cylindrical feature.",
                        "No CAD feature history was used; semantics remain candidate-level.",
                    ],
                },
            }
            hole_metadata = self._hole_metadata(face, radius)
            if hole_metadata:
                feature["hole_metadata"] = hole_metadata
            if recognition_context.get("real_topology"):
                feature["recognition"]["signals"] = {
                    "real_topology": True,
                    "axis_available": self._is_numeric_vec3(face.get("axis")),
                    "radius_positive": radius > 0,
                }
            self._attach_aag_metadata(feature, face.get("id"), aag_face_index)
            features.append(feature)
        return features

    def _hole_metadata(self, face: dict[str, Any], radius: float) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "diameter_mm": radius * 2.0,
            "mating_stack": self._mating_stack_metadata(face),
        }

        axis = face.get("axis")
        if self._is_numeric_vec3(axis):
            axis_direction = [float(item) for item in axis]
            axis_record: dict[str, Any] = {
                "direction": axis_direction,
                "direction_source": "topology_map.face.axis",
            }
            explicit_origin = self._vec3(face.get("axis_origin") or face.get("origin") or face.get("center"))
            if explicit_origin is not None:
                axis_record["origin_mm"] = explicit_origin
                axis_record["origin_source"] = "topology_map.face"
            else:
                bbox_center = self._bbox_center(face.get("bounding_box"))
                if bbox_center is not None:
                    axis_record["origin_mm"] = bbox_center
                    axis_record["origin_source"] = "bounding_box_center"
            metadata["axis"] = axis_record

        depth = self._hole_depth(face)
        if depth is not None:
            metadata["depth_mm"] = depth

        depth_kind = self._hole_depth_kind(face)
        metadata["hole_depth_kind"] = depth_kind
        if depth_kind == "through":
            metadata["through"] = True
        elif depth_kind == "blind":
            metadata["through"] = False

        counterbore = self._counterbore_metadata(face)
        if counterbore:
            metadata["counterbore"] = counterbore
        countersink = self._countersink_metadata(face)
        if countersink:
            metadata["countersink"] = countersink

        return metadata

    def _hole_depth_kind(self, face: dict[str, Any]) -> str:
        explicit = face.get("hole_depth_kind") or face.get("depth_kind")
        if isinstance(explicit, str) and explicit in {"through", "blind", "unknown"}:
            return explicit
        if face.get("through") is True or face.get("is_through") is True:
            return "through"
        if face.get("through") is False or face.get("is_through") is False:
            return "blind"

        adjacent = face.get("adjacent_entity_ids")
        if isinstance(adjacent, list):
            boundary_face_count = sum(1 for item in adjacent if isinstance(item, str) and item.startswith("face_"))
            if boundary_face_count >= 2:
                return "through"
            if boundary_face_count == 1 and self._hole_depth(face) is not None:
                return "blind"
        return "unknown"

    def _hole_depth(self, face: dict[str, Any]) -> float | None:
        for key in ("depth_mm", "depth", "hole_depth_mm"):
            value = face.get(key)
            if isinstance(value, NUMERIC_TYPES) and float(value) >= 0:
                return float(value)

        bbox = self._bbox(face.get("bounding_box"))
        axis = self._vec3(face.get("axis"))
        if bbox is None or axis is None:
            return None

        extents = [abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])]
        dominant = max(range(3), key=lambda idx: abs(axis[idx]))
        depth = extents[dominant]
        return float(depth) if depth >= 0 else None

    def _mating_stack_metadata(self, face: dict[str, Any]) -> dict[str, Any]:
        value = face.get("mating_stack_thickness_mm")
        if isinstance(value, NUMERIC_TYPES):
            return {
                "status": "known",
                "thickness_mm": float(value),
                "source": "topology_map.face.mating_stack_thickness_mm",
            }
        if isinstance(value, list) and value:
            return {
                "status": "ambiguous",
                "candidate_count": len(value),
                "reason": "Multiple mating stack thickness candidates were provided.",
            }
        return {
            "status": "unknown",
            "reason": "No part-stack relationship metadata is available for this hole candidate.",
        }

    def _counterbore_metadata(self, face: dict[str, Any]) -> dict[str, Any]:
        if isinstance(face.get("counterbore"), dict):
            return dict(face["counterbore"])
        result: dict[str, Any] = {}
        diameter = face.get("counterbore_diameter_mm")
        depth = face.get("counterbore_depth_mm")
        if isinstance(diameter, NUMERIC_TYPES):
            result["diameter_mm"] = float(diameter)
        if isinstance(depth, NUMERIC_TYPES):
            result["depth_mm"] = float(depth)
        return result

    def _countersink_metadata(self, face: dict[str, Any]) -> dict[str, Any]:
        if isinstance(face.get("countersink"), dict):
            return dict(face["countersink"])
        result: dict[str, Any] = {}
        angle = face.get("countersink_angle_deg")
        diameter = face.get("countersink_diameter_mm")
        if isinstance(angle, NUMERIC_TYPES):
            result["angle_deg"] = float(angle)
        if isinstance(diameter, NUMERIC_TYPES):
            result["diameter_mm"] = float(diameter)
        return result

    def _bbox(self, value: Any) -> list[float] | None:
        if not isinstance(value, list) or len(value) != 6:
            return None
        if not all(isinstance(item, NUMERIC_TYPES) for item in value):
            return None
        return [float(item) for item in value]

    def _bbox_center(self, value: Any) -> list[float] | None:
        bbox = self._bbox(value)
        if bbox is None:
            return None
        return [
            (bbox[0] + bbox[3]) / 2.0,
            (bbox[1] + bbox[4]) / 2.0,
            (bbox[2] + bbox[5]) / 2.0,
        ]

    def _vec3(self, value: Any) -> list[float] | None:
        if not self._is_numeric_vec3(value):
            return None
        return [float(item) for item in value]

    def _is_numeric_vec3(self, value: Any) -> bool:
        return isinstance(value, list) and len(value) == 3 and all(isinstance(item, NUMERIC_TYPES) for item in value)

    def _attach_aag_metadata(
        self,
        feature: dict[str, Any],
        face_id: Any,
        aag_face_index: dict[str, dict[str, Any]],
    ) -> None:
        if not isinstance(face_id, str):
            return
        aag_entry = aag_face_index.get(face_id)
        if aag_entry is None:
            return
        recognition = feature.setdefault("recognition", {})
        if isinstance(recognition, dict):
            recognition["aag_node_ids"] = [aag_entry["node_id"]]
            recognition["adjacent_face_ids"] = sorted(aag_entry["adjacent_face_ids"])

    def _aag_face_index(self, aag: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if not isinstance(aag, dict):
            return {}
        nodes = aag.get("nodes")
        arcs = aag.get("arcs")
        if not isinstance(nodes, list) or not isinstance(arcs, list):
            return {}

        face_to_node: dict[str, str] = {}
        node_to_face: dict[str, str] = {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            face_id = node.get("topology_entity_id")
            if isinstance(node_id, str) and isinstance(face_id, str):
                face_to_node[face_id] = node_id
                node_to_face[node_id] = face_id

        face_adjacency: dict[str, set[str]] = {face_id: set() for face_id in face_to_node}
        for arc in arcs:
            if not isinstance(arc, dict):
                continue
            source_node = arc.get("source_node")
            target_node = arc.get("target_node")
            if not isinstance(source_node, str) or not isinstance(target_node, str):
                continue
            source_face = node_to_face.get(source_node)
            target_face = node_to_face.get(target_node)
            if source_face and target_face and source_face != target_face:
                face_adjacency[source_face].add(target_face)
                face_adjacency[target_face].add(source_face)

        result: dict[str, dict[str, Any]] = {}
        for face_id, node_id in face_to_node.items():
            result[face_id] = {
                "node_id": node_id,
                "adjacent_face_ids": face_adjacency.get(face_id, set()),
            }
        return result

    def _hole_pattern_feature(
        self,
        hole_features: list[dict[str, Any]],
        recognition_context: dict[str, bool],
    ) -> dict[str, Any] | None:
        if len(hole_features) < 2:
            return None

        groups: dict[float, list[dict[str, Any]]] = {}
        for feature in hole_features:
            radius = feature.get("parameters", {}).get("radius_mm")
            if isinstance(radius, NUMERIC_TYPES):
                groups.setdefault(round(float(radius), 3), []).append(feature)

        if not groups:
            return None
        _, grouped_features = max(groups.items(), key=lambda item: (len(item[1]), -item[0]))
        if len(grouped_features) < 2:
            return None

        children = [feature["id"] for feature in grouped_features]
        face_ids: list[str] = []
        diameters: set[float] = set()
        for feature in grouped_features:
            face_ids.extend(feature.get("geometry_refs", {}).get("faces", []))
            diameter = feature.get("parameters", {}).get("diameter_mm")
            if isinstance(diameter, NUMERIC_TYPES):
                diameters.add(round(float(diameter), 3))

        parameters: dict[str, Any] = {"count": len(grouped_features)}
        if len(diameters) == 1:
            parameters["diameter_mm"] = sorted(diameters)[0]

        all_axis_available = True
        for feature in grouped_features:
            signals = feature.get("recognition", {}).get("signals") if isinstance(feature.get("recognition"), dict) else None
            if not isinstance(signals, dict) or signals.get("axis_available") is not True:
                all_axis_available = False
                break

        high_confidence = (
            recognition_context.get("real_topology")
            and len(grouped_features) >= 4
            and len(diameters) == 1
            and all_axis_available
        )

        return {
            "id": "feat_hole_pattern_001",
            "type": "mounting_hole_pattern",
            "name": "Cylindrical hole pattern candidate",
            "geometry_refs": {"faces": sorted(set(face_ids))},
            "children": children,
            "parameters": parameters,
            "parameter_source": "mock",
            "parameter_confidence": "low",
            "editable": True,
            "editability": "semantic_only",
            "writeback_strategy": "semantic_parameter_update_only",
            "editability_reason": (
                "Hole-pattern parameters support semantic proposals and protected-region checks only; "
                "CAD write-back requires an explicit parametric regeneration source."
            ),
            "intent": {"role": "mounting_interface_candidate"},
            "recognition": {
                "method": "rule_based_grouped_cylindrical_faces",
                "confidence": "high" if high_confidence else "medium",
                "uncertainty_notes": [
                    "Pattern grouping is geometric and does not prove manufacturing intent by itself.",
                    "Pattern should be confirmed against interfaces/constraints before execution.",
                ],
                "signals": {
                    "real_topology": bool(recognition_context.get("real_topology")),
                    "hole_count": len(grouped_features),
                    "single_diameter_group": len(diameters) == 1,
                    "axis_available_for_all_holes": all_axis_available,
                },
            },
        }

    def _unknown_feature(
        self,
        faces: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        referenced_faces: set[str],
        referenced_edges: set[str],
        recognition_context: dict[str, bool],
    ) -> dict[str, Any] | None:
        remaining_faces = sorted(
            str(face["id"]) for face in faces
            if isinstance(face.get("id"), str) and face["id"] not in referenced_faces
        )
        remaining_edges = sorted(
            str(edge["id"]) for edge in edges
            if isinstance(edge.get("id"), str) and edge["id"] not in referenced_edges
        )
        if not remaining_faces and not remaining_edges:
            return None

        return {
            "id": "feat_unknown_001",
            "type": "unknown_feature",
            "name": "Unclassified topology entities",
            "geometry_refs": {
                "faces": remaining_faces,
                "edges": remaining_edges,
            },
            "parameters": {},
            "parameter_source": "mock",
            "parameter_confidence": "low",
            "editable": False,
            "editability": "not_editable",
            "writeback_strategy": "none",
            "editability_reason": "Unclassified fallback geometry has no safe parameterized edit handle.",
            "intent": {"role": "unclassified_geometry"},
            "recognition": {
                "method": "fallback_unclassified_entities",
                "confidence": "low",
                "uncertainty_notes": [
                    "Unclassified entities require manual review or improved recognition rules.",
                ],
                "signals": {
                    "real_topology": bool(recognition_context.get("real_topology")),
                    "remaining_faces": len(remaining_faces),
                    "remaining_edges": len(remaining_edges),
                },
            },
        }

    # ── Phase 1 feature-graph heuristics (#297): slot, pocket, rib, hollow_body ──

    def _face_adjacency(
        self,
        faces: list[dict[str, Any]],
        aag_face_index: dict[str, dict[str, Any]],
    ) -> dict[str, set[str]]:
        """Build a face-id → adjacent-face-ids map from AAG or topology adjacency."""
        face_ids = {str(face["id"]) for face in faces if isinstance(face.get("id"), str)}
        adjacency: dict[str, set[str]] = {fid: set() for fid in face_ids}

        if aag_face_index:
            for face in faces:
                fid = str(face.get("id"))
                if not fid:
                    continue
                entry = aag_face_index.get(fid)
                if isinstance(entry, dict):
                    adjacency[fid] = {n for n in entry.get("adjacent_face_ids", []) if n in face_ids}
            return adjacency

        # Fallback for topology maps without a full AAG: use adjacent_entity_ids.
        face_id_set = face_ids
        for face in faces:
            fid = str(face.get("id"))
            if not fid:
                continue
            neighbors = set()
            for ref in face.get("adjacent_entity_ids") or []:
                if isinstance(ref, str) and ref in face_id_set and ref != fid:
                    neighbors.add(ref)
            adjacency[fid] = neighbors
        return adjacency

    def _connected_components(self, face_ids: set[str], adjacency: dict[str, set[str]]) -> list[set[str]]:
        """Return connected components among face_ids using the provided adjacency."""
        remaining = set(face_ids)
        components: list[set[str]] = []
        while remaining:
            seed = remaining.pop()
            component: set[str] = {seed}
            queue = [seed]
            while queue:
                current = queue.pop()
                for neighbor in adjacency.get(current, set()):
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        queue.append(neighbor)
            components.append(component)
        return components

    def _component_bbox(self, component: set[str], face_by_id: dict[str, dict[str, Any]]) -> list[float] | None:
        """Union bounding box of all faces in a component."""
        bboxes: list[list[float]] = []
        for fid in component:
            bbox = self._bbox(face_by_id.get(fid, {}).get("bounding_box"))
            if bbox:
                bboxes.append(bbox)
        if not bboxes:
            return None
        return [
            min(b[0] for b in bboxes),
            min(b[1] for b in bboxes),
            min(b[2] for b in bboxes),
            max(b[3] for b in bboxes),
            max(b[4] for b in bboxes),
            max(b[5] for b in bboxes),
        ]

    def _bbox_dimensions(self, bbox: list[float]) -> tuple[float, float, float]:
        """Return the three side lengths of a bbox, sorted smallest → largest."""
        dims = sorted([abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])])
        return (dims[0], dims[1], dims[2])

    def _component_touches_referenced(
        self,
        component: set[str],
        referenced_faces: set[str],
        adjacency: dict[str, set[str]],
    ) -> bool:
        """True if any face in the component is adjacent to an already-referenced face."""
        for fid in component:
            if adjacency.get(fid, set()) & referenced_faces:
                return True
        return False

    def _component_on_outer_boundary(
        self,
        component: set[str],
        solid_bbox: list[float] | None,
        face_by_id: dict[str, dict[str, Any]],
    ) -> bool:
        """Best-effort check that a component is the outer shell rather than an internal cut."""
        if solid_bbox is None:
            return False
        bbox = self._component_bbox(component, face_by_id)
        if bbox is None:
            return False
        # If the component spans almost the full solid extent in two axes, treat it as outer shell.
        tol = 1e-6
        spans = [
            (abs(bbox[3] - bbox[0]), abs(solid_bbox[3] - solid_bbox[0])),
            (abs(bbox[4] - bbox[1]), abs(solid_bbox[4] - solid_bbox[1])),
            (abs(bbox[5] - bbox[2]), abs(solid_bbox[5] - solid_bbox[2])),
        ]
        full_span_axes = sum(1 for span, total in spans if total > tol and span >= total * 0.95)
        return full_span_axes >= 2

    def _slot_features(
        self,
        faces: list[dict[str, Any]],
        referenced_faces: set[str],
        aag_face_index: dict[str, dict[str, Any]],
        solid_bbox: list[float] | None,
        recognition_context: dict[str, bool],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Recognize elongated cut slots among faces not already referenced."""
        face_by_id = {str(face["id"]): face for face in faces if isinstance(face.get("id"), str)}
        candidate_ids = {fid for fid in face_by_id if fid not in referenced_faces}
        if len(candidate_ids) < 2:
            return [], set()

        adjacency = self._face_adjacency(faces, aag_face_index)
        components = self._connected_components(candidate_ids, {fid: adjacency[fid] & candidate_ids for fid in candidate_ids})

        features: list[dict[str, Any]] = []
        consumed: set[str] = set()
        index = 1
        for component in components:
            if len(component) < 2:
                continue
            bbox = self._component_bbox(component, face_by_id)
            if bbox is None:
                continue
            d_min, d_mid, d_max = self._bbox_dimensions(bbox)
            if d_mid <= 0 or d_min < 0:
                continue
            # Slot: significantly elongated and relatively shallow.
            if d_max / d_mid < 2.5 or d_min >= d_mid * 0.8:
                continue
            # Must look like a cut, not the outer shell.
            if self._component_on_outer_boundary(component, solid_bbox, face_by_id):
                continue
            if not self._component_touches_referenced(component, referenced_faces, adjacency):
                continue

            depth_axis = self._reference_depth_axis(component, referenced_faces, face_by_id, adjacency)
            depth, width, length = self._oriented_cut_dims(bbox, depth_axis)
            confidence = "medium" if recognition_context.get("real_topology") else "low"
            feature = {
                "id": f"feat_slot_{index:03d}",
                "type": "slot",
                "name": "Slot candidate",
                "geometry_refs": {"faces": sorted(component)},
                "parameters": {
                    "length_mm": round(length, 3),
                    "width_mm": round(width, 3),
                    "depth_mm": round(depth, 3),
                    "face_count": len(component),
                },
                "parameter_source": "mock",
                "parameter_confidence": "low",
                "editable": True,
                "editability": "semantic_only",
                "writeback_strategy": "semantic_parameter_update_only",
                "editability_reason": "Slot dimensions are heuristic candidates; CAD write-back requires a parametric regeneration source.",
                "intent": {"role": "passage_or_adjustment_candidate"},
                "recognition": {
                    "method": "rule_based_elongated_cut_component",
                    "confidence": confidence,
                    "uncertainty_notes": [
                        "Slot recognition relies on bbox aspect ratio and adjacency; "
                        "verification against design intent is required.",
                    ],
                    "signals": {
                        "real_topology": bool(recognition_context.get("real_topology")),
                        "bbox": bbox,
                    },
                },
            }
            self._attach_aag_metadata(feature, sorted(component)[0], aag_face_index)
            features.append(feature)
            consumed.update(component)
            index += 1
        return features, consumed

    def _pocket_features(
        self,
        faces: list[dict[str, Any]],
        referenced_faces: set[str],
        aag_face_index: dict[str, dict[str, Any]],
        solid_bbox: list[float] | None,
        recognition_context: dict[str, bool],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Recognize recessed pockets among faces not already referenced."""
        face_by_id = {str(face["id"]): face for face in faces if isinstance(face.get("id"), str)}
        candidate_ids = {fid for fid in face_by_id if fid not in referenced_faces}
        if len(candidate_ids) < 3:
            return [], set()

        adjacency = self._face_adjacency(faces, aag_face_index)
        components = self._connected_components(candidate_ids, {fid: adjacency[fid] & candidate_ids for fid in candidate_ids})

        features: list[dict[str, Any]] = []
        consumed: set[str] = set()
        index = 1
        for component in components:
            if len(component) < 3:
                continue
            bbox = self._component_bbox(component, face_by_id)
            if bbox is None:
                continue
            d_min, d_mid, d_max = self._bbox_dimensions(bbox)
            if d_mid <= 0 or d_min < 0:
                continue
            # Pocket: not overly elongated, has some depth, and is not the outer shell.
            if d_max / d_mid >= 2.5 or d_min >= d_mid * 0.95:
                continue
            if self._component_on_outer_boundary(component, solid_bbox, face_by_id):
                continue
            if not self._component_touches_referenced(component, referenced_faces, adjacency):
                continue

            depth_axis = self._reference_depth_axis(component, referenced_faces, face_by_id, adjacency)
            depth, width, length = self._oriented_cut_dims(bbox, depth_axis)
            planar_faces = [face_by_id[fid] for fid in component if face_by_id[fid].get("surface_type") == "plane"]
            floor_area = max((float(f.get("area", 0.0)) for f in planar_faces), default=0.0)

            confidence = "medium" if recognition_context.get("real_topology") else "low"
            feature = {
                "id": f"feat_pocket_{index:03d}",
                "type": "pocket",
                "name": "Pocket candidate",
                "geometry_refs": {"faces": sorted(component)},
                "parameters": {
                    "depth_mm": round(depth, 3),
                    "width_mm": round(width, 3),
                    "length_mm": round(length, 3),
                    "floor_area_mm2": round(floor_area, 3),
                    "wall_count": len(component) - 1,
                    "face_count": len(component),
                },
                "parameter_source": "mock",
                "parameter_confidence": "low",
                "editable": True,
                "editability": "semantic_only",
                "writeback_strategy": "semantic_parameter_update_only",
                "editability_reason": "Pocket dimensions are heuristic candidates; CAD write-back requires a parametric regeneration source.",
                "intent": {"role": "recess_or_cavity_candidate"},
                "recognition": {
                    "method": "rule_based_recessed_planar_component",
                    "confidence": confidence,
                    "uncertainty_notes": [
                        "Pocket recognition relies on planar-face adjacency and bbox ratios; "
                        "floor/wall assignment is approximate.",
                    ],
                    "signals": {
                        "real_topology": bool(recognition_context.get("real_topology")),
                        "bbox": bbox,
                    },
                },
            }
            self._attach_aag_metadata(feature, sorted(component)[0], aag_face_index)
            features.append(feature)
            consumed.update(component)
            index += 1
        return features, consumed

    def _rib_features(
        self,
        faces: list[dict[str, Any]],
        referenced_faces: set[str],
        aag_face_index: dict[str, dict[str, Any]],
        solid_bbox: list[float] | None,
        recognition_context: dict[str, bool],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Recognize thin stiffening ribs among remaining planar faces."""
        face_by_id = {str(face["id"]): face for face in faces if isinstance(face.get("id"), str)}
        candidate_ids = {fid for fid, face in face_by_id.items() if fid not in referenced_faces and face.get("surface_type") == "plane"}
        if not candidate_ids:
            return [], set()

        adjacency = self._face_adjacency(faces, aag_face_index)
        face_areas = {fid: float(face_by_id[fid].get("area", 0.0)) for fid in candidate_ids}

        features: list[dict[str, Any]] = []
        consumed: set[str] = set()
        index = 1
        for fid in sorted(candidate_ids):
            if fid in consumed:
                continue
            face = face_by_id[fid]
            bbox = self._bbox(face.get("bounding_box"))
            if bbox is None:
                continue
            d_min, d_mid, d_max = self._bbox_dimensions(bbox)
            if d_max <= 0 or d_mid <= 0:
                continue
            # Thin and tall: smallest dimension is the wall thickness.
            if d_min >= d_mid * 0.35 or d_max < d_min * 4.0:
                continue
            # Skip faces that sit on the outer bounding box (outer walls, not ribs).
            if solid_bbox is not None and self._face_on_outer_boundary(face, solid_bbox):
                continue
            # Must bridge at least two larger adjacent faces.
            larger_neighbors = [
                n for n in adjacency.get(fid, set())
                if n in face_by_id and float(face_by_id[n].get("area", 0.0)) > face_areas.get(fid, 0.0) * 1.5
            ]
            if len(larger_neighbors) < 2:
                continue

            # Orient dimensions using the face normal: thickness is along the normal.
            normal = self._vec3(face.get("normal"))
            dims = [abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])]
            if normal is not None and max(abs(v) for v in normal) >= 0.5:
                axis_idx = max(range(3), key=lambda i: abs(normal[i]))
                thickness = dims[axis_idx]
                remaining = [d for i, d in enumerate(dims) if i != axis_idx]
                height, length = sorted(remaining)
            else:
                thickness, height, length = d_min, d_mid, d_max

            confidence = "medium" if recognition_context.get("real_topology") else "low"
            feature = {
                "id": f"feat_rib_{index:03d}",
                "type": "rib",
                "name": "Rib candidate",
                "geometry_refs": {"faces": [fid]},
                "parameters": {
                    "thickness_mm": round(thickness, 3),
                    "height_mm": round(height, 3),
                    "length_mm": round(length, 3),
                },
                "parameter_source": "mock",
                "parameter_confidence": "low",
                "editable": True,
                "editability": "semantic_only",
                "writeback_strategy": "semantic_parameter_update_only",
                "editability_reason": "Rib dimensions are heuristic candidates; CAD write-back requires a parametric regeneration source.",
                "intent": {"role": "stiffening_candidate"},
                "recognition": {
                    "method": "rule_based_thin_bridging_planar_face",
                    "confidence": confidence,
                    "uncertainty_notes": [
                        "Rib recognition relies on aspect ratio and adjacency to larger faces; "
                        "outer walls can be misclassified when only topology is available.",
                    ],
                    "signals": {
                        "real_topology": bool(recognition_context.get("real_topology")),
                        "larger_neighbor_count": len(larger_neighbors),
                        "bbox": bbox,
                    },
                },
            }
            self._attach_aag_metadata(feature, fid, aag_face_index)
            features.append(feature)
            consumed.add(fid)
            index += 1
        return features, consumed

    def _face_on_outer_boundary(
        self,
        face: dict[str, Any],
        solid_bbox: list[float],
    ) -> bool:
        """Return True if a planar face lies on the solid's outer bounding box."""
        bbox = self._bbox(face.get("bounding_box"))
        if bbox is None:
            return False
        normal = self._vec3(face.get("normal"))
        tol = 1e-6
        # Check if the face is flush with a bbox face in the direction of its normal.
        if normal is not None:
            for axis, sign in enumerate(normal):
                if abs(sign) < 0.5:
                    continue
                coord_index = axis if sign > 0 else axis + 3
                if abs(bbox[coord_index] - solid_bbox[coord_index]) <= tol:
                    # And it spans a meaningful portion of the other two axes.
                    other_spans = [abs(bbox[i + 3] - bbox[i]) for i in range(3) if i != axis]
                    other_totals = [abs(solid_bbox[i + 3] - solid_bbox[i]) for i in range(3) if i != axis]
                    if all(total > tol for total in other_totals) and all(
                        span >= total * 0.4 for span, total in zip(other_spans, other_totals)
                    ):
                        return True
        # Also treat any face that fully spans two bbox axes as outer shell.
        spans = [abs(bbox[i + 3] - bbox[i]) for i in range(3)]
        totals = [abs(solid_bbox[i + 3] - solid_bbox[i]) for i in range(3)]
        full_span_axes = sum(1 for span, total in zip(spans, totals) if total > tol and span >= total * 0.95)
        return full_span_axes >= 2

    def _reference_depth_axis(
        self,
        component: set[str],
        referenced_faces: set[str],
        face_by_id: dict[str, dict[str, Any]],
        adjacency: dict[str, set[str]],
    ) -> list[float] | None:
        """Infer the depth direction of a cut from an adjacent referenced planar face."""
        for fid in component:
            for neighbor in adjacency.get(fid, set()):
                if neighbor not in referenced_faces:
                    continue
                neighbor_face = face_by_id.get(neighbor)
                if neighbor_face and neighbor_face.get("surface_type") == "plane":
                    normal = self._vec3(neighbor_face.get("normal"))
                    if normal:
                        return normal
        return None

    def _oriented_cut_dims(
        self,
        bbox: list[float],
        depth_axis: list[float] | None,
    ) -> tuple[float, float, float]:
        """Return (depth, width, length) for a cut, using the depth axis if known."""
        dims = [abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])]
        if depth_axis is None:
            d_min, d_mid, d_max = self._bbox_dimensions(bbox)
            return d_min, d_mid, d_max
        axes = [abs(depth_axis[0]), abs(depth_axis[1]), abs(depth_axis[2])]
        if max(axes) < 0.5:
            d_min, d_mid, d_max = self._bbox_dimensions(bbox)
            return d_min, d_mid, d_max
        axis_idx = max(range(3), key=lambda i: axes[i])
        depth = dims[axis_idx]
        remaining = [d for i, d in enumerate(dims) if i != axis_idx]
        width, length = sorted(remaining)
        return depth, width, length

    def _hollow_body_feature(
        self,
        solids: list[dict[str, Any]],
        faces: list[dict[str, Any]],
        referenced_faces: set[str],
        aag_face_index: dict[str, dict[str, Any]],
        recognition_context: dict[str, bool],
    ) -> tuple[dict[str, Any] | None, set[str]]:
        """Recognize a hollow / shell body from low volume-to-bbox fill ratio."""
        if not solids:
            return None, set()
        solid = solids[0]
        bb = self._bbox(solid.get("bounding_box"))
        volume = solid.get("volume")
        if bb is None or not isinstance(volume, NUMERIC_TYPES):
            return None, set()
        bbox_volume = abs((bb[3] - bb[0]) * (bb[4] - bb[1]) * (bb[5] - bb[2]))
        if bbox_volume <= 0:
            return None, set()
        fill_ratio = max(0.0, min(1.0, float(volume) / bbox_volume))
        if fill_ratio >= 0.60:
            return None, set()

        body_id = str(solid.get("id", "body_001"))
        body_faces = [str(face["id"]) for face in faces if isinstance(face.get("id"), str) and str(face.get("body_id")) == body_id]
        if len(body_faces) < 6:
            return None, set()

        confidence = "high" if fill_ratio < 0.30 and recognition_context.get("real_topology") else "medium"
        feature = {
            "id": "feat_hollow_body_001",
            "type": "hollow_body",
            "name": f"Hollow body {body_id}",
            "geometry_refs": {"body": body_id, "faces": sorted(body_faces)},
            "parameters": {
                "bbox_fill_ratio": round(fill_ratio, 3),
                "face_count": len(body_faces),
            },
            "parameter_source": "mock",
            "parameter_confidence": "low",
            "editable": True,
            "editability": "semantic_only",
            "writeback_strategy": "semantic_parameter_update_only",
            "editability_reason": "Shell/wall-thickness parameters are heuristic candidates; CAD write-back requires a parametric regeneration source.",
            "intent": {"role": "shell", "manufacturing_note": "candidate hollow enclosure/housing"},
            "recognition": {
                "method": "bbox_volume_fill_ratio",
                "confidence": confidence,
                "uncertainty_notes": [
                    "Heuristic candidate only; wall thickness and open-face status require explicit geometry validation.",
                ],
                "signals": {
                    "real_topology": bool(recognition_context.get("real_topology")),
                    "fill_ratio": round(fill_ratio, 3),
                    "face_count": len(body_faces),
                },
            },
        }
        self._attach_aag_metadata(feature, body_faces[0] if body_faces else None, aag_face_index)
        return feature, set(body_faces)

    # ── Phase 2 feature-graph heuristics (#297): fillet, chamfer ──

    def _smallest_solid_dimension(self, solid_bbox: list[float] | None) -> float | None:
        if solid_bbox is None:
            return None
        dims = [
            abs(solid_bbox[3] - solid_bbox[0]),
            abs(solid_bbox[4] - solid_bbox[1]),
            abs(solid_bbox[5] - solid_bbox[2]),
        ]
        positive = [d for d in dims if d > 0]
        return min(positive) if positive else None

    def _fillet_features(
        self,
        faces: list[dict[str, Any]],
        referenced_faces: set[str],
        aag_face_index: dict[str, dict[str, Any]],
        solid_bbox: list[float] | None,
        recognition_context: dict[str, bool],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Recognize edge/corner fillets from quarter-round cylinder or torus faces.

        A fillet face is a constant-radius blend between two faces: an edge fillet
        is a quarter cylinder (cross-section ~ 1 radius, while a hole spans ~2
        radii), and a corner fillet is a small toroidal patch. Topology-only and
        candidate-level — a recognized fillet is a geometric signal, not a
        manufacturing-certified edge break.
        """
        face_by_id = {str(face["id"]): face for face in faces if isinstance(face.get("id"), str)}
        adjacency = self._face_adjacency(faces, aag_face_index)
        smallest_solid_dim = self._smallest_solid_dimension(solid_bbox)

        features: list[dict[str, Any]] = []
        consumed: set[str] = set()
        index = 1
        for fid in sorted(face_by_id):
            if fid in referenced_faces or fid in consumed:
                continue
            face = face_by_id[fid]
            surface_type = face.get("surface_type")
            if surface_type not in ("cylinder", "torus"):
                continue
            bbox = self._bbox(face.get("bounding_box"))
            if bbox is None:
                continue
            # A blend joins at least two other faces.
            if len(adjacency.get(fid, set())) < 2:
                continue
            dims = [abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])]

            if surface_type == "cylinder":
                radius = float(face["radius"]) if isinstance(face.get("radius"), NUMERIC_TYPES) else None
                if radius is None or radius <= 0:
                    continue
                axis = self._vec3(face.get("axis"))
                if axis is not None and max(abs(v) for v in axis) >= 0.5:
                    axis_idx = max(range(3), key=lambda i: abs(axis[i]))
                    length = dims[axis_idx]
                    cross = [d for i, d in enumerate(dims) if i != axis_idx]
                else:
                    ordered = sorted(dims)
                    cross = ordered[:2]
                    length = ordered[2]
                # Quarter-round signal: both cross extents ~ one radius. A through
                # hole / boss spans ~2 radii and is intentionally NOT matched here.
                if not all(0.4 * radius <= c <= 1.6 * radius for c in cross):
                    continue
                # Guard against labelling a large structural cylinder a fillet.
                if smallest_solid_dim is not None and radius > 0.5 * smallest_solid_dim:
                    continue
                method = "rule_based_quarter_round_cylindrical_blend"
                parameters: dict[str, Any] = {
                    "radius_mm": round(radius, 3),
                    "length_mm": round(length, 3),
                    "face_count": 1,
                }
                blend_kind = "edge_fillet"
            else:  # torus → corner blend
                d_min, d_mid, d_max = self._bbox_dimensions(bbox)
                if d_max <= 0:
                    continue
                # Corner blends are small compact patches, not elongated tubes.
                if d_max / max(d_min, 1e-9) > 3.0:
                    continue
                if smallest_solid_dim is not None and d_max > 0.5 * smallest_solid_dim:
                    continue
                method = "rule_based_toroidal_corner_blend"
                parameters = {
                    "radius_mm": round(d_min, 3),
                    "radius_source": "bbox_minor_extent_proxy",
                    "face_count": 1,
                }
                blend_kind = "corner_fillet"

            confidence = "medium" if recognition_context.get("real_topology") else "low"
            feature = {
                "id": f"feat_fillet_{index:03d}",
                "type": "fillet",
                "name": "Fillet candidate",
                "geometry_refs": {"faces": [fid]},
                "parameters": parameters,
                "parameter_source": "mock",
                "parameter_confidence": "low",
                "editable": True,
                "editability": "semantic_only",
                "writeback_strategy": "semantic_parameter_update_only",
                "editability_reason": "Fillet radius is a heuristic candidate; CAD write-back requires a parametric regeneration source.",
                "intent": {"role": "edge_blend", "manufacturing_note": "stress_relief"},
                "recognition": {
                    "method": method,
                    "confidence": confidence,
                    "uncertainty_notes": [
                        "Fillet recognition is a geometric blend signal, not a manufacturing-certified edge break.",
                        "Radius/extent are derived from the bounding box and adjacency; verify against design intent.",
                    ],
                    "signals": {
                        "real_topology": bool(recognition_context.get("real_topology")),
                        "blend_kind": blend_kind,
                        "surface_type": surface_type,
                        "neighbor_count": len(adjacency.get(fid, set())),
                    },
                },
            }
            self._attach_aag_metadata(feature, fid, aag_face_index)
            features.append(feature)
            consumed.add(fid)
            index += 1
        return features, consumed

    def _chamfer_features(
        self,
        faces: list[dict[str, Any]],
        referenced_faces: set[str],
        aag_face_index: dict[str, dict[str, Any]],
        recognition_context: dict[str, bool],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Recognize chamfers as narrow planar bevels with a diagonal normal.

        A chamfer is a flat bevel that breaks the edge between two faces, so its
        normal is not axis-aligned and it borders at least two larger,
        more-axis-aligned planar faces. Conservative and candidate-level — a
        diagonal design face may also match; confidence stays low/medium.
        """
        face_by_id = {str(face["id"]): face for face in faces if isinstance(face.get("id"), str)}
        adjacency = self._face_adjacency(faces, aag_face_index)

        features: list[dict[str, Any]] = []
        consumed: set[str] = set()
        index = 1
        for fid in sorted(face_by_id):
            if fid in referenced_faces or fid in consumed:
                continue
            face = face_by_id[fid]
            if face.get("surface_type") != "plane":
                continue
            normal = self._vec3(face.get("normal"))
            if normal is None:
                continue
            mags = sorted(abs(v) for v in normal)  # ascending
            # Diagonal normal: dominant component not axis-aligned and a second
            # component is significant (the bevel splits two directions).
            if mags[2] >= 0.95 or mags[1] < 0.3:
                continue
            bbox = self._bbox(face.get("bounding_box"))
            if bbox is None:
                continue
            this_area = float(face.get("area", 0.0))
            larger_axis_aligned = 0
            for neighbor in adjacency.get(fid, set()):
                neighbor_face = face_by_id.get(neighbor)
                if not neighbor_face or neighbor_face.get("surface_type") != "plane":
                    continue
                if float(neighbor_face.get("area", 0.0)) <= this_area:
                    continue
                neighbor_normal = self._vec3(neighbor_face.get("normal"))
                if neighbor_normal is not None and max(abs(v) for v in neighbor_normal) >= 0.9:
                    larger_axis_aligned += 1
            if larger_axis_aligned < 2:
                continue

            dims = sorted([abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])])
            confidence = "medium" if recognition_context.get("real_topology") else "low"
            feature = {
                "id": f"feat_chamfer_{index:03d}",
                "type": "chamfer",
                "name": "Chamfer candidate",
                "geometry_refs": {"faces": [fid]},
                "parameters": {
                    "width_mm": round(dims[1], 3),
                    "length_mm": round(dims[2], 3),
                    "face_count": 1,
                },
                "parameter_source": "mock",
                "parameter_confidence": "low",
                "editable": True,
                "editability": "semantic_only",
                "writeback_strategy": "semantic_parameter_update_only",
                "editability_reason": "Chamfer dimensions are heuristic candidates; CAD write-back requires a parametric regeneration source.",
                "intent": {"role": "edge_break", "manufacturing_note": "deburr_or_lead_in"},
                "recognition": {
                    "method": "rule_based_diagonal_planar_bevel",
                    "confidence": confidence,
                    "uncertainty_notes": [
                        "Chamfer recognition relies on a diagonal planar normal bordering two larger faces; "
                        "a sloped design face can also match. Verify against design intent.",
                    ],
                    "signals": {
                        "real_topology": bool(recognition_context.get("real_topology")),
                        "normal": normal,
                        "larger_axis_aligned_neighbors": larger_axis_aligned,
                    },
                },
            }
            self._attach_aag_metadata(feature, fid, aag_face_index)
            features.append(feature)
            consumed.add(fid)
            index += 1
        return features, consumed

    # Standard ISO metric coarse-thread tap-drill diameters (mm). A hole drilled
    # to one of these sizes is the pre-tap diameter for that thread.
    _TAP_DRILL_DIAMETERS_MM: tuple[tuple[str, float], ...] = (
        ("M2", 1.6),
        ("M2.5", 2.05),
        ("M3", 2.5),
        ("M4", 3.3),
        ("M5", 4.2),
        ("M6", 5.0),
        ("M8", 6.8),
        ("M10", 8.5),
        ("M12", 10.2),
        ("M16", 14.0),
        ("M20", 17.5),
    )

    def _thread_features(
        self,
        hole_features: list[dict[str, Any]],
        recognition_context: dict[str, bool],
    ) -> list[dict[str, Any]]:
        """Flag holes whose diameter matches a standard tap-drill size as candidate threads.

        Real helical thread geometry is rarely present in build123d models, so a
        tapped hole is modelled as a plain cylinder drilled to the pre-tap
        diameter. Matching that diameter to a standard tap-drill size is the best
        topology-only signal. Candidate-level and explicitly low-confidence: the
        same diameter can equally be a plain clearance hole.
        """
        features: list[dict[str, Any]] = []
        index = 1
        for hole in hole_features:
            diameter = hole.get("parameters", {}).get("diameter_mm")
            if not isinstance(diameter, NUMERIC_TYPES) or diameter <= 0:
                continue
            # A through hole at a round drill size reads as a clearance hole, not a
            # tapped one. Tapped holes are typically blind; keep blind/unknown only.
            hole_metadata = hole.get("hole_metadata")
            if isinstance(hole_metadata, dict) and hole_metadata.get("hole_depth_kind") == "through":
                continue
            match = None
            for nominal, tap_drill in self._TAP_DRILL_DIAMETERS_MM:
                if abs(float(diameter) - tap_drill) <= max(0.05, tap_drill * 0.025):
                    match = (nominal, tap_drill)
                    break
            if match is None:
                continue
            nominal, tap_drill = match
            face_ids = list(hole.get("geometry_refs", {}).get("faces", []))
            feature = {
                "id": f"feat_thread_{index:03d}",
                "type": "thread",
                "name": f"Thread candidate ({nominal})",
                "geometry_refs": {"faces": face_ids},
                "parameters": {
                    "nominal_size": nominal,
                    "tap_drill_diameter_mm": tap_drill,
                    "measured_diameter_mm": round(float(diameter), 3),
                },
                "parameter_source": "mock",
                "parameter_confidence": "low",
                "editable": False,
                "editability": "not_editable",
                "writeback_strategy": "none",
                "editability_reason": "Thread is an inferred annotation on a hole, not an independently editable feature.",
                "intent": {"role": "threaded_fastener_interface"},
                "recognition": {
                    "method": "rule_based_tap_drill_diameter_match",
                    "confidence": "low",
                    "uncertainty_notes": [
                        f"Hole diameter matches the {nominal} tap-drill size; no helical thread geometry was detected.",
                        "The same diameter may instead be a plain clearance/passage hole — verify against design intent.",
                    ],
                    "signals": {
                        "real_topology": bool(recognition_context.get("real_topology")),
                        "matched_hole_feature_id": hole.get("id"),
                    },
                },
            }
            features.append(feature)
            index += 1
        return features
