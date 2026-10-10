package io.github.carlsans.hrdpsweather

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** The place (sent by the web app when the user chooses it) and the widget's last snapshot. */
data class Place(val latitude: Double, val longitude: Double, val location: String, val timezone: String) {
    companion object {
        val DEFAULT = Place(46.8139, -71.2080, "Québec", "America/Toronto")      // same default as the apps

        fun fromJson(s: String): Place? = try {
            val o = JSONObject(s)
            Place(o.getDouble("latitude"), o.getDouble("longitude"), o.optString("location", "Ma position"),
                o.optString("timezone", DEFAULT.timezone))
        } catch (_: Exception) {
            null
        }
    }

    fun toJson(): String = JSONObject().put("latitude", latitude).put("longitude", longitude)
        .put("location", location).put("timezone", timezone).toString()
}

/** One column of the large widget's hourly strip. */
data class Slot(val time: String, val icon: String, val temp: String, val precip: String, val wet: Boolean)

/** What the widgets show; kept so they can be drawn instantly (and offline) after a reboot or a resize.
 *  The `detailed` part (wind, alerts, hourly strip) is only filled when a large widget exists. */
data class Snapshot(
    val icon: String, val temp: String, val describe: String, val feels: String, val next: String, val wet: Boolean,
    val place: String, val ref: String, val hour: String, val updated: Long,
    val detailed: Boolean = false, val wind: String = "", val alerts: String = "", val alertOn: Boolean = false,
    val run: String = "", val slots: List<Slot> = emptyList(),
) {
    fun toJson(): String {
        val hours = JSONArray()
        for (h in slots) hours.put(JSONObject().put("time", h.time).put("icon", h.icon).put("temp", h.temp)
            .put("precip", h.precip).put("wet", h.wet))
        return JSONObject().put("icon", icon).put("temp", temp).put("describe", describe).put("feels", feels)
            .put("next", next).put("wet", wet).put("place", place).put("ref", ref).put("hour", hour).put("updated", updated)
            .put("detailed", detailed).put("wind", wind).put("alerts", alerts).put("alertOn", alertOn).put("run", run)
            .put("slots", hours).toString()
    }

    companion object {
        fun fromJson(s: String?): Snapshot? = try {
            val o = JSONObject(s ?: return null)
            val hours = o.optJSONArray("slots") ?: JSONArray()
            Snapshot(o.getString("icon"), o.getString("temp"), o.getString("describe"), o.getString("feels"), o.getString("next"),
                o.getBoolean("wet"), o.getString("place"), o.getString("ref"), o.getString("hour"), o.getLong("updated"),
                o.optBoolean("detailed"), o.optString("wind"), o.optString("alerts"), o.optBoolean("alertOn"), o.optString("run"),
                (0 until hours.length()).map { k ->
                    val h = hours.getJSONObject(k)
                    Slot(h.getString("time"), h.getString("icon"), h.getString("temp"), h.getString("precip"), h.getBoolean("wet"))
                })
        } catch (_: Exception) {
            null
        }
    }
}

object Prefs {
    private fun sp(c: Context) = c.getSharedPreferences("hrdps-weather", Context.MODE_PRIVATE)

    fun place(c: Context): Place = sp(c).getString("place", null)?.let(Place::fromJson) ?: Place.DEFAULT

    /** True when the place changed (the widget then has to be refreshed). */
    fun setPlace(c: Context, p: Place): Boolean {
        val old = place(c)
        sp(c).edit().putString("place", p.toJson()).apply()
        return old != p
    }

    fun snapshot(c: Context): Snapshot? = Snapshot.fromJson(sp(c).getString("snapshot", null))
    fun setSnapshot(c: Context, s: Snapshot?) = sp(c).edit().putString("snapshot", s?.toJson()).apply()
}
