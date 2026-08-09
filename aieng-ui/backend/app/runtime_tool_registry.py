"""Registration of app-scoped MCP/runtime tool handlers.

Tool implementations are split into domain-focused submodules so the registry
stays composable and each domain can evolve independently.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .legacy_app_symbols import sync_main_symbols
from .logging_utils import log_exception

LOGGER = logging.getLogger("app.app_factory")


def _is_windows() -> bool:
    """Whether this process runs on Windows.

    An indirection on purpose: tests that need Windows-only behaviour used to
    do ``monkeypatch.setattr("os.name", "nt")``, which mutates the interpreter
    globally — on Linux every later ``Path()`` then tries to build a
    ``WindowsPath`` and raises, taking pytest's own machinery down with it.
    Patching this function keeps that intent local and platform-safe.
    """
    return os.name == "nt"


def _split_ccx_cmd(command: str, *, platform: str | None = None) -> list[str]:
    """Split an operator-provided ccx command into subprocess argv."""
    import shlex

    platform = platform or ("nt" if re.match(r"^[A-Za-z]:\\", command.strip()) else os.name)
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


def _conda_run_for_env_ccx(exe_path: str) -> list[str] | None:
    """If ``exe_path`` is a ccx executable inside a conda env, return a
    ``conda run -n <env> ccx`` argv (when a conda-family launcher resolves), else None.

    A bare conda-env ``ccx.exe`` invoked WITHOUT activation crashes on Windows with
    an access violation (0xC0000005) because it cannot load its runtime DLLs;
    prepending the env's ``Library/bin`` to PATH is not enough — full activation is
    required. Rewriting to the conda-run launcher activates the env so ccx loads its
    DLLs. Windows-only: on POSIX a bare conda-env ccx runs fine, so we leave it
    unchanged to avoid the per-call ``conda run`` overhead.
    """
    if not _is_windows():
        return None
    try:
        p = Path(exe_path)
    except (TypeError, ValueError):
        return None
    if not p.name.lower().startswith("ccx"):
        return None
    lowered = [seg.lower() for seg in p.parts]
    if "envs" not in lowered:
        return None
    idx = lowered.index("envs")
    if idx + 1 >= len(p.parts):
        return None
    env_name = p.parts[idx + 1]
    launcher = (
        _resolve_launcher("conda")
        or _resolve_launcher("mamba")
        or _resolve_launcher("micromamba")
    )
    if not launcher:
        return None
    return [launcher, "run", "-n", env_name, "ccx"]


#: Where a conda env keeps ccx, relative to the env root.
_CCX_ENV_RELATIVE_PATHS: tuple[tuple[str, ...], ...] = (
    ("Library", "bin", "ccx.exe"),   # Windows
    ("bin", "ccx"),                  # POSIX
    ("bin", "ccx_linux"),
)


def _conda_envs_roots() -> list[Path]:
    """Candidate conda ``envs`` directories, derived from the running process."""
    roots: list[Path] = []

    def _add(path: Path | None) -> None:
        if path and path.is_dir() and path not in roots:
            roots.append(path)

    # CONDA_EXE is <root>/Scripts/conda.exe (Windows) or <root>/bin/conda (POSIX)
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        exe = Path(conda_exe)
        if len(exe.parents) >= 2:
            _add(exe.parents[1] / "envs")
    # An ACTIVE env is <root>/envs/<name>; the base env is <root> itself.
    for var in ("CONDA_PREFIX", "CONDA_ROOT"):
        value = os.environ.get(var)
        if not value:
            continue
        prefix = Path(value)
        if prefix.parent.name == "envs":
            _add(prefix.parent)
        _add(prefix / "envs")
    return roots


def _discover_ccx_in_conda_envs() -> tuple[str | None, str | None]:
    """Find a ccx executable inside a sibling conda env. Returns ``(path, env_name)``.

    The documented CalculiX install deliberately uses a SEPARATE env (installing
    it into the backend env downgrades OpenSSL), so ccx is never on the backend
    process's PATH and ``shutil.which`` cannot see it. Rather than make the
    operator hand-write ``AIENG_CCX_CMD`` in the exact shell that launches the
    backend — easy to forget, and silently disables every solver path — derive
    it: scan the conda ``envs`` directory for the known ccx locations.

    Envs whose name mentions calculix/ccx are preferred so a machine with
    several envs resolves predictably. Read-only and bounded to one directory
    level; returns ``(None, None)`` when nothing is found.
    """
    for envs_dir in _conda_envs_roots():
        try:
            candidates = sorted(p for p in envs_dir.iterdir() if p.is_dir())
        except OSError:
            continue
        # Prefer an env that names itself after the solver.
        candidates.sort(key=lambda p: 0 if ("calculix" in p.name.lower() or "ccx" in p.name.lower()) else 1)
        for env_dir in candidates:
            for rel in _CCX_ENV_RELATIVE_PATHS:
                exe = env_dir.joinpath(*rel)
                if exe.is_file():
                    return str(exe), env_dir.name
    return None, None


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
        # Direct PATH hit: substitute the ABSOLUTE launcher path — EXCEPT a bare
        # conda-env ccx.exe, which crashes on Windows (DLL load); that is
        # rewritten to the conda-run launcher that activates the env.
        #
        # Resolving to an absolute path matters: a bare name is re-resolved by
        # CreateProcess at launch time, and libraries loaded in between (gmsh in
        # particular) mutate PATH, after which `conda` no longer resolves and the
        # solver dies with a bare "[WinError 2] file not found" — which is how
        # every sizing-sweep variant failed after meshing.
        resolved_direct = shutil.which(launcher)
        if resolved_direct:
            if len(parts) == 1:
                conda_form = _conda_run_for_env_ccx(resolved_direct)
                if conda_form:
                    return conda_form, (
                        f"AIENG_CCX_CMD points at a conda-env ccx ({launcher!r}); "
                        f"auto-using the conda-run launcher to avoid a Windows "
                        f"DLL-load crash"
                    )
            return [resolved_direct, *parts[1:]], (
                f"AIENG_CCX_CMD launcher {launcher!r} found on PATH ({resolved_direct})"
            )
        # Fallback: resolve a conda-family launcher via its *_EXE env var / .exe
        # (the Windows case where bare `conda` is a shim not on the process PATH).
        resolved = _resolve_launcher(launcher)
        if resolved:
            return [resolved, *parts[1:]], (
                f"resolved AIENG_CCX_CMD launcher {launcher!r} via launcher env hint"
            )
        return None, (
            f"AIENG_CCX_CMD launcher {launcher!r} is not resolvable in the backend "
            f"process. Ensure {launcher!r} is on PATH / its launcher env var "
            f"(e.g. CONDA_EXE) is set in the shell that starts the backend."
        )
    for candidate in ("ccx", "ccx_linux", "ccx2.21", "ccx_static"):
        path = shutil.which(candidate)
        if path:
            conda_form = _conda_run_for_env_ccx(path)
            if conda_form:
                return conda_form, (
                    f"found a conda-env ccx on PATH ({path}); auto-using the "
                    f"conda-run launcher to avoid a Windows DLL-load crash"
                )
            return [path], f"found {candidate!r} on PATH"
    # Last resort: the DOCUMENTED install puts ccx in its own conda env
    # (`conda create -n calculix-env -c conda-forge calculix`), which is never on
    # the backend env's PATH — so requiring AIENG_CCX_CMD was really just asking
    # the operator to hand-write a path we can derive. Scan sibling envs.
    discovered, env_name = _discover_ccx_in_conda_envs()
    if discovered:
        conda_form = _conda_run_for_env_ccx(discovered)
        if conda_form:
            return conda_form, (
                f"auto-discovered ccx in conda env {env_name!r} ({discovered}); "
                f"using the conda-run launcher (no AIENG_CCX_CMD needed)"
            )
        return [discovered], (
            f"auto-discovered ccx in conda env {env_name!r} ({discovered}) "
            f"(no AIENG_CCX_CMD needed)"
        )
    return None, (
        "AIENG_CCX_CMD is not set, no ccx executable was found on PATH, and no "
        "conda env containing ccx was discoverable. Install CalculiX — on "
        "Windows + conda, `conda create -n calculix-env -c conda-forge calculix` "
        "is auto-detected; otherwise set AIENG_CCX_CMD explicitly."
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

    from .runtime_registry import ai_preprocessing as _ai_preprocessing
    from .runtime_registry import aieng as _aieng
    from .runtime_registry import cad as _cad
    from .runtime_registry import cae as _cae
    from .runtime_registry import opt as _opt
    from .runtime_registry import standards as _standards

    aieng_handlers = _aieng.register_aieng_tools(_rt, active_settings, app_context, _schema)
    _ai_preprocessing.register_ai_preprocessing_tools(_rt, active_settings, app_context, _schema)
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
