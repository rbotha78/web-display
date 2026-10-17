# Copilot Session Handoff

Last updated: 2026-10-11.

## Start Here

Read `.github/copilot-instructions.md` for repository commands and conventions,
then `PLAN.md` for approved scope. This handoff records the latest implementation
and hardware status; distinguish user-reported hardware checks from local tests.

## Current Repository And Release

- Branch `main`, latest recorded commit `ef66d11` (`Add repository Copilot
  instructions`), following the `v0.5.8` release. The worktree was clean.
- This handoff refresh also updates `PLAN.md` and the signed-update status in
  `README.md`; these documentation changes are currently uncommitted.
- The current application/manager release is **0.5.8**. Both the Pi 2 and Pi 3
  were updated to 0.5.8 through the central manager and reported that version.
- The manager UI, device-side key-revocation UI, and firmware-version alignment
  are committed and pushed. No newer full SD-card image build is recorded for
  this release; signed bundles update the Python application, not base-image
  files or the OS.

## Milestone Status

- **Milestone 1:** implemented and validated on Pi 2.
- **Milestone 2:** networking, Wi-Fi setup/recovery, Ethernet preference, and
  setup AP functions are implemented. Pi 2 WPA2 client/AP operation and real
  outage recovery were validated. Pi 3 built-in Wi-Fi works as a client, but its
  built-in WPA2 setup AP does **not** authenticate Android or iPhone clients.
  Do not use an open AP as a workaround. A compatible USB Edimax EW-7811Un has
  been tested as an AP-capable adapter on Pi 3; secured AP, DHCP and HTTPS
  administration worked with it.
- **Milestone 3, Phase A:** signed application bundles, verification, staged
  install, rollback/boot guard, admin UI/API, GitHub release assets and manager
  update operations are implemented. Hardware checks of valid upload, invalid
  signature, tampering, rollback and anti-retry are recorded for Pi 2 and Pi 3.
  Manager-driven release updates to 0.5.7 and 0.5.8 were also tested on both
  Pis. Phase B (A/B root images) remains deferred. Do not treat an app bundle
  as updating the base image.
- **Milestone 4:** central PC manager is implemented and tested. It discovers
  and enrolls devices, pins TLS fingerprints, stores per-device API keys, lists
  multiple interface addresses, changes display URL, checks/installs updates,
  updates all devices and restarts devices. The device-side **Manager access**
  card lists and revokes keys; the user confirmed it works on Pi 3.

## Active Work

Two tracked todos remain `in_progress`:

- `m2-pi2-wifi-dropout`: Pi 2 previously stopped finding networks (`ssid-not-found`
  and empty scans); a user-approved reboot restored scanning and connection to
  the configured network. A later short observation showed a connected interface and clean
  gateway pings, but does not establish long-term reliability.
- `m2-pi3-wifi-client-reliability`: Pi 3 previously lost association during a
  WPA handshake and NetworkManager reported that no interactive secrets agent
  was available. The saved profile later reconnected and a short observation
  was clean; long-term and cold-boot reliability remain unproven.

Update 2026-10-12 (SSH now works on both Pis, ~26 h uptime, 60 pings each,
Wi-Fi only): with Wi-Fi power saving off (`wifi.powersave = 2` in
`web-display-wifi.conf`; `iw dev wlan0 get power_save` reports off) the links
look much more stable, which the user confirmed. Pi 3 had 0 disconnects, 9 ms
average latency and -35 dBm. Pi 2 (USB RTL8188CUS) is healthy at the radio level
(-45 dBm, no retries, failures or beacon loss, 0% ping loss) but roamed 158 times
in 26 h between two similarly strong APs of the same network (reason=4 locally generated) and averaged 64 ms latency with
spikes to ~500 ms. Decision: keep roaming and do not pin a BSSID. If stalls
become visible, move the displays to a dedicated Wi-Fi network. Note `iw` is in
`/usr/sbin`, which is not on `webdebug`'s PATH. Still not a full observation
window, so keep both todos open until a longer clean run or a recurrence.

The previous short checks were on 2026-10-10. Both Pis' Ethernet ports were
re-enabled for manager multi-address testing, so Ethernet reachability alone is
not evidence that Wi-Fi is healthy. Confirm the present device/link state before
starting an observation; for Wi-Fi-only testing, disable the Ethernet switch
ports and verify the Wi-Fi address directly. Capture relevant journal output
before rebooting because the journal is not persistent. Do not close either
todo without adequate observation or a documented recurrence/fix.

Other useful but not yet hardware-verified cases: manager address fallback when
one interface is actually down; the old-firmware warning on a real device; and
the device-side key-revocation card on Pi 2. These are not blockers for the
completed manager milestone.

## Device And Network Notes

- Both Pis can be on Ethernet and Wi-Fi at once with different DHCP addresses.
  Confirm current addresses (mDNS or the router) rather than relying on notes.
- SSH account in development images: `webdebug`. Release images disable SSH.
- Pi 2 Wi-Fi adapter is an Edimax RTL8188CUS. **Do not `modprobe -r` its
  driver**; that previously hung the Pi.
- WSL2 mDNS multicast does not discover LAN devices reliably. Run the manager
  natively on Windows when using discovery:

  ```powershell
  $env:PYTHONPATH="src"; python -m webdisplay_manager
  ```

  The manager is cross-platform. Image and device-push shell scripts require
  Linux/WSL.

## Video Playback Findings (Pi 2, 2026-10-17, hardware)

Measured on a Pi 2 for a possible carousel/video feature, using a 3.6 minute
H.264 clip served by a Plex server on the LAN. These are one-off measurements,
not automated tests.

- Chromium playing a Plex HLS transcode at about 404p and 2 Mbps (via hls.js)
  was smooth: about 0.5% dropped frames, about 50% CPU (peaks near 90% at
  start), peak 54 °C, no throttling, available memory down about 190 MB.
- Chromium cannot play the original `.m2ts` (MPEG-TS) file directly
  (`MEDIA_ERR_SRC_NOT_SUPPORTED`), and Chromium on Linux has no native HLS, so
  hls.js is required. 720p and 1080p were not measured in Chromium; expect them
  to be too heavy for software decode.
- Native `mpv` (0.40, Debian Trixie) does not help. The V4L2 stateful decoder
  (`bcm2835-codec-decode`) works (`--hwdec=v4l2m2m-copy`), but Xorg uses the
  fbdev driver, so output is CPU-bound. At 1440x1080 or 720p, `--vo=x11` ran at
  about 88% CPU and played well under real time with hundreds of dropped
  frames; software decode was no better. `--vo=xv` is unsupported, `--vo=drm`
  crawled, and `--vo=gpu` (DRM or X11/EGL) dropped over half the frames.
- Conclusion: on Pi 2 keep video slides in Chromium at about 480p or lower and
  load one live video at a time. Higher resolutions need a faster board or a
  GPU-accelerated X setup; do not change Pi 2's fbdev Xorg configuration for
  this. A Plex token is a secret; keep it in private device config only.
- `mpv` was installed by hand on the test Pi 2 and is not part of the image.

## Development And Validation

Run from the repository root. Full Python tests require Python 3.10+, `segno`,
and OpenSSL 3 with `pkeyutl -rawin` support on `PATH`:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
(cd ui && npm ci && npm run typecheck && npm test && npm run build)
python3 -m compileall -q src tests
```

The last recorded run passed 164 Python tests and 14 UI tests. Building the UI
updates both committed bundles: `src/webdisplay/static/` and
`src/webdisplay_manager/static/`. CI checks bundle freshness.

Image/device operations:

```sh
./build-image.sh
VARIANT=release ./build-image.sh
tools/push-to-device.sh webdebug@DEVICE_IP
```

Image builds need Docker, privileged-container support, about 30 GB free, and a
native Linux filesystem. A development push replaces the factory Python package
and clears installed update versions/state; it is not equivalent to installing
a signed bundle. Files such as the updater, helper programs, systemd units,
sudoers rules, Avahi configuration and NetworkManager configuration are base
image content and need a development push or image rebuild.

When preparing a release, bump `WEB_DISPLAY_VERSION` in
`image/stage-web-display/01-install/files/web-display-release` and `version` in
`pyproject.toml` together; tests enforce alignment. The release tag must match.
The signing private key is not in the repository; never commit or casually
transfer it. See `README.md` and CI workflow for release details.

## Architecture Pointers

- Device service/API and address reporting: `src/webdisplay/app.py`.
- Network supervisor and Wi-Fi recovery: `src/webdisplay/netmonitor.py`,
  `src/webdisplay/network.py`, and `src/webdisplay/wifi_helper.py`.
- Signed updates: `src/webdisplay/updater.py`, `src/webdisplay/releases.py`,
  `tools/`, and `image/stage-web-display/01-install/`.
- Manager backend: `src/webdisplay_manager/`; manager React UI:
  `ui/src/manager/`; device key UI: `ui/src/components/ManagerKeysCard.tsx`.
- Hardware tests are not represented by unit tests. Preserve the distinction
  between automated test results and checks performed on physical Pis.
