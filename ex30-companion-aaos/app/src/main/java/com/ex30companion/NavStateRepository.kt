package com.ex30companion

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.Locale

/**
 * Minimal, privacy-preserving turn-by-turn snapshot.
 *
 * Intentionally excludes street names, raw notification strings, and ETA:
 * only the canonical maneuver token, distance-to-maneuver string, and
 * optional roundabout exit number (1..9) are retained in memory or sent on
 * the wire.
 */
data class NavState(
    val active: Boolean = false,
    val maneuver: String = "none",
    val distance: String = "",
    val exitNumber: Int = 0,
)

object NavStateRepository {

    private val _state = MutableStateFlow(NavState())
    val state: StateFlow<NavState> = _state.asStateFlow()

    fun update(state: NavState) {
        _state.value = state
    }

    fun clear() {
        if (_state.value.active) {
            _state.value = NavState()
        }
    }
}

/**
 * Extracts only the next maneuver token, distance, and roundabout exit number
 * from navigation notification fields (English and Turkish), discarding any
 * street/address text immediately.
 */
object NavNotificationParser {

    private val TR_LOCALE = Locale("tr", "TR")

    private val DISTANCE_REGEX = Regex(
        """\b(\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd))\b""",
        RegexOption.IGNORE_CASE,
    )

    private val EXIT_REGEXES = listOf(
        Regex("""\b([1-9])\s*(?:\.|['’]?(?:nc[ıiuü]|üncü|uncu|ıncı|inci))?\s*ç[ıi]k[ıi][şs]"""),
        Regex("""\b([1-9])(?:st|nd|rd|th)?\s+exit\b"""),
        Regex("""\bexit\s+([1-9])\b"""),
    )

    private val WORD_EXITS = listOf(
        "birinci" to 1,
        "ikinci" to 2,
        "üçüncü" to 3,
        "ucuncu" to 3,
        "dördüncü" to 4,
        "dorduncu" to 4,
        "beşinci" to 5,
        "besinci" to 5,
        "altıncı" to 6,
        "altinci" to 6,
        "yedinci" to 7,
        "sekizinci" to 8,
        "dokuzuncu" to 9,
        "first" to 1,
        "second" to 2,
        "third" to 3,
        "fourth" to 4,
        "fifth" to 5,
        "sixth" to 6,
    )

    fun parse(
        title: String?,
        text: String?,
        bigText: String? = null,
    ): NavState {
        val cleanTitle = cleanSpace(title)
        val cleanText = cleanSpace(text)
        val cleanBig = cleanSpace(bigText)
        val body = if (cleanBig.length > cleanText.length && cleanBig.contains(cleanText, ignoreCase = true)) {
            cleanBig
        } else {
            cleanText.ifEmpty { cleanBig }
        }

        if (cleanTitle.isEmpty() && body.isEmpty()) {
            return NavState()
        }

        val distance = extractDistance(cleanTitle).ifEmpty { extractDistance(body) }
        val combinedRaw = listOf(cleanTitle, body).filter { it.isNotEmpty() }.joinToString(" ")
        val norm = normalizeForMatch(combinedRaw)

        val exitNumber = extractExitNumber(norm)
        val (maneuver, matchedKeyword) = classifyManeuver(norm, exitNumber)

        val hasNavSignal = distance.isNotEmpty() || matchedKeyword || exitNumber > 0
        if (!hasNavSignal) {
            return NavState()
        }

        return NavState(
            active = true,
            maneuver = maneuver,
            distance = distance,
            exitNumber = exitNumber,
        )
    }

    private fun cleanSpace(raw: String?): String =
        raw?.replace(Regex("""\s+"""), " ")?.trim().orEmpty()

    private fun normalizeForMatch(raw: String): String =
        raw.lowercase(TR_LOCALE).replace('ı', 'i')

    private fun extractDistance(src: String): String {
        val m = DISTANCE_REGEX.find(src) ?: return ""
        return m.groupValues[1].replace(Regex("""\s+"""), " ").trim().take(16)
    }

    private fun extractExitNumber(norm: String): Int {
        for (re in EXIT_REGEXES) {
            val match = re.find(norm)
            if (match != null) {
                val n = match.groupValues[1].toIntOrNull()
                if (n != null && n in 1..9) return n
            }
        }
        if (norm.contains("çikiş") || norm.contains("exit")) {
            for ((word, num) in WORD_EXITS) {
                if (norm.contains("$word çikiş") || norm.contains("$word exit")) {
                    return num
                }
            }
        }
        return 0
    }

    private fun classifyManeuver(norm: String, exitNumber: Int): Pair<String, Boolean> {
        fun hasAny(vararg needles: String): Boolean = needles.any { norm.contains(it) }

        val isRoundabout = hasAny("döner kavşak", "kavşağ", "kavşak", "roundabout", "rotary", "traffic circle")
        if (isRoundabout || exitNumber > 0) return "roundabout" to true
        if (hasAny("u dönüş", "u-turn", "u turn", "make a u")) return "uturn" to true
        if (hasAny("variş", "hedef", "varacağiniz", "destination", "arrive", "arrived", "your stop")) {
            return "destination" to true
        }
        if (hasAny("keskin sol", "tam sol", "sharp left")) return "sharp_left" to true
        if (hasAny("keskin sağ", "tam sağ", "sharp right")) return "sharp_right" to true
        if (hasAny("hafif sol", "sola doğru", "soldan devam", "sol şeritten", "solda kal", "keep left", "bear left", "slight left", "stay left")) {
            return "slight_left" to true
        }
        if (hasAny("hafif sağ", "sağa doğru", "sağdan devam", "sağ şeritten", "sağda kal", "keep right", "bear right", "slight right", "stay right")) {
            return "slight_right" to true
        }
        val isExitCue = hasAny("çikiş", "ayril", "exit", "ramp", "off-ramp")
        if (isExitCue && hasAny("sol", "left")) return "exit_left" to true
        if (isExitCue && hasAny("sağ", "right")) return "exit_right" to true
        if (hasAny("sola dön", "sola", "turn left", "left")) return "turn_left" to true
        if (hasAny("sağa dön", "sağa", "turn right", "right")) return "turn_right" to true
        if (hasAny("katil", "birleş", "merge")) return "merge" to true
        if (isExitCue) return "exit_right" to true
        if (hasAny("düz", "devam", "ileri", "straight", "continue", "head ", "keep going")) {
            return "straight" to true
        }
        return "straight" to false
    }
}
