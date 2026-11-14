package io.github.rbotha78.webdisplay.manager

import org.json.JSONObject

data class Device(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    val fingerprint: String,
    val key: String,
    val addresses: List<HostAddress> = listOf(HostAddress(host, port)),
    val hostname: String? = null,
)

data class HostAddress(val host: String, val port: Int)

data class FoundDevice(val name: String, val host: String, val port: Int, val enrolled: Boolean = false)

data class DeviceStatus(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    val online: Boolean = false,
    val version: String? = null,
    val hostname: String? = null,
    val addresses: List<String> = emptyList(),
    val url: String? = null,
    val internet: Boolean? = null,
    val defaultRoute: String? = null,
    val wifiConnected: Boolean? = null,
    val wifiSsid: String? = null,
    val installedVersion: String? = null,
    val restartNeeded: Boolean = false,
    val error: String? = null,
    val certificateChanged: Boolean = false,
    val keyRejected: Boolean = false,
    val firmwareOutdated: Boolean = false,
    val minFirmware: String? = null,
    val musicSync: MusicSyncState = MusicSyncState(),
)

data class SshKey(val id: String, val type: String, val comment: String, val fingerprint: String)

data class SshKeys(val enabled: Boolean, val keys: List<SshKey>)

object ManagerRules {
    const val DEFAULT_PORT = 8443
    const val MIN_FIRMWARE = "0.5.7"
    const val KEY_NAME = "Web Display Manager"

    private fun sameHostname(first: String?, second: String?): Boolean {
        fun normalized(value: String?) = value.orEmpty().trim().trimEnd('.').lowercase().removeSuffix(".local")
        val name = normalized(first)
        return name.isNotEmpty() && name == normalized(second)
    }

    fun matchesDiscovered(device: Device, found: FoundDevice): Boolean =
        (listOf(HostAddress(device.host, device.port)) + device.addresses).any {
            it.port == found.port && (
                sameHostname(it.host, found.host) || sameHostname(it.host, found.name) ||
                    sameHostname(device.hostname, found.name)
                )
        }

    fun markEnrolled(found: List<FoundDevice>, saved: List<Device>): List<FoundDevice> =
        found.map { item -> item.copy(enrolled = saved.any { matchesDiscovered(it, item) }) }

    fun normalizeFingerprint(value: String): String {
        val hex = value.replace(":", "").replace(" ", "").uppercase()
        require(hex.length == 64 && hex.all { it in "0123456789ABCDEF" }) {
            "A SHA-256 fingerprint has 64 hexadecimal digits"
        }
        return hex.chunked(2).joinToString(":")
    }

    fun deviceId(fingerprint: String): String =
        fingerprint.filter { it in "0123456789ABCDEFabcdef" }.take(12).lowercase()

    fun validateHost(host: String): String {
        val value = host.trim()
        val label = "[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        require(value.length in 1..253 && value.matches(Regex("$label(?:\\.$label)*"))) {
            "Enter a hostname or IPv4 address"
        }
        if (value.all { it.isDigit() || it == '.' }) {
            val octets = value.split('.')
            require(octets.size == 4 && octets.all { it.toIntOrNull() in 0..255 }) {
                "Enter a valid IPv4 address"
            }
        }
        return value
    }

    fun validatePort(port: String): Int {
        val value = port.toIntOrNull()
        require(value != null && value in 1..65535) { "Port must be 1-65535" }
        return value
    }

    fun validateName(name: String, fallback: String): String {
        val value = name.trim().ifEmpty { fallback }
        require(value.length in 1..40 && value.matches(Regex("[A-Za-z0-9 ._@-]+"))) {
            "Name must be 1-40 letters, digits, spaces or . _ @ -"
        }
        return value
    }

    fun firmwareTooOld(version: String?): Boolean {
        val installed = version?.let(::parseVersion) ?: return false
        val minimum = parseVersion(MIN_FIRMWARE) ?: return false
        val comparison = installed.zip(minimum).firstNotNullOfOrNull { (actual, required) ->
            (actual - required).takeIf { it != 0 }
        } ?: 0
        return comparison < 0
    }

    fun adminLoginToken(value: String): String {
        require(value.matches(Regex("[A-Za-z0-9_-]{43}"))) {
            "The device returned an invalid admin login link"
        }
        return value
    }

    fun shouldTryNextAddress(error: DeviceRequestException): Boolean = error.status == null

    fun parseKeys(json: JSONObject): SshKeys {
        try {
            val enabled = json.get("enabled") as? Boolean ?: error("Invalid enabled flag")
            val array = json.getJSONArray("keys")
            val keys = (0 until array.length()).map { index ->
                val item = array.getJSONObject(index)
                fun field(name: String): String = item.get(name) as? String ?: error("Invalid $name")
                SshKey(field("id"), field("type"), field("comment"), field("fingerprint")).also {
                    require(it.id.matches(Regex("[A-Za-z0-9_-]+")) &&
                        it.type.isNotBlank() && it.fingerprint.isNotBlank())
                }
            }
            return SshKeys(enabled, keys)
        } catch (_: Exception) {
            throw DeviceRequestException("The device returned an invalid SSH keys response")
        }
    }

    private fun parseVersion(value: String): List<Int>? {
        if (!value.matches(Regex("\\d+\\.\\d+\\.\\d+"))) return null
        return value.split('.').map { it.toIntOrNull() ?: return null }
    }

    fun candidateAddresses(device: Device, discovered: List<FoundDevice>): List<HostAddress> {
        val candidates = discovered.asSequence()
            .filter { sameHostname(device.hostname, it.name) }
            .map { HostAddress(it.host, it.port) }
        return (sequenceOf(device.addresses.firstOrNull() ?: HostAddress(device.host, device.port)) +
            device.addresses.asSequence() + candidates)
            .distinct()
            .take(9)
            .toList()
    }
}
