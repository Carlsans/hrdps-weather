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
    fun alertsMatchPython() {
        assumeTrue("set EXPECTED (python web/tests/make_expected.py expected.json)", expectedPath.isNotEmpty())
        val exp = JSONObject(File(expectedPath).readText(Charsets.UTF_8))
        val place = exp.getJSONObject("place")
        val zone = java.time.ZoneId.of(place.getString("timezone"))
        val f = Forecast(times, doubles(), strings(), place.getDouble("latitude"), place.getDouble("longitude"))
        for ((key, hours) in listOf("alerts12" to 12, "alerts" to 36)) {
            val all = exp.getJSONArray(key)
            for (i in 0 until f.n) {
                val e = all.getJSONArray(i)
                val want = (0 until e.length()).map { e.getJSONArray(it).getString(0) to e.getJSONArray(it).getInt(1) }
                assertEquals("$key($i)", want, f.alerts(i, hours, zone))
            }
        }
    }

    @Test
    fun sparseDetailedForecast() {
        // The large widget downloads more cells (GeoMet.cellsFor(i, detailed = true)): everything it shows must
        // equal what the full run gives.
        val zone = java.time.ZoneId.of("America/Toronto")
        val full = Forecast(times, doubles(), strings(), 46.8139, -71.2080)
        for (i in listOf(0, 1, 7, 20, 26, 40, 48)) {
            val keep = GeoMet.cellsFor(i, detailed = true).toSet()
            val f = Forecast(times, doubles { k, h -> (k to h) in keep }, strings { k, h -> (k to h) in keep }, 46.8139, -71.2080)
            assertEquals("alerts@$i", full.alerts(i, GeoMet.AHEAD, zone), f.alerts(i, GeoMet.AHEAD, zone))
            assertEquals("ws@$i", full.ws[i]!!, f.ws[i]!!, 1e-9)
            assertEquals("gust@$i", full.gust[i]!!, f.gust[i]!!, 1e-9)
            assertEquals("wd@$i", full.wd[i]!!, f.wd[i]!!, 1e-9)
            assertEquals("hr@$i", full.hr[i]!!, f.hr[i]!!, 1e-9)
            for (h in GeoMet.slots(i)) {
                assertEquals("slot icon $h@$i", full.icon(h), f.icon(h))
                assertEquals("slot tt $h@$i", full.tt[h]!!, f.tt[h]!!, 1e-9)
                assertEquals("slot pop $h@$i", full.pop[h], f.pop[h], 1e-9)
                assertEquals("slot mm $h@$i", full.pr[h] + full.pr[h - 1], f.pr[h] + f.pr[h - 1], 1e-9)
            }
        }
    }

    @Test
    fun pythonFormatting() {
        assertEquals("0", Forecast.fx(0.5)); assertEquals("2", Forecast.fx(2.5)); assertEquals("-0", Forecast.fx(-0.3))
        assertEquals("0.2", Forecast.fx(0.25, 1)); assertEquals("0.5", Forecast.fx(0.45, 1)); assertEquals("-2", Forecast.fx(-1.5))
        assertEquals("ONO", Forecast.compass(292.0)); assertEquals("N", Forecast.compass(-5.0)); assertEquals("N", Forecast.compass(355.0))
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
