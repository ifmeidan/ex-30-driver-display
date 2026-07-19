package com.ex30companion

import android.car.VehiclePropertyIds

/**
 * Translates raw [PropertySample] values into the wire schema documented in
 * `pi_bridge/protocol.md`.
 *
 * Enums are emitted as strings, not ints — same constant names AOSP uses,
 * minus prefixes. A value the build emits that we don't have a name for is
 * encoded as `"UNKNOWN"`, which the Pi receiver tolerates by design.
 */
object WireFieldMapper {

    data class Wire(val field: String, val value: Any)

    fun map(propertyId: Int, raw: Any?): Wire? {
        if (raw == null) return null
        return when (propertyId) {
            VehiclePropertyIds.PERF_VEHICLE_SPEED ->
                Wire("speed_aaos", (raw as Number).toFloat())
            VehiclePropertyIds.PERF_VEHICLE_SPEED_DISPLAY ->
                Wire("speed_display_aaos", (raw as Number).toFloat())
            VehiclePropertyIds.EV_BATTERY_LEVEL ->
                Wire("soc_aaos", (raw as Number).toFloat())
            // EV_BATTERY_INSTANTANEOUS_CHARGE_RATE per VHAL spec is mW with
            // positive = charging (regen / plugged in), negative = discharging.
            // The real EX30 ships the opposite sign (verified during the
            // 2026-05-14 charge cycle on the retail car): negative = charging
            // and regen, positive = discharging. We invert here so the wire
            // field matches the spec the Pi gauges code against — keeps the
            // receiver simple and robust if a future Volvo OTA fixes the sign.
            VehiclePropertyIds.EV_BATTERY_INSTANTANEOUS_CHARGE_RATE ->
                Wire("battery_power_mw", -(raw as Number).toFloat())
            VehiclePropertyIds.RANGE_REMAINING ->
                Wire("range_m_aaos", (raw as Number).toFloat())
            VehiclePropertyIds.CURRENT_GEAR ->
                Wire("gear", gearName((raw as Number).toInt()))
            VehiclePropertyIds.GEAR_SELECTION ->
                Wire("gear_selected", gearName((raw as Number).toInt()))
            VehiclePropertyIds.PARKING_BRAKE_ON ->
                Wire("parking_brake", raw as Boolean)
            VehiclePropertyIds.IGNITION_STATE ->
                Wire("ignition_state", ignitionName((raw as Number).toInt()))
            VehiclePropertyIds.EV_CHARGE_PORT_CONNECTED ->
                Wire("charge_port_connected", raw as Boolean)
            VehiclePropertyIds.ENV_OUTSIDE_TEMPERATURE ->
                Wire("ambient_temp_aaos", (raw as Number).toFloat())
            VehiclePropertyIds.NIGHT_MODE ->
                Wire("night_mode", raw as Boolean)
            VehiclePropertyIds.INFO_EV_BATTERY_CAPACITY ->
                Wire("pack_capacity_wh", (raw as Number).toFloat())
            else -> null
        }
    }

    // android.car.VehicleGear bit values — pinned here rather than referenced
    // because the constants moved between API 30 and 33; the underlying bits
    // are stable across all versions the EX30 has shipped.
    private fun gearName(bits: Int): String = when (bits) {
        0x0001 -> "N"  // GEAR_NEUTRAL
        0x0002 -> "R"  // GEAR_REVERSE
        0x0004 -> "P"  // GEAR_PARK
        0x0008 -> "D"  // GEAR_DRIVE (and any GEAR_1..GEAR_9 collapse to D for our purposes)
        in 0x0010..0x1000 -> "D"
        else -> "UNKNOWN"
    }

    // android.car.VehicleIgnitionState
    private fun ignitionName(v: Int): String = when (v) {
        1 -> "LOCK"
        2 -> "OFF"
        3 -> "ACC"
        4 -> "ON"
        5 -> "START"
        else -> "UNKNOWN"
    }
}
