package com.ex30companion

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.os.Bundle
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import androidx.core.app.NotificationManagerCompat
import java.util.Locale

/**
 * Experimental, opt-in listener for ongoing navigation notifications on the
 * head unit (e.g. built-in Google Maps or Waze).
 *
 * Privacy & safety invariants:
 * - Inert unless both system notification access AND the in-app opt-in toggle
 *   ([BridgeSettings.navEnabled]) are enabled.
 * - Extracts only canonical maneuver + distance via [NavNotificationParser];
 *   street names, addresses, and raw notification strings are never stored,
 *   transmitted, or logged.
 */
class NavNotificationListenerService : NotificationListenerService() {

    @Volatile
    private var activeNavKey: String? = null

    override fun onListenerConnected() {
        super.onListenerConnected()
        if (BridgeSettings.navEnabled(this)) {
            refreshFromActive()
        } else {
            NavStateRepository.clear()
        }
    }

    override fun onListenerDisconnected() {
        activeNavKey = null
        NavStateRepository.clear()
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        if (sbn == null) return
        if (!BridgeSettings.navEnabled(this)) {
            NavStateRepository.clear()
            return
        }
        if (!isCandidateNavigation(sbn)) return

        val parsed = parseNotification(sbn.notification)
        if (parsed.active) {
            activeNavKey = sbn.key
            NavStateRepository.update(parsed)
        } else if (sbn.key == activeNavKey) {
            refreshFromActive()
        }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification?) {
        if (sbn == null) return
        if (!BridgeSettings.navEnabled(this)) {
            activeNavKey = null
            NavStateRepository.clear()
            return
        }
        if (sbn.key == activeNavKey) {
            refreshFromActive()
        }
    }

    private fun refreshFromActive() {
        if (!BridgeSettings.navEnabled(this)) {
            activeNavKey = null
            NavStateRepository.clear()
            return
        }
        val active = try {
            activeNotifications
        } catch (_: Throwable) {
            null
        }
        if (active != null) {
            for (sbn in active) {
                if (!isCandidateNavigation(sbn)) continue
                val parsed = parseNotification(sbn.notification)
                if (parsed.active) {
                    activeNavKey = sbn.key
                    NavStateRepository.update(parsed)
                    return
                }
            }
        }
        activeNavKey = null
        NavStateRepository.clear()
    }

    private fun isCandidateNavigation(sbn: StatusBarNotification): Boolean {
        val n = sbn.notification ?: return false
        if ((n.flags and Notification.FLAG_GROUP_SUMMARY) != 0) return false
        if (n.category == Notification.CATEGORY_NAVIGATION) return true
        val pkg = sbn.packageName.lowercase(Locale.US)
        if (pkg in NAV_PACKAGES) return true
        return pkg.contains("maps") || pkg.contains("navi") || pkg.contains("waze") ||
            pkg.contains("osmand") || pkg.contains("sygic")
    }

    private fun parseNotification(notification: Notification): NavState {
        val extras = notification.extras ?: Bundle.EMPTY
        var title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
            ?: extras.getCharSequence(Notification.EXTRA_TITLE_BIG)?.toString()
        val textLines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
            ?.joinToString(" · ") { it.toString() }
        var text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
            ?: textLines
        var bigText = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()
        val ticker = notification.tickerText?.toString()

        val rvData = extractRemoteViewsData(notification)
        val rvStrings = rvData.texts.filterNot {
            it.lowercase(Locale("tr", "TR")).trim() in ACTION_LABELS
        }
        if (rvStrings.isNotEmpty()) {
            if (title.isNullOrBlank()) title = rvStrings[0]
            if (text.isNullOrBlank() && rvStrings.size >= 2) text = rvStrings[1]
            if (bigText.isNullOrBlank() && rvStrings.size >= 3) bigText = rvStrings.joinToString(" · ")
        }
        if (title.isNullOrBlank() && text.isNullOrBlank() && !ticker.isNullOrBlank()) {
            text = ticker
        }

        return NavNotificationParser.parse(
            title = title,
            text = text,
            bigText = bigText ?: ticker,
            iconHint = rvData.descriptions.joinToString(" "),
        )
    }

    private data class RemoteViewsData(
        val texts: List<String>,
        val descriptions: List<String>,
    )

    @Suppress("DEPRECATION")
    private fun extractRemoteViewsData(n: Notification): RemoteViewsData {
        val candidates = listOfNotNull(n.bigContentView, n.contentView, n.headsUpContentView)
        if (candidates.isEmpty()) return RemoteViewsData(emptyList(), emptyList())
        val texts = LinkedHashSet<String>()
        val descs = LinkedHashSet<String>()
        val parent = FrameLayout(applicationContext)
        for (rv in candidates) {
            try {
                val inflated = rv.apply(applicationContext, parent)
                collectViews(inflated, texts, descs)
            } catch (_: Throwable) {
            }
        }
        return RemoteViewsData(texts.toList(), descs.toList())
    }

    private fun collectViews(v: View?, texts: MutableSet<String>, descs: MutableSet<String>) {
        if (v == null || v.visibility == View.GONE) return
        v.contentDescription?.toString()?.replace(Regex("""\s+"""), " ")?.trim()?.let { desc ->
            if (desc.isNotEmpty() && desc.lowercase(Locale("tr", "TR")) !in ACTION_LABELS) {
                descs.add(desc)
            }
        }
        if (v is TextView) {
            val s = v.text?.toString()?.replace(Regex("""\s+"""), " ")?.trim().orEmpty()
            if (s.isNotEmpty()) texts.add(s)
        }
        if (v is ViewGroup) {
            for (i in 0 until v.childCount) {
                collectViews(v.getChildAt(i), texts, descs)
            }
        }
    }

    companion object {
        private val NAV_PACKAGES = setOf(
            "com.google.android.apps.maps",
            "com.google.android.apps.gmm",
            "com.waze",
            "com.sygic.aura",
            "com.iternio.abrpapp",
            "ru.yandex.yandexnavi",
            "com.here.app.maps",
            "net.osmand",
            "net.osmand.plus",
        )

        private val ACTION_LABELS = setOf(
            "çık", "çıkış", "exit", "iptal", "cancel", "durdur", "stop",
            "kapat", "dismiss", "sesi kapat", "mute", "sesi aç", "unmute",
            "google haritalar", "google maps", "haritalar", "maps", "navigasyon",
        )

        fun isPermissionGranted(context: Context): Boolean =
            context.packageName in NotificationManagerCompat.getEnabledListenerPackages(context)

        fun ensureRebind(context: Context) {
            if (!BridgeSettings.navEnabled(context) || !isPermissionGranted(context)) return
            try {
                requestRebind(ComponentName(context, NavNotificationListenerService::class.java))
            } catch (_: Throwable) {
            }
        }
    }
}
