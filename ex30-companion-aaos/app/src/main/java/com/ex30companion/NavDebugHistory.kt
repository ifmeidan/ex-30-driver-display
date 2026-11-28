package com.ex30companion

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteException
import android.database.sqlite.SQLiteOpenHelper
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.util.concurrent.Executors

enum class NavDebugKind { READ, DISTANCE_ONLY, UNREADABLE, FINAL_UNREADABLE, ENDED }

enum class NavDebugSource {
    CAR_TEXT, PRIMARY_TEXT, EXPANDED_TEXT, TICKER, SUBTEXT, REMOTE_TEXT,
    ICON_DESCRIPTION, RESOURCE_ICON, BITMAP_ICON
}

data class NavDebugEntry(
    val timeMillis: Long,
    val kind: NavDebugKind,
    val cue: NavState = NavState(),
    val sources: Set<NavDebugSource> = emptySet(),
) {
    companion object {
        private val maneuvers = setOf("straight", "turn_left", "turn_right", "slight_left", "slight_right",
            "sharp_left", "sharp_right", "uturn", "roundabout", "merge", "exit_left", "exit_right", "destination")
        private val arrivals = setOf("destination", "destination_left", "destination_right")
        private val distancePattern = Regex("""\d+(?:[.,]\d+)?\s?(?:km|m|mi|ft|yd)""", RegexOption.IGNORE_CASE)

        // The parser's strings are checked again at the persistence boundary. No free-form
        // field (text, resource name, notification key, or bitmap) can enter the database.
        internal fun fromCue(
            time: Long, state: NavState, sources: Set<NavDebugSource>, finalUpdate: Boolean = false,
        ): NavDebugEntry {
            val maneuver = state.maneuver.takeIf { it in maneuvers } ?: "none"
            val arrival = state.arrival.takeIf { it in arrivals } ?: "none"
            val distance = state.distance.takeIf { it.length <= 16 && distancePattern.matches(it) }.orEmpty()
            val exit = state.exitNumber.takeIf { maneuver == "roundabout" && it in 1..9 } ?: 0
            val kind = when {
                finalUpdate && (maneuver != "destination" || arrival == "none") -> NavDebugKind.FINAL_UNREADABLE
                maneuver != "none" || arrival != "none" -> NavDebugKind.READ
                distance.isNotEmpty() -> NavDebugKind.DISTANCE_ONLY
                else -> NavDebugKind.UNREADABLE
            }
            val cue = NavState(maneuver != "none" || arrival != "none" || distance.isNotEmpty(),
                maneuver, distance, arrival, exit)
            return NavDebugEntry(time, kind, cue, if (kind == NavDebugKind.READ) emptySet() else sources)
        }
    }
}

/** Dedupe one live guidance stream. Its Android notification key stays in process memory. */
internal class NavDebugSession {
    private var lastKey: String? = null
    private var lastEntry: NavDebugEntry? = null

    fun record(key: String?, entry: NavDebugEntry, append: (NavDebugEntry) -> Unit) {
        if (key == null && lastKey == null) return
        if (key == lastKey && lastEntry?.copy(timeMillis = entry.timeMillis) == entry) return
        append(entry)
        lastKey = key
        lastEntry = entry
    }

    fun reset() {
        lastKey = null
        lastEntry = null
    }
}

/** A single worker orders initial load, writes, resets, and user-requested clears. */
object NavDebugHistory {
    const val MAX_ENTRIES = 500
    private val worker = Executors.newSingleThreadExecutor()
    private val rows = MutableStateFlow<List<NavDebugEntry>>(emptyList())
    private val history = rows.asStateFlow()
    private val session = NavDebugSession()
    private var database: HistoryDatabase? = null
    private var loaded = false

    fun entries(context: Context): StateFlow<List<NavDebugEntry>> {
        withDatabase(context) { }
        return history
    }

    fun clear(context: Context) = withDatabase(context) { db ->
        db.writableDatabase.delete("entries", null, null)
        session.reset()
        rows.value = emptyList()
    }

    internal fun resetSession() = worker.execute { session.reset() }

    internal fun observe(
        context: Context, key: String, cue: NavState, sources: Set<NavDebugSource>, finalUpdate: Boolean = false,
    ) {
        if (!isRecording(context)) return
        record(context, key, NavDebugEntry.fromCue(System.currentTimeMillis(), cue, sources, finalUpdate))
    }

    internal fun ended(context: Context) {
        if (!isRecording(context)) return
        record(context, null, NavDebugEntry(System.currentTimeMillis(), NavDebugKind.ENDED))
    }

    private fun isRecording(context: Context) =
        BridgeSettings.navEnabled(context) && BridgeSettings.navDebugEnabled(context)

    private fun record(context: Context, key: String?, entry: NavDebugEntry) = withDatabase(context) { db ->
        session.record(key, entry) {
            db.append(it)
            rows.value = listOf(it) + rows.value.take(MAX_ENTRIES - 1)
        }
    }

    private fun withDatabase(context: Context, action: (HistoryDatabase) -> Unit) {
        val app = context.applicationContext
        worker.execute {
            try {
                val db = database ?: HistoryDatabase(app).also { database = it }
                if (!loaded) {
                    rows.value = db.readNewest()
                    loaded = true
                }
                action(db)
            } catch (_: SQLiteException) {
                Log.w("Ex30Nav", "Could not access the local navigation history")
            }
        }
    }

    private class HistoryDatabase(context: Context) :
        SQLiteOpenHelper(context, File(context.noBackupFilesDir, "nav-debug.db").path, null, 1) {
        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL("""CREATE TABLE entries (
                id INTEGER PRIMARY KEY, time_ms INTEGER NOT NULL, kind TEXT NOT NULL,
                maneuver TEXT NOT NULL, distance TEXT NOT NULL, arrival TEXT NOT NULL,
                exit_number INTEGER NOT NULL, sources TEXT NOT NULL)""")
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
            throw SQLiteException("Unsupported navigation history schema")
        }

        fun append(entry: NavDebugEntry) {
            val values = ContentValues().apply {
                put("time_ms", entry.timeMillis)
                put("kind", entry.kind.name)
                put("maneuver", entry.cue.maneuver)
                put("distance", entry.cue.distance)
                put("arrival", entry.cue.arrival)
                put("exit_number", entry.cue.exitNumber)
                put("sources", entry.sources.joinToString(",") { it.name })
            }
            val db = writableDatabase
            db.beginTransaction()
            try {
                val id = db.insertOrThrow("entries", null, values)
                db.delete("entries", "id <= ?", arrayOf((id - MAX_ENTRIES).toString()))
                db.setTransactionSuccessful()
            } finally {
                db.endTransaction()
            }
        }

        fun readNewest(): List<NavDebugEntry> = readableDatabase.rawQuery(
            "SELECT time_ms, kind, maneuver, distance, arrival, exit_number, sources FROM entries ORDER BY id DESC LIMIT ?",
            arrayOf(MAX_ENTRIES.toString()),
        ).use { cursor ->
            buildList {
                while (cursor.moveToNext()) {
                    val kind = NavDebugKind.valueOf(cursor.getString(1))
                    val maneuver = cursor.getString(2)
                    val distance = cursor.getString(3)
                    val arrival = cursor.getString(4)
                    val cue = NavState(maneuver != "none" || arrival != "none" || distance.isNotEmpty(),
                        maneuver, distance, arrival, cursor.getInt(5))
                    val sources = cursor.getString(6).split(',').filter { it.isNotEmpty() }.map(NavDebugSource::valueOf).toSet()
                    add(NavDebugEntry(cursor.getLong(0), kind, cue, sources))
                }
            }
        }
    }
}
