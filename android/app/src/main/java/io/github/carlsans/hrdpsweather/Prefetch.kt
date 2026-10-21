package io.github.carlsans.hrdpsweather

import android.content.Context
import android.net.ConnectivityManager
import android.os.SystemClock
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.time.Duration
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlin.math.asinh
import kotlin.math.atan
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.roundToInt
import kotlin.math.sinh
import kotlin.math.tan

/**
 * Downloads a whole HRDPS run (the point series and the map grids the web app draws) in the background, so that the
 * app opens on data that is already there. GeoMet answers about 4–5 requests a second whatever the parallelism, and a
 * run is ~1 400 point queries + ~500 grids: that is minutes, which the web page cannot do before being killed.
 *
 * The work is resumable (every answer is kept on disk, a worker stops after a time budget and queues its
 * continuation) and the result is handed to the page through MainActivity's request interception (`state.json`,
 * then the files listed in it), in the same shape as the web app's own IndexedDB entries.
 * Series download on any network; the maps (~35 MB per run) wait for an unmetered one.
 */
object Prefetch {
    const val NATIVE_HOST = "native.hrdps.invalid"
    const val RUN_TAG = "prefetch-run-tag"
    private const val NOW = "prefetch-now"
    private const val PERIODIC = "prefetch-periodic"
    private const val MORE = "prefetch-more"
    private const val RUN = "prefetch-run"
    private const val BUDGET_MS = 8 * 60_000L                  // WorkManager kills a job after ~10 minutes
    private const val RUN_STEP_S = 6 * 3600L
    private const val RUN_LAG_S = 3 * 3600L + 11 * 60L
    private const val WORKERS = 6
    private const val HOURS = GeoMet.HOURS
    private const val C = "HRDPS.CONTINENTAL_"
    private const val W = "HRDPS-WEonG_2.5km_"

    // the same layers, in the same order, as web/js/hrdps.js
    private val POINT = linkedMapOf(
        "tt" to C + "TT", "td" to C + "TD", "hr" to C + "HR", "re" to C + "RE", "hmx" to C + "HMX", "utci" to C + "UTCI",
        "ws" to C + "WSPD", "wd" to C + "WD", "wgx" to C + "WGX", "slp" to C + "PN-SLP", "nt" to C + "NT", "rt" to C + "RT",
        "acc_pr" to C + "PR", "acc_rn" to C + "RN", "acc_sn" to C + "SN", "acc_fr" to C + "FR", "acc_pe" to C + "PE",
        "uv" to C + "IUVA", "sd" to C + "SD", "pbl" to C + "HPBL", "cape" to C + "BE",
        "ptype" to W + "DominantPrecipType", "pop" to W + "Precip-Prob", "snowlvl" to W + "SnowLevelHeight",
        "tsprob" to W + "Thunderstorm-Prob", "pint" to W + "TotalPrecipIntensityIndex",
        "pchar" to W + "PrecipCharacter", "fogvis" to W + "LiquidFogVisibility",
    )
    private val EMPTY_IS_ZERO = setOf("acc_pr", "acc_rn", "acc_sn", "acc_fr", "acc_pe", "sd", "cape", "rt", "tsprob")
    private val MAPS = linkedMapOf("rt" to C + "RT", "tt" to C + "TT", "ws" to C + "WSPD", "wd" to C + "WD", "nt" to C + "NT")
    private const val REGION_ZOOM = 8
    private const val WIDE_ZOOM = 6
    private const val WIDE_CELLS = 80

    enum class Outcome { DONE, MORE, RETRY }

    private val network = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).setRequiresBatteryNotLow(true).build()

    // ── files ────────────────────────────────────────────────────────────────
    private fun root(c: Context) = File(c.filesDir, "prefetch").apply { mkdirs() }
    fun readyDir(c: Context) = File(root(c), "ready").apply { mkdirs() }
    fun stateFile(c: Context) = File(root(c), "state.json")
    private fun compact(ref: String) = ref.replace("-", "").replace(":", "")
    private fun cellsDir(c: Context, ref: String, p: Place) =
        File(root(c), "cells/${compact(ref)}_${"%.4f".format(p.latitude)}_${"%.4f".format(p.longitude)}").apply { mkdirs() }

    private fun atomic(f: File, write: (File) -> Unit) {
        val tmp = File(f.path + ".tmp")
        write(tmp)
        if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
    }

    private fun readState(c: Context): JSONObject = try { JSONObject(stateFile(c).readText()) } catch (_: Exception) { JSONObject() }

    private fun writeState(c: Context, edit: JSONObject.() -> Unit) = synchronized(this) {
        val s = readState(c).apply(edit)
        atomic(stateFile(c)) { it.writeText(s.toString()) }
    }

    private fun progress(c: Context, ref: String, phase: String, done: Int, total: Int) =
        writeState(c) { put("progress", JSONObject().put("ref", ref).put("phase", phase).put("done", done).put("total", total)
            .put("updated", System.currentTimeMillis())) }

    // ── scheduling ───────────────────────────────────────────────────────────
    /** Look for a new run every 30 minutes (cheap when there is none), as long as the app has a place. */
    fun schedule(c: Context) {
        val req = PeriodicWorkRequestBuilder<PrefetchWorker>(30, TimeUnit.MINUTES).setConstraints(network).build()
        WorkManager.getInstance(c).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, req)
    }

    /** Start now (the app was just opened). Replaces a pending job, which may be waiting out a retry back-off, or one
     *  that is running: the work is resumable, a restart only loses the requests in flight. */
    fun now(c: Context) {
        schedule(c)
        val req = OneTimeWorkRequestBuilder<PrefetchWorker>().setConstraints(network).build()
        WorkManager.getInstance(c).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, req)
    }

    private fun more(c: Context) {
        val req = OneTimeWorkRequestBuilder<PrefetchWorker>().setInitialDelay(5, TimeUnit.SECONDS).setConstraints(network).build()
        WorkManager.getInstance(c).enqueueUniqueWork(MORE, ExistingWorkPolicy.REPLACE, req)
    }

    /** One job aimed at the moment the next run should be complete (not armed by itself: it would cancel itself). */
    private fun armRun(c: Context, ref: Instant) {
        val due = ref.plusSeconds(RUN_STEP_S + RUN_LAG_S)
        val delay = Duration.between(Instant.now(), due).toMillis().coerceAtLeast(5_000L)
        val req = OneTimeWorkRequestBuilder<PrefetchWorker>().setInitialDelay(delay, TimeUnit.MILLISECONDS)
            .setConstraints(network).addTag(RUN_TAG).build()
        WorkManager.getInstance(c).enqueueUniqueWork(RUN, ExistingWorkPolicy.REPLACE, req)
    }

    // ── the job ──────────────────────────────────────────────────────────────
    suspend fun run(c: Context, armed: Boolean): Outcome = withContext(Dispatchers.IO) {
        val place = Prefs.place(c)
        val ref = GeoMet.latestRef() ?: return@withContext Outcome.RETRY
        if (!armed) armRun(c, ref)
        val refIso = GeoMet.iso(ref)
        val st = readState(c)
        val samePlace = st.optDouble("lat") == place.latitude && st.optDouble("lon") == place.longitude
        val haveSeries = samePlace && st.optJSONObject("series")?.optString("ref") == refIso
        val haveMaps = samePlace && st.optJSONObject("maps")?.optString("ref") == refIso
        val metered = (c.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).isActiveNetworkMetered
        if (haveSeries && (haveMaps || metered)) return@withContext Outcome.DONE
        val deadline = SystemClock.elapsedRealtime() + BUDGET_MS
        val dir = cellsDir(c, refIso, place)
        if (!haveSeries) {
            progress(c, refIso, "series", 0, POINT.size * (HOURS + 1))
            val r = seriesPhase(c, ref, refIso, place, dir, deadline)
            if (r != Outcome.DONE) return@withContext r
        }
        if (metered && !haveMaps) writeState(c) { remove("progress") }   // no maps on mobile data: the page fetches them itself
        if (!metered && !haveMaps) {
            progress(c, refIso, "maps", 0, 2 * (HOURS + 1) * MAPS.size)
            val r = mapsPhase(c, ref, refIso, place, dir, deadline)
            if (r != Outcome.DONE) return@withContext r
        }
        writeState(c) { remove("progress") }
        clean(c, refIso)
        Outcome.DONE
    }

    private fun clean(c: Context, keepRef: String) {
        File(root(c), "cells").listFiles()?.filter { !it.name.startsWith(compact(keepRef)) }?.forEach { it.deleteRecursively() }
        val st = readState(c)
        val keep = setOfNotNull(st.optJSONObject("series")?.optString("file"), st.optJSONObject("maps")?.optString("dir"))
        readyDir(c).listFiles()?.filter { it.name !in keep }?.forEach { it.deleteRecursively() }
    }

    /** Runs `todo` through GeoMet with a few workers; `one` returns false for a failed request. */
    private suspend fun <T> pool(c: Context, ref: String, phase: String, todo: List<T>, deadline: Long,
                                 one: (T) -> Boolean): Pair<Int, Int> {
        val gate = Semaphore(WORKERS)
        var done = 0; var failed = 0
        coroutineScope {
            todo.map { item ->
                async(Dispatchers.IO) {
                    gate.withPermit {
                        if (SystemClock.elapsedRealtime() < deadline) {
                            val ok = one(item)
                            synchronized(this@Prefetch) {
                                done++; if (!ok) failed++
                                if (done % 25 == 0) progress(c, ref, phase, done, todo.size)
                            }
                        }
                    }
                }
            }.awaitAll()
        }
        return done to failed
    }

    // ── series ───────────────────────────────────────────────────────────────
    private suspend fun seriesPhase(c: Context, ref: Instant, refIso: String, place: Place, dir: File, deadline: Long): Outcome {
        val file = File(dir, "series.tsv")
        val times = (0..HOURS).map { ref.plus(it.toLong(), java.time.temporal.ChronoUnit.HOURS) }
        val keys = POINT.keys.toList()
        val got = HashMap<String, GeoMet.Cell>()          // "key|hour" -> answer
        if (file.exists()) file.forEachLine { ln ->
            val p = ln.split('\t')
            if (p.size >= 4) got["${p[0]}|${p[1]}"] = when (p[2]) {
                "E" -> GeoMet.Cell.Empty
                else -> GeoMet.Cell.Value(num(p[3]), p.getOrNull(4)?.takeIf { it.isNotEmpty() })
            }
        }
        val all = (0..HOURS).flatMap { i -> keys.map { it to i } }       // hour by hour
        for (pass in 0 until 3) {
            val todo = all.filter { "${it.first}|${it.second}" !in got }
            if (todo.isEmpty()) break
            val (done, failed) = pool(c, refIso, "series", todo, deadline) { (k, i) ->
                val cell = GeoMet.feature(POINT.getValue(k), times[i], place.latitude, place.longitude)
                if (cell is GeoMet.Cell.Failed) false else {
                    synchronized(got) {
                        got["$k|$i"] = cell
                        file.appendText(when (cell) {
                            is GeoMet.Cell.Value -> "$k\t$i\tV\t${cell.v}\t${cell.label ?: ""}\n"
                            else -> "$k\t$i\tE\t\t\n"
                        })
                    }
                    true
                }
            }
            if (SystemClock.elapsedRealtime() >= deadline) return Outcome.MORE
            if (pass == 0 && done >= 50 && failed > done / 2) return Outcome.RETRY    // mostly offline (a few cells never answer, e.g. hour 0)
            if (pass > 0) Thread.sleep(2000)
        }
        val missing = all.count { "${it.first}|${it.second}" !in got }
        val tt = (0..HOURS).count { (got["tt|$it"] as? GeoMet.Cell.Value)?.v != null }
        if (missing > all.size / 20 || tt < 40) return Outcome.RETRY     // run still being published, or the network is poor
        val series = JSONObject(); val labels = JSONObject()
        for (k in keys) {
            val v = JSONArray(); val l = JSONArray()
            for (i in 0..HOURS) {
                when (val cell = got["$k|$i"]) {
                    is GeoMet.Cell.Empty -> { v.put(if (k in EMPTY_IS_ZERO) 0.0 else JSONObject.NULL); l.put(JSONObject.NULL) }
                    is GeoMet.Cell.Value -> { v.put(cell.v ?: JSONObject.NULL); l.put(cell.label ?: JSONObject.NULL) }
                    else -> { v.put(JSONObject.NULL); l.put(JSONObject.NULL) }
                }
            }
            series.put(k, v); labels.put(k, l)
        }
        val raw = JSONObject().put("ref", refIso).put("times", JSONArray(times.map { GeoMet.iso(it) }))
            .put("series", series).put("labels", labels)
        val name = "series-${compact(refIso)}.json"
        atomic(File(readyDir(c), name)) { it.writeText(raw.toString()) }
        writeState(c) {
            put("lat", place.latitude); put("lon", place.longitude)
            put("series", JSONObject().put("ref", refIso).put("file", name))
        }
        return Outcome.DONE
    }

    // ── maps ─────────────────────────────────────────────────────────────────
    private fun tileXY(lat: Double, lon: Double, z: Int): Pair<Int, Int> {
        val n = Math.pow(2.0, z.toDouble())
        return floor((lon + 180) / 360 * n).toInt() to floor((1 - asinh(tan(Math.toRadians(lat))) / Math.PI) / 2 * n).toInt()
    }

    private fun tileLonLat(x: Int, y: Int, z: Int): Pair<Double, Double> {
        val n = Math.pow(2.0, z.toDouble())
        return x / n * 360 - 180 to Math.toDegrees(atan(sinh(Math.PI * (1 - 2 * y / n))))
    }

    /** [lon_w, lat_s, lon_e, lat_n] of the 3×3 tile block around the point (mapBounds in hrdps.js). */
    private fun mapBounds(lat: Double, lon: Double, z: Int): DoubleArray {
        val (x, y) = tileXY(lat, lon, z)
        val (w, n) = tileLonLat(x - 1, y - 1, z)
        val (e, s) = tileLonLat(x + 2, y + 2, z)
        return doubleArrayOf(w, s, e, n)
    }

    /** toDoubleOrNull() screens its input with a regex (ICU), which ran the process out of memory on grid-sized work. */
    private fun num(s: String): Double? = try { java.lang.Double.parseDouble(s) } catch (_: NumberFormatException) { null }

    private fun words(line: String): List<String> {
        val t = java.util.StringTokenizer(line)
        return ArrayList<String>(t.countTokens()).also { while (t.hasMoreTokens()) it.add(t.nextToken()) }
    }

    /** ESRI ASCII grid -> (ny, nx, floats north→south), or null. */
    internal fun parseGrid(text: String): Triple<Int, Int, FloatArray>? {
        val start = text.indexOf("ncols")
        if (start < 0) return null
        val lines = text.substring(start).split('\n')
        var k = 0
        var nodata = Double.NaN
        while (k < lines.size && lines[k].isNotEmpty() && lines[k][0].isLetter()) {
            val a = words(lines[k])
            if (a[0].lowercase() == "nodata_value") nodata = a.getOrNull(1)?.let(::num) ?: Double.NaN
            k++
        }
        val rows = ArrayList<List<String>>()
        while (k < lines.size) {
            val ln = lines[k++]
            if (ln.startsWith("--") || ln.isBlank()) break
            rows.add(words(ln))
        }
        if (rows.isEmpty()) return null
        val nx = rows[0].size
        if (rows.any { it.size != nx }) return null
        val data = FloatArray(rows.size * nx)
        rows.forEachIndexed { y, r -> r.forEachIndexed { x, s ->
            val v = num(s) ?: Double.NaN
            data[y * nx + x] = if ((!nodata.isNaN() && v == nodata) || v < -1e20) Float.NaN else v.toFloat()
        } }
        return Triple(rows.size, nx, data)
    }

    private fun gridUrl(layer: String, t: Instant, b: DoubleArray, cells: Int): String {
        val (w, s, e, n) = b.toList()
        val scale = if (cells > 0) "&SCALESIZE=x($cells),y(${(cells * (n - s) / (e - w)).roundToInt()})" else ""
        return "${GeoMet.GEOMET}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=$layer&FORMAT=image/x-aaigrid" +
            "&SUBSETTINGCRS=EPSG:4326&SUBSET=x($w,$e)&SUBSET=y($s,$n)$scale&TIME=${GeoMet.iso(t)}"
    }

    private suspend fun mapsPhase(c: Context, ref: Instant, refIso: String, place: Place, dir: File, deadline: Long): Outcome {
        val times = (0..HOURS).map { ref.plus(it.toLong(), java.time.temporal.ChronoUnit.HOURS) }
        val sets = linkedMapOf("region" to (mapBounds(place.latitude, place.longitude, REGION_ZOOM) to 0),   // 0: native size
            "wide" to (mapBounds(place.latitude, place.longitude, WIDE_ZOOM) to WIDE_CELLS))
        fun cell(p: String, k: String, i: Int) = File(dir, "m_${p}_${k}_$i.bin")
        val all = sets.keys.flatMap { p -> (0..HOURS).flatMap { i -> MAPS.keys.map { k -> Triple(p, k, i) } } }
        for (pass in 0 until 3) {
            val todo = all.filter { (p, k, i) -> !cell(p, k, i).exists() }
            if (todo.isEmpty()) break
            val (done, failed) = pool(c, refIso, "maps", todo, deadline) { (p, k, i) ->
                val (b, cells) = sets.getValue(p)
                val g = GeoMet.get(gridUrl(MAPS.getValue(k), times[i], b, cells), 60000)?.let(::parseGrid)
                if (g == null) false else {
                    val bb = ByteBuffer.allocate(8 + g.third.size * 4).order(ByteOrder.LITTLE_ENDIAN)
                    bb.putInt(g.first).putInt(g.second); g.third.forEach { bb.putFloat(it) }
                    atomic(cell(p, k, i)) { it.writeBytes(bb.array()) }
                    true
                }
            }
            if (SystemClock.elapsedRealtime() >= deadline) return Outcome.MORE
            if (pass == 0 && done >= 50 && failed > done / 2) return Outcome.RETRY    // mostly offline (a few cells never answer, e.g. hour 0)
            if (pass > 0) Thread.sleep(2000)
        }
        val have = all.count { (p, k, i) -> cell(p, k, i).exists() }
        if (have < all.size * 0.9) return Outcome.RETRY
        val out = File(readyDir(c), "maps-${compact(refIso)}"); out.deleteRecursively(); out.mkdirs()
        val header = JSONObject().put("ref", refIso).put("layers", JSONArray(MAPS.keys.toList()))
        for ((p, bc) in sets) {
            // grid size = that of the first answer of the set (all answers of a set share it)
            val first = (0..HOURS).flatMap { i -> MAPS.keys.map { k -> cell(p, k, i) } }.firstOrNull { it.exists() }
            if (first == null) { if (p == "region") return Outcome.RETRY else continue }
            val hb = ByteBuffer.wrap(first.readBytes(), 0, 8).order(ByteOrder.LITTLE_ENDIAN)
            val ny = hb.getInt(); val nx = hb.getInt()
            for (k in MAPS.keys) {
                val all4 = ByteBuffer.allocate((HOURS + 1) * ny * nx * 4).order(ByteOrder.LITTLE_ENDIAN)
                val fb = all4.asFloatBuffer()
                for (i in 0..HOURS) {
                    val f = cell(p, k, i)
                    val bytes = if (f.exists()) f.readBytes() else null
                    val bb = bytes?.let { ByteBuffer.wrap(it).order(ByteOrder.LITTLE_ENDIAN) }
                    if (bb != null && bb.getInt(0) == ny && bb.getInt(4) == nx) {
                        bb.position(8); fb.put(bb.asFloatBuffer())
                    } else for (n in 0 until ny * nx) fb.put(Float.NaN)
                }
                File(out, "${p}_$k.bin").writeBytes(all4.array())
            }
            header.put(p, JSONObject().put("bounds", JSONArray(bc.first.toList())).put("T", HOURS + 1).put("ny", ny).put("nx", nx))
        }
        File(out, "header.json").writeText(header.toString())
        writeState(c) {
            put("lat", place.latitude); put("lon", place.longitude)
            put("maps", JSONObject().put("ref", refIso).put("dir", out.name))
            remove("progress")
        }
        return Outcome.DONE
    }

    fun continueLater(c: Context) = more(c)
}

class PrefetchWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val armed = Prefetch.RUN_TAG in tags
        return try {
            when (Prefetch.run(applicationContext, armed)) {
                Prefetch.Outcome.DONE -> Result.success()
                Prefetch.Outcome.MORE -> { Prefetch.continueLater(applicationContext); Result.success() }
                Prefetch.Outcome.RETRY -> Result.retry()
            }
        } catch (e: Exception) {
            Result.retry()
        }
    }
}
