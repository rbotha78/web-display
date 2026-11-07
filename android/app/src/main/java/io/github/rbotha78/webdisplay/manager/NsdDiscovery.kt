package io.github.rbotha78.webdisplay.manager

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper

class NsdDiscovery(
    context: Context,
    private val onDevices: (List<FoundDevice>) -> Unit,
    private val onError: (String?) -> Unit,
) {
    private val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    private val main = Handler(Looper.getMainLooper())
    private val resolves = ResolveQueue<NsdServiceInfo>()
    private var lock: WifiManager.MulticastLock? = null
    private var listener: NsdManager.DiscoveryListener? = null
    private val found = linkedMapOf<String, FoundDevice>()

    fun start() = onMain {
        if (listener != null) return@onMain
        resolves.start()
        val current = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) = Unit
            override fun onServiceFound(serviceInfo: NsdServiceInfo) = onMain {
                if (listener !== this) return@onMain
                val type = serviceInfo.serviceType.trimEnd('.').lowercase()
                if (type != "_web-display._tcp" && type != "_web-display._tcp.local") return@onMain
                val key = serviceKey(serviceInfo)
                if (key !in found) resolves.enqueue(key, serviceInfo)
                resolveNext()
            }
            override fun onServiceLost(serviceInfo: NsdServiceInfo) = onMain {
                if (listener !== this) return@onMain
                val key = serviceKey(serviceInfo)
                resolves.remove(key)
                if (found.remove(key) != null) onDevices(found.values.toList())
            }
            override fun onDiscoveryStopped(serviceType: String) = onMain {
                if (listener === this) clearDiscovery()
            }
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) = onMain {
                if (listener !== this) return@onMain
                onError("Could not start device discovery (NSD error $errorCode)")
                stop()
            }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) = onMain {
                if (listener !== this) return@onMain
                onError("Could not stop device discovery (NSD error $errorCode)")
                clearDiscovery()
            }
        }
        listener = current
        try {
            lock = wifi.createMulticastLock("web-display-manager-mdns").apply {
                setReferenceCounted(false)
                acquire()
            }
            nsd.discoverServices("_web-display._tcp.", NsdManager.PROTOCOL_DNS_SD, current)
        } catch (error: RuntimeException) {
            onError("Network discovery failed: ${error.message ?: "multicast DNS unavailable"}")
            stop()
        }
    }

    fun stop() = onMain {
        val current = listener
        clearDiscovery()
        if (current != null) {
            try {
                nsd.stopServiceDiscovery(current)
            } catch (error: RuntimeException) {
                onError("Could not stop device discovery: ${error.message ?: "NSD unavailable"}")
            }
        }
    }

    private fun clearDiscovery() {
        listener = null
        resolves.stop()
        found.clear()
        lock?.let { runCatching { if (it.isHeld) it.release() } }
        lock = null
        onDevices(emptyList())
    }

    private fun resolveNext() {
        val ticket = resolves.next() ?: return
        try {
            // Legacy NSD permits only one resolve at a time, including across stop/start.
            @Suppress("DEPRECATION")
            nsd.resolveService(ticket.value, object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) = onMain {
                    if (resolves.isCurrent(ticket)) {
                        onError("Could not resolve ${info.serviceName} (NSD error $errorCode). Rediscover or add it manually.")
                    }
                    resolves.complete(ticket)
                    resolveNext()
                }
                override fun onServiceResolved(resolved: NsdServiceInfo) = onMain {
                    if (resolves.isCurrent(ticket)) {
                        val address = resolved.host?.hostAddress
                        if (address == null || !isPrivateCandidate(address) || resolved.port !in 1..65535) {
                            onError("No usable IPv4 address was returned for ${resolved.serviceName}. Add it manually.")
                        } else {
                            found[ticket.key] = FoundDevice(resolved.serviceName, address, resolved.port)
                            onDevices(found.values.toList())
                        }
                    }
                    resolves.complete(ticket)
                    resolveNext()
                }
            })
        } catch (error: RuntimeException) {
            if (resolves.isCurrent(ticket)) {
                onError("Could not resolve ${ticket.value.serviceName}: ${error.message ?: "NSD unavailable"}")
            }
            resolves.complete(ticket)
            resolveNext()
        }
    }

    private fun serviceKey(info: NsdServiceInfo): String =
        info.serviceName + "|" + info.serviceType.trimEnd('.').removeSuffix(".local").lowercase()

    private fun onMain(action: () -> Unit) {
        if (Looper.myLooper() == main.looper) action() else main.post { action() }
    }

    private fun isPrivateCandidate(address: String): Boolean {
        val parts = address.split('.')
        if (parts.size != 4) return false
        val octets = parts.map { it.toIntOrNull() ?: return false }
        return octets.all { it in 0..255 } && octets[0] in 1..223 && octets[0] != 127 &&
            !(octets[0] == 169 && octets[1] == 254)
    }
}
