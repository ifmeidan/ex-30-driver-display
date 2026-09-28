import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
}

// keystore.properties is not committed (see .gitignore) and is only needed
// for the maintainer's Play uploads. Play App Signing flow: the AAB is
// uploaded signed with this key, Google holds the actual signing key.
// Without the file, release builds are simply unsigned (debug is unaffected).
val keystorePropsFile = rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystorePropsFile.exists()) FileInputStream(keystorePropsFile).use { load(it) }
}

android {
    namespace = "com.ex30companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.ex30companion"
        minSdk = 32
        // Bumped to 34 for Play Store acceptance (lintVitalRelease enforces
        // ExpiredTargetSdkVersion). Triggers the API-35 foreground-service-type
        // requirement, satisfied via FOREGROUND_SERVICE_CONNECTED_DEVICE +
        // android:foregroundServiceType="connectedDevice" on BridgeService.
        targetSdk = 35
        versionCode = 20
        versionName = "1.0.6"
    }

    signingConfigs {
        create("release") {
            if (keystorePropsFile.exists()) {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // Only attach the signing config when the keystore is actually
            // present, so debug builds and CI without the keystore still work.
            if (keystorePropsFile.exists()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    useLibrary("android.car")
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.activity:activity-ktx:1.9.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // Car App Library — used only to declare the androidx.car.app.category.IOT
    // car-app category required by Play for the Automotive OS listing
    // (Ex30CarAppService). 1.7.0 is the current stable and includes the
    // CVE-2024-10382 fix so Play's SDK scanner stays quiet.
    implementation("androidx.car.app:app:1.7.0")
    implementation("androidx.car.app:app-automotive:1.7.0")
}

// Phase 2 read-only invariant: fail the build if any vehicle-property write
// API appears in app sources. Enforced before every build, not opt-in.
tasks.register("verifyReadOnly") {
    group = "verification"
    description = "Fails build if a vehicle-property write API is referenced in app sources."
    val srcDir = file("src/main/java")
    inputs.dir(srcDir)
    doLast {
        val banned = listOf(
            "setProperty(",
            "CarPropertyManager.set",
            ".setBooleanProperty(",
            ".setIntProperty(",
            ".setFloatProperty("
        )
        val offenders = mutableListOf<String>()
        srcDir.walkTopDown()
            .filter { it.isFile && (it.extension == "kt" || it.extension == "java") }
            .forEach { f ->
                f.readLines().forEachIndexed { i, line ->
                    if (banned.any { it in line }) {
                        offenders += "${f.relativeTo(projectDir)}:${i + 1}: ${line.trim()}"
                    }
                }
            }
        if (offenders.isNotEmpty()) {
            throw GradleException(
                "Read-only invariant violated — this app must never write vehicle properties:\n" +
                    offenders.joinToString("\n")
            )
        }
    }
}

tasks.named("preBuild").configure { dependsOn("verifyReadOnly") }
