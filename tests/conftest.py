"""Make the integration importable as the package `nanit` with HA stubbed out.

The repo root IS the component directory, so it is registered under the
domain name explicitly rather than relying on the checkout directory being
called `nanit`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ha_stubs  # noqa: E402

ha_stubs.install()

_ROOT = Path(__file__).resolve().parent.parent

if "nanit" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "nanit", _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)]
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["nanit"] = _module
    _spec.loader.exec_module(_module)
