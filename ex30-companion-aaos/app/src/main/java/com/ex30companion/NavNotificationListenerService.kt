package com.ex30companion

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.content.res.Resources
import android.graphics.Bitmap
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Icon
import android.os.Bundle
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import androidx.car.app.notification.CarAppExtender
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
    private var lastUnknownShape: String? = null

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
            NavDebugHistory.resetSession()
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
            if (isEligibleGoogleMapsGuidance(sbn)) {
                // Even an unreadable step establishes ownership of this route key.
                if (NavNotificationSelector.shouldAcceptPost(sbn.key)) {
                    publishCue(sbn.key, parseNotification(sbn.notification, diagnose = true))
                }
                return
            }
            if (sbn.key != NavNotificationSelector.currentTrackedKey()) return
            if (!isMapsNavigationPost(sbn.packageName, sbn.notification.flags, sbn.notification.category)) {
                refreshFromActiveLocked()
                return
            }
            if (!NavNotificationSelector.canReadFinal(sbn.key)) return
            val final = parseNotification(sbn.notification, diagnose = true, finalUpdate = true)
            if (final.cue.maneuver == "destination" && final.cue.arrival != "none") {
                if (NavNotificationSelector.consumeFinal(sbn.key)) publishCue(sbn.key, final)
            } else {
                NavDebugHistory.observe(this, sbn.key, final.cue, final.sources, finalUpdate = true)
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
            if (sbn.packageName == NavNotificationSelector.GOOGLE_MAPS_PACKAGE) {
                val removedOwner = NavNotificationSelector.remove(sbn.key)
                refreshFromActiveLocked(removedKey = sbn.key, trackedKeyRemoved = removedOwner)
            }
        }
    }

    private fun refreshFromActive() {
        synchronized(serviceLock) {
            refreshFromActiveLocked()
        }
    }

    private fun refreshFromActiveLocked(removedKey: String? = null, trackedKeyRemoved: Boolean = false) {
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

        // A removal callback is authoritative even if the binder snapshot still
        // contains that key. Dismissible entries are inspected only as metadata.
        val mapsGuidance = active.filter { sbn ->
            sbn.key != removedKey &&
                isMapsNavigationPost(sbn.packageName, sbn.notification.flags, sbn.notification.category)
        }
        val (eligible, replacements) = mapsGuidance.partition(::isEligibleGoogleMapsGuidance)
        val parsed = eligible.sortedBy { it.postTime }.associate { it.key to parseNotification(it.notification) }
        val (selectedKey, selectedState) = NavNotificationSelector.selectActive(
            parsed.map { it.key to it.value.cue }, replacements.mapTo(mutableSetOf()) { it.key },
        )
        if (!isOptedInAndPermitted()) {
            NavNotificationSelector.reset()
            NavStateRepository.clear()
            return
        }
        if (NavStateRepository.applyRefreshIfUnchanged(seqBefore, selectedState, trackedKeyRemoved) != null) {
            // Also catch a route already underway when the listener binds or debug is
            // enabled. The history suppresses renewals with the same key and outcome.
            val result = parsed[selectedKey]
            if (selectedKey != null && result != null) {
                NavDebugHistory.observe(this, selectedKey, result.cue, result.sources)
            } else if (NavNotificationSelector.currentTrackedKey()?.let(NavNotificationSelector::canReadFinal) != true) {
                NavDebugHistory.ended(this)
            }
        }
    }

    private fun isOptedInAndPermitted(): Boolean =
        BridgeSettings.navEnabled(this) && isPermissionGranted(this)

    private fun publishCue(key: String, parsed: ParsedGuidance) {
        val cue = parsed.cue
        if (NavStateRepository.state.value != cue && Log.isLoggable(TAG, Log.DEBUG)) {
            Log.d(TAG, "maneuver=${cue.maneuver} distance=${cue.distance} arrival=${cue.arrival} exit=${cue.exitNumber}")
        }
        NavStateRepository.update(cue)
        NavDebugHistory.observe(this, key, cue, parsed.sources)
    }

    private data class ParsedGuidance(val cue: NavState, val sources: Set<NavDebugSource>)

    private fun parseNotification(
        notification: Notification, diagnose: Boolean = false, finalUpdate: Boolean = false,
    ): ParsedGuidance {
        val extras = notification.extras ?: Bundle.EMPTY
        val car = if (CarAppExtender.isExtended(notification)) CarAppExtender(notification) else null
        val rvData = extractRemoteViewsData(notification)
        val rvStrings = rvData.texts.filterNot {
            it.lowercase(Locale("tr", "TR")).trim() in ACTION_LABELS
        }

        val cue = NavNotificationParser.parse(
            title = listOfNotNull(
                car?.contentTitle,
                extras.getCharSequence(Notification.EXTRA_TITLE),
                extras.getCharSequence(Notification.EXTRA_TITLE_BIG),
            ).joinToString("\n"),
            text = listOfNotNull(
                car?.contentText,
                extras.getCharSequence(Notification.EXTRA_TEXT),
            ).joinToString("\n"),
            bigText = buildList {
                extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.let { add(it) }
                extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.let { addAll(it) }
                notification.tickerText?.let { add(it) }
            }.joinToString("\n"),
            iconHint = buildList {
                addAll(rvData.descriptions)
                extras.getCharSequence(Notification.EXTRA_PICTURE_CONTENT_DESCRIPTION)?.let { add(it.toString()) }
            }.joinToString("\n"),
            subText = extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString(),
            renderedTexts = rvStrings,
            summaryText = extras.getCharSequence(Notification.EXTRA_SUMMARY_TEXT)?.toString(),
        )
        val parsed = if (cue.maneuver == "none") withManeuverIcon(cue, notification, car, rvData.bitmaps) else cue
        val logShape = diagnose && Log.isLoggable(TAG, Log.DEBUG)
        val needsSources = parsed.maneuver == "none" ||
            (finalUpdate && (parsed.maneuver != "destination" || parsed.arrival == "none"))
        val sources = if (needsSources && (logShape || BridgeSettings.navDebugEnabled(this))) {
            buildSet {
                if (car?.contentTitle != null || car?.contentText != null) add(NavDebugSource.CAR_TEXT)
                if (extras.containsKey(Notification.EXTRA_TITLE) || extras.containsKey(Notification.EXTRA_TEXT) ||
                    extras.containsKey(Notification.EXTRA_TITLE_BIG)) add(NavDebugSource.PRIMARY_TEXT)
                if (extras.containsKey(Notification.EXTRA_BIG_TEXT) || extras.containsKey(Notification.EXTRA_TEXT_LINES)) {
                    add(NavDebugSource.EXPANDED_TEXT)
                }
                if (notification.tickerText != null) add(NavDebugSource.TICKER)
                if (extras.containsKey(Notification.EXTRA_SUB_TEXT)) add(NavDebugSource.SUBTEXT)
                if (rvStrings.isNotEmpty()) add(NavDebugSource.REMOTE_TEXT)
                if (rvData.descriptions.isNotEmpty() || extras.containsKey(Notification.EXTRA_PICTURE_CONTENT_DESCRIPTION)) {
                    add(NavDebugSource.ICON_DESCRIPTION)
                }
                val types = listOf(notification.getLargeIcon()?.type, notification.smallIcon?.type)
                if (Icon.TYPE_RESOURCE in types || (car?.smallIcon ?: 0) != 0) add(NavDebugSource.RESOURCE_ICON)
                if (Icon.TYPE_BITMAP in types || Icon.TYPE_ADAPTIVE_BITMAP in types ||
                    car?.largeIcon != null || rvData.bitmaps.isNotEmpty()) add(NavDebugSource.BITMAP_ICON)
            }
        } else emptySet()
        if (logShape) {
            if (parsed.maneuver == "none") {
                val shape = "distanceOnly=${parsed.active} sources=$sources" +
                    " largeIconType=${notification.getLargeIcon()?.type} smallIconType=${notification.smallIcon?.type}"
                if (shape != lastUnknownShape) Log.d(TAG, "Unrecognized Maps guidance $shape")
                lastUnknownShape = shape
            } else {
                lastUnknownShape = null
            }
        }
        return ParsedGuidance(parsed, sources)
    }

    // A named drawable can carry the maneuver when the accessible text only
    // contains a street name. Read drawable-backed and bitmap-backed icons only.
    private fun withManeuverIcon(
        cue: NavState, notification: Notification, car: CarAppExtender?, remoteBitmaps: Set<Bitmap>,
    ): NavState {
        val largeIcon = notification.getLargeIcon()
        val resIds = listOfNotNull(
            car?.smallIcon,
            mapsResourceId(largeIcon),
            mapsResourceId(notification.smallIcon),
        ).filter { it != 0 }.distinct()
        val bitmaps = buildSet {
            car?.largeIcon?.let { add(it) }
            bitmapIcon(largeIcon)?.let { add(it) }
            addAll(remoteBitmaps)
            bitmapIcon(notification.smallIcon)?.let { add(it) }
        }
        if (resIds.isEmpty() && bitmaps.isEmpty()) return cue
        val resources = try {
            packageManager.getResourcesForApplication(NavNotificationSelector.GOOGLE_MAPS_PACKAGE)
        } catch (_: PackageManager.NameNotFoundException) {
            return cue
        }
        val names = resIds.mapNotNull { resId ->
            try {
                resources.getResourceEntryName(resId)
            } catch (_: Resources.NotFoundException) {
                null
            }
        }
        val pkg = NavNotificationSelector.GOOGLE_MAPS_PACKAGE
        val bitmapMatches = bitmaps.mapNotNull { bitmap ->
            NavManeuverBitmap.match(cue, bitmap) { name ->
                val id = resources.getIdentifier(name, "drawable", pkg)
                if (id == 0) null else try {
                    (resources.getDrawable(id, null) as? BitmapDrawable)?.bitmap
                } catch (_: Resources.NotFoundException) {
                    null
                }
            }
        }
        return NavNotificationParser.resolveManeuverIcons(cue, names, bitmapMatches)
    }

    private fun bitmapIcon(icon: Icon?): Bitmap? =
        if (icon?.type == Icon.TYPE_BITMAP) (icon.loadDrawable(this) as? BitmapDrawable)?.bitmap else null

    private fun mapsResourceId(icon: Icon?): Int? =
        icon?.takeIf { it.type == Icon.TYPE_RESOURCE && it.resPackage == NavNotificationSelector.GOOGLE_MAPS_PACKAGE }?.resId

    private class RemoteViewsData {
        val texts = linkedSetOf<String>()
        val descriptions = linkedSetOf<String>()
        val bitmaps = linkedSetOf<Bitmap>()
    }

    @Suppress("DEPRECATION")
    private fun extractRemoteViewsData(n: Notification): RemoteViewsData {
        val candidates = listOfNotNull(n.bigContentView, n.contentView, n.headsUpContentView)
        val data = RemoteViewsData()
        if (candidates.isEmpty()) return data
        val parent = FrameLayout(applicationContext)
        for (rv in candidates) {
            try {
                val inflated = rv.apply(applicationContext, parent)
                val iconId = inflated.resources.getIdentifier(
                    "nav_notification_icon", "id", NavNotificationSelector.GOOGLE_MAPS_PACKAGE,
                )
                collectViews(inflated, data, iconId)
            } catch (_: Throwable) {
            }
        }
        return data
    }

    private fun collectViews(v: View?, data: RemoteViewsData, maneuverIconId: Int) {
        if (v == null || v.visibility != View.VISIBLE) return
        if (maneuverIconId != 0 && v.id == maneuverIconId && v is ImageView) {
            (v.drawable as? BitmapDrawable)?.bitmap?.let { data.bitmaps.add(it) }
        }
        v.contentDescription?.toString()?.replace(Regex("""[^\S\r\n]+"""), " ")?.trim()?.let { desc ->
            if (desc.isNotEmpty() && desc.lowercase(Locale("tr", "TR")) !in ACTION_LABELS) {
                data.descriptions.add(desc)
            }
        }
        if (v is TextView) {
            val s = v.text?.toString()?.replace(Regex("""[^\S\r\n]+"""), " ")?.trim().orEmpty()
            if (s.isNotEmpty()) data.texts.add(s)
        }
        if (v is ViewGroup) {
            for (i in 0 until v.childCount) {
                collectViews(v.getChildAt(i), data, maneuverIconId)
            }
        }
    }

    companion object {
        private const val TAG = "Ex30Nav"
        private val ACTION_LABELS = setOf(
            "çık", "çıkış", "exit", "iptal", "cancel", "durdur", "stop",
            "kapat", "dismiss", "sesi kapat", "mute", "sesi aç", "unmute",
            "google haritalar", "google maps", "haritalar", "maps", "navigasyon",
        )

        /**
         * Pure source & metadata filter checked BEFORE reading notification text.
         * Accepts only active ongoing navigation guidance notifications from
         * Google Maps (`com.google.android.apps.maps`), rejecting group summaries,
         * non-ongoing notifications, and generic progress/service notifications.
         */
        fun isEligibleGoogleMapsGuidance(
            packageName: String?,
            flags: Int,
            category: String?,
            isOngoing: Boolean,
        ): Boolean {
            val ongoingFlag = isOngoing || (flags and Notification.FLAG_ONGOING_EVENT) != 0
            return ongoingFlag && isMapsNavigationPost(packageName, flags, category)
        }

        private fun isMapsNavigationPost(packageName: String?, flags: Int, category: String?): Boolean =
            packageName == NavNotificationSelector.GOOGLE_MAPS_PACKAGE &&
                (flags and Notification.FLAG_GROUP_SUMMARY) == 0 &&
                category == Notification.CATEGORY_NAVIGATION

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
