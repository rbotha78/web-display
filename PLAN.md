# Raspberry Pi Web Display

## Goal

Produce a bootable appliance image that displays a configurable web page on an
attached screen without a Linux login. Administration must be web authenticated.

## Confirmed Requirements

- Support Raspberry Pi 2 and Raspberry Pi 3 with a shared 32-bit image where practical.
- Prefer Ethernet with verified internet access over Wi-Fi.
- Support the Pi 2 Edimax EW-7811Un USB adapter, USB ID 7392:7811,
  Realtek RTL8188CUS, subject to on-device driver and access-point validation.
- Use the Pi 3 built-in Wi-Fi for wireless setup.
- When no usable internet connection exists and an AP-capable adapter is present,
  offer a temporary password-protected setup access point.
- Display the setup SSID, password and configuration address on the screen.
- Provide authenticated HTTPS administration and remote firmware updates.
- Develop in WSL; validate display and Wi-Fi behaviour on actual Raspberry Pis.

## Milestone 1: Approved

Deliver a flashable Raspberry Pi 2 prototype with HDMI kiosk display and
authenticated Ethernet administration. This milestone establishes the local
management path needed for subsequent Wi-Fi and update testing.

### Scope

- Reproducible image build based on a pinned, supported 32-bit Raspberry Pi OS.
- DHCP Ethernet connectivity; show the device's administration address on-screen.
- Automatic startup of a minimal graphical session and Chromium kiosk browser.
- Dedicated unprivileged kiosk account; no interactive Linux login required.
- Local fallback screen when no URL is configured or the target cannot load.
- HTTPS administration with an explicit self-signed certificate trust procedure.
- First-use pairing code displayed on-screen to establish an administrator password.
- Authenticated URL configuration, status and reboot controls.
- Secure password hashing, protected sessions, CSRF protection and login throttling.
- Persist configuration atomically and restart failed services through systemd.
- Include network diagnostics tools and applicable Wi-Fi drivers/firmware for
  later hardware validation; do not claim AP compatibility before testing.
- Disable SSH by default; exclude secrets from logs and diagnostics.

### Acceptance Checks

- Pi 2 cold-boots to the HDMI display without a keyboard or Linux login.
- Administrator onboarding works from another device on Ethernet.
- Unauthenticated page and API requests cannot read protected settings or mutate them.
- Authenticated URL changes affect the display and survive a reboot.
- Invalid URLs and unreachable pages yield a useful local fallback and recovery path.
- Browser and administration service crashes recover automatically.
- A representative target webpage renders acceptably on Pi 2; record memory use
  and results from an extended unattended run.
- Record the image version, OS, kernel, browser and tested hardware.

### Boundaries

Automatic Wi-Fi setup, remote image updates, A/B rollback, off-site administration
and full read-only-system hardening are not milestone 1 deliverables. The prototype
is not production-ready until the later security and recovery gates pass.

## Later Milestones

1. Wireless validation and recovery: verify client and WPA2 AP operation on the
   EW-7811Un and Pi 3; implement Ethernet preference, connectivity probes,
   outage grace periods and automatic recovery. Failed Wi-Fi connection tests
   must restore setup mode even when a single adapter requires mode switching.
2. Remote updates: authenticated signed-image upload, compatibility checks,
   configuration migrations, inactive-partition installation and boot rollback.
3. Appliance hardening: bounded writable storage, browser isolation, firewall,
   physical reset, power-loss recovery and reproducible release artifacts.
4. Release acceptance: both Pi models, prolonged operation, network flapping,
   full storage, interrupted updates, rollback and factory-reset testing.

## Milestone 3: Signed Remote Updates (Approved Design)

Decisions: app bundles first, full A/B images later; Ed25519 signing key held as a
GitHub Actions secret; updates delivered by admin-page upload and by pulling GitHub
Releases; failed updates roll back automatically.

### Phase A: signed app bundles

- A bundle (`.wdupdate`, a tar file) holds `manifest.json`, `manifest.sig` and a
  `payload/` tree (the `webdisplay` Python package including the built UI).
- The manifest lists `format`, `version`, `base_compat`, and the SHA-256 of every
  payload file. The signature is Ed25519 over the exact manifest bytes, verified with
  `openssl` against the public key baked into the image (`/etc/web-display/update-key.pub`).
- Installation is performed by a root helper that runs the frozen base-image
  copy of the updater, never the updated code: it copies the upload into root-owned
  staging, verifies signature, hashes, tar safety, size limits, `base_compat` and
  anti-downgrade, unpacks to `/var/lib/web-display-app/versions/<version>` and atomically
  switches the `current` symlink. Services load the package via
  `PYTHONPATH=/var/lib/web-display-app/current`; no symlink means the factory copy.
- Anti-downgrade: a bundle must be newer than the running version and than the
  persisted version floor. Versions that failed health checks are recorded and not retried.
- Rollback: an install writes `pending.json`. A boot guard counts boot attempts and
  reverts to the previous version after repeated failures. A health confirmation
  service waits for the admin service, kiosk and network supervisor, then confirms;
  on failure it reverts and restarts the services.
- Delivery: upload through the admin page, or check/install from the latest GitHub
  Release. Both use the same verifier, so TLS trust is not relied upon.
- Not updatable by bundles (require an image): the updater, root helpers, systemd
  units, sudoers, the public key and the base OS. `base_compat` gates incompatibility.
- Release CI signs bundles with the `UPDATE_SIGNING_KEY` secret (protected by a
  required-reviewer environment) and attaches them to the release. Keep an offline
  backup of the key; rotation needs a new image carrying the new public key.

### Phase B: A/B root images (not started)

Needs a boot-selection design that works on Pi 2/3 (no `tryboot`), shared boot-file
power-loss protection, a persistent data partition and streamed image writes.

## Milestone 4: Central Device Management (Approved Design)

Decisions: a Python service on the user's PC serves a React UI on localhost; devices are
found by mDNS and by manual add; each PC authenticates with its own API key enrolled
once per device; v1 covers status, display URL, updates and reboot.

- **Device API keys:** an admin session (password login) can mint a named key
  (`POST /api/keys`, returned once; only a SHA-256 hash is stored in `config.json`),
  list keys and revoke them. A key is sent as `Authorization: Bearer wdk_...` and
  grants the same API as a session, except it cannot mint, list or revoke keys.
  Failed key attempts are throttled like logins. At most 10 keys per device.
- **Enrolment:** the manager logs in with the admin password once, mints a key, then
  discards the password. The device's TLS certificate fingerprint is shown for the user
  to compare with the device screen before it is trusted, then pinned.
- **Trust on the PC:** keys and pinned fingerprints are kept in a 0600 file in the user's
  config directory (keys must persist to be useful; passwords are never stored).
- **Discovery:** devices advertise `_web-display._tcp` on port 8443 via avahi. The
  manager lists them and also accepts manual hostnames/IPs.
- **Manager service:** `python3 -m webdisplay_manager` binds to 127.0.0.1 only, with a
  per-run token in the URL it opens, Host/Origin checks and a CSRF header on writes. It
  connects to devices with a pinned-fingerprint TLS check, not CA validation.
- **Functions (v1):** list with status, version and online/Wi-Fi state; change display
  URL; check and install updates on one or all devices; reboot.

## Architecture Direction

- NetworkManager owns both client and access-point networking.
- A local configuration service uses a narrowly privileged helper for system changes.
- Browser content never receives administrator credentials or system privileges.
- Distinguish internet failure from failure of the configured website.
- Verify internet connectivity over the selected interface using multiple endpoints;
  cable presence or Wi-Fi association alone is insufficient.
- A/B boot-health checks measure local boot and service health, not internet availability.
- Persistent state must accommodate configuration, certificates and an explicit
  browser-cookie policy without allowing unbounded caches or logs.
- Keep administration local by default; off-site access requires a separately
  selected VPN or secure tunnel, not a directly exposed administration port.

## Implementation Gates

- Verify the chosen OS provides a working graphical stack and Chromium on Pi 2
  before committing to the image architecture.
- Verify WSL image-building prerequisites, including any required container,
  filesystem mounting and ARM emulation support.
- Validate the Edimax adapter on the Pi after flashing; WSL cannot establish its
  Raspberry Pi driver or access-point capabilities.
- Specify setup credential rotation and reconnection behaviour before AP implementation.
- Specify boot selection, retry counters and shared boot-file protection before
  promising power-loss-safe A/B updates.
- Choose update signing-key custody and physical recovery behaviour before release (custody decided in milestone 3).

## Current Status

Milestone 1 is implemented and validated on Raspberry Pi 2. Milestone 2's network
features are implemented; Pi 2 WPA2 client/AP operation, Ethernet preference and
outage recovery were validated. Two longer-term Wi-Fi reliability investigations
remain open: Pi 2 scan/dropout recurrence and Pi 3 client reliability. With Wi-Fi
power saving off both links looked much more stable on 2026-10-12; Pi 2 still
roams between two similar access points, and a dedicated Wi-Fi network is the
fallback if that becomes a problem. Pi 3
built-in Wi-Fi works as a client, but its WPA2 setup AP does not authenticate
tested Android or iPhone clients. A compatible USB Edimax EW-7811Un was validated
as an AP-capable adapter on Pi 3; do not use an open AP as a workaround.

Milestone 3 Phase A (signed application bundles) is implemented. Hardware checks
of valid installation, invalid signature, tampering, rollback and anti-retry are
recorded on Pi 2 and Pi 3. Manager-driven release updates to 0.5.7 and 0.5.8 were
also tested on both devices. Phase B (A/B root images) remains deferred.
Milestone 4 (central device management) is implemented and hardware-tested,
including discovery/enrolment, multi-address reporting, management actions and
device-side API-key revocation. The latest application/manager release is 0.5.8;
see `HANDOFF.md` for current device, test and remaining-work details.

Production hardening, full release acceptance and the two Wi-Fi reliability
investigations remain outstanding. App bundles do not update base-image files or
the operating system; changes to those require a development push or new image.