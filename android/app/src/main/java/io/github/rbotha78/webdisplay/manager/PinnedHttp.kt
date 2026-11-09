package io.github.rbotha78.webdisplay.manager

import android.annotation.SuppressLint
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.Proxy
import java.net.URL
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

open class DeviceRequestException(message: String, val status: Int? = null) : Exception(message)
class CertificateChangedException : DeviceRequestException("The device certificate changed; it must be trusted again")

data class HttpReply(val status: Int, val json: JSONObject, val headers: Map<String, List<String>>)

object PinnedHttp {
    class RequestScope {
        private val connections = mutableSetOf<HttpsURLConnection>()
        private var cancelled = false

        @Synchronized
        internal fun track(connection: HttpsURLConnection) {
            if (cancelled) throw DeviceRequestException("Remote view closed")
            connections += connection
        }

        @Synchronized
        internal fun untrack(connection: HttpsURLConnection) {
            connections -= connection
        }

        fun cancel() {
            val active = synchronized(this) {
                cancelled = true
                connections.toList()
            }
            active.forEach { runCatching { it.disconnect() } }
        }
    }

    private const val MAX_RESPONSE = 1_048_576
    private val activeConnections = ConcurrentHashMap.newKeySet<HttpsURLConnection>()
    private val connectionLock = Any()
    @Volatile private var networkAllowed = false
    @SuppressLint("CustomX509TrustManager")
    private val previewTrust = object : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
    }

    fun fingerprint(host: String, port: Int): String {
        val connection = connection(host, port, null, "/health", 5_000)
        track(connection)
        try {
            connection.connect()
            val cert = connection.serverCertificates.firstOrNull() as? X509Certificate
                ?: throw DeviceRequestException("The device did not present a certificate")
            return formatFingerprint(cert.encoded)
        } finally {
            activeConnections -= connection
            connection.disconnect()
        }
    }

    fun request(
        host: String,
        port: Int,
        pin: String,
        method: String,
        path: String,
        body: JSONObject? = null,
        headers: Map<String, String> = emptyMap(),
        timeoutMs: Int = 10_000,
        requestScope: RequestScope? = null,
    ): HttpReply {
        val connection = connection(host, port, pin, path, timeoutMs)
        track(connection)
        try {
            requestScope?.track(connection)
            connection.requestMethod = method
            connection.setRequestProperty("Accept", "application/json")
            headers.forEach { (key, value) -> connection.setRequestProperty(key, value) }
            if (body != null) {
                val encoded = body.toString().toByteArray(Charsets.UTF_8)
                if (encoded.size > 65_536) throw DeviceRequestException("Request body exceeded 64 KB")
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(encoded) }
            }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            val bytes = stream?.use { input ->
                val output = ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                var total = 0
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    total += count
                    if (total > MAX_RESPONSE) throw DeviceRequestException("Device response exceeded 1 MB", status)
                    output.write(buffer, 0, count)
                }
                output.toByteArray()
            } ?: ByteArray(0)
            val json = ApiJson.decodeObject(bytes, status)
            val responseHeaders = connection.headerFields.filterKeys { it != null }
            return HttpReply(status, json, responseHeaders)
        } catch (error: DeviceRequestException) {
            throw error
        } catch (error: Exception) {
            if (error.causes().any { it is PinFailure }) throw CertificateChangedException()
            throw DeviceRequestException(error.message?.take(240) ?: "Could not reach $host:$port")
        } finally {
            requestScope?.untrack(connection)
            activeConnections -= connection
            connection.disconnect()
        }
    }

    fun cancelAll() {
        pauseNetwork()
    }

    fun resumeNetwork() {
        synchronized(connectionLock) { networkAllowed = true }
    }

    fun pauseNetwork() {
        synchronized(connectionLock) {
            networkAllowed = false
            activeConnections.toList().forEach { runCatching { it.disconnect() } }
        }
    }

    private fun track(connection: HttpsURLConnection) {
        synchronized(connectionLock) {
            if (!networkAllowed) throw DeviceRequestException("Network requests are paused")
            activeConnections += connection
        }
    }

    fun requireSuccess(reply: HttpReply): JSONObject {
        if (reply.status !in 200..299) {
            throw DeviceRequestException(reply.json.optString("error").ifBlank { "Device returned HTTP ${reply.status}" }, reply.status)
        }
        return reply.json
    }

    fun formatFingerprint(certificateDer: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(certificateDer).joinToString(":") {
            "%02X".format(it.toInt() and 0xff)
        }

    private fun connection(host: String, port: Int, pin: String?, path: String, timeoutMs: Int): HttpsURLConnection {
        val context = SSLContext.getInstance("TLSv1.2")
        val trustManager = if (pin == null) previewTrust else PinningTrustManager(pin)
        context.init(null, arrayOf<TrustManager>(trustManager), java.security.SecureRandom())
        val urlHost = if (host.contains(':') && !host.startsWith("[")) "[$host]" else host
        val connection = URL("https://$urlHost:$port$path").openConnection(Proxy.NO_PROXY) as HttpsURLConnection
        connection.sslSocketFactory = context.socketFactory
        connection.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.instanceFollowRedirects = false
        connection.useCaches = false
        connection.setRequestProperty("Connection", "close")
        return connection
    }

    private fun Throwable.causes(): Sequence<Throwable> = generateSequence(this) { it.cause }

    private class PinFailure : CertificateException()

    @SuppressLint("CustomX509TrustManager")
    private class PinningTrustManager(private val pin: String) : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
            throw CertificateException("Client certificates are not accepted")

        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            val cert = chain?.firstOrNull() ?: throw PinFailure()
            val expected = try {
                ManagerRules.normalizeFingerprint(pin)
            } catch (_: IllegalArgumentException) {
                throw PinFailure()
            }
            if (formatFingerprint(cert.encoded) != expected) throw PinFailure()
        }
    }

    internal object ApiJson {
        fun decodeObject(bytes: ByteArray, status: Int): JSONObject {
            if (bytes.isEmpty()) return JSONObject()
            return try {
                JSONObject(String(bytes, Charsets.UTF_8))
            } catch (_: Exception) {
                if (status >= 400) JSONObject()
                else throw DeviceRequestException("Device returned invalid JSON", status)
            }
        }
    }
}
