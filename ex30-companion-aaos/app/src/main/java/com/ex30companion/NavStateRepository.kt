package com.ex30companion

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.Locale

/**
 * Minimal, privacy-preserving turn-by-turn snapshot.
 *
 * Intentionally excludes street names, raw notification strings, and ETA:
 * only the canonical maneuver token, verified next-maneuver distance string,
 * arrival/destination-side token, and roundabout exit number (1..9) are
 * retained in memory or sent on the wire.
 */
data class NavState(
    val active: Boolean = false,
    val maneuver: String = "none",
    val distance: String = "",
    val arrival: String = "none",
    val exitNumber: Int = 0,
)

object NavStateRepository {

    private val lock = Any()
    private val _state = MutableStateFlow(NavState())
    val state: StateFlow<NavState> = _state.asStateFlow()

    @Volatile
    private var mutationSeq: Long = 0L

    @Volatile
    private var revalidator: (() -> Unit)? = null

    fun setRevalidator(fn: (() -> Unit)?) {
        revalidator = fn
    }

    fun currentSeq(): Long = synchronized(lock) { mutationSeq }

    /**
     * Authoritative update from a live notification callback (`onNotificationPosted`).
     * Bumps [mutationSeq] so any in-flight background refresh started earlier is discarded.
     */
    fun update(state: NavState): Long = synchronized(lock) {
        mutationSeq += 1L
        _state.value = state
        mutationSeq
    }

    /**
     * Authoritative clear from removal, opt-out, or permission revocation.
     * Always bumps [mutationSeq] even if already inactive so a concurrent
     * refresh cannot bring back a removed cue.
     */
    fun clear(): Long = synchronized(lock) {
        mutationSeq += 1L
        if (_state.value != NavState()) {
            _state.value = NavState()
        }
        mutationSeq
    }

    /**
     * Applies a refresh result only if no newer callback or clear has occurred
     * since [expectedSeq] was captured at the start of the refresh.
     */
    fun applyRefreshIfUnchanged(expectedSeq: Long, state: NavState): Boolean = synchronized(lock) {
        if (mutationSeq != expectedSeq) {
            false
        } else {
            _state.value = state
            true
        }
    }

    /**
     * Invokes the live [NavNotificationListenerService] revalidator. If no
     * listener is currently bound, clears any cached state and returns false.
     */
    fun revalidate(): Boolean {
        val fn = revalidator
        if (fn == null) {
            clear()
            return false
        }
        return try {
            fn.invoke()
            true
        } catch (_: Throwable) {
            clear()
            false
        }
    }

    internal fun resetForTests() {
        synchronized(lock) {
            mutationSeq = 0L
            _state.value = NavState()
            revalidator = null
        }
    }
}

/**
 * Tracks active notification keys from the selected navigation source
 * (`com.google.android.apps.maps`) in first-seen order so that:
 * - A genuinely newer notification key takes over from an older key.
 * - An older key that is still active cannot take control back simply by reposting.
 * - Periodic refreshes stay pinned to the currently selected key until it is removed
 *   or becomes inactive.
 */
object NavNotificationSelector {

    const val GOOGLE_MAPS_PACKAGE = "com.google.android.apps.maps"

    private val lock = Any()
    private val firstSeenSeqByKey = LinkedHashMap<String, Long>()
    private var nextSeq: Long = 0L
    private var currentKey: String? = null

    fun currentTrackedKey(): String? = synchronized(lock) { currentKey }

    /**
     * Evaluates whether a posted notification with [key] and [parsed] state
     * should immediately update the active cue, or whether it is an older
     * notification reposting while a newer key is still active.
     */
    fun shouldAcceptPost(
        key: String,
        parsed: NavState,
        activeEligibleKeys: Set<String>? = null,
    ): Boolean = synchronized(lock) {
        if (activeEligibleKeys != null) {
            firstSeenSeqByKey.keys.retainAll(activeEligibleKeys + key)
            if (currentKey != null && currentKey !in activeEligibleKeys && currentKey != key) {
                currentKey = null
            }
        }
        if (!parsed.active) {
            firstSeenSeqByKey.remove(key)
            if (currentKey == key) {
                currentKey = null
            }
            return false
        }
        val keySeq = firstSeenSeqByKey.getOrPut(key) {
            nextSeq += 1L
            nextSeq
        }
        val cur = currentKey
        if (cur == null || cur == key) {
            currentKey = key
            return true
        }
        val curSeq = firstSeenSeqByKey[cur]
        return if (curSeq == null || keySeq > curSeq) {
            currentKey = key
            true
        } else {
            // Older key reposting while a newer key is still tracked: ignore.
            false
        }
    }

    fun selectActive(
        candidates: List<Pair<String, NavState>>,
        pinnedKey: String? = currentTrackedKey(),
    ): Pair<String?, NavState> = synchronized(lock) {
        val activeCandidates = candidates.filter { it.second.active }
        val activeKeys = activeCandidates.map { it.first }.toSet()
        firstSeenSeqByKey.keys.retainAll(activeKeys)
        for ((k, _) in activeCandidates) {
            if (!firstSeenSeqByKey.containsKey(k)) {
                nextSeq += 1L
                firstSeenSeqByKey[k] = nextSeq
            }
        }

        val targetKey = pinnedKey ?: currentKey
        if (targetKey != null) {
            val pinned = activeCandidates.firstOrNull { it.first == targetKey }
            if (pinned != null) {
                currentKey = pinned.first
                return pinned
            }
        }

        // Fall back to the most recently introduced active key.
        val newest = activeCandidates.maxByOrNull { firstSeenSeqByKey[it.first] ?: 0L }
        currentKey = newest?.first
        return newest ?: (null to NavState())
    }

    fun reset() = synchronized(lock) {
        firstSeenSeqByKey.clear()
        nextSeq = 0L
        currentKey = null
    }
}

/**
 * Extracts next-maneuver direction, verified next-maneuver distance,
 * arrival/destination-side cues, and roundabout exit numbers from Google Maps
 * navigation notification fields (English and Turkish), discarding street
 * names and route-summary metadata immediately.
 */
object NavNotificationParser {

    private val TR_LOCALE = Locale("tr", "TR")

    private val DISTANCE_REGEX = Regex(
        """(?U)\b(\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd))\b""",
        RegexOption.IGNORE_CASE,
    )

    private val PURE_DISTANCE_REGEX = Regex(
        """(?U)^\s*\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd)\s*$""",
        RegexOption.IGNORE_CASE,
    )

    // Route-level / ETA / remaining-distance clauses that must never be treated
    // as a next-turn distance, turn direction, or arrival cue.
    private val ROUTE_SUMMARY_REGEX = Regex(
        """(?U)\b(?:\d+\s*(?:min|mins|minute|minutes|hr|hrs|hour|hours|dk|dakika|saat)|eta|remaining|kalan|trip\s+length|route\s+length|total|toplam|(?:distance\s+)?to\s+(?:your\s+|the\s+)?destination|from\s+(?:your\s+|the\s+)?destination|\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd)\s+(?:away|left|remaining|kalan|uzakta)|(?:destination|hedef|hedefiniz|var[ıi][şs])\s+is\s+\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd)\s+away|(?:hedefe|var[ıi][şs]a)\s+\d+(?:[.,]\d+)?\s*(?:km|m))\b|\b\d{1,2}:\d{2}\b""",
        RegexOption.IGNORE_CASE,
    )

    // Split on newlines, bullet/pipe/semicolon/dash separators, and commas that
    // are not decimal separators inside a number (e.g. "1,2 km").
    private val CLAUSE_SPLIT_REGEX = Regex("""\s*(?:[·•|;\r\n]+|(?<!\d),|,(?!\d)|\s-\s)\s*""")

    private val ROUNDABOUT_REGEX = Regex(
        """(?U)\b(?:roundabout|rotary|traffic\s+circle|döner\s+kavşak|kavşağ[ıi]|kavşakta|kavşak)\b""",
    )

    private val EXIT_REGEXES = listOf(
        Regex("""(?U)\b([1-9])\s*(?:\.|['’]?(?:nc[ıiuü]|üncü|uncu|ıncı|inci))?\s*ç[ıi]k[ıi][şs]\b"""),
        Regex("""(?U)\b([1-9])(?:st|nd|rd|th)?\s+exit\b"""),
        Regex("""(?U)\bexit\s+([1-9])\b"""),
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

    private val UTURN_REGEX = Regex("""(?U)\b(?:u\s+dönüş[üu]?|u-turn|u\s+turn|make\s+a\s+u)\b""")
    private val SHARP_LEFT_REGEX = Regex("""(?U)\b(?:keskin\s+sol[a]?|tam\s+sol[a]?|sharp\s+left)\b""")
    private val SHARP_RIGHT_REGEX = Regex("""(?U)\b(?:keskin\s+sağ[a]?|tam\s+sağ[a]?|sharp\s+right)\b""")
    private val SLIGHT_LEFT_REGEX = Regex(
        """(?U)\b(?:hafif\s+sol[a]?|sola\s+doğru|soldan\s+devam|sol\s+şeritten|solda\s+kal[ıi]?n?|keep\s+left|bear\s+left|slight\s+left|stay\s+left)\b""",
    )
    private val SLIGHT_RIGHT_REGEX = Regex(
        """(?U)\b(?:hafif\s+sağ[a]?|sağa\s+doğru|sağdan\s+devam|sağ\s+şeritten|sağda\s+kal[ıi]?n?|keep\s+right|bear\s+right|slight\s+right|stay\s+right)\b""",
    )
    private val EXIT_CUE_REGEX = Regex("""(?U)\b(?:ç[ıi]k[ıi][şs]|ayr[ıi]l[ıi]?n?|exit|ramp|off-ramp)\b""")
    private val LEFT_WORD_REGEX = Regex("""(?U)\b(?:sola|sol|soldan|left)\b""")
    private val RIGHT_WORD_REGEX = Regex("""(?U)\b(?:sağa|sağ|sağdan|right)\b""")
    private val TURN_LEFT_REGEX = Regex(
        """(?U)\b(?:sola\s+dön[üu]?n?|sola|turn\s+left|take\s+the\s+left|left\s+onto|left\s+turn|left\s+in)\b|^\s*(?:left|sol)\s*$""",
    )
    private val TURN_RIGHT_REGEX = Regex(
        """(?U)\b(?:sağa\s+dön[üu]?n?|sağa|turn\s+right|take\s+the\s+right|right\s+onto|right\s+turn|right\s+in)\b|^\s*(?:right|sağ)\s*$""",
    )
    private val MERGE_REGEX = Regex("""(?U)\b(?:kat[ıi]l[ıi]?n?|birleş[i]?n?|merge)\b""")
    private val STRAIGHT_REGEX = Regex(
        """(?U)\b(?:düz\s+devam|düz\s+gidin|düz|ileri\s+devam|straight|continue\s+straight|continue\s+on|continue\s+for|head\s+(?:north|south|east|west|straight)|keep\s+going)\b""",
    )

    private val ARRIVAL_NOUN_REGEX = Regex(
        """(?U)\b(?:destination|your\s+stop|var[ıi][şs]\s+noktas[ıi]|var[ıi][şs]|hedefiniz|hedef|varacağ[ıi]n[ıi]z\s+yer)\b""",
    )
    private val ARRIVAL_VERB_REGEX = Regex(
        """(?U)\b(?:you\s+have\s+arrived|arrived|arrive\s+at|arriving\s+at|ulaşt[ıi]n[ıi]z|geldiniz|vard[ıi]n[ıi]z)\b""",
    )
    private val ON_RIGHT_SIDE_REGEX = Regex("""(?U)\b(?:on\s+the\s+right|is\s+on\s+the\s+right|sağda|sağ\s+tarafta)\b""")
    private val ON_LEFT_SIDE_REGEX = Regex("""(?U)\b(?:on\s+the\s+left|is\s+on\s+the\s+left|solda|sol\s+tarafta)\b""")
    private val AWAY_SUMMARY_REGEX = Regex("""(?U)\b(?:away|uzakta|kald[ıi]|remaining|left)\b""")

    fun parse(
        title: String?,
        text: String?,
        bigText: String? = null,
        iconHint: String? = null,
    ): NavState {
        val titleClauses = extractClauses(title)
        val textClauses = extractClauses(text)
        val bigClauses = extractClauses(bigText)
        val hintClauses = extractClauses(iconHint)
        val bodyClauses = if (bigClauses.size >= textClauses.size && bigClauses.isNotEmpty()) {
            bigClauses
        } else {
            textClauses
        }

        if (titleClauses.isEmpty() && bodyClauses.isEmpty() && hintClauses.isEmpty()) {
            return NavState()
        }

        var turnManeuver = "none"
        var exitNumber = 0
        var arrivalCue = "none"

        val allClauses = buildList {
            addAll(hintClauses)
            addAll(titleClauses)
            for (c in bodyClauses) {
                if (c !in titleClauses) add(c)
            }
        }

        val nonArrivalTitleClauses = mutableListOf<String>()
        val nonArrivalBodyClauses = mutableListOf<String>()

        for (clause in titleClauses) {
            val arr = classifyArrivalClause(normalizeForMatch(clause))
            if (arr != null) {
                if (arrivalCue == "none") arrivalCue = arr
            } else {
                nonArrivalTitleClauses.add(clause)
            }
        }
        for (clause in bodyClauses) {
            val arr = classifyArrivalClause(normalizeForMatch(clause))
            if (arr != null) {
                if (arrivalCue == "none") arrivalCue = arr
            } else {
                nonArrivalBodyClauses.add(clause)
            }
        }

        val nonArrivalNorms = mutableListOf<String>()
        for (clause in allClauses) {
            val norm = normalizeForMatch(clause)
            val arr = classifyArrivalClause(norm)
            if (arr != null) {
                if (arrivalCue == "none") arrivalCue = arr
                continue
            }
            nonArrivalNorms.add(norm)
            val (m, exitNum) = classifyTurnClause(norm)
            if (m != "none" && turnManeuver == "none") {
                turnManeuver = m
                exitNumber = exitNum
            }
        }
        if (turnManeuver == "roundabout" && exitNumber == 0) {
            exitNumber = extractExitNumber(nonArrivalNorms.joinToString(" "))
        }

        val distance = extractVerifiedTurnDistance(nonArrivalTitleClauses, nonArrivalBodyClauses)
        val effectiveManeuver = when {
            turnManeuver != "none" -> turnManeuver
            arrivalCue != "none" -> "destination"
            else -> "none"
        }

        val hasNavSignal = distance.isNotEmpty() || turnManeuver != "none" || arrivalCue != "none"
        if (!hasNavSignal) {
            return NavState()
        }

        return NavState(
            active = true,
            maneuver = effectiveManeuver,
            distance = distance,
            arrival = arrivalCue,
            exitNumber = exitNumber,
        )
    }

    private fun extractClauses(raw: String?): List<String> {
        if (raw.isNullOrBlank()) return emptyList()
        return raw.split(CLAUSE_SPLIT_REGEX)
            .map { cleanSpace(it) }
            .filter { it.isNotEmpty() && !ROUTE_SUMMARY_REGEX.containsMatchIn(it) }
    }

    private fun extractVerifiedTurnDistance(
        titleClauses: List<String>,
        bodyClauses: List<String>,
    ): String {
        for (clause in titleClauses) {
            if (! isNonTurnDestinationMetadata(normalizeForMatch(clause))) {
                val d = extractDistance(clause)
                if (d.isNotEmpty()) return d
            }
        }
        if (titleClauses.isEmpty()) {
            for (clause in bodyClauses) {
                if (!isNonTurnDestinationMetadata(normalizeForMatch(clause))) {
                    val d = extractDistance(clause)
                    if (d.isNotEmpty()) return d
                }
            }
        } else {
            for (clause in bodyClauses) {
                if (PURE_DISTANCE_REGEX.matches(clause)) {
                    val d = extractDistance(clause)
                    if (d.isNotEmpty()) return d
                }
            }
        }
        return ""
    }

    private fun isNonTurnDestinationMetadata(norm: String): Boolean {
        if (ARRIVAL_NOUN_REGEX.containsMatchIn(norm) && AWAY_SUMMARY_REGEX.containsMatchIn(norm)) {
            return true
        }
        return false
    }

    private fun classifyArrivalClause(norm: String): String? {
        if (AWAY_SUMMARY_REGEX.containsMatchIn(norm) && DISTANCE_REGEX.containsMatchIn(norm)) {
            return null
        }
        val hasNoun = ARRIVAL_NOUN_REGEX.containsMatchIn(norm)
        val hasVerb = ARRIVAL_VERB_REGEX.containsMatchIn(norm)
        if (!hasNoun && !hasVerb) return null

        // Only treat as an arrival cue if it indicates side, arrival action, or
        // is a standalone destination arrival header (not "Destination: 12 km away").
        if (hasNoun && ON_RIGHT_SIDE_REGEX.containsMatchIn(norm)) return "destination_right"
        if (hasNoun && ON_LEFT_SIDE_REGEX.containsMatchIn(norm)) return "destination_left"
        if (hasVerb) return "destination"
        if (Regex("""^\s*(?:destination|var[ıi][şs]|hedef|var[ıi][şs]\s+noktas[ıi])\s*$""").matches(norm)) {
            return "destination"
        }
        return null
    }

    private fun classifyTurnClause(norm: String): Pair<String, Int> {
        if ( isNonTurnDestinationMetadata(norm)) return "none" to 0

        if (ROUNDABOUT_REGEX.containsMatchIn(norm)) {
            return "roundabout" to extractExitNumber(norm)
        }
        if (UTURN_REGEX.containsMatchIn(norm)) return "uturn" to 0
        if (SHARP_LEFT_REGEX.containsMatchIn(norm)) return "sharp_left" to 0
        if (SHARP_RIGHT_REGEX.containsMatchIn(norm)) return "sharp_right" to 0
        if (SLIGHT_LEFT_REGEX.containsMatchIn(norm)) return "slight_left" to 0
        if (SLIGHT_RIGHT_REGEX.containsMatchIn(norm)) return "slight_right" to 0

        if (EXIT_CUE_REGEX.containsMatchIn(norm)) {
            if (LEFT_WORD_REGEX.containsMatchIn(norm)) return "exit_left" to 0
            if (RIGHT_WORD_REGEX.containsMatchIn(norm)) return "exit_right" to 0
            // Numbered or bare highway exit without left/right or roundabout -> unknown direction.
            return "none" to 0
        }

        if (TURN_LEFT_REGEX.containsMatchIn(norm)) return "turn_left" to 0
        if (TURN_RIGHT_REGEX.containsMatchIn(norm)) return "turn_right" to 0
        if (MERGE_REGEX.containsMatchIn(norm)) return "merge" to 0
        if (STRAIGHT_REGEX.containsMatchIn(norm)) return "straight" to 0

        return "none" to 0
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
}
