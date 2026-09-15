# TOOLS

What this repo's instruments do, refuse to do, and lie about. Every line is
a claim with a timestamp - re-verify before building a plan on one, and edit
it when it stops being true. Rules about the work are in `jrackerby/HA`'s
`tools/work_docs/LAW.md` (the quality scale is its §15); hassfest's layout
trap is in that repo's `tools/work_docs/TOOLS.md`.

## `manifest.json` requirements

- **hassfest REFUSES ANY REQUIREMENT CONTAINING A SPACE, before its regex
  ever runs** (`validate_requirements_format`, `if " " in req`). A PEP 508
  direct reference must be written `name@https://...` - no whitespace round
  the `@`. That form matches `PACKAGE_REGEX`, and pip, uv and HA's own
  `is_installed()` (which handles `req.url` explicitly) all install it.
  Measured on #13: a summary of `PACKAGE_REGEX` alone said the
  spaced form passed; the space check sits above the regex and it did not.
- **HA installs a direct-reference wheel with no index involved**, so the
  asset URL is the whole contract: `jrackerby/aionanit`'s release.yml asserts
  the wheel's exact filename before tagging, and this repo's `tests` job
  installs exactly what the manifest names and resolves every `aionanit_jr`
  symbol the code imports against it (`.github/scripts/check_requirements.py`).

## The camera's request surface

- **Every `PUT_*` is request/response and this camera answers it.** Measured
  on firmware 6.58.615 over the CLOUD relay: `PUT_SETTINGS`,
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
  **The night light therefore does NOT restore on/off** and reads `unknown`
  between a restart and the first write - a `RestoreEntity` restore here
  republished the previous run's value indefinitely rather than reloading one
  the device would confirm. Do not "fix" that unknown by restoring it, and do
  not derive it from brightness.

## HA's stream worker on the RTMPS source

- **`homeassistant.components.stream` retries a dead source by itself and
  never asks the camera for anything.** Each attempt flips `Stream.available`
  True at its start and False at its failure (so the update callback sees
  every retry as an edge, not just the first), and the wait between attempts
  grows 10/20/30 s and only resets after a 300 s healthy run. Nothing in that
  loop re-sends `PUT_STREAMING`, so once the camera's push lapses every retry
  opens an empty ingest (observed over a two-minute window). `update_source()`
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

## Testing this repo

- **The component's own syntax needs Python >= 3.12 and Home Assistant needs
  >= 3.14.2** - `__init__.py` uses a PEP 695 `type` statement, so a 3.11
  interpreter cannot even parse the package, and `pip install homeassistant`
  for the running version fails on any interpreter older than 3.14.2. A
  session on an older container gets a real interpreter from `uv python
  install 3.13` rather than concluding the repo is untestable.
- **`tests/ha_stubs.py` fabricates the absent imports, and ONLY the absent
  ones.** It resolves each root first and stubs what is genuinely missing, so
  the same file runs against real `aionanit_jr` in CI and against a
  fabrication in a bare container. That filtering is load-bearing, not tidy:
  an unconditional stub would answer for the wheel and turn the `tests` job's
  symbol check green on nothing. Green here is logic only - LAW.md §16 - and
  says nothing about a running Home Assistant.
- **A FABRICATED STUB IS A CLASS, SO IT CANNOT BE A DECORATOR.** Every
  `homeassistant.*` name `ha_stubs` invents is a class; using one to
  decorate REPLACES the method with a stub instance, and calling that
  instance returns the instance and runs none of the method. A test of any
  `@callback` method therefore passes while asserting nothing - measured on
  #25's recovery-gate tests, which read green against the fix AND against
  master until `homeassistant.core.callback` was made a pass-through in
  `_IDENTITY_ATTRS`. Any other decorator reached from a stubbed root needs
  the same entry; running the new test against master is what catches it.
- **The suite runs inside the existing `tests` job, deliberately.** A new job
  would add a check name that master's required-context list does not carry,
  so it would report and gate nothing (jrackerby/whisker-ting#17).
