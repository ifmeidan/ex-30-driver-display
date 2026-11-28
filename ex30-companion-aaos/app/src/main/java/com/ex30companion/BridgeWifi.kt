package com.ex30companion

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Android owns auto-join. The bridge holds Wi-Fi and selects a route on the main thread. */
class BridgeWifi(private val context: Context, private val closeSocket: () -> Unit) {
    enum class Status {
        IDLE, WAKING, REQUESTING, AVAILABLE, UNAVAILABLE, PERMISSION_REQUIRED,
        WIFI_OFF, LOCATION_REQUIRED, LOCATION_OFF, SSID_HIDDEN
    }

    class Route(val network: Network, val host: String)

    private val currentRoute = MutableStateFlow<Route?>(null)
    val route = currentRoute.asStateFlow()
    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private val radio = context.applicationContext.getSystemService(WifiManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val afterWake = Runnable { request() }
    private var observer: ConnectivityManager.NetworkCallback? = null
    private var hold: ConnectivityManager.NetworkCallback? = null
    private var awake = false

    private val settingsChanges = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (!awake || isInitialStickyBroadcast || state.value == Status.WAKING) return
            when (intent.action) {
                WifiManager.WIFI_STATE_CHANGED_ACTION -> when (
                    intent.getIntExtra(WifiManager.EXTRA_WIFI_STATE, WifiManager.WIFI_STATE_UNKNOWN)
                ) {
                    WifiManager.WIFI_STATE_DISABLED -> request()
                    WifiManager.WIFI_STATE_ENABLED -> if (state.value == Status.WIFI_OFF) request()
                }
                LocationManager.MODE_CHANGED_ACTION -> if (BridgeSettings.ssid(context) != null) request()
            }
        }
    }

    fun start(active: Boolean, userInitiated: Boolean) {
        val filter = IntentFilter(WifiManager.WIFI_STATE_CHANGED_ACTION).apply {
            addAction(LocationManager.MODE_CHANGED_ACTION)
        }
        ContextCompat.registerReceiver(context, settingsChanges, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
        setAwake(active, userInitiated)
    }

    fun setAwake(active: Boolean, userInitiated: Boolean = false) {
        release()
        awake = active
        if (!awake || BridgeSettings.host(context) == null) return
        if (userInitiated) {
            request()
        } else {
            state.value = Status.WAKING
            Log.d(TAG, "Waiting 10 seconds before Wi-Fi connection")
            main.postDelayed(afterWake, WAKE_DELAY_MS)
        }
    }

    fun stop() {
        context.unregisterReceiver(settingsChanges)
        setAwake(false)
    }

    private fun request() {
        release()
        if (!awake) return
        val host = BridgeSettings.host(context) ?: return
        if (radio?.isWifiEnabled != true) {
            state.value = Status.WIFI_OFF
            return
        }
        val ssid = BridgeSettings.ssid(context)
        if (ssid != null && !canReadSsid(context)) {
            state.value = Status.LOCATION_REQUIRED
            return
        }
        if (ssid != null && !locationEnabled(context)) {
            state.value = Status.LOCATION_OFF
            return
        }

        val passwordProtected = ssid != null && BridgeSettings.hasPassphrase(context)
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        val flags = if (ssid == null) 0 else ConnectivityManager.NetworkCallback.FLAG_INCLUDE_LOCATION_INFO
        // A generic request holds the best Wi-Fi link, which may not be the Pi.
        // Observe all Wi-Fi links (including non-default, unvalidated links) independently.
        val cb = object : ConnectivityManager.NetworkCallback(flags) {
            private val matching = linkedSetOf<Network>()

            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                if (observer !== this) return
                val info = caps.transportInfo as? WifiInfo
                val reported = info?.ssid
                val matches = ssid == null || (info != null &&
                    matchesSavedWifi(ssid, passwordProtected, reported, info.currentSecurityType))
                if (matches) matching.add(network) else matching.remove(network)
                val hidden = ssid != null && (reported == null || reported == WifiManager.UNKNOWN_SSID)
                updateRoute(if (hidden) Status.SSID_HIDDEN else Status.REQUESTING)
            }

            override fun onLost(network: Network) {
                if (observer !== this) return
                matching.remove(network)
                updateRoute(Status.REQUESTING)
            }

            private fun updateRoute(waiting: Status) {
                if (currentRoute.value?.network in matching) return
                dropRoute()
                val network = matching.firstOrNull()
                if (network == null) {
                    state.value = waiting
                    return
                }
                // The retail EX30 Android 12L needs a process-bound plain Socket.
                if (Build.VERSION.SDK_INT == 32 && !cm.bindProcessToNetwork(network)) {
                    state.value = Status.UNAVAILABLE
                    return
                }
                currentRoute.value = Route(network, host)
                state.value = Status.AVAILABLE
                Log.d(TAG, "Bridge Wi-Fi route available")
            }
        }
        state.value = Status.REQUESTING
        try {
            cm.registerNetworkCallback(request, cb, main)
            observer = cb
            Log.d(TAG, "Watching for bridge Wi-Fi")
            val keepWifi = ConnectivityManager.NetworkCallback()
            try {
                cm.requestNetwork(request, keepWifi, main)
                hold = keepWifi
            } catch (_: SecurityException) {
                // Some OEMs allow only passive observation. Keep the listener.
            }
        } catch (_: SecurityException) {
            release()
            state.value = Status.PERMISSION_REQUIRED
        } catch (_: RuntimeException) {
            release()
            state.value = Status.UNAVAILABLE
        }
    }

    private fun dropRoute() {
        if (currentRoute.value == null) return
        currentRoute.value = null
        closeSocket()
        if (Build.VERSION.SDK_INT == 32) cm.bindProcessToNetwork(null)
    }

    private fun release() {
        main.removeCallbacks(afterWake)
        val registered = listOfNotNull(observer, hold)
        observer = null
        hold = null
        dropRoute()
        for (cb in registered) {
            try {
                cm.unregisterNetworkCallback(cb)
            } catch (_: IllegalArgumentException) {
                // Android may already have released the request.
            }
        }
        state.value = Status.IDLE
    }

    companion object {
        private const val TAG = "BridgeWifi"
        private const val WAKE_DELAY_MS = 10_000L
        private val state = MutableStateFlow(Status.IDLE)
        val status = state.asStateFlow()

        fun canReadSsid(context: Context): Boolean =
            listOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_BACKGROUND_LOCATION).all {
                ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
            }

        fun locationEnabled(context: Context): Boolean =
            context.getSystemService(LocationManager::class.java)?.isLocationEnabled == true
    }
}

// WifiInfo quotes UTF-8 SSIDs. The unquoted unknown sentinel and redacted security types must fail closed.
internal fun matchesSavedWifi(ssid: String, passwordProtected: Boolean, reported: String?, securityType: Int): Boolean {
    if (reported != "\"$ssid\"") return false
    return if (passwordProtected) {
        securityType == WifiInfo.SECURITY_TYPE_PSK || securityType == WifiInfo.SECURITY_TYPE_SAE
    } else {
        securityType == WifiInfo.SECURITY_TYPE_OPEN || securityType == WifiInfo.SECURITY_TYPE_OWE
    }
}
