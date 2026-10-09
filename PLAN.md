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
- Choose update signing-key custody and physical recovery behaviour before release.

## Current Status

Milestone 1 is implemented and validated on Raspberry Pi 2. Milestone 2 is
implemented and validated on Raspberry Pi 2, including WPA2 client/AP operation,
Ethernet route preference, outage recovery and on-screen AP credentials. Raspberry
Pi 3 built-in Wi-Fi client mode works, but its WPA2 AP fails under the current
Trixie stack with a kernel key-validation error. The user accepts this as a Pi 3
hardware/software blocker for now; do not use an open AP as a workaround. The
development image is version 0.4.0. Signed remote updates, production hardening
and release acceptance remain future milestones.