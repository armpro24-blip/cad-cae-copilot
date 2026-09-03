"""A recovery path that raises `NameError` is worse than no recovery path.

Found by CodeRabbit on a line I had just written, and it turned out to be
pre-existing at ten others: `runtime_registry/aieng.py`, `cae.py` and `opt.py`
each call `log_exception(...)` from inside `except:` blocks without importing it,
so every one of those handlers raised `NameError` instead of logging — turning a
handled, recoverable failure into an unhandled crash inside a tool.

Eleven sites, invisible because nothing exercises an error path whose trigger is
itself an unexpected failure. The name resolves only when something imports it,
and `sync_main_symbols(globals())` does not.

The guard is deliberately blunt: any module that CALLS a helper must be able to
RESOLVE it, checked by importing the module rather than by reading the source —
`ai_preprocessing.py` used to import it inside the handler, which works and made
the invariant unstatable, so it was normalised to the module-level form.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_REGISTRY = Path(__file__).resolve().parents[1] / "app" / "runtime_registry"

#: Helpers these modules call from recovery paths. Add a name here when a new
#: cross-module helper starts being used the same way.
_HELPERS = ("log_exception",)


def _modules() -> list[str]:
    return sorted(
        path.stem for path in _REGISTRY.glob("*.py") if path.stem != "__init__"
    )


@pytest.mark.parametrize("module_name", _modules())
def test_every_helper_a_registry_module_calls_is_resolvable(module_name: str) -> None:
    source = (_REGISTRY / f"{module_name}.py").read_text(encoding="utf-8")
    called = [h for h in _HELPERS if re.search(rf"(?<![\w.]){h}\s*\(", source)]
    if not called:
        pytest.skip(f"{module_name} calls none of {_HELPERS}")

    module = importlib.import_module(f"app.runtime_registry.{module_name}")
    missing = [name for name in called if not hasattr(module, name)]
    assert missing == [], (
        f"app.runtime_registry.{module_name} calls {missing} without importing "
        "it, so those call sites raise NameError. They sit in `except:` blocks, "
        "which is why nothing noticed: the failure only happens when something "
        "else already failed."
    )


def test_the_check_would_catch_a_missing_import() -> None:
    """A guard whose detector never fires would pass forever."""
    assert re.search(r"(?<![\w.])log_exception\s*\(", "    log_exception(LOGGER, 'x')")
    # An attribute call is somebody else's namespace, not this module's.
    assert not re.search(r"(?<![\w.])log_exception\s*\(", "logging_utils.log_exception(x)")
    # A definition or import mentioning the name is not a call.
    assert not re.search(r"(?<![\w.])log_exception\s*\(", "from ..logging_utils import log_exception")
