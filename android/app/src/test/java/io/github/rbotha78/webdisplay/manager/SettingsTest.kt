package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SettingsTest {
    private fun scene(kind: String, id: String = kind, change: (Scene) -> Scene = { it }) =
        change(SettingsRules.newScene(kind, id))

    @Test
    fun playlistValidationMirrorsDeviceRules() {
        val web = scene("web") { it.copy(url = "https://example.com") }
        assertNull(SettingsRules.playlistProblem(Playlist(true, listOf(web))))
        assertTrue(SettingsRules.playlistProblem(Playlist(true, listOf(web, web.copy(title = "Copy"))))!!.contains("unique"))
        assertTrue(SettingsRules.playlistProblem(Playlist(true, listOf(web.copy(enabled = false))))!!.contains("Enable at least one"))
        assertNull(SettingsRules.playlistProblem(Playlist(false, listOf(web.copy(enabled = false)))))
        assertNotNull(SettingsRules.sceneProblem(web.copy(duration = "")))
        assertNotNull(SettingsRules.sceneProblem(web.copy(duration = "4")))
        assertNotNull(SettingsRules.sceneProblem(web.copy(url = "https://user:pw@example.com")))
        assertNotNull(SettingsRules.sceneProblem(web.copy(url = "ftp://example.com")))
        assertNull(SettingsRules.sceneProblem(scene("video") { it.copy(url = "https://example.com/a.mp4", duration = "") }))
        assertNotNull(SettingsRules.sceneProblem(scene("video") { SettingsRules.withSource(it, "plex") }))
        assertNull(SettingsRules.sceneProblem(scene("video") { SettingsRules.withSource(it, "plex").copy(plexRatingKey = "123") }))
        assertNotNull(SettingsRules.sceneProblem(scene("video") {
            SettingsRules.withSource(it, "plex").copy(plexRatingKey = "123", plexBitrate = "100")
        }))
        assertNotNull(SettingsRules.sceneProblem(scene("train")))
        assertNull(SettingsRules.sceneProblem(scene("train") { it.copy(stationCrs = "pad") }))
        assertNotNull(SettingsRules.sceneProblem(scene("train") { it.copy(stationCrs = "PAD", destinationCrs = "R") }))
        val tooMany = Playlist(false, (1..31).map { scene("weather", "w$it") })
        assertNotNull(SettingsRules.playlistProblem(tooMany))
    }

    @Test
    fun playlistPayloadNormalisesKindsAndPreservesUnknownFields() {
        val source = JSONObject().put("enabled", true).put("scenes", JSONArray()
            .put(JSONObject().put("id", "v").put("kind", "video").put("duration", JSONObject.NULL).put("source", "plex")
                .put("plex_rating_key", "42").put("plex_bitrate_kbps", 4000).put("future_field", "kept"))
            .put(JSONObject().put("id", "t").put("kind", "train").put("duration", 120).put("station_crs", "PAD")))
        val playlist = SettingsRules.parsePlaylist(source)
        val edited = playlist.copy(scenes = playlist.scenes.map {
            if (it.kind == "train") it.copy(stationCrs = "rdg", showWeather = true) else it
        })
        val payload = SettingsRules.playlistPayload(edited).getJSONArray("scenes")
        val video = payload.getJSONObject(0)
        assertTrue(video.isNull("duration"))
        assertEquals("kept", video.getString("future_field"))
        assertEquals("", video.getString("url"))
        assertEquals(4000, video.getInt("plex_bitrate_kbps"))
        val train = payload.getJSONObject(1)
        assertEquals("RDG", train.getString("station_crs"))
        assertTrue(train.getBoolean("show_previous_departure"))
        assertFalse(train.getBoolean("show_weather"))
        assertThrows(IllegalArgumentException::class.java) {
            SettingsRules.playlistPayload(edited.copy(scenes = edited.scenes.map { it.copy(id = "same") }))
        }
    }

    @Test
    fun invalidResponsesFailInsteadOfBecomingEmptyDrafts() {
        assertThrows(DeviceRequestException::class.java) { SettingsRules.parsePlaylist(JSONObject().put("enabled", true)) }
        assertThrows(DeviceRequestException::class.java) { SettingsRules.parseSleep(JSONObject().put("days", JSONObject())) }
    }

    @Test
    fun secretsAreWriteOnlyAndClearsAreExplicit() {
        val plex = PlexSettings("http://plex:32400", configured = true, defaultBitrate = 4000)
        val keep = SettingsRules.plexPayload(PlexDraft("http://plex:32400/", "4000"), plex)
        assertFalse(keep.has("token"))
        assertEquals("http://plex:32400", keep.getString("server_url"))
        assertThrows(IllegalArgumentException::class.java) {
            SettingsRules.plexPayload(PlexDraft("http://plex:32400", "4000"), plex.copy(configured = false))
        }
        assertThrows(IllegalArgumentException::class.java) {
            SettingsRules.plexPayload(PlexDraft("http://plex:32400/web", "4000", "t"), plex)
        }
        val rail = SettingsRules.railPayload(RailDraft("", "details", "60"))
        assertFalse(rail.has("api_key"))
        assertEquals("details", rail.getString("service_details_api_key"))
        assertFalse(rail.has("clear_key"))
        assertFalse(SettingsRules.weatherPayload(WeatherDraft("London", "gb")).has("api_key"))
        assertFalse(PlexDraft("u", "1", "secret-token").toString().contains("secret-token"))
        assertFalse(WifiDraft("GB", "Home", "password1").toString().contains("password1"))
    }

    @Test
    fun displayPayloadSendsOnlyChangedFields() {
        val server = DisplayConfig("https://a.example", false, 2, "hdmi")
        val body = SettingsRules.displayPayload(SettingsRules.displayDraft(server).copy(safeArea = "5"), server)
        assertEquals(setOf("safe_area_percent"), body.keys().asSequence().toSet())
        assertThrows(IllegalArgumentException::class.java) { SettingsRules.displayPayload(SettingsRules.displayDraft(server), server) }
        assertThrows(IllegalArgumentException::class.java) {
            SettingsRules.displayPayload(SettingsRules.displayDraft(server).copy(safeArea = "11"), server)
        }
    }

    @Test
    fun musicMixAndLikePayloadsFollowDeviceFormats() {
        val items = listOf(MusicBrowseItem("1", "A", "artist", ""), MusicBrowseItem("2", "B", "artist", ""))
        val key = SettingsRules.mixKey("artist", items)
        assertEquals("mix:artist:1,2", key)
        assertEquals("artist", SettingsRules.mixKind(key))
        assertTrue(SettingsRules.validMusicSource(key))
        assertThrows(IllegalArgumentException::class.java) { SettingsRules.musicPayload(MusicDraft(true, "", 50)) }
        val status = MusicStatus("playing", "Song", "", false, "99", false)
        assertEquals("99", SettingsRules.likePayload(status).getString("rating_key"))
        assertThrows(IllegalArgumentException::class.java) { SettingsRules.likePayload(status.copy(ratingKey = "")) }
    }

    private fun musicJson(schedule: Map<String, String>? = null): JSONObject =
        JSONObject().put("enabled", true).put("station_key", "/playlists/1/items").put("volume", 50)
            .put("status", JSONObject().put("state", "playing"))
            .apply {
                if (schedule != null) {
                    put("day_sources", JSONObject(schedule))
                    put("active_day", "saturday")
                    put("active_station_key", schedule.getValue("saturday").ifEmpty { "/playlists/1/items" })
                }
            }

    @Test
    fun musicScheduleParsingHandlesNewAndOldFirmware() {
        val days = SettingsRules.DAYS.associateWith { if (it == "saturday") "mix:artist:1,2" else "" }
        val current = SettingsRules.parseMusic(musicJson(days))
        assertTrue(current.supportsDaySources)
        assertEquals(days, current.daySources)
        assertEquals("saturday", current.activeDay)
        assertEquals("mix:artist:1,2", current.activeStationKey)
        val old = SettingsRules.parseMusic(musicJson())
        assertFalse(old.supportsDaySources)
        assertEquals(SettingsRules.DAYS.associateWith { "" }, old.daySources)
        assertThrows(DeviceRequestException::class.java) {
            SettingsRules.parseMusic(musicJson(days).put("day_sources", JSONObject().put("monday", "")))
        }
    }

    @Test
    fun musicSchedulePayloadValidatesDaysAndSourceKeys() {
        val days = SettingsRules.DAYS.associateWith { if (it == "monday") "/playlists/2/items" else "" }
        val draft = SettingsRules.musicDraft(SettingsRules.parseMusic(musicJson(days)))
        val serialized = SettingsRules.musicPayload(draft).getJSONObject("day_sources")
        assertEquals(days, serialized.keys().asSequence().associateWith { serialized.getString(it) })
        assertFalse(SettingsRules.musicPayload(SettingsRules.musicDraft(SettingsRules.parseMusic(musicJson()))).has("day_sources"))
        assertThrows(IllegalArgumentException::class.java) {
            SettingsRules.musicPayload(draft.copy(daySources = days + ("holiday" to "")))
        }
        assertThrows(IllegalArgumentException::class.java) {
            SettingsRules.musicPayload(draft.copy(daySources = days - "monday"))
        }
        assertThrows(IllegalArgumentException::class.java) {
            SettingsRules.musicPayload(draft.copy(daySources = days + ("monday" to "invalid")))
        }
    }

    // ---------- Session behaviour ----------

    private class FakeBackend : SettingsBackend {
        val responses = mutableMapOf<String, JSONObject>()
        val sent = mutableListOf<Pair<String, JSONObject?>>()
        var gate: CompletableDeferred<Unit>? = null
        override suspend fun identity(id: String) = "AA:BB" to "display"
        override suspend fun fetch(id: String, paths: List<String>, requests: PinnedHttp.RequestScope): Map<String, Result<JSONObject>> {
            gate?.await()
            return paths.associateWith { path ->
                responses[path]?.let { Result.success(JSONObject(it.toString())) }
                    ?: Result.failure(DeviceRequestException("not found", 404))
            }
        }
        override suspend fun send(id: String, method: String, path: String, body: JSONObject?, timeoutMs: Int): JSONObject {
            sent += path to body
            if (path == "/api/playlist") responses[path] = JSONObject(body.toString())
            return JSONObject(responses.getValue(path).toString())
        }
        override suspend fun installUpdate(id: String) = "1.0"
    }

    private fun playlistJson(enabled: Boolean, url: String) = JSONObject().put("enabled", enabled).put("scenes", JSONArray()
        .put(JSONObject().put("id", "a").put("kind", "web").put("duration", 60).put("url", url)))

    private fun TestScope.openSession(backend: FakeBackend, notices: MutableList<String> = mutableListOf()): SettingsSession {
        val session = SettingsSession(this, backend) { notices += it }
        session.open(DeviceIdentity("d1", "Lobby", "10.0.0.2", 8443, null, "1.0"))
        advanceUntilIdle()
        return session
    }

    @Test
    fun missingEndpointsAreUnsupportedNotEmptySuccess() = runTest {
        val backend = FakeBackend().apply { responses["/api/playlist"] = playlistJson(true, "https://a.example") }
        val session = openSession(backend)
        val state = session.state.value!!
        assertEquals("AA:BB", state.identity.fingerprint)
        assertTrue(state.weather.unsupported)
        assertNull(state.weather.server)
        assertNull(state.weather.draft)
        assertEquals(1, state.playlist.draft!!.scenes.size)
        session.close()
    }

    @Test
    fun draftsSurviveRefreshAndConflictsBlockSilentOverwrite() = runTest {
        val backend = FakeBackend().apply { responses["/api/playlist"] = playlistJson(true, "https://a.example") }
        val notices = mutableListOf<String>()
        val session = openSession(backend, notices)
        session.setTab(SettingsTab.CAROUSEL)
        session.editScene("a") { it.copy(title = "Mine") }
        backend.responses["/api/playlist"] = playlistJson(true, "https://changed.example")
        session.resume()
        session.setTab(SettingsTab.GLOBAL)
        advanceUntilIdle()
        assertEquals("Mine", session.state.value!!.playlist.draft!!.scenes[0].title)

        session.savePlaylist()
        advanceUntilIdle()
        assertTrue(session.state.value!!.playlist.conflict)
        assertTrue(backend.sent.isEmpty())

        session.savePlaylist(force = true)
        advanceUntilIdle()
        val saved = session.state.value!!.playlist
        assertEquals(listOf("/api/playlist"), backend.sent.map { it.first })
        assertFalse(saved.conflict)
        assertEquals("Mine", saved.server!!.scenes[0].title)
        assertFalse(session.state.value!!.dirty(SettingsPart.PLAYLIST))
        session.close()
    }

    @Test
    fun invalidDraftIsNotSentAndClosingIgnoresLateResults() = runTest {
        val backend = FakeBackend().apply { responses["/api/playlist"] = playlistJson(true, "https://a.example") }
        val session = openSession(backend)
        session.editScene("a") { it.copy(url = "") }
        session.savePlaylist(force = true)
        advanceUntilIdle()
        assertTrue(backend.sent.isEmpty())
        assertNotNull(session.state.value!!.playlist.error)

        backend.gate = CompletableDeferred()
        session.reload()
        session.close()
        backend.gate!!.complete(Unit)
        advanceUntilIdle()
        assertNull(session.state.value)
    }

    @Test
    fun musicScheduleDraftSurvivesRefreshAndDetectsRemoteChanges() = runTest {
        val days = SettingsRules.DAYS.associateWith { "" }
        val backend = FakeBackend().apply { responses["/api/plex/music"] = musicJson(days) }
        val session = openSession(backend)
        session.editMusic { it.copy(daySources = it.daySources + ("monday" to "/playlists/2/items")) }
        assertTrue(session.state.value!!.dirty(SettingsPart.MUSIC))
        backend.responses["/api/plex/music"] = musicJson(days + ("tuesday" to "/playlists/3/items"))
        session.refreshMusic()
        advanceUntilIdle()
        assertEquals("/playlists/2/items", session.state.value!!.music.draft!!.daySources["monday"])
        backend.responses["/api/plex/music/control"] = backend.responses.getValue("/api/plex/music")
        session.musicControl("skip")
        advanceUntilIdle()
        session.saveMusic()
        advanceUntilIdle()
        assertTrue(session.state.value!!.music.conflict)
        assertEquals(listOf("/api/plex/music/control"), backend.sent.map { it.first })
        session.close()
    }

    @Test
    fun wifiPasswordIsClearedFromStateWhenConnecting() = runTest {
        val backend = FakeBackend().apply {
            responses["/api/network"] = JSONObject().put("wifi", JSONObject().put("country", "GB").put("saved_network", false)
                .put("devices", JSONArray())).put("internet", JSONObject().put("online", true))
            responses["/api/wifi/connect"] = JSONObject().put("status", "connected")
        }
        val session = openSession(backend)
        session.editWifi { it.copy(ssid = "Home", password = "password1") }
        session.connectWifi()
        assertEquals("", session.state.value!!.network.draft!!.password)
        advanceUntilIdle()
        assertEquals("password1", backend.sent.single { it.first == "/api/wifi/connect" }.second!!.getString("password"))
        session.close()
    }
}
