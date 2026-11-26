package com.ex30companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.security.GeneralSecurityException

/** Owns the bridge's one outstanding network request. All lifecycle and callback calls run on main. */
class BridgeWifi(private val context: Context, private val closeSocket: () -> Unit) {
    enum class Status {
        IDLE, REQUESTING, WAITING_TO_RETRY, AVAILABLE, UNAVAILABLE, PERMISSION_REQUIRED,
        WIFI_OFF, INVALID_CREDENTIAL
    }

    // Each request delivers a new route, even when Android reuses the same network ID.
    class Route(val network: Network, val host: String)

    private val currentRoute = MutableStateFlow<Route?>(null)
    val route = currentRoute.asStateFlow()
    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private val radio = context.applicationContext.getSystemService(WifiManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private var callback: ConnectivityManager.NetworkCallback? = null
    private var awake = false
    private var retryIndex = 0
    private val retry = Runnable { request(automatic = true) }

    private val radioChanges = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (!awake || isInitialStickyBroadcast) return
            when (intent.getIntExtra(WifiManager.EXTRA_WIFI_STATE, WifiManager.WIFI_STATE_UNKNOWN)) {
                WifiManager.WIFI_STATE_DISABLED -> {
                    release()
                    if (BridgeSettings.host(context) != null) state.value = Status.WIFI_OFF
                }
                WifiManager.WIFI_STATE_ENABLED -> if (state.value == Status.WIFI_OFF) setAwake(true)
            }
        }
    }

    fun start(active: Boolean, userInitiated: Boolean) {
        ContextCompat.registerReceiver(
            context, radioChanges, IntentFilter(WifiManager.WIFI_STATE_CHANGED_ACTION),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        setAwake(active, userInitiated)
    }

    fun setAwake(active: Boolean, userInitiated: Boolean = false) {
        awake = active
        retryIndex = 0
        request(automatic = !userInitiated)
    }

    fun stop() {
        context.unregisterReceiver(radioChanges)
        setAwake(false)
    }

    private fun request(automatic: Boolean) {
        release()
        if (!awake) return
        val host = BridgeSettings.host(context) ?: return
        if (radio?.isWifiEnabled != true) {
            state.value = Status.WIFI_OFF
            return
        }
        val wifi = try {
            BridgeSettings.wifi(context)
        } catch (_: GeneralSecurityException) {
            state.value = Status.INVALID_CREDENTIAL
            return
        }
        val builder = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        if (wifi != null) {
            val specifier = WifiNetworkSpecifier.Builder().setSsid(wifi.ssid)
            wifi.passphrase?.let { specifier.setWpa2Passphrase(it) }
            builder.setNetworkSpecifier(specifier.build())
        }
        val request = builder.build()
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                if (callback !== this) return
                dropRoute()
                // Preserve the retail EX30 Android 12L plain-Socket workaround.
                if (Build.VERSION.SDK_INT == 32 && !cm.bindProcessToNetwork(network)) {
                    state.value = Status.UNAVAILABLE
                    return
                }
                wifi?.let { BridgeSettings.recordConnection(context, it.ssid) }
                currentRoute.value = Route(network, host)
                state.value = Status.AVAILABLE
            }

            override fun onLost(network: Network) {
                if (callback !== this || currentRoute.value?.network != network) return
                dropRoute()
                state.value = Status.REQUESTING
                // An established local-only session ends when the AP disappears.
                // Submit a fresh exact request; Android remembers approved access points.
                if (wifi != null) setAwake(true)
            }

            override fun onUnavailable() {
                if (callback !== this) return
                release()
                // Android also reports this when a previously approved Pi takes longer
                // to boot than the system scan window. Only retry known profiles on
                // unattended starts. A failed setup or explicit retry stays dismissed.
                if (automatic && wifi != null && BridgeSettings.previouslyConnected(context) &&
                    retryIndex < RETRY_DELAYS_MS.size) {
                    state.value = Status.WAITING_TO_RETRY
                    main.postDelayed(retry, RETRY_DELAYS_MS[retryIndex++])
                } else {
                    state.value = Status.UNAVAILABLE
                }
            }
        }
        callback = cb
        state.value = Status.REQUESTING
        try {
            try {
                cm.requestNetwork(request, cb, main)
            } catch (e: SecurityException) {
                if (wifi != null) throw e
                // OEMs that deny an active generic request can still report the
                // Wi-Fi the driver joined manually in the original host-only setup.
                cm.registerNetworkCallback(request, cb, main)
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
        main.removeCallbacks(retry)
        val previous = callback
        callback = null
        dropRoute()
        if (previous != null) {
            try {
                cm.unregisterNetworkCallback(previous)
            } catch (_: IllegalArgumentException) {
                // An unavailable request may already have been removed by Android.
            }
        }
        state.value = Status.IDLE
    }

    companion object {
        private val RETRY_DELAYS_MS = longArrayOf(10_000L, 30_000L, 60_000L)
        private val state = MutableStateFlow(Status.IDLE)
        val status = state.asStateFlow()
    }
}
