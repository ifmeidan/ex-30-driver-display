package com.ex30companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NavNotificationParserTest {

    @Test
    fun `selector stays pinned to activeNavKey until that notification is gone`() {
        val rightTurn = NavNotificationParser.parse(title = "300 m", text = "Turn right")
        val leftTurn = NavNotificationParser.parse(title = "150 m", text = "Turn left")
        // Even if the other notification appears first in activeNotifications,
        // refreshing while key-right is still active must not switch to key-left.
        val candidates = listOf(
            "key-left" to leftTurn,
            "key-right" to rightTurn,
        )

        val (keptKey, keptState) = NavNotificationSelector.selectActive(candidates, currentKey = "key-right")
        assertEquals("key-right", keptKey)
        assertEquals("turn_right", keptState.maneuver)
        assertEquals("300 m", keptState.distance)

        // Once key-right is gone, selector falls back to the remaining active notification.
        val afterRemoval = listOf("key-left" to leftTurn)
        val (newKey, newState) = NavNotificationSelector.selectActive(afterRemoval, currentKey = "key-right")
        assertEquals("key-left", newKey)
        assertEquals("turn_left", newState.maneuver)

        // When no active notifications remain, selector clears key and state.
        val (emptyKey, emptyState) = NavNotificationSelector.selectActive(emptyList(), currentKey = "key-left")
        assertNull(emptyKey)
        assertFalse(emptyState.active)
    }

    @Test
    fun `route summary to destination is ignored on its own and does not override real maneuver`() {
        val summaryOnly = NavNotificationParser.parse(
            title = "12 km to destination",
            text = null,
        )
        assertFalse(summaryOnly.active)
        assertEquals("none", summaryOnly.maneuver)
        assertEquals("", summaryOnly.distance)

        val paired = NavNotificationParser.parse(
            title = "Turn left in 300 m",
            text = "12 km to destination",
        )
        assertTrue(paired.active)
        assertEquals("turn_left", paired.maneuver)
        assertEquals("300 m", paired.distance)

        val pairedSingleField = NavNotificationParser.parse(
            title = "Turn left in 300 m · 12 km to destination",
            text = null,
        )
        assertTrue(pairedSingleField.active)
        assertEquals("turn_left", pairedSingleField.maneuver)
        assertEquals("300 m", pairedSingleField.distance)
    }

    @Test
    fun `ambiguous exit and trip length summary are omitted`() {
        val bareExit = NavNotificationParser.parse(title = "Take the exit", text = null)
        assertFalse(bareExit.active)
        assertEquals("none", bareExit.maneuver)

        val tripLen = NavNotificationParser.parse(title = "Trip length: 2 mi", text = null)
        assertFalse(tripLen.active)
        assertEquals("", tripLen.distance)
    }
}
