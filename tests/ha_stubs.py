"""A stubbed Home Assistant surface, enough to import this integration's modules.

WHY THIS EXISTS AND WHAT IT IS NOT. Home Assistant 2026.9 requires Python
>= 3.14.2 and pulls several hundred dependencies; neither is available in
every session that needs to touch this code, and this repo has never had a
suite at all (`validate.yml` passes `test-command: ""`). This module
fabricates any `homeassistant.*` / `aionanit_jr*` module on import so the
platform modules can be loaded and their STATE LOGIC exercised in isolation.

It proves logic, never integration: LAW.md §16 is explicit that green on a
stub authorises nothing. Anything about how HA actually drives these
entities has to be observed on a running instance.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType

_STUB_ROOTS = ("homeassistant", "aionanit_jr", "aiohttp", "websockets", "google", "voluptuous")


class _StubMeta(type):
    """Metaclass so a fabricated class answers any attribute and subscript.

    Needed for four real shapes reached on import here: `ColorMode.BRIGHTNESS`
    (attribute on a class), `CoordinatorEntity[NanitPushCoordinator]`
    (subscript), bare use as a base class, and the vendored protobuf modules
    calling a runtime-version validator at import time.
    """

    def __getattr__(cls, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        value = _fabricate(name)
        setattr(cls, name, value)
        return value

    def __getitem__(cls, _item):
        return cls


class _StubBase(metaclass=_StubMeta):
    """Permissive instance side: constructing or calling a stub never fails."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return _fabricate(name)()


def _fabricate(name: str) -> type:
    """A fresh stub class, usable as a base, a constant, or a callable."""
    return _StubMeta(name, (_StubBase,), {})


def _identity(obj):
    """Return the decorated object unchanged."""
    return obj


# DECORATORS THAT MUST NOT BE FABRICATED. A fabricated stub is a CLASS, so
# using one as a decorator REPLACES the method with a stub instance: calling
# it silently returns the stub and runs none of the code. Every test of a
# `@callback` method then passes while asserting nothing -- measured on the
# #25 recovery-gate tests, which read green against both the fix and master.
# These names pass their argument straight through instead.
_IDENTITY_ATTRS = {
    ("homeassistant.core", "callback"),
}


class _StubModule(ModuleType):
    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        if (self.__name__, name) in _IDENTITY_ATTRS:
            value = _identity
        else:
            value = _fabricate(name)
        setattr(self, name, value)
        return value


class _StubLoader(Loader):
    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        return None


class _StubFinder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root not in _STUB_ROOTS:
            return None
        spec = ModuleSpec(fullname, _StubLoader(), is_package=True)
        spec.submodule_search_locations = []
        return spec


def install() -> None:
    """Stub only what is genuinely absent, then get out of the way.

    A STUB MUST NEVER SHADOW A REAL PACKAGE. The repo's `tests` CI job
    pip-installs `aionanit_jr` from the wheel `manifest.json` pins, and the
    whole point of that job is to catch a symbol that moved. A finder sitting
    unconditionally at the head of `sys.meta_path` would answer for it and
    turn that check green on a fabrication. So each root is resolved first
    and dropped from the stub set when the real thing is importable -- which
    is why the same file behaves differently in CI and in a bare container,
    deliberately.

    Idempotent.
    """
    global _STUB_ROOTS
    missing = []
    for root in _STUB_ROOTS:
        try:
            found = importlib.util.find_spec(root) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(root)
    _STUB_ROOTS = tuple(missing)
    if _STUB_ROOTS and not any(isinstance(f, _StubFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _StubFinder())
