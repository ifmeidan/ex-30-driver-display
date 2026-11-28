package com.ex30companion

import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeWifiProfileTest {
    @Test fun namedProfileRequiresExactVisibleSsid() {
        assertTrue(matchesSavedWifi("Display Pi ", false, "\"Display Pi \"", WifiInfo.SECURITY_TYPE_OPEN))
        for (reported in listOf(null, WifiManager.UNKNOWN_SSID, "\"Display Pi\"", "\"display Pi \"")) {
            assertFalse(matchesSavedWifi("Display Pi ", false, reported, WifiInfo.SECURITY_TYPE_OPEN))
        }
    }

    @Test fun sameNameKeepsTheSavedSecurityRequirement() {
        for (security in listOf(WifiInfo.SECURITY_TYPE_PSK, WifiInfo.SECURITY_TYPE_SAE)) {
            assertTrue(matchesSavedWifi("Pi", true, "\"Pi\"", security))
            assertFalse(matchesSavedWifi("Pi", false, "\"Pi\"", security))
        }
        for (security in listOf(WifiInfo.SECURITY_TYPE_OPEN, WifiInfo.SECURITY_TYPE_OWE)) {
            assertTrue(matchesSavedWifi("Pi", false, "\"Pi\"", security))
            assertFalse(matchesSavedWifi("Pi", true, "\"Pi\"", security))
        }
        assertFalse(matchesSavedWifi("Pi", true, "\"Pi\"", WifiInfo.SECURITY_TYPE_UNKNOWN))
        assertFalse(matchesSavedWifi("Pi", false, "\"Pi\"", WifiInfo.SECURITY_TYPE_UNKNOWN))
    }
}
