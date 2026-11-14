package io.github.rbotha78.webdisplay.manager

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class DisplayConfig(val url: String, val autoAp: Boolean, val safeAreaPercent: Int, val audioOutput: String)
data class DisplayDraft(val url: String, val autoAp: Boolean, val safeArea: String, val audioOutput: String)
data class AudioOutput(val id: String, val label: String)
data class BluetoothStatus(
    val supported: Boolean,
    val adapter: Boolean?,
    val address: String,
    val name: String,
    val enabled: Boolean,
    val delayMs: Int,
    val connected: Boolean,
    val active: Boolean,
)
data class BluetoothDevice(val address: String, val name: String, val paired: Boolean, val connected: Boolean, val audio: Boolean)

data class SleepWindow(val enabled: Boolean, val start: String, val end: String)
data class SleepSchedule(val days: Map<String, SleepWindow>)
data class SleepConfig(
    val schedule: SleepSchedule,
    val sleeping: Boolean,
    val scheduledSleep: Boolean,
    val wakeUntil: Double?,
    val deviceTime: String,
    val timezone: String,
)

data class PlexSettings(val serverUrl: String, val configured: Boolean, val defaultBitrate: Int, val serverName: String = "")
/** [token] is write-only: blank keeps the device's saved token. */
data class PlexDraft(val serverUrl: String, val defaultBitrate: String, val token: String = "") {
    override fun toString() = "PlexDraft(serverUrl=$serverUrl, defaultBitrate=$defaultBitrate, token=${redact(token)})"
}

data class MusicStatus(
    val state: String,
    val title: String,
    val error: String,
    val pausedByUser: Boolean,
    val ratingKey: String,
    val liked: Boolean,
    val artist: String = "",
)
data class MusicSettings(
    val enabled: Boolean, val stationKey: String, val volume: Int, val status: MusicStatus,
    val daySources: Map<String, String> = SettingsRules.DAYS.associateWith { "" },
    val supportsDaySources: Boolean = false,
    val activeDay: String = "", val activeStationKey: String = "",
)
data class MusicDraft(
    val enabled: Boolean, val stationKey: String, val volume: Int,
    val daySources: Map<String, String> = SettingsRules.DAYS.associateWith { "" },
    val supportsDaySources: Boolean = false,
)
data class MusicStation(val key: String, val title: String, val library: String, val kind: String, val smart: Boolean)
data class MusicBrowseItem(val id: String, val title: String, val type: String, val artist: String)
data class MusicBrowseResult(val items: List<MusicBrowseItem>, val total: Int, val offset: Int)
data class FilterChoice(val id: String, val title: String)
data class MusicFilters(val decade: List<FilterChoice>, val genre: List<FilterChoice>, val mood: List<FilterChoice>)

data class RailSettings(val boardConfigured: Boolean, val detailsConfigured: Boolean, val refreshSeconds: Int)
data class RailDraft(val boardKey: String, val detailsKey: String, val refreshSeconds: String) {
    override fun toString() =
        "RailDraft(boardKey=${redact(boardKey)}, detailsKey=${redact(detailsKey)}, refreshSeconds=$refreshSeconds)"
}

data class WeatherSettings(val city: String, val country: String, val configured: Boolean)
data class WeatherDraft(val city: String, val country: String, val apiKey: String = "") {
    override fun toString() = "WeatherDraft(city=$city, country=$country, apiKey=${redact(apiKey)})"
}

data class DeviceInfo(val hostname: String, val addresses: List<String>)

data class WifiInterface(val device: String, val type: String, val state: String, val connection: String)
data class NetworkInfo(
    val internetOnline: Boolean?,
    val defaultRoute: String,
    val country: String,
    val savedNetwork: Boolean,
    val wifiBlocked: Boolean,
    val error: String,
    val devices: List<WifiInterface>,
    val savedSsid: String = "",
)
data class WifiNetwork(val ssid: String, val signal: Int, val secured: Boolean)
/** [password] is write-only and is cleared after every connect attempt. */
data class WifiDraft(val country: String, val ssid: String = "", val password: String = "") {
    override fun toString() = "WifiDraft(country=$country, ssid=$ssid, password=${redact(password)})"
}

data class UpdateInfo(
    val runningVersion: String,
    val installedVersion: String,
    val baseVersion: String,
    val previousVersion: String?,
    val pendingVersion: String?,
    val failed: List<String>,
)
data class UpdateCheck(val available: Boolean, val latestVersion: String?, val size: Long?)

data class PlexBrowseItem(val id: String?, val key: String?, val title: String, val type: String, val container: Boolean)

data class PlaybackFailure(val message: String, val time: Double)
data class PlaybackStatus(val failures: Map<String, PlaybackFailure>, val updated: Double?)

data class Scene(
    val id: String,
    val kind: String,
    val title: String = "",
    val enabled: Boolean = true,
    /** Blank means "no cutoff"; only video scenes may use it. */
    val duration: String = "60",
    val audioEnabled: Boolean = false,
    val plexBitrate: String = "",
    val url: String = "",
    val source: String = "url",
    val plexRatingKey: String = "",
    val layout: String = "clock-notice",
    val text: String = "",
    val stationCrs: String = "",
    val destinationCrs: String = "",
    val filterStationCrs: String = "",
    val showPreviousDeparture: Boolean = true,
    val showWeather: Boolean = false,
    /** Unknown fields from newer firmware are preserved verbatim on save. */
    val extras: String = "{}",
)
data class Playlist(val enabled: Boolean, val scenes: List<Scene>)

private fun redact(value: String) = if (value.isEmpty()) "<blank>" else "<redacted>"

object SettingsRules {
    val DAYS = listOf("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    val WAKE_MINUTES = listOf(15, 30, 60, 120, 240)
    val SCENE_KINDS = listOf("web", "dashboard", "video", "train", "weather", "music")
    val LAYOUTS = listOf("clock", "notice", "clock-notice")
    const val MAX_SCENES = 30
    const val MAX_MIX_ITEMS = 20
    private val KNOWN_SCENE_FIELDS = setOf(
        "id", "kind", "title", "enabled", "duration", "audio_enabled", "plex_bitrate_kbps", "url", "source",
        "plex_rating_key", "layout", "text", "station_crs", "destination_crs", "filter_station_crs",
        "show_previous_departure", "show_weather", "plex_playback_url", "plex_stop_url",
    )
    private val TIME = Regex("(?:[01][0-9]|2[0-3]):[0-5][0-9]")
    private val SCENE_ID = Regex("[A-Za-z0-9_-]{1,64}")
    private val CRS = Regex("[A-Za-z]{3}")
    private val HOSTNAME = Regex("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
    private val STATION_KEY = Regex("/library/sections/[0-9]{1,10}/stations/(?:1|2|3|8)")
    private val PLAYLIST_KEY = Regex("/playlists/[0-9]{1,20}/items")
    private val MIX_KEY = Regex("mix:(artist|album):([0-9]{1,20}(?:,[0-9]{1,20}){0,19})")
    private val ADVERTISED_MIX_KEY = Regex("/(?:library|hubs)/[A-Za-z0-9_./?=&%:-]{1,512}")

    // ---------- Parsing: invalid responses throw instead of producing empty drafts ----------

    fun parseDisplay(json: JSONObject): DisplayConfig = parsing("display settings") {
        DisplayConfig(json.str("url"), json.bool("auto_ap"), json.int("safe_area_percent"), json.str("audio_output"))
    }

    fun parseAudioOutputs(json: JSONObject): List<AudioOutput> = parsing("audio output") {
        json.objects("items").map { AudioOutput(it.str("id"), it.str("label")) }
    }

    fun parseBluetooth(json: JSONObject): BluetoothStatus = parsing("Bluetooth speaker") {
        val adapter = json.opt("adapter")
        require(adapter == null || adapter is Boolean || adapter === JSONObject.NULL)
        BluetoothStatus(
            json.bool("supported"), (adapter as? Boolean), json.str("address"), json.str("name"),
            json.bool("enabled"), json.int("delay_ms").also { require(it in -1000..1000) },
            json.bool("connected"), json.bool("active"),
        )
    }

    fun parseBluetoothScan(json: JSONObject): List<BluetoothDevice> = parsing("Bluetooth scan") {
        json.objects("items").map {
            BluetoothDevice(it.str("address"), it.str("name"), it.bool("paired"), it.bool("connected"), it.bool("audio"))
        }
    }

    fun bluetoothEnabledPayload(enabled: Boolean) = JSONObject().put("enabled", enabled)

    fun bluetoothDelayPayload(value: String): JSONObject {
        val delay = value.trim().toIntOrNull()
        require(delay != null && delay in -1000..1000) { "Speaker delay must be between -1000 and 1000 ms" }
        return JSONObject().put("delay_ms", delay)
    }

    fun bluetoothPairPayload(address: String, name: String): JSONObject {
        require(address.isNotBlank()) { "Select a Bluetooth speaker to pair" }
        return JSONObject().put("address", address).put("name", name)
    }

    fun parseSleep(json: JSONObject): SleepConfig = parsing("sleep schedule") {
        val days = json.getJSONObject("days")
        SleepConfig(
            SleepSchedule(DAYS.associateWith { day ->
                val item = days.getJSONObject(day)
                SleepWindow(item.bool("enabled"), item.str("start"), item.str("end"))
            }),
            json.bool("sleeping"), json.bool("scheduled_sleep"),
            if (json.isNull("wake_until")) null else json.num("wake_until"),
            json.optString("device_time"), json.optString("timezone"),
        )
    }

    fun parsePlex(json: JSONObject): PlexSettings = parsing("Plex settings") {
        PlexSettings(json.str("server_url"), json.bool("configured"), json.int("default_bitrate_kbps"),
            json.optString("server_name"))
    }

    fun parseMusic(json: JSONObject): MusicSettings = parsing("music settings") {
        val status = json.optJSONObject("status") ?: JSONObject()
        val supportsDaySources = json.has("day_sources")
        val days = if (supportsDaySources) json.getJSONObject("day_sources") else null
        if (days != null) require(days.keys().asSequence().toSet() == DAYS.toSet())
        MusicSettings(
            json.bool("enabled"), json.str("station_key"), json.int("volume"),
            MusicStatus(
                status.optString("state", "unknown"), status.optString("title"), status.optString("error"),
                status.optBoolean("paused_by_user"), status.optString("rating_key"), status.optBoolean("liked"), status.optString("artist"),
            ),
            daySources = DAYS.associateWith { day -> days?.str(day) ?: "" },
            supportsDaySources = supportsDaySources,
            activeDay = json.optString("active_day"), activeStationKey = json.optString("active_station_key"),
        )
    }

    fun parseStations(json: JSONObject): List<MusicStation> = parsing("music source") {
        json.objects("items").map {
            MusicStation(it.str("key"), it.str("title"), it.optString("library"),
                it.optString("kind", "station"), it.optBoolean("smart"))
        }
    }

    fun parseMusicBrowse(json: JSONObject): MusicBrowseResult = parsing("music library") {
        MusicBrowseResult(
            json.objects("items").map { MusicBrowseItem(it.str("id"), it.str("title"), it.optString("type"), it.optString("artist")) },
            json.optInt("total"), json.optInt("offset"),
        )
    }

    fun parseMusicFilters(json: JSONObject): MusicFilters = parsing("music filter") {
        fun list(name: String) = json.objects(name).map { FilterChoice(it.str("id"), it.str("title")) }
        MusicFilters(list("decade"), list("genre"), list("mood"))
    }

    fun parseStation(json: JSONObject): MusicStation = parsing("smart playlist") {
        MusicStation(json.str("key"), json.str("title"), json.optString("library"), json.optString("kind", "playlist"),
            json.optBoolean("smart"))
    }

    fun parseRail(json: JSONObject): RailSettings = parsing("National Rail settings") {
        RailSettings(json.bool("board_configured"), json.bool("details_configured"), json.int("refresh_seconds"))
    }

    fun parseWeather(json: JSONObject): WeatherSettings = parsing("weather settings") {
        WeatherSettings(json.str("city"), json.str("country"), json.bool("configured"))
    }

    fun parseDeviceInfo(json: JSONObject): DeviceInfo = parsing("device status") {
        val addresses = json.optJSONArray("addresses") ?: JSONArray()
        DeviceInfo(json.str("hostname"), (0 until addresses.length()).mapNotNull { addresses.opt(it) as? String })
    }

    fun parseNetwork(json: JSONObject): NetworkInfo = parsing("network status") {
        val wifi = json.getJSONObject("wifi")
        NetworkInfo(
            json.optJSONObject("internet")?.opt("online") as? Boolean,
            json.optString("default_route"),
            wifi.optString("country"),
            wifi.optBoolean("saved_network"),
            wifi.optBoolean("wifi_blocked"),
            wifi.optString("error"),
            (wifi.optJSONArray("devices")?.let { array -> (0 until array.length()).mapNotNull { array.optJSONObject(it) } }
                ?: emptyList()).map {
                WifiInterface(it.optString("device"), it.optString("type"), it.optString("state"), it.optString("connection"))
            },
            wifi.optString("saved_ssid"),
        )
    }

    fun parseScan(json: JSONObject): List<WifiNetwork> = parsing("Wi-Fi scan") {
        json.objects("networks").map { WifiNetwork(it.str("ssid"), it.int("signal"), it.bool("secured")) }
    }

    fun parseUpdate(json: JSONObject): UpdateInfo = parsing("update status") {
        val failed = json.optJSONArray("failed") ?: JSONArray()
        UpdateInfo(
            json.str("running_version"), json.str("installed_version"), json.optString("base_version"),
            json.optStringOrNull("previous_version"),
            json.optJSONObject("pending")?.optStringOrNull("version"),
            (0 until failed.length()).mapNotNull { failed.opt(it) as? String },
        )
    }

    fun parseUpdateCheck(json: JSONObject): UpdateCheck = parsing("update check") {
        val latest = json.optJSONObject("latest")
        UpdateCheck(json.bool("available"), latest?.str("version"), latest?.optLong("size"))
    }

    fun parsePlexBrowse(json: JSONObject): List<PlexBrowseItem> = parsing("Plex library") {
        json.objects("items").map {
            PlexBrowseItem(it.optStringOrNull("id"), it.optStringOrNull("key"), it.str("title"), it.optString("type"),
                it.bool("container"))
        }
    }

    fun parsePlaybackStatus(json: JSONObject): PlaybackStatus = parsing("carousel status") {
        val failures = json.optJSONObject("failures") ?: JSONObject()
        PlaybackStatus(
            failures.keys().asSequence().mapNotNull { key ->
                failures.optJSONObject(key)?.let { key to PlaybackFailure(it.optString("message"), it.optDouble("time", 0.0)) }
            }.toMap(),
            if (json.isNull("updated")) null else json.optDouble("updated"),
        )
    }

    fun parsePlaylist(json: JSONObject): Playlist = parsing("carousel") {
        Playlist(json.bool("enabled"), json.objects("scenes").map(::parseScene))
    }

    private fun parseScene(item: JSONObject): Scene {
        val kind = item.str("kind")
        val extras = JSONObject()
        item.keys().forEach { key -> if (key !in KNOWN_SCENE_FIELDS) extras.put(key, item.get(key)) }
        val duration = if (item.has("duration") && item.isNull("duration")) "" else
            item.optIntStrict("duration")?.toString() ?: if (kind == "video" || kind == "train") "300" else "60"
        return Scene(
            id = item.str("id"), kind = kind, title = item.optString("title"),
            enabled = item.opt("enabled") as? Boolean ?: true, duration = duration,
            audioEnabled = item.optBoolean("audio_enabled"),
            plexBitrate = if (item.isNull("plex_bitrate_kbps")) "" else item.optIntStrict("plex_bitrate_kbps")?.toString().orEmpty(),
            url = item.optString("url"), source = item.optString("source", "url").ifBlank { "url" },
            plexRatingKey = item.optString("plex_rating_key"),
            layout = item.optString("layout", "clock-notice").ifBlank { "clock-notice" }, text = item.optString("text"),
            stationCrs = item.optString("station_crs"), destinationCrs = item.optString("destination_crs"),
            filterStationCrs = item.optString("filter_station_crs"),
            showPreviousDeparture = item.opt("show_previous_departure") as? Boolean ?: true,
            showWeather = item.optBoolean("show_weather"),
            extras = extras.toString(),
        )
    }

    // ---------- Drafts ----------

    fun displayDraft(config: DisplayConfig) =
        DisplayDraft(config.url, config.autoAp, config.safeAreaPercent.toString(), config.audioOutput)
    fun plexDraft(settings: PlexSettings) = PlexDraft(settings.serverUrl, settings.defaultBitrate.toString())
    fun musicDraft(settings: MusicSettings) = MusicDraft(
        settings.enabled, settings.stationKey, settings.volume, settings.daySources, settings.supportsDaySources,
    )
    fun railDraft(settings: RailSettings) = RailDraft("", "", settings.refreshSeconds.toString())
    fun weatherDraft(settings: WeatherSettings) = WeatherDraft(settings.city, settings.country)

    fun newScene(kind: String, id: String = UUID.randomUUID().toString()): Scene {
        require(kind in SCENE_KINDS) { "Unknown scene type" }
        return Scene(id = id, kind = kind, duration = if (kind == "video" || kind == "train") "300" else "60")
    }

    /** Changing a video's source clears selections that belong to the other source. */
    fun withSource(scene: Scene, source: String): Scene =
        if (source == scene.source) scene else scene.copy(
            source = source, plexRatingKey = "", url = if (source == "plex") "" else scene.url,
            plexBitrate = if (source == "plex") scene.plexBitrate else "",
        )

    fun move(playlist: Playlist, index: Int, offset: Int): Playlist {
        val target = index + offset
        if (index !in playlist.scenes.indices || target !in playlist.scenes.indices) return playlist
        val scenes = playlist.scenes.toMutableList()
        scenes[index] = scenes[target].also { scenes[target] = scenes[index] }
        return playlist.copy(scenes = scenes)
    }

    // ---------- Validation and payloads mirroring the device validators ----------

    fun displayPayload(draft: DisplayDraft, server: DisplayConfig): JSONObject {
        val safeArea = draft.safeArea.trim().toIntOrNull()
        require(safeArea != null && safeArea in 0..10) { "Overscan safe area must be a whole percentage from 0 to 10" }
        val url = draft.url.trim()
        if (url.isNotEmpty()) validateUrl(url)
        val body = JSONObject()
        if (url != server.url) body.put("url", url)
        if (draft.autoAp != server.autoAp) body.put("auto_ap", draft.autoAp)
        if (safeArea != server.safeAreaPercent) body.put("safe_area_percent", safeArea)
        if (draft.audioOutput != server.audioOutput) body.put("audio_output", draft.audioOutput)
        require(body.length() > 0) { "No display settings changed" }
        return body
    }

    fun sleepPayload(schedule: SleepSchedule): JSONObject {
        val days = JSONObject()
        DAYS.forEach { day ->
            val window = schedule.days[day] ?: throw IllegalArgumentException("Sleep schedule must contain all seven days")
            require(TIME.matches(window.start) && TIME.matches(window.end)) { "Sleep times must use HH:MM (00:00–23:59)" }
            require(!window.enabled || window.start != window.end) { "Sleep start and end must differ for enabled $day" }
            days.put(day, JSONObject().put("enabled", window.enabled).put("start", window.start).put("end", window.end))
        }
        return JSONObject().put("days", days)
    }

    fun wakePayload(minutes: Int): JSONObject {
        require(minutes == 0 || minutes in WAKE_MINUTES) { "Wake duration must be 0, 15, 30, 60, 120 or 240 minutes" }
        return JSONObject().put("duration_minutes", minutes)
    }

    fun plexPayload(draft: PlexDraft, server: PlexSettings): JSONObject {
        val url = draft.serverUrl.trim().trimEnd('/')
        require(url.isNotEmpty()) { "Plex server URL must be an HTTP(S) server address" }
        val parsed = validateUrl(url, "Plex server URL must be an HTTP(S) server address")
        require(parsed.rest.isEmpty()) {
            "Plex server URL must not include a path, query, or fragment"
        }
        val bitrate = bitrate(draft.defaultBitrate)
        val body = JSONObject().put("server_url", url).put("default_bitrate_kbps", bitrate)
        if (draft.token.isNotEmpty()) {
            require(draft.token.length <= 512 && printable(draft.token)) { "Plex token must contain 1-512 printable characters" }
            body.put("token", draft.token)
        } else require(server.configured) { "Enter a Plex token to connect this server" }
        return body
    }

    fun plexClearPayload(): JSONObject = JSONObject().put("server_url", "").put("clear_token", true)

    fun musicPayload(draft: MusicDraft): JSONObject {
        require(draft.volume in 0..100) { "Music volume must be an integer from 0 to 100" }
        require(draft.stationKey.isEmpty() || validMusicSource(draft.stationKey)) {
            "Select a Plex station, audio playlist, or artist/album mix"
        }
        require(!draft.enabled || draft.stationKey.isNotEmpty()) { "Select a music source before enabling playback" }
        val body = JSONObject().put("enabled", draft.enabled).put("station_key", draft.stationKey).put("volume", draft.volume)
        if (draft.supportsDaySources) {
            require(draft.daySources.keys == DAYS.toSet()) { "Music schedule must contain exactly seven weekdays" }
            val days = JSONObject()
            DAYS.forEach { day ->
                val source = draft.daySources.getValue(day)
                require(source.isEmpty() || validMusicSource(source)) { "Select a valid music source for $day" }
                days.put(day, source)
            }
            body.put("day_sources", days)
        }
        return body
    }

    fun musicControlPayload(action: String): JSONObject {
        require(action in setOf("play", "pause", "resume", "stop", "skip")) { "Choose play, pause, resume, stop or skip" }
        return JSONObject().put("action", action)
    }

    fun likePayload(status: MusicStatus): JSONObject {
        require(status.ratingKey.matches(Regex("[0-9]{1,20}"))) { "No current song can be liked" }
        return JSONObject().put("rating_key", status.ratingKey)
    }

    /** The primary transport action the device accepts for the current state. */
    fun primaryMusicAction(settings: MusicSettings): String =
        if (!settings.enabled) "play" else if (settings.status.pausedByUser) "resume" else "pause"

    fun mixKey(kind: String, items: List<MusicBrowseItem>): String {
        require(kind == "artist" || kind == "album") { "Unknown mix type" }
        require(items.size <= MAX_MIX_ITEMS) { "Choose up to $MAX_MIX_ITEMS items" }
        return if (items.isEmpty()) "" else "mix:$kind:" + items.joinToString(",") { it.id }
    }

    fun mixKind(stationKey: String): String? = MIX_KEY.matchEntire(stationKey)?.groupValues?.get(1)

    fun validMusicSource(key: String) =
        listOf(STATION_KEY, PLAYLIST_KEY, MIX_KEY, ADVERTISED_MIX_KEY).any { it.matches(key) }

    fun railPayload(draft: RailDraft): JSONObject {
        val refresh = draft.refreshSeconds.trim().toIntOrNull()
        require(refresh != null && refresh >= 30) { "Rail Data refresh interval must be a whole number of at least 30 seconds" }
        val body = JSONObject().put("refresh_seconds", refresh)
        listOf("api_key" to draft.boardKey, "service_details_api_key" to draft.detailsKey).forEach { (field, value) ->
            if (value.isNotEmpty()) {
                require(value.length <= 512 && printable(value)) { "Rail Data API keys must contain 1-512 printable characters" }
                body.put(field, value)
            }
        }
        return body
    }

    fun railClearPayload(board: Boolean, details: Boolean): JSONObject {
        require(board || details) { "Choose a key to remove" }
        return JSONObject().put("clear_key", board).put("clear_details_key", details)
    }

    fun weatherPayload(draft: WeatherDraft): JSONObject {
        val city = draft.city.trim()
        require(city.length in 1..100 && city.none { it.code < 32 }) { "Weather city must contain 1-100 characters" }
        require(draft.country.matches(Regex("[A-Za-z]{2}"))) { "Weather country must be a two-letter country code" }
        val body = JSONObject().put("city", city).put("country", draft.country.uppercase())
        if (draft.apiKey.isNotEmpty()) {
            require(draft.apiKey.length <= 512 && printable(draft.apiKey)) { "Invalid OpenWeather API key" }
            body.put("api_key", draft.apiKey)
        }
        return body
    }

    fun weatherClearPayload(): JSONObject = JSONObject().put("clear_key", true)

    fun hostnamePayload(value: String): JSONObject {
        val name = value.trim().lowercase()
        require(HOSTNAME.matches(name)) {
            "Hostname must be 1-63 characters: letters, digits and hyphens, not starting or ending with a hyphen"
        }
        return JSONObject().put("hostname", name)
    }

    fun countryPayload(value: String): JSONObject {
        val country = value.trim().uppercase()
        require(country.matches(Regex("[A-Z]{2}"))) { "Country must be a two-letter ISO code such as GB" }
        return JSONObject().put("country", country)
    }

    fun connectPayload(ssid: String, password: String): JSONObject {
        require(ssid.isNotEmpty()) { "SSID is required" }
        require(ssid.toByteArray(Charsets.UTF_8).size <= 32) { "SSID must be at most 32 bytes" }
        require(ssid.none { it.code < 32 || it.code == 127 }) { "SSID contains control characters" }
        if (password.isNotEmpty()) {
            require(password.length in 8..63) { "Wi-Fi password must be 8 to 63 characters" }
            require(password.all { it.code in 32..126 }) { "Wi-Fi password must be printable ASCII" }
        }
        return JSONObject().put("ssid", ssid).put("password", password)
    }

    fun smartPlaylistPayload(library: String, decade: String, genre: String, mood: String, title: String? = null): JSONObject {
        require(library.isNotEmpty()) { "Select a music library" }
        require(decade.isNotEmpty() || genre.isNotEmpty() || mood.isNotEmpty()) { "Choose at least one filter" }
        val body = JSONObject().put("library", library).put("decade", decade).put("genre", genre).put("mood", mood)
        if (title != null) {
            val trimmed = title.trim()
            require(trimmed.length in 1..100 && trimmed.none { it.code < 32 || it.code == 127 }) {
                "Playlist name must contain 1-100 characters without control characters"
            }
            body.put("title", trimmed)
        }
        return body
    }

    /** Returns the first problem in the draft, naming the scene, or null when it matches the device rules. */
    fun playlistProblem(playlist: Playlist): String? {
        if (playlist.scenes.size > MAX_SCENES) return "Playlist must contain at most $MAX_SCENES scenes"
        val ids = mutableSetOf<String>()
        playlist.scenes.forEachIndexed { index, scene ->
            val label = "Scene ${index + 1}" + scene.title.trim().takeIf { it.isNotEmpty() }?.let { " ($it)" }.orEmpty()
            sceneProblem(scene)?.let { return "$label: $it" }
            if (!ids.add(scene.id)) return "$label: Scene IDs must be unique"
        }
        if (playlist.enabled && playlist.scenes.none { it.enabled }) {
            return "Enable at least one scene before enabling the carousel"
        }
        return null
    }

    fun sceneProblem(scene: Scene): String? {
        if (!SCENE_ID.matches(scene.id)) return "Each scene needs a valid ID"
        if (scene.kind !in SCENE_KINDS) return "Supported scene types are web, dashboard, video, train and weather"
        if (scene.title.length > 80) return "Scene titles must be at most 80 characters"
        if (scene.duration.isBlank()) {
            if (scene.kind != "video") return "Scene duration must be 5-86400 seconds"
        } else if (scene.duration.trim().toIntOrNull()?.takeIf { it in 5..86400 } == null) {
            return "Scene duration must be 5-86400 seconds" + if (scene.kind == "video") " (or no cutoff)" else ""
        }
        if (scene.kind == "video") {
            if (scene.source != "url" && scene.source != "plex") return "Video source must be a URL or Plex"
            if (scene.source == "plex") {
                if (!scene.plexRatingKey.matches(Regex("[0-9]{1,20}"))) return "Choose Plex media for this video"
                if (scene.plexBitrate.isNotBlank() &&
                    scene.plexBitrate.trim().toIntOrNull()?.takeIf { it in 500..20000 } == null
                ) return "Plex bitrate must be 500-20000 kbps"
            }
        }
        if (scene.kind == "web" || (scene.kind == "video" && scene.source == "url")) {
            if (scene.url.isBlank()) return "Web and video scenes require a URL"
            runCatching { validateUrl(scene.url.trim()) }.onFailure { return it.message }
        }
        if (scene.kind == "dashboard") {
            if (scene.layout !in LAYOUTS) return "Unknown dashboard layout"
            if (scene.text.length > 2000) return "Notice text must be at most 2000 characters"
        }
        if (scene.kind == "train") {
            if (!CRS.matches(scene.stationCrs)) return "Station CRS code must contain three letters"
            if (scene.destinationCrs.isNotEmpty() && !CRS.matches(scene.destinationCrs)) {
                return "Destination CRS code must contain three letters"
            }
            if (scene.filterStationCrs.isNotEmpty() && !CRS.matches(scene.filterStationCrs)) {
                return "Calling station CRS code must contain three letters"
            }
        }
        return null
    }

    fun playlistPayload(playlist: Playlist): JSONObject {
        playlistProblem(playlist)?.let { throw IllegalArgumentException(it) }
        val scenes = JSONArray()
        playlist.scenes.forEach { scene ->
            val item = JSONObject(scene.extras)
            val video = scene.kind == "video"
            val plex = video && scene.source == "plex"
            item.put("id", scene.id).put("kind", scene.kind).put("title", scene.title.trim()).put("enabled", scene.enabled)
                .put("duration", if (video && scene.duration.isBlank()) JSONObject.NULL else scene.duration.trim().toInt())
                .put("audio_enabled", video && scene.audioEnabled)
                .put("source", if (plex) "plex" else "url")
                .put("plex_rating_key", if (plex) scene.plexRatingKey else "")
                .put("plex_bitrate_kbps", if (plex && scene.plexBitrate.isNotBlank()) scene.plexBitrate.trim().toInt() else JSONObject.NULL)
                .put("url", if (scene.kind == "web" || (video && !plex)) scene.url.trim() else "")
                .put("layout", if (scene.kind == "dashboard") scene.layout else "clock-notice")
                .put("text", if (scene.kind == "dashboard") scene.text else "")
                .put("station_crs", if (scene.kind == "train") scene.stationCrs.uppercase() else "")
                .put("destination_crs", if (scene.kind == "train") scene.destinationCrs.uppercase() else "")
                .put("filter_station_crs", if (scene.kind == "train") scene.filterStationCrs.uppercase() else "")
                .put("show_previous_departure", scene.kind == "train" && scene.showPreviousDeparture)
                .put("show_weather", scene.kind == "dashboard" && scene.showWeather)
            scenes.put(item)
        }
        return JSONObject().put("enabled", playlist.enabled).put("scenes", scenes)
    }

    fun bitrate(value: String): Int {
        val bitrate = value.trim().toIntOrNull()
        require(bitrate != null && bitrate in 500..20000) { "Plex bitrate must be 500-20000 kbps" }
        return bitrate
    }

    data class ParsedUrl(val authority: String, val rest: String)

    /** Mirrors the device's urlparse checks without rejecting URLs java.net.URI would refuse. */
    fun validateUrl(value: String, message: String = "URL must use http:// or https:// and include a host"): ParsedUrl {
        require(value.length <= 2048 && value.none { it.code < 32 }) { "URL is too long or contains control characters" }
        val scheme = value.substringBefore("://", "").lowercase()
        require(scheme == "http" || scheme == "https") { message }
        val remainder = value.substringAfter("://")
        val end = remainder.indexOfFirst { it == '/' || it == '?' || it == '#' }.let { if (it < 0) remainder.length else it }
        val authority = remainder.substring(0, end)
        require('@' !in authority) { "URLs containing credentials are not allowed" }
        val host = if (authority.startsWith("[")) authority.substringBefore("]").drop(1) else authority.substringBefore(":")
        require(host.isNotEmpty()) { message }
        if (!authority.startsWith("[") && ':' in authority || authority.contains("]:")) {
            val port = authority.substringAfterLast(":")
            require(port.isEmpty() || port.toIntOrNull()?.takeIf { it in 0..65535 } != null) { "Invalid URL port" }
        }
        return ParsedUrl(authority, remainder.substring(end))
    }

    private fun printable(value: String) = value.all { it.code in 33..126 }

    // ---------- JSON helpers ----------

    private inline fun <T> parsing(what: String, block: () -> T): T = try {
        block()
    } catch (error: DeviceRequestException) {
        throw error
    } catch (_: Exception) {
        throw DeviceRequestException("The device returned an invalid $what response")
    }

    private fun JSONObject.str(name: String): String = opt(name) as? String ?: error("Missing $name")
    private fun JSONObject.bool(name: String): Boolean = opt(name) as? Boolean ?: error("Missing $name")
    private fun JSONObject.num(name: String): Double = (opt(name) as? Number)?.toDouble() ?: error("Missing $name")
    private fun JSONObject.int(name: String): Int = optIntStrict(name) ?: error("Missing $name")
    private fun JSONObject.optIntStrict(name: String): Int? {
        val value = opt(name) as? Number ?: return null
        val double = value.toDouble()
        return if (double % 1.0 == 0.0 && double in Int.MIN_VALUE.toDouble()..Int.MAX_VALUE.toDouble()) double.toInt() else null
    }
    private fun JSONObject.optStringOrNull(name: String): String? = opt(name) as? String
    private fun JSONObject.objects(name: String): List<JSONObject> {
        val array = getJSONArray(name)
        return (0 until array.length()).map { array.getJSONObject(it) }
    }
}
