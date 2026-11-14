# Repository instructions

## Session continuity

Read `AGENTS.md` for the iteration/deployment workflow and `HANDOFF.md` for the
latest dated implementation and hardware status before starting work. `PLAN.md`
records approved designs; `README.md` describes user-facing operation. Keep these
documents consistent when completing an iteration. Check source, Git history and
release assets rather than treating historical notes or session todos as current.
Do not mark unresolved hardware/endurance checks complete because tests pass.

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
Use installed dependencies for subsequent iterations; reinstall from the
lockfile when dependencies change or required dependencies are missing.

Android manager builds require JDK 17, Android platform 36 and build tools
35.0.0. With `ANDROID_HOME` configured, run from `android/`:
`./gradlew --no-daemon testDebugUnitTest lintDebug assembleDebug`.
The APK is `android/app/build/outputs/apk/debug/web-display-manager.apk`.
Device-only music controls do not require a new native manager/Android build.

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

Discovery must classify already-added devices from persisted enrollment, not
online/status snapshots. Match all saved endpoints and normalized hostname
aliases (case, trailing dot, `.local`); do not use user-assigned display labels.
Keep different service ports distinct, and never let discovery association bypass
TLS pinning. Reclassify scans after enrollment/removal and guard stale scan/UI
responses so known devices do not reappear as new.

The PC manager's native configuration view reuses device React editors through
`DeviceApiContext` and `createApi`; shared editors must use the context rather
than hard-code the default device API singleton. Manager configuration requests
use a token-protected, explicitly allowlisted JSON gateway in
`webdisplay_manager.service.Manager.settings`, with the enrolled device's TLS
pin and bearer key. Add newly exposed operations deliberately; never turn this
into an arbitrary URL/header proxy or expose device keys to browser code.
Password-only API-key management and raw bundle uploads are not proxied.

Android native configuration uses `SettingsSession` and typed settings models
with the existing pinned-address transport and mutation admission. Keep secrets
in memory only, retain drafts across settings tabs/status refreshes, and cancel
requests when leaving/backgrounding. Preserve unknown scene fields when editing
newer firmware. Sleep/music/carousel overwrite checks are client-side and must
not be described as atomic server concurrency control.

Manager home cards show display identity and inline music/carousel controls;
keep maintenance in secondary actions and full editors in Configure device.
Carousel transport is separate from saved playlist configuration: the service
publishes group-readable `/run/web-display/carousel-control.json` with generation,
desired pause and a monotonic Next sequence. The kiosk ignores old generations,
baselines already-seen Next commands on reconstruction, pauses both scene timing
and mpv, and publishes `carousel-status.json` every tick. Managers must gate new
controls on advertised support and a fresh heartbeat, not version guesses.

Carousel scene kinds are `web`, `dashboard`, `video`, `train` and `weather`.
`scene_player.py` coordinates Chromium/mpv and the read-only local scene server;
`ui/src/SceneScreen.tsx` renders local scenes and acknowledges their revision.
`raildata.py` and `weather.py` own provider access/caching. `remote_view.py`
publishes sanitized reconstruction data at
`/run/web-display-kiosk/remote-view.json`; this is not a screenshot or the native
now-playing notification. Both managers reconstruct the view without credentials.

Background music is separate from the carousel playlist. `plex.py` discovers and
validates sources; `music.py` owns private queues/audio relay; `music_player.py`
supervises a separate persistent audio-only mpv. Provider tokens stay in the
HTTPS service and upstream headers, never in scene data or player URLs.

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
- Preserve the two-website-tab bound and hidden-tab freezing. Consecutive videos
  reuse mpv; browser handover must become visible before stopping mpv to reclaim
  decoder/allocator memory. Scene readiness requires the rendered-revision
  acknowledgment, not merely a ready browser document. Do not mask failures by
  extending startup timeouts or equating zero service restarts with correct
  scene order.
- Train calling-station filters include earlier and upcoming stops. Keep cache
  and tracking identities separate for each station/filter combination. Fetch
  Service Details only for the next live service; use board look-backs or
  previously captured observations for departed services. Never infer actual
  departures/last-seen stations from estimates or a service disappearing.
  `show_previous_departure` defaults true; hiding it must reclaim the space.
- Music sync groups (`webdisplay.music_sync`, `MusicRelay` leader/follower modes):
  config `music_sync` is `{"role":"off"}`, leader `{group_id, token_hash}` or
  follower `{group_id, token, leader{name,hostname,port,addresses,fingerprint}}`.
  `GET`/`PUT /api/music/sync` (managers; 404 = unsupported firmware) and
  `POST /api/music/sync/peer` (header `X-Music-Sync-Token`, throttled, leader only).
  The leader owns the group clock (`started_at`, pause/idle freeze) in epoch time;
  followers pin the leader certificate, fetch the same rating key from their own
  Plex (machine identifier must match) and the kiosk player uses relay action
  `sync` with prime-then-unpause plus speed/seek drift correction. In sync mode
  legacy player actions are rejected; video/sleep mute locally only. The device
  package must not import `webdisplay_manager`.
- Bluetooth speaker (`webdisplay.bluetooth`): root `bluetooth-action` sudo helper
  (scan/pair/connect/disconnect/remove via `bluetoothctl`, whose exit codes are
  unreliable, so verify with `info`) and root `web-display-bluetooth.service`
  supervisor. The app publishes `/run/web-display/bluetooth.json`; the supervisor
  publishes `bluetooth-status.json`; `active_output` picks
  `alsa/bluealsa:DEV=..,PROFILE=a2dp` only while freshly connected, else the
  wired output. Players switch `audio-device` live; positive `delay_ms` shifts
  group playback earlier. BlueALSA runs source-only (`a2dp-source`).
- Keep background-music settings backwards compatible as
  `{enabled, station_key, volume, day_sources}`; a PUT omitting `day_sources`
  preserves the saved weekday schedule. Blank days use `station_key`. The relay
  resolves today's source by device-local date and switches only at a song
  boundary (next/skip/queue end), never mid-song. `station_key` identifies stations, audio
  playlists or `mix:artist:<IDs>` / `mix:album:<IDs>` selections. Validate against
  Plex, limit custom mixes to 20 unique correctly typed selections, and reject
  arbitrary URLs. Browse with both Plex container start and size (100/page) to
  stay within XML response limits. Renew the same source at queue end; empty
  sources fail explicitly, never fall back to another source.
- Custom music mixes shuffle selected music; they are not Plexamp sonic/Guest DJ
  sessions. Import only server-exposed playlists/stations, not app-local mixes.
  Do not enable music or change a display's source for a deployment/probe.
  Preserve video-audio pause/resume, sleep and private relay capability rotation.
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
