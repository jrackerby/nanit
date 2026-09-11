# Nanit

Home Assistant integration for Nanit baby monitors — the camera stream, the
Sound + Light unit, the sensors behind them, and the diary.

Nanit publishes no local API and no documented cloud one: everything here goes
through the same cloud endpoints the phone app uses, which is why sign-in needs
the emailed MFA code and why a session can end and ask for it again. Camera and
speaker traffic is local once the devices are found; discovery uses zeroconf
where it works, and both addresses can be entered by hand where it does not.

## Lineage

This is a **fork** of
[`wealthystudent/ha-nanit`](https://github.com/wealthystudent/ha-nanit), whose
author remains a codeowner in `manifest.json`. Bug reports about behaviour this
fork did not change are better filed upstream. What this fork adds over it:

- **`aionanit_sl`** — a vendored client for the **Sound + Light** device
  (protobuf over the vendor's transport), which the upstream `aionanit`
  dependency does not cover. This is what makes the `light`, `media_player`
  and sound-machine controls exist at all.
- Diary logging as actions (`log_diaper_change`, `log_bottle_feed`,
  `log_nursing`, `delete_diary_log`, `import_history`).
- Diagnostics with credential redaction, and a re-auth flow that refuses a
  different email than the entry was created with.

## What it creates

Platforms: `camera`, `media_player`, `light`, `switch`, `select`, `number`,
`sensor`, `binary_sensor`. It also registers a frontend resource set and brand
assets, and exposes the diary actions above plus downloadable diagnostics.

## Actions

| action | what it does |
|---|---|
| `nanit.reset_stream` | restart the camera stream |
| `nanit.log_diaper_change` | write a diaper entry to the Nanit diary |
| `nanit.log_bottle_feed` | write a bottle feed |
| `nanit.log_nursing` | write a nursing session |
| `nanit.delete_diary_log` | remove an entry |
| `nanit.import_history` | backfill diary history |

Every field is described in `services.yaml`, which is what Home Assistant
renders in Developer Tools and the automation editor.

## Configuration

Config flow with MFA. Required: email, password, and the emailed MFA code, then
device selection. Optional: explicit camera and speaker IPs where discovery
does not find them, and whether to store credentials for re-auth.

`after_dependencies: zeroconf` — discovery is used when available and not
required.

## Install

**Via HACS.** HACS → ⋮ → *Custom repositories* → `https://github.com/jrackerby/nanit`,
category **Integration**. Install, restart Home Assistant, then add it under
*Settings → Devices & Services → Add Integration → "Nanit"*.

The integration lives at the repository **root**, not under
`custom_components/`. `hacs.json` declares `content_in_root: true`, so HACS
copies the root into `/config/custom_components/nanit/`.

## Development

Issues and feature requests: **[jrackerby/nanit/issues](https://github.com/jrackerby/nanit/issues)**.

CI runs [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest)
and HACS validation on every push. hassfest scans `custom_components/*` and
takes no path argument, so `.github/workflows/validate.yml` stages this repo
into that layout before invoking it; the repo itself stays root-layout because
`hacs.json` declares `content_in_root: true`.

Pushing a `manifest.json` whose `version` has changed tags and publishes a
release automatically — that is the only supported way to cut one.
