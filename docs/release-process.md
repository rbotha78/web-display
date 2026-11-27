# Release Process

## Overview

Releases produce three artifact types, all attached to a GitHub Release:

| Artifact | Produced by | Contents |
|----------|-------------|----------|
| Base image | `build-image.yml` | `web-display-<version>.img.xz`, `SHA256SUMS` |
| Update bundle | `build-image.yml` | `web-display-update-<version>.wdupdate`, `SHA256SUMS-update` |
| CYD firmware | `cyd.yml` | `.bin`, `.bootloader.bin`, `.partitions.bin`, `.elf`, `.manifest.json`, `.manifest.sig`, checksums |

## Steps

### 1. Bump version

Edit **two files** (enforced by `tests/test_manager.py`):

```
image/stage-web-display/01-install/files/web-display-release
pyproject.toml
```

Both must contain the same `WEB_DISPLAY_VERSION` (e.g., `0.5.29`).

### 2. Create GitHub Release Tag

Create and publish a release with tag `v<WEB_DISPLAY_VERSION>` (e.g., `v0.5.29`).
This triggers three CI workflows automatically.

### 3. CI Pipeline

#### `build-image.yml` — Image Build Job

1. Validates tag matches `WEB_DISPLAY_VERSION`
2. Runs Python unit tests
3. Builds hardened image: `VARIANT=release ./build-image.sh`
4. Generates build provenance attestation
5. Uploads `deploy/*.img.xz` and `SHA256SUMS` to the release

#### `build-image.yml` — Update Bundle Job

1. Validates tag matches `WEB_DISPLAY_VERSION`
2. Runs Python unit tests
3. Signs bundle: `python3 tools/make_update_bundle.py --output-dir bundle`
4. Verifies against committed public key
5. Generates build provenance attestation
6. Uploads `bundle/*.wdupdate` and checksums to the release

#### `cyd.yml` — Firmware Build Job

1. Extracts `CYD_VERSION` from `cyd/platformio.ini`
2. Builds `cyd` environment (production, HTTPS only)
3. Signs OTA manifest (requires `UPDATE_SIGNING_KEY` secret)
4. Assembles all firmware artifacts
5. Generates build provenance attestation
6. Uploads all CYD files to the release

## Security

- **Signing key**: `UPDATE_SIGNING_KEY` GitHub secret, protected by a required-reviewer environment
- **Public key**: committed at `image/stage-web-display/01-install/files/update-key.pub`
- **Bundle format**: Ed25519-signed tar containing `manifest.json`, `manifest.sig`, `payload/webdisplay/...`
- **Verification**: signature → manifest → per-file SHA-256 hashes → version policy (no downgrades, base_compat check, failed-version exclusion)

## Device Update Flow

1. Device polls `https://api.github.com/repos/rbotha78/web-display/releases/latest`
2. Extracts matching `.wdupdate` asset
3. Downloads, verifies Ed25519 signature + SHA-256 hashes
4. Installs to `/var/lib/web-display-app/versions/<version>/`
5. Switches symlink `current → versions/<version>`
6. **Boot guard** runs at startup — rolls back after 3 failed boots
7. **Health confirmation** waits 180 seconds (20s stable window) — confirms if healthy, rolls back otherwise

## Constraints

- **`UPDATE_BASE_COMPAT`** must match between device base image and bundle manifest
- Changes to root helpers/systemd/OS require a base image flash, not a bundle
- Anti-downgrade: rejects versions ≤ installed version
- Failed versions tracked in `state.json` and blocked from re-install
- Bundle changes cannot update OS, root helpers, systemd units, sudoers, signing key, or frozen updater

## Offline Key Backup

Keep an offline backup of the private signing key; never commit it.
