package com.ex30companion

import android.app.Notification
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class NavNotificationParserTest {

    @Before
    fun setUp() {
        NavStateRepository.resetForTests()
        NavNotificationSelector.reset()
    }

    @Test
    fun `stale refresh result is discarded after removal or newer callback`() {
        val leftTurn = NavNotificationParser.parse(title = "300 m", text = "Turn left")
        val rightTurn = NavNotificationParser.parse(title = "150 m", text = "Turn right")

        NavStateRepository.update(leftTurn)
        val seqBeforeRefresh = NavStateRepository.currentSeq()

        // 1. A removal/clear arrives while refresh is in flight -> refresh must NOT bring back the removed cue.
        NavStateRepository.clear()
        val appliedAfterClear = NavStateRepository.applyRefreshIfUnchanged(seqBeforeRefresh, leftTurn)
        assertFalse(appliedAfterClear)
        assertFalse(NavStateRepository.state.value.active)

        // 2. A newer callback updates state while refresh is in flight -> refresh must NOT overwrite the newer cue.
        NavStateRepository.update(leftTurn)
        val seqBeforeSecondRefresh = NavStateRepository.currentSeq()
        NavStateRepository.update(rightTurn)
        val appliedAfterReplacement = NavStateRepository.applyRefreshIfUnchanged(seqBeforeSecondRefresh, leftTurn)
        assertFalse(appliedAfterReplacement)
        assertEquals("turn_right", NavStateRepository.state.value.maneuver)
        assertEquals("150 m", NavStateRepository.state.value.distance)

        // 3. When no callback intervened, refresh applies cleanly.
        val seqQuiet = NavStateRepository.currentSeq()
        assertTrue(NavStateRepository.applyRefreshIfUnchanged(seqQuiet, rightTurn))
    }

    @Test
    fun `source filter requires Google Maps package and active guidance`() {
        // Non-Maps package rejected before content inspection
        assertFalse(
            NavNotificationListenerService.isEligibleGoogleMapsGuidance(
                packageName = "com.waze",
                flags = Notification.FLAG_ONGOING_EVENT,
                category = Notification.CATEGORY_NAVIGATION,
                isOngoing = true,
            ),
        )
        // Group summary rejected
        assertFalse(
            NavNotificationListenerService.isEligibleGoogleMapsGuidance(
                packageName = "com.google.android.apps.maps",
                flags = Notification.FLAG_ONGOING_EVENT or Notification.FLAG_GROUP_SUMMARY,
                category = Notification.CATEGORY_NAVIGATION,
                isOngoing = true,
            ),
        )
        // Non-ongoing, non-navigation category rejected
        assertFalse(
            NavNotificationListenerService.isEligibleGoogleMapsGuidance(
                packageName = "com.google.android.apps.maps",
                flags = 0,
                category = Notification.CATEGORY_PROMO,
                isOngoing = false,
            ),
        )
        // Active Google Maps navigation accepted
        assertTrue(
            NavNotificationListenerService.isEligibleGoogleMapsGuidance(
                packageName = "com.google.android.apps.maps",
                flags = Notification.FLAG_ONGOING_EVENT,
                category = Notification.CATEGORY_NAVIGATION,
                isOngoing = true,
            ),
        )
    }

    @Test
    fun `newer key takes over from older key and older key cannot take control back by reposting`() {
        val oldCue = NavNotificationParser.parse(title = "500 m", text = "Turn left")
        val liveMapsCue = NavNotificationParser.parse(title = "300 m", text = "Turn right")

        // 1. Older notification key-old is posted first.
        assertTrue(NavNotificationSelector.shouldAcceptPost("key-old", oldCue, setOf("key-old")))
        assertEquals("key-old", NavNotificationSelector.currentTrackedKey())

        // 2. Newer notification key-maps starts -> takes over immediately.
        assertTrue(
            NavNotificationSelector.shouldAcceptPost(
                "key-maps",
                liveMapsCue,
                setOf("key-old", "key-maps"),
            ),
        )
        assertEquals("key-maps", NavNotificationSelector.currentTrackedKey())

        // 3. Older notification key-old simply reposts while key-maps is still active -> ignored!
        assertFalse(
            NavNotificationSelector.shouldAcceptPost(
                "key-old",
                oldCue,
                setOf("key-old", "key-maps"),
            ),
        )
        assertEquals("key-maps", NavNotificationSelector.currentTrackedKey())

        // 4. Periodic refresh also stays pinned to key-maps.
        val candidates = listOf("key-old" to oldCue, "key-maps" to liveMapsCue)
        val (keptKey, keptState) = NavNotificationSelector.selectActive(candidates)
        assertEquals("key-maps", keptKey)
        assertEquals("turn_right", keptState.maneuver)

        // 5. Once key-maps is removed, selector falls back to remaining active notification or clears.
        val (fallbackKey, fallbackState) = NavNotificationSelector.selectActive(listOf("key-old" to oldCue))
        assertEquals("key-old", fallbackKey)
        assertEquals("turn_left", fallbackState.maneuver)

        val (emptyKey, emptyState) = NavNotificationSelector.selectActive(emptyList())
        assertNull(emptyKey)
        assertFalse(emptyState.active)
    }

    @Test
    fun `route summary to destination is ignored on its own and does not override real maneuver`() {
        val summaries = listOf(
            "12 km to destination",
            "12 km to your destination",
            "5 mi to the destination",
            "Your destination is 12 km away",
            "Destination is 4.5 mi away",
            "Hedefe 12 km",
            "12 km left",
            "Trip length: 2 mi",
        )
        for (summary in summaries) {
            val summaryOnly = NavNotificationParser.parse(title = summary, text = null)
            assertFalse("Expected inactive for: $summary", summaryOnly.active)
            assertEquals("none", summaryOnly.maneuver)
            assertEquals("none", summaryOnly.arrival)
            assertEquals("", summaryOnly.distance)
        }

        val pairedSeparateFields = NavNotificationParser.parse(
            title = "Turn left in 300 m",
            text = "12 km to destination",
        )
        assertTrue(pairedSeparateFields.active)
        assertEquals("turn_left", pairedSeparateFields.maneuver)
        assertEquals("300 m", pairedSeparateFields.distance)

        // Turn left in primary field with destination metadata in a separate field stays turn_left
        val turnWithDestMeta = NavNotificationParser.parse(
            title = "Turn left in 300 m",
            text = "Your destination is 12 km away",
        )
        assertTrue(turnWithDestMeta.active)
        assertEquals("turn_left", turnWithDestMeta.maneuver)
        assertEquals("300 m", turnWithDestMeta.distance)

        val turnWithDestSideInOtherField = NavNotificationParser.parse(
            title = "Turn left in 300 m",
            text = "Your destination is on the right",
        )
        assertTrue(turnWithDestSideInOtherField.active)
        assertEquals("turn_left", turnWithDestSideInOtherField.maneuver)
        assertEquals("300 m", turnWithDestSideInOtherField.distance)
        assertEquals("destination_right", turnWithDestSideInOtherField.arrival)

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
    fun `destination on right or left is recognized as arrival-side cue without turn arrow`() {
        val destRight = NavNotificationParser.parse(
            title = "Your destination is on the right",
            text = null,
        )
        assertTrue(destRight.active)
        assertEquals("destination", destRight.maneuver)
        assertEquals("destination_right", destRight.arrival)

        val destLeft = NavNotificationParser.parse(
            title = "Your destination is on the left",
            text = null,
        )
        assertTrue(destLeft.active)
        assertEquals("destination", destLeft.maneuver)
        assertEquals("destination_left", destLeft.arrival)

        val trDestRight = NavNotificationParser.parse(
            title = "Hedefiniz sağda",
            text = null,
        )
        assertTrue(trDestRight.active)
        assertEquals("destination", trDestRight.maneuver)
        assertEquals("destination_right", trDestRight.arrival)
    }

    @Test
    fun `word traps like Brighton Road and 12 km left do not produce turns and highway exits are not roundabouts`() {
        // "Brighton Road" contains substring "right" — must NOT match turn_right
        val brighton = NavNotificationParser.parse(title = "350 m", text = "Brighton Road")
        assertTrue(brighton.active)
        assertEquals("none", brighton.maneuver)
        assertEquals("350 m", brighton.distance)

        // "Brighton Road" alone without distance has no nav signal
        val brightonOnly = NavNotificationParser.parse(title = "Brighton Road", text = null)
        assertFalse(brightonOnly.active)
        assertEquals("none", brightonOnly.maneuver)

        // Numbered highway exit without roundabout keyword must NOT be classified as roundabout
        val highwayExit = NavNotificationParser.parse(title = "500 m", text = "Take exit 4")
        assertTrue(highwayExit.active)
        assertEquals("none", highwayExit.maneuver)
        assertEquals(0, highwayExit.exitNumber)
        assertEquals("500 m", highwayExit.distance)

        val highwayExitRight = NavNotificationParser.parse(title = "500 m", text = "Exit 4 on the right")
        assertTrue(highwayExitRight.active)
        assertEquals("exit_right", highwayExitRight.maneuver)
        assertEquals(0, highwayExitRight.exitNumber)

        // Actual roundabout with exit number is classified as roundabout with exitNumber
        val roundabout = NavNotificationParser.parse(
            title = "200 m",
            text = "At the roundabout, take the 2nd exit",
        )
        assertTrue(roundabout.active)
        assertEquals("roundabout", roundabout.maneuver)
        assertEquals(2, roundabout.exitNumber)
        assertEquals("200 m", roundabout.distance)

        // Decimal comma in Turkish wording is preserved
        val trTurn = NavNotificationParser.parse(title = "1,2 km", text = "Sağa dönün")
        assertTrue(trTurn.active)
        assertEquals("turn_right", trTurn.maneuver)
        assertEquals("1,2 km", trTurn.distance)

        // Bare ambiguous exit is omitted
        val bareExit = NavNotificationParser.parse(title = "Take the exit", text = null)
        assertFalse(bareExit.active)
        assertEquals("none", bareExit.maneuver)
    }
}
