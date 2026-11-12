# Copilot Session Handoff

Last updated: 2026-11-12.

## Start Here

Read `AGENTS.md` and `.github/copilot-instructions.md` for workflow, commands and
invariants, then this current-status section and `PLAN.md` for approved scope.
`README.md` documents user-facing operation. Do not depend on session-local todos
or artifacts to resume work. Distinguish tests, live API checks, physical-display
observations and endurance acceptance; historical findings below are not current
vitals or deployment instructions.

## Current Repository And Release

- Branch `main`; use `git status` and `git log` for the actual checkout/commit.
  Train layout refinements were committed in `f5a18e4`; Plex playlists/mixes and
  the guidance refresh followed in `c82595c`. The v0.5.18 release work includes
  the deployed animated equalizer icon, Plex smart-playlist builder, floating
  admin music controls and admin landing/guidance refinements.
- Release version/tag: **v0.5.18**. The user requested commit, push and release
  publication on Nov 12. This release adds optional previous departures,
  reclaimed row space, the larger footer clock, Plex playlists/custom mixes,
  smart-playlist creation, floating admin music controls, the animated music
  icon and Global settings as the default administration view.
  Publishing triggers signed-update and full-image builds; check GitHub release
  assets and workflow status rather than assuming either build has completed.
- Confirmed on Nov 9: v0.5.17 has the signed `.wdupdate`, update checksum, Android
  APK, full `.img.xz` and image checksum. The image workflow completed successfully
  (run `42657619061`); it is no longer waiting to build. This is not a claim that
  this newly built image has been flashed/boot-accepted on both Pis.
- The local Android APK at
  `android/app/build/outputs/apk/debug/web-display-manager.apk` was built Nov 9
  at 14:58 UTC and matches the release APK (25,197,967 bytes). No Android source
  changed for v0.5.18; the same APK can be attached without rebuilding it.

## Current Development Deployment (2026-11-12)

- Both targets listed in `AGENTS.md` received the Global settings default and
  carousel warning cleanup
  using `tools/push-to-device.sh --restart-kiosk`, with pinned SSH host checking.
  They remain development targets; confirm this again before future pushes.
- Both now run the factory package at
  `/usr/lib/python3/dist-packages/webdisplay`, not patched `versions/0.5.11`.
  Development pushes removed installed update versions/state but preserved
  private configuration, API keys and carousel playlists. They are not signed
  update installations, even though version metadata still says 0.5.17.
- Final deployed device UI: `static/assets/index-Dnu0bTpF.js`,
  CSS `static/assets/style-BAC1ff-r.css`. The manager CSS was rebuilt too.
  Both services (`web-display` and `web-display-kiosk`) were active; HTTPS
  `/health`, Python module hashes, static index/JS hashes and served JS bytes
  matched the final local build on both devices.
- Remote-view snapshot: `/run/web-display-kiosk/remote-view.json`, not
  `/run/web-display/remote-view.json`. Private configuration remains
  `/var/lib/web-display/config.json`; never print credentials or relay capability
  URLs during checks.
- Do not redeploy or alter playback merely to resume a session. Inspect current
  live state first. Never silently replace a changed SSH host key or disable
  host verification. Do not enable music or change sources during probes.

## Latest Implemented Features

- Administration opens on **Global settings**; the menu switches to **Carousel**
  without discarding either screen's drafts. The obsolete experimental/browser-
  relaunch banner is removed; retained Chromium is the current transition path.
  Display backgrounds are black. Existing endurance acceptance remains open.
- Both admin screens have a floating bottom-pinned background-music player:
  Play/Pause/Resume, Stop and Skip. Manual pause retains position through video
  handovers; a paused skip defers loading until Resume. Stop persists disabled
  playback but retains the source/volume; Play starts a fresh queue for that
  source. Source/volume drafts remain intact and Play/Stop updates the enabled
  checkbox. Sleep/video-audio guards still apply. Manual pause is transient and
  clears on a source/enable/identity change or service restart.
- Train boards support destination and calling-station filters (including earlier
  stops), actual-only last-seen/departure observations, compact previous-service
  routes and horizontally scrolling long routes with key stations pinned.
  **Show previous departure** defaults on; disabling it lets upcoming rows fill
  the space. The bold gold seconds clock stays below weather. Both reconstructed
  manager views follow the scene settings.
- Weather scenes, dashboard/train weather, overscan-safe countdown, weekly sleep
  and temporary wake are implemented. Sleep stops music/skips videos, not the
  screen or non-video scenes.
- **Global settings -> Background music** lists Plex library stations,
  regular/smart audio playlists and supported server-advertised mixes. Artist
  and album mix builders search/page through libraries and save up to 20
  selections on this display, not as a new Plex playlist.
- **Create Plex smart playlist** saves dynamic audio playlists in the connected
  Plex account, available to other Plex clients. Optional album-decade,
  track-genre and track-mood filters match all selected criteria; at least one
  is required. Choices come from Plex, a preview counts current matches and
  new playlists use random ordering. Empty matches and duplicate names are
  rejected. Creation does not change the display's music source or enabled state.
  Edit/delete playlists in Plex; missing mood metadata limits filtering.
- Custom mixes shuffle selected tracks, not sonic recommendations or Guest DJ.
  Plexamp-local mixes are not directly importable: save them as an audio playlist
  first unless the server already exposes them as a supported station/playlist.
  Smart playlists re-evaluate and mixes reshuffle when a queue is renewed.
  Empty sources fail explicitly. Music remains opt-in, pauses for video audio,
  respects sleep/output selection, and has bounded automatic retries.
- The native top-left now-playing card floats without resizing the scene; it
  can overlap content and is not included in reconstructed manager previews.
  Playback shows animated equalizer bars instead of the "Now playing:" label;
  paused/loading/retry states retain explicit text. The bars indicate playback,
  not measured audio levels. Only the small icon is redrawn at five frames per
  second; carousel/configuration polling retains its one-second cadence.
  PC/Android managers open device administration for music configuration; they
  have no dedicated native music editor or carousel playlist editor.

## Latest Validation

- Release preparation: all 399 Python tests and 94 UI tests passed; both UIs
  typechecked and rebuilt with the same final assets already deployed above.
  Firmware/Python package versions are aligned at 0.5.18. The development
  displays still report 0.5.17 metadata; release preparation does not imply a
  signed update installation or new physical/endurance acceptance.
- Admin landing/guidance cleanup: 24 UI tests passed across `App.test.tsx` and
  `PlaylistCard.test.tsx`, including Global settings as the initial view,
  active-menu state, removal of the obsolete banner and draft retention on both
  screens. Both UI bundles rebuilt/typechecked; manager output unchanged.
  Both Pis received the final bundle, with active services, healthy HTTPS health
  and matching installed/served assets. This does not close endurance acceptance.
- Admin player controls: 84 Python tests passed across `test_music_player`,
  `test_plex`, `test_app`, `test_kiosk` and `test_scene_windows`; compilation
  passed. 32 UI tests passed across `MusicControls.test.tsx`, `MusicCard.test.tsx`,
  `PlexCard.test.tsx` and `App.test.tsx`; both UI bundles rebuilt/typechecked.
  The new status poll is sequential, aborts on unmount and reports an outage once
  until recovery rather than repeating notifications.
- Real Chromium geometry checks passed at 320x640, 375x812, 480x600, 800x600 and
  1280x720: the bar stays pinned through scrolling, long titles/buttons fit,
  final save controls remain reachable and toasts sit above the bar.
- Isolated real-mpv checks passed on both Pis with silent output and synthetic
  local tracks: pause, video handover while manually paused, paused skip, resume,
  queue-end skip, stop and play. Pause/skip reused mpv; Stop terminated it.
  These checks did not use Plex or change either display's actual music settings.
  Services/HTTPS health and installed/served final assets matched after deployment.
  Physical listening and sustained endurance acceptance were not repeated.
- Smart-playlist builder: 64 Python tests passed with
  `PYTHONPATH=src:tests python3 -m unittest test_plex test_app test_music_player -q`;
  compilation passed. 25 UI tests passed across `MusicCard.test.tsx`,
  `PlexCard.test.tsx` and `App.test.tsx`; `npm run build` typechecked and rebuilt
  both apps (manager output unchanged).
- Live Plex check through Pi 2 created a temporary smart audio playlist combining
  1990s, Pop/Rock and Acerbic: preview and saved membership both matched 21 tracks.
  Verified all three stored filter IDs, track type, random sort, album years,
  genre tags and discovery in the source catalog. Deleted the temporary playlist;
  saved display music settings were unchanged.
- Both Pis had active management/kiosk services, healthy HTTPS health, matching
  installed Python/static hashes and matching served JS after the pinned-SSH
  development pushes. New endpoints reject unauthenticated requests. No Android
  code changed; the existing APK remains applicable. Physical audio/endurance
  acceptance was not repeated.
- Animated music icon: 29 tests passed with
  `PYTHONPATH=src:tests python3 -m unittest test_scene_windows test_kiosk test_music_player -q`;
  compilation and whitespace checks passed. Tests cover icon-only redraws,
  unchanged captions, track/state/geometry changes and preserved carousel cadence.
- Both Pis passed native off-screen X11/Cairo rendering checks: eight distinct
  equalizer frames, unchanged caption pixels and paused-label rendering.
  Pi 2's live notification produced six distinct sampled frames. Pi 3's live
  notification was hidden; music settings were not changed to force playback.
  Services and HTTPS health were healthy; installed Python/static hashes and
  served JS matched, with no recent notification errors. This is not new physical
  speaker or endurance acceptance. No web UI or Android source changed, so their
  existing bundles/APK were retained.
- Playlist/mix extension: 61 Python tests passed with
  `PYTHONPATH=src:tests python3 -m unittest test_plex test_music_player test_app -q`.
  Compilation and whitespace checks passed.
- 23 UI tests passed with
  `cd ui && npm test -- src/components/MusicCard.test.tsx src/components/PlexCard.test.tsx src/App.test.tsx`.
  `npm run build` typechecked and rebuilt both apps. The final regression prevents
  saved-mix metadata lookup after Plex credentials are removed.
- Live Pi 2 checks discovered stations/playlists, searched/paged artist/album
  metadata, built artist-mix, album-mix and nonempty smart-playlist queues, and
  streamed audio bytes through a separate private relay. Temporary queue items
  were cleaned up; neither display's saved music settings was changed.
  These checks did not include listening to physical speaker output.
- Train space-reclamation checks used real Chromium at 1920x1080, 1280x720 and
  800x600, with 3, 8 and 40 services and previous departure shown/hidden.
  They establish geometry, not sustained physical-display acceptance.
- Full-suite counts recorded farther below are historical, not a claim that a
  full Python/UI suite was rerun for the latest extension.

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

## Outstanding Work And Acceptance

The latest requested music features and admin player controls are complete and deployed; no
further implementation is implied. Resume only the work the user requests.

- `verify-persistent-scenes`: still open. Dashboard acknowledgment retries and
  stage-specific diagnostics are implemented, but Pi 2 again reported
  `Dashboard did not acknowledge its rendered revision` on Nov 7 after about
  54.7 hours uptime. Zero service restarts did not mean correct carousel playback.
  The latest deployment restarted the kiosk; short healthy checks do not prove
  this recurrence fixed. Investigate revision acknowledgment/freeze-thaw and
  perform comparable multi-cycle/endurance checks without masking timeouts.
- `train-departure-scene`, `weather-scenes`, `plex-background-music`: implemented,
  committed in earlier iterations and deployed, not missing feature work.
  Sustained visual/provider/forecast/audio/queue-recovery acceptance remains
  incomplete. The playlist/mix extension is also deployed and live API/relay
  checked, but has no new physical-audio/endurance sign-off.
- `m2-pi2-wifi-dropout`: historical scan/dropout issue remains under observation.
  A reboot restored scanning; short clean samples do not establish reliability.
- `admin-ui-resume-session-resource-spike`: leaving the admin UI open and then
  suspending/resuming the computer can produce repeated fetch-error notifications
  instead of returning to login after session expiry, driving CPU/memory growth
  and making the tab unresponsive. Tracking only; not fixed by toast expiry.
  Investigate polling/effect lifecycle, cancellation, overlapping requests and
  expired-session handling after the carousel issue.
- `pi3-builtin-ap-phones`: built-in secured AP authentication remains unsupported
  with phones; use the tested compatible USB adapter, never an open AP.
- `verify-manager-address-fallback`: exercise an actual interface outage.
- `phase-b-ab-images`: blocked/deferred, not implemented.

Image uploads, native manager playlist editing, general-purpose overlays and
animated transitions remain deferred. Weekly sleep and the specific now-playing
notification are implemented exceptions to the original scheduling/overlay
deferral. Do not enlarge scope without a request.

### Historical Wi-Fi Observations

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
window; it is not evidence of present-day Wi-Fi reliability.

The previous short checks were on 2026-10-10. Both Pis' Ethernet ports were
re-enabled for manager multi-address testing, so Ethernet reachability alone is
not evidence that Wi-Fi is healthy. Confirm the present device/link state before
starting an observation; for Wi-Fi-only testing, disable the Ethernet switch
ports and verify the Wi-Fi address directly. Capture relevant journal output
before rebooting: on the latest Pi 2 check only the current boot was available,
despite a persistent-journal directory existing. Do not assume previous-boot
logs survive or close reliability work based on Ethernet reachability alone.

Other useful but not yet hardware-verified cases: manager address fallback when
one interface is actually down; the old-firmware warning on a real device; and
the device-side key-revocation card on Pi 2. These are not blockers for the
completed manager milestone.

## Historical Carousel Findings (2026-10-30)

The following measurements and deployment differences describe that checkpoint,
not the current matched development deployment. The later Nov 7 recurrence above
is the latest recorded long-run dashboard evidence.

### Implementation And Deployment

- Chromium is controlled through inherited DevTools pipes, not a debugging TCP
  listener. Keep at most two website tabs with LRU eviction and preserve their
  page state. Hidden website and local dashboard tabs are frozen and thawed
  before readiness checks. Dashboards update in place rather than navigate.
- Reuse mpv for consecutive videos, but stop it only after browser handover
  makes the next scene visible. Retaining idle mpv grew its memory across loops.
  Use one decoder, a 32 MiB forward/4 MiB back-cache limit and a 15-second
  read-ahead target. Consecutive-video transitions are not guaranteed gapless.
- The stabilization patch bounds pipe writes, checks active browser scenes every
  five seconds and contains the second process-wait timeout after SIGKILL.
  A process that has not exited remains tracked; recovery backs off for
  60 seconds rather than launching overlapping replacements.
- At that checkpoint, configuration and update state were preserved by a targeted
  patch with backed-up installed modules. That was different from today's
  development pushes, which replace the factory package and clear installed
  versions/state.
- Initial two-cycle Pi 2 observation followed configured scene order without
  failures or restarts, retained one Chromium process, and had no idle mpv in
  browser scenes. Minimum available RAM was 433 MiB and peak mpv PSS 86.5 MiB.
  That short run did not establish endurance.

### Historical Endurance And Vitals

The original overnight run after `9df7105` failed on Pi 2: 965 scene failures,
947 browser-control timeouts and two automatic kiosk restarts were recorded.
The fatal path was an uncaught final `process.wait(timeout=5)` during browser
shutdown. No kernel OOM kill was recorded. Pi 3 had one scene failure and no
restarts in that comparison; workloads differed.

The first overnight attempt after stabilization was interrupted by unplugged
power at an unknown time, so it is inconclusive, not evidence of a kiosk crash.
Power was restored on 2026-10-29 morning. A fresh baseline at 07:40 UTC had about
four minutes of uptime. Pre-NTP timestamps in the current boot must not be
mistaken for overnight runtime; previous-boot logs were unavailable.

| Snapshot (UTC) | Pi 2, Oct 29 21:55 | Pi 3, Oct 30 09:31 |
| --- | --- | --- |
| Uptime | 14.3 hours | About 9.1 days |
| Sampled system CPU | 3.2% | 12.7% |
| Available RAM | 385 MiB | 273 MiB |
| Swap used | 277 MiB | 149 MiB |
| Temperature | 50-52 C | 64-66 C |
| Service automatic restarts | 0 | 0 |
| Throttling | None | Historical frequency cap only |

These are different scenes/software and short CPU samples, not controlled
performance comparisons. Pi 2 swap grew from zero at boot, but that alone does
not prove a leak. Its post-restoration logs had no browser-control timeouts,
tracebacks, OOM kills or undervoltage events at the evening check.

**Historical blocker:** Pi 2 logged 128 dashboard startup failures by Oct 29
21:55, plus one Plex retry. Another 182 dashboard retries occurred between that
snapshot and Oct 30 09:31. The recorded error is "Scene did not start within
30 seconds." Other scene kinds continue playing, but the dashboard is skipped;
zero service restarts must not be mistaken for correct carousel operation.
The cause had not been established. Subsequent rendered-revision acknowledgment
retries and stage-specific diagnostics did not eliminate the later recurrence.
Relevant code remains `browser.py`, `scene_player.py` and
`ui/src/SceneScreen.tsx`; do not merely increase timeouts or disable tab retention.

Pi 2's fresh-boot 720p playback also showed rapidly increasing output-frame
drops despite `v4l2m2m` and zero decoder drops; smooth playback remains unproven.
Its evening mpv snapshot raced with transitions and could not assess playback.
Pi 3's latest 640x352 hardware-decoded sample had zero output/decoder drops and
no current scene failures. Do not infer visual acceptance from decoder counters.

These results motivated the dashboard diagnostics/retries and remain reasons
to measure scene failures/order as well as memory/swap and playback metrics.
No future sampling job is scheduled. Keep raw vitals/logs in session artifacts
rather than committing private configuration, Plex tokens or runtime capability
URLs.

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

- Under the former fbdev X setup, Chromium playing a Plex HLS transcode at
  about 404p and 2 Mbps (via hls.js)
  was smooth: about 0.5% dropped frames, about 50% CPU (peaks near 90% at
  start), peak 54 °C, no throttling, available memory down about 190 MB.
- Chromium cannot play the original `.m2ts` (MPEG-TS) file directly
  (`MEDIA_ERR_SRC_NOT_SUPPORTED`), and Chromium on Linux has no native HLS, so
  hls.js is required. The initial fbdev run did not measure 720p or 1080p;
  accelerated-X results are recorded below.
- Native `mpv` (0.40, Debian Trixie) did not help under fbdev. The V4L2 stateful decoder
  (`bcm2835-codec-decode`) works (`--hwdec=v4l2m2m-copy`), but under the former
  fbdev Xorg driver output was CPU-bound. At 1440x1080 or 720p, `--vo=x11` ran at
  about 88% CPU and played well under real time with hundreds of dropped
  frames; software decode was no better. `--vo=xv` is unsupported, `--vo=drm`
  crawled, and `--vo=gpu` (DRM or X11/EGL) dropped over half the frames.
- Historical fbdev conclusion: Chromium at about 480p or lower was preferred.
  The accelerated-X retests below supersede that recommendation; the current
  carousel uses native mpv. Load one live video at a time and keep Plex tokens
  in private device configuration only.
- At this historical test mpv was installed by hand; current image configuration
  includes it.

## Development And Validation

Run from the repository root. Full Python tests require Python 3.10+, `segno`,
and OpenSSL 3 with `pkeyutl -rawin` support on `PATH`:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
(cd ui && npm ci && npm run typecheck && npm test && npm run build)
python3 -m compileall -q src tests
```

The historical renderer-stabilization regression passed **257 Python tests**, including
real desktop Chromium retention and local-tab freeze/thaw coverage. The most
recent recorded UI suite passed **33 tests**, before the Python-only stabilization
changes. Desktop/file-fixture browser checks do not replace network/hardware
acceptance. Building the UI
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
- Playlist schema/scheduler: `src/webdisplay/carousel.py`; retained renderers:
  `src/webdisplay/browser.py`, `src/webdisplay/scene_player.py`.
- Rail/weather providers: `src/webdisplay/raildata.py`, `src/webdisplay/weather.py`;
  local rendering: `ui/src/SceneScreen.tsx`,
  `ui/src/components/ServiceStationTimes.tsx`.
- Music catalog/selection: `src/webdisplay/plex.py`; private queue/relay:
  `src/webdisplay/music.py`; kiosk player: `src/webdisplay/music_player.py`;
  administration: `ui/src/components/MusicCard.tsx`.
- Music settings retain `{enabled, station_key, volume}`. Source keys accept
  station paths, `/playlists/<id>/items` and `mix:artist:<IDs>` /
  `mix:album:<IDs>`. `GET /api/plex/music/browse` accepts bounded library/kind/
  search/offset queries, or a mix `key` alone for editing. Plex pagination must
  send container start and size together. Reuse the validated source/metadata
  helpers rather than inventing Plexamp `/mix` APIs.
- Remote view: `src/webdisplay/remote_view.py`; sleep:
  `src/webdisplay/sleep.py`; API types/clients: `ui/src/api.ts`,
  `ui/src/manager/api.ts`.
- Network supervisor and Wi-Fi recovery: `src/webdisplay/netmonitor.py`,
  `src/webdisplay/network.py`, and `src/webdisplay/wifi_helper.py`.
- Signed updates: `src/webdisplay/updater.py`, `src/webdisplay/releases.py`,
  `tools/`, and `image/stage-web-display/01-install/`.
- Manager backend: `src/webdisplay_manager/`; manager React UI:
  `ui/src/manager/`; device key UI: `ui/src/components/ManagerKeysCard.tsx`.
- Hardware tests are not represented by unit tests. Preserve the distinction
  between automated test results and checks performed on physical Pis.

## GPU-Accelerated X on Pi 2 (2026-10-17, hardware)

- Xorg with modesetting and glamor (`VC4 V3D 2.1`) replaced fbdev. A fresh X
  session was black on HDMI although X rendered correctly and the DRM state
  looked normal; the cause was a zeroed colour matrix (CTM) on the output.
  Setting it to the identity matrix (`xrandr --output <out> --set CTM
  1,0,0,0,1,0,0,0,1`) fixes it. `kiosk-session` does this at start, and a cold
  boot was confirmed to show the page.
- Chromium idles under 5% CPU on the departures page. mpv with GPU output on the
  1440x1080 clip reached about 10-21 fps (x11 about 5 fps) using about 22% CPU.
- Pi 3 was also deployed with the same modesetting config and CTM reset. The
  configured page was visually confirmed after a kiosk restart and a reboot;
  Xorg reported glamor acceleration on VC4 V3D 2.1.

### Chromium Video Retest

The same Plex clip was played in kiosk-mode Chromium through hls.js 1.6.13 under
modesetting/glamor; the viewport size was not recorded. Each measurement covered about one minute after playback
started, with actual video dimensions checked. Chromium reported
`V4L2VideoDecoder` and `kIsPlatformVideoDecoder=true` at all three resolutions.
CPU below is whole-system utilization, normalized across all four cores.

| Actual stream | Plex bitrate limit | Dropped frames | System CPU | End temperature |
| --- | --- | --- | --- | --- |
| 718x404, 25 fps | 1.5 Mbps | 22/1546 (1.4%) | 40% | 63 C |
| 1280x720, 25 fps | 4 Mbps | 100/1589 (6.3%) | 54% | 63 C |
| 1920x1080, 25 fps | 8 Mbps | 538/1592 (33.8%) | 69% | 65 C |

No measured buffering stalls or throttling occurred. hls.js reported recovered,
nonfatal `bufferFullError` events at every resolution. The low-resolution result
was not visually confirmed because the timed run ended before the observer
checked it; frame counters do not establish perceived smoothness. These runs
used a temporary browser profile and a local forwarding proxy, so the earlier
fbdev result is not a controlled before/after comparison.

Keep roughly 480p as the conservative default. Hardware decoding works in
Chromium, but 720p still drops some frames and 1080p is unsuitable in this
configuration. The normal kiosk was restored after testing.

### mpv Video Retest

With accelerated X, mpv 0.40 played the same three Plex HLS transcodes
fullscreen using `--no-config --vo=gpu --gpu-context=x11egl --hwdec=v4l2m2m`.
mpv confirmed hardware decoding and `drm_prime` video frames at each verified
resolution. The normal Chromium kiosk stayed running underneath the video.
Each sample covered about one minute after the first five seconds of playback.
CPU is whole-system utilization across all four cores, as in the Chromium test.

| Actual stream | Plex bitrate limit | Output frame drops | System CPU | End temperature |
| --- | --- | --- | --- | --- |
| 718x404, 25 fps | 1.5 Mbps | 0 | 18% | 57 C |
| 1280x720, 25 fps | 4 Mbps | 0 | 27% | 60 C |
| 1920x1080, 25 fps | 8 Mbps | 77 (about 5% of expected frames) | 53% | 64 C |

Playback advanced in real time at all three resolutions, with no decoder drops
or sampled cache pauses. No throttling was recorded after the runs. The observer
confirmed video was visible and smooth, without a separate per-resolution
visual assessment. Audio was not validated: muted playback was requested, but
the audio backends failed to open an output device.

These results supersede the earlier fbdev conclusion for native playback:
720p H.264 through mpv is a promising option on Pi 2, while 1080p still loses
frames. They do not establish direct playback performance for the original
interlaced clip or other codecs. Temperature differences from Chromium are
not a controlled thermal comparison. mpv exited after each run, and the normal
kiosk remained active; temporary playback files and forwarding were removed.

### Chromium Screen-Filling Retest

The observer reported that the earlier Chromium video initially did not fill
the screen, unlike mpv. A repeat explicitly set the browser size and fullscreen
window state. Browser measurements confirmed the video element covered the
entire 1919x1079 viewport on a 1920x1080 output (a one-pixel window margin).
The observer confirmed screen-filling playback at all three resolutions.
The same bitrate limits and hardware decoder were used as above; successful
samples lasted about two minutes.

| Actual stream | Dropped frames | System CPU | End temperature | Visual result |
| --- | --- | --- | --- | --- |
| 718x404, 25 fps | 131/3061 (4.3%) | 49% | 64 C | Screen-filling, stutters |
| 1280x720, 25 fps | 488/3052 (16.0%) | 58% | 66 C | Screen-filling, stutters |
| 1920x1080, 25 fps | No complete sample | Not measured | Not measured | Stutter, then black picture |

No buffering stalls or throttling were recorded in the completed samples;
recovered hls.js buffer-full events still occurred. During 1080p playback the
kernel logged vc4 GEM DMA allocation failures, followed by Chromium reporting
that its GPU process exited unexpectedly with exit code 11. The observer saw a
black picture with scroll bars. This is a playback failure, not a valid
frame-drop measurement. Restarting the normal kiosk restored the configured
page, visually confirmed by the observer.

These visually verified results supersede the earlier Chromium smoothness
assessment: even the low-resolution stream stutters when screen-filling.
mpv remains the better candidate for fullscreen video on Pi 2. The earlier
Chromium viewport was not recorded, so its performance cannot be directly
attributed to a particular window size.
