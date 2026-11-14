package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.test.TestScope
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

@OptIn(ExperimentalCoroutinesApi::class)
class PlayerSessionTest {
    private fun carousel(enabled: Boolean = true, paused: Boolean = false) = JSONObject()
        .put("enabled", enabled).put("paused", paused).put("transport_supported", true)
        .put("transport_available", true).put("scene_title", "Scene")

    private fun music(ratingKey: String = "12") = JSONObject()
        .put("enabled", true).put("station_key", "/playlists/1/items").put("volume", 50)
        .put("status", JSONObject().put("state", "playing").put("title", "Song").put("rating_key", ratingKey))

    private inner class Backend : PlayerBackend {
        var responses = mapOf(
            PlayerProtocol.MUSIC to Result.success(music()),
            PlayerProtocol.CAROUSEL to Result.success(carousel()),
        )
        val commands = mutableListOf<PlayerCommand>()
        var calls = 0
        var active = 0
        var maxActive = 0
        var gate: CompletableDeferred<Unit>? = null
        var sendGate: CompletableDeferred<Unit>? = null
        var ignoreCancellation = false
        var sendError: Exception? = null
        override suspend fun fetch(id: String, requests: PinnedHttp.RequestScope): Map<String, Result<JSONObject>> {
            calls++
            active++
            maxActive = maxOf(maxActive, active)
            val result = responses
            try {
                if (ignoreCancellation) withContext(NonCancellable) { gate?.await() } else gate?.await()
                return result
            } finally { active-- }
        }

        override suspend fun send(id: String, command: PlayerCommand, requests: PinnedHttp.RequestScope) {
            commands += command
            sendGate?.await()
            sendError?.let { throw it }
        }
    }

    private fun TestScope.session(backend: Backend, admission: WorkAdmission = WorkAdmission()) =
        PlayerSession(backgroundScope, backend, admission).also {
            it.devices(listOf("one"))
            it.visible(true)
            it.resume()
        }

    @Test
    fun carouselCapabilitiesAreExplicitAndMalformedStatusDoesNotInventSupport() {
        val legacy = JSONObject().put("failures", JSONObject()).put("updated", 42)
        val old = PlayerProtocol.state(mapOf(
            PlayerProtocol.MUSIC to Result.success(music()),
            PlayerProtocol.CAROUSEL to Result.success(legacy),
        ))
        assertTrue(old.music.available)
        assertTrue(old.carousel.unsupported)
        assertFalse(old.carousel.available)
        assertEquals(PlayerProtocol.UNSUPPORTED, old.carousel.error)
        val modern = PlayerProtocol.carousel(carousel(paused = true))
        assertTrue(modern.paused)
        assertEquals("Scene", modern.sceneTitle)
        val malformed = carousel().put("enabled", "true")
        assertThrows(DeviceRequestException::class.java) { PlayerProtocol.carousel(malformed) }
        assertThrows(DeviceRequestException::class.java) { PlayerProtocol.carousel(carousel().put("scene_title", 1)) }
    }

    @Test
    fun missingOptionalEndpointDoesNotHideMusicOrImplyDeviceOffline() {
        val status = PlayerProtocol.state(mapOf(
            PlayerProtocol.MUSIC to Result.success(music()),
            PlayerProtocol.CAROUSEL to Result.failure(DeviceRequestException("Not found", 404)),
        ))
        assertTrue(status.music.available)
        assertTrue(status.carousel.unsupported)
        assertFalse(status.trustRequired)
        assertNull(status.error)
    }

    @Test
    fun transportBodiesContainOnlyTheStrictActionOrDisplayedTrackKey() {
        CarouselAction.entries.forEach { action ->
            val (path, body) = PlayerProtocol.request(PlayerCommand.Carousel(action))
            assertEquals("/api/playlist/control", path)
            assertEquals(setOf("action"), body.keys().asSequence().toSet())
            assertEquals(action.wire, body.getString("action"))
        }
        val (path, like) = PlayerProtocol.request(PlayerCommand.Like("123"))
        assertEquals("/api/plex/music/like", path)
        assertEquals("{\"rating_key\":\"123\"}", like.toString())
        assertThrows(IllegalArgumentException::class.java) { PlayerProtocol.request(PlayerCommand.Music("next")) }
        assertThrows(IllegalArgumentException::class.java) { PlayerProtocol.request(PlayerCommand.Like("")) }
    }

    @Test
    fun pollingIsHomeOnlyAndStopsOnBackgroundAndNavigation() = runTest {
        val backend = Backend()
        val session = session(backend)
        runCurrent()
        assertEquals(1, backend.calls)
        advanceTimeBy(2_000)
        runCurrent()
        assertEquals(2, backend.calls)
        session.visible(false)
        advanceTimeBy(10_000)
        runCurrent()
        assertEquals(2, backend.calls)
        session.visible(true)
        runCurrent()
        assertEquals(3, backend.calls)
        session.pause()
        advanceTimeBy(10_000)
        runCurrent()
        assertEquals(3, backend.calls)
        session.resume()
        runCurrent()
        assertEquals(4, backend.calls)
    }

    @Test
    fun slowPollNeverOverlapsAndCancelledGenerationCannotOverwriteNewStatus() = runTest {
        val backend = Backend().apply { gate = CompletableDeferred(); ignoreCancellation = true }
        val session = session(backend)
        runCurrent()
        advanceTimeBy(8_000)
        runCurrent()
        assertEquals(1, backend.calls)
        session.visible(false)
        session.visible(true)
        backend.responses = backend.responses + (PlayerProtocol.CAROUSEL to Result.success(carousel(paused = true)))
        runCurrent()
        assertEquals(1, backend.calls)
        backend.gate!!.complete(Unit)
        runCurrent()
        assertEquals(2, backend.calls)
        assertEquals(1, backend.maxActive)
        assertTrue(session.state.value.getValue("one").carousel.value!!.paused)
    }

    @Test
    fun manyDevicesHaveAtMostFourConcurrentPollsAndExternalMutationIsNotOvertaken() = runTest {
        val backend = Backend().apply { gate = CompletableDeferred() }
        val admission = WorkAdmission()
        val session = session(backend, admission)
        session.devices((1..9).map { "device-$it" })
        runCurrent()
        assertEquals(4, backend.active)
        backend.gate!!.complete(Unit)
        runCurrent()
        assertEquals(9, backend.calls)
        assertEquals(4, backend.maxActive)
        assertEquals(WorkAdmission.Result.START, admission.admit("device:device-1", false))
        session.command("device-1", PlayerCommand.Carousel(CarouselAction.PAUSE))
        runCurrent()
        assertTrue(backend.commands.isEmpty())
        assertEquals("Another device operation is running.", session.state.value.getValue("device-1").commandError)
        admission.cancel("device:device-1")
    }

    @Test
    fun queuedCommandsRefreshAppliedStateWithoutOptimisticPauseAndShareAdmission() = runTest {
        val backend = Backend().apply { sendGate = CompletableDeferred() }
        val admission = WorkAdmission()
        val session = session(backend, admission)
        runCurrent()
        session.command("one", PlayerCommand.Carousel(CarouselAction.PAUSE))
        runCurrent()
        assertTrue(session.state.value.getValue("one").working)
        assertFalse(session.state.value.getValue("one").carousel.value!!.paused)
        session.command("one", PlayerCommand.Music("pause"))
        assertEquals(1, backend.commands.size)
        assertEquals(WorkAdmission.Result.REJECTED, admission.admit("device:one", false))
        backend.sendGate!!.complete(Unit)
        runCurrent()
        assertEquals(2, backend.calls)
        assertFalse(session.state.value.getValue("one").working)
        assertFalse(session.state.value.getValue("one").carousel.value!!.paused)
        backend.responses = backend.responses + (PlayerProtocol.CAROUSEL to Result.success(carousel(paused = true)))
        advanceTimeBy(2_000)
        runCurrent()
        assertTrue(session.state.value.getValue("one").carousel.value!!.paused)
        assertEquals(WorkAdmission.Result.START, admission.admit("device:one", false))
        admission.cancel("device:one")
    }

    @Test
    fun disabledAndUnsupportedCarouselCannotSendCommandsButMusicStillCan() = runTest {
        val backend = Backend().apply {
            responses = responses + (PlayerProtocol.CAROUSEL to Result.success(carousel(enabled = false)))
        }
        val session = session(backend)
        runCurrent()
        session.command("one", PlayerCommand.Carousel(CarouselAction.NEXT))
        runCurrent()
        assertTrue(backend.commands.isEmpty())
        backend.responses = backend.responses + (PlayerProtocol.CAROUSEL to Result.failure(DeviceRequestException("Missing", 404)))
        advanceTimeBy(2_000)
        runCurrent()
        session.command("one", PlayerCommand.Carousel(CarouselAction.PAUSE))
        session.command("one", PlayerCommand.Music("pause"))
        runCurrent()
        assertEquals(listOf(PlayerCommand.Music("pause")), backend.commands)
    }

    @Test
    fun missingKioskHeartbeatDisablesCarouselWithoutReportingStalePauseAsAvailable() = runTest {
        val backend = Backend().apply {
            responses = responses + (PlayerProtocol.CAROUSEL to Result.success(
                carousel(paused = true).put("transport_available", false),
            ))
        }
        val session = session(backend)
        runCurrent()
        val transport = session.state.value.getValue("one").carousel.value!!
        assertFalse(transport.available)
        session.command("one", PlayerCommand.Carousel(CarouselAction.RESUME))
        session.command("one", PlayerCommand.Carousel(CarouselAction.NEXT))
        runCurrent()
        assertTrue(backend.commands.isEmpty())
        val olderCompatible = carousel().apply { remove("transport_available") }
        assertTrue(PlayerProtocol.carousel(olderCompatible).available)
    }

    @Test
    fun likeSendsDisplayedKeyAndServerConflictIsExplicitWithoutChangingSettings() = runTest {
        val backend = Backend()
        val session = session(backend)
        runCurrent()
        val displayed = session.state.value.getValue("one").music.value!!.status.ratingKey
        backend.responses = backend.responses + (PlayerProtocol.MUSIC to Result.success(music("99")))
        advanceTimeBy(2_000)
        runCurrent()
        backend.sendError = DeviceRequestException("Song changed; try again", 409)
        session.command("one", PlayerCommand.Like(displayed))
        runCurrent()
        assertEquals(listOf(PlayerCommand.Like("12")), backend.commands)
        assertEquals("Song changed; try again", session.state.value.getValue("one").commandError)
        assertEquals("99", session.state.value.getValue("one").music.value!!.status.ratingKey)
    }

    @Test
    fun trustFailuresDisablePlaybackAndRecoverableNetworkErrorsClearOnSuccess() = runTest {
        val backend = Backend().apply {
            responses = responses + (PlayerProtocol.CAROUSEL to Result.failure(CertificateChangedException()))
        }
        val session = session(backend)
        runCurrent()
        assertTrue(session.state.value.getValue("one").trustRequired)
        session.command("one", PlayerCommand.Music("pause"))
        runCurrent()
        assertTrue(backend.commands.isEmpty())
        backend.responses = mapOf(
            PlayerProtocol.CAROUSEL to Result.success(carousel()),
            PlayerProtocol.MUSIC to Result.success(music()),
        )
        advanceTimeBy(2_000)
        runCurrent()
        assertFalse(session.state.value.getValue("one").trustRequired)
        assertNull(session.state.value.getValue("one").error)
        assertTrue(session.state.value.getValue("one").music.available)
    }

    @Test
    fun navigationCancelsMutationAndReleasesAdmissionWithoutLateStateWrites() = runTest {
        val backend = Backend().apply { sendGate = CompletableDeferred() }
        val admission = WorkAdmission()
        val session = session(backend, admission)
        runCurrent()
        session.command("one", PlayerCommand.Carousel(CarouselAction.NEXT))
        runCurrent()
        session.visible(false)
        runCurrent()
        assertFalse(session.state.value.getValue("one").working)
        assertEquals(WorkAdmission.Result.START, admission.admit("device:one", false))
        admission.cancel("device:one")
        val calls = backend.calls
        backend.sendGate!!.complete(Unit)
        runCurrent()
        assertEquals(calls, backend.calls)
    }
}
