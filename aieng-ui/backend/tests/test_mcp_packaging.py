from __future__ import annotations

import pytest
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
AIENG_ROOT = BACKEND_ROOT.parents[1] / "aieng"


def test_pyproject_exposes_branded_mcp_entrypoints() -> None:
    project = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["name"] == "aieng-workbench-mcp"
    assert project["scripts"]["aieng-workbench-mcp"] == "aieng_workbench_mcp.__main__:main"
    assert project["scripts"]["aieng-workbench-mcp-smoke"] == "aieng_workbench_mcp.smoke:main"

    dependencies = set(project["dependencies"])
    cad_extra = set(project["optional-dependencies"]["cad"])
    # Assert the invariant, not the literal: the backend must require the
    # aieng-format built alongside it. A hard-coded version here breaks on every
    # release bump and teaches nothing when it does.
    core_version = tomllib.loads(
        (AIENG_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    assert f"aieng-format>={core_version}" in dependencies, (
        f"backend must depend on the sibling aieng-format {core_version}; "
        f"declared: {sorted(d for d in dependencies if d.startswith('aieng-format'))}"
    )
    assert "build123d>=0.10.0" not in dependencies
    assert "build123d>=0.10.0" in cad_extra


def test_python_module_entrypoint_uses_existing_mcp_server_main() -> None:
    from app.mcp_server import main as server_main
    from aieng_workbench_mcp.__main__ import main as branded_main

    assert branded_main is server_main


def test_cli_runtime_options_configure_headless_modes(monkeypatch, tmp_path: Path) -> None:
    from app import mcp_server as ms

    # _apply_cli_runtime_options intentionally mutates process-wide environment
    # state. Register every affected key with monkeypatch so later tests do not
    # inherit this test's managed-approval or data-directory configuration.
    for key in (
        "AIENG_BACKEND_URL",
        "AIENG_MCP_BLOCK_APPROVAL_TOOLS",
        "AIENG_MCP_MANAGED_APPROVAL",
        "AIENG_MCP_REQUIRE_GUIDES",
        "AIENG_PLATFORM_DATA",
    ):
        monkeypatch.delenv(key, raising=False)

    old_backend_url = ms._BACKEND_URL
    try:
        ms._apply_cli_runtime_options(
            backend_url="",
            approval_mode="block",
            data_dir=str(tmp_path),
            require_guides=False,
        )
        assert ms._BACKEND_URL == ""
        assert "AIENG_BACKEND_URL" not in ms.os.environ
        assert ms.os.environ["AIENG_MCP_BLOCK_APPROVAL_TOOLS"] == "1"
        assert "AIENG_MCP_MANAGED_APPROVAL" not in ms.os.environ
        assert ms.os.environ["AIENG_MCP_REQUIRE_GUIDES"] == "0"
        assert Path(ms.os.environ["AIENG_PLATFORM_DATA"]) == tmp_path.resolve()

        ms._apply_cli_runtime_options(
            backend_url="http://127.0.0.1:8000/",
            approval_mode="managed",
            data_dir=None,
            require_guides=True,
        )
        assert ms._BACKEND_URL == "http://127.0.0.1:8000"
        assert ms.os.environ["AIENG_BACKEND_URL"] == "http://127.0.0.1:8000"
        assert ms.os.environ["AIENG_MCP_MANAGED_APPROVAL"] == "1"
        assert "AIENG_MCP_BLOCK_APPROVAL_TOOLS" not in ms.os.environ
        assert ms.os.environ["AIENG_MCP_REQUIRE_GUIDES"] == "1"
    finally:
        ms._BACKEND_URL = old_backend_url


def test_packaged_mcp_smoke_stubbed_cad(tmp_path: Path) -> None:
    from aieng_workbench_mcp.smoke import run_smoke

    result = run_smoke(data_dir=tmp_path, real_cad=False)

    assert result["status"] == "ok"
    assert result["mode"] == "stubbed-cad"
    assert result["approval_block_code"] == "approval_blocked"
    assert "smoke_base_plate" in result["cad_named_parts"]


@pytest.mark.skipif(shutil.which("python") is None, reason="python executable not found")
def test_installed_mcp_wheel_smoke(tmp_path: Path) -> None:
    """Build both wheels, install into a clean venv, and run the packaged smoke."""
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "build"],
        check=True,
    )

    # Build aieng-format wheel first (MCP dependency).
    subprocess.run(
        [sys.executable, "-m", "build", AIENG_ROOT.as_posix()],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    aieng_wheels = sorted((AIENG_ROOT / "dist").glob("*.whl"))
    assert aieng_wheels, "aieng-format wheel not produced"
    aieng_wheel = aieng_wheels[-1]

    # Build aieng-workbench-mcp wheel.
    subprocess.run(
        [sys.executable, "-m", "build", BACKEND_ROOT.as_posix()],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    mcp_wheels = sorted((BACKEND_ROOT / "dist").glob("*.whl"))
    assert mcp_wheels, "aieng-workbench-mcp wheel not produced"
    mcp_wheel = mcp_wheels[-1]

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", venv_dir.as_posix()], check=True)

    if sys.platform == "win32":
        python_bin = venv_dir / "Scripts" / "python.exe"
        smoke_cli = venv_dir / "Scripts" / "aieng-workbench-mcp-smoke.exe"
    else:
        python_bin = venv_dir / "bin" / "python"
        smoke_cli = venv_dir / "bin" / "aieng-workbench-mcp-smoke"

    subprocess.run(
        [python_bin.as_posix(), "-m", "pip", "install", "--quiet", aieng_wheel.as_posix(), mcp_wheel.as_posix()],
        check=True,
    )

    version_check = subprocess.run(
        [python_bin.as_posix(), "-c", "import aieng_workbench_mcp; print(aieng_workbench_mcp.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert version_check.stdout.strip().startswith("0.1.0a")

    # This install resolves `mcp` freely, so it is the canary for the exact break
    # #463 fixed: an unbounded transitive dep shipping a major version. Report
    # which major an external agent actually gets — the port supports both, and a
    # future break should say WHICH side it landed on rather than only "import
    # failed".
    sdk_major = subprocess.run(
        [python_bin.as_posix(), "-c",
         "from importlib.metadata import version; print(version('mcp').split('.')[0])"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert sdk_major in {"1", "2"}, f"unexpected mcp major resolved: {sdk_major!r}"
    print(f"[packaging smoke] external install resolved mcp major {sdk_major}")

    assert smoke_cli.exists()
    smoke_result = subprocess.run(
        [smoke_cli.as_posix()],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"status": "ok"' in smoke_result.stdout
    assert '"approval_block_code": "approval_blocked"' in smoke_result.stdout
