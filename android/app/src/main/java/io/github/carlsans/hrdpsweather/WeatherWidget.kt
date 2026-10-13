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
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.concurrent.TimeUnit

/** Home-screen widget (3×2): current conditions at the chosen place and the next precipitation, from HRDPS. */
class WeatherWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) = Widgets.onUpdate(context)
    override fun onEnabled(context: Context) = Widgets.schedule(context)
    override fun onDisabled(context: Context) = Widgets.onDisabled(context)
}

/** Large widget (4×3): the same, plus wind and humidity, 12-hour alerts and an hourly strip up to +12 h. */
class WeatherWidgetLarge : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) = Widgets.onUpdate(context)
    override fun onEnabled(context: Context) = Widgets.schedule(context)
    override fun onDisabled(context: Context) = Widgets.onDisabled(context)
}

/** What both widgets share: one background refresh, one snapshot, drawing. */
object Widgets {
    private const val PERIODIC = "widget-periodic"
    private const val NOW = "widget-now"
    private const val HOUR = "widget-hour"
    private const val RUN = "widget-run"
    const val BOUNDARY = "widget-boundary"                      // tag of the two one-off jobs below
    private const val RUN_STEP_S = 6 * 3600L                    // HRDPS runs every 6 hours…
    private const val RUN_LAG_S = 3 * 3600L + 11 * 60L          // …and is complete on GeoMet about 3 h 10 min later
    private val HM = DateTimeFormatter.ofPattern("HH'h'mm")
    private const val WET = 0xFFFAB387.toInt()
    private const val DRY = 0xFFA6ADC8.toInt()
    private const val MUTED = 0xFF6C7086.toInt()
    private val SLOT_IDS = listOf(
        listOf(R.id.h0_time, R.id.h0_icon, R.id.h0_temp, R.id.h0_precip),
        listOf(R.id.h1_time, R.id.h1_icon, R.id.h1_temp, R.id.h1_precip),
        listOf(R.id.h2_time, R.id.h2_icon, R.id.h2_temp, R.id.h2_precip),
        listOf(R.id.h3_time, R.id.h3_icon, R.id.h3_temp, R.id.h3_precip),
        listOf(R.id.h4_time, R.id.h4_icon, R.id.h4_temp, R.id.h4_precip),
        listOf(R.id.h5_time, R.id.h5_icon, R.id.h5_temp, R.id.h5_precip),
    )

    private val network = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    fun onUpdate(c: Context) {
        render(c)
        refreshNow(c)
    }

    fun onDisabled(c: Context) {
        if (smallIds(c).isEmpty() && largeIds(c).isEmpty()) WorkManager.getInstance(c).cancelUniqueWork(PERIODIC)
    }

    /** Every 15 min while a widget exists (Android's minimum for periodic work). UPDATE replaces the 30-minute job
     *  that earlier versions left scheduled. */
    fun schedule(c: Context) {
        val req = PeriodicWorkRequestBuilder<WidgetWorker>(15, TimeUnit.MINUTES).setConstraints(network).build()
        WorkManager.getInstance(c).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.UPDATE, req)
    }

    /** The data only changes at two moments: when the hour turns, and when a new model run is published. Aim one-off
     *  jobs at both, so the widget is current within seconds instead of waiting for the next 15-minute slot. They do
     *  not repeat: every regular refresh arms them again, replacing any pending one (and they never re-arm
     *  themselves, which would cancel the running job). */
    fun armBoundaries(c: Context, ref: Instant, now: Instant) {
        if (smallIds(c).isEmpty() && largeIds(c).isEmpty()) return
        val wm = WorkManager.getInstance(c)
        fun at(name: String, delayMs: Long) {
            val req = OneTimeWorkRequestBuilder<WidgetWorker>()
                .setInitialDelay(delayMs.coerceAtLeast(5_000L), TimeUnit.MILLISECONDS)
                .setConstraints(network).addTag(BOUNDARY).build()
            wm.enqueueUniqueWork(name, ExistingWorkPolicy.REPLACE, req)
        }
        val nextHour = (now.epochSecond / 3600 + 1) * 3600
        at(HOUR, (nextHour + 20 - now.epochSecond) * 1000)                       // 20 s past the hour
        val expected = ref.plusSeconds(RUN_STEP_S + RUN_LAG_S)                   // next run, expected to be complete
        if (expected.isAfter(now)) at(RUN, Duration.between(now, expected).toMillis())
    }

    fun refreshNow(c: Context) {
        if (smallIds(c).isEmpty() && largeIds(c).isEmpty()) return
        schedule(c)
        val req = OneTimeWorkRequestBuilder<WidgetWorker>().setConstraints(network).build()
        WorkManager.getInstance(c).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, req)
    }

    private fun smallIds(c: Context) = AppWidgetManager.getInstance(c).getAppWidgetIds(ComponentName(c, WeatherWidget::class.java))
    fun largeIds(c: Context): IntArray =
        AppWidgetManager.getInstance(c).getAppWidgetIds(ComponentName(c, WeatherWidgetLarge::class.java))

    /** Draw every widget from the saved snapshot. */
    fun render(c: Context) {
        val s = Prefs.snapshot(c)
        val place = Prefs.place(c)
        val zone = runCatching { ZoneId.of(place.timezone) }.getOrDefault(ZoneId.systemDefault())
        val open = PendingIntent.getActivity(c, 0, Intent(c, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val manager = AppWidgetManager.getInstance(c)
        for ((layout, provider) in listOf(R.layout.widget to WeatherWidget::class.java, R.layout.widget_large to WeatherWidgetLarge::class.java)) {
            val large = layout == R.layout.widget_large
            val v = RemoteViews(c.packageName, layout)
            common(v, s, place, zone, large)
            if (large) detail(v, s)
            v.setOnClickPendingIntent(R.id.root, open)
            manager.updateAppWidget(ComponentName(c, provider), v)
        }
    }

    private fun common(v: RemoteViews, s: Snapshot?, place: Place, zone: ZoneId, large: Boolean) {
        if (s == null) {
            v.setTextViewText(R.id.icon, "⛅")
            v.setTextViewText(R.id.temp, "–")
            v.setTextViewText(R.id.place, place.location)
            v.setTextViewText(R.id.describe, "Chargement…")
            v.setTextViewText(R.id.next, "Données HRDPS d'Environnement Canada")
            v.setTextColor(R.id.next, DRY)                         // the layouts' preview colour is orange
            v.setTextViewText(R.id.meta, "")
            return
        }
        val age = (System.currentTimeMillis() - s.updated) / 60000
        v.setTextViewText(R.id.icon, s.icon)
        v.setTextViewText(R.id.temp, s.temp)
        v.setTextViewText(R.id.place, s.place)
        v.setTextViewText(R.id.describe, s.describe)
        v.setTextViewText(R.id.next, s.next)
        v.setTextColor(R.id.next, if (s.wet) WET else DRY)
        val stale = if (age > 90) " · données de ${age / 60} h" else ""
        val updated = "màj ${HM.format(Instant.ofEpochMilli(s.updated).atZone(zone))}$stale"
        // the large widget has its own "Ressenti" line: its last line names the model run instead
        v.setTextViewText(R.id.meta, if (large && s.run.isNotEmpty()) "HRDPS run ${s.run} · $updated" else "${s.feels} · $updated")
    }

    private fun detail(v: RemoteViews, s: Snapshot?) {
        val ready = s != null && s.detailed
        v.setTextViewText(R.id.feels, if (s != null) s.feels else "")
        v.setTextViewText(R.id.wind, if (ready) s!!.wind else "")
        v.setTextViewText(R.id.alerts, if (ready) s!!.alerts else if (s != null) "Chargement des détails…" else "")
        v.setTextColor(R.id.alerts, if (ready && s!!.alertOn) WET else MUTED)
        SLOT_IDS.forEachIndexed { k, ids ->
            val h = if (ready) s!!.slots.getOrNull(k) else null
            v.setTextViewText(ids[0], h?.time ?: "")
            v.setTextViewText(ids[1], h?.icon ?: "")
            v.setTextViewText(ids[2], h?.temp ?: "")
            v.setTextViewText(ids[3], h?.precip ?: "")
            v.setTextColor(ids[3], if (h?.wet == true) WET else DRY)
        }
    }
}

/** Background refresh: newest run → the point values around the current hour → snapshot → redraw. */
class WidgetWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val c = applicationContext
        val place = Prefs.place(c)
        val zone = runCatching { ZoneId.of(place.timezone) }.getOrDefault(ZoneId.systemDefault())
        val detailed = Widgets.largeIds(c).isNotEmpty()                 // only download the extras when they are shown
        val ref = GeoMet.latestRef() ?: return Result.retry()
        val now = Instant.now()
        if (Widgets.BOUNDARY !in tags) Widgets.armBoundaries(c, ref, now)
        val hourKey = GeoMet.iso(Instant.ofEpochSecond(now.epochSecond / 3600 * 3600))
        val old = Prefs.snapshot(c)
        if (old != null && old.ref == GeoMet.iso(ref) && old.hour == hourKey && old.place == place.location
            && (old.detailed || !detailed)) {
            Prefs.setSnapshot(c, old.copy(updated = System.currentTimeMillis()))   // checked just now: still current
            Widgets.render(c)                                           // same run, same hour: nothing new to fetch
            return Result.success()
        }
        val f = GeoMet.fetch(ref, place.latitude, place.longitude, now, detailed) ?: return Result.retry()
        val i = f.nowIndex(now)
        val next = nextPrecipitation(f, i, zone, place)
        var snap = Snapshot(
            icon = f.icon(i), temp = "${f.tt[i]?.let(::deg) ?: "–"}°", describe = f.describe(i),
            feels = f.re[i]?.let { "Ressenti ${deg(it)}°" } ?: "", next = next.first, wet = next.second,
            place = place.location, ref = GeoMet.iso(ref), hour = hourKey, updated = System.currentTimeMillis(),
        )
        if (detailed) snap = details(snap, f, i, zone, ref)
        Prefs.setSnapshot(c, snap)
        Widgets.render(c)
        return Result.success()
    }

    /** Wind and humidity now, 12-hour alerts (same rules as the desktop), and the hourly strip. */
    private fun details(s: Snapshot, f: Forecast, i: Int, zone: ZoneId, ref: Instant): Snapshot {
        val wind = buildList {
            f.ws[i]?.let { w -> add("Vent ${Forecast.fx(w)} km/h" + (f.wd[i]?.let { " " + Forecast.compass(it) } ?: "")) }
            val g = f.gust[i]; val w = f.ws[i]
            if (g != null && w != null && g - w > 5) add("rafales ${Forecast.fx(g)}")
            f.hr[i]?.let { add("HR ${Forecast.fx(it)} %") }
        }.joinToString(" · ")
        val al = f.alerts(i, GeoMet.AHEAD, zone)
        val alerts = if (al.isEmpty()) "Aucune alerte d'ici ${GeoMet.AHEAD} h" else al.joinToString("   ") { "⚠ " + it.first }
        val slots = GeoMet.slots(i).filter { it < f.n }.map { h ->
            val mm = f.pr[h] + f.pr[h - 1]                              // the 2 hours ending at h
            val precip = when {
                mm >= 0.2 -> "${"%.1f".format(Locale.CANADA_FRENCH, mm)} mm"
                f.pop[h] >= 10 -> "${Forecast.fx(f.pop[h])} %"
                else -> ""
            }
            Slot("${"%02d".format(f.local(h, zone).hour)}h", f.icon(h), "${f.tt[h]?.let(::deg) ?: "–"}°", precip, mm >= 0.2)
        }
        return s.copy(detailed = true, wind = wind, alerts = alerts, alertOn = al.isNotEmpty(),
            run = "%02dZ".format(ref.atZone(ZoneId.of("UTC")).hour), slots = slots)
    }

    /** ("Neige vers 15h · 4,5 mm d'ici 12 h", true) or ("Pas de précipitations d'ici 03h", false). */
    private suspend fun nextPrecipitation(f: Forecast, i: Int, zone: ZoneId, place: Place): Pair<String, Boolean> {
        val end = minOf(i + GeoMet.AHEAD, f.n - 1)
        val total = (i + 1..end).sumOf { f.pr[it] }
        val amount = if (total >= 0.1) " · ${"%.1f".format(Locale.CANADA_FRENCH, total)} mm d'ici ${end - i} h" else ""
        if (f.kind(i) != "none") return "En cours$amount" to true                // the line above already says what
        val j = (i + 1..end).firstOrNull { f.pr[it] >= 0.2 }
            ?: return "Pas de précipitations d'ici ${"%02d".format(f.local(end, zone).hour)}h" to false
        val what = GeoMet.ptypeAt(f.times[j], place.latitude, place.longitude)?.takeIf { it != "Aucune" } ?: "Précipitations"
        return "$what vers ${"%02d".format(f.local(j, zone).hour)}h$amount" to true
    }

    /** Whole degrees like the desktop (round half to even), without the "-0". */
    private fun deg(x: Double): String = Forecast.fx(x).let { if (it == "-0") "0" else it }
}
