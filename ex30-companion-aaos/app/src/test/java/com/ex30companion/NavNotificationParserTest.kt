package com.ex30companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NavNotificationParserTest {

    @Test
    fun `selector stays pinned to activeNavKey on refresh and switches after live post or removal`() {
        val rightTurn = NavNotificationParser.parse(title = "300 m", text = "Turn right")
        val leftTurn = NavNotificationParser.parse(title = "150 m", text = "Turn left")
        // Even if an older notification ("key-left") sits first in activeNotifications,
        // once a live post selects "key-right", periodic refresh stays pinned to "key-right".
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
        for (summary in listOf("12 km to destination", "12 km to your destination", "5 mi to the destination")) {
            val summaryOnly = NavNotificationParser.parse(title = summary, text = null)
            assertFalse("Expected inactive for: $summary", summaryOnly.active)
            assertEquals("none", summaryOnly.maneuver)
            assertEquals("", summaryOnly.distance)
        }

        val pairedSeparateFields = NavNotificationParser.parse(
            title = "Turn left in 300 m",
            text = "12 km to destination",
        )
        assertTrue(pairedSeparateFields.active)
        assertEquals("turn_left", pairedSeparateFields.maneuver)
        assertEquals("300 m", pairedSeparateFields.distance)

        // Single field separated by bullet, newline, or comma
        for (sep in listOf(" · ", "\n", "\r\n", ", ")) {
            val pairedSingleField = NavNotificationParser.parse(
                title = "Turn left in 300 m${sep}12 km to destination",
                text = null,
            )
            assertTrue("Expected active for separator '$sep'", pairedSingleField.active)
            assertEquals("turn_left", pairedSingleField.maneuver)
            assertEquals("300 m", pairedSingleField.distance)
        }
    }

    @Test
    fun `destination on right or left is recognized as arrival cue without turn arrow`() {
        val destRight = NavNotificationParser.parse(
            title = "Your destination is on the right",
            text = null,
        )
        assertTrue(destRight.active)
        assertEquals("destination", destRight.maneuver)

        val destLeft = NavNotificationParser.parse(
            title = "Your destination is on the left",
            text = null,
        )
        assertTrue(destLeft.active)
        assertEquals("destination", destLeft.maneuver)
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
