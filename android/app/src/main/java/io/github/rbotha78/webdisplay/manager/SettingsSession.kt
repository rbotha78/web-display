package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.ConcurrentHashMap

interface SettingsBackend {
    suspend fun identity(id: String): Pair<String, String?>
    suspend fun fetch(id: String, paths: List<String>, requests: PinnedHttp.RequestScope): Map<String, Result<JSONObject>>
    suspend fun send(id: String, method: String, path: String, body: JSONObject?, timeoutMs: Int): JSONObject
    suspend fun installUpdate(id: String): String
}

enum class SettingsTab { GLOBAL, CAROUSEL }

enum class SettingsPart(val path: String, val label: String) {
    DISPLAY("/api/config", "Display"),
    AUDIO("/api/audio/outputs", "Audio outputs"),
    BLUETOOTH("/api/bluetooth", "Bluetooth speaker"),
    SLEEP("/api/sleep", "Sleep schedule"),
    PLEX("/api/plex/config", "Plex"),
    MUSIC("/api/plex/music", "Background music"),
    STATIONS("/api/plex/stations", "Music sources"),
    RAIL("/api/raildata/config", "National Rail"),
    WEATHER("/api/weather/config", "Weather"),
    STATUS("/api/status", "Hostname"),
    NETWORK("/api/network", "Wi-Fi"),
    UPDATES("/api/update/status", "Updates"),
    PLAYLIST("/api/playlist", "Carousel"),
}

data class Section<S, D>(
    val server: S? = null,
    val draft: D? = null,
    val loading: Boolean = false,
    /** A load failure when [server] is null, otherwise the last rejected save or action. */
    val error: String? = null,
    val unsupported: Boolean = false,
    /** The device changed after this section was loaded; saving needs an explicit overwrite. */
    val conflict: Boolean = false,
)

data class DeviceIdentity(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    val hostname: String?,
    val version: String?,
    val fingerprint: String? = null,
)

data class PlexPicker(
    val sceneId: String,
    val path: List<Pair<String, String?>>,
    val items: List<PlexBrowseItem> = emptyList(),
    val loading: Boolean = true,
    val error: String? = null,
)

data class SettingsState(
    val identity: DeviceIdentity,
    val tab: SettingsTab = SettingsTab.GLOBAL,
    val working: String? = null,
    val display: Section<DisplayConfig, DisplayDraft> = Section(),
    val audio: Section<List<AudioOutput>, Unit> = Section(),
    val bluetooth: Section<BluetoothStatus, Unit> = Section(),
    val bluetoothScan: List<BluetoothDevice>? = null,
    val bluetoothSelectedAddress: String = "",
    val sleep: Section<SleepConfig, SleepSchedule> = Section(),
    val plex: Section<PlexSettings, PlexDraft> = Section(),
    val music: Section<MusicSettings, MusicDraft> = Section(),
    val stations: Section<List<MusicStation>, Unit> = Section(),
    val rail: Section<RailSettings, RailDraft> = Section(),
    val weather: Section<WeatherSettings, WeatherDraft> = Section(),
    val status: Section<DeviceInfo, String> = Section(),
    val network: Section<NetworkInfo, WifiDraft> = Section(),
    val updates: Section<UpdateInfo, Unit> = Section(),
    val playlist: Section<Playlist, Playlist> = Section(),
    val scan: List<WifiNetwork>? = null,
    val updateCheck: UpdateCheck? = null,
    val playback: PlaybackStatus? = null,
    val picker: PlexPicker? = null,
) {
    fun dirtyParts(): List<SettingsPart> = SettingsSession.bindings.filter { it.value.dirty(this) }.keys.toList()
    fun dirty(part: SettingsPart): Boolean = SettingsSession.bindings[part]?.dirty(this) == true
}

/**
 * Owns one device's settings editor. Drafts survive tab/section switches and are only replaced by
 * an explicit reload or a successful save; there is no background polling.
 */
class SettingsSession(
    private val scope: CoroutineScope,
    private val backend: SettingsBackend,
    private val notify: (String) -> Unit,
) {
    internal class Binding<S, D>(
        val get: (SettingsState) -> Section<S, D>,
        val set: (SettingsState, Section<S, D>) -> SettingsState,
        val parse: (JSONObject) -> S,
        val draft: (S) -> D?,
        val tracksDrafts: Boolean = true,
    ) {
        fun dirty(state: SettingsState): Boolean {
            val section = get(state)
            val server = section.server ?: return false
            return tracksDrafts && section.draft != null && section.draft != draft(server)
        }
    }

    companion object {
        internal val bindings: Map<SettingsPart, Binding<*, *>> = mapOf(
            SettingsPart.DISPLAY to Binding({ it.display }, { s, v -> s.copy(display = v) },
                SettingsRules::parseDisplay, SettingsRules::displayDraft),
            SettingsPart.AUDIO to Binding({ it.audio }, { s, v -> s.copy(audio = v) },
                SettingsRules::parseAudioOutputs, { null }),
            SettingsPart.BLUETOOTH to Binding({ it.bluetooth }, { s, v -> s.copy(bluetooth = v) },
                SettingsRules::parseBluetooth, { null }, tracksDrafts = false),
            SettingsPart.SLEEP to Binding({ it.sleep }, { s, v -> s.copy(sleep = v) },
                SettingsRules::parseSleep, { it.schedule }),
            SettingsPart.PLEX to Binding({ it.plex }, { s, v -> s.copy(plex = v) },
                SettingsRules::parsePlex, SettingsRules::plexDraft),
            SettingsPart.MUSIC to Binding({ it.music }, { s, v -> s.copy(music = v) },
                SettingsRules::parseMusic, SettingsRules::musicDraft),
            SettingsPart.STATIONS to Binding({ it.stations }, { s, v -> s.copy(stations = v) },
                SettingsRules::parseStations, { null }),
            SettingsPart.RAIL to Binding({ it.rail }, { s, v -> s.copy(rail = v) },
                SettingsRules::parseRail, SettingsRules::railDraft),
            SettingsPart.WEATHER to Binding({ it.weather }, { s, v -> s.copy(weather = v) },
                SettingsRules::parseWeather, SettingsRules::weatherDraft),
            SettingsPart.STATUS to Binding({ it.status }, { s, v -> s.copy(status = v) },
                SettingsRules::parseDeviceInfo, { it.hostname }),
            SettingsPart.NETWORK to Binding({ it.network }, { s, v -> s.copy(network = v) },
                SettingsRules::parseNetwork, { WifiDraft(it.country.takeIf { c -> c != "00" }.orEmpty()) },
                tracksDrafts = false),
            SettingsPart.UPDATES to Binding({ it.updates }, { s, v -> s.copy(updates = v) },
                SettingsRules::parseUpdate, { null }),
            SettingsPart.PLAYLIST to Binding({ it.playlist }, { s, v -> s.copy(playlist = v) },
                SettingsRules::parsePlaylist, { it }),
        )

        @Suppress("UNCHECKED_CAST")
        private fun binding(part: SettingsPart) = bindings.getValue(part) as Binding<Any?, Any?>

        const val UNSUPPORTED = "This device's firmware does not support this setting. Update the device to use it."
        const val INTERRUPTED = "Loading was interrupted. Reload to try again."
    }

    private val mutable = MutableStateFlow<SettingsState?>(null)
    val state: StateFlow<SettingsState?> = mutable
    private var generation = 0
    private var requests = PinnedHttp.RequestScope("Settings closed")
    private val loads: MutableSet<Job> = ConcurrentHashMap.newKeySet()
    private val mutations: MutableSet<Job> = ConcurrentHashMap.newKeySet()
    private val writeLock = Mutex()

    fun open(identity: DeviceIdentity) {
        if (mutable.value?.identity?.id == identity.id) return
        close()
        mutable.value = SettingsState(identity)
        val current = generation
        track(loads, scope.launch {
            try {
                val (fingerprint, hostname) = backend.identity(identity.id)
                if (current == generation) mutable.update {
                    it?.copy(identity = it.identity.copy(fingerprint = fingerprint, hostname = it.identity.hostname ?: hostname))
                }
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                // The identity card falls back to the status values already shown.
            }
        })
        load(SettingsPart.entries.toSet() - SettingsPart.AUDIO, discard = true)
        load(setOf(SettingsPart.AUDIO), discard = true)
    }

    fun close() {
        generation++
        requests.cancel()
        requests = PinnedHttp.RequestScope("Settings closed")
        loads.toList().forEach { it.cancel() }
        mutable.value = null
    }

    /** Called when the app leaves the foreground: every request is cancelled, drafts are kept. */
    fun pause() {
        requests.cancel()
        loads.toList().forEach { it.cancel() }
        mutations.toList().forEach { it.cancel() }
    }

    fun resume() {
        requests = PinnedHttp.RequestScope("Settings closed")
        val state = mutable.value ?: return
        mutable.update { it?.copy(picker = it.picker?.copy(loading = false)) }
        val missing = SettingsPart.entries.filter { part ->
            val section = binding(part).get(state)
            section.server == null && !section.loading && !section.unsupported
        }.toSet()
        if (missing.isNotEmpty()) load(missing, discard = false)
    }

    fun setTab(tab: SettingsTab) = mutable.update { it?.copy(tab = tab) }

    /** Reloads everything and discards drafts; callers confirm first when drafts are dirty. */
    fun reload() {
        mutable.update { it?.copy(scan = null, updateCheck = null, playback = null) }
        load(SettingsPart.entries.toSet(), discard = true)
    }

    fun reload(part: SettingsPart) = load(setOf(part), discard = true)

    fun discard(part: SettingsPart) = mutable.update { state ->
        state ?: return@update null
        val binding = binding(part)
        val section = binding.get(state)
        binding.set(state, section.copy(draft = section.server?.let(binding.draft), error = null, conflict = false))
    }

    // ---------- Draft edits ----------

    fun editDisplay(change: (DisplayDraft) -> DisplayDraft) = edit({ it.display }, { s, v -> s.copy(display = v) }, change)
    fun editSleep(change: (SleepSchedule) -> SleepSchedule) = edit({ it.sleep }, { s, v -> s.copy(sleep = v) }, change)
    fun editPlex(change: (PlexDraft) -> PlexDraft) = edit({ it.plex }, { s, v -> s.copy(plex = v) }, change)
    fun editMusic(change: (MusicDraft) -> MusicDraft) = edit({ it.music }, { s, v -> s.copy(music = v) }, change)
    fun editRail(change: (RailDraft) -> RailDraft) = edit({ it.rail }, { s, v -> s.copy(rail = v) }, change)
    fun editWeather(change: (WeatherDraft) -> WeatherDraft) = edit({ it.weather }, { s, v -> s.copy(weather = v) }, change)
    fun editHostname(value: String) = edit({ it.status }, { s, v -> s.copy(status = v) }) { value }
    fun editWifi(change: (WifiDraft) -> WifiDraft) = edit({ it.network }, { s, v -> s.copy(network = v) }, change)
    fun editPlaylist(change: (Playlist) -> Playlist) = edit({ it.playlist }, { s, v -> s.copy(playlist = v) }, change)
    fun editScene(id: String, change: (Scene) -> Scene) =
        editPlaylist { playlist -> playlist.copy(scenes = playlist.scenes.map { if (it.id == id) change(it) else it }) }

    private fun <S, D> edit(
        get: (SettingsState) -> Section<S, D>, set: (SettingsState, Section<S, D>) -> SettingsState, change: (D) -> D,
    ) = mutable.update { state ->
        state ?: return@update null
        val section = get(state)
        val draft = section.draft ?: return@update state
        set(state, section.copy(draft = change(draft)))
    }

    // ---------- Saves ----------

    fun saveDisplay() = save(SettingsPart.DISPLAY, "Display settings saved. The display will update shortly.") { state ->
        SettingsRules.displayPayload(requireNotNull(state.display.draft), requireNotNull(state.display.server))
    }

    fun saveSleep(force: Boolean = false) = save(SettingsPart.SLEEP, "Sleep schedule saved on the device.", force,
        compare = { a, b -> (a as SleepConfig).schedule == (b as SleepConfig).schedule }) { state ->
        SettingsRules.sleepPayload(requireNotNull(state.sleep.draft))
    }

    fun savePlex() = save(SettingsPart.PLEX, null, after = ::afterPlexChange) { state ->
        SettingsRules.plexPayload(requireNotNull(state.plex.draft), requireNotNull(state.plex.server))
    }

    fun clearPlex() = save(SettingsPart.PLEX, "Plex settings removed.", useDraft = false, after = ::afterPlexChange) {
        SettingsRules.plexClearPayload()
    }

    fun saveMusic(force: Boolean = false) = save(SettingsPart.MUSIC, null, force,
        compare = { a, b -> SettingsRules.musicDraft(a as MusicSettings) == SettingsRules.musicDraft(b as MusicSettings) },
        after = { saved -> notify(if ((saved as MusicSettings).enabled) "Background music enabled." else "Background music disabled.") },
    ) { state -> SettingsRules.musicPayload(requireNotNull(state.music.draft)) }

    fun saveRail() = save(SettingsPart.RAIL, "National Rail settings saved on the device.") { state ->
        SettingsRules.railPayload(requireNotNull(state.rail.draft))
    }

    fun clearRail(board: Boolean, details: Boolean) =
        save(SettingsPart.RAIL, "National Rail API key removed.", useDraft = false) { SettingsRules.railClearPayload(board, details) }

    fun saveWeather() = save(SettingsPart.WEATHER, "Weather settings saved on the device.") { state ->
        SettingsRules.weatherPayload(requireNotNull(state.weather.draft))
    }

    fun clearWeather() = save(SettingsPart.WEATHER, "OpenWeather API key removed.", useDraft = false) {
        SettingsRules.weatherClearPayload()
    }

    fun savePlaylist(force: Boolean = false) = save(SettingsPart.PLAYLIST,
        "Carousel saved and applied. Playback restarts at the first enabled scene.", force,
        compare = { a, b -> a == b }) { state -> SettingsRules.playlistPayload(requireNotNull(state.playlist.draft)) }

    private fun afterPlexChange(saved: Any?) {
        val plex = saved as PlexSettings
        if (plex.configured) notify("Verified ${plex.serverName.ifBlank { "Plex Media Server" }} and saved Plex settings.")
        load(setOf(SettingsPart.MUSIC, SettingsPart.STATIONS), discard = false)
    }

    private fun save(
        part: SettingsPart,
        success: String?,
        force: Boolean = false,
        compare: ((Any?, Any?) -> Boolean)? = null,
        useDraft: Boolean = true,
        after: (Any?) -> Unit = {},
        payload: (SettingsState) -> JSONObject,
    ) {
        val state = mutable.value ?: return
        val binding = binding(part)
        val section = binding.get(state)
        if (section.server == null || (useDraft && section.draft == null)) return
        val body = try {
            payload(state)
        } catch (error: IllegalArgumentException) {
            setError(part, error.message ?: "Invalid settings")
            notify(error.message ?: "Invalid settings")
            return
        }
        mutate("Saving ${part.label.lowercase()}", part) { id, current ->
            if (compare != null && !force) {
                val fresh = backend.fetch(id, listOf(part.path), PinnedHttp.RequestScope("Settings closed"))
                    .getValue(part.path).getOrThrow()
                if (!compare(binding.parse(fresh), section.server)) {
                    if (current()) updateSection(part) {
                        it.copy(conflict = true, error = "${part.label} changed on the device after it was loaded. " +
                            "Overwrite the device with your draft, or discard your draft and reload.")
                    }
                    return@mutate
                }
            }
            val saved = binding.parse(backend.send(id, "PUT", part.path, body, 60_000))
            if (current()) updateSection(part) { Section(server = saved, draft = binding.draft(saved)) }
            if (current()) success?.let(notify)
            after(saved)
        }
    }

    // ---------- Actions ----------

    fun wake(minutes: Int) {
        val body = try { SettingsRules.wakePayload(minutes) } catch (error: IllegalArgumentException) {
            notify(error.message.orEmpty()); return
        }
        mutate(if (minutes == 0) "Cancelling temporary wake" else "Waking the display", SettingsPart.SLEEP) { id, current ->
            val saved = SettingsRules.parseSleep(backend.send(id, "POST", "/api/sleep/wake", body, 30_000))
            if (!current()) return@mutate
            ownWrite(SettingsPart.SLEEP, saved) { old, new -> (new as SleepConfig).copy(schedule = (old as SleepConfig).schedule) }
            notify(if (minutes == 0) "Temporary wake cancelled; schedule resumed." else "Temporary wake started; schedule unchanged.")
        }
    }

    fun musicControl(action: String) {
        val body = try { SettingsRules.musicControlPayload(action) } catch (error: IllegalArgumentException) {
            notify(error.message.orEmpty()); return
        }
        mutate("Sending $action to the music player", SettingsPart.MUSIC) { id, current ->
            val saved = SettingsRules.parseMusic(backend.send(id, "POST", "/api/plex/music/control", body, 60_000))
            if (current()) ownWrite(SettingsPart.MUSIC, saved, ::mergeMusicStatus)
        }
    }

    fun likeMusic() {
        val status = mutable.value?.music?.server?.status ?: return
        val body = try { SettingsRules.likePayload(status) } catch (error: IllegalArgumentException) {
            notify(error.message.orEmpty()); return
        }
        mutate("Liking the current song", SettingsPart.MUSIC) { id, current ->
            val saved = SettingsRules.parseMusic(backend.send(id, "POST", "/api/plex/music/like", body, 30_000))
            if (current()) {
                ownWrite(SettingsPart.MUSIC, saved, ::mergeMusicStatus)
                notify("Rated the current song 5 stars in Plex.")
            }
        }
    }

    private fun mergeMusicStatus(old: Any?, new: Any?): MusicSettings {
        val baseline = old as MusicSettings
        val latest = new as MusicSettings
        return baseline.copy(status = latest.status, activeDay = latest.activeDay, activeStationKey = latest.activeStationKey)
    }

    /** Refreshes the music status; a dirty music draft and its baseline are left untouched. */
    fun refreshMusic() = load(setOf(SettingsPart.MUSIC), discard = false)

    fun refreshBluetooth() = load(setOf(SettingsPart.BLUETOOTH), discard = false)

    fun setBluetoothEnabled(enabled: Boolean) = mutate(
        if (enabled) "Enabling Bluetooth speaker" else "Disabling Bluetooth speaker", SettingsPart.BLUETOOTH,
    ) { id, current ->
        val saved = SettingsRules.parseBluetooth(backend.send(
            id, "PUT", SettingsPart.BLUETOOTH.path, SettingsRules.bluetoothEnabledPayload(enabled), 60_000,
        ))
        if (current()) ownWrite(SettingsPart.BLUETOOTH, saved)
    }

    fun saveBluetoothDelay(value: String) {
        val body = try { SettingsRules.bluetoothDelayPayload(value) } catch (error: IllegalArgumentException) {
            setError(SettingsPart.BLUETOOTH, error.message)
            notify(error.message.orEmpty())
            return
        }
        mutate("Saving speaker delay", SettingsPart.BLUETOOTH) { id, current ->
            val saved = SettingsRules.parseBluetooth(backend.send(id, "PUT", SettingsPart.BLUETOOTH.path, body, 60_000))
            if (current()) ownWrite(SettingsPart.BLUETOOTH, saved)
        }
    }

    fun scanBluetooth() = mutate("Searching for Bluetooth speakers", SettingsPart.BLUETOOTH) { id, current ->
        val devices = SettingsRules.parseBluetoothScan(backend.send(
            id, "POST", "/api/bluetooth/scan", JSONObject(), 60_000,
        ))
        if (current()) mutable.update { state ->
            state?.copy(
                bluetoothScan = devices,
                bluetoothSelectedAddress = (devices.firstOrNull { it.audio } ?: devices.firstOrNull())?.address.orEmpty(),
            )
        }
        if (current()) notify("${devices.size} Bluetooth devices found.")
    }

    fun selectBluetooth(address: String) = mutable.update { it?.copy(bluetoothSelectedAddress = address) }

    fun pairBluetooth() {
        val state = mutable.value ?: return
        val device = state.bluetoothScan?.firstOrNull { it.address == state.bluetoothSelectedAddress } ?: return
        val body = try { SettingsRules.bluetoothPairPayload(device.address, device.name) } catch (error: IllegalArgumentException) {
            setError(SettingsPart.BLUETOOTH, error.message)
            notify(error.message.orEmpty())
            return
        }
        mutate("Pairing Bluetooth speaker", SettingsPart.BLUETOOTH) { id, current ->
            val saved = SettingsRules.parseBluetooth(backend.send(id, "POST", "/api/bluetooth/pair", body, 120_000))
            if (current()) {
                ownWrite(SettingsPart.BLUETOOTH, saved)
                mutable.update { it?.copy(bluetoothScan = null, bluetoothSelectedAddress = "") }
                notify("Bluetooth speaker paired.")
            }
        }
    }

    fun forgetBluetooth() = mutate("Forgetting Bluetooth speaker", SettingsPart.BLUETOOTH) { id, current ->
        val saved = SettingsRules.parseBluetooth(backend.send(id, "POST", "/api/bluetooth/forget", JSONObject(), 60_000))
        if (current()) {
            ownWrite(SettingsPart.BLUETOOTH, saved)
            mutable.update { it?.copy(bluetoothScan = null, bluetoothSelectedAddress = "") }
            notify("Bluetooth speaker forgotten.")
        }
    }

    fun setHostname() {
        val draft = mutable.value?.status?.draft ?: return
        val body = try { SettingsRules.hostnamePayload(draft) } catch (error: IllegalArgumentException) {
            setError(SettingsPart.STATUS, error.message); notify(error.message.orEmpty()); return
        }
        mutate("Changing the hostname", SettingsPart.STATUS) { id, current ->
            val result = backend.send(id, "POST", "/api/hostname", body, 60_000)
            val hostname = result.optString("hostname").ifBlank { body.getString("hostname") }
            if (!current()) return@mutate
            updateSection(SettingsPart.STATUS) { section ->
                val info = (section.server as DeviceInfo?)?.copy(hostname = hostname) ?: DeviceInfo(hostname, emptyList())
                Section(server = info, draft = hostname)
            }
            notify(if (result.optBoolean("changed", true)) "Hostname changed to $hostname." else "Hostname is already $hostname.")
        }
    }

    fun setCountry() {
        val draft = mutable.value?.network?.draft ?: return
        val body = try { SettingsRules.countryPayload(draft.country) } catch (error: IllegalArgumentException) {
            setError(SettingsPart.NETWORK, error.message); notify(error.message.orEmpty()); return
        }
        mutate("Setting the Wi-Fi country", SettingsPart.NETWORK) { id, current ->
            backend.send(id, "POST", "/api/wifi/country", body, 60_000)
            if (current()) {
                notify("Wi-Fi country set.")
                load(setOf(SettingsPart.NETWORK), discard = true)
            }
        }
    }

    fun scanWifi() = mutate("Scanning for Wi-Fi networks", SettingsPart.NETWORK) { id, current ->
        val networks = SettingsRules.parseScan(backend.send(id, "POST", "/api/wifi/scan", JSONObject(), 90_000))
        if (!current()) return@mutate
        mutable.update { state ->
            state?.copy(scan = networks, network = state.network.copy(draft = state.network.draft?.copy(
                ssid = state.network.draft.ssid.takeIf { s -> networks.any { it.ssid == s } } ?: networks.firstOrNull()?.ssid.orEmpty(),
            )))
        }
        notify("${networks.size} networks found.")
    }

    fun connectWifi() {
        val draft = mutable.value?.network?.draft ?: return
        // The password is discarded from the draft before any request is made.
        editWifi { it.copy(password = "") }
        val body = try { SettingsRules.connectPayload(draft.ssid, draft.password) } catch (error: IllegalArgumentException) {
            setError(SettingsPart.NETWORK, error.message); notify(error.message.orEmpty()); return
        }
        mutate("Connecting to ${draft.ssid}", SettingsPart.NETWORK) { id, current ->
            backend.send(id, "POST", "/api/wifi/connect", body, 120_000)
            if (current()) {
                notify("Connected to ${draft.ssid}.")
                load(setOf(SettingsPart.NETWORK), discard = false)
            }
        }
    }

    fun forgetWifi() = mutate("Forgetting the saved network", SettingsPart.NETWORK) { id, current ->
        backend.send(id, "POST", "/api/wifi/forget", JSONObject(), 60_000)
        if (current()) {
            notify("Saved network removed.")
            load(setOf(SettingsPart.NETWORK), discard = false)
        }
    }

    fun checkUpdate() = mutate("Checking for updates", SettingsPart.UPDATES) { id, current ->
        val check = SettingsRules.parseUpdateCheck(backend.send(id, "POST", "/api/update/check", JSONObject(), 60_000))
        if (!current()) return@mutate
        mutable.update { it?.copy(updateCheck = check) }
        notify(if (check.available) "Version ${check.latestVersion} is available." else "This device is up to date.")
    }

    fun installUpdate() = mutate("Installing the update", SettingsPart.UPDATES) { id, current ->
        val version = backend.installUpdate(id)
        notify("$version installed; restart the device to apply it.")
        if (current()) {
            mutable.update { it?.copy(updateCheck = null) }
            load(setOf(SettingsPart.UPDATES), discard = true)
        }
    }

    fun reboot() = mutate("Restarting the device", null) { id, _ ->
        backend.send(id, "POST", "/api/reboot", JSONObject(), 30_000)
        notify("Device is restarting.")
    }

    fun checkPlayback() {
        val state = mutable.value ?: return
        val current = generation
        val requestScope = requests
        track(loads, scope.launch {
            try {
                val result = backend.fetch(state.identity.id, listOf("/api/playlist/status"), requestScope)
                    .getValue("/api/playlist/status").getOrThrow()
                val status = SettingsRules.parsePlaybackStatus(result)
                if (current == generation) mutable.update { it?.copy(playback = status) }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                if (current == generation) notify(error.message ?: "Could not read carousel playback status")
            }
        })
    }

    // ---------- Plex picker and Plex music lookups ----------

    fun openPicker(sceneId: String) {
        mutable.update { it?.copy(picker = PlexPicker(sceneId, listOf("Libraries" to null))) }
        browsePicker(null) { picker, items -> picker.copy(items = items) }
    }

    fun closePicker() = mutable.update { it?.copy(picker = null) }

    fun pickerOpen(item: PlexBrowseItem) {
        val picker = mutable.value?.picker ?: return
        if (picker.loading) return
        if (!item.container) {
            val id = item.id ?: return
            editScene(picker.sceneId) { SettingsRules.withSource(it, "plex").copy(plexRatingKey = id, title = item.title.take(80)) }
            closePicker()
            return
        }
        val key = item.key ?: item.id ?: return
        browsePicker(key) { current, items -> current.copy(items = items, path = current.path + (item.title to key)) }
    }

    fun pickerBack() {
        val picker = mutable.value?.picker ?: return
        if (picker.loading || picker.path.size <= 1) return
        val previous = picker.path.dropLast(1)
        browsePicker(previous.last().second) { current, items -> current.copy(items = items, path = previous) }
    }

    fun retryPicker() {
        val picker = mutable.value?.picker ?: return
        browsePicker(picker.path.last().second) { current, items -> current.copy(items = items) }
    }

    private fun browsePicker(key: String?, apply: (PlexPicker, List<PlexBrowseItem>) -> PlexPicker) {
        val sceneId = mutable.value?.picker?.sceneId ?: return
        mutable.update { it?.copy(picker = it.picker?.copy(loading = true, error = null)) }
        val path = "/api/plex/browse" + (key?.let { "?key=" + encode(it) } ?: "")
        lookup(path, SettingsRules::parsePlexBrowse) { result ->
            mutable.update { state ->
                val picker = state?.picker?.takeIf { it.sceneId == sceneId } ?: return@update state
                state.copy(picker = result.fold(
                    { apply(picker, it).copy(loading = false, error = null) },
                    { picker.copy(loading = false, error = it.message ?: "Could not browse Plex") },
                ))
            }
        }
    }

    /** Read-only lookup whose result is delivered only while this editor remains open. */
    fun <T> lookup(path: String, parse: (JSONObject) -> T, done: (Result<T>) -> Unit) {
        val state = mutable.value ?: return
        val current = generation
        val requestScope = requests
        track(loads, scope.launch {
            val result = try {
                Result.success(parse(backend.fetch(state.identity.id, listOf(path), requestScope).getValue(path).getOrThrow()))
            } catch (error: CancellationException) {
                if (current == generation) done(Result.failure(DeviceRequestException(INTERRUPTED)))
                throw error
            } catch (error: Exception) {
                Result.failure(error)
            }
            if (current == generation) done(result)
        })
    }

    /** Explicit Plex write (smart playlist preview/creation); serialized with other writes. */
    fun <T> plexAction(label: String, path: String, body: JSONObject, parse: (JSONObject) -> T, done: (T) -> Unit) =
        mutate(label, SettingsPart.MUSIC) { id, current ->
            val result = parse(backend.send(id, "POST", path, body, 60_000))
            if (current()) done(result)
        }

    fun addStation(station: MusicStation) = mutable.update { state ->
        val list = state?.stations?.server ?: return@update state
        state.copy(stations = state.stations.copy(server = list.filterNot { it.key == station.key } + station))
    }

    // ---------- Internals ----------

    private fun load(parts: Set<SettingsPart>, discard: Boolean) {
        val state = mutable.value ?: return
        val id = state.identity.id
        val current = generation
        val requestScope = requests
        val ordered = parts.sortedBy { it.ordinal }
        ordered.forEach { part -> updateSection(part) { it.copy(loading = true, error = if (it.server == null) null else it.error) } }
        track(loads, scope.launch {
            try {
                val results = backend.fetch(id, ordered.map { it.path }, requestScope)
                if (current != generation) return@launch
                ordered.forEach { part ->
                    val result = results[part.path] ?: Result.failure(DeviceRequestException("No response"))
                    applyLoad(part, result, discard)
                }
            } catch (error: CancellationException) {
                if (current == generation) ordered.forEach { part ->
                    updateSection(part) { it.copy(loading = false, error = if (it.server == null) INTERRUPTED else it.error) }
                }
                throw error
            } catch (error: Exception) {
                if (current == generation) ordered.forEach { part ->
                    updateSection(part) {
                        it.copy(loading = false, error = if (it.server == null) error.message ?: "Could not load settings" else it.error)
                    }
                }
                if (current == generation) notify(error.message ?: "Could not load device settings")
            }
        })
    }

    private fun applyLoad(part: SettingsPart, result: Result<JSONObject>, discard: Boolean) {
        val binding = binding(part)
        val parsed = result.mapCatching { binding.parse(it) }
        mutable.update { state ->
            state ?: return@update null
            val section = binding.get(state)
            val next: Section<Any?, Any?> = parsed.fold(
                { server ->
                    if (!discard && binding.dirty(state)) {
                        // Keep the user's draft and the baseline it was based on, so a later save can detect conflicts.
                        val merged = if (part == SettingsPart.MUSIC) {
                            mergeMusicStatus(section.server, server)
                        } else section.server
                        section.copy(server = merged, loading = false)
                    } else Section(server = server, draft = binding.draft(server))
                },
                { error ->
                    val status = (error as? DeviceRequestException)?.status
                    val unsupported = status == 404 || status == 405
                    val bluetoothOldFirmware = part == SettingsPart.BLUETOOTH && status == 404
                    val plexOff = part == SettingsPart.STATIONS && state.plex.server?.configured == false
                    when {
                        plexOff -> Section<Any?, Any?>(server = emptyList<MusicStation>())
                        bluetoothOldFirmware -> Section(loading = false, unsupported = true,
                            error = "Bluetooth audio is unavailable on this firmware. Update the device to use a speaker.")
                        section.server != null && !discard -> section.copy(loading = false, error = error.message)
                        else -> Section(loading = false, unsupported = unsupported,
                            error = if (unsupported) UNSUPPORTED else error.message ?: "Could not load ${part.label}")
                    }
                },
            )
            binding.set(state, next)
        }
    }

    private fun mutate(
        label: String,
        part: SettingsPart?,
        work: suspend (id: String, current: () -> Boolean) -> Unit,
    ) {
        val state = mutable.value ?: return
        if (!writeLock.tryLock()) {
            notify("Another change is still being sent to the device. Wait for it to finish.")
            return
        }
        val id = state.identity.id
        val started = generation
        val current = { started == generation }
        mutable.update { it?.copy(working = label) }
        part?.let { p -> updateSection(p) { it.copy(error = null, conflict = false) } }
        val job = scope.launch {
            try {
                work(id, current)
            } catch (error: CancellationException) {
                notify("$label was interrupted. The device may or may not have applied it; reload to check.")
                throw error
            } catch (error: Exception) {
                val message = error.message ?: "$label failed"
                if (current() && part != null) setError(part, message)
                notify(message)
            } finally {
                writeLock.unlock()
                if (current()) mutable.update { it?.copy(working = null) }
            }
        }
        track(mutations, job)
    }

    private fun ownWrite(part: SettingsPart, saved: Any?, keepBaseline: ((Any?, Any?) -> Any?)? = null) {
        val binding = binding(part)
        mutable.update { state ->
            state ?: return@update null
            val section = binding.get(state)
            val dirty = binding.dirty(state)
            val server = if (dirty && keepBaseline != null && section.server != null) keepBaseline(section.server, saved) else saved
            binding.set(state, section.copy(server = server, draft = if (dirty) section.draft else binding.draft(saved), error = null))
        }
    }

    private fun setError(part: SettingsPart, message: String?) = updateSection(part) { it.copy(error = message) }

    private fun updateSection(part: SettingsPart, change: (Section<Any?, Any?>) -> Section<Any?, Any?>) {
        val binding = binding(part)
        mutable.update { state -> state?.let { binding.set(it, change(binding.get(it))) } }
    }

    private fun track(set: MutableSet<Job>, job: Job) {
        set += job
        job.invokeOnCompletion { set -= job }
    }

    private fun encode(value: String) = URLEncoder.encode(value, "UTF-8")
}
