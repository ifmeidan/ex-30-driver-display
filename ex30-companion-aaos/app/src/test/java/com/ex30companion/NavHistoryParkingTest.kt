package com.ex30companion

import android.car.VehiclePropertyIds.GEAR_SELECTION
import android.car.VehiclePropertyIds.PARKING_BRAKE_ON
import android.car.VehiclePropertyIds.PERF_VEHICLE_SPEED
import android.car.VehiclePropertyIds.PERF_VEHICLE_SPEED_DISPLAY
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class NavHistoryParkingTest {
    private val vehicle = VehicleStateRepository
    private fun sample(value: Any) = PropertySample(value = value, status = PropertyStatus.OK)

    @Before @After fun reset() = vehicle.clearParkingSignals()

    @Test fun simulatorCannotGrantParkOrHideVehicleMotion() {
        vehicle.updateFromSimulator(GEAR_SELECTION, sample(4))
        vehicle.updateFromSimulator(PARKING_BRAKE_ON, sample(true))
        assertFalse(vehicle.parkedForNavHistory)

        vehicle.updateFromVehicle(GEAR_SELECTION, sample(4))
        assertTrue(vehicle.parkedForNavHistory)
        for (speed in listOf(PERF_VEHICLE_SPEED, PERF_VEHICLE_SPEED_DISPLAY)) {
            vehicle.updateFromVehicle(speed, sample(-2f))
            vehicle.updateFromSimulator(speed, sample(0f))
            assertFalse(vehicle.parkedForNavHistory)
            vehicle.updateFromVehicle(speed, sample(0f))
            assertTrue(vehicle.parkedForNavHistory)
        }
    }

    @Test fun brakeMustComeFromAvailableVehicleDataAndExpiresWithSubscription() {
        vehicle.updateFromVehicle(PARKING_BRAKE_ON, sample(true))
        assertTrue(vehicle.parkedForNavHistory)
        vehicle.updateFromVehicle(PARKING_BRAKE_ON, PropertySample(status = PropertyStatus.ERROR))
        assertFalse(vehicle.parkedForNavHistory)
        vehicle.updateFromVehicle(PARKING_BRAKE_ON, sample(true))
        vehicle.clearParkingSignals()
        assertFalse(vehicle.parkedForNavHistory)
    }
}
