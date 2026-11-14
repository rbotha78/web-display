package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.CancellationException
import org.json.JSONArray
import org.json.JSONObject
import java.security.SecureRandom
import java.util.Base64

enum class SyncSupport { UNKNOWN, UNSUPPORTED, SUPPORTED }
enum class SyncRole(val wire: String) { OFF("off"), LEADER("leader"), FOLLOWER("follower") }

data class SyncLeader(val name: String, val hostname: String, val port: Int, val addresses: List<String>)
data class SyncMember(val name: String, val listening: Boolean, val lastSeen: Double)
data class MusicSyncState(
    val support: SyncSupport = SyncSupport.UNKNOWN,
    val role: SyncRole = SyncRole.OFF,
    val groupId: String = "",
    val leader: SyncLeader? = null,
    val members: List<SyncMember> = emptyList(),
    val connected: Boolean = true,
    val error: String = "",
)

data class MusicSyncGroup(val id: String, val leaders: List<DeviceStatus>, val followers: List<DeviceStatus>) {
    val name: String get() = leaders.firstOrNull()?.name ?: followers.firstOrNull()?.musicSync?.leader?.name ?: "Unknown leader"
    val devices: List<DeviceStatus> get() = followers + leaders
}

data class MusicSyncOutcome(val id: String, val name: String, val outcome: String, val success: Boolean)

// Credentials exist only for the duration of a create operation, never in observable or saved state.
class MusicSyncCredentials(val groupId: String, val token: String) {
    override fun toString() = "MusicSyncCredentials(redacted)"
}

object MusicSyncRules {
    const val PATH = "/api/music/sync"
    const val HELP = "Followers play the leader's music; the leader's music settings apply and followers' settings are ignored. " +
        "All members must use the same Plex server. Controls on any display act on the whole group. " +
        "Volume, video audio and sleep are per display."

    fun credentials(random: SecureRandom = SecureRandom()): MusicSyncCredentials {
        fun encoded(size: Int): String = Base64.getUrlEncoder().withoutPadding()
            .encodeToString(ByteArray(size).also(random::nextBytes))
        return MusicSyncCredentials(encoded(16), encoded(32))
    }

    fun parseResponse(status: Int, body: JSONObject): MusicSyncState {
        if (status == 404) return MusicSyncState(SyncSupport.UNSUPPORTED)
        if (status !in 200..299) return MusicSyncState()
        return try {
            val supported = body.get("supported") as? Boolean ?: error("Invalid support")
            if (!supported) return MusicSyncState(SyncSupport.UNSUPPORTED)
            val role = SyncRole.entries.first { it.wire == body.getString("role") }
            val group = body.getString("group_id")
            require(if (role == SyncRole.OFF) group.isEmpty() else group.matches(Regex("[A-Za-z0-9_-]{16,64}")))
            val connected = body.get("connected") as? Boolean ?: error("Invalid connection")
            val error = body.get("error") as? String ?: error("Invalid error")
            val leader = if (role == SyncRole.FOLLOWER) {
                val item = body.getJSONObject("leader")
                val addresses = item.getJSONArray("addresses")
                SyncLeader(item.getString("name"), item.getString("hostname"), item.getInt("port"),
                    (0 until addresses.length()).map { addresses.getString(it) })
            } else null
            val array = body.getJSONArray("members")
            val members = (0 until array.length()).map { index ->
                val item = array.getJSONObject(index)
                SyncMember(item.getString("name"), item.getBoolean("listening"), item.getDouble("last_seen"))
            }
            MusicSyncState(SyncSupport.SUPPORTED, role, group, leader, members, connected, error)
        } catch (_: Exception) {
            MusicSyncState()
        }
    }

    fun followerError(state: MusicSyncState): String? = state.error.takeIf { it.isNotBlank() }
        ?: "Leader unreachable".takeIf { !state.connected }

    fun summary(state: MusicSyncState): String = "Music sync: " + when (state.support) {
        SyncSupport.UNKNOWN -> "unknown"
        SyncSupport.UNSUPPORTED -> "unsupported (update firmware)"
        SyncSupport.SUPPORTED -> when (state.role) {
            SyncRole.OFF -> "off"
            SyncRole.LEADER -> "leading (${state.members.size} followers)"
            SyncRole.FOLLOWER -> "following ${state.leader?.name ?: "Unknown leader"}" +
                (followerError(state)?.let { " — $it" } ?: "")
        }
    }

    fun unavailableReason(device: DeviceStatus): String? = when {
        !device.online -> "Offline"
        device.certificateChanged -> "Certificate must be trusted again"
        device.keyRejected -> "Enrol again"
        device.error != null -> "Device status unavailable"
        device.musicSync.support == SyncSupport.UNSUPPORTED -> "Unsupported — update firmware"
        device.musicSync.support == SyncSupport.UNKNOWN -> "Music sync status unknown — refresh"
        else -> null
    }

    fun groups(devices: List<DeviceStatus>): List<MusicSyncGroup> = devices.filter {
        it.musicSync.support == SyncSupport.SUPPORTED && it.musicSync.role != SyncRole.OFF && it.musicSync.groupId.isNotEmpty()
    }.groupBy { it.musicSync.groupId }.map { (id, members) ->
        MusicSyncGroup(id, members.filter { it.musicSync.role == SyncRole.LEADER },
            members.filter { it.musicSync.role == SyncRole.FOLLOWER })
    }.sortedBy { it.name.lowercase() }

    fun reassignedGroups(devices: List<DeviceStatus>, selectedIds: Set<String>): List<MusicSyncGroup> =
        groups(devices).filter { group -> group.devices.any { it.id in selectedIds } }

    fun offPayload(): JSONObject = JSONObject().put("role", "off")

    fun leaderPayload(credentials: MusicSyncCredentials): JSONObject = JSONObject()
        .put("role", "leader").put("group_id", credentials.groupId).put("token", credentials.token)

    fun followerPayload(credentials: MusicSyncCredentials, leader: Device, hostname: String?): JSONObject {
        val addresses = (listOf(leader.host) + leader.addresses.map { it.host }).distinct().take(8)
        require(addresses.isNotEmpty() && addresses.none { it.isBlank() })
        return JSONObject().put("role", "follower").put("group_id", credentials.groupId).put("token", credentials.token)
            .put("leader", JSONObject().put("name", leader.name).put("hostname", hostname.orEmpty())
                .put("port", leader.port).put("addresses", JSONArray(addresses))
                .put("fingerprint", ManagerRules.normalizeFingerprint(leader.fingerprint)))
    }
}

class MusicSyncOperations(private val put: suspend (String, JSONObject) -> Unit) {
    private suspend fun send(device: DeviceStatus, payload: JSONObject, action: String): MusicSyncOutcome = try {
        put(device.id, payload)
        MusicSyncOutcome(device.id, device.name, action, true)
    } catch (error: CancellationException) {
        throw error
    } catch (error: Exception) {
        // Do not echo response bodies or exception messages: they may contain submitted credentials.
        val detail = (error as? DeviceRequestException)?.status?.let { " (HTTP $it)" }.orEmpty()
        MusicSyncOutcome(device.id, device.name, "Failed$detail — refresh and retry", false)
    }

    suspend fun create(
        leader: DeviceStatus, followers: List<DeviceStatus>, savedLeader: Device,
        credentials: MusicSyncCredentials = MusicSyncRules.credentials(),
    ): List<MusicSyncOutcome> {
        require(savedLeader.id == leader.id)
        require(followers.isNotEmpty() && followers.none { it.id == leader.id } && followers.distinctBy { it.id }.size == followers.size)
        require((followers + leader).all { MusicSyncRules.unavailableReason(it) == null })
        val followerBody = MusicSyncRules.followerPayload(credentials, savedLeader, leader.hostname)
        val result = mutableListOf(send(leader, MusicSyncRules.leaderPayload(credentials), "Leading"))
        if (!result.first().success) {
            return result + followers.map { MusicSyncOutcome(it.id, it.name, "Not attempted — leader failed", false) }
        }
        followers.forEach { result += send(it, followerBody, "Following ${leader.name}") }
        return result
    }

    suspend fun createReplacing(
        leader: DeviceStatus, followers: List<DeviceStatus>, savedLeader: Device,
        dissolveFirst: List<MusicSyncGroup>,
    ): List<MusicSyncOutcome> {
        require(savedLeader.id == leader.id && followers.isNotEmpty() && followers.none { it.id == leader.id } &&
            followers.distinctBy { it.id }.size == followers.size)
        require((followers + leader).all { MusicSyncRules.unavailableReason(it) == null })
        // Validate the pinned leader information before changing any existing groups.
        val credentials = MusicSyncRules.credentials()
        MusicSyncRules.followerPayload(credentials, savedLeader, leader.hostname)
        val results = mutableListOf<MusicSyncOutcome>()
        for (group in dissolveFirst) results += dissolve(group)
        if (results.any { !it.success }) return results + (listOf(leader) + followers).map {
            MusicSyncOutcome(it.id, it.name, "Not attempted — old group could not be dissolved", false)
        }
        return results + create(leader, followers, savedLeader, credentials)
    }

    suspend fun dissolve(group: MusicSyncGroup): List<MusicSyncOutcome> = group.devices.map {
        send(it, MusicSyncRules.offPayload(), "Music sync off")
    }

    suspend fun remove(follower: DeviceStatus): List<MusicSyncOutcome> {
        require(follower.musicSync.role == SyncRole.FOLLOWER)
        return listOf(send(follower, MusicSyncRules.offPayload(), "Music sync off"))
    }
}
