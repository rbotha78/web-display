package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

class DeviceManager(private val store: SecureDeviceStore) {
    private val updateLocks = ConcurrentHashMap<String, Mutex>()
    @Volatile private var discovered: List<FoundDevice> = emptyList()

    suspend fun devices(): List<Device> = io { store.load() }

    suspend fun forget(id: String) = io { store.remove(id) }

    suspend fun saveDiscovered(found: List<FoundDevice>): List<FoundDevice> {
        discovered = found
        val saved = devices()
        saved.forEach { device ->
            val candidates = found.filter { ManagerRules.matchesDiscovered(device, it) }
                .map { HostAddress(it.host, it.port) }
            val updated = (device.addresses + candidates).distinct().take(8)
            if (updated != device.addresses) io { store.update(device.copy(addresses = updated)) }
        }
        return ManagerRules.markEnrolled(found, saved)
    }

    suspend fun fingerprint(host: String, port: String): Pair<HostAddress, String> = io {
        val address = HostAddress(ManagerRules.validateHost(host), ManagerRules.validatePort(port))
        address to PinnedHttp.fingerprint(address.host, address.port)
    }

    suspend fun enroll(
        host: String,
        port: String,
        fingerprint: String,
        password: String,
        name: String,
        replaceId: String? = null,
    ): String {
        val requestJob = currentCoroutineContext()[Job]
        return io {
        val address = HostAddress(ManagerRules.validateHost(host), ManagerRules.validatePort(port))
        val pin = ManagerRules.normalizeFingerprint(fingerprint)
        val cleanName = ManagerRules.validateName(name, address.host)
        requestJob?.ensureActive()
        val health = PinnedHttp.requireSuccess(
            PinnedHttp.request(address.host, address.port, pin, "GET", "/health", timeoutMs = 10_000),
        )
        val version = health.optString("version").takeIf { it.isNotBlank() }
        if (ManagerRules.firmwareTooOld(version)) {
            throw DeviceRequestException(
                "This device runs firmware $version; the manager needs ${ManagerRules.MIN_FIRMWARE} or newer. Update the device first.",
                409,
            )
        }
        var sessionHeaders: Map<String, String> = emptyMap()
        var sessionStarted = false
        val key = try {
            requestJob?.ensureActive()
            val login = PinnedHttp.request(
                address.host, address.port, pin, "POST", "/api/login",
                JSONObject().put("password", password),
            )
            if (login.status == 401 || login.status == 429) {
                throw DeviceRequestException(login.json.optString("error").ifBlank { "Login failed" }, login.status)
            }
            if (login.status != 200 || !login.json.has("csrf")) {
                throw DeviceRequestException(login.json.optString("error").ifBlank { "Login failed" }, login.status)
            }
            val cookie = login.headers.entries.firstOrNull { it.key.equals("Set-Cookie", true) }
                ?.value?.firstOrNull()?.substringBefore(';')?.takeIf { it.startsWith("session=") }
                ?: throw DeviceRequestException("Login failed: session cookie was not returned")
            sessionHeaders = mapOf("Cookie" to cookie, "X-CSRF-Token" to login.json.getString("csrf"))
            sessionStarted = true
            requestJob?.ensureActive()
            val minted = PinnedHttp.request(
                address.host, address.port, pin, "POST", "/api/keys",
                JSONObject().put("name", ManagerRules.KEY_NAME), sessionHeaders,
            )
            if (minted.status != 201 || !minted.json.optString("key").isNotEmpty()) {
                throw DeviceRequestException(minted.json.optString("error").ifBlank { "Could not create an API key" }, minted.status)
            }
            minted.json.getString("key")
        } finally {
            if (sessionStarted && requestJob?.isActive != false) {
                runCatching {
                    PinnedHttp.request(address.host, address.port, pin, "POST", "/api/logout", JSONObject(), sessionHeaders)
                }
            }
        }
        val id = ManagerRules.deviceId(pin)
        val previous = (if (replaceId != null) store.load().firstOrNull { it.id == replaceId } else null)
            ?: store.load().firstOrNull { it.id == id }
        val addresses = (listOf(address) + (previous?.addresses ?: emptyList()))
            .distinct().take(8)
        store.save(Device(id, cleanName, address.host, address.port, pin, key, addresses, previous?.hostname))
        if (replaceId != null && replaceId != id) store.remove(replaceId)
        id
        }
    }

    suspend fun refresh(): List<DeviceStatus> {
        val saved = devices()
        val gate = Semaphore(4)
        return coroutineScope {
            saved.map { device ->
                async { gate.withPermit { summary(device) } }
            }.map { it.await() }.sortedBy { it.name.lowercase() }
        }
    }

    private suspend fun summary(device: Device): DeviceStatus {
        val requestJob = currentCoroutineContext()[Job]
        return io {
        var lastFailure: DeviceRequestException? = null
        val candidates = ManagerRules.candidateAddresses(device, discovered)
        var chosen: HostAddress? = null
        var health: JSONObject? = null
        for (address in candidates) {
            try {
                requestJob?.ensureActive()
                health = PinnedHttp.requireSuccess(
                    PinnedHttp.request(address.host, address.port, device.fingerprint, "GET", "/health",
                        headers = bearer(device.key), timeoutMs = 5_000),
                )
                chosen = address
                break
            } catch (error: DeviceRequestException) {
                if (error.status != null || lastFailure !is CertificateChangedException) {
                    lastFailure = error
                }
                if (!ManagerRules.shouldTryNextAddress(error)) break
            }
        }
        if (chosen == null || health == null) {
            val failure = lastFailure
            return@io DeviceStatus(
                device.id, device.name, device.host, device.port,
                error = failure?.message ?: "No known address answered",
                certificateChanged = failure is CertificateChangedException,
                keyRejected = failure?.status == 401,
            )
        }
        val host = chosen
        var current = device.copy(host = host.host, port = host.port,
            addresses = (listOf(host) + device.addresses.filterNot { it == host }).take(8))
        val version = health.optString("version").takeIf { it.isNotBlank() }
        requestJob?.ensureActive()
        val musicSync = try {
            val reply = PinnedHttp.request(current.host, current.port, current.fingerprint, "GET", MusicSyncRules.PATH,
                headers = bearer(current.key), timeoutMs = 5_000)
            MusicSyncRules.parseResponse(reply.status, reply.json)
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            MusicSyncState()
        }
        try {
            val status = call(current, "GET", "/api/status", requestJob)
            val hostname = status.optString("hostname").takeIf { it.isNotBlank() }
            val addressesJson = status.optJSONArray("addresses") ?: JSONArray()
            val reported = (0 until addressesJson.length()).mapNotNull { addressesJson.optString(it).takeIf(String::isNotBlank) }
                .filter { isUsableIpv4(it) }
                .map { HostAddress(it, current.port) }
            val learned = (current.addresses + reported).distinct().take(8)
            current = current.copy(addresses = learned, hostname = hostname ?: current.hostname)
            store.update(current)
            val config = call(current, "GET", "/api/config", requestJob = requestJob)
            val network = call(current, "GET", "/api/network", requestJob = requestJob)
            val internet = network.optJSONObject("internet")?.optBoolean("online")
            val wifiDevices = network.optJSONObject("wifi")?.optJSONArray("devices")
            val wifiConnected = wifiDevices?.let { list ->
                (0 until list.length()).any { index ->
                    val item = list.optJSONObject(index)
                    item?.optString("type") == "wifi" && item.optString("state") == "connected"
                }
            }
            val update = call(current, "GET", "/api/update/status", requestJob = requestJob)
            val installed = update.optString("installed_version").takeIf { it.isNotBlank() && it != "null" }
            val running = update.optString("running_version").takeIf { it.isNotBlank() && it != "null" }
            DeviceStatus(
                id = device.id, name = device.name, host = current.host, port = current.port,
                online = true, version = version, hostname = hostname,
                addresses = current.addresses.map { it.host }.distinct(),
                url = config.optString("url").takeIf { it.isNotBlank() },
                internet = internet, defaultRoute = network.optString("default_route").takeIf { it.isNotBlank() },
                wifiConnected = wifiConnected, installedVersion = installed,
                wifiSsid = network.optJSONObject("wifi")?.optString("saved_ssid")?.takeIf { it.isNotBlank() },
                restartNeeded = installed != null && running != null && installed != running,
                firmwareOutdated = ManagerRules.firmwareTooOld(version),
                minFirmware = if (ManagerRules.firmwareTooOld(version)) ManagerRules.MIN_FIRMWARE else null,
                musicSync = musicSync,
            )
        } catch (error: DeviceRequestException) {
            DeviceStatus(
                id = device.id, name = device.name, host = current.host, port = current.port,
                error = error.message, online = true, version = version, keyRejected = error.status == 401,
                certificateChanged = error is CertificateChangedException,
                firmwareOutdated = ManagerRules.firmwareTooOld(version),
                minFirmware = if (ManagerRules.firmwareTooOld(version)) ManagerRules.MIN_FIRMWARE else null,
                musicSync = musicSync,
            )
        }
        }
    }

    private val syncOperations = MusicSyncOperations { id, body ->
        deviceAction(id) { device ->
            val reply = PinnedHttp.request(device.host, device.port, device.fingerprint,
                "PUT", MusicSyncRules.PATH, body, bearer(device.key))
            if (reply.status !in 200..299) throw DeviceRequestException("Music sync request failed", reply.status)
        }
    }

    suspend fun createMusicSync(
        leader: DeviceStatus, followers: List<DeviceStatus>, dissolveFirst: List<MusicSyncGroup>,
    ): List<MusicSyncOutcome> {
        val saved = devices().firstOrNull { it.id == leader.id } ?: throw DeviceRequestException("Unknown leader")
        return syncOperations.createReplacing(leader, followers, saved, dissolveFirst)
    }

    suspend fun dissolveMusicSync(group: MusicSyncGroup) = syncOperations.dissolve(group)
    suspend fun removeMusicSyncFollower(follower: DeviceStatus) = syncOperations.remove(follower)

    suspend fun setUrl(id: String, url: String) = deviceAction(id) { device ->
        PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
            "PUT", "/api/config", JSONObject().put("url", url), bearer(device.key)))
    }

    suspend fun reboot(id: String) = deviceAction(id) { device ->
        PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
            "POST", "/api/reboot", JSONObject(), bearer(device.key)))
    }

    suspend fun remoteView(id: String, requests: PinnedHttp.RequestScope): RemoteSnapshot =
        deviceAction(id, requests) { device ->
            val reply = PinnedHttp.request(
                device.host, device.port, device.fingerprint, "GET", "/api/remote-view",
                headers = bearer(device.key), requestScope = requests,
            )
            if (reply.status == 404) throw DeviceRequestException(
                "Remote view needs newer device software. Update the device to use this feature.", 404,
            )
            RemoteViewParser.parse(PinnedHttp.requireSuccess(reply), android.os.SystemClock.elapsedRealtime())
        }

    suspend fun adminUrl(id: String): String = deviceAction(id) { device ->
        val reply = PinnedHttp.request(device.host, device.port, device.fingerprint, "POST",
            "/api/admin-login-token", JSONObject(), bearer(device.key))
        if (reply.status == 404) throw DeviceRequestException(
            "Automatic login needs newer device software. Open the admin page manually or update the device.",
        )
        val body = PinnedHttp.requireSuccess(reply)
        val token = ManagerRules.adminLoginToken(body.optString("token"))
        val host = if (device.host.contains(':') && !device.host.startsWith("[")) "[${device.host}]" else device.host
        "https://$host:${device.port}/#admin-login=$token"
    }

    suspend fun checkUpdate(id: String): String? = deviceAction(id) { device ->
        val body = PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
            "POST", "/api/update/check", JSONObject(), bearer(device.key), 30_000))
        if (body.optBoolean("available")) body.optJSONObject("latest")?.optString("version")?.takeIf { it.isNotBlank() } else null
    }

    suspend fun installUpdate(id: String): String {
        val lock = updateLocks.computeIfAbsent(id) { Mutex() }
        if (!lock.tryLock()) throw DeviceRequestException("An update is already running for this device")
        try {
            return deviceAction(id) { device ->
            val body = PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
                "POST", "/api/update/install", JSONObject(), bearer(device.key), 120_000))
            body.optString("version").takeIf { it.isNotBlank() }
                ?: throw DeviceRequestException("The device did not report the installed version")
            }
        } finally {
            lock.unlock()
        }
    }

    suspend fun debugKeys(id: String): SshKeys = deviceAction(id) { device ->
        parseKeys(PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
            "GET", "/api/debug-keys", headers = bearer(device.key))))
    }

    suspend fun addDebugKey(id: String, key: String, name: String): SshKeys = deviceAction(id) { device ->
        val body = JSONObject().put("key", key).also { if (name.isNotBlank()) it.put("name", name) }
        parseKeys(PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
            "POST", "/api/debug-keys", body, bearer(device.key), 60_000)))
    }

    suspend fun removeDebugKey(id: String, keyId: String): SshKeys = deviceAction(id) { device ->
        require(keyId.matches(Regex("[A-Za-z0-9_-]+"))) { "Invalid SSH key identifier" }
        parseKeys(PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
            "DELETE", "/api/debug-keys/$keyId", headers = bearer(device.key), timeoutMs = 60_000)))
    }

    suspend fun settingsIdentity(id: String): Pair<String, String?> = io {
        val device = store.load().firstOrNull { it.id == id } ?: throw DeviceRequestException("Unknown device")
        device.fingerprint to device.hostname
    }

    /**
     * Reads several settings endpoints over one verified connection. Per-endpoint HTTP errors are
     * returned so older firmware can report unsupported sections; transport, certificate and key
     * failures abort the whole read.
     */
    suspend fun fetchSettings(
        id: String, paths: List<String>, requests: PinnedHttp.RequestScope,
    ): Map<String, Result<JSONObject>> {
        val requestJob = currentCoroutineContext()[Job]
        return deviceAction(id, requests) { device ->
            paths.associateWith { path ->
                requestJob?.ensureActive()
                val reply = PinnedHttp.request(device.host, device.port, device.fingerprint, "GET", path,
                    headers = bearer(device.key), timeoutMs = 30_000, requestScope = requests)
                if (reply.status == 401) throw DeviceRequestException(
                    "The device rejected this manager's key. Enrol the device again.", 401,
                )
                try {
                    Result.success(PinnedHttp.requireSuccess(reply))
                } catch (error: DeviceRequestException) {
                    Result.failure(error)
                }
            }
        }
    }

    suspend fun sendSettings(
        id: String, method: String, path: String, body: JSONObject?, timeoutMs: Int,
    ): JSONObject = deviceAction(id) { device ->
        PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
            method, path, body, bearer(device.key), timeoutMs))
    }

    suspend fun playerStatus(id: String, requests: PinnedHttp.RequestScope): Map<String, Result<JSONObject>> {
        val requestJob = currentCoroutineContext()[Job]
        return deviceAction(id, requests) { device ->
            listOf(PlayerProtocol.MUSIC, PlayerProtocol.CAROUSEL).associateWith { path ->
                requestJob?.ensureActive()
                val reply = PinnedHttp.request(device.host, device.port, device.fingerprint, "GET", path,
                    headers = bearer(device.key), timeoutMs = 5_000, requestScope = requests)
                if (reply.status == 401) throw DeviceRequestException(
                    "The device rejected this manager's key. Enrol the device again.", 401,
                )
                try { Result.success(PinnedHttp.requireSuccess(reply)) }
                catch (error: DeviceRequestException) { Result.failure(error) }
            }
        }
    }

    suspend fun playerControl(id: String, command: PlayerCommand, requests: PinnedHttp.RequestScope) {
        val (path, body) = PlayerProtocol.request(command)
        deviceAction(id, requests) { device ->
            PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
                "POST", path, body, bearer(device.key), timeoutMs = 10_000, requestScope = requests))
        }
    }

    suspend fun updateAll(): List<UpdateOutcome> {
        val saved = devices()
        val gate = Semaphore(4)
        return coroutineScope {
            saved.map { device -> async {
                gate.withPermit {
                    try {
                        val checked = deviceAction(device.id) { current ->
                            PinnedHttp.requireSuccess(PinnedHttp.request(current.host, current.port, current.fingerprint,
                                "POST", "/api/update/check", JSONObject(), bearer(current.key), 30_000))
                        }
                        if (!checked.optBoolean("available")) UpdateOutcome(device.id, device.name, "up to date")
                        else {
                            val version = installUpdate(device.id)
                            UpdateOutcome(device.id, device.name, "installed", version)
                        }
                    } catch (error: CancellationException) {
                        throw error
                    } catch (error: Exception) {
                        UpdateOutcome(device.id, device.name, "failed", error = error.message ?: "Update failed")
                    }
                }
            } }.map { it.await() }
        }
    }

    private suspend fun <T> deviceAction(
        id: String, requests: PinnedHttp.RequestScope? = null, action: (Device) -> T,
    ): T {
        val requestJob = currentCoroutineContext()[Job]
        return withContext(Dispatchers.IO) {
        requestJob?.ensureActive()
        val saved = store.load().firstOrNull { it.id == id } ?: throw DeviceRequestException("Unknown device")
        val device = connect(saved, requestJob, requests)
        requestJob?.ensureActive()
        action(device)
        }
    }

    private fun connect(device: Device, requestJob: Job?, requests: PinnedHttp.RequestScope? = null): Device {
        var failure: DeviceRequestException? = null
        for (address in ManagerRules.candidateAddresses(device, discovered)) {
            try {
                requestJob?.ensureActive()
                PinnedHttp.requireSuccess(
                    PinnedHttp.request(address.host, address.port, device.fingerprint, "GET", "/health",
                        headers = bearer(device.key), timeoutMs = 5_000, requestScope = requests),
                )
                val promoted = device.copy(
                    host = address.host,
                    port = address.port,
                    addresses = (listOf(address) + device.addresses.filterNot { it == address }).take(8),
                )
                if (promoted.host != device.host || promoted.port != device.port || promoted.addresses != device.addresses) {
                    store.update(promoted)
                }
                return promoted
            } catch (error: DeviceRequestException) {
                if (error.status != null || failure !is CertificateChangedException) failure = error
                if (!ManagerRules.shouldTryNextAddress(error)) break
            }
        }
        throw failure ?: DeviceRequestException("No known address answered")
    }

    private fun call(
        device: Device,
        method: String,
        path: String,
        requestJob: Job? = null,
        body: JSONObject? = null,
    ): JSONObject {
        requestJob?.ensureActive()
        return PinnedHttp.requireSuccess(PinnedHttp.request(device.host, device.port, device.fingerprint,
            method, path, body, bearer(device.key)))
    }

    private fun parseKeys(json: JSONObject): SshKeys {
        return ManagerRules.parseKeys(json)
    }

    private fun bearer(key: String) = mapOf("Authorization" to "Bearer $key")

    private fun isUsableIpv4(value: String): Boolean {
        val octets = value.split('.')
        if (octets.size != 4) return false
        val numbers = octets.map { it.toIntOrNull() ?: return false }
        return numbers.all { it in 0..255 } && numbers[0] != 127 &&
            !(numbers[0] == 169 && numbers[1] == 254) &&
            !(numbers[0] == 0) && numbers[0] < 224
    }

    private suspend fun <T> io(action: () -> T): T = withContext(Dispatchers.IO) { action() }
}

data class UpdateOutcome(val id: String, val name: String, val outcome: String, val version: String? = null, val error: String? = null)
