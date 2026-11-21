# Web Display CYD

PlatformIO firmware for an ESP32-2432S028R "Cheap Yellow Display" companion.
It is a low-resolution status and touch-control client for the Raspberry Pi
Web Display; it does not render Chromium pages or play video/audio.

## Features

- 320x240 LovyanGFX UI with an XPT2046 resistive-touch driver.
- Captive-portal Wi-Fi setup at `192.168.4.1`.
- Pairing-code enrollment or login to an already paired Pi.
- A dedicated `wdk_...` API key; the Pi administrator password is never saved.
- SHA-256 certificate fingerprint confirmation and pinning before authenticated
  requests are sent.
- Native dashboard, weather, train and now-playing scenes from `/api/remote-view`.
- Touch controls for carousel pause/resume/next and music play/pause/resume,
  stop and skip.
- Offline, stale-heartbeat and certificate-change states.
- NVS persistence, reconnect backoff and a physical reset flow.
- Runtime landscape orientation selection persisted in NVS. A short BOOT press
  toggles 180-degree orientation; holding BOOT for five seconds resets enrollment.
- DNS and `_web-display._tcp` mDNS endpoint resolution, with direct IPv4
  enrollment available on networks that block local name resolution.

The remote view contains sanitized public display state only. Plex capability
URLs, provider tokens and the Pi private configuration are not copied to the
ESP32.

## Build

Use the release environment for normal firmware:

```sh
cd cyd
pio run -e cyd
```

The `cyd-dev` environment enables an explicit HTTP transport selector. It is
for controlled local development only and must not be treated as a release
or deployment build.

```sh
pio run -e cyd-dev
```

## GitHub builds and releases

The [`CYD firmware`](../../.github/workflows/cyd.yml) action builds only the
release `cyd` environment for pull requests and pushes that change `cyd/`.
It uploads firmware, bootloader, partition, boot-app and ELF artifacts as a
14-day GitHub Actions artifact.

Published GitHub Releases also receive versioned CYD assets and SHA-256
checksums. The release tag tracks the Raspberry Pi/Web Display version;
`CYD_VERSION` in `platformio.ini` is maintained independently.

## Remote hardware

The attached development board is exposed through PlatformIO Remote as
`HomePC:COM5`. All device operations use `pio remote`:

```sh
pio remote -a HomePC run -d cyd -e cyd
pio remote -a HomePC run -d cyd -e cyd -t upload --upload-port COM5
pio remote -a HomePC device monitor -p COM5 -b 115200 -d cyd -e cyd
```

PlatformIO Remote serial monitoring requires an interactive terminal. In a
scripted shell use a pseudo-terminal wrapper around the monitor command.

## First enrollment

1. Flash the release firmware and power the CYD.
2. Join the setup Wi-Fi shown on its screen.
3. Open `http://192.168.4.1`.
4. Enter the local Wi-Fi credentials and the Pi hostname or IPv4 address.
5. Compare the displayed SHA-256 certificate fingerprint with the Pi display
   and confirm it in the portal.
6. For an unpaired Pi, enter the pairing code shown on the Pi and choose a new
   administrator password. For an already paired Pi, choose login mode and
   enter the existing administrator password.
7. The CYD creates a named API key, stores that key and Wi-Fi credentials in
   NVS, discards the password, and reconnects as a companion.

Press the board's BOOT button briefly while the companion is running to flip
the display and persist that orientation. Orientation can also be selected in
the provisioning portal. Hold BOOT for five seconds to clear the saved
enrollment and return to provisioning. ESP32 NVS is not encrypted storage;
physical flash compromise can expose the saved Wi-Fi and bearer key.

## Current limitations

- No Chromium/web-page rendering, video playback or local provider fetching.
- No SD-card assets or album art yet.
- No native OTA delivery yet; firmware uploads are currently over the remote
  USB/serial device. The min-SPIFFS partition layout reserves two application
  slots for a future OTA implementation.
- Touch calibration, TLS enrollment, reconnects, controls and long-running heap
  behavior require physical-board acceptance. A successful build or USB boot
  does not complete those checks.
