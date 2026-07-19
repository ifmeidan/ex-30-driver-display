package com.ex30companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Auto-starts [BridgeService] when AAOS finishes booting, so the bridge runs
 * without the user ever opening MainActivity. Registered for both
 * LOCKED_BOOT_COMPLETED (Direct-Boot, fires early on head-unit wake) and
 * BOOT_COMPLETED — some OEM builds only deliver one of the two.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            -> try {
                BridgeService.start(context)
            } catch (_: Throwable) {
            }
        }
    }
}
