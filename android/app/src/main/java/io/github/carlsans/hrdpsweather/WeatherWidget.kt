package io.github.carlsans.hrdpsweather

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit

/** Home-screen widget: current conditions at the chosen place and the next precipitation, from HRDPS. */
class WeatherWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        render(context)
        refreshNow(context)
    }

    override fun onEnabled(context: Context) = schedule(context)

    override fun onDisabled(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(PERIODIC)
    }

    companion object {
        private const val PERIODIC = "widget-periodic"
        private const val NOW = "widget-now"
        private val HM = DateTimeFormatter.ofPattern("HH'h'mm")

        private val network = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

        /** Every 30 min while a widget exists (Android's minimum for periodic work is 15 min). */
        fun schedule(c: Context) {
            val req = PeriodicWorkRequestBuilder<WidgetWorker>(30, TimeUnit.MINUTES).setConstraints(network).build()
            WorkManager.getInstance(c).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun refreshNow(c: Context) {
            if (ids(c).isEmpty()) return
            schedule(c)
            val req = OneTimeWorkRequestBuilder<WidgetWorker>().setConstraints(network).build()
            WorkManager.getInstance(c).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, req)
        }

        private fun ids(c: Context): IntArray =
            AppWidgetManager.getInstance(c).getAppWidgetIds(ComponentName(c, WeatherWidget::class.java))

        /** Draw every widget from the saved snapshot. */
        fun render(c: Context) {
            val s = Prefs.snapshot(c)
            val place = Prefs.place(c)
            val v = RemoteViews(c.packageName, R.layout.widget)
            if (s == null) {
                v.setTextViewText(R.id.icon, "⛅")
                v.setTextViewText(R.id.temp, "–")
                v.setTextViewText(R.id.place, place.location)
                v.setTextViewText(R.id.describe, "Chargement…")
                v.setTextViewText(R.id.next, "Données HRDPS d'Environnement Canada")
                v.setTextColor(R.id.next, 0xFFA6ADC8.toInt())             // the layout's preview colour is orange
                v.setTextViewText(R.id.meta, "")
            } else {
                val zone = runCatching { ZoneId.of(place.timezone) }.getOrDefault(ZoneId.systemDefault())
                val age = (System.currentTimeMillis() - s.updated) / 60000
                v.setTextViewText(R.id.icon, s.icon)
                v.setTextViewText(R.id.temp, s.temp)
                v.setTextViewText(R.id.place, s.place)
                v.setTextViewText(R.id.describe, s.describe)
                v.setTextViewText(R.id.next, s.next)
                v.setTextColor(R.id.next, if (s.wet) 0xFFFAB387.toInt() else 0xFFA6ADC8.toInt())
                val stale = if (age > 90) " · données de ${age / 60} h" else ""
                v.setTextViewText(R.id.meta, "${s.feels} · màj ${HM.format(Instant.ofEpochMilli(s.updated).atZone(zone))}$stale")
            }
            val open = PendingIntent.getActivity(c, 0, Intent(c, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            v.setOnClickPendingIntent(R.id.root, open)
            AppWidgetManager.getInstance(c).updateAppWidget(ComponentName(c, WeatherWidget::class.java), v)
        }
    }
}

/** Background refresh: newest run → the few point values around the current hour → snapshot → redraw. */
class WidgetWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val c = applicationContext
        val place = Prefs.place(c)
        val zone = runCatching { ZoneId.of(place.timezone) }.getOrDefault(ZoneId.systemDefault())
        val ref = GeoMet.latestRef() ?: return Result.retry()
        val now = Instant.now()
        val hourKey = GeoMet.iso(Instant.ofEpochSecond(now.epochSecond / 3600 * 3600))
        val old = Prefs.snapshot(c)
        if (old != null && old.ref == GeoMet.iso(ref) && old.hour == hourKey && old.place == place.location) {
            WeatherWidget.render(c)                                    // same run, same hour: nothing new to fetch
            return Result.success()
        }
        val f = GeoMet.fetch(ref, place.latitude, place.longitude, now) ?: return Result.retry()
        val i = f.nowIndex(now)
        val next = nextPrecipitation(f, i, zone, place)
        Prefs.setSnapshot(c, Snapshot(
            icon = f.icon(i), temp = "${f.tt[i]?.let(::round0) ?: "–"}°", describe = f.describe(i),
            feels = f.re[i]?.let { "Ressenti ${round0(it)}°" } ?: "", next = next.first, wet = next.second,
            place = place.location, ref = GeoMet.iso(ref), hour = hourKey, updated = System.currentTimeMillis(),
        ))
        WeatherWidget.render(c)
        return Result.success()
    }

    /** ("Neige vers 15h · 4,5 mm d'ici 12 h", true) or ("Pas de précipitations d'ici 03h", false). */
    private suspend fun nextPrecipitation(f: Forecast, i: Int, zone: ZoneId, place: Place): Pair<String, Boolean> {
        val end = minOf(i + GeoMet.AHEAD, f.n - 1)
        val total = (i + 1..end).sumOf { f.pr[it] }
        val amount = if (total >= 0.1) " · ${"%.1f".format(java.util.Locale.CANADA_FRENCH, total)} mm d'ici ${end - i} h" else ""
        if (f.kind(i) != "none") return "En cours$amount" to true                // the line above already says what
        val j = (i + 1..end).firstOrNull { f.pr[it] >= 0.2 }
            ?: return "Pas de précipitations d'ici ${f.local(end, zone).hour.toString().padStart(2, '0')}h" to false
        val what = GeoMet.ptypeAt(f.times[j], place.latitude, place.longitude)?.takeIf { it != "Aucune" } ?: "Précipitations"
        return "$what vers ${f.local(j, zone).hour.toString().padStart(2, '0')}h$amount" to true
    }

    /** Python's f"{x:.0f}" (round half to even), without the "-0". */
    private fun round0(x: Double): String = Math.rint(x).toInt().toString()
}
