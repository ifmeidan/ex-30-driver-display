package com.ex30companion

import android.app.Notification
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
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
        assertNull(appliedAfterClear)
        assertFalse(NavStateRepository.state.value.active)

        // 2. A newer callback updates state while refresh is in flight -> refresh must NOT overwrite the newer cue.
        NavStateRepository.update(leftTurn)
        val seqBeforeSecondRefresh = NavStateRepository.currentSeq()
        NavStateRepository.update(rightTurn)
        val appliedAfterReplacement = NavStateRepository.applyRefreshIfUnchanged(seqBeforeSecondRefresh, leftTurn)
        assertNull(appliedAfterReplacement)
        assertEquals("turn_right", NavStateRepository.state.value.maneuver)
        assertEquals("150 m", NavStateRepository.state.value.distance)

        // 3. When no callback intervened, refresh applies cleanly.
        val seqQuiet = NavStateRepository.currentSeq()
        assertNotNull(NavStateRepository.applyRefreshIfUnchanged(seqQuiet, rightTurn))
    }

    @Test
    fun `publisher serializes paused renewal against concurrent clear and newer turn at wire level`() {
        val publisher = NavFramePublisher(nowMs = { 1000L })
        val oldLeft = NavNotificationParser.parse(title = "300 m", text = "Turn left")
        val newRight = NavNotificationParser.parse(title = "150 m", text = "Turn right")

        // Case 1: Renewal reads oldLeft, then clear happens before renewal stages -> wire sees ONLY clear.
        val vLeft1 = NavStateRepository.update(oldLeft)
        assertTrue(publisher.stageChange(vLeft1, optedIn = true))
        val firstWire = publisher.pollLine()!!
        assertTrue(firstWire.contains(""""maneuver":"turn_left""""))

        val pausedRenewalSnapshot1 = NavStateRepository.currentVersioned()
        val vCleared = NavStateRepository.clear()
        assertTrue(publisher.stageChange(vCleared, optedIn = true))
        val clearWire = publisher.pollLine()!!
        assertTrue(clearWire.contains(""""active":false"""))

        // Paused renewal resumes after clear: must be rejected at staging.
        assertFalse(
            publisher.stageRenewal(
                versioned = pausedRenewalSnapshot1,
                latestRepoSeq = NavStateRepository.currentSeq(),
                optedIn = true,
            ),
        )
        assertNull(publisher.pollLine())

        // Case 2: Renewal reads oldLeft, then newRight arrives before renewal stages -> wire sees ONLY newRight.
        val vLeft2 = NavStateRepository.update(oldLeft)
        assertTrue(publisher.stageChange(vLeft2, optedIn = true))
        assertNotNull(publisher.pollLine())

        val pausedRenewalSnapshot2 = NavStateRepository.currentVersioned()
        val vRight = NavStateRepository.update(newRight)
        assertTrue(publisher.stageChange(vRight, optedIn = true))
        val rightWire = publisher.pollLine()!!
        assertTrue(rightWire.contains(""""maneuver":"turn_right""""))
        assertTrue(rightWire.contains(""""distance":"150 m""""))

        // Paused renewal of oldLeft resumes: must be rejected at staging.
        assertFalse(
            publisher.stageRenewal(
                versioned = pausedRenewalSnapshot2,
                latestRepoSeq = NavStateRepository.currentSeq(),
                optedIn = true,
            ),
        )
        assertNull(publisher.pollLine())
    }

    @Test
    fun `source filter requires Google Maps package, ongoing flag, and navigation category`() {
        // Non-Maps package rejected
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
        // Non-ongoing navigation-category fixture rejected
        assertFalse(
            NavNotificationListenerService.isEligibleGoogleMapsGuidance(
                packageName = "com.google.android.apps.maps",
                flags = 0,
                category = Notification.CATEGORY_NAVIGATION,
                isOngoing = false,
            ),
        )
        // Ongoing generic Maps progress / service notification rejected
        assertFalse(
            NavNotificationListenerService.isEligibleGoogleMapsGuidance(
                packageName = "com.google.android.apps.maps",
                flags = Notification.FLAG_ONGOING_EVENT,
                category = Notification.CATEGORY_PROGRESS,
                isOngoing = true,
            ),
        )
        assertFalse(
            NavNotificationListenerService.isEligibleGoogleMapsGuidance(
                packageName = "com.google.android.apps.maps",
                flags = Notification.FLAG_ONGOING_EVENT,
                category = Notification.CATEGORY_SERVICE,
                isOngoing = true,
            ),
        )
        assertFalse(
            NavNotificationListenerService.isEligibleGoogleMapsGuidance(
                packageName = "com.google.android.apps.maps",
                flags = Notification.FLAG_ONGOING_EVENT,
                category = null,
                isOngoing = true,
            ),
        )
        // Active ongoing Google Maps navigation accepted
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

        assertTrue(NavNotificationSelector.shouldAcceptPost("key-old", oldCue, setOf("key-old")))
        assertEquals("key-old", NavNotificationSelector.currentTrackedKey())

        assertTrue(
            NavNotificationSelector.shouldAcceptPost(
                "key-maps",
                liveMapsCue,
                setOf("key-old", "key-maps"),
            ),
        )
        assertEquals("key-maps", NavNotificationSelector.currentTrackedKey())

        assertFalse(
            NavNotificationSelector.shouldAcceptPost(
                "key-old",
                oldCue,
                setOf("key-old", "key-maps"),
            ),
        )
        assertEquals("key-maps", NavNotificationSelector.currentTrackedKey())

        val candidates = listOf("key-old" to oldCue, "key-maps" to liveMapsCue)
        val (keptKey, keptState) = NavNotificationSelector.selectActive(candidates)
        assertEquals("key-maps", keptKey)
        assertEquals("turn_right", keptState.maneuver)

        val (fallbackKey, fallbackState) = NavNotificationSelector.selectActive(listOf("key-old" to oldCue))
        assertEquals("key-old", fallbackKey)
        assertEquals("turn_left", fallbackState.maneuver)

        val (emptyKey, emptyState) = NavNotificationSelector.selectActive(emptyList())
        assertNull(emptyKey)
        assertFalse(emptyState.active)
    }

    @Test
    fun `route summary is ignored and em-dash, parenthesized total, or same-length bigText does not erase turn`() {
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

        // Single field separated by bullet, newline, comma, en-dash, em-dash, or parentheses
        val singleFieldInputs = listOf(
            "Turn left in 300 m · 12 km to destination",
            "Turn left in 300 m\n12 km to destination",
            "Turn left in 300 m\r\n12 km to destination",
            "Turn left in 300 m, 12 km to destination",
            "Turn left in 300 m — 12 km to destination",
            "Turn left in 300 m—12 km to destination",
            "Turn left in 300 m – 12 km to destination",
            "Turn left in 300 m (12 km to destination)",
            "Turn left in 300 m (Trip length: 2 mi)",
        )
        for (input in singleFieldInputs) {
            val parsed = NavNotificationParser.parse(title = input, text = null)
            assertTrue("Expected active for '$input'", parsed.active)
            assertEquals("turn_left", parsed.maneuver)
            assertEquals("300 m", parsed.distance)
        }

        // Same-length expanded bigText metadata field must NOT hide an explicit turn in text
        val sameLengthBigText = NavNotificationParser.parse(
            title = "300 m",
            text = "Turn left",
            bigText = "Central Station Area",
        )
        assertTrue(sameLengthBigText.active)
        assertEquals("turn_left", sameLengthBigText.maneuver)
        assertEquals("300 m", sameLengthBigText.distance)
    }

    @Test
    fun `destination on right or left preserves side and step distance together`() {
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

        // Arrival side WITH step distance ("in 300 m") preserves both side and distance
        val destLeftWithDist = NavNotificationParser.parse(
            title = "Your destination is on the left in 300 m",
            text = null,
        )
        assertTrue(destLeftWithDist.active)
        assertEquals("destination", destLeftWithDist.maneuver)
        assertEquals("destination_left", destLeftWithDist.arrival)
        assertEquals("300 m", destLeftWithDist.distance)

        val destRightWithDist = NavNotificationParser.parse(
            title = "Your destination is on the right in 300 m",
            text = null,
        )
        assertTrue(destRightWithDist.active)
        assertEquals("destination", destRightWithDist.maneuver)
        assertEquals("destination_right", destRightWithDist.arrival)
        assertEquals("300 m", destRightWithDist.distance)

        val trDestRight = NavNotificationParser.parse(
            title = "Hedefiniz sağda",
            text = null,
        )
        assertTrue(trDestRight.active)
        assertEquals("destination", trDestRight.maneuver)
        assertEquals("destination_right", trDestRight.arrival)
    }

    @Test
    fun `word traps, generic Turkish junctions, and highway exits are handled accurately`() {
        // "Brighton Road" contains substring "right" — must NOT match turn_right
        val brighton = NavNotificationParser.parse(title = "350 m", text = "Brighton Road")
        assertTrue(brighton.active)
        assertEquals("none", brighton.maneuver)
        assertEquals("350 m", brighton.distance)

        // Generic Turkish junction ("kavşak") without "döner" is NOT a roundabout
        val genericTrJunctionOnly = NavNotificationParser.parse(title = "300 m", text = "Kavşak")
        assertTrue(genericTrJunctionOnly.active)
        assertEquals("none", genericTrJunctionOnly.maneuver)
        assertEquals("300 m", genericTrJunctionOnly.distance)

        val genericTrJunctionTurn = NavNotificationParser.parse(title = "300 m", text = "Kavşaktan sola dönün")
        assertTrue(genericTrJunctionTurn.active)
        assertEquals("turn_left", genericTrJunctionTurn.maneuver)
        assertEquals("300 m", genericTrJunctionTurn.distance)

        // Explicit Turkish roundabout ("Döner kavşak") IS a roundabout
        val explicitTrRoundabout = NavNotificationParser.parse(title = "200 m", text = "Döner kavşakta 2. çıkış")
        assertTrue(explicitTrRoundabout.active)
        assertEquals("roundabout", explicitTrRoundabout.maneuver)
        assertEquals(2, explicitTrRoundabout.exitNumber)
        assertEquals("200 m", explicitTrRoundabout.distance)

        // Numbered highway exit without roundabout keyword must NOT be classified as roundabout
        val highwayExit = NavNotificationParser.parse(title = "500 m", text = "Take exit 4")
        assertTrue(highwayExit.active)
        assertEquals("none", highwayExit.maneuver)
        assertEquals(0, highwayExit.exitNumber)
        assertEquals("500 m", highwayExit.distance)

        // Actual English roundabout with exit number
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
    }
}
