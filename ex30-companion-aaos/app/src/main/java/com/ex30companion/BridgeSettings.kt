package com.ex30companion

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.GeneralSecurityException
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object BridgeSettings {

    private const val PREFS = "bridge"
    private const val KEY_HOST = "pi_host"
    private const val KEY_SSID = "pi_ssid"
    private const val KEY_PASSPHRASE = "pi_wpa2_encrypted"
    private const val KEY_NAV_ENABLED = "nav_enabled"
    private const val KEY_ALIAS = "ex30_bridge_wifi"
    private const val KEY_STORE = "AndroidKeyStore"
    private const val CIPHER = "AES/GCM/NoPadding"

    data class Wifi(val ssid: String, val passphrase: String?) {
        override fun toString() = "Wifi(<redacted>)"
    }

    fun host(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_HOST, null)
            ?.takeIf { it.isNotBlank() }

    fun setHost(context: Context, host: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_HOST, host.trim())
            .apply()
    }

    fun ssid(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_SSID, null)

    fun hasPassphrase(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).contains(KEY_PASSPHRASE)

    fun wifi(context: Context): Wifi? {
        val saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).all
        val ssid = saved[KEY_SSID] as String? ?: return null
        val encrypted = saved[KEY_PASSPHRASE] as String? ?: return Wifi(ssid, null)
        try {
            val parts = encrypted.split(".")
            require(parts.size == 2)
            val iv = Base64.decode(parts[0], Base64.NO_WRAP)
            val cipher = Cipher.getInstance(CIPHER)
            val key = loadKey() ?: throw GeneralSecurityException("Missing Wi-Fi credential key")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, iv))
            cipher.updateAAD(ssid.toByteArray(Charsets.UTF_8))
            return Wifi(ssid, String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), Charsets.UTF_8))
        } catch (error: Exception) {
            // Never reinterpret an unreadable protected profile as an open network.
            throw GeneralSecurityException("Cannot read the saved Wi-Fi credential", error)
        }
    }

    fun setConnection(context: Context, host: String, ssid: String, passphrase: String?) {
        val encrypted = passphrase?.let {
            val cipher = Cipher.getInstance(CIPHER)
            cipher.init(Cipher.ENCRYPT_MODE, loadKey() ?: generateKey())
            cipher.updateAAD(ssid.toByteArray(Charsets.UTF_8))
            val data = cipher.doFinal(it.toByteArray(Charsets.UTF_8))
            Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + "." + Base64.encodeToString(data, Base64.NO_WRAP)
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_HOST, host.trim())
            .putString(KEY_SSID, ssid.ifEmpty { null })
            .putString(KEY_PASSPHRASE, encrypted)
            .apply()
    }

    private fun loadKey(): SecretKey? =
        KeyStore.getInstance(KEY_STORE).apply { load(null) }.getKey(KEY_ALIAS, null) as SecretKey?

    private fun generateKey(): SecretKey {
        val spec = KeyGenParameterSpec.Builder(
            KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build()
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEY_STORE).run {
            init(spec)
            generateKey()
        }
    }

    /**
     * Experimental opt-in toggle for turn-by-turn maneuver + distance
     * forwarding. Off by default.
     */
    fun navEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getBoolean(KEY_NAV_ENABLED, false)

    fun setNavEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_NAV_ENABLED, enabled)
            .apply()
    }
}
