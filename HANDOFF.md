# Copilot Session Handoff

Last updated: 2026-10-09.

## Current Status

Milestone 1 and the Pi 2 scope of milestone 2 are implemented. Read `PLAN.md`
for agreed product requirements. The user accepts Pi 3 built-in WPA2 AP failure
on the current Raspberry Pi OS Trixie stack as a blocker for now. Do not claim
Pi 3 WPA2 AP validation, and do not use an open AP as a workaround.

The latest development firmware is
`deploy/image_2026-10-10-web-display-development-milestone2.img.gz`,
version 0.4.3, SHA-256
`54b48a6f8318f1d23435607df78035a4bf73cf7790827d4383cfec53cc8f5bb2`
(passes `gzip -t`), with saved-client reconnection retries. This full build used
the HTTPS mirrorservice Raspbian mirror instead of the unstable redirector and
completed successfully.
It was flashed to a clean SD card and cold-booted on a Pi 3 (Ethernet): the full
first-use setup (pairing, password, display URL) worked. The user also tested the
release image from the `v0.4.2` release on a Pi 2; the full setup worked as expected.

The `v0.4.2` pre-release was built by the GitHub workflow (release variant); its
checksum and build attestation were verified, and it has now been boot-tested on a
Pi 2. The release workflow no longer forces releases to be marked as pre-releases.

The Pi 3 now has Ethernet connected for Wi-Fi investigation. Its Wi-Fi initially
connected, then NetworkManager lost the association during a WPA handshake and failed
the profile because no interactive secrets agent is available. The saved system
profile still has its PSK. With automatic AP disabled, the supervisor previously did
not retry an inactive client profile; 0.4.3 adds a 60-second retry and unlimited
NetworkManager autoconnect retries. The latest failures reached the WPA four-way
handshake and disconnected; NetworkManager then reported no secrets agent. Even with the
saved system PSK intact, association retries initially failed. A temporary BSSID test
mistakenly targeted nearby networks rather than the configured SSID; after restoring
automatic selection, Wi-Fi reconnected through the configured network and stayed up for
at least five minutes. Continue observing before closing the reliability issue.

The Pi 2 also had a Wi-Fi outage on 2026-10-10 despite these retry settings. Over
Ethernet, logs showed repeated `ssid-not-found` failures and scans returned no networks;
the Edimax RTL8188CUS remained enumerated on the USB bus. A user-approved reboot
restored scanning and Wi-Fi reconnected to `HomeNET` at `192.168.0.250`, remaining
connected for at least five minutes. Root cause is unknown; track recurrence separately.

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
- Raspbian packages default to
  `https://mirrorservice.org/sites/archive.raspbian.org/raspbian`; override with
  `RASPBIAN_MIRROR` when building. The selected mirror is part of the cached
  development-base identity.
- The image runs a Python HTTPS management service on port 8443 and Chromium
  under Xorg. The Pi 2 requires Xorg's fbdev driver.
- NetworkManager owns Wi-Fi client and setup AP connections. Ethernet route
  metric is 100; Wi-Fi client profiles use metric 600. The saved Wi-Fi client now
  retries indefinitely, and the supervisor retries an inactive profile every 60
  seconds when no setup AP is active.
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
- **Pi 3 Wi-Fi client reliability:** observed on the current device: it connected at
  boot, then lost association during a WPA handshake. NetworkManager requested secrets
  and failed because no interactive agent exists; later traces repeatedly failed at
  the WPA four-way handshake. The 0.4.3 client retry improvements were deployed, and
  Wi-Fi reconnected automatically after the saved profile was restored and remained
  connected for at least five minutes. Keep Ethernet attached and observe longer/cold
  boot before marking the issue resolved.
- Pi 2 was unreachable from WSL at the end of the session. The Pi 3 remained
  reachable and its appliance services were active.
- Report hardware checks separately from unit tests. The v0.4.2 development image
  was clean-booted on Pi 3; its release image was boot-tested on Pi 2. Do not claim
  Pi 3 WPA2 AP works.

## Validation

- `PYTHONPATH=src python3 -m unittest discover -s tests`: 60 tests pass.
- Shell syntax and Python compilation checks pass.
- The latest compressed image passes `gzip -t`; its package manifest includes
  NetworkManager, dnsmasq-base, python3-xlib and openssh-server.
- Signed remote updates, boot-slot/rollback design, appliance hardening and
  release acceptance are not implemented.

## Next Work

The next planned milestone is signed remote firmware updates. Before coding,
define the Pi boot/partition design, signing-key custody, configuration
migrations and physical recovery behaviour. Preserve SSH in development images;
disable it only in the final hardened release.
