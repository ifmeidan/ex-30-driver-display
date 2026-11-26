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
 * Experimental, opt-in listener for ongoing Google Maps navigation guidance
 * notifications on the head unit (`com.google.android.apps.maps`).
 *
 * Privacy & safety invariants:
 * - Inert unless both system notification access AND the in-app opt-in toggle
 *   ([BridgeSettings.navEnabled]) are enabled.
 * - Pre-filters by exact package name, non-group-summary, and active guidance
 *   flags/category before reading any notification content.
 * - Serializes callbacks and refreshes so an in-flight refresh can never
 *   overwrite a newer callback or resurrect a removed cue.
 */
class NavNotificationListenerService : NotificationListenerService() {

    private val serviceLock = Any()

    override fun onListenerConnected() {
        super.onListenerConnected()
        NavStateRepository.setRevalidator { refreshFromActive() }
        if (isOptedInAndPermitted()) {
            refreshFromActive()
        } else {
            NavNotificationSelector.reset()
            NavStateRepository.clear()
        }
    }

    override fun onListenerDisconnected() {
        NavStateRepository.setRevalidator(null)
        synchronized(serviceLock) {
            NavNotificationSelector.reset()
            NavStateRepository.clear()
        }
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        if (sbn == null) return
        synchronized(serviceLock) {
            if (!isOptedInAndPermitted()) {
                NavNotificationSelector.reset()
                NavStateRepository.clear()
                return
            }
            // Filter source package and guidance flags BEFORE reading content.
            if (!isEligibleGoogleMapsGuidance(sbn)) {
                if (sbn.key == NavNotificationSelector.currentTrackedKey()) {
                    refreshFromActiveLocked()
                }
                return
            }

            val parsed = parseNotification(sbn.notification)
            val activeKeys = currentEligibleKeysOrNull()
            if (parsed.active) {
                if (NavNotificationSelector.shouldAcceptPost(sbn.key, parsed, activeKeys)) {
                    NavStateRepository.update(parsed)
                }
            } else if (sbn.key == NavNotificationSelector.currentTrackedKey()) {
                refreshFromActiveLocked()
            }
        }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification?) {
        if (sbn == null) return
        synchronized(serviceLock) {
            if (!isOptedInAndPermitted()) {
                NavNotificationSelector.reset()
                NavStateRepository.clear()
                return
            }
            if (sbn.key == NavNotificationSelector.currentTrackedKey() ||
                sbn.packageName == NavNotificationSelector.GOOGLE_MAPS_PACKAGE
            ) {
                refreshFromActiveLocked()
            }
        }
    }

    private fun refreshFromActive() {
        synchronized(serviceLock) {
            refreshFromActiveLocked()
        }
    }

    private fun refreshFromActiveLocked() {
        if (!isOptedInAndPermitted()) {
            NavNotificationSelector.reset()
            NavStateRepository.clear()
            return
        }
        val seqBefore = NavStateRepository.currentSeq()
        val active = try {
            activeNotifications
        } catch (_: Throwable) {
            null
        }
        if (active == null) {
            NavNotificationSelector.reset()
            NavStateRepository.clear()
            return
        }

        val candidates = active.mapNotNull { sbn ->
            if (!isEligibleGoogleMapsGuidance(sbn)) null
            else sbn.key to parseNotification(sbn.notification)
        }
        val (_, selectedState) = NavNotificationSelector.selectActive(candidates)
        if (!isOptedInAndPermitted()) {
            NavNotificationSelector.reset()
            NavStateRepository.clear()
            return
        }
        NavStateRepository.applyRefreshIfUnchanged(seqBefore, selectedState)
    }

    private fun currentEligibleKeysOrNull(): Set<String>? = try {
        activeNotifications
            ?.filter { isEligibleGoogleMapsGuidance(it) }
            ?.map { it.key }
            ?.toSet()
    } catch (_: Throwable) {
        null
    }

    private fun isOptedInAndPermitted(): Boolean =
        BridgeSettings.navEnabled(this) && isPermissionGranted(this)

    private fun parseNotification(notification: Notification): NavState {
        val extras = notification.extras ?: Bundle.EMPTY
        var title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
            ?: extras.getCharSequence(Notification.EXTRA_TITLE_BIG)?.toString()
        val textLines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
            ?.joinToString("\n") { it.toString() }
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
            if (bigText.isNullOrBlank() && rvStrings.size >= 3) bigText = rvStrings.joinToString("\n")
        }
        if (title.isNullOrBlank() && text.isNullOrBlank() && !ticker.isNullOrBlank()) {
            text = ticker
        }

        return NavNotificationParser.parse(
            title = title,
            text = text,
            bigText = bigText ?: ticker,
            iconHint = rvData.descriptions.joinToString("\n"),
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
        v.contentDescription?.toString()?.replace(Regex("""[^\S\r\n]+"""), " ")?.trim()?.let { desc ->
            if (desc.isNotEmpty() && desc.lowercase(Locale("tr", "TR")) !in ACTION_LABELS) {
                descs.add(desc)
            }
        }
        if (v is TextView) {
            val s = v.text?.toString()?.replace(Regex("""[^\S\r\n]+"""), " ")?.trim().orEmpty()
            if (s.isNotEmpty()) texts.add(s)
        }
        if (v is ViewGroup) {
            for (i in 0 until v.childCount) {
                collectViews(v.getChildAt(i), texts, descs)
            }
        }
    }

    companion object {
        private val ACTION_LABELS = setOf(
            "çık", "çıkış", "exit", "iptal", "cancel", "durdur", "stop",
            "kapat", "dismiss", "sesi kapat", "mute", "sesi aç", "unmute",
            "google haritalar", "google maps", "haritalar", "maps", "navigasyon",
        )

        /**
         * Pure source & metadata filter checked BEFORE reading notification text.
         * Accepts only ongoing navigation guidance notifications from Google Maps
         * (`com.google.android.apps.maps`), rejecting group summaries and
         * non-guidance categories.
         */
        fun isEligibleGoogleMapsGuidance(
            packageName: String?,
            flags: Int,
            category: String?,
            isOngoing: Boolean,
        ): Boolean {
            if (packageName != NavNotificationSelector.GOOGLE_MAPS_PACKAGE) return false
            if ((flags and Notification.FLAG_GROUP_SUMMARY) != 0) return false
            val ongoingFlag = isOngoing || (flags and Notification.FLAG_ONGOING_EVENT) != 0
            if (category == Notification.CATEGORY_NAVIGATION) return true
            if (!ongoingFlag) return false
            return category == null || category == Notification.CATEGORY_SERVICE ||
                category == Notification.CATEGORY_TRANSPORT || category == Notification.CATEGORY_PROGRESS
        }

        fun isEligibleGoogleMapsGuidance(sbn: StatusBarNotification): Boolean {
            val n = sbn.notification ?: return false
            return isEligibleGoogleMapsGuidance(
                packageName = sbn.packageName,
                flags = n.flags,
                category = n.category,
                isOngoing = sbn.isOngoing,
            )
        }

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
