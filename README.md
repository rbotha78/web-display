# Raspberry Pi Web Display

Milestone 1 builds a 32-bit Raspberry Pi OS appliance image for Raspberry Pi 2
and Raspberry Pi 3. It boots to Chromium on HDMI and exposes authenticated HTTPS
administration over DHCP Ethernet. Wi-Fi provisioning and remote firmware
updates remain intentionally deferred.

## Build

The build is pinned to Raspberry Pi OS Trixie armhf using `pi-gen` commit
`c4f875735c109c658cd5ee99eaaaf70886a853b5`. The selected official compatibility
baseline is the 2026-09-15 Lite image with SHA-256:

```text
c766b3fb279b95c12cb4dd22d06f8eab31972c372675d05bd0ca95b060523a7f
```

Requirements are Docker with privileged-container support, approximately 30 GB
of free space, and a native Linux filesystem. From this directory:

```sh
./build-image.sh
```

The compressed image, package manifest and build logs are copied to `deploy/`.
Package versions in that manifest are part of the release record because the
supported Raspberry Pi repositories can change after this source revision.
The temporary account required by `pi-gen` is deleted during the custom stage,
and first-boot user renaming is explicitly disabled.

### Firmware variants

- `./build-image.sh` (default `VARIANT=development`) keeps key-only SSH for the
  `webdebug` account, authorised by `DEBUG_SSH_PUBLIC_KEY` (default
  `~/.ssh/id_rsa.pub`). Password login and root login are disabled. Use
  `sudo disable-web-display-debug` on the device to turn it off.
- `VARIANT=release ./build-image.sh` is hardened: no debug account, SSH masked.
  Build this one as the final firmware. It always builds from scratch and
  compresses with `xz`.
- Development builds are faster: the first build keeps the pi-gen container with
  the finished base (stages 0-2), and later builds reuse it and rebuild only
  `stage-web-display`, compressing with `gz`. `FRESH=1 ./build-image.sh` forces a
  full rebuild; the base is also rebuilt after `BASE_MAX_AGE_DAYS` (default 14)
  to pick up OS updates. A code-only rebuild takes about 8 minutes instead of 25. `DEPLOY_COMPRESSION=none|gz|xz|zip` overrides the format.

For quick iteration without reflashing, run
`tools/push-to-device.sh webdebug@DEVICE_IP` to copy the source and service files
to a development device and restart the management services. It installs all helpers,
units, the release file and the Xorg config; add `--restart-kiosk` to also restart the
kiosk (blanks the screen briefly) when kiosk code, its unit or the Xorg config changed.

## Wi-Fi (milestone 2, staged)

After logging in to the administration page you can set the Wi-Fi regulatory
country (required before Wi-Fi is unblocked), scan, connect to one WPA2-PSK or
open network, or forget it. Credentials are stored as a root-only NetworkManager
profile written by a narrowly privileged helper. Ethernet keeps priority through
NetworkManager's default route metrics. The Network panel shows internet
reachability (three endpoints) separately from reachability of the configured
website. A failed connect restores the previous saved network.

### Offline indicator and screen behaviour

If a URL is configured, a connection loss does not replace the page: after two failed
15-second checks a small red "no connection" icon appears in the top-right corner and
disappears when the site is reachable again. The full-screen setup message is shown only
when no URL is configured (or, at boot, a minimal "Waiting for network connection" page
before the site has ever loaded). When the setup AP starts its SSID and password replace
the page within about one second of the AP being created (the kiosk polls once per second
and relaunches the browser, a few seconds on a Pi 2).

### Hostname

The administration page has a **Hostname** card (`POST /api/hostname`, CSRF-protected).
Names are RFC 1123 labels (1-63 letters, digits, hyphens). The request goes through the
sudo-restricted `hostname-action` helper, which starts a transient root unit that runs
`hostnamectl`, updates `/etc/hosts`, restarts Avahi, regenerates the self-signed HTTPS
certificate for the new name and restarts the management service. Reconnect at
`https://<name>.local:8443` and accept the new certificate.

### Setup access point and recovery

`web-display-network.service` (root, sandboxed) probes internet reachability every
10 seconds. Automatic AP start is **disabled by default**; enable it with the
"Start the setup access point automatically" checkbox in the administration page
(the `auto_ap` setting, also available via `PUT /api/config`). Disabling it stops a
running automatic AP. When enabled, after 120 seconds of continuous outage, and only
if the Wi-Fi country is set and the adapter supports AP mode, it starts a WPA2 (CCMP) access point named
`WebDisplay-XXXX` with a freshly generated 12-character password (rotated every time
the AP starts). The SSID, password and administration address (`https://10.42.0.1:8443/`)
are shown on the display's fallback page. While the AP is up the saved Wi-Fi network is
retried every 10 minutes; if that fails the AP returns with new credentials. The AP stops
as soon as internet is reachable again and the saved client network is restored.
If a client connection attempt fails while the setup AP is active, the saved client
profile is rolled back and the setup AP is immediately restored with rotated credentials.
On supervisor restart, any stale setup AP is removed and the saved client profile is
explicitly reactivated. Wi-Fi routes use metric 600; the standard Ethernet route metric
is 100, so Ethernet remains preferred when both links are active.
Limitation: the country must first be set while an administration route (Ethernet)
exists. Test manually with `sudo python3 -m webdisplay.netmonitor start-ap` and `stop-ap`
(stop `web-display-network` first, or it will remove the AP while Ethernet is online).
On the tested Raspberry Pi OS Trixie image, Pi 3 Model B client Wi-Fi connects, but
NetworkManager's secured AP activation currently fails on its built-in Broadcom adapter
with `nl80211: kernel reports: key setting validation failed` /
`802.1X supplicant took too long to authenticate`. A standard NetworkManager WPA2 hotspot
fails the same way, so this is not specific to the generated profile. The supervisor
restores the saved client connection after the failed AP attempt. WPA2 AP operation on
the Pi 3 remains an acceptance blocker; do not use an open AP as a workaround.

## Flash and first use

1. Write the generated `.img.gz` (development) or `.img.xz` (release) to an SD card with Raspberry Pi Imager or
   `bmaptool`, insert it in the Pi, attach HDMI and Ethernet, then power on.
2. The fallback screen shows the DHCP administration address and a one-time
   six-digit pairing code. No Linux console login or shared password is enabled.
3. Open the displayed `https://...:8443/` address. Verify the certificate
   fingerprint against the attached screen or local console before trusting it:

   ```sh
   openssl s_client -connect web-display.local:8443 </dev/null 2>/dev/null \
     | openssl x509 -noout -fingerprint -sha256
   ```

4. Export the certificate and add it to the administering device's local trust
   store only after verifying that fingerprint. The certificate is available
   from the TLS connection:

   ```sh
   openssl s_client -showcerts -connect web-display.local:8443 </dev/null \
     2>/dev/null | openssl x509 -outform PEM > web-display.crt
   ```

5. Enter the on-screen pairing code and establish an administrator password of
   at least 12 characters. Configure an HTTP or HTTPS display URL.

The certificate covers `web-display.local`, `web-display`, and localhost. It
does not cover a dynamic DHCP IP address, so use the `.local` hostname after
trusting the certificate. A future production enrollment flow should replace
this explicit local trust procedure.

## Runtime and security

- `webdisplay` owns the private configuration and TLS key.
- `kiosk` is an unprivileged browser account and receives only the current URL
  and first-use pairing code through a group-readable runtime directory.
- Passwords use scrypt with a random salt. Sessions are random, secure,
  HTTP-only and same-site; mutations require a CSRF token.
- Login and pairing failures are throttled. Settings are atomically replaced.
- The reboot API can invoke only the root-owned `system-action reboot` helper.
- SSH and console autologin are disabled. systemd restarts both application
  services after failure.
- Chromium switches to the local recovery page when the configured target
  cannot be fetched, while Ethernet status remains independently visible in
  administration.
- Pi 2 uses Xorg's framebuffer driver to avoid a vc4 KMS black-screen
  transition observed with the modesetting/glamor driver. Chromium is sized
  from the active X resolution because no desktop window manager is installed.
- The mouse cursor is permanently hidden: Xorg starts with `-nocursor`, since no
  pointing device is ever used on the kiosk.

## Administration UI

The administration page is a React + TypeScript single-page app in `ui/` (Vite,
no UI framework dependencies, dark/light themes, responsive). The compiled bundle
is committed in `src/webdisplay/static/` and served by the Python service, so
neither the Pi nor the image build needs Node. After changing anything in `ui/`:

```sh
cd ui && npm install && npm run typecheck && npm test && npm run build
```

and commit the regenerated `src/webdisplay/static/`. `npm run dev` runs a hot-reload
server that proxies `/api` to a service on `https://localhost:8443`. The page is
served with a strict Content-Security-Policy (no inline scripts or styles).

## Development checks

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
bash -n build-image.sh image/stage-web-display/{prerun.sh,01-install/00-run.sh}
```

Cold boot, HDMI rendering, browser performance, certificate onboarding, service
recovery, memory use and unattended operation still require validation on an
actual Raspberry Pi 2 before milestone acceptance.

## Temporary offline SSH recovery

SSH is disabled in production images. To diagnose a failed prototype without
adding a shared password, shut it down, attach its SD card to the build machine,
identify the ext4 root partition, and run:

```sh
sudo tools/enable-debug-ssh.sh /dev/sdX2 ~/.ssh/id_rsa.pub
```

The script verifies the Web Display filesystem, creates a locked-password
`webdebug` account, permits only public-key SSH, and enables temporary sudo
access. It mounts the selected partition directly as ext4 rather than relying
on `lsblk` filesystem metadata, which can be absent for usbipd devices in WSL.
After diagnosis, disable it on the Pi:

```sh
sudo disable-web-display-debug
sudo reboot
```

## License

Copyright (C) 2026 Robert Botha. Licensed under the GNU General Public License,
version 3 or (at your option) any later version. See `LICENSE`. The firmware image
also contains Raspberry Pi OS, Debian and Chromium, which keep their own licenses.
