package com.ex30companion

import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.net.Uri
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSuggestion
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import com.ex30companion.databinding.ActivityMainBinding
import com.ex30companion.databinding.DialogBridgeWifiBinding
import java.security.GeneralSecurityException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var adapter: PropertyListAdapter
    private val historyAdapter = NavDebugEntryAdapter()
    private var historyUpdates: Job? = null
    private var navIsOn = false
    private val listenerAccessObserver = object : ContentObserver(Handler(Looper.getMainLooper())) {
        override fun onChange(selfChange: Boolean) = renderNavStatus()
    }
    private val historyBack = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() = closeNavHistory()
    }

    private enum class WifiSetup { CONNECT, ADD_IF_NEEDED, ADD }
    private var pendingWifiSetup = WifiSetup.CONNECT

    private val wifiSettings = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        BridgeService.connect(this)
        if (wifiEnabled()) continueWifiSetup() else pendingWifiSetup = WifiSetup.CONNECT
    }
    private val appPermissions = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        BridgeService.connect(this)
        if (BridgeWifi.canReadSsid(this)) continueWifiSetup() else pendingWifiSetup = WifiSetup.CONNECT
    }
    private val locationSettings = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        BridgeService.connect(this)
        if (BridgeWifi.locationEnabled(this)) continueWifiSetup() else pendingWifiSetup = WifiSetup.CONNECT
    }
    private val saveNetwork = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        // Some AAOS settings screens add the network but omit the result list or return CANCELED.
        // Recheck the actual connection on return. A canceled form never opens another screen.
        BridgeService.connect(this)
        val saved = result.data?.getIntegerArrayListExtra(Settings.EXTRA_WIFI_NETWORK_RESULT_LIST)?.singleOrNull()
        if (result.resultCode == RESULT_OK && saved == Settings.ADD_WIFI_RESULT_ADD_OR_UPDATE_FAILED) {
            Toast.makeText(this, R.string.wifi_system_save_failed, Toast.LENGTH_LONG).show()
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("pendingWifiSetup", pendingWifiSetup.name)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingWifiSetup = savedInstanceState?.getString("pendingWifiSetup")?.let(WifiSetup::valueOf) ?: WifiSetup.CONNECT
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        adapter = PropertyListAdapter()
        binding.list.layoutManager = LinearLayoutManager(this)
        binding.list.adapter = adapter
        setUpNavHistory()

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
                    rows to hasParkSignal(state)
                }.collect { (rows, parked) ->
                    adapter.submitList(rows)
                    binding.list.visibility = if (parked) View.VISIBLE else View.GONE
                    binding.parkedCard.visibility = if (parked) View.GONE else View.VISIBLE
                    updateNavHistoryAccess()
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
            .apply { if (savedSsid != null) setNeutralButton(R.string.wifi_add_to_car, null) }
            .setNegativeButton(android.R.string.cancel, null)
            .create()
        dialog.setOnDismissListener { fields.passphrase.text.clear() }
        dialog.show()
        fun applyProfile(forceAdd: Boolean) {
            val ssid = fields.ssid.text.toString()
            if (!Charsets.UTF_8.newEncoder().canEncode(ssid) || ssid.toByteArray(Charsets.UTF_8).size > 32) {
                fields.ssid.error = getString(R.string.wifi_name_invalid)
                return
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
                return
            }
            val host = binding.hostInput.text.toString()
            try {
                if (keepPassword) BridgeSettings.setHost(this, host)
                else BridgeSettings.setConnection(this, host, ssid, passphrase)
            } catch (_: Exception) {
                fields.passphrase.error = getString(R.string.wifi_save_failed)
                return
            }
            dialog.dismiss()
            val setup = when {
                ssid.isEmpty() -> WifiSetup.CONNECT
                forceAdd -> WifiSetup.ADD
                ssid != savedSsid -> WifiSetup.ADD_IF_NEEDED
                keepPassword || (!requiresPassword && !savedPassword) -> WifiSetup.CONNECT
                else -> WifiSetup.ADD // Replace the saved security settings or password.
            }
            connectBridge(setup)
        }
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener { applyProfile(forceAdd = false) }
        if (savedSsid != null) {
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener { applyProfile(forceAdd = true) }
        }
    }

    private fun connectBridge(setup: WifiSetup = WifiSetup.CONNECT) {
        pendingWifiSetup = setup
        BridgeService.connect(this)
        if (setup == WifiSetup.CONNECT && BridgeSettings.host(this) == null) return
        if (!wifiEnabled()) openWifiSettings() else continueWifiSetup()
    }

    private fun continueWifiSetup() {
        val ssid = BridgeSettings.ssid(this) ?: return
        if (!BridgeWifi.canReadSsid(this) || !BridgeWifi.locationEnabled(this)) {
            checkWifiAccess()
            return
        }
        val setup = pendingWifiSetup
        pendingWifiSetup = WifiSetup.CONNECT
        if (setup == WifiSetup.ADD || (setup == WifiSetup.ADD_IF_NEEDED && !alreadyOnNetwork(ssid))) {
            saveNetworkInAndroid()
        }
    }

    // One foreground snapshot avoids reopening a blank OEM add-network form for a joined Pi.
    // Route selection in the service always uses the per-network capabilities callback.
    @Suppress("DEPRECATION")
    private fun alreadyOnNetwork(ssid: String): Boolean {
        val info = try {
            applicationContext.getSystemService(WifiManager::class.java)?.connectionInfo ?: return false
        } catch (_: SecurityException) {
            return false
        }
        return matchesSavedWifi(ssid, BridgeSettings.hasPassphrase(this), info.ssid, info.currentSecurityType)
    }

    private fun saveNetworkInAndroid() {
        val profile = try {
            BridgeSettings.wifi(this) ?: return
        } catch (_: GeneralSecurityException) {
            Toast.makeText(this, R.string.wifi_credential_error, Toast.LENGTH_LONG).show()
            return
        }
        try {
            val builder = WifiNetworkSuggestion.Builder().setSsid(profile.ssid)
            profile.passphrase?.let { builder.setWpa2Passphrase(it) }
            val intent = Intent(Settings.ACTION_WIFI_ADD_NETWORKS).putParcelableArrayListExtra(
                Settings.EXTRA_WIFI_NETWORK_LIST, arrayListOf(builder.build())
            )
            saveNetwork.launch(intent)
        } catch (_: ActivityNotFoundException) {
            joinInCarSettings()
        } catch (_: SecurityException) {
            joinInCarSettings()
        } catch (_: IllegalArgumentException) {
            Toast.makeText(this, R.string.wifi_system_save_failed, Toast.LENGTH_LONG).show()
        }
    }

    private fun joinInCarSettings() {
        Toast.makeText(this, R.string.wifi_join_manually, Toast.LENGTH_LONG).show()
        openWifiSettings()
    }

    private fun checkWifiAccess() {
        if (BridgeSettings.ssid(this) == null) return
        if (!BridgeWifi.canReadSsid(this)) {
            val details = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
            val message = getString(R.string.wifi_location_explanation, packageManager.backgroundPermissionOptionLabel)
            AlertDialog.Builder(this)
                .setMessage(message)
                .setPositiveButton(R.string.wifi_app_settings) { _, _ -> openPermissionSettings(details) }
                .setNegativeButton(android.R.string.cancel, null)
                .show()
        } else if (!BridgeWifi.locationEnabled(this)) {
            openPermissionSettings(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
        }
    }

    private fun openPermissionSettings(intent: Intent) {
        try {
            if (intent.action == Settings.ACTION_LOCATION_SOURCE_SETTINGS) locationSettings.launch(intent)
            else appPermissions.launch(intent)
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(this, R.string.wifi_check_location_settings, Toast.LENGTH_LONG).show()
        } catch (_: SecurityException) {
            Toast.makeText(this, R.string.wifi_check_location_settings, Toast.LENGTH_LONG).show()
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
        pendingWifiSetup = WifiSetup.CONNECT
        Toast.makeText(this, R.string.wifi_settings_missing, Toast.LENGTH_LONG).show()
    }

    private fun renderWifiStatus(status: BridgeWifi.Status) {
        val messageId = when (status) {
            BridgeWifi.Status.IDLE -> if (BridgeSettings.host(this) == null) R.string.wifi_host_needed else R.string.wifi_idle
            BridgeWifi.Status.REQUESTING -> R.string.wifi_requesting
            BridgeWifi.Status.WAKING -> R.string.wifi_waking
            BridgeWifi.Status.AVAILABLE -> R.string.wifi_available
            BridgeWifi.Status.UNAVAILABLE -> R.string.wifi_unavailable
            BridgeWifi.Status.PERMISSION_REQUIRED -> R.string.wifi_blocked
            BridgeWifi.Status.WIFI_OFF -> R.string.wifi_off
            BridgeWifi.Status.LOCATION_REQUIRED -> R.string.wifi_location_required
            BridgeWifi.Status.LOCATION_OFF -> R.string.wifi_location_off
            BridgeWifi.Status.SSID_HIDDEN -> R.string.wifi_ssid_hidden
        }
        val message = getString(messageId)
        val ssid = BridgeSettings.ssid(this)
        binding.wifiStatus.text = if (ssid == null) message else getString(R.string.wifi_named_status, ssid, message)
    }

    override fun onStart() {
        super.onStart()
        // NotificationManagerCompat reads this per-user setting for listener access.
        contentResolver.registerContentObserver(
            Settings.Secure.getUriFor("enabled_notification_listeners"), false, listenerAccessObserver,
        )
    }

    override fun onResume() {
        super.onResume()
        NavNotificationListenerService.ensureRebind(this)
        renderNavStatus()
    }

    override fun onStop() {
        contentResolver.unregisterContentObserver(listenerAccessObserver)
        closeNavHistory()
        super.onStop()
    }

    private fun setUpNavHistory() {
        val screen = binding.navDebugHistory
        screen.historyList.layoutManager = LinearLayoutManager(this)
        screen.historyList.adapter = historyAdapter
        onBackPressedDispatcher.addCallback(this, historyBack)
        binding.navDebug.setOnClickListener { openNavHistory() }
        screen.historyBack.setOnClickListener { closeNavHistory() }
        screen.historyRecord.setOnClickListener {
            if (checkNavHistoryAccess()) BridgeSettings.setNavDebugEnabled(this, screen.historyRecord.isChecked)
        }
        screen.historyClear.setOnClickListener {
            if (checkNavHistoryAccess()) NavDebugHistory.clear(this)
        }
    }

    private fun openNavHistory() {
        if (!checkNavHistoryAccess()) return
        val screen = binding.navDebugHistory
        binding.dashboard.isVisible = false
        screen.root.isVisible = true
        screen.historyRecord.isChecked = BridgeSettings.navDebugEnabled(this)
        screen.historyList.scrollToPosition(0)
        historyBack.isEnabled = true
        historyUpdates = lifecycleScope.launch {
            NavDebugHistory.entries(this@MainActivity).collect { entries ->
                historyAdapter.submitList(entries)
                screen.historyClear.isEnabled = entries.isNotEmpty()
                screen.historyEmpty.isVisible = entries.isEmpty()
            }
        }
    }

    private fun closeNavHistory() {
        if (!historyBack.isEnabled) return
        historyBack.isEnabled = false
        historyUpdates?.cancel()
        historyUpdates = null
        binding.navDebugHistory.root.isVisible = false
        binding.dashboard.isVisible = true
        historyAdapter.submitList(emptyList())
    }

    private fun updateNavHistoryAccess() {
        val allowed = navIsOn && VehicleStateRepository.parkedForNavHistory
        binding.navDebug.isVisible = allowed
        if (!allowed) closeNavHistory()
    }

    private fun checkNavHistoryAccess(): Boolean {
        // Recheck permission and the latest vehicle samples at the time of the tap.
        renderNavStatus()
        return lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) && binding.navDebug.isVisible
    }

    private fun renderSoakStatus() {
        binding.soakToggle.text = if (SyntheticLoadDriver.isRunning()) "Soak: ON" else "Soak"
    }

    private fun renderNavStatus() {
        val enabled = BridgeSettings.navEnabled(this)
        val granted = NavNotificationListenerService.isPermissionGranted(this)
        navIsOn = enabled && granted
        binding.navToggle.text = when {
            !enabled -> "Nav: OFF"
            !granted -> "Nav: Grant"
            else -> "Nav: ON"
        }
        updateNavHistoryAccess()
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
