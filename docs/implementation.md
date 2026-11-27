# Implementation Guide

## API specification

The OpenAPI 3.0 spec at `docs/api/openapi.yaml` documents the device API.
Update it whenever the display's API surface changes (new endpoints, changed
request/response shapes, new security requirements).

Always validate with **both** tools before committing:

```sh
npx @redocly/cli lint docs/api/openapi.yaml
npx @apidevtools/swagger-cli validate docs/api/openapi.yaml
```

The Redocly run may produce warnings (license, operationId, 4xx responses);
these are best-practice suggestions, not errors. The spec must pass both
validators with zero errors.

## Cross-cutting conventions

### UI bundles

After UI changes, run `cd ui && npm run build` and include the regenerated
`src/webdisplay/static` and `src/webdisplay_manager/static` files. This one
command builds both UIs. CI rejects stale or untracked bundle output; neither
device nor manager needs Node at runtime. Both servers enforce CSP without
inline scripts/styles, so use bundled assets and CSS classes.

### API consistency

Keep API changes consistent across the device handler, `ui/src/api.ts`, and,
when the manager exposes the operation, its Python client/service/server and
`ui/src/manager/api.ts`. Device session mutations require `X-CSRF-Token`;
bearer API keys bypass CSRF but cannot create, list, or revoke keys. Keys are
returned once and only their hashes are stored on-device.

### Manager parity

Keep the PC and Android managers at feature parity. The PC manager reuses the
device's React editors, so new device settings appear there automatically;
the Android manager (`android/`) has native editors, models and parsers that
must be updated in the same change (e.g. settings sections, scene kinds,
remote-view data, players). Add Android tests and run
`cd android && ./gradlew testDebugUnitTest lintDebug assembleDebug`.

### Manager security

The manager uses a per-run `X-Manager-Token` plus Host/Origin checks and binds
only to loopback. Enrollment confirms a TLS fingerprint, uses the password
once to obtain an API key, then discards the password. Its store is mode 0600.
Removing a device locally does not revoke its device-side key.

### Atomic writes

Preserve atomic configuration writes and private file modes. Reuse existing
validators and helper protocols: Wi-Fi and hostname helpers accept JSON on
stdin; update installation streams bundle bytes. They return JSON results or
`{"error": ...}` on stdout, with a nonzero exit status on failure.

### Image manifest

Add installed helpers/configuration/units to
`image/stage-web-display/01-install/files/install-manifest`; both image
installation and development deployment consume that same manifest. New
system packages belong in `00-install-packages/00-packages` and require an
image rebuild or explicit installation on an existing device.

## Networking

NetworkManager owns client and AP profiles. Preserve Ethernet priority
(metric 100 versus Wi-Fi 600), failed-connect restoration of the previous
profile/AP, and credential rotation when starting the AP. Automatic AP is
opt-in (`auto_ap`, default false); its offline test (`ap_check`) defaults to
default-gateway reachability, with an internet probe option; client reconnection
continues even when automatic AP is disabled or Ethernet is online.

A previously loaded target page stays visible during an outage, with an
offline overlay after two failed checks. Full-screen setup replaces it when
no URL is configured or the setup AP is active; before first successful load,
show the waiting page. Do not conflate site failure with internet failure.

## Carousel transitions

Preserve the two-website-tab bound and hidden-tab freezing. Consecutive videos
reuse mpv; browser handover must become visible before stopping mpv to reclaim
decoder/allocator memory. Scene readiness requires the rendered-revision
acknowledgment, not merely a ready browser document. Do not mask failures by
extending startup timeouts or equating zero service restarts with correct
scene order.

## Train boards

Train calling-station filters include earlier and upcoming stops. Keep cache
and tracking identities separate for each station/filter combination. Fetch
Service Details only for the next live service; use board look-backs or
previously captured observations for departed services. Never infer actual
departures/last-seen stations from estimates or a service disappearing.
`show_previous_departure` defaults true; hiding it must reclaim the space.

## Music sync groups

Music sync groups (`webdisplay.music_sync`, `MusicRelay` leader/follower modes):
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

## Bluetooth speaker

Bluetooth speaker (`webdisplay.bluetooth`): root `bluetooth-action` sudo helper
(scan/pair/connect/disconnect/remove via `bluetoothctl`, whose exit codes are
unreliable, so verify with `info`) and root `web-display-bluetooth.service`
supervisor. The app publishes `/run/web-display/bluetooth.json`; the supervisor
publishes `bluetooth-status.json`; `active_output` picks
`alsa/bluealsa:DEV=..,PROFILE=a2dp` only while freshly connected, else the
wired output. Players switch `audio-device` live; positive `delay_ms` shifts
group playback earlier. BlueALSA runs source-only (`a2dp-source`).

## Background music

Keep background-music settings backwards compatible as
`{enabled, station_key, volume, day_sources}`; a PUT omitting `day_sources`
preserves the saved weekday schedule. Blank days use `station_key`. The relay
resolves today's source by device-local date and switches only at a song
boundary (next/skip/queue end), never mid-song. `station_key` identifies stations, audio
playlists or `mix:artist:<IDs>` / `mix:album:<IDs>` selections. Validate against
Plex, limit custom mixes to 20 unique correctly typed selections, and reject
arbitrary URLs. Browse with both Plex container start and size (100/page) to
stay within XML response limits. Renew the same source at queue end; empty
sources fail explicitly, never fall back to another source.

Custom music mixes shuffle selected music; they are not Plexamp sonic/Guest DJ
sessions. Import only server-exposed playlists/stations, not app-local mixes.
Do not enable music or change a display's source for a deployment/probe.
Preserve video-audio pause/resume, sleep and private relay capability rotation.

## Xorg display

Xorg uses modesetting/glamor; keep the CTM reset in `kiosk-session`, or Pi 2
shows a black screen. Pi 3 built-in secured AP client authentication remains
unvalidated/unsupported; prefer a compatible USB adapter and never substitute
an open AP.
