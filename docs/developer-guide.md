# Developer Guide

## API specification

The OpenAPI 3.0 spec at `docs/api/openapi.yaml` documents the device API.
Update it whenever the display's API surface changes.

Always validate with **both** tools before committing:

```sh
npx @redocly/cli lint docs/api/openapi.yaml
npx @apidevtools/swagger-cli validate docs/api/openapi.yaml
```

## Requirements

- Python 3.10+
- Node 24 (for UI development only; not needed at runtime)
- Docker with privileged-container support (for image builds, ~30 GB free)
- Native Linux filesystem (for image builds)
- OpenSSL 3 with `pkeyutl -rawin` support (for bundle signing)
- `python3-segno` (for tests)

## Build

### Raspberry Pi OS Image

```sh
./build-image.sh                              # development build (key-only SSH)
WPA_COUNTRY=US ./build-image.sh               # set Wi-Fi regulatory country
VARIANT=release ./build-image.sh              # hardened release build (SSH masked)
FRESH=1 ./build-image.sh                      # force full base rebuild
```

Output goes to `deploy/`. Development builds compress with `gz` (~8 min);
release builds use `xz` (~30 min). The first build keeps the pi-gen container
with the finished base (stages 0–2); later builds reuse it.

### Application Update Bundle

```sh
python3 tools/make_update_bundle.py --output-dir bundle
python3 tools/verify_update_bundle.py bundle/*.wdupdate
```

Requires `UPDATE_SIGNING_KEY` environment variable or `--key-file`.

### ESP32 Companion Firmware

```sh
cd cyd && pio run -e cyd          # production build (HTTPS only)
pio run -e cyd-dev                # development build (allows HTTP)
pio run -e cyd-ota-test           # OTA test build
```

Device uploads use `pio remote -a HomePC`, not local serial.

### Android Manager

```sh
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

Requires JDK 17, Android platform 36, build tools 35.0.0.
APK output: `android/app/build/outputs/apk/debug/web-display-manager.apk`

## Test

### Python

```sh
# All tests
PYTHONPATH=src python3 -m unittest discover -s tests -v

# Single file
PYTHONPATH=src python3 -m unittest discover -s tests -p test_app.py -v

# Specific class or method
PYTHONPATH=src:tests python3 -m unittest test_app.ConfigTests -v
PYTHONPATH=src:tests python3 -m unittest test_app.ConfigTests.test_url_validation -v

# Compilation check
python3 -m compileall -q src tests
```

### Shell scripts

```sh
for script in build-image.sh tools/*.sh image/stage-web-display/01-install/00-run.sh; do
  bash -n "$script"
done
```

### React UI

```sh
# Full suite: install, typecheck, test, build
(cd ui && npm ci && npm run typecheck && npm test && npm run build)

# Single test file
(cd ui && npm test -- src/App.test.tsx)

# Filter to one test name
(cd ui && npm test -- src/App.test.tsx -t "shows pairing when the device is unpaired")

# Hot reload (proxies /api to https://localhost:8443)
(cd ui && npm run dev)
```

## Deploy

### To Development Devices

```sh
tools/push-to-device.sh webdebug@DEVICE_IP          # copy source, restart services
tools/push-to-device.sh --restart-kiosk webdebug@DEVICE_IP  # also restart kiosk
```

Locate devices with mDNS: `avahi-browse -rpt _web-display._tcp`

### To Production Devices

```sh
tools/push-to-device.sh webdebug@DEVICE_IP   # replaces factory package, clears update state
```

This is not equivalent to installing a signed update bundle.

### GitHub Actions

- `ci.yml` — runs on every push/PR: Python tests, shell check, UI typecheck/tests/bundle verification
- `build-image.yml` — triggered on release publish or manual dispatch; builds image and update bundle
- `cyd.yml` — triggered on push/PR/manual/dispatch; builds CYD firmware
- `android.yml` — builds Android debug APK

## Release Process

See `docs/release-process.md` for the full release procedure.

## Device Architecture Reference

| Component | Port | User | Purpose |
|-----------|------|------|---------|
| `webdisplay.app` | 8443 (HTTPS) | `webdisplay` | Administration + JSON API |
| `webdisplay_manager` | 8765 (localhost) | — | PC-side device management |
| Kiosk (`kiosk`) | — | `kiosk` | Chromium + scene rendering |
| CYD companion | — | — | ESP32 remote-view display |

Key runtime paths:
- `/var/lib/web-display/config.json` — private configuration
- `/run/web-display/` — group-readable runtime files
- `/run/web-display-kiosk/remote-view.json` — sanitized remote view
- `/var/lib/web-display-app/versions/<version>/` — installed update bundles
- `/var/lib/web-display-app/current` — symlink to active version
