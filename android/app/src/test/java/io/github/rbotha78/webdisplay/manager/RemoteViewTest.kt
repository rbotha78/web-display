package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.TimeZone

internal fun remoteBody(scene: String = """{"kind":"dashboard","layout":"clock","title":"Lobby"}""") =
    JSONObject(
        """{"available":true,"mode":"carousel","updated":1790000000,
        "device_time":"2026-07-01T12:30:00+02:00","timezone":"Africa/Johannesburg",
        "scene":$scene,"train":null,"weather":null,
        "sleep":{"sleeping":false,"scheduled_sleep":false,"wake_until":null},"message":""}""",
    )

class RemoteViewTest {
    @Test
    fun websiteIsTextOnlyAndVideoDiscardsAllPlayableUrls() {
        val website = RemoteViewParser.parse(remoteBody(
            """{"kind":"web","title":"<script>not executable</script>","url":"https://example.com/status"}""",
        ), 100)
        assertEquals(
            RemoteScene.Website("<script>not executable</script>", "https://example.com/status"),
            website.scene,
        )
        val video = RemoteViewParser.parse(remoteBody(
            """{"kind":"video","title":"Film","source":"plex","audio_enabled":false,"duration":90,
            "plex_rating_key":"42","url":"https://secret/stream","runtime_url":"http://relay/video",
            "poster":"https://external/image"}""",
        ), 100).scene as RemoteScene.Video
        assertEquals(RemoteScene.Video("Film", "plex", false, 90.0, "42"), video)
        assertFalse(video.toString().contains("https://"))
        assertFalse(video.toString().contains("relay"))
        val minimal = RemoteViewParser.parse(remoteBody("""{"kind":"video","title":"Clip"}"""), 100)
            .scene as RemoteScene.Video
        assertNull(minimal.audioEnabled)
        assertNull(minimal.duration)
    }

    @Test
    fun dashboardClockUsesDeviceZoneAndMonotonicElapsedTimeNotThePhoneClock() {
        val previous = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("America/Los_Angeles"))
            val body = remoteBody(
                """{"kind":"dashboard","layout":"notice","text":"Plain <b>notice</b>","show_weather":true}""",
            )
            val snapshot = RemoteViewParser.parse(body, 5_000)
            val dashboard = snapshot.scene as RemoteScene.Dashboard
            assertEquals("notice", dashboard.layout)
            assertEquals("Plain <b>notice</b>", dashboard.text)
            assertTrue(dashboard.showWeather)
            assertEquals("2026-07-01T12:30:06+02:00[Africa/Johannesburg]",
                snapshot.clock.at(11_000).toString())
            assertEquals(12, snapshot.clock.at(0).hour)
            body.put("timezone", "Unknown/Device_Zone")
            val fallback = RemoteViewParser.parse(body, 0).clock
            assertEquals("+02:00", fallback.zone.id)
            assertEquals(12, fallback.at(0).hour)
        } finally {
            TimeZone.setDefault(previous)
        }
    }

    @Test
    fun trainPreservesBoardNoticesStatusAndGroupedCallingPoints() {
        val body = remoteBody("""{"kind":"train","title":"Departures","station_crs":"KGX"}""")
        body.put("train", JSONObject(
            """{"station":"London Kings Cross","platform_available":true,"services_available":true,
            "stale":true,"error":"Cached board","details_available":false,"details_error":"Details offline",
            "rows":[{"scheduled":"12:40","estimated":"Delayed","destination":"York","platform":"4",
            "operator":"LNER","status":"Delayed by 8 min","state":"delayed","cancelled":false,
            "service_type":"bus","alerts":["Replacement service"]}],
            "alerts":["Engineering work"],"updated_at":1790000000,
            "next":{"previous_stops":[[{"station":"Earlier","actual":"12:20","cancelled":false}]],
            "calling_at":[[{"station":"York","scheduled":"14:00","estimated":"14:08","cancelled":false}],
            [{"station":"Leeds","cancelled":true}]],
            "last_seen":{"station":"London","actual":"12:30"}}}""",
        ))
        val snapshot = RemoteViewParser.parse(body, 0)
        assertEquals("KGX", (snapshot.scene as RemoteScene.Train).stationCrs)
        val train = requireNotNull(snapshot.train)
        assertTrue(train.stale)
        assertFalse(requireNotNull(train.detailsAvailable))
        assertEquals("Details offline", train.detailsError)
        assertTrue(train.rows.single().bus)
        assertEquals("Delayed by 8 min", train.rows.single().status)
        assertEquals(listOf("Replacement service"), train.rows.single().alerts)
        assertEquals("Earlier (actual 12:20)", train.previousStops.single().single().description())
        assertEquals("York (14:00; expected 14:08)", train.callingAt[0].single().description())
        assertEquals("Leeds (Cancelled)", train.callingAt[1].single().description())
        assertEquals("London at 12:30", train.lastSeen)
        assertEquals(listOf("Engineering work"), train.alerts)
    }

    @Test
    fun weatherPreservesMissingMeasurementsForecastAndStaleErrors() {
        val body = remoteBody("""{"kind":"weather","title":"Weather"}""")
        body.put("weather", JSONObject(
            """{"location":"Cape Town","temperature":17.4,"description":"Cloudy","humidity":64,
            "wind_kmh":8.5,"observed_at":1790000000,"stale":true,"error":"Offline",
            "forecast":[{"date":"2026-07-02","low":8,"high":20,"description":"Clear"}],
            "forecast_error":"Partial forecast","icon":"https://do-not-load/image"}""",
        ))
        val snapshot = RemoteViewParser.parse(body, 0)
        assertTrue(snapshot.scene is RemoteScene.Weather)
        val weather = requireNotNull(snapshot.weather)
        assertEquals(17.4, requireNotNull(weather.temperature), 0.001)
        assertNull(weather.feelsLike)
        assertEquals("Partial forecast", weather.forecastError)
        assertEquals(RemoteForecast("2026-07-02", 8.0, 20.0, "Clear"), weather.forecast.single())
        assertTrue(weather.stale)
        assertEquals("Offline", weather.error)
        assertFalse(weather.toString().contains("https://"))
    }

    @Test
    fun recoveryAndSleepDoNotInventACurrentScene() {
        val body = remoteBody("null").put("mode", "recovery").put("message", "Waiting for a target")
        body.getJSONObject("sleep").put("sleeping", true).put("scheduled_sleep", true).put("wake_until", 1790001000)
        val snapshot = RemoteViewParser.parse(body, 0)
        assertNull(snapshot.scene)
        assertEquals("recovery", snapshot.mode)
        assertEquals("Waiting for a target", snapshot.message)
        assertTrue(snapshot.sleeping)
        assertTrue(snapshot.scheduledSleep)
        assertEquals(1790001000.0, snapshot.wakeUntil)
    }

    @Test
    fun malformedOrUnavailableStateFailsRatherThanShowingFakeDefaults() {
        listOf(
            remoteBody().put("available", false),
            remoteBody().put("device_time", "not a time"),
            remoteBody().put("mode", "unknown"),
            remoteBody().put("updated", JSONObject.NULL),
            remoteBody().put("scene", JSONObject("""{"kind":"unsupported"}""")),
            remoteBody().put("sleep", JSONObject("""{"sleeping":"false"}""")),
        ).forEach { body ->
            assertThrows(DeviceRequestException::class.java) { RemoteViewParser.parse(body, 0) }
        }
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class RemoteViewSessionTest {
    @Test
    fun pollsImmediatelyThenFiveSecondsAfterCompletionOnlyWhileOpenAndForegrounded() = runTest {
        val snapshot = RemoteViewParser.parse(remoteBody(), 0)
        var calls = 0
        val session = RemoteViewSession(this) { _, _ ->
            calls++
            delay(2_000)
            snapshot
        }
        session.open("one", "Lobby")
        runCurrent()
        assertEquals(0, calls)
        session.resume()
        runCurrent()
        assertEquals(1, calls)
        advanceTimeBy(6_999)
        runCurrent()
        assertEquals(1, calls)
        advanceTimeBy(1)
        runCurrent()
        assertEquals(2, calls)
        session.pause()
        runCurrent()
        advanceTimeBy(20_000)
        assertEquals(2, calls)
        assertTrue(requireNotNull(session.state.value).stale)
        session.resume()
        runCurrent()
        assertEquals(3, calls)
        session.close()
        runCurrent()
        advanceTimeBy(20_000)
        assertEquals(3, calls)
        assertNull(session.state.value)
    }

    @Test
    fun failedRefreshKeepsLastSceneExplicitlyStaleAndSuccessfulRetryClearsError() = runTest {
        val snapshot = RemoteViewParser.parse(remoteBody(), 0)
        var calls = 0
        val session = RemoteViewSession(this) { _, _ ->
            if (++calls == 2) throw DeviceRequestException("Device unavailable", 503)
            snapshot
        }
        session.resume()
        session.open("one", "Lobby")
        runCurrent()
        assertFalse(requireNotNull(session.state.value).stale)
        advanceTimeBy(5_000)
        runCurrent()
        val failed = requireNotNull(session.state.value)
        assertEquals(snapshot, failed.snapshot)
        assertEquals("Device unavailable", failed.error)
        assertTrue(failed.stale)
        advanceTimeBy(5_000)
        runCurrent()
        assertFalse(requireNotNull(session.state.value).stale)
        assertNull(session.state.value?.error)
        session.close()
    }

    @Test
    fun rapidCloseReopenCannotOverlapRequestsOrPublishCancelledResults() = runTest {
        val snapshot = RemoteViewParser.parse(remoteBody(), 0)
        val finishOldRequest = CompletableDeferred<Unit>()
        val called = mutableListOf<String>()
        val session = RemoteViewSession(this) { id, _ ->
            called += id
            if (id == "old") withContext(NonCancellable) { finishOldRequest.await() }
            snapshot
        }
        session.resume()
        session.open("old", "Old")
        runCurrent()
        session.close()
        session.open("intermediate", "Intermediate")
        runCurrent()
        session.close()
        session.open("new", "New")
        runCurrent()
        assertEquals(listOf("old"), called)
        assertNull(session.state.value?.snapshot)
        finishOldRequest.complete(Unit)
        runCurrent()
        assertEquals(listOf("old", "new"), called)
        assertEquals("new", session.state.value?.deviceId)
        assertEquals(snapshot, session.state.value?.snapshot)
        session.close()
    }
}
