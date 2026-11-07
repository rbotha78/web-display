package io.github.rbotha78.webdisplay.manager

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class WorkflowSafetyTest {
    @Test
    fun rapidServiceAnnouncementsResolveSeriallyAndDeduplicate() {
        val queue = ResolveQueue<String>()
        queue.start()
        queue.enqueue("lobby", "Lobby Pi")
        queue.enqueue("lobby", "Duplicate announcement")
        queue.enqueue("kitchen", "Kitchen Pi")
        val lobby = requireNotNull(queue.next())
        assertEquals("Lobby Pi", lobby.value)
        assertNull(queue.next())
        queue.enqueue("lobby", "Duplicate active resolve")
        queue.complete(lobby)
        val kitchen = requireNotNull(queue.next())
        assertEquals("Kitchen Pi", kitchen.value)
        queue.complete(kitchen)
        assertNull(queue.next())
    }

    @Test
    fun stoppingInvalidatesCallbacksButKeepsTheResolverSlotUntilCompletion() {
        val queue = ResolveQueue<String>()
        queue.start()
        queue.enqueue("old", "Old Pi")
        val old = requireNotNull(queue.next())
        queue.enqueue("discarded", "Queued before stop")
        queue.stop()
        assertFalse(queue.isCurrent(old))
        queue.start()
        queue.enqueue("new", "New Pi")
        assertNull(queue.next())
        queue.complete(old)
        val new = requireNotNull(queue.next())
        assertTrue(queue.isCurrent(new))
        assertEquals("New Pi", new.value)
        queue.complete(old)
        assertNull(queue.next())
        queue.complete(new)
        assertNull(queue.next())
    }

    @Test
    fun serviceLossInvalidatesInFlightResolveAndDropsQueuedResolve() {
        val queue = ResolveQueue<String>()
        queue.start()
        queue.enqueue("first", "First")
        val first = requireNotNull(queue.next())
        queue.enqueue("second", "Second")
        queue.remove("first")
        queue.remove("second")
        assertFalse(queue.isCurrent(first))
        queue.complete(first)
        assertNull(queue.next())
    }

    @Test
    fun mutationsRejectDuplicateAdmissionUntilTheOwnerFinishesOrCancels() {
        val admission = WorkAdmission()
        assertEquals(WorkAdmission.Result.START, admission.admit("enroll", false))
        assertEquals(WorkAdmission.Result.REJECTED, admission.admit("enroll", false))
        assertEquals(WorkAdmission.Result.START, admission.admit("different-device", false))
        assertFalse(admission.takeRepeat("enroll"))
        assertEquals(WorkAdmission.Result.REJECTED, admission.admit("enroll", false))
        admission.cancel("enroll")
        assertEquals(WorkAdmission.Result.START, admission.admit("enroll", false))
        admission.cancel("enroll")
        assertEquals(WorkAdmission.Result.START, admission.admit("enroll", false))
    }

    @Test
    fun refreshAndDiscoveryCoalesceToOneFollowupUsingTheLatestSnapshot() {
        val admission = WorkAdmission()
        var latest = listOf("first")
        assertEquals(WorkAdmission.Result.START, admission.admit("discovery", true))
        val saved = mutableListOf(latest)
        latest = listOf("first", "second")
        assertEquals(WorkAdmission.Result.COALESCED, admission.admit("discovery", true))
        latest = listOf("third")
        assertEquals(WorkAdmission.Result.COALESCED, admission.admit("discovery", true))
        assertTrue(admission.takeRepeat("discovery"))
        saved += latest
        assertFalse(admission.takeRepeat("discovery"))
        assertEquals(listOf(listOf("first"), listOf("third")), saved)
    }

    @Test
    fun sshKeySuccessRequiresBooleanEnabledAndCompleteTypedRecords() {
        val valid = JSONObject(
            """{"enabled":true,"keys":[{"id":"key-1","type":"ssh-ed25519","comment":"","fingerprint":"SHA256:abc"}]}""",
        )
        assertEquals("key-1", ManagerRules.parseKeys(valid).keys.single().id)
        assertFalse(ManagerRules.parseKeys(JSONObject("""{"enabled":false,"keys":[]}""")).enabled)
        listOf(
            """{}""",
            """{"enabled":"false","keys":[]}""",
            """{"enabled":true,"keys":[{"id":"key-1"}]}""",
            """{"enabled":true,"keys":[null]}""",
            """{"enabled":true,"keys":[{"id":42,"type":"ssh-rsa","comment":"","fingerprint":"SHA256:abc"}]}""",
        ).forEach { malformed ->
            assertThrows(DeviceRequestException::class.java) { ManagerRules.parseKeys(JSONObject(malformed)) }
        }
    }
}
