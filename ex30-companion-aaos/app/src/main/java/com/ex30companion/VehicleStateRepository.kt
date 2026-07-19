package com.ex30companion

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

object VehicleStateRepository {

    private val _state = MutableStateFlow<Map<Int, PropertySample>>(
        PropertyCatalog.entries.associate { it.propertyId to PropertySample() }
    )
    val state: StateFlow<Map<Int, PropertySample>> = _state.asStateFlow()

    fun update(propertyId: Int, sample: PropertySample) {
        _state.update { it + (propertyId to sample) }
    }
}
