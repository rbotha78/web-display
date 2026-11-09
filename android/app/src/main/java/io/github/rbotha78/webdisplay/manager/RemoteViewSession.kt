package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

internal class RemoteViewSession(
    private val scope: CoroutineScope,
    private val fetch: suspend (String, PinnedHttp.RequestScope) -> RemoteSnapshot,
) {
    private val mutableState = MutableStateFlow<RemoteViewState?>(null)
    val state: StateFlow<RemoteViewState?> = mutableState
    private var job: Job? = null
    private var requests: PinnedHttp.RequestScope? = null
    private var foreground = false
    private val pollLock = Mutex()

    fun open(id: String, name: String) {
        if (state.value?.deviceId == id) return
        stop()
        mutableState.value = RemoteViewState(id, name, paused = !foreground)
        if (foreground) start()
    }

    fun close() {
        stop()
        mutableState.value = null
    }

    fun resume() {
        foreground = true
        if (state.value != null && job?.isActive != true) start()
    }

    fun pause() {
        foreground = false
        stop()
    }

    private fun start() {
        val id = state.value?.deviceId ?: return
        val previous = job
        val requestScope = PinnedHttp.RequestScope()
        requests = requestScope
        mutableState.update { it?.copy(paused = false, awaitingSnapshot = true) }
        job = scope.launch {
            previous?.join()
            while (isActive) {
                mutableState.update { it?.copy(refreshing = true) }
                try {
                    // Cancellation disconnects I/O; the lock also serializes rapid close/reopen cycles.
                    val snapshot = pollLock.withLock { fetch(id, requestScope) }
                    currentCoroutineContext().ensureActive()
                    mutableState.update { it?.received(snapshot) }
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    currentCoroutineContext().ensureActive()
                    mutableState.update { it?.failed(error.message ?: "Could not load remote view.") }
                }
                delay(5_000)
            }
        }
    }

    private fun stop() {
        job?.cancel()
        requests?.cancel()
        requests = null
        mutableState.update { it?.copy(paused = true, refreshing = false) }
    }
}
