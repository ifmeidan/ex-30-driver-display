# R8 keep rules for the EX30 Companion app.
# Most of the surface is plain Kotlin + AndroidX, which the default
# `proguard-android-optimize.txt` already handles. Project-specific:

# AAOS framework lookups happen via reflection from system services.
-keep class android.car.** { *; }
-dontwarn android.car.**

# View binding generates classes from layouts at compile time and the binding
# is referenced reflectively by AppCompat in some configurations.
-keep class com.ex30companion.databinding.** { *; }

# Foreground service is referenced from the manifest by class name.
-keep class com.ex30companion.BridgeService { *; }
