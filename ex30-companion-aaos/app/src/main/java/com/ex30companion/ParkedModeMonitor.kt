package com.ex30companion

import android.car.VehiclePropertyIds
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.PowerManager
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * Decides when the driver has left the car, so the bridge can go quiet
 * instead of streaming 30 Hz frames at a locked, empty vehicle (which held
 * WiFi and CPU awake and, via the Pi's OBD polling, kept the whole car from
 * sleeping — root-caused 2026-07-17).
 *
 * Primary signal — EX30 retail VHAL ignition, verified 2026-07-17 by
 * correlating lock/unlock times against the bridge JSONL on the Pi:
 *   ON    = car unlocked/opened (someone is there, even before READY)
 *   ACC   = car locked, vehicle network still awake (wind-down)
 *   OFF/LOCK = never delivered; the head unit suspends first
 *
 * Backup signal: the head-unit display turning off with no ignition change
 * (VHAL glitch insurance) parks the bridge the same way.
 *
 * Exception: while the charge port is connected the bridge stays up so the
 * Pi can render the charging screen; the head unit's own power policy
 * decides when everything actually sleeps.
 */
class ParkedModeMonitor(private val context: Context, scope: CoroutineScope) {

    data class State(val parked: Boolean, val reason: String)

    private val _state = MutableStateFlow(State(parked = false, reason = "init"))
    val state: StateFlow<State> = _state

    @Volatile private var screenOn: Boolean = true
    @Volatile private var ignition: String = ""
    @Volatile private var charging: Boolean = false

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, intent: Intent?) {
            when (intent?.action) {
                Intent.ACTION_SCREEN_ON -> { screenOn = true; recompute() }
                Intent.ACTION_SCREEN_OFF -> { screenOn = false; recompute() }
            }
        }
    }

    init {
        screenOn = context.getSystemService(PowerManager::class.java)
            ?.isInteractive ?: true
        ContextCompat.registerReceiver(
            context,
            screenReceiver,
            IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_ON)
                addAction(Intent.ACTION_SCREEN_OFF)
            },
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        scope.launch {
            VehicleStateRepository.state.collect { snapshot ->
                snapshot[VehiclePropertyIds.IGNITION_STATE]?.let { s ->
                    if (s.status == PropertyStatus.OK && s.value != null) {
                        ignition = (WireFieldMapper.map(
                            VehiclePropertyIds.IGNITION_STATE, s.value,
                        )?.value as? String).orEmpty()
                    }
                }
                snapshot[VehiclePropertyIds.EV_CHARGE_PORT_CONNECTED]?.let { s ->
                    val v = s.value
                    if (s.status == PropertyStatus.OK && v is Boolean) charging = v
                }
                recompute()
            }
        }
    }

    fun release() {
        try { context.unregisterReceiver(screenReceiver) } catch (_: Throwable) {}
    }

    private fun recompute() {
        val next = when {
            charging -> State(parked = false, reason = "charging")
            ignition in PARKED_IGNITION -> State(parked = true, reason = "ignition_$ignition")
            !screenOn -> State(parked = true, reason = "screen_off")
            else -> State(parked = false, reason = "active")
        }
        if (next != _state.value) _state.value = next
    }

    companion object {
        private val PARKED_IGNITION = setOf("ACC", "OFF", "LOCK")
    }
}
