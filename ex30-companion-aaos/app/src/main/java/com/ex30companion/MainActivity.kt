package com.ex30companion

import android.car.VehiclePropertyIds
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import com.ex30companion.databinding.ActivityMainBinding
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var adapter: PropertyListAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        adapter = PropertyListAdapter()
        binding.list.layoutManager = LinearLayoutManager(this)
        binding.list.adapter = adapter

        binding.hostInput.setText(BridgeSettings.host(this).orEmpty())
        binding.hostApply.setOnClickListener {
            // BridgeClient reads the host every reconnect tick, so no restart
            // is needed.
            BridgeSettings.setHost(this, binding.hostInput.text.toString())
        }
        renderSoakStatus()
        binding.soakToggle.setOnClickListener {
            if (SyntheticLoadDriver.isRunning()) SyntheticLoadDriver.stop()
            else SyntheticLoadDriver.start()
            renderSoakStatus()
        }

        val missing = PropertyCatalog.dangerousPermissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            startActivity(Intent(this, RationaleActivity::class.java))
            // Continue to start the service so Normal-tier properties still
            // flow and the user sees "permission denied" rows for the rest.
        }

        // Bridge + VHAL subscriptions live in the foreground service so they
        // outlive this activity.
        BridgeService.start(this)

        val tickFlow = flow {
            while (true) {
                emit(System.currentTimeMillis())
                delay(250)
            }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                combine(VehicleStateRepository.state, tickFlow) { state, now ->
                    val rows = PropertyCatalog.entries.map { entry ->
                        PropertyRow(entry, state[entry.propertyId] ?: PropertySample(), now)
                    }
                    rows to isParked(state)
                }.collect { (rows, parked) ->
                    adapter.submitList(rows)
                    binding.list.visibility = if (parked) View.VISIBLE else View.GONE
                    binding.parkedCard.visibility = if (parked) View.GONE else View.VISIBLE
                }
            }
        }
    }

    /**
     * Distraction gate: show live property data only when the vehicle is
     * demonstrably stationary. Either signal alone is accepted — VHAL stub
     * builds may report only one. Conservative default is "not parked".
     */
    private fun isParked(state: Map<Int, PropertySample>): Boolean {
        val brake = state[VehiclePropertyIds.PARKING_BRAKE_ON]
        if (brake?.status == PropertyStatus.OK && brake.value == true) return true
        val gear = state[VehiclePropertyIds.GEAR_SELECTION]
        // android.car.VehicleGear.GEAR_PARK == 0x0004 (pinned, see WireFieldMapper).
        if (gear?.status == PropertyStatus.OK && (gear.value as? Number)?.toInt() == 0x0004) return true
        return false
    }

    private fun renderSoakStatus() {
        binding.soakToggle.text = if (SyntheticLoadDriver.isRunning()) "Soak: ON" else "Soak"
    }
}
