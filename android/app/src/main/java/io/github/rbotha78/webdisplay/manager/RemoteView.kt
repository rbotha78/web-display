package io.github.rbotha78.webdisplay.manager

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime

sealed interface RemoteScene {
    val title: String
    data class Website(override val title: String, val url: String) : RemoteScene
    data class Dashboard(
        override val title: String, val layout: String, val text: String, val showWeather: Boolean,
    ) : RemoteScene
    data class Train(override val title: String, val stationCrs: String) : RemoteScene
    data class Weather(override val title: String) : RemoteScene
    data class Video(
        override val title: String, val source: String, val audioEnabled: Boolean?,
        val duration: Double?, val ratingKey: String,
    ) : RemoteScene
}

data class RemoteSnapshot(
    val mode: String,
    val updated: Double,
    val clock: DeviceClock,
    val scene: RemoteScene?,
    val train: RemoteTrain?,
    val weather: RemoteWeather?,
    val sleeping: Boolean,
    val scheduledSleep: Boolean,
    val wakeUntil: Double?,
    val message: String,
)

data class DeviceClock(
    val instant: Instant,
    val zone: ZoneId,
    val timezone: String,
    val receivedElapsed: Long,
) {
    fun at(elapsed: Long): ZonedDateTime =
        instant.plusMillis((elapsed - receivedElapsed).coerceAtLeast(0)).atZone(zone)
}

data class RemoteTrain(
    val station: String,
    val rows: List<RemoteDeparture>,
    val platformAvailable: Boolean,
    val servicesAvailable: Boolean?,
    val stale: Boolean,
    val error: String,
    val alerts: List<String>,
    val detailsAvailable: Boolean?,
    val detailsError: String,
    val previousStops: List<List<RemoteStop>>,
    val callingAt: List<List<RemoteStop>>,
    val lastSeen: String,
    val updatedAt: Double?,
)

data class RemoteDeparture(
    val scheduled: String, val estimated: String, val destination: String, val platform: String,
    val operator: String, val cancelled: Boolean, val status: String, val state: String,
    val bus: Boolean, val alerts: List<String>,
)

data class RemoteStop(
    val station: String, val scheduled: String, val estimated: String, val actual: String,
    val cancelled: Boolean,
) {
    fun description(): String {
        val time = when {
            cancelled -> "Cancelled"
            actual.isNotBlank() -> "actual $actual"
            else -> scheduled
        }
        val expected = if (!cancelled && actual.isBlank() && estimated.isNotBlank() && estimated != "On time") {
            "; expected $estimated"
        } else ""
        return "$station ($time$expected)"
    }
}

data class RemoteWeather(
    val location: String, val temperature: Double?, val feelsLike: Double?, val description: String,
    val humidity: Double?, val windKmh: Double?, val observedAt: Double?,
    val forecast: List<RemoteForecast>, val forecastError: String, val error: String, val stale: Boolean,
)

data class RemoteForecast(
    val date: String, val low: Double?, val high: Double?, val description: String,
)

data class RemoteViewState(
    val deviceId: String,
    val name: String,
    val snapshot: RemoteSnapshot? = null,
    val error: String? = null,
    val refreshing: Boolean = false,
    val paused: Boolean = true,
    val awaitingSnapshot: Boolean = true,
) {
    val stale: Boolean get() = snapshot != null && (error != null || paused || awaitingSnapshot)
    fun failed(message: String) = copy(error = message, refreshing = false)
    fun received(value: RemoteSnapshot) =
        copy(snapshot = value, error = null, refreshing = false, awaitingSnapshot = false)
}

object RemoteViewParser {
    fun parse(body: JSONObject, elapsed: Long): RemoteSnapshot {
        try {
            if (body.opt("available") != true) {
                throw DeviceRequestException(body.text("message").ifBlank { "Remote view is unavailable on the device." })
            }
            val mode = body.getString("mode")
            require(mode in setOf("carousel", "single", "recovery"))
            val time = OffsetDateTime.parse(body.getString("device_time"))
            val timezone = body.getString("timezone")
            // An unknown IANA name must not silently switch to the phone's timezone.
            val zone = runCatching { ZoneId.of(timezone) }.getOrElse { time.offset }
            val scene = body.optJSONObject("scene")?.let { value ->
                val title = value.text("title")
                when (value.getString("kind")) {
                    "web" -> RemoteScene.Website(title, value.text("url"))
                    "dashboard" -> RemoteScene.Dashboard(
                        title, value.text("layout"), value.text("text"), value.optBoolean("show_weather"),
                    )
                    "train" -> RemoteScene.Train(title, value.text("station_crs"))
                    "weather" -> RemoteScene.Weather(title)
                    "video" -> RemoteScene.Video(
                        title, value.text("source"), value.boolean("audio_enabled"),
                        value.number("duration"), value.text("plex_rating_key"),
                    )
                    else -> throw DeviceRequestException("This scene type needs a newer manager.")
                }
            }
            val sleep = body.getJSONObject("sleep")
            require(sleep.opt("sleeping") is Boolean && sleep.opt("scheduled_sleep") is Boolean)
            return RemoteSnapshot(
                mode, body.number("updated") ?: throw IllegalArgumentException(),
                DeviceClock(time.toInstant(), zone, timezone, elapsed), scene,
                body.optJSONObject("train")?.let(::train),
                body.optJSONObject("weather")?.let(::weather),
                sleep.getBoolean("sleeping"), sleep.getBoolean("scheduled_sleep"),
                sleep.number("wake_until"), body.text("message"),
            )
        } catch (error: DeviceRequestException) {
            throw error
        } catch (_: Exception) {
            throw DeviceRequestException("The device returned an invalid remote view.")
        }
    }

    private fun train(value: JSONObject): RemoteTrain {
        val next = value.optJSONObject("next")
        val lastSeen = next?.optJSONObject("last_seen")
        return RemoteTrain(
            value.text("station"),
            value.optJSONArray("rows").objects().map {
                RemoteDeparture(
                    it.text("scheduled"), it.text("estimated"), it.text("destination"), it.text("platform"),
                    it.text("operator"), it.optBoolean("cancelled"), it.text("status"), it.text("state"),
                    it.text("service_type") == "bus", it.optJSONArray("alerts").texts(),
                )
            },
            value.optBoolean("platform_available"), value.boolean("services_available"),
            value.optBoolean("stale"), value.text("error"), value.optJSONArray("alerts").texts(),
            value.boolean("details_available"), value.text("details_error"),
            next?.optJSONArray("previous_stops").stops(), next?.optJSONArray("calling_at").stops(),
            lastSeen?.let { "${it.text("station")} at ${it.text("actual")}" }.orEmpty(),
            value.number("updated_at"),
        )
    }

    private fun weather(value: JSONObject) = RemoteWeather(
        value.text("location"), value.number("temperature"), value.number("feels_like"),
        value.text("description"), value.number("humidity"), value.number("wind_kmh"),
        value.number("observed_at"),
        value.optJSONArray("forecast").objects().map {
            RemoteForecast(it.text("date"), it.number("low"), it.number("high"), it.text("description"))
        },
        value.text("forecast_error"), value.text("error"), value.optBoolean("stale"),
    )

    private fun JSONArray?.stops(): List<List<RemoteStop>> =
        (0 until (this?.length() ?: 0)).mapNotNull { index -> this?.optJSONArray(index) }.map { group ->
            group.objects().map {
                RemoteStop(it.text("station"), it.text("scheduled"), it.text("estimated"),
                    it.text("actual"), it.optBoolean("cancelled"))
            }
        }

    private fun JSONArray?.objects(): List<JSONObject> =
        (0 until (this?.length() ?: 0)).mapNotNull { index -> this?.optJSONObject(index) }

    private fun JSONArray?.texts(): List<String> =
        (0 until (this?.length() ?: 0)).mapNotNull { index -> this?.opt(index) as? String }

    private fun JSONObject.text(key: String) = opt(key) as? String ?: ""
    private fun JSONObject.boolean(key: String) = opt(key) as? Boolean
    private fun JSONObject.number(key: String) = (opt(key) as? Number)?.toDouble()?.takeIf { it.isFinite() }
}
