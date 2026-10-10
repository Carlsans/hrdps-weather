// Android app: the web app (web/) in a WebView, plus a native home-screen widget.
// Build: ./gradlew assembleRelease (signed when HRDPS_KEYSTORE* are set, see .github/workflows/android.yml)
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// One version for every platform: read it from the Python package.
val appVersion: String = Regex("__version__ = \"([^\"]+)\"")
    .find(rootProject.file("../src/hrdps_weather/__init__.py").readText())!!.groupValues[1]
val versionParts = appVersion.split(".").map { it.toInt() }

android {
    namespace = "io.github.carlsans.hrdpsweather"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.carlsans.hrdpsweather"
        minSdk = 26
        targetSdk = 35
        versionCode = versionParts[0] * 10000 + versionParts[1] * 100 + versionParts[2]
        versionName = appVersion
        buildConfigField("String", "APP_URL", "\"https://carlsans.github.io/hrdps-weather/\"")
    }

    signingConfigs {
        create("release") {
            System.getenv("HRDPS_KEYSTORE")?.let { storeFile = file(it) }
            storePassword = System.getenv("HRDPS_KEYSTORE_PASSWORD")
            keyAlias = System.getenv("HRDPS_KEY_ALIAS") ?: "hrdps-weather"
            keyPassword = System.getenv("HRDPS_KEY_PASSWORD") ?: System.getenv("HRDPS_KEYSTORE_PASSWORD")
        }
    }

    buildTypes {
        debug {
            // Development: the web app served from this computer (python -m http.server 8765 --directory web,
            // then adb reverse tcp:8765 tcp:8765). Cleartext to localhost is allowed in debug builds only.
            buildConfigField("String", "APP_URL", "\"http://localhost:8765/\"")
        }
        release {
            isMinifyEnabled = false
            if (System.getenv("HRDPS_KEYSTORE") != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures { buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    testOptions {
        unitTests.all {
            // ForecastTest compares the Kotlin port with the Python output (web/tests/make_expected.py)
            it.systemProperty("expected", System.getenv("EXPECTED") ?: "")
            it.systemProperty("sample", rootProject.file("../src/hrdps_weather/data/sample_series.json").absolutePath)
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
