package io.github.rbotha78.webdisplay.manager

import android.annotation.SuppressLint
import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class StoreException(message: String, cause: Throwable? = null) : Exception(message, cause)

class SecureDeviceStore(context: Context) {
    private val preferences = context.getSharedPreferences("secure_devices", Context.MODE_PRIVATE)
    private val alias = "web-display-manager-v1"

    @Synchronized
    fun load(): List<Device> {
        val blob = try {
            preferences.getString("devices", null)
        } catch (error: Exception) {
            throw StoreException("Saved device data is damaged or unavailable; it was not reset.", error)
        } ?: return emptyList()
        try {
            val encrypted = Base64.decode(blob, Base64.NO_WRAP)
            require(encrypted.size > 12) { "Encrypted device list is truncated" }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, encrypted.copyOfRange(0, 12)))
            val json = JSONObject(String(cipher.doFinal(encrypted.copyOfRange(12, encrypted.size)), Charsets.UTF_8))
            val array = json.getJSONArray("devices")
            return (0 until array.length()).map { parseDevice(array.getJSONObject(it)) }
        } catch (error: Exception) {
            throw StoreException("Saved device data is damaged or unavailable. It was not reset; remove app data only if you intend to forget all devices.", error)
        }
    }

    @Synchronized
    fun save(device: Device) {
        val devices = try {
            load().associateBy { it.id }.toMutableMap()
        } catch (error: StoreException) {
            throw error
        }
        val prior = devices[device.id]
        val addresses = (listOf(HostAddress(device.host, device.port)) +
            (prior?.addresses ?: device.addresses).filterNot { it == HostAddress(device.host, device.port) })
            .distinct().take(8)
        devices[device.id] = device.copy(addresses = addresses)
        write(devices.values.toList())
    }

    @Synchronized
    fun update(device: Device) {
        val devices = load().associateBy { it.id }.toMutableMap()
        if (devices.containsKey(device.id)) {
            devices[device.id] = device
            write(devices.values.toList())
        }
    }

    @Synchronized
    fun remove(id: String) {
        val devices = load().filterNot { it.id == id }
        write(devices)
    }

    @SuppressLint("UseKtx")
    private fun write(devices: List<Device>) {
        try {
            val array = JSONArray()
            devices.forEach { device ->
                val addresses = JSONArray()
                device.addresses.take(8).forEach {
                    addresses.put(JSONObject().put("host", it.host).put("port", it.port))
                }
                array.put(JSONObject().put("id", device.id).put("name", device.name)
                    .put("host", device.host).put("port", device.port)
                    .put("fingerprint", device.fingerprint).put("key", device.key)
                    .put("hostname", device.hostname).put("addresses", addresses))
            }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key())
            val encrypted = cipher.iv + cipher.doFinal(JSONObject().put("devices", array).toString().toByteArray(Charsets.UTF_8))
            check(preferences.edit().putString("devices", Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit())
        } catch (error: Exception) {
            throw StoreException("Could not securely save the device list.", error)
        }
    }

    private fun parseDevice(json: JSONObject): Device {
        val addressArray = json.getJSONArray("addresses")
        val addresses = (0 until addressArray.length()).map {
            val item = addressArray.getJSONObject(it)
            HostAddress(item.getString("host"), item.getInt("port"))
        }.take(8)
        val fingerprint = ManagerRules.normalizeFingerprint(json.getString("fingerprint"))
        val host = json.getString("host")
        val port = json.getInt("port")
        return Device(json.getString("id"), json.getString("name"), host, port, fingerprint,
            json.getString("key"), addresses.ifEmpty { listOf(HostAddress(host, port)) },
            json.optString("hostname").takeIf { it.isNotBlank() })
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build())
            generateKey()
        }
    }
}
