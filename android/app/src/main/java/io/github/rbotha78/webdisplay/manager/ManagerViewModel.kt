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
    val found: StateFlow<List<FoundDevice>> = _found
    private val _busy = MutableStateFlow<Set<String>>(emptySet())
    val busy: StateFlow<Set<String>> = _busy
    private val _notice = MutableStateFlow<String?>(null)
    val notice: StateFlow<String?> = _notice
    private val _storeError = MutableStateFlow<String?>(null)
    val storeError: StateFlow<String?> = _storeError
    private val _managerVersion = MutableStateFlow("1.0.0")
    val managerVersion: StateFlow<String> = _managerVersion

    fun refresh() {
        perform("refresh", coalesce = true, work = {
            _statuses.value = manager.refresh()
            _storeError.value = null
            null
        })
    }

    fun onDiscovered(items: List<FoundDevice>) {
        _found.value = items
        perform("discovery-update", coalesce = true, work = {
            manager.saveDiscovered(_found.value)
            null
        })
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
        manager.enroll(host, port, fingerprint, password, name, replaceId)
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
        jobs.toList().forEach { it.cancel() }
        PinnedHttp.pauseNetwork()
    }

    override fun onCleared() {
        cancelNetwork()
    }
}
