package com.ex30companion

import android.car.Car
import android.car.VehiclePropertyIds

/**
 * Single source of truth for the v1 property cut. Mirrors
 * `docs/properties.md` (v1 freeze).
 *
 * If you change this list, change the doc. If you change the doc, change this.
 */
object PropertyCatalog {

    enum class Tier { NORMAL, DANGEROUS }

    data class Entry(
        val propertyId: Int,
        val name: String,
        val permission: String,
        val tier: Tier,
    )

    val entries: List<Entry> = listOf(
        // --- Normal tier --------------------------------------------------
        n(VehiclePropertyIds.CURRENT_GEAR, "CURRENT_GEAR", Car.PERMISSION_POWERTRAIN),
        n(VehiclePropertyIds.ENV_OUTSIDE_TEMPERATURE, "ENV_OUTSIDE_TEMPERATURE", Car.PERMISSION_EXTERIOR_ENVIRONMENT),
        n(VehiclePropertyIds.INFO_EV_BATTERY_CAPACITY, "INFO_EV_BATTERY_CAPACITY", Car.PERMISSION_CAR_INFO),
        n(VehiclePropertyIds.EV_CHARGE_PORT_CONNECTED, "EV_CHARGE_PORT_CONNECTED", Car.PERMISSION_ENERGY_PORTS),
        n(VehiclePropertyIds.GEAR_SELECTION, "GEAR_SELECTION", Car.PERMISSION_POWERTRAIN),
        n(VehiclePropertyIds.IGNITION_STATE, "IGNITION_STATE", Car.PERMISSION_POWERTRAIN),
        n(VehiclePropertyIds.NIGHT_MODE, "NIGHT_MODE", Car.PERMISSION_EXTERIOR_ENVIRONMENT),
        n(VehiclePropertyIds.PARKING_BRAKE_ON, "PARKING_BRAKE_ON", Car.PERMISSION_POWERTRAIN),

        // --- Dangerous tier -----------------------------------------------
        d(VehiclePropertyIds.EV_BATTERY_INSTANTANEOUS_CHARGE_RATE, "EV_BATTERY_INSTANTANEOUS_CHARGE_RATE", Car.PERMISSION_ENERGY),
        d(VehiclePropertyIds.EV_BATTERY_LEVEL, "EV_BATTERY_LEVEL", Car.PERMISSION_ENERGY),
        d(VehiclePropertyIds.PERF_VEHICLE_SPEED, "PERF_VEHICLE_SPEED", Car.PERMISSION_SPEED),
        d(VehiclePropertyIds.PERF_VEHICLE_SPEED_DISPLAY, "PERF_VEHICLE_SPEED_DISPLAY", Car.PERMISSION_SPEED),
        d(VehiclePropertyIds.RANGE_REMAINING, "RANGE_REMAINING", Car.PERMISSION_ENERGY),
    )

    val dangerousPermissions: List<String> =
        entries.filter { it.tier == Tier.DANGEROUS }.map { it.permission }.distinct()

    fun byId(id: Int): Entry? = entries.firstOrNull { it.propertyId == id }

    private fun n(id: Int, name: String, perm: String) = Entry(id, name, perm, Tier.NORMAL)
    private fun d(id: Int, name: String, perm: String) = Entry(id, name, perm, Tier.DANGEROUS)
}
