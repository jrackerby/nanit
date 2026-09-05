# Nanit

Home Assistant integration for Nanit baby monitors — cameras, sound and light,
sensors, and the diary API.

## Lineage

This is the **estate fork**. It was previously versioned `1.13.0-estate`, which
is a valid semver *pre-release* and therefore sorts **before** `1.13.0` — so
every ordering comparison read the fork as older than the release it forked
from. It is now `1.14.0` and the fork is recorded here instead of in a version
string that has to sort (jrackerby/HA#483).

Depends on `aionanit`; `aionanit_sl` in this repo carries the Sound+Light
device support that upstream does not.

## What it creates

Platforms include `camera`, `media_player`, `light`, `switch`, `select`,
`number`, `sensor`, `binary_sensor`. Registers frontend resources and a
`brand/` asset set, and exposes services plus diagnostics.

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

> **That path has two owners today.** `jrackerby/HA` also submodules this repo
> as `custom_components/nanit` and writes the same directory on deploy. Until
> that cutover is settled (jrackerby/HA#483), a HACS install and a `git push ha
> master` will fight over it — install here only if you are not deploying this
> component from `jrackerby/HA`.

## Development

Issues and feature requests: **[jrackerby/nanit/issues](https://github.com/jrackerby/nanit/issues)**.

CI runs [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest)
and HACS validation on every push. hassfest scans `custom_components/*` and
takes no path argument, so `.github/workflows/validate.yml` stages this repo
into that layout before invoking it; the repo itself stays root-layout because
`jrackerby/HA` submodules it at that path.

Pushing a `manifest.json` whose `version` has changed tags and publishes a
release automatically — that is the only supported way to cut one.
