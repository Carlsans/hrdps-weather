package io.github.carlsans.hrdpsweather

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/** "Roaming" = connected through a mobile network that is not the user's own (abroad, or a partner network). */
object Roaming {
    /** True when the active network is cellular and Android reports that it is roaming. */
    fun isRoaming(c: Context): Boolean {
        if (BuildConfig.DEBUG && java.io.File(c.filesDir, "debug_roaming").exists()) return true   // tests: the emulator cannot roam
        return try {
            val cm = c.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val caps = cm.getNetworkCapabilities(cm.activeNetwork)
            caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
                && !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_ROAMING)
        } catch (_: Exception) {
            false
        }
    }

    /** True when the user asked for nothing to be downloaded while roaming (the default) and the phone is. */
    fun blocked(c: Context): Boolean = Prefs.blockRoaming(c) && isRoaming(c)
}
