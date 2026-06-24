"""Registration of app-scoped MCP/runtime tool handlers.

Tool implementations are split into domain-focused submodules so the registry
stays composable and each domain can evolve independently.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any

from .legacy_app_symbols import sync_main_symbols
from .logging_utils import log_exception

LOGGER = logging.getLogger("app.app_factory")


def _split_ccx_cmd(command: str, *, platform: str | None = None) -> list[str]:
    """Split an operator-provided ccx command into subprocess argv."""
    import shlex

    platform = platform or os.name
    parts = shlex.split(command, posix=platform != "nt")
    if platform == "nt":
        parts = [
            part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in {"'", '"'} else part
            for part in parts
        ]
    return parts


# Conda-family launchers are frequently NOT bare PATH executables on Windows —
# `conda` is a .bat shim, and the real entry point is reachable via CONDA_EXE
# (set whenever a conda env is activated, e.g. the shell that launches uvicorn).
# Map each launcher to its env var hint + candidate executable names so the
# recommended `AIENG_CCX_CMD="conda run -n <env> ccx"` form actually resolves.
_LAUNCHER_RESOLUTION: dict[str, tuple[str, tuple[str, ...]]] = {
    "conda": ("CONDA_EXE", ("conda.exe", "conda.bat", "conda")),
    "mamba": ("MAMBA_EXE", ("mamba.exe", "mamba.bat", "mamba")),
    "micromamba": ("MAMBA_EXE", ("micromamba.exe", "micromamba")),
}


def _resolve_launcher(name: str) -> str | None:
    """Resolve a command launcher, falling back to conda-family heuristics.

    A bare ``shutil.which`` hit always wins. Otherwise, for a known conda-family
    launcher, try its ``*_EXE`` env var (preferred — it points at a real
    executable) then common executable names. ``.exe`` is preferred over ``.bat``
    so the resolved path is directly runnable via subprocess on Windows.
    """
    direct = shutil.which(name)
    if direct:
        return direct
    spec = _LAUNCHER_RESOLUTION.get(name.lower())
    if not spec:
        return None
    env_var, candidates = spec
    env_path = os.environ.get(env_var)
    if env_path and os.path.exists(env_path):
        return env_path
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def resolve_ccx_command() -> tuple[list[str] | None, str]:
    """Resolve the CalculiX (ccx) command, respecting AIENG_CCX_CMD.

    Returns ``(parts, reason)`` where ``parts`` is the subprocess argv (e.g.
    ``["/usr/bin/ccx"]`` or ``["C:\\...\\conda.exe", "run", "-n", "calculix-env",
    "ccx"]``) or ``None`` when ccx cannot be found, and ``reason`` is a
    human-readable explanation suitable for surfacing in diagnostics — it
    distinguishes "env var unset" from "env var set but launcher unresolved".
    """
    ccx_env = os.environ.get("AIENG_CCX_CMD")
    if ccx_env:
        try:
            parts = _split_ccx_cmd(ccx_env)
        except ValueError as exc:
            return None, f"AIENG_CCX_CMD could not be parsed: {exc}"
        if not parts:
            return None, "AIENG_CCX_CMD is set but empty."
        launcher = parts[0]
        # Direct PATH hit: keep the original argv unchanged (subprocess resolves
        # the launcher at run time, as before — no behavioral change).
        if shutil.which(launcher):
            return parts, f"AIENG_CCX_CMD launcher {launcher!r} found on PATH"
        # Fallback: resolve a conda-family launcher via its *_EXE env var / .exe
        # (the Windows case where bare `conda` is a shim not on the process PATH).
        resolved = _resolve_launcher(launcher)
        if resolved:
            return [resolved, *parts[1:]], (
                f"resolved AIENG_CCX_CMD launcher {launcher!r} via launcher env hint"
            )
        return None, (
            f"AIENG_CCX_CMD launcher {launcher!r} is not resolvable in the backend "
            f"process. Set AIENG_CCX_CMD to the absolute ccx executable path "
            f"(most reliable on Windows), or ensure {launcher!r} is on PATH / its "
            f"launcher env var is set in the shell that starts the backend."
        )
    for candidate in ("ccx", "ccx_linux", "ccx2.21", "ccx_static"):
        path = shutil.which(candidate)
        if path:
            return [path], f"found {candidate!r} on PATH"
    return None, (
        "AIENG_CCX_CMD is not set and no ccx executable was found on PATH. "
        "Install CalculiX and set AIENG_CCX_CMD (absolute ccx path recommended)."
    )


def _resolve_ccx_cmd() -> list[str] | None:
    """Back-compat wrapper: return just the resolved ccx argv (or None)."""
    return resolve_ccx_command()[0]


@dataclass(frozen=True)
class RuntimeToolHandlers:
    apply_shape_ir_patch: Any
    derive_topology_optimization_problem: Any
    run_topology_optimization: Any
    writeback_topology_optimization: Any
    topology_to_sizing: Any
    run_assembly_topology_optimization: Any


def register_runtime_tools(*, active_settings: Any, app_context: Any) -> RuntimeToolHandlers:
    """Orchestrate domain-specific runtime tool registrations."""
    from . import runtime as _rt
    from . import runtime_tools
    from .runtime_tool_schemas import get_schema as _schema

    from .runtime_registry import aieng as _aieng
    from .runtime_registry import cad as _cad
    from .runtime_registry import cae as _cae
    from .runtime_registry import opt as _opt
    from .runtime_registry import standards as _standards

    aieng_handlers = _aieng.register_aieng_tools(_rt, active_settings, app_context, _schema)
    _cad.register_cad_tools(_rt, active_settings, app_context, _schema)
    _cae.register_cae_tools(_rt, active_settings, app_context, _schema)
    opt_handlers = _opt.register_opt_tools(_rt, active_settings, app_context, _schema)
    _standards.register_standards_tools(_rt, active_settings, app_context, _schema)



    runtime_tools.register_engineering_template_tools(_rt, active_settings)

    return RuntimeToolHandlers(
        apply_shape_ir_patch=aieng_handlers["apply_shape_ir_patch"],
        derive_topology_optimization_problem=opt_handlers["derive_topology_optimization_problem"],
        run_topology_optimization=opt_handlers["run_topology_optimization"],
        writeback_topology_optimization=opt_handlers["writeback_topology_optimization"],
        topology_to_sizing=opt_handlers["topology_to_sizing"],
        run_assembly_topology_optimization=opt_handlers["run_assembly_topology_optimization"],
    )
