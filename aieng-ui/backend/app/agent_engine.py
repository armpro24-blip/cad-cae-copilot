from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from . import action_selector
from .consistency_gate import (
    consistency_samples,
    consistency_threshold,
    evaluate_consistency,
    low_consistency_reply,
    plan_decision_signature,
)

def sanitize_llm_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if k != "api_key"}


def sanitize_selected_geometry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    pointers = [str(item) for item in raw.get("pointers") or [] if isinstance(item, str) and item.startswith("@")]
    faces: list[dict[str, Any]] = []
    for item in raw.get("faces") or []:
        if not isinstance(item, dict):
            continue
        pointer = item.get("pointer")
        if not isinstance(pointer, str) or not pointer.startswith("@"):
            continue
        roles = [str(role) for role in item.get("roles") or [] if isinstance(role, str)]
        faces.append(
            {
                "pointer": pointer,
                "label": str(item.get("label") or pointer),
                "surface_type": str(item.get("surface_type") or "unknown"),
                "roles": roles,
            }
        )
    highlighted = [
        str(item)
        for item in raw.get("highlightedFaceIds") or raw.get("highlighted_face_ids") or []
        if isinstance(item, str)
    ]
    if not pointers and faces:
        pointers = [str(face["pointer"]) for face in faces]
    if not pointers and not faces and not highlighted:
        return None
    return {
        "pointers": pointers,
        "faces": faces,
        "highlighted_face_ids": highlighted,
    }


def _inject_path(path: Path) -> tuple[str, bool]:
    candidate = str(path)
    if candidate in sys.path:
        return candidate, False
    sys.path.insert(0, candidate)
    return candidate, True


def _remove_path(candidate: str, injected: bool) -> None:
    if injected:
        try:
            sys.path.remove(candidate)
        except ValueError:
            pass


def _clear_stale_aieng_modules(expected_root: Path) -> None:
    """Drop cached ``aieng[.submodule]`` entries that were loaded from a
    different root than ``expected_root``.

    Pytest test pollution: when one test imports ``aieng`` via an
    injected ``aieng_root`` that points at a tmp_path stub, the package
    is cached in ``sys.modules`` with that stub's ``__path__``. A later
    test that injects the real ``aieng/src`` path then fails on
    ``from aieng.benchmarking.providers import ...`` because Python
    reuses the cached package and looks for submodules in the stub
    path. Purging the stale cache before each ``_inject_path`` call
    forces a fresh resolution.
    """
    try:
        expected_resolved = expected_root.resolve()
    except OSError:
        return
    stale: list[str] = []
    for name, module in list(sys.modules.items()):
        if name != "aieng" and not name.startswith("aieng."):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            continue
        try:
            Path(module_file).resolve().relative_to(expected_resolved)
        except ValueError:
            stale.append(name)
        except OSError:
            stale.append(name)
    for name in stale:
        sys.modules.pop(name, None)


def _coerce_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed


def _build_provider(settings: Any, llm_config: dict[str, Any]) -> Any:
    src = settings.aieng_root / "src"
    _clear_stale_aieng_modules(src)
    candidate, injected = _inject_path(src)
    try:
        from aieng.benchmarking.providers import ProviderConfig, build_provider

        config = ProviderConfig(
            provider=str(llm_config.get("provider") or "openai-compatible"),
            model=str(llm_config.get("model") or "configured-model"),
            api_key=llm_config.get("api_key") or None,
            api_key_env=llm_config.get("api_key_env") or None,
            base_url=llm_config.get("base_url") or None,
            input_price_per_million_tokens=llm_config.get("input_price_per_million_tokens"),
            output_price_per_million_tokens=llm_config.get("output_price_per_million_tokens"),
            max_output_tokens=int(llm_config.get("max_output_tokens") or 8192),
            temperature=float(llm_config.get("temperature") or 0.0),
            top_p=float(llm_config.get("top_p") or 1.0),
            seed=llm_config.get("seed"),
        )
        return build_provider(config)
    finally:
        _remove_path(candidate, injected)


def _compact_context(project_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(project_summary, dict):
        return {}
    if project_summary.get("schema_version") and "agent_brief" in project_summary:
        return {
            "project": project_summary.get("project"),
            "package": project_summary.get("package"),
            "agent_brief": project_summary.get("agent_brief"),
            "cad": {
                "status": (project_summary.get("cad") or {}).get("status")
                if isinstance(project_summary.get("cad"), dict) else None,
                "geometry_evidence_level": (project_summary.get("cad") or {}).get("geometry_evidence_level")
                if isinstance(project_summary.get("cad"), dict) else None,
                "known_geometry": (project_summary.get("cad") or {}).get("known_geometry")
                if isinstance(project_summary.get("cad"), dict) else {},
                "missing_information": (project_summary.get("cad") or {}).get("missing_information")
                if isinstance(project_summary.get("cad"), dict) else [],
            },
            "cae": {
                "present": (project_summary.get("cae") or {}).get("present")
                if isinstance(project_summary.get("cae"), dict) else None,
                "results_available": (project_summary.get("cae") or {}).get("results_available")
                if isinstance(project_summary.get("cae"), dict) else None,
                "available_fields": (project_summary.get("cae") or {}).get("available_fields")
                if isinstance(project_summary.get("cae"), dict) else [],
            },
            "design_targets": {
                "count": (project_summary.get("design_targets") or {}).get("count")
                if isinstance(project_summary.get("design_targets"), dict) else 0,
            },
            "computed_metrics": {
                "metrics_count": (project_summary.get("computed_metrics") or {}).get("metrics_count")
                if isinstance(project_summary.get("computed_metrics"), dict) else 0,
            },
            "target_comparison": project_summary.get("target_comparison"),
            "available_actions": project_summary.get("available_actions") or [],
            "warnings": project_summary.get("warnings") or [],
        }
    cae = project_summary.get("cae") if isinstance(project_summary.get("cae"), dict) else {}
    return {
        "project": project_summary.get("project"),
        "manifest": project_summary.get("manifest"),
        "validation": project_summary.get("validation"),
        "derived": project_summary.get("derived"),
        "viewer": project_summary.get("viewer"),
        "cae": {
            "present": cae.get("present"),
            "results_available": cae.get("results_available"),
            "available_fields": cae.get("available_fields") or [],
            "constraints_count": cae.get("constraints_count"),
            "loads_count": cae.get("loads_count"),
            "evidence_count": cae.get("evidence_count"),
        },
    }


def _tool_names(runtime_tools: list[dict[str, Any]]) -> set[str]:
    return {str(tool.get("name")) for tool in runtime_tools if tool.get("name")}


def _tool_requires_approval(runtime_tools: list[dict[str, Any]], tool_name: str) -> bool:
    for tool in runtime_tools:
        if tool.get("name") == tool_name:
            return bool(tool.get("requires_approval"))
    return False


def _step(
    step_id: str,
    kind: str,
    tool_name: str,
    description: str,
    inputs: dict[str, Any] | None = None,
    approval_required: bool = False,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "kind": kind,
        "tool_name": tool_name,
        "name": tool_name,
        "description": description,
        "input": inputs or {},
        "status": "pending",
        "approval_required": approval_required,
    }


def _infer_template_request(message: str) -> tuple[str | None, dict[str, Any]]:
    """Best-effort local extraction for the Agent Action Sandbox.

    This intentionally stays lightweight: strong LLMs can do better via the
    LLM planning path, while the heuristic path gives users a no-API-key way to
    try controlled AIENG template actions. Extracted values are only fed into
    controlled template validators; invalid guesses return structured errors.
    """
    text = message.lower()
    template_id: str | None = None
    if any(tok in text for tok in ["plate_with_hole", "plate with hole", "hole plate"]):
        template_id = "plate_with_hole"
    elif any(tok in text for tok in ["cantilever_beam", "cantilever", "beam"]):
        template_id = "cantilever_beam"
    if template_id is None:
        return None, {}

    def number_for(patterns: list[str]) -> float | None:
        for pattern in patterns:
            m = re.search(pattern, message, flags=re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1))
                except (TypeError, ValueError):
                    return None
        return None

    params: dict[str, Any] = {}
    material_aliases = {
        "aluminum_6061_t6": ["aluminum_6061_t6", "6061", "aluminum", "aluminium"],
        "steel_s235": ["steel_s235", "s235", "steel"],
        "stainless_304": ["stainless_304", "304", "stainless"],
    }
    for material_id, aliases in material_aliases.items():
        if any(alias in text for alias in aliases):
            params["material"] = material_id
            break

    length = number_for([r"(?:length)\D{0,12}([0-9]+(?:\.[0-9]+)?)\s*mm", r"([0-9]+(?:\.[0-9]+)?)\s*mm\D{0,8}(?:long|length)"])
    width = number_for([r"(?:width)\D{0,12}([0-9]+(?:\.[0-9]+)?)\s*mm", r"([0-9]+(?:\.[0-9]+)?)\s*mm\D{0,8}(?:wide|width)"])
    height = number_for([r"(?:height)\D{0,12}([0-9]+(?:\.[0-9]+)?)\s*mm"])
    thickness = number_for([r"(?:thickness)\D{0,12}([0-9]+(?:\.[0-9]+)?)\s*mm"])
    hole = number_for([r"(?:hole diameter|diameter)\D{0,12}([0-9]+(?:\.[0-9]+)?)\s*mm"])
    load = number_for([r"(?:load|force)\D{0,12}([0-9]+(?:\.[0-9]+)?)\s*n", r"([0-9]+(?:\.[0-9]+)?)\s*n"])
    stress = number_for([r"(?:stress)\D{0,16}([0-9]+(?:\.[0-9]+)?)\s*mpa", r"([0-9]+(?:\.[0-9]+)?)\s*mpa"])
    displacement = number_for([r"(?:displacement|deflection)\D{0,16}([0-9]+(?:\.[0-9]+)?)\s*mm"])

    if length is not None:
        params["length_mm"] = length
    if width is not None:
        params["width_mm"] = width
    if stress is not None:
        params["allowable_stress_MPa"] = stress
    if template_id == "cantilever_beam":
        if height is not None:
            params["height_mm"] = height
        elif thickness is not None:
            params["height_mm"] = thickness
        if load is not None:
            params["tip_load_N"] = load
        if displacement is not None:
            params["max_displacement_mm"] = displacement
    elif template_id == "plate_with_hole":
        if thickness is not None:
            params["thickness_mm"] = thickness
        if hole is not None:
            params["hole_diameter_mm"] = hole
        if load is not None:
            params["tensile_load_N"] = load
    return template_id, params


def heuristic_agent_plan(
    *,
    message: str,
    project_id: str | None,
    patch_json: dict[str, Any] | None,
    runtime_tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], str]:
    text = message.lower()
    tools = _tool_names(runtime_tools)
    base_input = {"project_id": project_id} if project_id else {}
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []

    if not project_id:
        warnings.append("No project_id was provided; agent can explain a plan but cannot inspect or execute project tools.")

    if project_id and "aieng.agent_context" in tools:
        steps.append(_step("agent_context", "tool", "aieng.agent_context", "Read compact CAD/CAE context for the connected agent.", base_input))

    if project_id and "aieng.inspect_package" in tools:
        steps.append(_step("inspect", "tool", "aieng.inspect_package", "Read current .aieng package context.", base_input))

    template_id, template_params = _infer_template_request(message)
    if project_id and template_id and "engineering_template.preview" in tools:
        template_input = {**base_input, "template_id": template_id, "parameters": template_params}
        steps.append(
            _step(
                "template_preview",
                "tool",
                "engineering_template.preview",
                f"Preview controlled template {template_id} from the natural-language request.",
                template_input,
            )
        )
        wants_action = any(
            token in text
            for token in [
                "design", "create", "generate", "build", "model", "cad", "fixture",
                "design", "generate", "create", "model", "target",
            ]
        )
        if wants_action and "engineering_template.save_draft" in tools:
            steps.append(
                _step(
                    "template_save_draft",
                    "tool",
                    "engineering_template.save_draft",
                    "Save reviewed template draft artifacts into the package.",
                    template_input,
                    approval_required=True,
                )
            )
        if wants_action and "engineering_template.adopt_targets" in tools:
            steps.append(
                _step(
                    "template_adopt_targets",
                    "tool",
                    "engineering_template.adopt_targets",
                    "Adopt saved template target suggestions into task/design_targets.yaml.",
                    {**base_input, "template_id": template_id},
                    approval_required=True,
                )
            )
        if wants_action and "engineering_template.generate_cad_fixture" in tools:
            steps.append(
                _step(
                    "template_cad_fixture",
                    "tool",
                    "engineering_template.generate_cad_fixture",
                    "Write deterministic CAD fixture metadata and mark downstream evidence stale.",
                    template_input,
                    approval_required=True,
                )
            )
        if wants_action:
            warnings.append(
                "Controlled template actions were inferred from the request. Package-writing steps pause for approval and do not run CAD/mesh/solver tools."
            )

    wants_modification = patch_json is not None or any(
        token in text
        for token in ["apply", "patch", "edit", "model"]
    )
    if wants_modification and not template_id:
        # The mcp.check / mcp.parse_patch / mcp.prepare_execution steps that used
        # to be planned here returned `status: unavailable` on every provider —
        # the bridge they fronted never had an implementation. A patch proposal
        # is applied through `aieng.apply_shape_ir_patch` (dry_run first), which
        # is approval-gated and therefore never planned automatically.
        if project_id and patch_json:
            warnings.append(
                "A patch_json was provided. Patch proposals are applied with "
                "aieng.apply_shape_ir_patch (use dry_run=true to preview); that "
                "tool is approval-gated and is not planned automatically."
            )
        else:
            warnings.append(
                "Modeling request detected, but no executable patch_json was provided. "
                "Ask for a concrete patch proposal, or use the cad.* tools behind their "
                "modeling-plan confirmation."
            )

    if project_id and any(token in text for token in ["preview", "glb", "stl"]) and "aieng.generate_preview" in tools:
        steps.append(_step("preview", "tool", "aieng.generate_preview", "Refresh the web preview artifact.", base_input))

    if project_id and not steps and "aieng.inspect_package" in tools:
        steps.append(_step("inspect", "tool", "aieng.inspect_package", "Default safe inspection.", base_input))

    reply = (
        "I built a guarded agent plan. Mutating CAD work needs a concrete, supported "
        "patch or the cad.* modeling tools behind their plan confirmation."
    )
    return steps, warnings, reply


def llm_agent_plan(
    *,
    settings: Any,
    message: str,
    project_id: str | None,
    project_summary: dict[str, Any] | None,
    selected_geometry: dict[str, Any] | None,
    runtime_tools: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    llm_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], str, str | None]:
    provider = _build_provider(settings, llm_config)
    executable_tools = [
        {
            "name": tool.get("name"),
            "requires_approval": bool(tool.get("requires_approval")),
            "description": tool.get("description"),
        }
        for tool in runtime_tools
    ]
    capability_brief = [
        {
            "name": cap.get("name"),
            "source": cap.get("source"),
            "category": cap.get("category"),
            "mutates_cad": cap.get("mutates_cad"),
            "mutates_package": cap.get("mutates_package"),
            "available": cap.get("available"),
        }
        for cap in capabilities[:80]
    ]
    system_prompt = (
        "You are an engineering CAD/CAE planning agent. Return only JSON. "
        "You may propose steps only using the provided executable runtime tools. "
        "Do not invent tools. For CAD mutation, use the cad.* tools behind their "
        "modeling-plan confirmation; do not execute unsupported arbitrary modeling. "
        "Never update claims unless an explicit claim update tool is provided."
    )
    user_prompt = json.dumps(
        {
            "user_message": message,
            "project_id": project_id,
            "selected_geometry": selected_geometry or None,
            "project_context": _compact_context(project_summary),
            "executable_runtime_tools": executable_tools,
            "capabilities": capability_brief,
            "response_schema": {
                "reply": "short human-readable response",
                "warnings": ["list of warnings or missing inputs"],
                "steps": [
                    {
                        "id": "stable id",
                        "tool_name": "one executable_runtime_tools.name",
                        "description": "what this step does",
                        "input": {"project_id": project_id},
                    }
                ],
            },
        },
        ensure_ascii=False,
    )
    raw = provider.generate(system_prompt=system_prompt, user_prompt=user_prompt)
    parsed = _coerce_json_object(raw)
    raw_steps = parsed.get("steps") if isinstance(parsed.get("steps"), list) else []
    tool_set = _tool_names(runtime_tools)
    mcp_tool_names = {
        str(cap.get("name"))
        for cap in capabilities
        if str(cap.get("source") or "").lower().endswith("mcp")
    }
    steps: list[dict[str, Any]] = []
    warnings = [str(item) for item in parsed.get("warnings") or []]
    base_input = {"project_id": project_id} if project_id else {}

    for index, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or item.get("name") or "")
        if tool_name not in tool_set:
            warnings.append(f"LLM proposed unavailable tool and it was dropped: {tool_name}")
            continue
        step_input = item.get("input") if isinstance(item.get("input"), dict) else {}
        merged = {**base_input, **step_input}
        steps.append(
            _step(
                str(item.get("id") or f"step_{index + 1}"),
                "mcp_tool" if tool_name in mcp_tool_names else "tool",
                tool_name,
                str(item.get("description") or tool_name),
                merged,
                approval_required=_tool_requires_approval(runtime_tools, tool_name),
            )
        )
    return steps, warnings, str(parsed.get("reply") or "I built a guarded agent plan."), raw


def test_llm_provider(
    settings: Any,
    llm_config: dict[str, Any],
    *,
    api_key: str | None = None,
    verify_connection: bool = False,
) -> dict[str, Any]:
    """Test LLM provider configuration and optionally verify API connectivity.

    Returns:
        Dict with ``config_ready``, ``connection_verified``, ``api_key_present``,
        and ``error_message``. Never includes the actual API key.
    """
    provider_name = str(llm_config.get("provider") or "openai-compatible")
    model = str(llm_config.get("model") or "configured-model")
    base_url = llm_config.get("base_url") or None

    # Step 1: Check API key presence
    src = settings.aieng_root / "src"
    _clear_stale_aieng_modules(src)
    candidate, injected = _inject_path(src)
    try:
        from aieng.benchmarking.providers import ProviderConfig

        config = ProviderConfig(
            provider=provider_name,
            model=model,
            api_key=api_key,
            api_key_env=llm_config.get("api_key_env") or None,
            base_url=base_url,
        )
        resolved_key = config.resolved_api_key()
        if not resolved_key:
            env_name = config.resolved_api_key_env()
            source = f"environment variable {env_name!r}" if env_name else "a provider API key"
            return {
                "config_ready": False,
                "connection_verified": False,
                "provider": provider_name,
                "model": model,
                "base_url": base_url,
                "api_key_present": False,
                "error_message": f"API key not provided and {source} is not set",
            }
    finally:
        _remove_path(candidate, injected)

    # Step 2: Validate config structure (can we build the provider?)
    enriched_config = dict(llm_config)
    enriched_config["api_key"] = api_key
    try:
        provider = _build_provider(settings, enriched_config)
    except Exception as exc:
        return {
            "config_ready": False,
            "connection_verified": False,
            "provider": provider_name,
            "model": model,
            "base_url": base_url,
            "api_key_present": True,
            "error_message": f"Invalid config: {exc}",
        }

    if not verify_connection:
        return {
            "config_ready": True,
            "connection_verified": False,
            "provider": provider_name,
            "model": model,
            "base_url": base_url,
            "api_key_present": True,
            "error_message": None,
        }

    # Step 3: Real API connectivity test (lightweight)
    try:
        if provider_name.lower() == "anthropic":
            provider.client.messages.create(
                model=model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
        else:
            provider.client.chat.completions.create(
                model=model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
        return {
            "config_ready": True,
            "connection_verified": True,
            "provider": provider_name,
            "model": model,
            "base_url": base_url,
            "api_key_present": True,
            "error_message": None,
        }
    except Exception as exc:
        return {
            "config_ready": True,
            "connection_verified": False,
            "provider": provider_name,
            "model": model,
            "base_url": base_url,
            "api_key_present": True,
            "error_message": f"API call failed: {exc}",
        }


def build_agent_plan(
    *,
    settings: Any,
    message: str,
    project_id: str | None,
    project_summary: dict[str, Any] | None,
    runtime_tools: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    llm_config: dict[str, Any],
    selected_geometry: dict[str, Any] | None = None,
    patch_json: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    llm_raw: str | None = None
    mode = "heuristic"
    consistency: dict[str, Any] | None = None
    requires_user_input = False
    if llm_config and not dry_run:
        try:
            def _draw_plan() -> tuple[list[dict[str, Any]], list[str], str, str | None]:
                return llm_agent_plan(
                    settings=settings,
                    message=message,
                    project_id=project_id,
                    project_summary=project_summary,
                    selected_geometry=selected_geometry,
                    runtime_tools=runtime_tools,
                    capabilities=capabilities,
                    llm_config=llm_config,
                )

            samples = consistency_samples(llm_config)
            if samples <= 1:
                # Default path (gate disabled): a single judged sample, unchanged.
                steps, warnings, reply, llm_raw = _draw_plan()
            else:
                # Multi-sample consistency gate (#220): judge the decision by
                # cross-sample agreement, not the model's self-reported confidence.
                threshold = consistency_threshold(llm_config)
                drawn = [_draw_plan() for _ in range(samples)]
                signatures = [plan_decision_signature(plan[0]) for plan in drawn]
                consistency = evaluate_consistency(signatures, threshold=threshold)
                if consistency["consistent"]:
                    chosen = next(
                        plan
                        for plan, sig in zip(drawn, signatures)
                        if sig == consistency["modal_signature"]
                    )
                    steps, warnings, reply, llm_raw = chosen
                else:
                    # Low consistency → ask the user instead of acting on a guess.
                    steps = []
                    warnings = [
                        "LLM consistency below threshold; routed to ask_user instead "
                        "of acting on a low-agreement guess."
                    ]
                    reply = low_consistency_reply(consistency)
                    llm_raw = drawn[0][3] if drawn else None
                    requires_user_input = True
            mode = "llm"
        except Exception as exc:
            steps, warnings, reply = heuristic_agent_plan(
                message=message,
                project_id=project_id,
                patch_json=patch_json,
                runtime_tools=runtime_tools,
            )
            errors.append(f"LLM planning unavailable; used heuristic planner: {type(exc).__name__}: {exc}")
    else:
        steps, warnings, reply = heuristic_agent_plan(
            message=message,
            project_id=project_id,
            patch_json=patch_json,
            runtime_tools=runtime_tools,
        )

    runtime_tool_names = _tool_names(runtime_tools)
    filtered: list[dict[str, Any]] = []
    for step in steps:
        tool_name = str(step.get("tool_name") or step.get("name") or "")
        if tool_name not in runtime_tool_names:
            warnings.append(f"Dropped unavailable runtime tool: {tool_name}")
            continue
        step["approval_required"] = bool(step.get("approval_required")) or _tool_requires_approval(runtime_tools, tool_name)
        filtered.append(step)

    mutating = any(step.get("approval_required") for step in filtered)
    return {
        "reply": reply,
        "mode": mode,
        "message": message,
        "project_id": project_id,
        "selected_geometry": selected_geometry,
        "agent_context": _compact_context(project_summary),
        "action_selection": action_selector.select_actions_for_intent(
            message=message,
            available_actions=(
                project_summary.get("available_actions")
                if isinstance(project_summary, dict) and isinstance(project_summary.get("available_actions"), list)
                else []
            ),
        ),
        "steps": filtered,
        "requires_approval": mutating,
        "preview": {
            "step_count": len(filtered),
            "tools": [step.get("tool_name") for step in filtered],
            "would_execute": [step.get("tool_name") for step in filtered if not step.get("approval_required")],
            "approval_gated": [step.get("tool_name") for step in filtered if step.get("approval_required")],
            "side_effects": [
                "Runtime events and audit records are written.",
                "MCP bridge steps are dry-run/preflight unless an explicit execution tool is wired.",
            ],
            "warnings": warnings,
        },
        "warnings": warnings,
        "errors": errors,
        "llm_raw": llm_raw,
        "llm_config": sanitize_llm_config(llm_config),
        "requires_user_input": requires_user_input,
        "consistency": consistency,
    }
