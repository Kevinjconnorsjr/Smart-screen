import java.util.Base64
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// ---- App signing ---------------------------------------------------------------------------
// Android only installs an update over an existing app if both are signed with the same key.
// The permanent key is NEVER committed. Provide it as either:
//   GitHub Actions secrets:  SMART_SCREEN_KEYSTORE_B64 (base64 of the .jks) + SMART_SCREEN_KEYSTORE_PASSWORD
//   local.properties:        signing.storeFile=/path/to/smart-screen.jks   signing.storePassword=...
// Without it, builds are signed with a throwaway debug key: fine to install, but the next build
// can't update it (uninstall first). See README "Phone app".
val localProps = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
val keystorePassword: String? = System.getenv("SMART_SCREEN_KEYSTORE_PASSWORD")?.ifBlank { null }
    ?: localProps.getProperty("signing.storePassword")?.ifBlank { null }
val keystoreFile: File? = System.getenv("SMART_SCREEN_KEYSTORE_B64")?.takeIf { it.isNotBlank() }?.let { b64 ->
    layout.buildDirectory.file("signing/smart-screen.jks").get().asFile.apply {
        parentFile.mkdirs()
        writeBytes(Base64.getMimeDecoder().decode(b64.trim()))
    }
} ?: localProps.getProperty("signing.storeFile")?.ifBlank { null }?.let { file(it) }
val hasPermanentKey = keystoreFile?.isFile == true && keystorePassword != null
logger.lifecycle(if (hasPermanentKey) "Smart Screen: signing with the permanent key" else "Smart Screen: no permanent key; using a debug key")

android {
    namespace = "com.smartscreen.remote"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.smartscreen.remote"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    signingConfigs {
        if (hasPermanentKey) {
            create("permanent") {
                storeFile = keystoreFile
                storePassword = keystorePassword
                keyAlias = "smartscreen"
                keyPassword = keystorePassword
            }
        }
    }

    buildTypes {
        debug {
            if (hasPermanentKey) signingConfig = signingConfigs.getByName("permanent")
        }
        release {
            signingConfig = if (hasPermanentKey) signingConfigs.getByName("permanent") else signingConfigs.getByName("debug")
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}
