# Copilot Session Handoff

Last updated: 2026-11-27.

## Start Here

Read `AGENTS.md` and `.github/copilot-instructions.md` for workflow, commands
and invariants, `docs/architecture.md` and `docs/implementation.md` for
architecture and conventions, then this current-status section and `PLAN.md`
for approved scope. `README.md` documents user-facing operation. Do not depend
on session-local todos or artifacts to resume work. Distinguish tests, live API
checks, physical-display observations and endurance acceptance; historical
findings below are not current vitals or deployment instructions.

## Current Release

**Branch:** `main` | **Release:** v0.5.28 | **CYD firmware:** 0.2.27

Release v0.5.28 keeps active non-video scenes visible in Android and PC manager
remote views during display sleep, matching the CYD. Validation passed: 494
Python tests plus successful `cyd` and `cyd-dev` PlatformIO builds.

### Recent Features

- **CYD companion** — captive-portal enrollment, touch controls, signed OTA
  with manifest verification, SHA-256 payload check, health-confirm rollback
- **Background music** — Plex stations/playlists/mixes, internet radio,
  weekday sources, smart-playlist builder, music sync groups
- **Carousel transport** — Pause/Resume/Next via manager, heartbeat-gated
- **Remote view** — display-shaped reconstruction with sleep state
- **Android manager** — native settings editors, compact home cards,
  interactive preview with guarded controls
- **Bluetooth speakers** — pair/connect, live output switching, wired fallback
- **Weekly sleep** — per-day schedule with temporary wake override

## Current Development Deployment

Both targets received the latest iteration with pinned SSH and `--restart-kiosk`:

| Target | Address | Notes |
|--------|---------|-------|
| Pi 2 | `192.168.0.249` | Development target |
| Pi 3 | `192.168.0.251` | Development target |
| Zero 2 W | `192.168.0.246` | Development target |

Confirm addresses via mDNS before each deployment:
```sh
avahi-browse -rpt _web-display._tcp
```

Services are active, `/health` is OK, and deployed `app.py` hashes match the
repository. Private configuration, API keys and carousel playlists are preserved
across development pushes.

## Outstanding Work And Acceptance

### Open

- **CYD physical acceptance** — weather/dashboard/music layout visual checks,
  touch button edge/corner/duplicate suppression, OTA repeated power-loss
  recovery, heap under repeated train payloads
- **Endurance** — long-duration TLS stall recurrence, Pi 2 dashboard
  acknowledgment, sync group audible accuracy, Pi 2 CPU cost, leader-loss
  behavior
- **Android phone** — portrait/landscape layout, weather glyph quality,
  control feedback, native settings interaction
- **Music audio** — physical listening on Bluetooth speakers, video-audio
  over Bluetooth, sustained endurance with background music enabled

### Known Issues

- **Pi 3 TLS handshake stall** — isolated without service restart; a slow-lossy
  TLS client hangs the accept backlog. Fixed in `app.py` with bounded
  handshake threads. Long-duration recurrence acceptance remains open.
- **Pi 2 dashboard retries** — `verify-persistent-scenes` still open; dashboard
  acknowledgment retries are implemented but multi-cycle/endurance checks
  have not proven the recurrence fixed.
- **Pi 3 built-in AP** — WPA2 authentication fails with Android/iPhone clients.
  Use a compatible USB adapter; never substitute an open AP.

### Deferred

- A/B root images (Phase B)
- Image uploads, native manager playlist editing, general-purpose overlays,
  animated transitions

## Architecture Reference

See `docs/architecture.md` for full system architecture. Key files:

| Area | Files |
|------|-------|
| Device service | `src/webdisplay/app.py` |
| Carousel/playlist | `src/webdisplay/carousel.py`, `src/webdisplay/scene_player.py`, `src/webdisplay/browser.py` |
| Music | `src/webdisplay/plex.py`, `src/webdisplay/music.py`, `src/webdisplay/music_player.py` |
| Providers | `src/webdisplay/raildata.py`, `src/webdisplay/weather.py` |
| Network/Wi-Fi | `src/webdisplay/netmonitor.py`, `src/webdisplay/network.py`, `src/webdisplay/wifi_helper.py` |
| Updates | `src/webdisplay/updater.py`, `src/webdisplay/releases.py` |
| Remote view | `src/webdisplay/remote_view.py`, `src/webdisplay/sleep.py` |
| Manager | `src/webdisplay_manager/` |
| UI | `ui/` (device + manager) |
| CYD | `cyd/` |
| Android | `android/` |
| Image | `image/stage-web-display/` |

## API Specification

The device API is documented in `docs/api/openapi.yaml` (OpenAPI 3.0). Update
it whenever the API surface changes. Always validate with both tools:

```sh
npx @redocly/cli lint docs/api/openapi.yaml
npx @apidevtools/swagger-cli validate docs/api/openapi.yaml
```

## Quick Commands

See `docs/developer-guide.md` for full reference.

```sh
# Tests
PYTHONPATH=src python3 -m unittest discover -s tests -v
(cd ui && npm ci && npm run typecheck && npm test && npm run build)

# Build
./build-image.sh                          # development
VARIANT=release ./build-image.sh          # release
(cd cyd && pio run -e cyd)               # CYD firmware

# Deploy
tools/push-to-device.sh webdebug@DEVICE_IP
tools/push-to-device.sh --restart-kiosk webdebug@DEVICE_IP
```
