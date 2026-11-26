package com.ex30companion

import android.content.Context

/**
 * Single SharedPreferences entry: the Pi host (hostname or IP). Port is fixed
 * at 7878 (see pi_bridge/protocol.md).
 *
 * Persisted across launches so the bench host doesn't need to be re-typed
 * after every install.
 */
object BridgeSettings {

    private const val PREFS = "bridge"
    private const val KEY_HOST = "pi_host"
    private const val KEY_NAV_ENABLED = "nav_enabled"

    fun host(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_HOST, null)
            ?.takeIf { it.isNotBlank() }

    fun setHost(context: Context, host: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_HOST, host.trim())
            .apply()
    }

    /**
     * Experimental opt-in toggle for turn-by-turn maneuver + distance
     * forwarding. Off by default.
     */
    fun navEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getBoolean(KEY_NAV_ENABLED, false)

    fun setNavEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_NAV_ENABLED, enabled)
            .apply()
    }
}
