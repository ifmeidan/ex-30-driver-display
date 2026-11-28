package com.ex30companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NavDebugHistoryTest {
    private val right = NavState(active = true, maneuver = "turn_right", distance = "80 m")
    private val sources = setOf(NavDebugSource.PRIMARY_TEXT, NavDebugSource.BITMAP_ICON)

    @Test fun distinguishReadDistanceOnlyAndUnreadableWithoutPersistingRawStrings() {
        val read = NavDebugEntry.fromCue(1, right, sources)
        assertEquals(NavDebugKind.READ, read.kind)
        assertTrue(read.sources.isEmpty())
        assertEquals("turn_right", read.cue.maneuver)

        val partial = NavDebugEntry.fromCue(2, NavState(true, "future", "1,5 km"), sources)
        assertEquals(NavDebugKind.DISTANCE_ONLY, partial.kind)
        assertEquals(sources, partial.sources)
        assertEquals("1,5 km", partial.cue.distance)

        val unreadable = NavDebugEntry.fromCue(3, NavState(true, "Secret street", "20 m to Secret street", "home", 8), sources)
        assertEquals(NavDebugKind.UNREADABLE, unreadable.kind)
        assertEquals(NavState(), unreadable.cue)
        assertEquals(sources, unreadable.sources)
    }

    @Test fun deduplicateRenewalsAndDisambiguateNewRoutesAndRealRemovals() {
        val session = NavDebugSession()
        val log = mutableListOf<NavDebugEntry>()
        val entry = NavDebugEntry.fromCue(10, right, emptySet())
        val end = NavDebugEntry(20, NavDebugKind.ENDED)
        session.record(null, end, log::add)
        session.record("route", entry, log::add)
        session.record("route", entry.copy(timeMillis = 15), log::add)
        session.record(null, end, log::add)
        session.record(null, end.copy(timeMillis = 25), log::add)
        session.record("route", entry.copy(timeMillis = 30), log::add)
        session.record("new-key", entry.copy(timeMillis = 40), log::add)
        session.reset()
        session.record("new-key", entry.copy(timeMillis = 50), log::add)
        assertEquals(listOf(10L, 20L, 30L, 40L, 50L), log.map { it.timeMillis })
    }

    @Test fun aFinalUpdateWithOnlyATurnOrDistanceDoesNotInventAnArrival() {
        val final = NavDebugEntry.fromCue(10, right, sources, finalUpdate = true)
        assertEquals(NavDebugKind.FINAL_UNREADABLE, final.kind)
        assertEquals(sources, final.sources)
        assertEquals("none", final.cue.arrival)
        assertEquals("turn_right", final.cue.maneuver)

        val arrival = NavState(active = true, maneuver = "destination", arrival = "destination_right")
        assertEquals(NavDebugKind.READ, NavDebugEntry.fromCue(11, arrival, sources, finalUpdate = true).kind)
    }
}
