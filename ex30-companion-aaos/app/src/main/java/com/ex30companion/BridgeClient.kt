package com.ex30companion

import android.content.Context
import android.os.Build
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedWriter
import java.io.IOException
import java.io.OutputStreamWriter
import java.net.InetSocketAddress
import java.net.Socket
import java.net.UnknownHostException
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
    private val port: Int = 7878,
    private val appVersion: String = BuildConfig.VERSION_NAME,
    private val parkedState: kotlinx.coroutines.flow.StateFlow<ParkedModeMonitor.State>? = null,
    private val navEnabledProvider: () -> Boolean = { false },
) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val sendQueue = Channel<String>(capacity = QUEUE_CAPACITY)
    // Ordered, generation-checked publisher for `type: "nav"` frames so
    // renewals, changes, and reconnect seeds never reorder an older cue after a newer one.
    private val navPublisher = NavFramePublisher()
    private val pendingChanges = mutableMapOf<String, Any>()
    private val pendingLock = Any()
    // Serializes direct writes (hello / goodbye / synthetic parked frame)
    // against the queued writer, so two threads can't interleave bytes
    // mid-line and corrupt both frames.
    private val writeLock = Any()
    private val socketRef = AtomicReference<Socket?>(null)
    private var started = false
    @Volatile private var lastSentMs: Long = 0L
    private val wifi = BridgeWifi(context, ::closeSocketQuietly)

    fun start(reconnect: Boolean) {
        val parked = parkedState?.value?.parked == true
        if (started) {
            if (reconnect) wifi.setAwake(!parked, userInitiated = true)
            return
        }
        started = true
        wifi.start(!parked, userInitiated = reconnect)
        scope.launch { observeRepository() }
        scope.launch { observeNavigation() }
        scope.launch { runCoalescer() }
        scope.launch { runHeartbeat() }
        scope.launch { runConnectionLoop() }
        scope.launch { observeParked(parked) }
    }

    fun stop() {
        // Detach the last socket before releasing the request. The shutdown
        // writer owns it until the best-effort goodbye has been sent.
        val lastSocket = socketRef.getAndSet(null)
        scope.cancel()
        if (started) wifi.stop()
        if (lastSocket != null) {
            CoroutineScope(Dispatchers.IO).launch {
                lastSocket.use { sendBestEffortGoodbye("app_destroyed", it) }
            }
        }
    }

    // --- Parked mode --------------------------------------------------------

    /**
     * Parked (driver left): flush the ignition frame that caused it, tell the
     * Pi goodbye, drop the socket AND the WiFi request so nothing on the head
     * unit holds a radio or wakelock for a locked car. Unparked: re-request
     * WiFi; the connection loop resumes and reseeds a full snapshot.
     */
    private suspend fun observeParked(initiallyParked: Boolean) {
        val flow = parkedState ?: return
        var wasParked = initiallyParked
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
                withContext(Dispatchers.Main.immediate) { wifi.setAwake(false) }
                drainQueue()
            } else {
                withContext(Dispatchers.Main.immediate) { wifi.setAwake(true) }
            }
        }
    }

    private fun drainQueue() {
        while (sendQueue.tryReceive().isSuccess) { /* drop stale frames */ }
        navPublisher.clearPending()
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

    /**
     * Emits standalone `{"type": "nav", ...}` frames via [navPublisher],
     * serializing renewals, live updates, and opt-out clears with a generation
     * check at staging time.
     */
    private suspend fun observeNavigation() {
        var wasOptedIn = false
        scope.launch {
            while (isActive) {
                delay(NAV_RENEW_MS)
                val optedIn = navEnabledProvider() && NavNotificationListenerService.isPermissionGranted(context)
                if (!optedIn) {
                    if (wasOptedIn || NavStateRepository.state.value.active) {
                        wasOptedIn = false
                        NavNotificationSelector.reset()
                        val cleared = NavStateRepository.clear()
                        if (socketRef.get() != null) {
                            navPublisher.stageOptOutClear(cleared)
                        }
                    }
                    continue
                }
                wasOptedIn = true
                if (!NavStateRepository.revalidate()) continue
                val snapshot = NavStateRepository.currentVersioned()
                if (socketRef.get() != null) {
                    navPublisher.stageRenewal(
                        versioned = snapshot,
                        latestRepoSeq = NavStateRepository.currentSeq(),
                        optedIn = true,
                    )
                }
            }
        }
        NavStateRepository.versionedState.collect { versioned ->
            val optedIn = navEnabledProvider() && NavNotificationListenerService.isPermissionGranted(context)
            if (socketRef.get() != null && (optedIn || wasOptedIn || !versioned.state.active)) {
                if (optedIn || wasOptedIn) {
                    navPublisher.stageChange(versioned, optedIn)
                }
            }
            wasOptedIn = optedIn
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
        wifi.route.collectLatest { route ->
            if (route == null) return@collectLatest
            var retryMs = RETRY_MIN_MS
            while (currentCoroutineContext().isActive) {
                parkedState?.first { !it.parked }
                if (connectAndWrite(route)) retryMs = RETRY_MIN_MS
                delay(retryMs)
                retryMs = (retryMs * 2).coerceAtMost(RETRY_MAX_MS)
            }
        }
    }

    private suspend fun connectAndWrite(route: BridgeWifi.Route): Boolean {
        // Resolve on the selected link and validate the actual address passed to
        // connect(). Never fall back to cellular DNS or a public destination.
        val address = try {
            route.network.getByName(route.host)
        } catch (_: UnknownHostException) {
            return false
        }
        if (!address.isSiteLocalAddress && !address.isLoopbackAddress) return false

        // Keep target construction before Socket() for the retail 12L release
        // build. On newer builds bind just this socket to the requested network.
        val target = InetSocketAddress(address, port)
        val socket = Socket()
        socketRef.set(socket)
        var connected = false
        try {
            if (wifi.route.value !== route) return false
            if (Build.VERSION.SDK_INT > 32) route.network.bindSocket(socket)
            socket.connect(target, CONNECT_TIMEOUT_MS)
            socket.tcpNoDelay = true
            socket.keepAlive = true
            connected = true
            writeHello(socket)
            seedInitialSnapshot()
            runWriter(socket)
        } catch (_: IOException) {
            // Lost route or Pi not listening yet.
        } finally {
            if (socketRef.compareAndSet(socket, null)) socket.close()
        }
        return connected
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
        // Discard any pre-connect nav frame and stage only the single latest
        // validated state after `hello` (including an inactive state when no route is active).
        navPublisher.clearPending()
        if (navEnabledProvider()) {
            val permitted = NavNotificationListenerService.isPermissionGranted(context)
            if (!permitted) {
                NavNotificationSelector.reset()
                NavStateRepository.clear()
            } else {
                NavStateRepository.revalidate()
            }
            val current = NavStateRepository.currentVersioned()
            navPublisher.stageReconnectSeed(
                versioned = if (permitted) current else VersionedNavState(current.seq, NavState()),
                latestRepoSeq = NavStateRepository.currentSeq(),
                optedIn = true,
            )
        }
    }

    private suspend fun runWriter(socket: Socket) = withContext(Dispatchers.IO) {
        val writer = BufferedWriter(OutputStreamWriter(socket.getOutputStream(), Charsets.UTF_8))
        while (true) {
            val navLine = navPublisher.pollLine()
            if (navLine != null) {
                if (socketRef.get() !== socket) return@withContext
                synchronized(writeLock) {
                    writer.write(navLine)
                    writer.write("\n")
                    writer.flush()
                }
                continue
            }
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

    private fun sendBestEffortGoodbye(reason: String, socket: Socket? = socketRef.get()) {
        if (socket == null) return
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
        private const val NAV_RENEW_MS = 5_000L
        private const val CONNECT_TIMEOUT_MS = 3_000
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
