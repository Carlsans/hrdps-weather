package io.github.carlsans.hrdpsweather

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.time.Instant

/** The widget's logic must give what the desktop gives (same run, same place): see web/tests/make_expected.py. */
class ForecastTest {
    private val sample = JSONObject(File(System.getProperty("sample")!!).readText(Charsets.UTF_8))
    private val expectedPath = System.getProperty("expected").orEmpty()
    private val times = sample.getJSONArray("times").let { a -> (0 until a.length()).map { Instant.parse(a.getString(it)) } }

    private fun doubles(keep: (String, Int) -> Boolean = { _, _ -> true }): Map<String, List<Double?>> {
        val o = sample.getJSONObject("series")
        return o.keys().asSequence().associateWith { k ->
            val a = o.getJSONArray(k)
            (0 until a.length()).map { h -> if (keep(k, h) && !a.isNull(h)) a.getDouble(h) else null }
        }
    }

    private fun strings(keep: (String, Int) -> Boolean = { _, _ -> true }): Map<String, List<String?>> {
        val o = sample.getJSONObject("labels")
        return o.keys().asSequence().associateWith { k ->
            val a = o.getJSONArray(k)
            (0 until a.length()).map { h -> if (keep(k, h) && !a.isNull(h)) a.getString(h) else null }
        }
    }

    @Test
    fun matchesPython() {
        assumeTrue("set EXPECTED (python web/tests/make_expected.py expected.json)", expectedPath.isNotEmpty())
        val exp = JSONObject(File(expectedPath).readText(Charsets.UTF_8))
        val place = exp.getJSONObject("place")
        val f = Forecast(times, doubles(), strings(), place.getDouble("latitude"), place.getDouble("longitude"))
        for (i in 0 until f.n) {
            assertEquals("kind[$i]", exp.getJSONArray("kind").getString(i), f.kind(i))
            assertEquals("icon[$i]", exp.getJSONArray("icon").getString(i), f.icon(i))
            assertEquals("describe[$i]", exp.getJSONArray("describe").getString(i), f.describe(i))
            assertEquals("is_day[$i]", exp.getJSONArray("is_day").getBoolean(i), f.isDay(i))
        }
        val series = exp.getJSONObject("series")
        for ((k, v) in mapOf("tt" to f.tt, "re_" to f.re, "nt" to f.nt, "pr" to f.pr, "rain" to f.rain, "rate" to f.rate)) {
            val a: JSONArray = series.getJSONArray(k)
            for (i in 0 until f.n) assertEquals("$k[$i]", if (a.isNull(i)) Double.NaN else a.getDouble(i), v[i] ?: Double.NaN, 1e-9)
        }
    }

    @Test
    fun sparseForecastAroundOneHour() {
        // Only what the widget downloads (GeoMet.cellsFor) must give the same icon, text and hourly precipitation.
        val full = Forecast(times, doubles(), strings(), 46.8139, -71.2080)
        for (i in listOf(0, 1, 7, 26, 40, 48)) {
            val keep = GeoMet.cellsFor(i).toSet()
            val f = Forecast(times, doubles { k, h -> (k to h) in keep }, strings { k, h -> (k to h) in keep }, 46.8139, -71.2080)
            assertEquals("icon@$i", full.icon(i), f.icon(i))
            assertEquals("describe@$i", full.describe(i), f.describe(i))
            assertEquals("tt@$i", full.tt[i]!!, f.tt[i]!!, 1e-9)
            for (j in i + 1..minOf(i + GeoMet.AHEAD, full.n - 1)) assertEquals("pr[$j]@$i", full.pr[j], f.pr[j], 1e-9)
        }
    }

    @Test
    fun parsing() {
        val caps = "<Layer><Name>HRDPS.CONTINENTAL_TT</Name><Dimension name=\"reference_time\" units=\"ISO8601\">" +
            "2026-10-09T06:00:00Z/2026-10-10T12:00:00Z/PT6H</Dimension></Layer>"
        assertEquals(Instant.parse("2026-10-10T12:00:00Z"), GeoMet.parseLatestRef(caps))
        assertEquals(null, GeoMet.parseLatestRef("<x/>"))
        assertEquals(GeoMet.Cell.Empty, GeoMet.parseFeature("""{"type":"FeatureCollection","features":[]}"""))
        assertEquals(GeoMet.Cell.Failed, GeoMet.parseFeature(null))
        assertEquals(GeoMet.Cell.Value(11.45, "10 15"),
            GeoMet.parseFeature("""{"features":[{"properties":{"value":11.45,"class":"10 15"}}]}"""))
    }
}
