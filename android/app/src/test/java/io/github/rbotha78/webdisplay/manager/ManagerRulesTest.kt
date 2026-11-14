package io.github.rbotha78.webdisplay.manager

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ManagerRulesTest {
    @Test
    fun fingerprintNormalizationRequiresExactlySha256Hex() {
        val compact = "a".repeat(64)
        val expected = compact.chunked(2).joinToString(":").uppercase()
        assertEquals(expected, ManagerRules.normalizeFingerprint(compact))
        assertEquals(expected, ManagerRules.normalizeFingerprint(compact.chunked(2).joinToString(":")))
        assertThrows(IllegalArgumentException::class.java) {
            ManagerRules.normalizeFingerprint("AA:BB")
        }
        assertThrows(IllegalArgumentException::class.java) {
            ManagerRules.normalizeFingerprint("G".repeat(64))
        }
    }

    @Test
    fun firmwareComparisonUsesNumericComponentsAndFailsOpenForUnknownVersions() {
        assertTrue(ManagerRules.firmwareTooOld("0.5.6"))
        assertFalse(ManagerRules.firmwareTooOld("0.5.7"))
        assertFalse(ManagerRules.firmwareTooOld("0.10.0"))
        assertFalse(ManagerRules.firmwareTooOld("not-a-version"))
        assertFalse(ManagerRules.firmwareTooOld(null))
    }

    @Test
    fun discoveredAddressesAreOnlyCandidatesForTheRememberedHostname() {
        val device = Device(
            id = "device", name = "Lobby", host = "192.168.1.10", port = 8443,
            fingerprint = "AA".repeat(32).chunked(2).joinToString(":"),
            key = "encrypted-at-rest-key",
            addresses = listOf(HostAddress("192.168.1.10", 8443), HostAddress("192.168.1.11", 8443)),
            hostname = "display-lobby",
        )
        val found = listOf(
            FoundDevice("DISPLAY-LOBBY.LOCAL.", "192.168.1.20", 8443),
            FoundDevice("other-device", "192.168.1.30", 8443),
        )
        assertEquals(
            listOf(
                HostAddress("192.168.1.10", 8443),
                HostAddress("192.168.1.11", 8443),
                HostAddress("192.168.1.20", 8443),
            ),
            ManagerRules.candidateAddresses(device, found),
        )
    }

    @Test
    fun discoveryUsesSavedAddressesAndHostnameInsteadOfStatusOrDisplayLabel() {
        val device = Device(
            id = "pi2", name = "Living room", host = "192.168.0.249", port = 8443,
            fingerprint = "AA".repeat(32), key = "test-key", hostname = "web-display-pi2",
            addresses = listOf(HostAddress("192.168.0.249", 8443), HostAddress("192.168.0.150", 8443)),
        )
        val found = listOf(
            FoundDevice("another-name", "192.168.0.150", 8443),
            FoundDevice("WEB-DISPLAY-PI2.LOCAL.", "192.168.0.151", 8443),
            FoundDevice("Living room", "192.168.0.152", 8443),
            FoundDevice("other-device", "192.168.0.153", 8443),
            FoundDevice("web-display-pi2", "192.168.0.249", 9000),
        )
        val classified = ManagerRules.markEnrolled(found, listOf(device))
        assertEquals(listOf(true, true, false, false, false), classified.map { it.enrolled })
        assertTrue(ManagerRules.markEnrolled(classified, emptyList()).none { it.enrolled })
    }

    @Test
    fun discoveryRecognizesSavedDnsAddressBeforeStatusHasLoaded() {
        val device = Device(
            id = "pi2", name = "Living room", host = "WEB-DISPLAY-PI2.local.", port = 8443,
            fingerprint = "AA".repeat(32), key = "test-key",
            addresses = listOf(HostAddress("192.168.0.249", 8443)),
        )
        val found = FoundDevice("web-display-pi2", "192.168.0.150", 8443)
        assertTrue(ManagerRules.markEnrolled(listOf(found), listOf(device)).single().enrolled)
    }

    @Test
    fun addressAndNameInputsAreValidatedBeforeNetworkUse() {
        assertEquals("display.local", ManagerRules.validateHost(" display.local "))
        assertEquals(8443, ManagerRules.validatePort("8443"))
        assertEquals("Lobby TV", ManagerRules.validateName("Lobby TV", "fallback"))
        assertThrows(IllegalArgumentException::class.java) { ManagerRules.validateHost("device/path") }
        assertThrows(IllegalArgumentException::class.java) { ManagerRules.validateHost("999.1.1.1") }
        assertThrows(IllegalArgumentException::class.java) { ManagerRules.validatePort("0") }
        assertThrows(IllegalArgumentException::class.java) { ManagerRules.validateName("name!", "fallback") }
    }

    @Test
    fun adminLoginTokenAndDeviceJsonAreValidatedRatherThanSilentlyAccepted() {
        val token = "A".repeat(43)
        assertEquals(token, ManagerRules.adminLoginToken(token))
        assertThrows(IllegalArgumentException::class.java) {
            ManagerRules.adminLoginToken("short")
        }
        assertTrue(PinnedHttp.ApiJson.decodeObject("""{"ok":true}""".toByteArray(), 200).getBoolean("ok"))
        assertThrows(DeviceRequestException::class.java) {
            PinnedHttp.ApiJson.decodeObject("""{"ok":""".toByteArray(), 200)
        }
        assertEquals(0, PinnedHttp.ApiJson.decodeObject("not-json".toByteArray(), 401).length())
    }

    @Test
    fun onlyConnectivityAndCertificateFailuresPermitAddressFallback() {
        assertTrue(ManagerRules.shouldTryNextAddress(DeviceRequestException("offline")))
        assertTrue(ManagerRules.shouldTryNextAddress(CertificateChangedException()))
        assertFalse(ManagerRules.shouldTryNextAddress(DeviceRequestException("key rejected", 401)))
    }
}
