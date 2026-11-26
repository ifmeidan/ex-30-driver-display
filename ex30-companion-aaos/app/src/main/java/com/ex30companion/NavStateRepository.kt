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

data class VersionedNavState(
    val seq: Long = 0L,
    val state: NavState = NavState(),
)

object NavStateRepository {

    private val lock = Any()
    private val _versioned = MutableStateFlow(VersionedNavState())
    val versionedState: StateFlow<VersionedNavState> = _versioned.asStateFlow()

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

    fun currentVersioned(): VersionedNavState = synchronized(lock) { _versioned.value }

    /**
     * Authoritative update from a live notification callback (`onNotificationPosted`).
     * Bumps [mutationSeq] so any in-flight background refresh started earlier is discarded.
     */
    fun update(state: NavState): VersionedNavState = synchronized(lock) {
        mutationSeq += 1L
        val v = VersionedNavState(mutationSeq, state)
        _versioned.value = v
        _state.value = state
        v
    }

    /**
     * Authoritative clear from removal, opt-out, or permission revocation.
     * Always bumps [mutationSeq] even if already inactive so a concurrent
     * refresh or paused renewal cannot bring back a removed cue.
     */
    fun clear(): VersionedNavState = synchronized(lock) {
        mutationSeq += 1L
        val empty = NavState()
        val v = VersionedNavState(mutationSeq, empty)
        _versioned.value = v
        if (_state.value != empty) {
            _state.value = empty
        }
        v
    }

    /**
     * Applies a refresh result only if no newer callback or clear has occurred
     * since [expectedSeq] was captured at the start of the refresh.
     */
    fun applyRefreshIfUnchanged(expectedSeq: Long, state: NavState): VersionedNavState? = synchronized(lock) {
        if (mutationSeq != expectedSeq) {
            null
        } else {
            val v = if (_state.value != state) {
                mutationSeq += 1L
                VersionedNavState(mutationSeq, state).also {
                    _versioned.value = it
                    _state.value = state
                }
            } else {
                _versioned.value
            }
            v
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
            val empty = NavState()
            _versioned.value = VersionedNavState(0L, empty)
            _state.value = empty
            revalidator = null
        }
    }
}

/**
 * Single ordered publisher for `type: "nav"` frames with generation checking
 * at staging time. Serializes live state changes, 5s renewals, opt-out clears,
 * and post-`hello` reconnect seeds so a paused renewal or stale callback can
 * never stage an older cue after a newer clear or turn update.
 */
class NavFramePublisher(
    private val nowMs: () -> Long = { System.currentTimeMillis() },
) {
    private val lock = Any()
    private var lastStagedSeq: Long = -1L
    private var lastDeliveredState: NavState? = null
    private var pendingLine: String? = null

    fun stageChange(versioned: VersionedNavState, optedIn: Boolean): Boolean = synchronized(lock) {
        if (versioned.seq < lastStagedSeq) return false
        val effective = if (optedIn) versioned.state else NavState()
        lastStagedSeq = versioned.seq
        if (effective == lastDeliveredState && pendingLine == null) return false
        lastDeliveredState = effective
        pendingLine = buildFrameJson(effective, nowMs())
        true
    }

    fun stageRenewal(
        versioned: VersionedNavState,
        latestRepoSeq: Long = NavStateRepository.currentSeq(),
        optedIn: Boolean,
    ): Boolean = synchronized(lock) {
        if (!optedIn || !versioned.state.active) return false
        // Discard if a newer mutation occurred after the renewal read the state.
        if (versioned.seq < lastStagedSeq || versioned.seq != latestRepoSeq) return false
        lastStagedSeq = versioned.seq
        lastDeliveredState = versioned.state
        pendingLine = buildFrameJson(versioned.state, nowMs())
        true
    }

    fun stageReconnectSeed(
        versioned: VersionedNavState,
        latestRepoSeq: Long = NavStateRepository.currentSeq(),
        optedIn: Boolean,
    ): Boolean = synchronized(lock) {
        pendingLine = null
        lastDeliveredState = null
        if (!optedIn) return false
        if (versioned.seq != latestRepoSeq) return false
        lastStagedSeq = maxOf(lastStagedSeq, versioned.seq)
        lastDeliveredState = versioned.state
        pendingLine = buildFrameJson(versioned.state, nowMs())
        true
    }

    fun stageOptOutClear(clearVersion: VersionedNavState): Boolean = synchronized(lock) {
        lastStagedSeq = maxOf(lastStagedSeq, clearVersion.seq)
        val empty = NavState()
        lastDeliveredState = empty
        pendingLine = buildFrameJson(empty, nowMs())
        true
    }

    fun pollLine(): String? = synchronized(lock) {
        val line = pendingLine
        pendingLine = null
        line
    }

    fun clearPending() = synchronized(lock) {
        pendingLine = null
        lastDeliveredState = null
    }

    companion object {
        private fun escapeJson(s: String): String = buildString(s.length + 4) {
            for (ch in s) {
                when (ch) {
                    '\\' -> append("\\\\")
                    '"' -> append("\\\"")
                    '\n' -> append("\\n")
                    '\r' -> append("\\r")
                    '\t' -> append("\\t")
                    else -> append(ch)
                }
            }
        }

        fun buildFrameJson(nav: NavState, tsMs: Long): String {
            val act = nav.active
            val man = escapeJson(if (act) nav.maneuver else "none")
            val dist = escapeJson(if (act) nav.distance else "")
            val arr = escapeJson(if (act) nav.arrival else "none")
            val ex = if (act) nav.exitNumber else 0
            return """{"type":"nav","ts":$tsMs,"src":"aaos","nav":{"active":$act,"maneuver":"$man","distance":"$dist","arrival":"$arr","exit":$ex}}"""
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
 *
 * Uses portable Unicode letter/digit lookarounds (`(?<![\p{L}\p{N}_])` and
 * `(?![\p{L}\p{N}_])`) instead of the JVM-only `(?U)` inline flag, which
 * throws `PatternSyntaxException` on Android's ICU regex engine.
 */
object NavNotificationParser {

    private val TR_LOCALE = Locale("tr", "TR")

    // Portable Unicode word boundaries valid on both Android ICU and desktop JVM.
    private const val WB = """(?<![\p{L}\p{N}_])"""
    private const val WE = """(?![\p{L}\p{N}_])"""

    private val DISTANCE_REGEX = Regex(
        """$WB(\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd))$WE""",
        RegexOption.IGNORE_CASE,
    )

    private val PURE_DISTANCE_REGEX = Regex(
        """^\s*\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd)\s*$""",
        RegexOption.IGNORE_CASE,
    )

    // Route-level / ETA / remaining-distance clauses that must never be treated
    // as a next-turn distance, turn direction, or arrival cue.
    private val ROUTE_SUMMARY_REGEX = Regex(
        """$WB(?:\d+\s*(?:min|mins|minute|minutes|hr|hrs|hour|hours|dk|dakika|saat)|eta|remaining|kalan|trip\s+length|route\s+length|total|toplam|(?:distance\s+)?to\s+(?:your\s+|the\s+)?destination|from\s+(?:your\s+|the\s+)?destination|\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd)\s+(?:away|left|remaining|kalan|uzakta)|(?:destination|hedef|hedefiniz|var[ıi][şs])\s+is\s+\d+(?:[.,]\d+)?\s*(?:km|m|mi|ft|yd)\s+away|(?:hedefe|var[ıi][şs]a)\s+\d+(?:[.,]\d+)?\s*(?:km|m))$WE|$WB\d{1,2}:\d{2}$WE""",
        RegexOption.IGNORE_CASE,
    )

    private val PAREN_CLAUSE_REGEX = Regex("""\(([^)]*)\)|\[([^\]]*)\]""")

    // Split on newlines, bullet/pipe/semicolon/em-dash/en-dash/spaced-hyphen
    // separators, and commas that are not decimal separators inside a number (e.g. "1,2 km").
    private val CLAUSE_SPLIT_REGEX = Regex(
        """\s*(?:[·•|;\r\n\u2013\u2014]+|(?<!\d),|,(?!\d)|\s+-\s+)\s*""",
    )

    // In Turkish, only explicit "döner kavşak" (not a generic "kavşak" junction) is a roundabout.
    private val ROUNDABOUT_REGEX = Regex(
        """$WB(?:roundabout|rotary|traffic\s+circle|döner\s+kavşak(?:ta|tan)?|döner\s+kavşağ[\p{L}]*)$WE""",
    )

    private val EXIT_REGEXES = listOf(
        Regex("""$WB([1-9])\s*(?:\.|['’]?(?:nc[ıiuü]|üncü|uncu|ıncı|inci))?\s*ç[ıi]k[ıi][şs]$WE"""),
        Regex("""$WB([1-9])(?:st|nd|rd|th)?\s+exit$WE"""),
        Regex("""${WB}exit\s+([1-9])$WE"""),
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

    private val UTURN_REGEX = Regex("""$WB(?:u\s+dönüş[üu]?|u-turn|u\s+turn|make\s+a\s+u)$WE""")
    private val SHARP_LEFT_REGEX = Regex("""$WB(?:keskin\s+sol[a]?|tam\s+sol[a]?|sharp\s+left)$WE""")
    private val SHARP_RIGHT_REGEX = Regex("""$WB(?:keskin\s+sağ[a]?|tam\s+sağ[a]?|sharp\s+right)$WE""")
    private val SLIGHT_LEFT_REGEX = Regex(
        """$WB(?:hafif\s+sol[a]?|sola\s+doğru|soldan\s+devam|sol\s+şeritten|solda\s+kal[ıi]?n?|keep\s+left|bear\s+left|slight\s+left|stay\s+left)$WE""",
    )
    private val SLIGHT_RIGHT_REGEX = Regex(
        """$WB(?:hafif\s+sağ[a]?|sağa\s+doğru|sağdan\s+devam|sağ\s+şeritten|sağda\s+kal[ıi]?n?|keep\s+right|bear\s+right|slight\s+right|stay\s+right)$WE""",
    )
    private val EXIT_CUE_REGEX = Regex("""$WB(?:ç[ıi]k[ıi][şs](?:tan)?|ayr[ıi]l[ıi]?n?|exit|ramp|off-ramp)$WE""")
    private val LEFT_WORD_REGEX = Regex("""$WB(?:sola|sol|soldan|left)$WE""")
    private val RIGHT_WORD_REGEX = Regex("""$WB(?:sağa|sağ|sağdan|right)$WE""")
    private val TURN_LEFT_REGEX = Regex(
        """$WB(?:sola\s+dön[üu]?n?|sola|turn\s+left|take\s+the\s+left|left\s+onto|left\s+turn|left\s+in)$WE|^\s*(?:left|sol)\s*$""",
    )
    private val TURN_RIGHT_REGEX = Regex(
        """$WB(?:sağa\s+dön[üu]?n?|sağa|turn\s+right|take\s+the\s+right|right\s+onto|right\s+turn|right\s+in)$WE|^\s*(?:right|sağ)\s*$""",
    )
    private val MERGE_REGEX = Regex("""$WB(?:kat[ıi]l[ıi]?n?|birleş[i]?n?|merge)$WE""")
    private val STRAIGHT_REGEX = Regex(
        """$WB(?:düz\s+devam|düz\s+gidin|düz|ileri\s+devam|straight|continue\s+straight|continue\s+on|continue\s+for|head\s+(?:north|south|east|west|straight)|keep\s+going)$WE""",
    )

    private val ARRIVAL_NOUN_REGEX = Regex(
        """$WB(?:destination|your\s+stop|var[ıi][şs]\s+noktas[ıi]|var[ıi][şs]|hedefiniz|hedef|varacağ[ıi]n[ıi]z\s+yer)$WE""",
    )
    private val ARRIVAL_VERB_REGEX = Regex(
        """$WB(?:you\s+have\s+arrived|arrived|arrive\s+at|arriving\s+at|ulaşt[ıi]n[ıi]z|geldiniz|vard[ıi]n[ıi]z)$WE""",
    )
    private val ON_RIGHT_SIDE_REGEX = Regex("""$WB(?:on\s+the\s+right|is\s+on\s+the\s+right|sağda|sağ\s+tarafta)$WE""")
    private val ON_LEFT_SIDE_REGEX = Regex("""$WB(?:on\s+the\s+left|is\s+on\s+the\s+left|solda|sol\s+tarafta)$WE""")
    // Note: "left" is excluded here so "Your destination is on the left in 300 m"
    // is never mistaken for an away-summary; "<distance> left" is already caught
    // by ROUTE_SUMMARY_REGEX.
    private val AWAY_SUMMARY_REGEX = Regex("""$WB(?:away|uzakta|kald[ıi]|remaining)$WE""")

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

        if (titleClauses.isEmpty() && textClauses.isEmpty() && bigClauses.isEmpty() && hintClauses.isEmpty()) {
            return NavState()
        }

        // Preserve distinct field roles: primary fields (title, text, iconHint)
        // are evaluated ahead of expanded bigText metadata so a same-length
        // expanded metadata field never hides an explicit turn in text.
        val orderedClauses = buildList {
            addAll(hintClauses)
            addAll(titleClauses)
            for (c in textClauses) {
                if (c !in this) add(c)
            }
            for (c in bigClauses) {
                if (c !in this) add(c)
            }
        }

        var turnManeuver = "none"
        var exitNumber = 0
        var arrivalCue = "none"
        val nonArrivalNorms = mutableListOf<String>()
        val arrivalClausesWithDistance = mutableListOf<String>()

        val nonArrivalTitleClauses = mutableListOf<String>()
        val nonArrivalTextClauses = mutableListOf<String>()
        val nonArrivalBigClauses = mutableListOf<String>()

        for (clause in titleClauses) {
            val norm = normalizeForMatch(clause)
            val arr = classifyArrivalClause(norm)
            if (arr != null) {
                if (arrivalCue == "none") arrivalCue = arr
                arrivalClausesWithDistance.add(clause)
            } else {
                nonArrivalTitleClauses.add(clause)
            }
        }
        for (clause in textClauses) {
            val norm = normalizeForMatch(clause)
            val arr = classifyArrivalClause(norm)
            if (arr != null) {
                if (arrivalCue == "none") arrivalCue = arr
                arrivalClausesWithDistance.add(clause)
            } else {
                nonArrivalTextClauses.add(clause)
            }
        }
        for (clause in bigClauses) {
            val norm = normalizeForMatch(clause)
            val arr = classifyArrivalClause(norm)
            if (arr != null) {
                if (arrivalCue == "none") arrivalCue = arr
                arrivalClausesWithDistance.add(clause)
            } else {
                nonArrivalBigClauses.add(clause)
            }
        }

        for (clause in orderedClauses) {
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

        val turnDistance = extractVerifiedTurnDistance(
            titleClauses = nonArrivalTitleClauses,
            textClauses = nonArrivalTextClauses,
            bigClauses = nonArrivalBigClauses,
        )
        // If there is no separate turn distance, allow a verified step distance
        // attached directly to an arrival cue (e.g. "Your destination is on the left in 300 m").
        val distance = if (turnDistance.isNotEmpty()) {
            turnDistance
        } else if (turnManeuver == "none") {
            arrivalClausesWithDistance.firstNotNullOfOrNull { c ->
                extractDistance(c).takeIf { it.isNotEmpty() }
            }.orEmpty()
        } else {
            ""
        }

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
        // Expand parenthesized/bracketed segments into separate clauses so
        // "Turn left in 300 m (12 km to destination)" drops only the summary.
        val expanded = PAREN_CLAUSE_REGEX.replace(raw) { m ->
            val inner = m.groupValues[1].ifEmpty { m.groupValues[2] }
            " · $inner · "
        }
        return expanded.split(CLAUSE_SPLIT_REGEX)
            .map { cleanSpace(it) }
            .filter { it.isNotEmpty() && !ROUTE_SUMMARY_REGEX.containsMatchIn(it) }
    }

    private fun extractVerifiedTurnDistance(
        titleClauses: List<String>,
        textClauses: List<String>,
        bigClauses: List<String>,
    ): String {
        for (clause in titleClauses) {
            if (!isNonTurnDestinationMetadata(normalizeForMatch(clause))) {
                val d = extractDistance(clause)
                if (d.isNotEmpty()) return d
            }
        }
        if (titleClauses.isEmpty()) {
            for (clause in textClauses) {
                if (!isNonTurnDestinationMetadata(normalizeForMatch(clause))) {
                    val d = extractDistance(clause)
                    if (d.isNotEmpty()) return d
                }
            }
            for (clause in bigClauses) {
                if (!isNonTurnDestinationMetadata(normalizeForMatch(clause))) {
                    val d = extractDistance(clause)
                    if (d.isNotEmpty()) return d
                }
            }
        } else {
            for (clause in textClauses + bigClauses) {
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

        if (hasNoun && ON_RIGHT_SIDE_REGEX.containsMatchIn(norm)) return "destination_right"
        if (hasNoun && ON_LEFT_SIDE_REGEX.containsMatchIn(norm)) return "destination_left"
        if (hasVerb) return "destination"
        if (Regex("""^\s*(?:destination|var[ıi][şs]|hedef|var[ıi][şs]\s+noktas[ıi])\s*$""").matches(norm)) {
            return "destination"
        }
        return null
    }

    private fun classifyTurnClause(norm: String): Pair<String, Int> {
        if (isNonTurnDestinationMetadata(norm)) return "none" to 0

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
