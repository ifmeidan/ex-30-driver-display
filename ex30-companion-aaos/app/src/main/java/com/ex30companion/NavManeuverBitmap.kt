package com.ex30companion

import android.graphics.Bitmap

// Resource names are looked up in the installed Maps package. Only native,
// pixel-identical BitmapDrawables qualify. No notification pixels are cached.
internal object NavManeuverBitmap {
    private val names = listOf(
        "turn_right", "turn_left", "turn_slight_right", "turn_slight_left",
        "arrive", "maneuver_destination", "destination_right", "destination_left",
        // Some icon sets reuse a turn arrow for a ramp. Such a match is ambiguous.
        "on_ramp_right", "on_ramp_left", "off_ramp_right", "off_ramp_left",
        "on_ramp_slight_right", "on_ramp_slight_left", "off_ramp_slight_right", "off_ramp_slight_left",
    ).flatMap { listOf("ic_$it", "ic_notification_$it") }

    fun match(state: NavState, bitmap: Bitmap, lookup: (String) -> Bitmap?): NavState? {
        if (bitmap.width !in 16..256 || bitmap.height !in 16..256 ||
            bitmap.config == Bitmap.Config.HARDWARE || !bitmap.hasAlpha()
        ) return null
        var match: NavState? = null
        for (name in names) {
            val reference = lookup(name) ?: continue
            if (reference.config != bitmap.config || reference.width != bitmap.width ||
                reference.height != bitmap.height || !bitmap.sameAs(reference)
            ) continue
            val cue = NavNotificationParser.withManeuverIcon(state, name) ?: return null
            if (match != null && match != cue) return null
            match = cue
        }
        return match?.takeIf { isMonochromeSilhouette(bitmap) }
    }

    private fun isMonochromeSilhouette(bitmap: Bitmap): Boolean {
        val row = IntArray(bitmap.width)
        var color = -1
        var transparent = false
        var opaque = false
        for (y in 0 until bitmap.height) {
            bitmap.getPixels(row, 0, row.size, 0, y, row.size, 1)
            for (pixel in row) {
                val alpha = pixel ushr 24
                if (alpha == 0) {
                    transparent = true
                } else {
                    val rgb = pixel and 0xffffff
                    if (color != -1 && rgb != color) return false
                    color = rgb
                    if (alpha == 255) opaque = true
                }
            }
        }
        return transparent && opaque
    }
}
