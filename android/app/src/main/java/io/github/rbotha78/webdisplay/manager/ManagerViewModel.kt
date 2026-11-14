package io.github.rbotha78.webdisplay.manager

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap

class ManagerViewModel(application: Application) : AndroidViewModel(application) {
    private val manager = DeviceManager(SecureDeviceStore(application))
    private val jobs = ConcurrentHashMap.newKeySet<Job>()
    private val admission = WorkAdmission()

    private val _statuses = MutableStateFlow<List<DeviceStatus>>(emptyList())
    val statuses: StateFlow<List<DeviceStatus>> = _statuses
    private val _found = MutableStateFlow<List<FoundDevice>>(emptyList())
    private var latestDiscovered = emptyList<FoundDevice>()
    private var discoveryGeneration = 0
    val found: StateFlow<List<FoundDevice>> = _found
    private val _busy = MutableStateFlow<Set<String>>(emptySet())
    val busy: StateFlow<Set<String>> = _busy
    private val _notice = MutableStateFlow<String?>(null)
    val notice: StateFlow<String?> = _notice
    private val _musicSyncResults = MutableStateFlow<List<MusicSyncOutcome>>(emptyList())
    val musicSyncResults: StateFlow<List<MusicSyncOutcome>> = _musicSyncResults
    private val _storeError = MutableStateFlow<String?>(null)
    val storeError: StateFlow<String?> = _storeError
    private val _managerVersion = MutableStateFlow("1.0.0")
    val managerVersion: StateFlow<String> = _managerVersion
    private val remoteSession = RemoteViewSession(viewModelScope, manager::remoteView)
    val remoteView: StateFlow<RemoteViewState?> = remoteSession.state
    private val playerSession = PlayerSession(viewModelScope, object : PlayerBackend {
        override suspend fun fetch(id: String, requests: PinnedHttp.RequestScope) = manager.playerStatus(id, requests)
        override suspend fun send(id: String, command: PlayerCommand, requests: PinnedHttp.RequestScope) =
            manager.playerControl(id, command, requests)
    }, admission)
    val players = playerSession.state
    fun homeVisible(visible: Boolean) = playerSession.visible(visible)
    fun resumePlayers() = playerSession.resume()
    fun playerCommand(id: String, command: PlayerCommand) = playerSession.command(id, command)

    val settingsEditor = SettingsSession(viewModelScope, object : SettingsBackend {
        override suspend fun identity(id: String) = manager.settingsIdentity(id)
        override suspend fun fetch(id: String, paths: List<String>, requests: PinnedHttp.RequestScope) =
            manager.fetchSettings(id, paths, requests)
        override suspend fun send(id: String, method: String, path: String, body: org.json.JSONObject?, timeoutMs: Int) =
            manager.sendSettings(id, method, path, body, timeoutMs)
        override suspend fun installUpdate(id: String) = manager.installUpdate(id)
    }) { _notice.value = it }
    val settings: StateFlow<SettingsState?> = settingsEditor.state

    fun openSettings(status: DeviceStatus) = settingsEditor.open(
        DeviceIdentity(status.id, status.name, status.host, status.port, status.hostname, status.version),
    )

    fun closeSettings() {
        settingsEditor.close()
        refresh()
    }

    fun resumeSettings() = settingsEditor.resume()

    fun openRemoteView(id: String, name: String) = remoteSession.open(id, name)
    fun closeRemoteView() = remoteSession.close()
    fun resumeRemoteView() = remoteSession.resume()

    fun refresh() {
        perform("refresh", coalesce = true, work = {
            refreshDiscovered()
            _statuses.value = manager.refresh()
            refreshDiscovered()
            playerSession.devices(_statuses.value.filter {
                it.online && it.error == null && !it.certificateChanged && !it.keyRejected && !it.firmwareOutdated
            }.map { it.id })
            _storeError.value = null
            null
        })
    }

    fun onDiscovered(items: List<FoundDevice>) {
        latestDiscovered = items
        perform("discovery-update", coalesce = true, work = {
            refreshDiscovered()
            null
        })
    }

    private suspend fun refreshDiscovered() {
        val generation = ++discoveryGeneration
        val items = latestDiscovered
        val classified = manager.saveDiscovered(items)
        if (generation == discoveryGeneration && items == latestDiscovered) _found.value = classified
    }

    fun fingerprint(host: String, port: String, success: (Pair<HostAddress, String>) -> Unit) =
        perform("fingerprint", work = { manager.fingerprint(host, port) }, onSuccess = success)

    fun enroll(
        host: String,
        port: String,
        fingerprint: String,
        password: String,
        name: String,
        replaceId: String?,
        success: (String) -> Unit,
    ) = perform("enroll", work = {
        val id = manager.enroll(host, port, fingerprint, password, name, replaceId)
        refreshDiscovered()
        id
    }, onSuccess = {
        success(it)
        refresh()
    })

    fun forget(id: String) = perform("forget:$id", work = {
        manager.forget(id)
        refresh()
        null
    }, onSuccess = { _notice.value = "Removed from this manager. The device-side API key was not revoked." })

    fun setUrl(id: String, url: String, success: () -> Unit) = perform("device:$id", work = {
        manager.setUrl(id, url)
        null
    }, onSuccess = {
        _statuses.update { list -> list.map { if (it.id == id) it.copy(url = url) else it } }
        success()
        refresh()
        _notice.value = "Display address saved."
    })

    fun reboot(id: String) = perform("device:$id", work = {
        manager.reboot(id)
        null
    }, onSuccess = { _notice.value = "Device is restarting." })

    fun adminUrl(id: String, success: (String) -> Unit) =
        perform("device:$id", work = { manager.adminUrl(id) }, onSuccess = success)

    fun checkUpdate(id: String, success: (String?) -> Unit) =
        perform("device:$id", work = { manager.checkUpdate(id) }, onSuccess = success)

    fun installUpdate(id: String, success: (String) -> Unit) =
        perform("device:$id", work = { manager.installUpdate(id) }, onSuccess = {
            success(it)
            refresh()
        })

    fun debugKeys(id: String, success: (SshKeys) -> Unit) =
        perform("keys:$id", work = { manager.debugKeys(id) }, onSuccess = success)

    fun addDebugKey(id: String, key: String, name: String, success: (SshKeys) -> Unit) =
        perform("keys:$id", work = { manager.addDebugKey(id, key, name) }, onSuccess = success)

    fun removeDebugKey(id: String, keyId: String, success: (SshKeys) -> Unit) =
        perform("keys:$id", work = { manager.removeDebugKey(id, keyId) }, onSuccess = success)

    fun updateAll(success: (List<UpdateOutcome>) -> Unit) =
        perform("update-all", work = { manager.updateAll() }, onSuccess = success)

    fun createMusicSync(leaderId: String, followerIds: Set<String>, dissolveFirst: Boolean) =
        perform("music-sync", work = {
            _musicSyncResults.value = emptyList()
            val current = _statuses.value
            val leader = current.first { it.id == leaderId }
            val followers = current.filter { it.id in followerIds }
            require(followers.size == followerIds.size) { "A selected display is no longer enrolled. Refresh and try again." }
            val oldGroups = if (dissolveFirst) MusicSyncRules.reassignedGroups(current, followerIds + leaderId) else emptyList()
            manager.createMusicSync(leader, followers, oldGroups)
        }, onSuccess = {
            _musicSyncResults.value = it
            refresh()
        })

    fun dissolveMusicSync(group: MusicSyncGroup) = perform("music-sync", work = {
        _musicSyncResults.value = emptyList()
        manager.dissolveMusicSync(group)
    }, onSuccess = { _musicSyncResults.value = it; refresh() })

    fun removeMusicSyncFollower(follower: DeviceStatus) = perform("music-sync", work = {
        _musicSyncResults.value = emptyList()
        manager.removeMusicSyncFollower(follower)
    }, onSuccess = { _musicSyncResults.value = it; refresh() })

    fun dismissNotice() {
        _notice.value = null
    }

    fun reportNotice(message: String?) {
        _notice.value = message
    }

    private fun <T> perform(
        key: String,
        coalesce: Boolean = false,
        work: suspend () -> T,
        onSuccess: (T) -> Unit = {},
    ) {
        when (admission.admit(key, coalesce)) {
            WorkAdmission.Result.COALESCED -> return
            WorkAdmission.Result.REJECTED -> {
                _notice.value = "This operation is already running. Wait for it to finish before trying again."
                return
            }
            WorkAdmission.Result.START -> Unit
        }
        _busy.update { it + key }
        val job = viewModelScope.launch(start = CoroutineStart.LAZY) {
            try {
                do {
                    onSuccess(work())
                } while (admission.takeRepeat(key))
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                val message = error.message ?: "Request failed"
                if (error is StoreException) _storeError.value = message else _notice.value = message
            }
        }
        jobs += job
        job.invokeOnCompletion {
            jobs -= job
            admission.cancel(key)
            _busy.update { it - key }
        }
        job.start()
    }

    fun cancelNetwork() {
        playerSession.pause()
        remoteSession.pause()
        settingsEditor.pause()
        jobs.toList().forEach { it.cancel() }
        PinnedHttp.pauseNetwork()
    }

    override fun onCleared() {
        cancelNetwork()
    }
}
