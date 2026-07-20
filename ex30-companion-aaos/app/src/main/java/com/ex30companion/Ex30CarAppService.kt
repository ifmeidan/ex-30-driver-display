package com.ex30companion

import android.content.Intent
import android.content.pm.ApplicationInfo
import androidx.car.app.CarAppService
import androidx.car.app.CarContext
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.model.Action
import androidx.car.app.model.Pane
import androidx.car.app.model.PaneTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.Template
import androidx.car.app.validation.HostValidator

/**
 * Minimal Car App Library service. Its only purpose is to carry the
 * androidx.car.app.category.IOT car-app category that Google Play requires to be
 * declared in the manifest for the Automotive OS listing (see the
 * <intent-filter> on this service in AndroidManifest.xml).
 *
 * The actual driver display is [MainActivity]; this templated surface is
 * intentionally a single static screen and does not duplicate that UI.
 */
class Ex30CarAppService : CarAppService() {

    override fun createHostValidator(): HostValidator =
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
        } else {
            HostValidator.Builder(applicationContext)
                .addAllowedHosts(androidx.car.app.R.array.hosts_allowlist_sample)
                .build()
        }

    override fun onCreateSession(): Session = Ex30Session()
}

private class Ex30Session : Session() {
    override fun onCreateScreen(intent: Intent): Screen = Ex30Screen(carContext)
}

private class Ex30Screen(carContext: CarContext) : Screen(carContext) {
    override fun onGetTemplate(): Template {
        val pane = Pane.Builder()
            .addRow(
                Row.Builder()
                    .setTitle("EX30 Companion")
                    .addText("Open EX30 Companion from the home screen for the driver display.")
                    .build()
            )
            .build()
        return PaneTemplate.Builder(pane)
            .setHeaderAction(Action.APP_ICON)
            .build()
    }
}
