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

## The camera's request surface

- **Every `PUT_*` is request/response and this camera answers it.** Measured
  2026-09-13 on firmware 6.58.615 over the CLOUD relay: `PUT_SETTINGS`,
  `PUT_CONTROL` and `PUT_STREAMING` each round-tripped in well under a second,
  with no timeout and no retry. #4's five days of `timed out after 10.0s` were
  not a protocol that lacks responses - do not "fix" a write timeout by making
  a PUT fire-and-forget.
- **`GET_CONTROL` answers but omits `night_light`**, even though
  `async_get_control()` asks for it by name (`GetControl(night_light=True)`).
  So `state.control.night_light` stays `None` from startup until a
  `PUT_CONTROL` echo fills it, and anything deriving on/off from it reads
  nothing (#20). `settings` has no such gap: `GET_SETTINGS` populates it at
  startup and `night_light_brightness` survives a restart, so the settings
  block - not control - is what a value written earlier can be read back from.

## HA's stream worker on the RTMPS source

- **`homeassistant.components.stream` retries a dead source by itself and
  never asks the camera for anything.** Each attempt flips `Stream.available`
  True at its start and False at its failure (so the update callback sees
  every retry as an edge, not just the first), and the wait between attempts
  grows 10/20/30 s and only resets after a 300 s healthy run. Nothing in that
  loop re-sends `PUT_STREAMING`, so once the camera's push lapses every retry
  opens an empty ingest (observed 2026-09-13 02:20-02:22). `update_source()`
  restarts the worker at once and zeroes that backoff, the same string
  included - it is the lever, `stop()`/`start()` is not.
- **The worker logs the failing source verbatim, access token and all.**
  `redact_credentials` strips `user:pass@` and `?token=`; Nanit's token sits
  in the URL path, so each `Error opening stream` line carries a live JWT
  (#27).
- **A WebRTC viewer is invisible to `Stream.outputs()`.** go2rtc dials the
  RTMPS source itself off `stream_source()` at offer time; count sessions from
  `async_handle_async_webrtc_offer` to `close_webrtc_session` or a
  WebRTC-only viewer reads as nobody watching (#24). go2rtc's own
  *preload* of that source panics in its rtmp handler on this estate (#26).
