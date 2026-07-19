package com.ex30companion

import android.car.Car
import android.car.hardware.CarPropertyConfig
import android.car.hardware.CarPropertyValue
import android.car.hardware.property.CarPropertyManager
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat

/**
 * Wraps the per-property Car/CarPropertyManager wiring. Owns one Car instance,
 * inspects each entry in [PropertyCatalog], and registers a callback for every
 * property whose config is non-null and whose permission is granted. Results
 * land in [VehicleStateRepository] — this class never reads back from it.
 *
 * Read-only by construction: there is no setProperty path. The verifyReadOnly
 * Gradle task enforces it across the whole module.
 */
class CarPropertyService(private val context: Context) {

    private var car: Car? = null
    private var manager: CarPropertyManager? = null
    @Volatile private var parked = false

    fun start() {
        if (car != null) return
        val created = Car.createCar(context.applicationContext)
        car = created
        val mgr = created.getCarManager(Car.PROPERTY_SERVICE) as? CarPropertyManager
        manager = mgr ?: return
        for (entry in PropertyCatalog.entries) {
            register(mgr, entry)
        }
    }

    /**
     * Parked (driver left): drop every subscription except the ones that can
     * end parked mode — ignition and the charge port. The 30 Hz power feed
     * in particular must not keep ticking for a locked, empty car. Unparked:
     * re-register the full catalog (STATIC entries just re-read once).
     */
    fun setParked(nowParked: Boolean) {
        if (nowParked == parked) return
        parked = nowParked
        val mgr = manager ?: return
        for (entry in PropertyCatalog.entries) {
            if (entry.propertyId in KEEP_WHILE_PARKED) continue
            if (nowParked) {
                try { mgr.unregisterCallback(callback, entry.propertyId) } catch (_: Throwable) {}
            } else {
                register(mgr, entry)
            }
        }
    }

    fun stop() {
        manager?.let { mgr ->
            try { mgr.unregisterCallback(callback) } catch (_: Throwable) {}
        }
        try { car?.disconnect() } catch (_: Throwable) {}
        car = null
        manager = null
    }

    private fun register(mgr: CarPropertyManager, entry: PropertyCatalog.Entry) {
        // Permission check first so we report PERMISSION_DENIED instead of a
        // generic "not supported" when the user denied a Dangerous prompt.
        val granted = ContextCompat.checkSelfPermission(context, entry.permission) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) {
            VehicleStateRepository.update(
                entry.propertyId,
                PropertySample(
                    status = PropertyStatus.PERMISSION_DENIED,
                    timestampMs = System.currentTimeMillis(),
                    detail = entry.permission,
                ),
            )
            return
        }

        val config = try {
            mgr.getCarPropertyConfig(entry.propertyId)
        } catch (_: Throwable) {
            null
        }
        if (config == null) {
            VehicleStateRepository.update(
                entry.propertyId,
                PropertySample(
                    status = PropertyStatus.NOT_SUPPORTED,
                    timestampMs = System.currentTimeMillis(),
                    detail = "config null on this build",
                ),
            )
            return
        }

        // STATIC properties (e.g. INFO_EV_BATTERY_CAPACITY) don't fire
        // callbacks reliably on AAOS — read once at registration and skip
        // the callback wiring entirely.
        if (config.changeMode == CarPropertyConfig.VEHICLE_PROPERTY_CHANGE_MODE_STATIC) {
            try {
                val v = mgr.getProperty<Any>(entry.propertyId, 0)
                VehicleStateRepository.update(
                    entry.propertyId,
                    PropertySample(
                        value = v.value,
                        timestampMs = System.currentTimeMillis(),
                        status = PropertyStatus.OK,
                    ),
                )
            } catch (t: Throwable) {
                VehicleStateRepository.update(
                    entry.propertyId,
                    PropertySample(
                        status = PropertyStatus.ERROR,
                        timestampMs = System.currentTimeMillis(),
                        detail = t.javaClass.simpleName + ": " + t.message,
                    ),
                )
            }
            return
        }

        val rate = when (config.changeMode) {
            CarPropertyConfig.VEHICLE_PROPERTY_CHANGE_MODE_CONTINUOUS -> {
                val cap = CONTINUOUS_RATE_CAP_HZ[entry.propertyId] ?: DEFAULT_CONTINUOUS_RATE_HZ
                config.maxSampleRate
                    .coerceAtMost(cap)
                    .coerceAtLeast(config.minSampleRate)
            }
            else -> CarPropertyManager.SENSOR_RATE_ONCHANGE
        }
        val ok = try {
            mgr.registerCallback(callback, entry.propertyId, rate)
        } catch (t: Throwable) {
            VehicleStateRepository.update(
                entry.propertyId,
                PropertySample(
                    status = PropertyStatus.ERROR,
                    timestampMs = System.currentTimeMillis(),
                    detail = t.javaClass.simpleName + ": " + t.message,
                ),
            )
            return
        }
        if (!ok) {
            VehicleStateRepository.update(
                entry.propertyId,
                PropertySample(
                    status = PropertyStatus.ERROR,
                    timestampMs = System.currentTimeMillis(),
                    detail = "registerCallback returned false",
                ),
            )
        }
    }

    private val callback = object : CarPropertyManager.CarPropertyEventCallback {
        override fun onChangeEvent(value: CarPropertyValue<*>) {
            VehicleStateRepository.update(
                value.propertyId,
                PropertySample(
                    value = value.value,
                    timestampMs = System.currentTimeMillis(),
                    status = PropertyStatus.OK,
                ),
            )
        }

        override fun onErrorEvent(propId: Int, zone: Int) {
            VehicleStateRepository.update(
                propId,
                PropertySample(
                    status = PropertyStatus.ERROR,
                    timestampMs = System.currentTimeMillis(),
                    detail = "VHAL error event (zone=$zone)",
                ),
            )
        }
    }

    companion object {
        // Subscriptions that survive parked mode: the exit conditions.
        private val KEEP_WHILE_PARKED = setOf(
            android.car.VehiclePropertyIds.IGNITION_STATE,
            android.car.VehiclePropertyIds.EV_CHARGE_PORT_CONNECTED,
        )

        // Per-property cap for CONTINUOUS properties. The VHAL reports
        // maxSampleRate values far higher than the bridge needs — cap each
        // property to the rate the Pi UI can actually consume.
        private const val DEFAULT_CONTINUOUS_RATE_HZ = 1f
        private val CONTINUOUS_RATE_CAP_HZ = mapOf(
            android.car.VehiclePropertyIds.PERF_VEHICLE_SPEED to 10f,
            android.car.VehiclePropertyIds.PERF_VEHICLE_SPEED_DISPLAY to 10f,
            // 30Hz so the power/regen gauges move smoothly with pedal input;
            // pair with the 33ms coalescer tick in BridgeClient.
            android.car.VehiclePropertyIds.EV_BATTERY_INSTANTANEOUS_CHARGE_RATE to 30f,
            android.car.VehiclePropertyIds.EV_BATTERY_LEVEL to 0.5f,
            android.car.VehiclePropertyIds.RANGE_REMAINING to 0.5f,
            android.car.VehiclePropertyIds.ENV_OUTSIDE_TEMPERATURE to 0.5f,
        )
    }
}
