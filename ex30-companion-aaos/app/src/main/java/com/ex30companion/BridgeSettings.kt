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
}
