"""Prove manifest.json's requirements satisfy the code that imports them.

The camera client is pip-installed from the wheel manifest.json pins, not
vendored, so nothing in this tree can be unit-tested against it. What CAN be
proven, and is the one thing that breaks silently on a live instance, is
that every symbol the integration imports from `aionanit_jr` exists in the
wheel that manifest.json names. Run after `pip install` of those requirements.

A wheel that installs but lacks a symbol fails at integration setup, on the
estate, with the config entry left in setup_error.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "aionanit_jr"


def main() -> int:
    manifest = json.loads((ROOT / "manifest.json").read_text())
    reqs = manifest["requirements"]
    pinned = [r for r in reqs if r.split("@", 1)[0].replace("-", "_") == PACKAGE]
    if len(pinned) != 1 or "@https://" not in pinned[0]:
        print(f"CANNOT RUN: manifest.json must pin exactly one {PACKAGE} wheel by URL, got {reqs}")
        return 1
    if importlib.util.find_spec("aionanit") is not None:
        print("CANNOT PASS: upstream `aionanit` is importable beside the fork")
        return 1

    checked = 0
    missing: list[str] = []
    files = [p for p in ROOT.rglob("*.py") if ".github" not in p.parts and PACKAGE not in p.parts]
    for path in files:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] != PACKAGE:
                    continue
                module = importlib.import_module(node.module)
                for alias in node.names:
                    checked += 1
                    if hasattr(module, alias.name):
                        continue
                    try:
                        importlib.import_module(f"{node.module}.{alias.name}")
                    except ImportError:
                        missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.module}.{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.level > 0 and node.module and PACKAGE in node.module:
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno} relative import of {PACKAGE} - it is pip-installed, not vendored")

    for line in missing:
        print("MISSING", line)
    # A sweep that read nothing is not a pass.
    if checked == 0:
        print(f"CANNOT PASS: no {PACKAGE} imports found in {len(files)} files")
        return 1
    print(f"{checked} symbols across {len(files)} files resolve against the pinned wheel")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
