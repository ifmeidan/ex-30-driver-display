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
                    // Maps can replace the very same guidance notification with a
                    // dismissible arrival. It is a final post, never a renewable cue.
                    if (isMapsNavigationPost(sbn.packageName, sbn.notification.flags, sbn.notification.category)) {
                        val finalCue = parseNotification(sbn.notification, diagnose = true)
                        if (finalCue.maneuver == "destination" && finalCue.arrival != "none") {
                            publishCue(finalCue)
                            return
                        }
                    }
                    refreshFromActiveLocked()
                }
                return
            }

            val parsed = parseNotification(sbn.notification, diagnose = true)
            val activeKeys = currentEligibleKeysOrNull()
            if (parsed.active) {
                if (NavNotificationSelector.shouldAcceptPost(sbn.key, parsed, activeKeys)) {
                    publishCue(parsed)
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

    private fun publishCue(cue: NavState) {
        if (NavStateRepository.state.value != cue && Log.isLoggable(TAG, Log.DEBUG)) {
            Log.d(TAG, "maneuver=${cue.maneuver} distance=${cue.distance} arrival=${cue.arrival} exit=${cue.exitNumber}")
        }
        NavStateRepository.update(cue)
    }

    private fun parseNotification(notification: Notification, diagnose: Boolean = false): NavState {
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
                addAll(rvStrings)
                notification.tickerText?.let { add(it) }
            }.joinToString("\n"),
            iconHint = buildList {
                addAll(rvData.descriptions)
                extras.getCharSequence(Notification.EXTRA_PICTURE_CONTENT_DESCRIPTION)?.let { add(it.toString()) }
            }.joinToString("\n"),
            subText = extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString(),
        )
        val parsed = if (cue.maneuver == "none") withManeuverIcon(cue, notification, car, rvData.bitmaps) else cue
        if (diagnose && Log.isLoggable(TAG, Log.DEBUG)) {
            if (parsed.maneuver == "none") {
                val shape = "distanceOnly=${parsed.active} carText=${car?.contentTitle != null || car?.contentText != null}" +
                    " primaryText=${extras.containsKey(Notification.EXTRA_TITLE) || extras.containsKey(Notification.EXTRA_TEXT)}" +
                    " expandedText=${extras.containsKey(Notification.EXTRA_BIG_TEXT) || extras.containsKey(Notification.EXTRA_TEXT_LINES)}" +
                    " remoteText=${rvStrings.isNotEmpty()} iconDescription=${rvData.descriptions.isNotEmpty() || extras.containsKey(Notification.EXTRA_PICTURE_CONTENT_DESCRIPTION)}" +
                    " largeIconType=${notification.getLargeIcon()?.type} smallIconType=${notification.smallIcon?.type}" +
                    " carBitmap=${car?.largeIcon != null} remoteManeuverBitmap=${rvData.bitmaps.isNotEmpty()}"
                if (shape != lastUnknownShape) Log.d(TAG, "Unrecognized Maps guidance $shape")
                lastUnknownShape = shape
            } else {
                lastUnknownShape = null
            }
        }
        return parsed
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
            if (largeIcon == null && car?.largeIcon == null) bitmapIcon(notification.smallIcon)?.let { add(it) }
        }
        if (resIds.isEmpty() && bitmaps.isEmpty()) return cue
        val resources = try {
            packageManager.getResourcesForApplication(NavNotificationSelector.GOOGLE_MAPS_PACKAGE)
        } catch (_: PackageManager.NameNotFoundException) {
            return cue
        }
        for (resId in resIds) {
            val name = try {
                resources.getResourceEntryName(resId)
            } catch (_: Resources.NotFoundException) {
                continue
            }
            NavNotificationParser.withManeuverIcon(cue, name)?.let { return it }
        }
        val pkg = NavNotificationSelector.GOOGLE_MAPS_PACKAGE
        var matched: NavState? = null
        for (bitmap in bitmaps) {
            val candidate = NavManeuverBitmap.match(cue, bitmap) { name ->
                val id = resources.getIdentifier(name, "drawable", pkg)
                if (id == 0) null else try {
                    (resources.getDrawable(id, null) as? BitmapDrawable)?.bitmap
                } catch (_: Resources.NotFoundException) {
                    null
                }
            } ?: continue
            if (matched != null && matched != candidate) return cue
            matched = candidate
        }
        return matched ?: cue
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
