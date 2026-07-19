package com.ex30companion

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedWriter
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.atomic.AtomicReference

/**
 * Pi bridge transport. One TCP connection at a time to `<host>:7878`,
 * line-delimited JSON, framing per `pi_bridge/protocol.md`.
 *
 * Read-only by construction: there's no path here that touches
 * CarPropertyManager.
 */
class BridgeClient(
    private val context: Context,
    private val hostProvider: () -> String?,
    private val port: Int = 7878,
    private val appVersion: String = BuildConfig.VERSION_NAME,
    private val parkedState: kotlinx.coroutines.flow.StateFlow<ParkedModeMonitor.State>? = null,
) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val sendQueue = Channel<String>(capacity = QUEUE_CAPACITY)
    private val pendingChanges = mutableMapOf<String, Any>()
    private val pendingLock = Any()
    // Serializes direct writes (hello / goodbye / synthetic parked frame)
    // against the queued writer, so two threads can't interleave bytes
    // mid-line and corrupt both frames.
    private val writeLock = Any()
    private val socketRef = AtomicReference<Socket?>(null)
    private var started = false
    @Volatile private var lastSentMs: Long = 0L
    @Volatile private var wifiHeld = false

    private val cm: ConnectivityManager =
        context.applicationContext.getSystemService(ConnectivityManager::class.java)

    // Wifi pinning, two mechanisms in tandem on the retail EX30
    // (Android Automotive 12L / API 32):
    //   1. requestNetwork — holds the no-internet ex30-pi WiFi for this app.
    //   2. bindProcessToNetwork in onAvailable — routes connect() via wlan0.
    // The connection loop suspends on this flow while WiFi is down and is
    // woken the moment onAvailable fires.
    private val wifiNetwork = MutableStateFlow<Network?>(null)
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            try { cm.bindProcessToNetwork(network) } catch (_: Throwable) {}
            wifiNetwork.value = network
        }
        override fun onLost(network: Network) {
            if (wifiNetwork.value == network) {
                wifiNetwork.value = null
                try { cm.bindProcessToNetwork(null) } catch (_: Throwable) {}
            }
            // Unblock the writer immediately instead of letting it pump a
            // dead socket's send buffer until TCP gives up.
            closeSocketQuietly()
        }
    }

    fun start() {
        if (started) return
        started = true
        registerWifiCallback()
        scope.launch { observeRepository() }
        scope.launch { runCoalescer() }
        scope.launch { runHeartbeat() }
        scope.launch { runConnectionLoop() }
        scope.launch { observeParked() }
    }

    fun stop() {
        scope.launch {
            sendBestEffortGoodbye("app_destroyed")
            try { cm.unregisterNetworkCallback(networkCallback) } catch (_: Throwable) {}
            try { cm.bindProcessToNetwork(null) } catch (_: Throwable) {}
            scope.cancel()
        }
    }

    private fun registerWifiCallback() {
        if (wifiHeld) return
        wifiHeld = true
        // removeCapability(INTERNET) is required: requestNetwork implies
        // INTERNET on some builds and the ex30-pi AP has none. Passive
        // registerNetworkCallback is the fallback where OEM policy blocks
        // requestNetwork.
        val req = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        try {
            cm.requestNetwork(req, networkCallback)
        } catch (_: Throwable) {
            try { cm.registerNetworkCallback(req, networkCallback) } catch (_: Throwable) {}
        }
    }

    private fun unregisterWifiCallback() {
        if (!wifiHeld) return
        wifiHeld = false
        try { cm.unregisterNetworkCallback(networkCallback) } catch (_: Throwable) {}
        try { cm.bindProcessToNetwork(null) } catch (_: Throwable) {}
        wifiNetwork.value = null
    }

    // --- Parked mode --------------------------------------------------------

    /**
     * Parked (driver left): flush the ignition frame that caused it, tell the
     * Pi goodbye, drop the socket AND the WiFi request so nothing on the head
     * unit holds a radio or wakelock for a locked car. Unparked: re-request
     * WiFi; the connection loop resumes and reseeds a full snapshot.
     */
    private suspend fun observeParked() {
        val flow = parkedState ?: return
        var wasParked = false
        flow.collect { st ->
            if (st.parked == wasParked) return@collect
            wasParked = st.parked
            if (st.parked) {
                // Give the coalescer time to put the ACC/OFF state frame on
                // the wire before we hang up — the Pi reacts to that frame.
                delay(PARK_FLUSH_MS)
                if (st.reason == "screen_off") {
                    // VHAL never flipped ignition — synthesize the parked
                    // state so the Pi still gets an explicit signal.
                    socketRef.get()?.let { s ->
                        try {
                            val frame = JSONObject().apply {
                                put("type", "state")
                                put("ts", System.currentTimeMillis())
                                put("src", "aaos")
                                put("fields", JSONObject(mapOf("ignition_state" to "OFF")))
                            }
                            writeLine(s, frame.toString())
                        } catch (_: Throwable) {}
                    }
                }
                sendBestEffortGoodbye("parked_" + st.reason)
                closeSocketQuietly()
                unregisterWifiCallback()
                drainQueue()
            } else {
                registerWifiCallback()
            }
        }
    }

    private fun drainQueue() {
        while (sendQueue.tryReceive().isSuccess) { /* drop stale frames */ }
        synchronized(pendingLock) { pendingChanges.clear() }
    }

    // --- Repository → pendingChanges ---------------------------------------

    private suspend fun observeRepository() {
        val lastSeen = mutableMapOf<Int, Any?>()
        VehicleStateRepository.state.collect { snapshot ->
            for ((propertyId, sample) in snapshot) {
                if (sample.status != PropertyStatus.OK) continue
                val raw = sample.value ?: continue
                if (lastSeen[propertyId] == raw) continue
                lastSeen[propertyId] = raw
                val wire = WireFieldMapper.map(propertyId, raw) ?: continue
                synchronized(pendingLock) {
                    pendingChanges[wire.field] = wire.value
                }
            }
        }
    }

    // --- Coalescer ----------------------------------------------------------

    private suspend fun runCoalescer() {
        while (scope.isActive) {
            delay(TICK_MS)
            val drained: Map<String, Any> = synchronized(pendingLock) {
                if (pendingChanges.isEmpty()) emptyMap()
                else pendingChanges.toMap().also { pendingChanges.clear() }
            }
            if (drained.isEmpty()) continue
            val frame = JSONObject().apply {
                put("type", "state")
                put("ts", System.currentTimeMillis())
                put("src", "aaos")
                put("fields", JSONObject(drained))
            }
            enqueue(frame.toString())
        }
    }

    private suspend fun runHeartbeat() {
        while (scope.isActive) {
            delay(HEARTBEAT_CHECK_MS)
            if (socketRef.get() == null) continue
            val idleFor = System.currentTimeMillis() - lastSentMs
            if (lastSentMs > 0L && idleFor >= HEARTBEAT_IDLE_MS) {
                val frame = JSONObject().apply {
                    put("type", "heartbeat")
                    put("ts", System.currentTimeMillis())
                }
                enqueue(frame.toString())
            }
        }
    }

    private fun enqueue(line: String) {
        // Drop-oldest on overflow keeps memory bounded if the socket stalls.
        while (true) {
            val result = sendQueue.trySend(line)
            if (result.isSuccess) return
            if (result.isClosed) return
            if (sendQueue.tryReceive().getOrNull() == null) return
        }
    }

    // --- Connection loop ----------------------------------------------------

    private suspend fun runConnectionLoop() {
        var retryMs = RETRY_MIN_MS
        while (scope.isActive) {
            // Parked: no reconnect attempts at all until the driver is back.
            parkedState?.let { flow ->
                if (flow.value.parked) {
                    flow.first { !it.parked }
                    retryMs = RETRY_MIN_MS
                }
            }
            val host = hostProvider().orEmpty().trim()
            if (host.isEmpty()) {
                delay(NO_HOST_POLL_MS)
                continue
            }
            // Don't burn retries while WiFi is down — suspend until the
            // ex30-pi AP is joined; onAvailable wakes this instantly.
            var net = wifiNetwork.value
            if (net == null) {
                net = wifiNetwork.first { it != null }
                retryMs = RETRY_MIN_MS
            }

            // Safety invariant (docs/safety_warranty.md): frames may only go
            // to a private-range (RFC1918) or loopback address — the bridge
            // must never be pointable at an internet host. Unresolvable
            // hosts are refused the same way (the Pi AP has no DNS anyway).
            val target = try {
                InetSocketAddress(host, port)
            } catch (_: Throwable) {
                null
            }
            val addr: InetAddress? = target?.address
            if (target == null || addr == null ||
                !(addr.isSiteLocalAddress || addr.isLoopbackAddress)
            ) {
                delay(NO_HOST_POLL_MS)
                continue
            }

            var connected = false
            try {
                // AAOS trap (hit on the retail EX30's 12L build): keep
                // `target` as an explicit local built BEFORE the Socket, and
                // never use Network.socketFactory — both variants pre-bind
                // in a way that zeroes the destination port on the wire
                // under R8 release builds (root-caused 2026-05-14,
                // v0.1.4/v0.1.7).
                val socket = Socket().apply {
                    connect(target, CONNECT_TIMEOUT_MS)
                    soTimeout = 0
                    tcpNoDelay = true
                    keepAlive = true
                }
                socketRef.set(socket)
                connected = true
                writeHello(socket)
                seedInitialSnapshot()
                runWriter(socket)
            } catch (_: Throwable) {
                // Fall through to retry.
            } finally {
                closeSocketQuietly()
            }

            // Sleep the backoff, but wake immediately if the WiFi network
            // (re)appears — e.g. the Pi AP just came up mid-backoff.
            val wokeOnNetwork = withTimeoutOrNull(retryMs) {
                wifiNetwork.first { it != null && it != net }
            } != null
            retryMs = if (connected || wokeOnNetwork) RETRY_MIN_MS
            else (retryMs * 2).coerceAtMost(RETRY_MAX_MS)
        }
    }

    private fun writeHello(socket: Socket) {
        val subscribed = JSONArray().apply {
            VehicleStateRepository.state.value
                .filterValues { it.status == PropertyStatus.OK }
                .keys
                .mapNotNull { id -> WireFieldMapper.map(id, sentinelFor(id)) }
                .map { it.field }
                .distinct()
                .forEach { put(it) }
        }
        val hello = JSONObject().apply {
            put("type", "hello")
            put("ts", System.currentTimeMillis())
            put("app_version", appVersion)
            put("schema_version", 1)
            put("fields_subscribed", subscribed)
        }
        writeLine(socket, hello.toString())
        lastSentMs = System.currentTimeMillis()
    }

    // Mapper needs *some* non-null raw to produce a Wire; only the field name
    // is used, so feed a type-correct sentinel.
    private fun sentinelFor(propertyId: Int): Any = when (propertyId) {
        android.car.VehiclePropertyIds.PARKING_BRAKE_ON,
        android.car.VehiclePropertyIds.EV_CHARGE_PORT_CONNECTED,
        android.car.VehiclePropertyIds.NIGHT_MODE -> false
        else -> 0
    }

    /**
     * On (re)connect, restage every currently-known OK value so the next
     * coalescer tick emits a full snapshot — the Pi must not be left
     * inferring state from delta frames it never received.
     */
    private fun seedInitialSnapshot() {
        synchronized(pendingLock) {
            for ((propertyId, sample) in VehicleStateRepository.state.value) {
                if (sample.status != PropertyStatus.OK) continue
                val raw = sample.value ?: continue
                val wire = WireFieldMapper.map(propertyId, raw) ?: continue
                pendingChanges[wire.field] = wire.value
            }
        }
    }

    private suspend fun runWriter(socket: Socket) = withContext(Dispatchers.IO) {
        val writer = BufferedWriter(OutputStreamWriter(socket.getOutputStream(), Charsets.UTF_8))
        while (true) {
            // Poll with a timeout instead of blocking forever on the channel:
            // when parked mode (or a network drop) swaps the socket out from
            // under us, this loop must end so the connection loop can get
            // back to its parked/WiFi gates instead of waiting for a frame
            // that may be hours away.
            val line = withTimeoutOrNull(WRITER_POLL_MS) { sendQueue.receive() }
            if (socketRef.get() !== socket) return@withContext
            if (line == null) continue
            synchronized(writeLock) {
                writer.write(line)
                writer.write("\n")
                writer.flush()
            }
            lastSentMs = System.currentTimeMillis()
        }
    }

    private fun writeLine(socket: Socket, line: String) {
        synchronized(writeLock) {
            val out = socket.getOutputStream()
            out.write((line + "\n").toByteArray(Charsets.UTF_8))
            out.flush()
        }
    }

    private fun sendBestEffortGoodbye(reason: String) {
        val socket = socketRef.get() ?: return
        try {
            val frame = JSONObject().apply {
                put("type", "goodbye")
                put("ts", System.currentTimeMillis())
                put("reason", reason)
            }
            writeLine(socket, frame.toString())
        } catch (_: Throwable) {
        }
    }

    private fun closeSocketQuietly() {
        val s = socketRef.getAndSet(null) ?: return
        try { s.close() } catch (_: Throwable) {}
    }

    companion object {
        // 33ms = ~30Hz, paced to the EV_BATTERY_INSTANTANEOUS_CHARGE_RATE cap
        // so the Pi power gauge moves smoothly with the pedal.
        private const val TICK_MS = 33L
        private const val HEARTBEAT_CHECK_MS = 1_000L
        private const val HEARTBEAT_IDLE_MS = 5_000L
        private const val CONNECT_TIMEOUT_MS = 3_000
        private const val NO_HOST_POLL_MS = 2_000L
        // Connection-refused on the local AP fails instantly, so fast retries
        // are cheap: the bridge connects within ~RETRY_MIN_MS of the Pi
        // daemon starting to listen.
        private const val RETRY_MIN_MS = 500L
        private const val RETRY_MAX_MS = 5_000L
        // ~2s of 30Hz frames of headroom for a brief socket stall.
        private const val QUEUE_CAPACITY = 192
        // Parked-mode: how long to let the coalescer/writer flush the final
        // ignition frame before goodbye + hangup. Several coalescer ticks.
        private const val PARK_FLUSH_MS = 400L
        // Writer wakes at least this often to notice a swapped-out socket.
        private const val WRITER_POLL_MS = 1_000L
    }
}
