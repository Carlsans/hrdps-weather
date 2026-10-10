package io.github.carlsans.hrdpsweather

import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlin.math.PI
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin

/**
 * The part of hrdps.py's Data class the widget needs (kind, icon, description, day/night), ported line by line
 * like web/js/hrdps.js. Pure Kotlin, so it is unit-tested on the JVM against the Python output (ForecastTest).
 *
 * series[key][i] / labels[key][i] use the desktop cache layout (keys of POINT_LAYERS, index 0 = run start);
 * the widget fills only the hours it downloaded and leaves the rest null.
 */
class Forecast(
    val times: List<Instant>,
    private val series: Map<String, List<Double?>>,
    private val labels: Map<String, List<String?>>,
    private val lat: Double,
    private val lon: Double,
) {
    val n: Int = times.size
    private fun s(k: String): List<Double?> = series[k] ?: List(n) { null }
    private fun l(k: String): List<String?> = labels[k] ?: List(n) { null }

    val tt: List<Double?> = fill(s("tt"))
    val re: List<Double?> = fill(s("re"))
    val nt: List<Double?> = fill(s("nt"))
    val tsprob: List<Double> = s("tsprob").map { it ?: 0.0 }
    val fogvis: List<Double?> = s("fogvis")
    val rate: List<Double> = s("rt").map { if (it != null) it * 3600 else 0.0 }   // mm/h instantaneous
    val pr: List<Double> = diff(s("acc_pr"))
    val rain: List<Double> = diff(s("acc_rn"))
    val swe: List<Double> = diff(s("acc_sn"))
    val frz: List<Double> = diff(s("acc_fr"))
    val pel: List<Double> = diff(s("acc_pe"))
    val ptype: List<String> = l("ptype").map { x -> if (!x.isNullOrEmpty()) PTYPE_FR[x] ?: x else "Aucune" }
    val pint: List<String> = l("pint").map { x -> if (!x.isNullOrEmpty()) PINT_FR[x] ?: x else "" }

    fun nowIndex(now: Instant = Instant.now()): Int {
        val hours = Math.floorDiv(now.epochSecond / 3600 * 3600 - times[0].epochSecond, 3600L).toInt()
        return hours.coerceIn(0, n - 1)
    }

    fun isDay(i: Int) = sunElevation(times[i].plusSeconds(1800), lat, lon) > -1.0

    /** Dominant precipitation of hour i: rain | snow | frz | pel | mix | none. */
    fun kind(i: Int): String {
        if (pr[i] < 0.05 && rate[i] < 0.05) return "none"
        val amt = linkedMapOf("rain" to rain[i], "snow" to swe[i], "frz" to frz[i], "pel" to pel[i])
        val tot = amt.values.sum()
        if (tot < 1e-3) return mapOf("Neige" to "snow", "Grésil" to "pel", "Pluie verglaçante" to "frz")[ptype[i]] ?: "rain"
        if (amt.getValue("frz") > 0.2 * tot) return "frz"
        var top = "rain"
        for ((k, v) in amt) if (v > amt.getValue(top)) top = k                    // first of ties, like max()
        if ((top == "snow" || top == "rain") && amt.getValue(if (top == "rain") "snow" else "rain") > 0.3 * tot) return "mix"
        return top
    }

    fun icon(i: Int): String {
        val k = kind(i)
        val day = isDay(i)
        if (k != "none" && tsprob[i] >= 40) return "⛈️"
        if (k == "snow") return if (pr[i] < 1.5) "🌨️" else "❄️"
        if (k == "frz" || k == "pel") return "🧊"
        if (k == "mix") return "🌨️"
        if (k == "rain") {
            val cover = nt[i].let { if (it == null || it == 0.0) 100.0 else it }       // Python: `nt or 100`
            return if (pr[i] < 0.5 && cover < 80) "🌦️" else "🌧️"
        }
        fogvis[i]?.let { if (it < 1000) return "🌫️" }
        val c = nt[i] ?: 50.0
        return when {
            c < 15 -> if (day) "☀️" else "🌙"
            c < 40 -> if (day) "🌤️" else "🌙"
            c < 75 -> if (day) "⛅" else "☁️"
            else -> "☁️"
        }
    }

    fun sky(i: Int): String {
        val c = nt[i] ?: 50.0
        return when { c < 15 -> "Dégagé"; c < 40 -> "Peu nuageux"; c < 75 -> "Partiellement nuageux"; else -> "Couvert" }
    }

    fun describe(i: Int): String {
        val k = kind(i)
        if (k == "none") return sky(i)
        val name = mapOf("rain" to "Pluie", "snow" to "Neige", "frz" to "Pluie verglaçante", "pel" to "Grésil",
            "mix" to "Pluie et neige").getValue(k)
        return if (pint[i].isNotEmpty()) "$name (${pint[i].lowercase()})" else name
    }

    fun local(i: Int, zone: ZoneId): ZonedDateTime = times[i].atZone(zone)

    companion object {
        val PTYPE_FR = mapOf("None" to "Aucune", "Rain" to "Pluie", "Snow" to "Neige", "Freezing rain" to "Pluie verglaçante",
            "Ice pellets" to "Grésil", "Drizzle" to "Bruine", "Freezing drizzle" to "Bruine verglaçante",
            "Wet snow" to "Neige mouillée", "Rain/snow" to "Pluie et neige", "Snow grains" to "Neige en grains")
        val PINT_FR = mapOf("No Intensity" to "", "Low Intensity" to "Faible", "Light Intensity" to "Faible",
            "Moderate Intensity" to "Modérée", "Heavy Intensity" to "Forte", "Very Heavy Intensity" to "Très forte",
            "No precipitation" to "Aucune")

        /** Fill gaps of a continuous series: linear interpolation inside, nearest value at the edges. */
        fun fill(vals: List<Double?>): List<Double?> {
            val known = vals.indices.filter { vals[it] != null }
            if (known.isEmpty() || known.size == vals.size) return vals
            return vals.indices.map { i ->
                vals[i] ?: run {
                    val lo = known.lastOrNull { it < i }
                    val hi = known.firstOrNull { it > i }
                    when {
                        lo == null -> vals[hi!!]
                        hi == null -> vals[lo]
                        else -> vals[lo]!! + (vals[hi]!! - vals[lo]!!) * (i - lo) / (hi - lo)
                    }
                }
            }
        }

        /** Hourly amount from a cumulative series (missing values count as 0, like the desktop). */
        fun diff(acc: List<Double?>): List<Double> {
            val a = acc.map { it ?: 0.0 }
            return a.indices.map { i -> if (i == 0) 0.0 else max(a[i] - a[i - 1], 0.0) }
        }

        /** Approximate solar elevation (degrees) at lat/lon (NOAA low-precision formula). */
        fun sunElevation(t: Instant, lat: Double, lon: Double): Double {
            val u = t.atZone(ZoneId.of("UTC"))
            val d = u.dayOfYear + (u.hour + u.minute / 60.0) / 24
            val g = 2 * PI / 365 * (d - 1)
            val decl = 0.006918 - 0.399912 * cos(g) + 0.070257 * sin(g) - 0.006758 * cos(2 * g) + 0.000907 * sin(2 * g)
            val eqt = 229.18 * (0.000075 + 0.001868 * cos(g) - 0.032077 * sin(g) - 0.014615 * cos(2 * g) - 0.040849 * sin(2 * g))
            val ha = Math.toRadians((u.hour * 60 + u.minute + eqt + 4 * lon) / 4 - 180)
            val la = Math.toRadians(lat)
            return Math.toDegrees(asin(sin(la) * sin(decl) + cos(la) * cos(decl) * cos(ha)))
        }
    }
}
