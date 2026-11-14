package io.github.rbotha78.webdisplay.manager

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.Base64

class MusicSyncTest {
    private val groupId = "g".repeat(22)
    private val credentials = MusicSyncCredentials(groupId, "t".repeat(43))
    private val saved = Device("leader", "Saved Kitchen", "192.168.0.249", 9443, "ab".repeat(32), "api-key",
        listOf(HostAddress("kitchen.local", 8443), HostAddress("192.168.0.249", 9443)))
    private fun status(id: String, role: SyncRole = SyncRole.OFF, group: String = groupId) =
        DeviceStatus(id, id, "host", 8443, online = true, hostname = "$id-pi",
            musicSync = MusicSyncState(SyncSupport.SUPPORTED, role, if (role == SyncRole.OFF) "" else group))
    private fun response(role: String = "off") = JSONObject().put("supported", true).put("role", role)
        .put("group_id", if (role == "off") "" else groupId).put("leader", JSONObject.NULL)
        .put("members", JSONArray()).put("connected", true).put("error", "")

    @Test fun groupPlayerTargetsOnlyOnlineLeadersWithPlayerStatus() {
        val leader = status("leader", SyncRole.LEADER)
        val offline = status("offline", SyncRole.LEADER).copy(online = false)
        val unhealthy = status("unhealthy", SyncRole.LEADER)
        val follower = status("follower", SyncRole.FOLLOWER)
        val players = listOf("leader", "offline", "follower").associateWith { PlayerState() }
        assertEquals(listOf(leader), groupLeaders(listOf(follower, offline, unhealthy, leader), players))
    }

    @Test fun oldFirmwareIsUnsupportedEvenWithEmptyOrInvalidBody() {
        assertEquals(SyncSupport.UNSUPPORTED, MusicSyncRules.parseResponse(404, JSONObject()).support)
        assertEquals(SyncSupport.UNSUPPORTED, MusicSyncRules.parseResponse(404, response()).support)
        assertEquals(SyncSupport.UNSUPPORTED, MusicSyncRules.parseResponse(200, JSONObject().put("supported", false)).support)
    }

    @Test fun errorsAndMalformedResponsesAreUnknownNotOff() {
        assertEquals(SyncSupport.UNKNOWN, MusicSyncRules.parseResponse(503, response()).support)
        assertEquals(SyncSupport.UNKNOWN, MusicSyncRules.parseResponse(401, response()).support)
        assertEquals(SyncSupport.UNKNOWN, MusicSyncRules.parseResponse(200, JSONObject()).support)
        assertEquals(SyncSupport.UNKNOWN, MusicSyncRules.parseResponse(200, response("wrong")).support)
        assertEquals(SyncSupport.UNKNOWN, MusicSyncRules.parseResponse(200, response("leader").put("group_id", "short")).support)
        assertEquals(SyncSupport.UNKNOWN, MusicSyncRules.parseResponse(200, response("follower")).support)
    }

    @Test fun parsesFollowerConnectionAndLeaderMetadata() {
        val body = response("follower").put("connected", false).put("error", "Plex server mismatch")
            .put("leader", JSONObject().put("name", "Kitchen").put("hostname", "kitchen-pi").put("port", 9443)
                .put("addresses", JSONArray(listOf("192.168.0.249", "kitchen.local"))))
        val state = MusicSyncRules.parseResponse(200, body)
        assertEquals(SyncSupport.SUPPORTED, state.support)
        assertEquals(SyncRole.FOLLOWER, state.role)
        assertEquals(9443, state.leader!!.port)
        assertEquals(listOf("192.168.0.249", "kitchen.local"), state.leader.addresses)
        assertEquals("Music sync: following Kitchen — Plex server mismatch", MusicSyncRules.summary(state))
        assertEquals("Music sync: following Kitchen — Leader unreachable", MusicSyncRules.summary(state.copy(error = "")))
        assertEquals("Music sync: following Kitchen", MusicSyncRules.summary(state.copy(error = "", connected = true)))
    }

    @Test fun leadingCountsRecentlySeenMembersNotListeningMembers() {
        val body = response("leader").put("members", JSONArray()
            .put(JSONObject().put("name", "Lounge").put("listening", true).put("last_seen", 1794644765.5))
            .put(JSONObject().put("name", "Bedroom").put("listening", false).put("last_seen", 1794644770.0)))
        val state = MusicSyncRules.parseResponse(200, body)
        assertEquals(1794644765.5, state.members.first().lastSeen, 0.0)
        assertEquals("Music sync: leading (2 followers)", MusicSyncRules.summary(state))
        assertEquals("Music sync: off", MusicSyncRules.summary(MusicSyncRules.parseResponse(200, response())))
    }

    @Test fun payloadUsesSavedNamePrimaryFirstPinnedFingerprintAndLatestHostname() {
        val body = MusicSyncRules.followerPayload(credentials, saved, "latest-host")
        assertEquals(setOf("role", "group_id", "token", "leader"), body.keySet())
        assertEquals("follower", body.getString("role"))
        assertEquals(groupId, body.getString("group_id"))
        assertEquals(credentials.token, body.getString("token"))
        val leader = body.getJSONObject("leader")
        assertEquals(setOf("name", "hostname", "port", "addresses", "fingerprint"), leader.keySet())
        assertEquals("Saved Kitchen", leader.getString("name"))
        assertEquals("latest-host", leader.getString("hostname"))
        assertEquals(9443, leader.getInt("port"))
        assertEquals(listOf("192.168.0.249", "kitchen.local"), leader.getJSONArray("addresses").toList())
        assertEquals(List(32) { "AB" }.joinToString(":"), leader.getString("fingerprint"))
        assertFalse(body.toString().contains("api-key"))
    }

    @Test fun addressesAreDistinctCappedAtEightAndMissingLatestHostnameIsEmpty() {
        val leader = saved.copy(hostname = "stale", addresses = (1..12).map { HostAddress("display-$it.local", 8443) } + saved.addresses)
        val body = MusicSyncRules.followerPayload(credentials, leader, null).getJSONObject("leader")
        assertEquals("", body.getString("hostname"))
        assertEquals(listOf("192.168.0.249") + (1..7).map { "display-$it.local" }, body.getJSONArray("addresses").toList())
    }

    @Test fun offAndLeaderBodiesContainOnlyContractFields() {
        assertEquals(setOf("role"), MusicSyncRules.offPayload().keySet())
        assertEquals("off", MusicSyncRules.offPayload().getString("role"))
        val leader = MusicSyncRules.leaderPayload(credentials)
        assertEquals(setOf("role", "group_id", "token"), leader.keySet())
        assertEquals("leader", leader.getString("role"))
        assertEquals(credentials.token, leader.getString("token"))
        assertThrows(IllegalArgumentException::class.java) {
            MusicSyncRules.followerPayload(credentials, saved.copy(fingerprint = "invalid"), null)
        }
    }

    @Test fun randomCredentialsHaveExactEntropyLengthsAndAreRedacted() {
        val first = MusicSyncRules.credentials()
        val second = MusicSyncRules.credentials()
        assertTrue(first.groupId.matches(Regex("[A-Za-z0-9_-]{22}")))
        assertTrue(first.token.matches(Regex("[A-Za-z0-9_-]{43}")))
        assertEquals(16, Base64.getUrlDecoder().decode(first.groupId).size)
        assertEquals(32, Base64.getUrlDecoder().decode(first.token).size)
        assertNotEquals(first.groupId, second.groupId)
        assertNotEquals(first.token, second.token)
        assertFalse(first.toString().contains(first.token))
    }

    @Test fun groupsDeriveEnrolledLeadersAndFollowersByIdIncludingErrorsAndOrphans() {
        val leader = status("leader", SyncRole.LEADER)
        val follower = status("follower", SyncRole.FOLLOWER).let {
            it.copy(musicSync = it.musicSync.copy(connected = false, error = "Mismatch"))
        }
        val orphan = status("orphan", SyncRole.FOLLOWER, "other".repeat(4)).let {
            it.copy(musicSync = it.musicSync.copy(leader = SyncLeader("Unknown kitchen", "", 8443, listOf("host"))))
        }
        val groups = MusicSyncRules.groups(listOf(status("off"), orphan, follower, leader,
            status("unknown").copy(musicSync = MusicSyncState())))
        assertEquals(2, groups.size)
        val group = groups.first { it.id == groupId }
        assertEquals(listOf(leader), group.leaders)
        assertEquals(listOf(follower), group.followers)
        assertEquals("Mismatch", group.followers.single().musicSync.error)
        val missing = groups.first { it.id != groupId }
        assertTrue(missing.leaders.isEmpty())
        assertEquals("Unknown kitchen", missing.name)
        assertEquals(listOf(follower, leader), group.devices)
    }

    @Test fun reassignmentFindsEntireOldGroupNotOnlySelectedMembers() {
        val leader = status("leader", SyncRole.LEADER)
        val follower = status("follower", SyncRole.FOLLOWER)
        val groups = MusicSyncRules.reassignedGroups(listOf(leader, follower, status("off")), setOf("leader"))
        assertEquals(listOf(follower, leader), groups.single().devices)
        assertEquals(groups, MusicSyncRules.reassignedGroups(listOf(leader, follower), setOf("follower")))
        assertTrue(MusicSyncRules.reassignedGroups(listOf(leader, follower), setOf("off")).isEmpty())
    }

    @Test fun selectionDisablesOfflineUnsupportedUnknownAndUntrustedDevices() {
        val device = status("device")
        assertNull(MusicSyncRules.unavailableReason(device))
        assertEquals("Offline", MusicSyncRules.unavailableReason(device.copy(online = false)))
        assertTrue(MusicSyncRules.unavailableReason(device.copy(musicSync = MusicSyncState(SyncSupport.UNSUPPORTED)))!!.contains("update firmware"))
        assertNotNull(MusicSyncRules.unavailableReason(device.copy(musicSync = MusicSyncState())))
        assertNotNull(MusicSyncRules.unavailableReason(device.copy(certificateChanged = true)))
        assertNotNull(MusicSyncRules.unavailableReason(device.copy(keyRejected = true)))
        assertNotNull(MusicSyncRules.unavailableReason(device.copy(error = "Failed")))
    }

    @Test fun creationSendsLeaderFirstThenFollowersWithSameCredentials() = runTest {
        val calls = mutableListOf<Pair<String, JSONObject>>()
        val results = MusicSyncOperations { id, body -> calls += id to body }
            .create(status("leader"), listOf(status("f1"), status("f2")), saved, credentials)
        assertEquals(listOf("leader", "f1", "f2"), calls.map { it.first })
        assertEquals(listOf("leader", "follower", "follower"), calls.map { it.second.getString("role") })
        assertTrue(calls.all { it.second.getString("token") == credentials.token && it.second.getString("group_id") == groupId })
        assertTrue(results.all { it.success })
        assertFalse(results.toString().contains(credentials.token))
    }

    @Test fun leaderFailureAbortsAllFollowersAndSanitizesErrors() = runTest {
        val calls = mutableListOf<String>()
        val results = MusicSyncOperations { id, _ ->
            calls += id; throw DeviceRequestException("Validation failed: token ${credentials.token}", 400)
        }.create(status("leader"), listOf(status("f1"), status("f2")), saved, credentials)
        assertEquals(listOf("leader"), calls)
        assertEquals(3, results.size)
        assertTrue(results.none { it.success })
        assertTrue(results.first().outcome.contains("HTTP 400"))
        assertTrue(results.last().outcome.contains("Not attempted"))
        assertFalse(results.toString().contains(credentials.token))
    }

    @Test fun followerFailureReportsPartialGroupAndContinuesRemainingFollowers() = runTest {
        val calls = mutableListOf<String>()
        val results = MusicSyncOperations { id, _ ->
            calls += id; if (id == "f1") throw DeviceRequestException("Offline")
        }.create(status("leader"), listOf(status("f1"), status("f2")), saved, credentials)
        assertEquals(listOf("leader", "f1", "f2"), calls)
        assertEquals(listOf(true, false, true), results.map { it.success })
    }

    @Test fun dissolveSendsFollowersOffBeforeLeadersEvenAfterFailure() = runTest {
        val calls = mutableListOf<String>()
        val group = MusicSyncGroup(groupId, listOf(status("leader", SyncRole.LEADER)),
            listOf(status("f1", SyncRole.FOLLOWER), status("f2", SyncRole.FOLLOWER)))
        val results = MusicSyncOperations { id, body ->
            assertEquals(setOf("role"), body.keySet()); assertEquals("off", body.getString("role"))
            calls += id; if (id == "f1") throw DeviceRequestException("Offline")
        }.dissolve(group)
        assertEquals(listOf("f1", "f2", "leader"), calls)
        assertEquals(listOf(false, true, true), results.map { it.success })
    }

    @Test fun removalOnlyChangesChosenFollower() = runTest {
        val calls = mutableListOf<String>()
        val results = MusicSyncOperations { id, body -> calls += id; assertEquals("off", body.getString("role")) }
            .remove(status("follower", SyncRole.FOLLOWER))
        assertEquals(listOf("follower"), calls)
        assertTrue(results.single().success)
    }

    @Test fun dissolvingOldGroupFirstAbortsCreationOnAnyFailure() = runTest {
        val calls = mutableListOf<String>()
        val old = MusicSyncGroup(groupId, listOf(status("old", SyncRole.LEADER)), listOf(status("f1", SyncRole.FOLLOWER)))
        val results = MusicSyncOperations { id, body ->
            calls += "$id:${body.getString("role")}"; if (id == "f1") throw DeviceRequestException("Offline")
        }.createReplacing(status("leader"), listOf(status("f1")), saved, listOf(old))
        assertEquals(listOf("f1:off", "old:off"), calls)
        assertTrue(results.takeLast(2).all { !it.success && it.outcome.contains("Not attempted") })
    }

    @Test fun dissolvingOldGroupFirstClearsWholeGroupBeforeNewLeader() = runTest {
        val calls = mutableListOf<String>()
        val old = MusicSyncGroup(groupId, listOf(status("old", SyncRole.LEADER)), listOf(status("f1", SyncRole.FOLLOWER)))
        MusicSyncOperations { id, body -> calls += "$id:${body.getString("role")}" }
            .createReplacing(status("leader"), listOf(status("f1")), saved, listOf(old))
        assertEquals(listOf("f1:off", "old:off", "leader:leader", "f1:follower"), calls)
    }

    @Test fun cancellationDoesNotContinueToFollowers() = runTest {
        val calls = mutableListOf<String>()
        try {
            MusicSyncOperations { id, _ -> calls += id; throw CancellationException("Paused") }
                .create(status("leader"), listOf(status("f1")), saved, credentials)
            fail("Cancellation should propagate")
        } catch (_: CancellationException) { }
        assertEquals(listOf("leader"), calls)
    }
}
