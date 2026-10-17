# Repository instructions

## Build, test, and development commands

Run commands from the repository root unless shown otherwise. Python requires
3.10+; CI uses Ubuntu 24.04 and Node 24. The Python services use the standard
library rather than a web framework. Full Python tests need `segno` (CI installs
`python3-segno`) and OpenSSL 3 with `pkeyutl -rawin` support on `PATH`.

```sh
# All Python tests
PYTHONPATH=src python3 -m unittest discover -s tests

# One Python test file
PYTHONPATH=src python3 -m unittest discover -s tests -p test_app.py -v

# One test class or method: tests/ is not a Python package
PYTHONPATH=src:tests python3 -m unittest test_app.ConfigTests -v
PYTHONPATH=src:tests python3 -m unittest test_app.ConfigTests.test_url_validation -v

# Python compilation and shell syntax checks
python3 -m compileall -q src tests
for script in build-image.sh tools/*.sh image/stage-web-display/prerun.sh image/stage-web-display/01-install/00-run.sh; do
  bash -n "$script"
done

# Both React UIs: install from the lockfile, typecheck, test, and build
(cd ui && npm ci && npm run typecheck && npm test && npm run build)

# One UI test file, optionally filtered to one test name
(cd ui && npm test -- src/App.test.tsx)
(cd ui && npm test -- src/App.test.tsx -t "shows pairing when the device is unpaired")

# Device UI hot reload; /api and /health proxy to https://localhost:8443
(cd ui && npm run dev)

# PC manager, using its committed UI; --no-browser suppresses browser launch
PYTHONPATH=src python3 -m webdisplay_manager --no-browser
```

UI tests use Vitest, jsdom, and React Testing Library. Manager UI tests are in
`ui/src/manager/ManagerApp.test.tsx`; the same test selectors apply.

Image builds require Docker with privileged-container support, about 30 GB free,
and a native Linux filesystem:

```sh
./build-image.sh                           # cached development build, key-only SSH
FRESH=1 ./build-image.sh                   # rebuild the development base
VARIANT=release ./build-image.sh           # clean hardened build, SSH masked
tools/push-to-device.sh webdebug@DEVICE_IP  # deploy without reflashing
tools/push-to-device.sh --restart-kiosk webdebug@DEVICE_IP
```

The development build reads `DEBUG_SSH_PUBLIC_KEY` (default `~/.ssh/id_rsa.pub`).
Image artifacts go to `deploy/`. A development push replaces the factory Python
package and clears installed update versions/state; it is not equivalent to
installing a signed bundle. Restart the kiosk when changing kiosk code, its unit,
or Xorg configuration.

## Architecture

This is a Raspberry Pi 2/3 appliance using a shared 32-bit Raspberry Pi OS Trixie
armhf image. `build-image.sh` pins pi-gen and adds `image/stage-web-display` after
the Lite base stages. The image installs the Python package, Chromium/Xorg,
NetworkManager, root helpers, and systemd units; it does not build the React UI.

On the device, `webdisplay.app` serves HTTPS administration and the JSON API on
8443 using `ThreadingHTTPServer`. `Application` coordinates configuration,
authentication, helper calls, and updates. `ConfigStore` persists private state
in `/var/lib/web-display/config.json`. The service runs as `webdisplay` and
publishes only the target URL and pairing code through group-readable files in
`/run/web-display`. `webdisplay.kiosk`, running as the separate unprivileged
`kiosk` user, reads those files and setup-AP metadata, renders recovery screens,
and supervises Chromium under Xorg. It never reads administrator credentials.

`webdisplay.network` distinguishes internet probes from configured-site
reachability. The root `webdisplay.netmonitor` supervisor owns automatic setup-AP
transitions; `wifi_helper` owns client provisioning through NetworkManager.
Client changes and supervisor ticks share `/run/web-display/network.lock`.
Hostname, Wi-Fi, reboot, and update mutations cross narrowly permitted sudo
helpers rather than running the HTTPS service as root.

Signed application updates contain only `webdisplay`, including its static UI.
`tools/make_update_bundle.py` signs a manifest and payload hashes with Ed25519;
`webdisplay.updater` verifies and installs versioned packages under
`/var/lib/web-display-app`. Device services load the `current` symlink through
`PYTHONPATH`, falling back to the factory package when absent. The root
`update-action` explicitly ignores that path and runs the frozen factory updater.
Boot-guard and health-confirmation services handle rollback.

The PC-side `webdisplay_manager` is a separate localhost HTTP service on 8765.
`server.py` exposes its UI/API, `service.py` orchestrates operations,
`client.py` connects to fingerprint-pinned device HTTPS APIs, `discovery.py`
implements mDNS discovery, and `store.py` persists API keys, fingerprints, and
known addresses. Devices advertise `_web-display._tcp` through Avahi. Device
identity is certificate-based, not tied to one DHCP address; successful fallback
addresses are promoted. Both React apps share `ui/` dependencies and styles but
have separate entry points and API clients.

## Cross-cutting conventions

- After UI changes, run `cd ui && npm run build` and include the regenerated
  `src/webdisplay/static` and `src/webdisplay_manager/static` files. This one
  command builds both UIs. CI rejects stale or untracked bundle output; neither
  device nor manager needs Node at runtime. Both servers enforce CSP without
  inline scripts/styles, so use bundled assets and CSS classes.
- Keep API changes consistent across the device handler, `ui/src/api.ts`, and,
  when the manager exposes the operation, its Python client/service/server and
  `ui/src/manager/api.ts`. Device session mutations require `X-CSRF-Token`;
  bearer API keys bypass CSRF but cannot create, list, or revoke keys. Keys are
  returned once and only their hashes are stored on-device.
- The manager uses a per-run `X-Manager-Token` plus Host/Origin checks and binds
  only to loopback. Enrollment confirms a TLS fingerprint, uses the password
  once to obtain an API key, then discards the password. Its store is mode 0600.
  Removing a device locally does not revoke its device-side key.
- Preserve atomic configuration writes and private file modes. Reuse existing
  validators and helper protocols: Wi-Fi and hostname helpers accept JSON on
  stdin; update installation streams bundle bytes. They return JSON results or
  `{"error": ...}` on stdout, with a nonzero exit status on failure.
- Add installed helpers/configuration/units to
  `image/stage-web-display/01-install/files/install-manifest`; both image
  installation and development deployment consume that same manifest. New
  system packages belong in `00-install-packages/00-packages` and require an
  image rebuild or explicit installation on an existing device.
- NetworkManager owns client and AP profiles. Preserve Ethernet priority
  (metric 100 versus Wi-Fi 600), failed-connect restoration of the previous
  profile/AP, and credential rotation when starting the AP. Automatic AP is
  opt-in (`auto_ap`, default false); client reconnection continues even when
  automatic AP is disabled or Ethernet is online.
- A previously loaded target page stays visible during an outage, with an
  offline overlay after two failed checks. Full-screen setup replaces it when
  no URL is configured or the setup AP is active; before first successful load,
  show the waiting page. Do not conflate site failure with internet failure.
- For releases, bump `WEB_DISPLAY_VERSION` in
  `image/stage-web-display/01-install/files/web-display-release` and `version`
  in `pyproject.toml` together; `tests/test_manager.py` enforces this. Release
  tags must be `v<WEB_DISPLAY_VERSION>`. Manager firmware compatibility is
  separately gated by `MIN_FIRMWARE` in `webdisplay_manager/version.py`.
- Bundle changes cannot update the OS, root helpers, systemd units, sudoers,
  signing public key, or frozen updater. Changes to those require a base image;
  account for `UPDATE_BASE_COMPAT`. Preserve signature/hash validation,
  anti-downgrade state, failed-version exclusion, and local-service health
  rollback. Release CI signs/verifies bundles against the committed public key.
- `README.md` describes operation; `PLAN.md` records agreed designs and
  `HANDOFF.md` hardware observations. Milestone/version status in those notes
  can lag implemented code; use current source, release metadata, and CI for
  commands and capabilities. Report hardware validation separately from tests.
  Xorg uses modesetting/glamor; keep the CTM reset in `kiosk-session`, or Pi 2 shows a black screen. Pi 3 built-in secured AP client
  authentication remains unvalidated/unsupported; prefer a compatible USB
  adapter and never substitute an open AP.
