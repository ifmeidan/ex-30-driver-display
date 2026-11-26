package com.ex30companion

import android.car.VehiclePropertyIds
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import com.ex30companion.databinding.ActivityMainBinding
import com.ex30companion.databinding.DialogBridgeWifiBinding
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var adapter: PropertyListAdapter

    private val wifiSettings = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        // The running service handles the radio broadcast. Recreate it if Android
        // reclaimed it while Settings was open, without replacing an active request.
        BridgeService.start(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        adapter = PropertyListAdapter()
        binding.list.layoutManager = LinearLayoutManager(this)
        binding.list.adapter = adapter

        binding.hostInput.setText(BridgeSettings.host(this).orEmpty())
        binding.hostApply.setOnClickListener {
            BridgeSettings.setHost(this, binding.hostInput.text.toString())
            connectBridge()
        }
        binding.wifiSetup.setOnClickListener { editWifi() }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                BridgeWifi.status.collect { renderWifiStatus(it) }
            }
        }
        renderSoakStatus()
        binding.soakToggle.setOnClickListener {
            if (SyntheticLoadDriver.isRunning()) SyntheticLoadDriver.stop()
            else SyntheticLoadDriver.start()
            renderSoakStatus()
        }
        renderNavStatus()
        binding.navToggle.setOnClickListener {
            onNavToggleClicked()
        }
        binding.navToggle.setOnLongClickListener {
            BridgeSettings.setNavEnabled(this, false)
            NavNotificationSelector.reset()
            NavStateRepository.clear()
            renderNavStatus()
            true
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

    private fun editWifi() {
        val fields = DialogBridgeWifiBinding.inflate(layoutInflater)
        val savedSsid = BridgeSettings.ssid(this)
        val savedPassword = BridgeSettings.hasPassphrase(this)
        fields.ssid.setText(savedSsid)
        fields.protectedNetwork.isChecked = savedSsid == null || savedPassword
        fun updateFields() {
            val name = fields.ssid.text.toString()
            fields.protectedNetwork.isEnabled = name.isNotEmpty()
            fields.passphrase.visibility = if (name.isNotEmpty() && fields.protectedNetwork.isChecked) {
                View.VISIBLE
            } else {
                View.GONE
            }
            fields.passphrase.setHint(
                if (savedPassword && name == savedSsid) R.string.wifi_password_saved
                else R.string.wifi_password
            )
        }
        fields.ssid.doAfterTextChanged { updateFields() }
        fields.protectedNetwork.setOnCheckedChangeListener { _, _ -> updateFields() }
        updateFields()

        val dialog = AlertDialog.Builder(this)
            .setTitle(R.string.wifi_setup_title)
            .setView(fields.root)
            .setPositiveButton(R.string.wifi_save, null)
            .setNegativeButton(android.R.string.cancel, null)
            .create()
        dialog.setOnDismissListener { fields.passphrase.text.clear() }
        dialog.show()
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val ssid = fields.ssid.text.toString()
            if (!Charsets.UTF_8.newEncoder().canEncode(ssid) || ssid.toByteArray(Charsets.UTF_8).size > 32) {
                fields.ssid.error = getString(R.string.wifi_name_invalid)
                return@setOnClickListener
            }
            val requiresPassword = ssid.isNotEmpty() && fields.protectedNetwork.isChecked
            val passphrase = fields.passphrase.text.toString().takeIf { requiresPassword }
            val keepPassword = requiresPassword && passphrase.isNullOrEmpty() &&
                ssid == savedSsid && savedPassword &&
                ssid == BridgeSettings.ssid(this) && BridgeSettings.hasPassphrase(this)
            if (passphrase != null && !keepPassword &&
                (passphrase.length !in 8..63 || passphrase.any { it.code !in 32..126 })
            ) {
                fields.passphrase.error = getString(R.string.wifi_password_invalid)
                return@setOnClickListener
            }
            val host = binding.hostInput.text.toString()
            try {
                if (keepPassword) BridgeSettings.setHost(this, host)
                else BridgeSettings.setConnection(this, host, ssid, passphrase)
            } catch (_: Exception) {
                fields.passphrase.error = getString(R.string.wifi_save_failed)
                return@setOnClickListener
            }
            dialog.dismiss()
            connectBridge()
        }
    }

    private fun connectBridge() {
        // Apply host/profile changes immediately, including when the radio is off.
        BridgeService.connect(this)
        renderWifiStatus(BridgeWifi.status.value)
        if (BridgeSettings.host(this) != null && !wifiEnabled()) {
            AlertDialog.Builder(this)
                .setMessage(R.string.wifi_enable_message)
                .setPositiveButton(R.string.wifi_open_settings) { _, _ -> openWifiSettings() }
                .setNegativeButton(android.R.string.cancel, null)
                .show()
        }
    }

    private fun wifiEnabled() =
        applicationContext.getSystemService(WifiManager::class.java)?.isWifiEnabled == true

    private fun openWifiSettings() {
        for (action in listOf(Settings.Panel.ACTION_WIFI, Settings.ACTION_WIFI_SETTINGS, Settings.ACTION_SETTINGS)) {
            try {
                wifiSettings.launch(Intent(action))
                return
            } catch (_: ActivityNotFoundException) {
                // OEM head units can omit the Android panel or its dedicated settings activity.
            } catch (_: SecurityException) {
                // Some OEM settings activities are restricted to system apps.
            }
        }
        Toast.makeText(this, R.string.wifi_settings_missing, Toast.LENGTH_LONG).show()
    }

    private fun renderWifiStatus(status: BridgeWifi.Status) {
        val messageId = when (status) {
            BridgeWifi.Status.IDLE -> if (BridgeSettings.host(this) == null) R.string.wifi_host_needed else R.string.wifi_idle
            BridgeWifi.Status.REQUESTING -> R.string.wifi_requesting
            BridgeWifi.Status.WAITING_TO_RETRY -> R.string.wifi_waiting_to_retry
            BridgeWifi.Status.AVAILABLE -> R.string.wifi_available
            BridgeWifi.Status.UNAVAILABLE -> R.string.wifi_unavailable
            BridgeWifi.Status.PERMISSION_REQUIRED -> R.string.wifi_blocked
            BridgeWifi.Status.WIFI_OFF -> R.string.wifi_off
            BridgeWifi.Status.INVALID_CREDENTIAL -> R.string.wifi_credential_error
        }
        val message = getString(messageId)
        val ssid = BridgeSettings.ssid(this)
        binding.wifiStatus.text = if (ssid == null) message else getString(R.string.wifi_named_status, ssid, message)
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

    override fun onResume() {
        super.onResume()
        NavNotificationListenerService.ensureRebind(this)
        renderNavStatus()
    }

    private fun renderSoakStatus() {
        binding.soakToggle.text = if (SyntheticLoadDriver.isRunning()) "Soak: ON" else "Soak"
    }

    private fun renderNavStatus() {
        val enabled = BridgeSettings.navEnabled(this)
        val granted = NavNotificationListenerService.isPermissionGranted(this)
        binding.navToggle.text = when {
            !enabled -> "Nav: OFF"
            !granted -> "Nav: Grant"
            else -> "Nav: ON"
        }
    }

    private fun onNavToggleClicked() {
        val enabled = BridgeSettings.navEnabled(this)
        val granted = NavNotificationListenerService.isPermissionGranted(this)
        when {
            !enabled -> {
                BridgeSettings.setNavEnabled(this, true)
                if (!granted) {
                    openNotificationListenerSettings()
                } else {
                    NavNotificationListenerService.ensureRebind(this)
                    NavStateRepository.revalidate()
                }
            }
            !granted -> {
                // Tapping while in "Nav: Grant" opens settings; long-press or
                // tapping again after granting flips to ON, or turns OFF if already granted.
                openNotificationListenerSettings()
            }
            else -> {
                BridgeSettings.setNavEnabled(this, false)
                NavNotificationSelector.reset()
                NavStateRepository.clear()
            }
        }
        renderNavStatus()
    }

    private fun openNotificationListenerSettings() {
        try {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        } catch (_: Throwable) {
            try {
                startActivity(
                    Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.fromParts("package", packageName, null),
                    ),
                )
            } catch (_: Throwable) {
            }
        }
    }
}
