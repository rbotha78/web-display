# Raspberry Pi Web Display

A 32-bit Raspberry Pi OS appliance for Raspberry Pi 2 and Raspberry Pi 3 that turns
them into networked displays running Chromium in kiosk mode. Features include a
configurable scene carousel (web pages, dashboards, video, weather, train
departures, now-playing music), authenticated HTTPS administration, Wi-Fi
provisioning with captive-portal discovery, signed application updates, and
PC/Android display managers.

An optional ESP32-2432S028R companion display shows the active scene with touch
controls for carousel and music transport.

## Quick Start

### Build the Image

```sh
./build-image.sh                              # development build
WPA_COUNTRY=US ./build-image.sh               # set Wi-Fi regulatory country
VARIANT=release ./build-image.sh              # hardened release build
```

Output goes to `deploy/`.

### Flash and First Use

1. Write the `.img.gz` (development) or `.img.xz` (release) to an SD card
2. Insert in the Pi, attach HDMI and optionally Ethernet, power on
3. The fallback screen shows the DHCP administration address or a setup AP
4. Open `https://<address>:8443/`, verify the TLS certificate fingerprint
5. Enter the pairing code and set an administrator password

### Deploy to Development Devices

```sh
tools/push-to-device.sh webdebug@DEVICE_IP          # copy source, restart services
tools/push-to-device.sh --restart-kiosk webdebug@DEVICE_IP  # also restart kiosk
```

Locate devices with: `avahi-browse -rpt _web-display._tcp`

## Build, Test and Deploy

See [docs/developer-guide.md](docs/developer-guide.md) for full command reference.

### Python Tests

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

### React UI

```sh
cd ui && npm ci && npm run typecheck && npm test && npm run build
(cd ui && npm run dev)   # hot reload
```

### ESP32 Companion

```sh
cd cyd && pio run -e cyd    # production build
```

### Android Manager

```sh
cd android && ./gradlew testDebugUnitTest lintDebug assembleDebug
```

## Administration

### Per-Device Admin Page

Open the display's HTTPS address to access:

- **Global settings** — display defaults, sleep schedule, provider credentials,
  networking, updates and access keys
- **Carousel** — configure ordered scenes (web, dashboard, video, train,
  weather, now-playing music)
- **Background music** — Plex library radio, internet radio, smart playlist
  builder, artist/album mixes
- **Remote view** — live reconstruction of the current scene

### PC Manager

```sh
PYTHONPATH=src python3 -m webdisplay_manager
```

Manages multiple displays from one page. Discovers devices via mDNS, enrolls
with TLS fingerprint pinning, and provides inline controls for music, carousel,
and remote view.

### Android Manager

Build and sideload the debug APK from `android/`. Supports the same operations
as the PC manager with native Jetpack Compose UI.

## Architecture

See [docs/architecture.md](docs/architecture.md) for system architecture details.

Key components:
- **`src/webdisplay/`** — Python HTTPS service, scene rendering, kiosk supervisor
- **`src/webdisplay_manager/`** — PC-side device management server
- **`ui/`** — React UI (shared by device admin and PC manager)
- **`cyd/`** — ESP32 companion firmware (PlatformIO/LovyanGFX)
- **`android/`** — Android manager (Kotlin/Jetpack Compose)
- **`image/stage-web-display/`** — pi-gen customisation stages
- **`tools/`** — build, deployment and bundle utilities

## Documentation

| File | Purpose |
|------|---------|  
| [docs/architecture.md](docs/architecture.md) | System architecture |
| [docs/implementation.md](docs/implementation.md) | Implementation conventions |
| [docs/developer-guide.md](docs/developer-guide.md) | Build, test, deploy commands |
| [docs/release-process.md](docs/release-process.md) | Release procedure |
| [docs/api/openapi.yaml](docs/api/openapi.yaml) | Device API specification (OpenAPI 3.0) |
| `HANDOFF.md` | Current implementation status, hardware observations |
| `PLAN.md` | Approved scope and designs |
| `.github/copilot-instructions.md` | Detailed workflow and invariants |

## License

Copyright (C) 2026 Robert Botha. Licensed under the GNU General Public License,
version 3 or (at your option) any later version. See `LICENSE`. The firmware
image also contains Raspberry Pi OS, Debian and Chromium, which keep their own
licenses.
