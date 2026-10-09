# Copilot Session Handoff

Last updated: 2026-10-08.

## Current Status

Milestone 1 and the Pi 2 scope of milestone 2 are implemented. Read `PLAN.md`
for agreed product requirements. The user accepts Pi 3 built-in WPA2 AP failure
on the current Raspberry Pi OS Trixie stack as a blocker for now. Do not claim
Pi 3 WPA2 AP validation, and do not use an open AP as a workaround.

The latest development firmware is
`deploy/image_2026-10-09-web-display-development-milestone2.img.gz`,
version 0.4.1, SHA-256
`163d6ba2b76ea1b1cbab441678a18bd394a7c268f6fed3f9b6c36d5b5c4b2b4b`
(passes `gzip -t`; the same-day filename is reused, so it replaces the 0.4.0 build).
It has not been flashed as a clean install.

## Build And Runtime

- Pinned image base: Raspberry Pi OS Trixie armhf, pi-gen commit
  `c4f875735c109c658cd5ee99eaaaf70886a853b5`.
- Build with `./build-image.sh`; default `VARIANT=development`. Use
  `VARIANT=release` for the final hardened firmware. Development rebuilds reuse
  the preserved container `web_display_pigen_development` (stages 0-2) and emit
  `.img.gz`; `FRESH=1` forces a full build. Remove the container with
  `docker rm -v web_display_pigen_development` to reclaim disk space.
- The admin UI is React + TypeScript in `ui/`; the built bundle is committed in
  `src/webdisplay/static/` (rebuild with `cd ui && npm run build`). No Node is
  needed on the device or for the image build. CSP forbids inline script/style.
- Development SSH uses the public key at `~/.ssh/id_rsa.pub` by default;
  override with `DEBUG_SSH_PUBLIC_KEY`. The release variant has no debug user
  and masks SSH.
- Use `tools/push-to-device.sh webdebug@DEVICE_IP` for source/service deployment
  without reflashing.
- The image runs a Python HTTPS management service on port 8443 and Chromium
  under Xorg. The Pi 2 requires Xorg's fbdev driver.
- NetworkManager owns Wi-Fi client and setup AP connections. Ethernet route
  metric is 100; Wi-Fi client profiles use metric 600.
- Automatic AP start is opt-in (`auto_ap` in config.json, default off; toggle in
  the admin page). When enabled, the supervisor starts a WPA2 AP after 120 seconds of internet outage, retries
  the saved client every 10 minutes, and restores client/AP state after
  transitions and failed connections. AP credentials rotate and appear on the
  kiosk fallback screen.
- A configured website stays visible through brief outages; an offline icon is
  overlaid after two failed reachability checks.

## Hardware Validation

- **Pi 2 + Edimax EW-7811Un:** WPA2 client and AP mode work. The user joined the
  AP, obtained an address and loaded the administration page. The user also
  tested a real switch-port outage; the AP came up after the grace period.
- **Pi 3 Model B:** built-in WPA2 client works, and Ethernet remains preferred.
  WPA2 AP activation fails in both the generated profile and a standard
  NetworkManager hotspot with `nl80211: kernel reports: key setting validation
  failed` / `802.1X supplicant took too long to authenticate`. Failed activation
  restores the saved client. The user accepts this as a blocker for now.
- Pi 2 was unreachable from WSL at the end of the session. The Pi 3 remained
  reachable and its appliance services were active.
- Report hardware checks separately from unit tests. Do not claim the 0.4.1
  image was booted or that Pi 3 WPA2 AP works.

## Validation

- `PYTHONPATH=src python3 -m unittest discover -s tests`: 39 tests pass.
- Shell syntax and Python compilation checks pass.
- The latest compressed image passes `xz -t`; its package manifest includes
  NetworkManager, dnsmasq-base, python3-xlib and openssh-server.
- Signed remote updates, boot-slot/rollback design, appliance hardening and
  release acceptance are not implemented.

## Next Work

The next planned milestone is signed remote firmware updates. Before coding,
define the Pi boot/partition design, signing-key custody, configuration
migrations and physical recovery behaviour. Preserve SSH in development images;
disable it only in the final hardened release.
