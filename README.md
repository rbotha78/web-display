# Raspberry Pi Web Display

A 32-bit Raspberry Pi OS appliance for Raspberry Pi 2 and Raspberry Pi 3,
with Chromium on HDMI, authenticated HTTPS administration, Wi-Fi provisioning,
signed application updates and PC and Android display managers.
Release **[v0.5.17](https://github.com/rbotha78/web-display/releases/tag/v0.5.17)**
adds enhanced train boards, manager remote views and floating now-playing information.
The current `main` branch additionally includes optional previous departures,
train-layout refinements and Plex playlists/custom mixes; those additions are
not part of the v0.5.17 release artifacts.
Saving settings retains the saved values when confirmation notifications appear,
without reloading stale configuration into the editor.
Administration opens on **Carousel**, with an **Administration** menu for
switching to **Global settings** (display defaults, sleep, provider credentials,
networking, updates and access keys). Draft edits stay intact when switching
screens; global settings load when first opened.

## Display features

The carousel supports websites, streamed/Plex video, clock/date/notice dashboards,
live National Rail departure boards and OpenWeather scenes. Plex radio stations,
audio playlists and artist/album shuffle mixes can play in the background across
scene changes, pausing for videos with audio.
Display scenes and their outer safe-area border use a black background.
Background music has a permanent, compact now-playing card floating in the
top-left corner, showing the song and artist when available, including on websites
and videos. It leaves browser and video layouts unchanged, so it can overlay scene
content. Long titles are
truncated to fit; paused music, loading and automatic retries are labelled
explicitly. The card respects the overscan safe area and disappears when music
is disabled or the display sleeps. Reconstructed manager previews do not include
this native display notification.

Train boards use locally bundled D-DIN regular and bold fonts (SIL Open Font
License, bundled as `fonts/OFL.txt` with update-compatible filenames).
They show departures and clearly labelled rail replacement buses,
platforms, station messages and per-service disruption notices. Status text is
green for on-time services, amber for delays and flashing red for cancellations.
Departure-board scenes accept separate optional **Destination CRS code** and
**Calling station CRS filter** settings (three letters, for example `WFJ` or
`BWO`). Leave either blank to disable that constraint. The calling-station filter
includes earlier and upcoming intermediate stops, not just the final destination;
services without that station in their route are excluded. The display combines
the rail API's `filterType=from` and `filterType=to` requests using `filterCrs`,
deduplicates services and shows the next eight departures in time order. Both
directions are also checked for previous-service look-backs. The provider may
omit earlier station times from board responses; these remain unknown rather
than being inferred or fetched through historical Service Details calls.
With both filters set, results must also terminate at the destination
code. With only a destination set, the existing API `to` filter is retained.
Different station/filter combinations have separate caches and departure tracking.

The next service uses compact station-and-time cells for previous stops, the
selected station and upcoming stops.
Each station strip stays one row high. On long routes, the selected station and
the known last-seen station stay pinned on the next service while the other stations automatically
scroll horizontally back and forth, pausing at each end. Short routes remain
static. Station names use at most two lines (full names
remain available on hover), so long routes cannot push departures off-screen.
Boards with six or more services use tighter later-departure rows and slightly
smaller text; the platform column leaves more room for destination names.
Cancelled services hide the route; actual last-reported observations remain
visible when available.
The next service and its station times stay fixed above the later departures,
which use slightly smaller text and scroll automatically without a visible
scrollbar. The previous-service row stays fixed below the scrolling area. Weather
uses a compact horizontal strip below it, wrapping only when needed to leave
more vertical space for departures.
The bottom clock shows hours, minutes and seconds in large, bold gold text in its
own row below the weather, including on dense departure boards.

Each train scene has a **Show previous departure** checkbox in the carousel
configuration, enabled by default for new and existing scenes. Turn it off to
hide the entire previous-service section. Upcoming-service rows expand vertically
to use the freed space, keeping the smaller text size; longer lists still scroll.
The manager's reconstructed preview follows the same setting.

A fixed row below the vertically scrolling upcoming-services area fetches the previous scheduled train
immediately using a departure-board API look-back of up to two hours, without
calling Service Details for previous services. It is labelled
**Previous scheduled service** until an actual departure is reported; an
"On time" estimate does not confirm departure, and missing actual times and
lateness remain unreported. Once confirmed, **Most recent departure** retains
the last train confirmed to have departed this station. The compact previous row
shows only the selected station, configured calling-filter station and final
destination, deduplicating stations that serve more than one role. Without a
calling filter it shows the selected station and destination. Missing timing data
is shown as `--`; terminal arrival times are included when reported. Times are
actual where reported, otherwise an estimated clock marked `est`, or scheduled.
Separate monitored-station arrival entries are omitted. Station strips fit within
the available width with no visible scrollbar; only unpinned next-service calling
stations scroll, without moving the fixed service rows.
Full timing data remains available
through the API; estimates are never presented as actual times.
Both service rows keep the selected board station highlighted in gold and the
last actually reported station in blue. For the next service, the last-seen
station is also shown with its actual observation time when known; schedules and estimates do
not produce a last-seen indicator. Both manager previews show this information.
Confirmed observations captured while a train was the next live service remain
visible until another train is confirmed to have departed. They are not polled
afterwards, so later station observations and destination arrival may be missing.
Rail replacement buses and cancelled station calls do not replace the row.

Restarts clear in-memory tracking and reload the previous scheduled service from
the API. This is a limited live-board look-back, not a historical departures feed;
if no eligible service is available within two hours, the row explains this.
Disappearing from the board alone is not proof of departure. The previous row
uses only data returned by departure boards or already captured during normal
next-service updates; it needs no Service Details API key. Missing station times
and actual observations remain unknown. Failed board look-backs retain the last
available data with a stale/error indication. Service Details requests are made
only for the next live service, never to poll departed or retained services.
Both manager remote views include the same recent-service information.

Weather appears in standalone scenes, optional dashboard widgets and a compact
train-board widget, with local condition icons, Celsius temperatures and forecast
data. Configure provider keys privately in administration on each device.
Rail refresh timing is configurable (minimum 30 seconds); weather normally
refreshes every 10 minutes. Failed fetches retry after 30 seconds.

A transparent circle fills clockwise during timed scenes and is hidden while
loading or playing uncapped videos. Local scenes and the circle respect an
adjustable overscan safe area. Train boards include larger weather text/icons and
a seconds clock in separate footer rows, preventing overlap. Oversized departure
content scrolls vertically while the heading, weather and clock remain visible.

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
Raspbian packages are fetched from the HTTPS mirrorservice mirror by default,
rather than the geographically redirected Oxford backend that has failed during
builds. Override it with `RASPBIAN_MIRROR=https://... ./build-image.sh` if needed.
Changing the mirror invalidates the cached development base and triggers a full
base rebuild.
The temporary account required by `pi-gen` is deleted during the custom stage,
and first-boot user renaming is explicitly disabled.

### Firmware variants

- `./build-image.sh` (default `VARIANT=development`) keeps key-only SSH for the
  `webdebug` account, authorised by `DEBUG_SSH_PUBLIC_KEY` (default
  `~/.ssh/id_rsa.pub`). Password login and root login are disabled. Use
  `sudo disable-web-display-debug` on the device to turn it off.
  Development devices also show a **Development SSH keys** card in the admin
  UI, and in each device card of the PC manager, to add or remove `webdebug`
  keys. Anyone with the admin password or a manager key can thereby gain root
  over SSH, so it exists only on development builds; devices built or pushed before this
  feature need `tools/push-to-device.sh` once to gain it.
- `VARIANT=release ./build-image.sh` is hardened: no debug account, SSH masked.
  Build this one as the final firmware. It always builds from scratch and
  compresses with `xz`.
- Development builds are faster: the first build keeps the pi-gen container with
  the finished base (stages 0-2), and later builds reuse it and rebuild only
  `stage-web-display`, compressing with `gz`. `FRESH=1 ./build-image.sh` forces a
  full rebuild; the base is also rebuilt after `BASE_MAX_AGE_DAYS` (default 14)
  to pick up OS updates. A code-only rebuild takes about 8 minutes instead of 25. `DEPLOY_COMPRESSION=none|gz|xz|zip` overrides the format.

### GitHub Actions

`.github/workflows/ci.yml` runs on every push to `main` and every pull request: the Python
tests, a shell syntax check, the UI typecheck and tests, and a check that the committed
`src/webdisplay/static/` bundle matches a fresh `npm run build`.

`.github/workflows/build-image.yml` builds the image on GitHub:

- **Publishing a release** builds `VARIANT=release` (hardened, `xz`), checks that the tag
  (`v0.4.3`) matches `WEB_DISPLAY_VERSION`, attaches the `.img.xz` and `SHA256SUMS` to the
  release, and creates a build provenance attestation (`gh attestation verify <image> --repo
  rbotha78/web-display`). Release images are boot-tested on a Pi before publication.
- **Run workflow** (manual) builds `development` or `release`. Development builds need your
  SSH public key in the `ssh_public_key` input. The result is a workflow artifact kept for 14 days.

Hosted builds are always full builds (about 30 minutes).

For quick iteration without reflashing, run
`tools/push-to-device.sh webdebug@DEVICE_IP` to copy the source and service files
to a development device and restart the management services. It installs everything listed in
`image/stage-web-display/01-install/files/install-manifest` (the same list the image build
uses, so add new files there once); add `--restart-kiosk` to also restart the
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
The kiosk checks HDMI output state every five seconds. When a screen reconnects,
or is connected without an active mode, it enables the preferred mode and
reapplies the VC4 colour-matrix reset. The active renderer is then restarted at
the recovered resolution (carousel playback restarts at its first enabled
scene). No reboot is required; normal connected playback is left untouched.

### Experimental carousel

The device administration page has a **Carousel** editor for up to 30 ordered
web-page, widget-dashboard, streamed-video, train and weather scenes. Add scenes, move them up
or down, choose which to include, enable the carousel, then select **Save and
apply**. Draft edits are not published. Each apply or kiosk restart starts at
the first enabled scene. Disabling the carousel restores the existing single
URL and its offline behaviour; it does not delete the playlist or URL.
Select **Check playback status** to show the last failure beside each affected
scene. These URL-free diagnostics clear when the scene successfully starts
again, and reset when the carousel renderer restarts. The authenticated
`GET /api/playlist/status` endpoint exposes the same report.

Dashboard scenes have clock/date, plain-text notice, or combined fixed layouts.
They are rendered by the bundled React UI on a read-only loopback server, with
no administrator credentials exposed. Web pages use direct navigation, not
iframes. The current renderer retains Chromium and prepares the next scene
before handover; see **Carousel transitions and retained websites** below.
Transitions are hard cuts, not animated or guaranteed gapless.

Page/dashboard/weather scenes default to 60 seconds; train scenes default to
300 seconds. Video scenes finish at end of
stream or their cutoff (default 300 seconds), whichever comes first; disabling
the cutoff allows full-length playback, including endless live streams.
Configurable durations are whole seconds from 5 to 86400. Web timers begin
after handover to the prepared page; local-scene timers require React's rendered
revision acknowledgment, and video timers require mpv playback readiness.
Startup has a separate 30-second timeout. Failed/unreachable scenes,
renderer exits and video stalls are skipped with a 60-second retry cooldown.
If all scenes fail, a local recovery screen remains visible between retries.
Starting the setup access point interrupts the carousel for the existing setup
screen; after it stops, the playlist starts again from its first enabled scene.

Direct-video scenes use HTTP(S) streams/files supported by mpv, not web-player
pages. Video audio is muted by default and can be enabled per scene, with the
tested X11/EGL and V4L2 decoding options.
For `.m3u8` master playlists the kiosk selects a rendition before starting mpv:
at most 1280x720 and 4 Mbps where advertised, excluding explicitly advertised
HEVC/AV1/VP9 codecs. This avoids opening every rendition (including 4K) during
startup. Masters with no compatible rendition are skipped; direct media
playlists and non-HLS files are not transcoded or downscaled by this selection.
The base-image package list now includes **mpv**. Existing devices need it
installed separately or a rebuilt base image: an application update or
development source push cannot install system packages. Missing mpv causes
direct-video scenes to fail and be skipped; browser/dashboard scenes still work.

### Plex library playback

Configure a Plex Media Server base address and token in the device
administration page; server/account discovery and Plex account sign-in are not
included. The token is write-only in the UI and stored in the private device
configuration; it is never included in playlist API responses, runtime scene
data, logs, HLS URLs or browser content. The picker browses movie and TV
libraries and saves only the selected Plex rating key in the playlist.

The device uses one persistent `X-Plex-Client-Identifier` across playback,
restarts and application updates. Each playback gets a separate session ID.
Plex HLS is relayed through a loopback-only proxy that injects the token as an
HTTP header and strips it from media-resource URLs; the transcoder session is
stopped when the scene ends, is replaced, or the service shuts down. This keeps
the token away from Chromium and mpv command-line URLs. Video scenes are muted
by default; enable audio per scene in the carousel settings when sound is
desired. Plex HLS playback is now active on both Pi 2 and Pi 3; recent device
checks showed hardware decoding with no dropped frames. Validate audio separately.

The authenticated API is `GET /api/playlist` and `PUT /api/playlist`, using the
same session/CSRF or bearer-key rules as other device settings. PUT replaces the
whole playlist with `{"enabled": boolean, "scenes": [...]}`. Each scene has a
unique `id`, `kind` (`web`, `dashboard`, `video`, `train`, `weather`), optional `title` and `enabled`,
and `duration` in seconds (`null` disables a video cutoff). Web and direct-video
scenes require `url`; Plex video scenes use `source: "plex"` and a
`plex_rating_key`; video scenes accept optional `audio_enabled` (default false).
Plex video scenes may set `plex_bitrate_kbps` to override the global default;
valid values are 500-20000 kbps. The global Plex default is 4000 kbps.
The effective Plex bitrate is used for both the server request and local HLS
selection/playback; the 720p resolution limit remains in place. Direct streams
retain their separate 4 Mbps limit.
Dashboards accept `layout` (`clock`, `notice`, `clock-notice`) and `text` (at
most 2000 characters), plus optional `show_weather`. Train scenes require
`station_crs`, accept optional `destination_crs` and `filter_station_crs`, and
default `show_previous_departure` to true. Weather scenes use the device's
configured weather location. Omitting timing uses the defaults above.
Private configuration is atomically saved; the administrator playlist API
contains no Plex token or playback capability URLs.
The authenticated Plex endpoints are `GET /api/plex/config`,
`PUT /api/plex/config`, `GET /api/plex/libraries`, and
`GET /api/plex/browse?key=...`. The config response exposes only the server
address and whether a token is configured. A blank token on save keeps the
current token; **Remove Plex settings** clears both server address and token.

#### Background library radio

The device administration page also offers **Background music**. Connect Plex,
select an advertised music-library station (Library Radio, Deep Cuts Radio,
Time Travel Radio, or Random Album Radio), an audio playlist, or a custom
artist/album mix, choose volume, and explicitly enable
playback. Music defaults **off**, including on existing installations; selecting
a source alone does not enable it. Settings are private and independent of the
carousel playlist. Removing Plex credentials disables music.

Regular and **smart playlists** are listed for the configured Plex token's user.
Playlist rules and order remain managed in Plex; each new playback queue evaluates
the current playlist, so smart-playlist changes take effect at the next queue
renewal or playback restart. Empty playlists report an explicit playback error
rather than falling back to another source.

Use **Build artist mix** or **Build album mix** to search and page through music
libraries, then select up to 20 artists or albums. The device saves that selection
and asks Plex to shuffle their tracks into a new mix on each queue renewal.
These are selection-based shuffle mixes, not Plexamp sonic recommendations or
Guest DJ sessions, and they are saved on this display rather than creating a
playlist on Plex. Saved Plexamp mixes appear in the source picker when the server
exposes them as audio playlists or supported station entries. Plexamp-only
client-local mixes cannot be imported directly; save them as an audio playlist
in Plex first. Playback still uses only music available to the configured token.

A separate audio-only persistent mpv plays through the selected audio output
across web, dashboard, weather, train and muted-video scenes, including single-page mode. Videos with
audio pause music and release its ALSA device, then music resumes at the same
position. The kiosk sees only scoped loopback control/stream URLs in
`/run/web-display/music.json`; Plex tokens remain in the HTTPS service and never
appear in player arguments. Tracks stream directly through a range-capable
private relay using mpv's installed audio decoders; no Plex audio transcode or
additional OS packages are required.

**Display → Audio output** selects the device-wide output for both background
music and video audio. HDMI is the default; available playback devices include
the Pi's 3.5 mm jack and connected USB audio devices. Reload the administration
page after connecting a device. Selection uses stable ALSA card identifiers,
not card numbers; changing output restarts playback. A disconnected selected
device is shown as unavailable and playback fails visibly rather than silently
switching outputs. This setting does not enable audio on muted video scenes.

The service owns a server-generated music queue, reports playing/paused/stopped
timelines and retrieves a window centered on the completed track to find the
next item. Station queues on the tested server are finite: reaching their actual
end creates a **new queue for the same source**, rather than assuming Plex
automatically refills them indefinitely. New queue creation may briefly interrupt
audio, and sources may repeat tracks. Playback is not gapless or crossfaded.
Connection, decoder and stalled-player failures retry after 5 seconds, then
15 seconds, then once per minute while music remains enabled and the display
is awake. Repeated failures clear the stale Plex queue so stations recover
after a server restart without manually toggling music. Successful playback
resets the retry delay. The card reports sanitized playback state/errors.

Authenticated endpoints: `GET /api/plex/stations`, `GET /api/plex/music/browse`, `GET /api/plex/music` and
`PUT /api/plex/music` (session mutations require CSRF). Music settings are
`enabled`, `station_key`, and integer `volume` (0–100, default 30).
The stations endpoint now includes audio playlists and advertised mixes.
Music browsing accepts `library`, `kind` (`artist`/`album`), `search`, and
`offset` (100 results per page), or `key` to resolve an existing custom mix.
The backwards-compatible `station_key` also accepts `/playlists/<id>/items`
and `mix:artist:<comma-separated IDs>` / `mix:album:<comma-separated IDs>`;
arbitrary URLs and non-music selections are rejected.

Slice 1 was accepted on 2026-10-19 after physical video playback confirmation on
Pi 2 and Pi 3, unavailable-content and browser/player-crash recovery checks,
single-URL restoration, and physical screen power-off/on recovery confirmation.
An approximately 18-hour endurance comparison showed stable available memory,
no automatic service restarts and no current throttling; sampled video playback
used hardware decoding with no reported dropped frames. Some transient scene
failures occurred and later cleared. Pi 3 temperature and Pi 2 swap growth remain
monitoring items, not guarantees of indefinite unattended operation. Plex
playback has been validated on both devices; audio validation, image uploads
and manager-side playlist editing remain deferred.

### Weekly sleep mode

The administration page's **Weekly sleep** card configures one enabled sleep
window per weekday, using the **display's local timezone**, not the phone or
browser timezone. Each day can be disabled. An end time earlier than its start
continues into the following day, including Sunday into Monday; disabling that
following day does not truncate the previous day's overnight window. Enabled
windows must have different start and end times. Existing devices default to
no scheduled sleep.

During sleep, background music stops completely and video scenes are stopped
and skipped, including videos already playing or being prepared. Web pages,
train boards, weather and dashboard scenes continue normally; sleep does not
turn off the screen. A playlist containing only videos shows carousel recovery
while asleep. Music and eligible videos return when the sleep window ends,
subject to their normal enabled settings.

**Wake temporarily** overrides scheduled sleep for 15 minutes, 30 minutes,
1 hour, 2 hours or 4 hours. It does not change the weekly schedule. Cancel the
override to return immediately to the schedule; otherwise it expires
automatically. A saved override deadline survives service/device restarts.
Check the displayed device time and timezone when scheduling; correct device
time is required, and daylight-saving transitions follow its local wall clock.

### Carousel transitions and retained websites

Carousel mode reuses one Chromium process and reuses mpv for consecutive videos
rather than restarting renderers at every scene boundary. Dashboards update in place. Websites
use normal top-level navigation, not iframes, so sites that block embedding
remain supported. Chromium control uses private inherited pipes, with no
remote-debugging network port.
Cold browser startup has a separate 20-second control allowance; failed browser
initialisation or control is discarded before preparing another scene, rather
than retaining a broken tab cache. Renderer recovery waits 60 seconds before
restarting to avoid repeated cold starts under memory pressure. A process that
has not exited after termination is kept tracked; no replacement is started
until it has been reaped. Playback status includes the active and
preparing scene IDs so startup skips can be distinguished from playlist order.

At most two website tabs are retained, in addition to the local dashboard and
recovery tab. Revisiting a retained tab preserves its page state without
reloading. Hidden website and dashboard tabs are frozen; they resume when prepared
for display. Active browser scenes are checked every five seconds rather than
continuously polling Chromium.
When a third website is needed, the least-recently-used non-current website is
closed and must reload on its next visit. Saving a playlist, restarting the kiosk,
or display-output recovery resets the cache. Retention is not an automatic refresh:
sites that need fresh data should update themselves when resumed.

The current scene remains visible while the next website loads or its video URL
is resolved. The next scene's duration starts only after handover. Video uses one
embedded output window, waits for decoded output before being raised, and enables
audio only at handover. Leaving video pauses/mutes it during preparation and
releases playback and exits mpv after the browser is visible, returning its retained
decoder and allocator memory to the system. Consecutive videos reuse mpv via
`loadfile`, but still need stream buffering and decoder reinitialisation: these
transitions are not guaranteed gapless and do not preload a second video decoder.
Video buffering is limited to 32 MiB ahead and 4 MiB behind, with a 15-second
read-ahead target, instead of mpv's larger desktop defaults.
Animated fades and crossfades are not implemented.

This transition path has automated coverage, including real desktop Chromium tab
retention when Chromium is installed. Pi 2/Pi 3 visual, audio, memory and endurance
acceptance of the persistent-renderer path is still required.

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
the AP starts). WPA2-PSK/CCMP is used with protected management frames explicitly
disabled because the Pi 3's built-in Wi-Fi fails to initialize the AP otherwise.
This does not disable WPA2 data encryption. The SSID, password and administration
address (`https://10.42.0.1:8443/`)
are shown on the display's fallback page. While the AP is up the saved Wi-Fi network is
retried every 10 minutes; if that fails the AP returns with new credentials. The AP stops
as soon as internet is reachable again and the saved client network is restored.
If a client connection attempt fails while the setup AP is active, the saved client
profile is rolled back and the setup AP is immediately restored with rotated credentials.
The saved Wi-Fi profile retries indefinitely, and the supervisor reactivates it every
60 seconds if it is inactive and no setup AP is running, even when Ethernet is online or
automatic AP is disabled. On supervisor restart, any stale setup AP is removed and the
saved client profile is explicitly reactivated. Wi-Fi routes use metric 600; the standard
Ethernet route metric is 100, so Ethernet remains preferred when both links are active.
Limitation: the country must first be set while an administration route (Ethernet)
exists. Test manually with `sudo python3 -m webdisplay.netmonitor start-ap` and `stop-ap`
(stop `web-display-network` first, or it will remove the AP while Ethernet is online).
On Raspberry Pi OS Trixie, Pi 3 Model B AP startup previously failed with
`nl80211: kernel reports: key setting validation failed` /
`802.1X supplicant took too long to authenticate`. Explicitly disabling protected
management frames in the WPA2 profile allowed the AP to activate, but tested Android
and iPhone clients both failed WPA2 authentication with an “incorrect password”
message. Detailed logs show clients reaching the WPA handshake before disconnecting.
The Pi 3's secured AP is not yet usable; do not use an open AP as a workaround.

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
- Xorg uses the modesetting driver with glamor on vc4 KMS. On Pi 2, a fresh X
  session left the HDMI output black because the CRTC colour matrix (CTM) was
  zeroed; `kiosk-session` resets it to the identity matrix before starting
  Chromium. Chromium is sized from the active X resolution because no desktop
  window manager is installed.
- The mouse cursor is permanently hidden: Xorg starts with `-nocursor`, since no
  pointing device is ever used on the kiosk.

## Administration UI

The administration page is a React + TypeScript single-page app in `ui/` (Vite,
no UI framework dependencies, dark/light themes, responsive). The compiled bundle
is committed in `src/webdisplay/static/` and served by the Python service, so
neither the Pi nor the image build needs Node. After changing anything in `ui/`:

```sh
cd ui && npm ci && npm run typecheck && npm test && npm run build
```

and include the regenerated `src/webdisplay/static/` and
`src/webdisplay_manager/static/` in UI commits. `npm run dev` runs a hot-reload
server that proxies `/api` to a service on `https://localhost:8443`. The page is
served with a strict Content-Security-Policy (no inline scripts or styles).

### Overscan and local scene readability

In device administration, open **Display → Overscan safe area (% each edge)**,
then **Save settings**. The per-device setting defaults to **5%** on each edge,
including existing devices, accepts whole percentages **0–10**, and survives
restarts. It affects locally rendered train, weather, dashboard and recovery
screens and the X11 countdown only; external web pages and video stay full-screen.
Changes take effect shortly without rebooting.

Train boards use larger departure, route-detail and notice text. Their heading,
enlarged current-weather widget and clock stay visible while oversized departure tables
and station/service notices scroll automatically, with a visible scrolling hint
and pauses at each end. Long dashboard content also scrolls rather than being
silently truncated. All content remains available, but arbitrarily long provider
notices/routes cannot fit simultaneously on a finite screen, and a short scene
duration may end before a complete scrolling pass. Previous-stop and calling-at
lists scroll horizontally on separate single lines. Increase scene duration when
needed. Layouts target both 1920×1080 and 1152×864; TV/hardware readability still
requires checking on the actual display.

## Signed updates (milestone 3, Phase A implemented)

Application updates are signed `.wdupdate` bundles installed by a root helper
(`update-action`) into `/var/lib/web-display-app/versions/<version>`. The helper,
units, sudoers rules, public key and OS are part of the base image and are not
updatable by a bundle. Bundles are Ed25519-signed; the device verifies the
signature, every file hash, the version (no downgrades) and compatibility.

- Admin page, **Updates**: check GitHub Releases and install, or upload a file.
- Updates apply after a restart. The boot guard rolls back automatically if the
  new version does not start three times or the services are not healthy for 20
  seconds within 180 seconds; failed versions are never retried.
- CI signs a bundle on each release with the `UPDATE_SIGNING_KEY` secret and
  verifies it against `image/stage-web-display/01-install/files/update-key.pub`.
  Keep an offline backup of the private key; never commit it.
- Local build: `python3 tools/make_update_bundle.py --version 0.5.1 --key-file KEY`
  (needs OpenSSL 3). Check with `tools/verify_update_bundle.py`.
- Devices need a base image of 0.5.0 or later (or a `tools/push-to-device.sh`
  push) before they can accept bundles. Valid installation, invalid signatures,
  tampering, rollback and anti-retry were hardware-tested on Pi 2 and Pi 3.
  Manager-driven GitHub release updates to 0.5.7 and 0.5.8 were also tested on
  both Pis. The device admin page's direct upload/release-pull workflow still
  needs separate hardware validation. See `HANDOFF.md` for current status.

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

## Central device manager

A Python app on your PC manages several displays from one page (needs only the repository; the UI is prebuilt):

```sh
PYTHONPATH=src python3 -m webdisplay_manager     # or `webdisplay-manager` once installed
```

It serves a UI on `127.0.0.1:8765` and opens the browser. Devices are found via mDNS
(`_web-display._tcp`) or added by address. On adding, the manager shows the device's TLS
fingerprint (compare it with the device screen), then uses the administrator password once to
create an API key for this PC. Only the key and pinned fingerprint are stored, in
`~/.config/web-display-manager/devices.json` (mode 0600); passwords are never stored.
Functions: status, change display address, check/install updates (one or all), restart.
**Remote view** opens a reconstructed preview of the current display scene,
refreshing every five seconds only while the view is open. Dashboard clocks
and notices, train boards and weather use the display's current scene data.
Web pages show an address/details card; videos show a metadata card and are
never streamed to the manager. This is not a screenshot or remote-control
session: browser page contents, playback pixels and exact TV layout are not
reproduced. The view identifies recovery/sleep states and reports unavailable
or stale data rather than presenting it as live. Device firmware must support
the authenticated `/api/remote-view` endpoint.
**Open admin page** signs the browser in using the manager's existing key, without
storing or asking for the password. The device issues a single-use login token that
expires after one minute; it travels in the URL fragment (not access logs) and is
removed from the address bar before exchange for a secure, HttpOnly session cookie.
These sessions retain manager-key permissions: API-key management still requires a
password login, and revoking the originating key also invalidates its browser sessions.
Both the manager and device need updated software; **Open without automatic login**
remains available for manual login or browser certificate confirmation.
Keys are listed and revoked in the device admin page's "Manager access" card (API: `GET /api/keys`, `DELETE /api/keys/<id>`, session login only).
Notifications in both web UIs disappear after five seconds (ten for errors) and
can also be dismissed immediately.
Rebuild the manager UI with `cd ui && npm run build`.

The manager shares the firmware version (`WEB_DISPLAY_VERSION`; `--version` prints it). When releasing, bump
`WEB_DISPLAY_VERSION` and `version` in `pyproject.toml` together (a test enforces this). The manager refuses to
enrol, and warns about, devices older than `MIN_FIRMWARE` in `src/webdisplay_manager/version.py` (0.5.7).

### Standalone Android manager

`android/` contains a native Kotlin/Jetpack Compose manager for **Android 10
(API 29) or newer**. It connects directly to displays over the local network;
neither the PC manager nor a cloud account is needed. Keep the phone and displays
on a network that permits peer-to-peer traffic. mDNS discovers
`_web-display._tcp` devices; manual hostname/IP and port entry is also available
when multicast is blocked.

The app supports the same operations as the PC manager: device enrollment,
online/offline and network status, URL changes, restart, individual and fleet
update checks/installations, SSH public-key management on development images,
automatic or manual browser administration links, and the same reconstructed
**Remote view** available in the PC manager. Installation does not
automatically reboot a display. Detailed device settings and carousel editing
remain in the device's administration page, as with the PC manager.

Enrollment shows the certificate's SHA-256 fingerprint for confirmation before
sending the administrator password. Subsequent connections verify the saved
fingerprint even when trying alternative addresses. API keys and device records
are encrypted using an Android Keystore key and excluded from Android backups;
administrator passwords are not saved. Changed certificates or rejected keys
require explicit re-enrollment. Removing a device locally **does not revoke**
the app's API key; revoke it in the device's **Manager access** card.

Browser administration uses the browser's own TLS trust rules. A confirmed pin
in the app does not suppress the browser's self-signed-certificate warning;
use manual administration to confirm the certificate when necessary. Devices
need firmware 0.5.7 or newer; automatic administration links need newer firmware
that supports single-use login tokens.

Build with JDK 17 and an Android SDK containing platform 36 and build tools
35.0.0. Set `ANDROID_HOME` to the SDK location (or use an ignored
`android/local.properties` with `sdk.dir=...`):

```sh
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
adb install -r app/build/outputs/apk/debug/web-display-manager.apk
```

The APK is named **web-display-manager.apk**. The **Android manager** GitHub
Actions workflow builds an installable debug APK
artifact. Debug APKs are for development/sideloading, not Play Store release
distribution; production signing is not configured. Phone-side discovery,
enrollment and visual acceptance need testing on an Android device.
