package com.ex30companion

import android.car.VehiclePropertyIds
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.random.Random

/**
 * Soak driver. Pushes synthetic [PropertySample]s into
 * [VehicleStateRepository] at a fixed tick rate, exercising the full
 * downstream path (BridgeClient coalescer → queue → socket → Pi listener)
 * without depending on VHAL or `cmd car_service inject-vhal-event` (which
 * is gated to non-user builds and unavailable on every standard Automotive
 * AVD).
 *
 * Not wired to anything in production builds beyond a manual toggle from
 * [MainActivity]. Has no side effects on the real car: it never calls
 * `setProperty` and never touches CarPropertyManager.
 */
object SyntheticLoadDriver {

    private const val TICK_MS = 200L  // 5 Hz

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var job: Job? = null

    fun isRunning(): Boolean = job?.isActive == true

    fun start() {
        if (job?.isActive == true) return
        job = scope.launch { run() }
    }

    fun stop() {
        job?.cancel()
        job = null
    }

    private suspend fun run() {
        val rng = Random(System.currentTimeMillis())
        var speed = 0f                      // m/s
        var soc = 75f                       // %
        var range = 300_000f                // m
        var temp = 18f                      // °C
        var chargeRate = 0f                 // kW
        var gear = 0x0004                   // P
        var gearSel = 0x0004
        var parking = true
        var night = false
        var portConnected = false
        var ignition = 4                    // ON

        // STATIC field — emitted once at start so the seed snapshot includes it.
        VehicleStateRepository.update(
            VehiclePropertyIds.INFO_EV_BATTERY_CAPACITY,
            PropertySample(64_000f, System.currentTimeMillis(), PropertyStatus.OK)
        )

        while (scope.isActive) {
            val now = System.currentTimeMillis()

            // Pick a small mutation per tick — keeps each frame meaningful
            // (only changed values become wire fields) without blowing the
            // coalescer queue.
            when (rng.nextInt(0, 11)) {
                0 -> { speed = (speed + rng.nextFloat() * 6f - 3f).coerceIn(0f, 35f) }
                1 -> { soc = (soc + rng.nextFloat() - 0.5f).coerceIn(0f, 100f) }
                2 -> { range = (range + rng.nextFloat() * 4_000f - 2_000f).coerceIn(0f, 500_000f) }
                3 -> { temp = (temp + rng.nextFloat() - 0.5f).coerceIn(-20f, 45f) }
                4 -> {
                    val gears = listOf(0x0001, 0x0002, 0x0004, 0x0008)
                    gearSel = gears[(gears.indexOf(gearSel) + 1) % gears.size]
                    gear = gearSel
                }
                5 -> { parking = !parking }
                6 -> { night = !night }
                7 -> { portConnected = !portConnected }
                8 -> { ignition = listOf(2, 3, 4)[(listOf(2, 3, 4).indexOf(ignition) + 1) % 3] }
                9 -> { chargeRate = (chargeRate + rng.nextFloat() * 2f - 1f).coerceIn(0f, 150f) }
                10 -> { /* idle tick — measures coalescer behavior with no input */ }
            }

            // Push everything every tick. Only changed values produce wire frames
            // because BridgeClient.observeRepository diffs against last-seen.
            // Both speed properties are m/s on the wire (protocol.md); the Pi
            // does the km/h conversion. The display copy gets a small upward
            // bias to mimic the dash speedo's calibration offset.
            push(VehiclePropertyIds.PERF_VEHICLE_SPEED, speed, now)
            push(VehiclePropertyIds.PERF_VEHICLE_SPEED_DISPLAY, speed * 1.03f, now)
            push(VehiclePropertyIds.EV_BATTERY_LEVEL, soc, now)
            push(VehiclePropertyIds.RANGE_REMAINING, range, now)
            push(VehiclePropertyIds.ENV_OUTSIDE_TEMPERATURE, temp, now)
            push(VehiclePropertyIds.EV_BATTERY_INSTANTANEOUS_CHARGE_RATE, chargeRate, now)
            push(VehiclePropertyIds.CURRENT_GEAR, gear, now)
            push(VehiclePropertyIds.GEAR_SELECTION, gearSel, now)
            push(VehiclePropertyIds.PARKING_BRAKE_ON, parking, now)
            push(VehiclePropertyIds.NIGHT_MODE, night, now)
            push(VehiclePropertyIds.EV_CHARGE_PORT_CONNECTED, portConnected, now)
            push(VehiclePropertyIds.IGNITION_STATE, ignition, now)

            delay(TICK_MS)
        }
    }

    private fun push(propertyId: Int, value: Any, tsMs: Long) {
        VehicleStateRepository.update(
            propertyId,
            PropertySample(value, tsMs, PropertyStatus.OK, detail = "synthetic")
        )
    }
}
