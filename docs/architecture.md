# Architecture

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

## ESP32-2432S028R companion (CYD)

The `cyd/` PlatformIO project is an ESP32-2432S028R companion client, not a
replacement for the Linux renderer. It uses LovyanGFX, captive-portal pairing,
NVS-stored bearer credentials and a SHA-256-pinned HTTPS remote-view transport.
Keep provider tokens, Plex playback capability URLs and private Pi configuration
off the CYD contract. Normal CYD builds reject HTTP; only the separate `cyd-dev`
environment may enable it. Device operations for the attached board use
`pio remote -a HomePC`, not a local serial upload. Device session mutations require `X-CSRF-Token`;
bearer API keys bypass CSRF but cannot create, list, or revoke keys. Keys are
returned once and only their hashes are stored on-device.

The CYD shows the sanitized active remote view (dashboard, weather, train,
now-playing music) and provides five touch controls for carousel and background-music
transport. First boot starts a password-protected captive portal; enrollment
confirms the Pi's SHA-256 certificate fingerprint, obtains a dedicated bearer
API key and stores that key plus Wi-Fi credentials in ESP32 NVS.

CYD OTA updates use a signed manifest and streamed firmware download through the Pi.
The CYD verifies the manifest signature and payload SHA-256 before writing the
inactive OTA app partition and requires remote-health confirmation after reboot.
If confirmation expires, it explicitly reselects the previous running partition
before restart. OTA remains opt-in; USB/serial is used for development flashing.
