package com.ex30companion

import android.car.VehiclePropertyIds
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

enum class PropertyStatus {
    UNKNOWN,            // initial; nothing observed yet
    OK,                 // value read at least once
    NOT_SUPPORTED,      // getCarPropertyConfig returned null on this build
    PERMISSION_DENIED,  // permission missing at runtime
    ERROR,              // VHAL error / exception
}

data class PropertySample(
    val value: Any? = null,
    val timestampMs: Long = 0L,
    val status: PropertyStatus = PropertyStatus.UNKNOWN,
    val detail: String? = null,
)

internal fun hasParkSignal(state: Map<Int, PropertySample>): Boolean {
    val brake = state[VehiclePropertyIds.PARKING_BRAKE_ON]
    val gear = state[VehiclePropertyIds.GEAR_SELECTION]
    return (brake?.status == PropertyStatus.OK && brake.value == true) ||
        (gear?.status == PropertyStatus.OK && (gear.value as? Number)?.toInt() == 0x0004)
}

object VehicleStateRepository {
    // Only the vehicle service may provide evidence that the history screen can be opened.
    private val parkingSignals = MutableStateFlow<Map<Int, PropertySample>>(emptyMap())
    private val parkingProperties = setOf(
        VehiclePropertyIds.GEAR_SELECTION, VehiclePropertyIds.PARKING_BRAKE_ON,
        VehiclePropertyIds.PERF_VEHICLE_SPEED, VehiclePropertyIds.PERF_VEHICLE_SPEED_DISPLAY,
    )

    val parkedForNavHistory: Boolean
        get() {
            val signals = parkingSignals.value
            fun moving(propertyId: Int): Boolean {
                val sample = signals[propertyId] ?: return false
                val speed = (sample.value as? Number)?.toDouble() ?: return false
                return sample.status == PropertyStatus.OK && speed.isFinite() && speed != 0.0
            }
            return hasParkSignal(signals) && !moving(VehiclePropertyIds.PERF_VEHICLE_SPEED) &&
                !moving(VehiclePropertyIds.PERF_VEHICLE_SPEED_DISPLAY)
        }

    fun clearParkingSignals() {
        parkingSignals.value = emptyMap()
    }


    private val _state = MutableStateFlow<Map<Int, PropertySample>>(
        PropertyCatalog.entries.associate { it.propertyId to PropertySample() }
    )
    val state: StateFlow<Map<Int, PropertySample>> = _state.asStateFlow()

    fun updateFromVehicle(propertyId: Int, sample: PropertySample) {
        if (propertyId in parkingProperties) parkingSignals.update { it + (propertyId to sample) }
        _state.update { it + (propertyId to sample) }
    }

    fun updateFromSimulator(propertyId: Int, sample: PropertySample) {
        _state.update { it + (propertyId to sample) }
    }
}
