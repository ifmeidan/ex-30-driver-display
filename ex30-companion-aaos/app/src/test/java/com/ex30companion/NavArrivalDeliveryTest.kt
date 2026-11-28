package com.ex30companion

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class NavArrivalDeliveryTest {
    private val publisher = NavFramePublisher()
    private val arrival = NavState(active = true, maneuver = "destination", arrival = "destination_right")

    @Before
    fun resetRepository() {
        NavStateRepository.resetForTests()
        NavNotificationSelector.reset()
    }

    private fun removeNotification(): VersionedNavState =
        requireNotNull(NavStateRepository.applyRefreshIfUnchanged(NavStateRepository.currentSeq(), NavState()))

    @Test
    fun removalSeenBeforeCollectorRunsStillDeliversArrivalThenClear() {
        NavStateRepository.update(arrival)
        publisher.stageChange(removeNotification(), optedIn = true)
        assertTrue(publisher.pollLine()!!.contains(""""arrival":"destination_right""""))
        assertTrue(publisher.pollLine()!!.contains(""""active":false"""))
        assertNull(publisher.pollLine())
    }

    @Test
    fun arrivalAlreadyWrittenIsNotReplayedOnRemoval() {
        publisher.stageChange(NavStateRepository.update(arrival), optedIn = true)
        assertTrue(publisher.pollLine()!!.contains("destination_right"))
        publisher.stageChange(removeNotification(), optedIn = true)
        assertTrue(publisher.pollLine()!!.contains(""""active":false"""))
        assertNull(publisher.pollLine())
    }

    @Test
    fun optOutAndReconnectDiscardPendingArrival() {
        NavStateRepository.update(arrival)
        val removed = removeNotification()
        publisher.stageChange(removed, optedIn = true)
        publisher.stageReconnectSeed(removed, optedIn = true)
        // A collector catching up after the reconnect must respect the seed.
        publisher.stageChange(removed, optedIn = true)
        assertFalse(publisher.pollLine()!!.contains("destination_right"))
        assertNull(publisher.pollLine())

        NavStateRepository.update(arrival)
        publisher.stageChange(removeNotification(), optedIn = true)
        publisher.stageOptOutClear(NavStateRepository.clear())
        assertTrue(publisher.pollLine()!!.contains(""""active":false"""))
        assertNull(publisher.pollLine())
    }

    @Test
    fun removedOwnerArrivalPrecedesActiveFallbackEvenIfRenewalWinsTheRace() {
        val oldTurn = NavState(active = true, maneuver = "turn_left")
        NavNotificationSelector.shouldAcceptPost("A")
        NavNotificationSelector.shouldAcceptPost("B")
        assertTrue(NavNotificationSelector.consumeFinal("B"))
        NavStateRepository.update(arrival)
        val removedOwner = NavNotificationSelector.remove("B")
        val fallback = NavNotificationSelector.selectActive(listOf("A" to oldTurn)).second
        val version = requireNotNull(NavStateRepository.applyRefreshIfUnchanged(
            NavStateRepository.currentSeq(), fallback, trackedKeyRemoved = removedOwner,
        ))
        assertTrue(publisher.stageRenewal(version, optedIn = true))
        publisher.stageChange(version, optedIn = true)
        assertTrue(publisher.stageRenewal(version, optedIn = true))
        assertTrue(publisher.pollLine()!!.contains("destination_right"))
        assertTrue(publisher.pollLine()!!.contains("turn_left"))
        assertNull(publisher.pollLine())
        publisher.stageRenewal(version, optedIn = true)
        assertTrue(publisher.pollLine()!!.contains("turn_left"))
        assertNull(publisher.pollLine())
    }

    @Test
    fun emptySnapshotBeforeRemovalDoesNotDuplicateArrivalWhenFallbackIsRenewed() {
        NavStateRepository.update(arrival)
        publisher.stageChange(removeNotification(), optedIn = true)
        assertTrue(publisher.pollLine()!!.contains("destination_right"))
        val fallback = requireNotNull(NavStateRepository.applyRefreshIfUnchanged(
            NavStateRepository.currentSeq(), NavState(active = true, maneuver = "turn_right"), trackedKeyRemoved = true,
        ))
        publisher.stageChange(fallback, optedIn = true)
        assertTrue(publisher.pollLine()!!.contains("turn_right"))
        assertNull(publisher.pollLine())
    }

    @Test
    fun ordinaryActiveStepChangeDoesNotReplayPreviousArrival() {
        NavStateRepository.update(arrival)
        val step = requireNotNull(NavStateRepository.applyRefreshIfUnchanged(
            NavStateRepository.currentSeq(), NavState(active = true, maneuver = "turn_right"),
        ))
        publisher.stageChange(step, optedIn = true)
        assertTrue(publisher.pollLine()!!.contains("turn_right"))
        assertNull(publisher.pollLine())
    }

    @Test
    fun removalWithoutArrivalOnlyClears() {
        NavStateRepository.update(NavState(active = true, maneuver = "turn_right"))
        publisher.stageChange(removeNotification(), optedIn = true)
        assertTrue(publisher.pollLine()!!.contains(""""active":false"""))
        assertNull(publisher.pollLine())
    }
}
