package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import org.json.JSONObject

data class CarouselTransport(
    val enabled: Boolean,
    val paused: Boolean,
    val sceneTitle: String,
    val available: Boolean = true,
)

data class PlayerSection<T>(val value: T? = null, val error: String? = null, val unsupported: Boolean = false) {
    val available: Boolean get() = value != null && error == null && !unsupported
}

data class PlayerState(
    val music: PlayerSection<MusicSettings> = PlayerSection(),
    val carousel: PlayerSection<CarouselTransport> = PlayerSection(),
    val refreshing: Boolean = true,
    val working: Boolean = false,
    val error: String? = null,
    val commandError: String? = null,
    val trustRequired: Boolean = false,
)

enum class CarouselAction(val wire: String) { PAUSE("pause"), RESUME("resume"), NEXT("next") }

sealed interface PlayerCommand {
    data class Carousel(val action: CarouselAction) : PlayerCommand
    data class Music(val action: String) : PlayerCommand
    data class Like(val ratingKey: String) : PlayerCommand
}

object PlayerProtocol {
    const val MUSIC = "/api/plex/music"
    const val CAROUSEL = "/api/playlist/status"
    const val UNSUPPORTED = "Carousel controls need a firmware update."

    fun carousel(json: JSONObject): CarouselTransport {
        if (json.opt("transport_supported") != true ||
            json.opt("enabled") !is Boolean || json.opt("paused") !is Boolean
        ) throw DeviceRequestException(UNSUPPORTED, 404)
        if (json.opt("scene_title") !is String) throw DeviceRequestException("Invalid carousel playback status.")
        if (json.has("transport_available") && json.opt("transport_available") !is Boolean) {
            throw DeviceRequestException("Invalid carousel playback availability.")
        }
        return CarouselTransport(json.getBoolean("enabled"), json.getBoolean("paused"), json.getString("scene_title"),
            json.optBoolean("transport_available", true))
    }

    fun request(command: PlayerCommand): Pair<String, JSONObject> = when (command) {
        is PlayerCommand.Carousel -> "/api/playlist/control" to JSONObject().put("action", command.action.wire)
        is PlayerCommand.Music -> "/api/plex/music/control" to SettingsRules.musicControlPayload(command.action)
        is PlayerCommand.Like -> "/api/plex/music/like" to SettingsRules.likePayload(
            MusicStatus("", "", "", false, command.ratingKey, false),
        )
    }

    fun state(responses: Map<String, Result<JSONObject>>): PlayerState {
        fun <T> section(path: String, parse: (JSONObject) -> T): PlayerSection<T> = try {
            PlayerSection(value = parse(responses[path]?.getOrThrow()
                ?: throw DeviceRequestException("No playback status returned.")))
        } catch (error: Exception) {
            if (error is CancellationException) throw error
            if (error is CertificateChangedException || (error as? DeviceRequestException)?.status == 401) throw error
            val unsupported = (error as? DeviceRequestException)?.status == 404
            PlayerSection(error = if (unsupported) {
                if (path == CAROUSEL) UNSUPPORTED else "Music controls need a firmware update."
            } else error.message ?: "Could not load playback status.", unsupported = unsupported)
        }
        return PlayerState(
            music = section(MUSIC, SettingsRules::parseMusic),
            carousel = section(CAROUSEL, ::carousel),
            refreshing = false,
        )
    }
}

interface PlayerBackend {
    suspend fun fetch(id: String, requests: PinnedHttp.RequestScope): Map<String, Result<JSONObject>>
    suspend fun send(id: String, command: PlayerCommand, requests: PinnedHttp.RequestScope)
}

/** Home-only status: never reads provider configuration or touches settings drafts. */
internal class PlayerSession(
    private val scope: CoroutineScope,
    private val backend: PlayerBackend,
    private val admission: WorkAdmission,
) {
    private val mutable = MutableStateFlow<Map<String, PlayerState>>(emptyMap())
    val state: StateFlow<Map<String, PlayerState>> = mutable
    private var ids = emptyList<String>()
    private var foreground = false
    private var visible = false
    private var generation = 0
    private var poll: Job? = null
    private val mutations = mutableSetOf<Job>()
    private var requests = PinnedHttp.RequestScope("Home player controls closed")
    // Also prevents overlap with blocking I/O from a cancelled previous generation.
    private val lock = Mutex()

    fun devices(devices: List<String>) {
        val next = devices.distinct().sorted()
        if (ids == next) return
        stop()
        ids = next
        mutable.update { previous -> ids.associateWith { previous[it] ?: PlayerState() } }
        start()
    }

    fun visible(value: Boolean) {
        if (visible == value) return
        visible = value
        if (value) start() else stop()
    }

    fun resume() { foreground = true; start() }
    fun pause() { foreground = false; stop() }

    private fun start() {
        if (!foreground || !visible || ids.isEmpty() || poll?.isActive == true) return
        val current = generation
        val activeRequests = requests
        poll = scope.launch {
            while (isActive) {
                lock.withLock {
                    coroutineScope {
                        val gate = Semaphore(4)
                        ids.map { id -> async { gate.withPermit { refresh(id, current, activeRequests) } } }
                            .forEach { it.await() }
                    }
                }
                delay(2_000)
            }
        }
    }

    private suspend fun refresh(id: String, current: Int, activeRequests: PinnedHttp.RequestScope) {
        if (current != generation) return
        mutable.update { it + (id to (it[id] ?: PlayerState()).copy(refreshing = true)) }
        try {
            val result = PlayerProtocol.state(backend.fetch(id, activeRequests))
            currentCoroutineContext().ensureActive()
            if (current == generation) mutable.update {
                it + (id to result.copy(working = it[id]?.working == true, commandError = it[id]?.commandError))
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            currentCoroutineContext().ensureActive()
            if (current == generation) mutable.update {
                it + (id to (it[id] ?: PlayerState()).copy(
                    refreshing = false, error = error.message ?: "Could not load player status.",
                    trustRequired = error is CertificateChangedException || (error as? DeviceRequestException)?.status == 401,
                    music = PlayerSection(error = "Playback status unavailable."),
                    carousel = PlayerSection(error = "Playback status unavailable."),
                ))
            }
        }
    }

    fun command(id: String, command: PlayerCommand) {
        val currentState = mutable.value[id] ?: return
        if (!foreground || !visible || currentState.working || currentState.trustRequired) return
        val allowed = when (command) {
            is PlayerCommand.Carousel -> currentState.carousel.available &&
                currentState.carousel.value?.let { it.enabled && it.available } == true
            is PlayerCommand.Music -> currentState.music.available
            is PlayerCommand.Like -> currentState.music.available && command.ratingKey.matches(Regex("[0-9]{1,20}"))
        }
        if (!allowed) return
        val key = "device:$id"
        if (admission.admit(key, false) != WorkAdmission.Result.START) {
            mutable.update { it + (id to currentState.copy(commandError = "Another device operation is running.")) }
            return
        }
        val current = generation
        val activeRequests = requests
        mutable.update { it + (id to currentState.copy(working = true, commandError = null)) }
        val job = scope.launch(start = CoroutineStart.LAZY) {
            lock.withLock {
                try {
                    backend.send(id, command, activeRequests)
                    // A queued command is not applied state. Only the next GET may change the controls.
                    refresh(id, current, activeRequests)
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    currentCoroutineContext().ensureActive()
                    if (current == generation) {
                        refresh(id, current, activeRequests)
                        mutable.update { states -> states[id]?.let {
                            states + (id to it.copy(
                                commandError = error.message ?: "Player command failed.",
                                trustRequired = it.trustRequired || error is CertificateChangedException ||
                                    (error as? DeviceRequestException)?.status == 401,
                            ))
                        } ?: states }
                    }
                }
            }
        }
        mutations += job
        job.invokeOnCompletion {
            mutations -= job
            admission.cancel(key)
            if (current == generation) mutable.update { states ->
                states[id]?.let { states + (id to it.copy(working = false)) } ?: states
            }
        }
        job.start()
    }

    private fun stop() {
        generation++
        poll?.cancel()
        requests.cancel()
        requests = PinnedHttp.RequestScope("Home player controls closed")
        mutations.toList().forEach { it.cancel() }
        mutable.update { states -> states.mapValues { (_, value) -> value.copy(refreshing = false, working = false) } }
    }
}
