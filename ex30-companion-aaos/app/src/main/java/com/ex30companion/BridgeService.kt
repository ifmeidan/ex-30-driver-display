package com.ex30companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Long-lived host for [CarPropertyService] (VHAL subscriptions) and
 * [BridgeClient] (Pi TCP transport). Foreground-promoted on start so the
 * pipeline survives `MainActivity` death and process trimming.
 *
 * The service has no UI and is therefore not subject to AAOS distraction
 * rules — the live-data list lives on the activity, gated by parked-state.
 */
class BridgeService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var carService: CarPropertyService? = null
    private var bridge: BridgeClient? = null
    private var parkedMonitor: ParkedModeMonitor? = null

    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, buildNotification())
        val monitor = ParkedModeMonitor(this, scope)
        parkedMonitor = monitor
        carService = CarPropertyService(this).also { it.start() }
        bridge = BridgeClient(
            this,
            hostProvider = { BridgeSettings.host(this) },
            parkedState = monitor.state,
        ).also { it.start() }
        // Parked flips also trim/restore the VHAL subscriptions themselves —
        // no 30 Hz power callbacks for a locked, empty car.
        scope.launch {
            monitor.state.collect { st -> carService?.setParked(st.parked) }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // START_STICKY: if Android kills us under memory pressure, restart
        // automatically with a null intent. Acceptable because every dependency
        // (host setting, repository) is global state, not request-scoped.
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        parkedMonitor?.release()
        parkedMonitor = null
        scope.cancel()
        bridge?.stop()
        bridge = null
        carService?.stop()
        carService = null
        super.onDestroy()
    }

    private fun buildNotification(): Notification {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.bridge_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = getString(R.string.bridge_channel_desc)
                setShowBadge(false)
            }
            nm.createNotificationChannel(channel)
        }
        val tap = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.bridge_notification_title))
            .setContentText(getString(R.string.bridge_notification_text))
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setContentIntent(tap)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "ex30_bridge_status"
        private const val NOTIFICATION_ID = 1

        fun start(context: Context) {
            val intent = Intent(context, BridgeService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
