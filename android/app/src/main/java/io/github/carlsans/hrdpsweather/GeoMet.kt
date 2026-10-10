package io.github.carlsans.hrdpsweather

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

/** MSC GeoMet requests for the widget (the same WMS GetFeatureInfo point queries as hrdps.py). */
object GeoMet {
    const val GEOMET = "https://geo.weather.gc.ca/geomet"
    const val HOURS = 48
    const val AHEAD = 12                       // hours scanned for the next precipitation
    private const val C = "HRDPS.CONTINENTAL_"
    private const val W = "HRDPS-WEonG_2.5km_"
    val LAYERS = mapOf(
        "tt" to C + "TT", "re" to C + "RE", "nt" to C + "NT", "rt" to C + "RT",
        "acc_pr" to C + "PR", "acc_rn" to C + "RN", "acc_sn" to C + "SN", "acc_fr" to C + "FR", "acc_pe" to C + "PE",
        "ptype" to W + "DominantPrecipType", "tsprob" to W + "Thunderstorm-Prob", "pint" to W + "TotalPrecipIntensityIndex",
        "fogvis" to W + "LiquidFogVisibility",
        "hr" to C + "HR", "ws" to C + "WSPD", "wd" to C + "WD", "wgx" to C + "WGX", "pop" to W + "Precip-Prob",
    )
    private val EMPTY_IS_ZERO = setOf("acc_pr", "acc_rn", "acc_sn", "acc_fr", "acc_pe", "rt", "tsprob")
    const val SLOT_STEP = 2                    // large widget: one column every 2 h…
    const val SLOTS = 6                        // …up to +12 h
    private val LABEL_KEYS = setOf("ptype", "pint")
    private val ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'").withZone(ZoneId.of("UTC"))
    private const val UA = "hrdps-weather-android (+https://github.com/Carlsans/hrdps-weather)"

    fun iso(t: Instant): String = ISO.format(t)

    private fun get(url: String, timeoutMs: Int = 30000, tries: Int = 2): String? {
        repeat(tries) {
            try {
                val c = URL(url).openConnection() as HttpURLConnection
                c.connectTimeout = timeoutMs; c.readTimeout = timeoutMs
                c.setRequestProperty("User-Agent", UA)
                try {
                    if (c.responseCode == 200) return c.inputStream.bufferedReader().use { it.readText() }
                    if (c.responseCode in 400..499) return null
                } finally {
                    c.disconnect()
                }
            } catch (_: Exception) {
            }
        }
        return null
    }

    /** Reference time of the newest run in a GetCapabilities document (same parsing as hrdps.latest_ref). */
    fun parseLatestRef(s: String): Instant? {
        val i = s.indexOf("<Name>${C}TT</Name>")
        if (i < 0) return null
        val m = Regex("name=\"reference_time\"[^>]*>([^<]*)<").find(s.substring(i, minOf(s.length, i + 6000))) ?: return null
        val parts = m.groupValues[1].split("/")
        val last = if (parts.size >= 2) parts[1] else parts[0].split(",").last()
        return try { Instant.parse(last.trim()) } catch (_: Exception) { null }
    }

    fun latestRef(): Instant? =
        get("$GEOMET?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities&LAYER=${C}TT", 40000)?.let(::parseLatestRef)

    sealed class Cell { object Failed : Cell(); object Empty : Cell(); data class Value(val v: Double?, val label: String?) : Cell() }

    /** One GetFeatureInfo answer: Value, Empty (no feature: nil value) or Failed (network). */
    fun parseFeature(raw: String?): Cell {
        if (raw == null) return Cell.Failed
        return try {
            val feats = JSONObject(raw).optJSONArray("features") ?: return Cell.Empty
            if (feats.length() == 0) return Cell.Empty
            val p = feats.getJSONObject(0).getJSONObject("properties")
            Cell.Value(if (p.isNull("value")) null else p.getDouble("value"), if (p.isNull("class")) null else p.getString("class"))
        } catch (_: Exception) {
            Cell.Failed
        }
    }

    private fun feature(layer: String, t: Instant, lat: Double, lon: Double): Cell {
        val d = 0.01
        val url = "$GEOMET?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&LAYERS=$layer&QUERY_LAYERS=$layer" +
            "&CRS=EPSG:4326&BBOX=${lat - d},${lon - d},${lat + d},${lon + d}&WIDTH=101&HEIGHT=101&I=50&J=50" +
            "&INFO_FORMAT=application/json&FORMAT=image/png&STYLES=&TIME=${iso(t)}"
        return parseFeature(get(url))
    }

    /** Which (key, hour) cells the widget needs for hour i: the current hour, the hour before (hourly amounts
     *  come from cumulative layers) and the cumulative precipitation over the next AHEAD hours. */
    fun cellsFor(i: Int, detailed: Boolean = false): List<Pair<String, Int>> {
        val end = minOf(i + AHEAD, HOURS)
        val out = linkedSetOf<Pair<String, Int>>()
        for (h in maxOf(i - 1, 0)..end) out += "acc_pr" to h
        for (k in listOf("acc_rn", "acc_sn", "acc_fr", "acc_pe")) for (h in maxOf(i - 1, 0)..i) out += k to h
        for (k in listOf("tt", "re", "nt", "rt", "tsprob", "fogvis", "ptype", "pint")) out += k to i
        if (detailed) {
            // the large widget: 12-hour alerts (kind, gusts, rain/snow, frost, storms hour by hour), wind and
            // humidity now, and icon / temperature / probability for each column of the hourly strip
            for (k in listOf("acc_rn", "acc_sn", "acc_fr", "acc_pe")) for (h in maxOf(i - 1, 0)..end) out += k to h
            for (k in listOf("tt", "ws", "wgx", "tsprob", "rt")) for (h in i..end) out += k to h
            for (h in slots(i) + i) for (k in listOf("nt", "fogvis", "ptype", "pint", "pop")) out += k to h
            for (k in listOf("hr", "wd")) out += k to i
        }
        return out.toList()
    }

    /** Hours shown in the large widget's strip. */
    fun slots(i: Int): List<Int> = (1..SLOTS).map { i + it * SLOT_STEP }.filter { it <= HOURS }

    /** Download the cells for the current hour of run `ref` and build a sparse Forecast around it. */
    suspend fun fetch(ref: Instant, lat: Double, lon: Double, now: Instant = Instant.now(), detailed: Boolean = false): Forecast? =
        withContext(Dispatchers.IO) {
        val times = (0..HOURS).map { ref.plus(it.toLong(), ChronoUnit.HOURS) }
        val i = Math.floorDiv(now.epochSecond / 3600 * 3600 - ref.epochSecond, 3600L).toInt().coerceIn(0, HOURS)
        val cells = cellsFor(i, detailed)
        val gate = Semaphore(4)                                         // gentle with GeoMet
        val res = coroutineScope {
            cells.map { (k, h) -> async { gate.withPermit { feature(LAYERS.getValue(k), times[h], lat, lon) } } }.awaitAll()
        }
        if (res.count { it is Cell.Failed } > cells.size / 3) return@withContext null      // mostly offline: keep the old widget
        val series = LAYERS.keys.associateWith { MutableList<Double?>(times.size) { null } }
        val labels = LAYERS.keys.associateWith { MutableList<String?>(times.size) { null } }
        cells.zip(res).forEach { (cell, r) ->
            val (k, h) = cell
            when (r) {
                is Cell.Empty -> if (k in EMPTY_IS_ZERO) series.getValue(k)[h] = 0.0
                is Cell.Value -> { series.getValue(k)[h] = r.v; if (k in LABEL_KEYS) labels.getValue(k)[h] = r.label }
                Cell.Failed -> {}
            }
        }
        if (series.getValue("tt")[i] == null) return@withContext null                     // run still being published
        Forecast(times, series, labels, lat, lon)
    }

    /** Precipitation type at one hour (for "Neige vers 15h"), French, or null. */
    suspend fun ptypeAt(t: Instant, lat: Double, lon: Double): String? = withContext(Dispatchers.IO) {
        (feature(LAYERS.getValue("ptype"), t, lat, lon) as? Cell.Value)?.label?.let { Forecast.PTYPE_FR[it] ?: it }
    }
}
