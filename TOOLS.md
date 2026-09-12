# TOOLS

What this repo's instruments do, refuse to do, and lie about. Every line is
a claim with a timestamp - re-verify before building a plan on one, and edit
it when it stops being true. Rules about the work are in `jrackerby/HA`'s
`tools/work_docs/LAW.md`; hassfest's quality-scale and layout traps are in
`kiosk-pi/TOOLS.md`.

## `manifest.json` requirements

- **hassfest REFUSES ANY REQUIREMENT CONTAINING A SPACE, before its regex
  ever runs** (`validate_requirements_format`, `if " " in req`). A PEP 508
  direct reference must be written `name@https://...` - no whitespace round
  the `@`. That form matches `PACKAGE_REGEX`, and pip, uv and HA's own
  `is_installed()` (which handles `req.url` explicitly) all install it.
  Measured 2026-09-11 on #13: a summary of `PACKAGE_REGEX` alone said the
  spaced form passed; the space check sits above the regex and it did not.
- **HA installs a direct-reference wheel with no index involved**, so the
  asset URL is the whole contract: `jrackerby/aionanit`'s release.yml asserts
  the wheel's exact filename before tagging, and this repo's `tests` job
  installs exactly what the manifest names and resolves every `aionanit_jr`
  symbol the code imports against it (`.github/scripts/check_requirements.py`).
