package io.github.rbotha78.webdisplay.manager

import android.os.SystemClock
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

@Composable
fun RemoteViewDialog(state: RemoteViewState, onClose: () -> Unit) {
    val snapshot = state.snapshot
    var elapsed by remember(snapshot) { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(snapshot, state.paused) {
        while (!state.paused && isActive) {
            elapsed = SystemClock.elapsedRealtime()
            delay(1_000)
        }
    }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(
            modifier = Modifier.padding(12.dp).widthIn(max = 900.dp).fillMaxWidth().fillMaxHeight(0.95f),
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row {
                    Text(
                        "${state.name} · Remote view",
                        style = MaterialTheme.typography.titleLarge,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = onClose) { Text("Close") }
                }
                Text("Reconstructed view, not a screenshot. Websites and video are details only.")
                if (state.refreshing) LinearProgressIndicator(Modifier.fillMaxWidth())
                Column(
                    Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    if (state.paused) Text("Paused while the app is in the background.")
                    state.error?.let { Warning(it) }
                    if (state.stale) Warning("Stale — showing the last received device state.")
                    if (snapshot == null) {
                        Text(if (state.error == null) "Loading device state…" else "No device state is available.")
                    } else {
                        Text("Mode: ${snapshot.mode}")
                        Text(
                            "Device time: ${snapshot.clock.at(elapsed).format(CLOCK_FORMAT)}" +
                                "\n${snapshot.clock.at(elapsed).format(DATE_FORMAT)}" +
                                "\nTimezone: ${snapshot.clock.timezone} (${snapshot.clock.zone.id})",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Text("State updated: ${epochTime(snapshot.updated, snapshot.clock)}",
                            style = MaterialTheme.typography.bodySmall)
                        if (snapshot.message.isNotBlank()) Text(snapshot.message)
                        if (snapshot.sleeping) {
                            RemoteSection("Display sleeping") {
                                Text("The physical display is asleep; no scene is currently visible.")
                                Text(if (snapshot.scheduledSleep) "Scheduled sleep is active." else "Not scheduled sleep.")
                            }
                        } else {
                            if (snapshot.scheduledSleep) {
                                Text("Scheduled sleep is active, but the display is temporarily awake.")
                            }
                            snapshot.wakeUntil?.let {
                                Text("Wake override until: ${epochTime(it, snapshot.clock)}")
                            }
                            if (snapshot.mode == "recovery") {
                                RemoteSection("Recovery") {
                                    Text("The device is showing its recovery/setup screen.")
                                }
                            }
                            snapshot.scene?.let { RemoteSceneContent(it, snapshot, elapsed) }
                                ?: Text("No current scene was reported by the device.")
                        }
                    }
                    Text("Refreshes every 5 seconds while this view is open and the app is foregrounded.",
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun RemoteSceneContent(scene: RemoteScene, snapshot: RemoteSnapshot, elapsed: Long) {
    RemoteSection(scene.title.ifBlank { "Current scene" }) {
        when (scene) {
            is RemoteScene.Website -> {
                Text("Website · URL/details only", fontWeight = FontWeight.Bold)
                Text(scene.url.ifBlank { "No website address was reported." })
                Text("This website is not loaded by the manager. Private URL details may be omitted.")
            }
            is RemoteScene.Video -> {
                Text("Video · details only", fontWeight = FontWeight.Bold)
                Text("Source: ${scene.source.ifBlank { "Not reported" }}")
                Text("Audio: ${scene.audioEnabled?.let { if (it) "Enabled" else "Muted" } ?: "Not reported"}")
                scene.duration?.let { Text("Scene duration: ${number(it)} seconds") }
                if (scene.ratingKey.isNotBlank()) Text("Plex rating key: ${scene.ratingKey}")
                Text("Video and audio are not streamed or played in this preview.")
            }
            is RemoteScene.Dashboard -> {
                if (scene.layout != "notice") {
                    Text(snapshot.clock.at(elapsed).format(CLOCK_FORMAT),
                        style = MaterialTheme.typography.displaySmall)
                    Text(snapshot.clock.at(elapsed).format(DATE_FORMAT))
                }
                if (scene.layout != "clock") Text(scene.text.ifBlank { "No notice text." })
                if (scene.showWeather) WeatherContent(snapshot.weather, snapshot.clock)
            }
            is RemoteScene.Train -> {
                Text("Live departures · ${scene.stationCrs}")
                TrainContent(snapshot.train, snapshot.clock)
                WeatherContent(snapshot.weather, snapshot.clock, currentOnly = true)
            }
            is RemoteScene.Weather -> WeatherContent(snapshot.weather, snapshot.clock)
            is RemoteScene.Music -> {
                Text(if (scene.state == "paused") "Now playing · paused" else "Now playing",
                    fontWeight = FontWeight.Bold)
                if (scene.state in setOf("playing", "paused") && scene.song.isNotBlank()) {
                    Text(scene.song, style = MaterialTheme.typography.titleLarge)
                    if (scene.artist.isNotBlank()) Text(scene.artist)
                    if (scene.album.isNotBlank()) Text(scene.album)
                } else {
                    Text("No music is playing.")
                }
                Text("Album covers and audio are not shown in this preview.")
            }
        }
    }
}

@Composable
private fun TrainContent(board: RemoteTrain?, clock: DeviceClock) {
    if (board == null) {
        Text("No departure data was supplied by the device.")
        return
    }
    if (board.station.isNotBlank()) Text(board.station, style = MaterialTheme.typography.titleLarge)
    if (board.stale) Warning("Departure data may be out of date.")
    if (board.error.isNotBlank()) Warning(board.error)
    if (board.detailsAvailable == false) {
        Warning(board.detailsError.ifBlank { "Detailed status is unavailable; showing the board estimate." })
    }
    if (board.servicesAvailable == false) Text("Services are currently unavailable at this station.")
    if (board.rows.isEmpty() && board.servicesAvailable != false && board.error.isBlank()) {
        Text("No upcoming departures were returned.")
    }
    board.rows.forEachIndexed { index, row ->
        RemoteSection("${row.scheduled} · ${row.destination.ifBlank { "Destination unavailable" }}") {
            val status = when {
                row.cancelled || row.state == "cancelled" -> "Cancelled"
                row.status.isNotBlank() -> row.status
                row.estimated.isNotBlank() -> row.estimated
                else -> "Status unavailable"
            }
            Text(status, fontWeight = FontWeight.Bold)
            if (row.bus) Text("Rail replacement bus")
            if (board.platformAvailable) {
                Text(if (row.bus) "Bus stop" else "Platform: ${row.platform.ifBlank { "TBC" }}")
            }
            if (row.operator.isNotBlank()) Text(row.operator)
            row.alerts.forEach { Text("Service notice: $it") }
            if (index == 0) {
                if (!row.cancelled && row.state != "cancelled") {
                    val current = board.nextStation?.let {
                        it.copy(scheduled = it.scheduled.ifBlank { row.scheduled }, estimated = it.estimated.ifBlank { row.estimated })
                    }
                    val stops = board.previousStops.flatten() + listOfNotNull(current) + board.callingAt.flatten()
                    stops.forEach {
                        StationTime(it.station, board.station, it.compactDescription(), board.lastSeenStop?.station.orEmpty())
                    }
                } else {
                    board.lastSeenStop?.let { StationTime(it.station, board.station, it.compactDescription(), it.station) }
                }
                board.lastSeenStop?.let { Text("Last seen: ${it.station} at ${it.actual}", color = MaterialTheme.colorScheme.primary) }
            }
        }
    }
    if (board.alerts.isNotEmpty()) {
        Text("Station messages", fontWeight = FontWeight.Bold)
        board.alerts.forEach { Text(it) }
    }
    board.updatedAt?.let { Text("Live data checked: ${epochTime(it, clock)}") }
    if (board.recent != null || board.recentError.isNotBlank()) {
        RemoteSection(board.recent?.heading ?: "Most recent departure") {
            if (board.recentError.isNotBlank()) Warning(board.recentError)
            board.recent?.let { recent ->
                if (recent.stale) Warning("Stale data")
                if (recent.detailsError.isNotBlank()) Warning(recent.detailsError)
                (recent.displayStops ?: recent.stops.filter { it.timing != RemoteRailTiming.ARRIVAL })
                    .forEach { StationTime(it.station, board.station, it.compactDescription()) }
            }
        }
    }
}

@Composable
private fun StationTime(station: String, currentStation: String, text: String, lastSeenStation: String = "") {
    val current = currentStation.isNotBlank() && station.trim().equals(currentStation.trim(), ignoreCase = true)
    val observed = lastSeenStation.isNotBlank() && station.trim().equals(lastSeenStation.trim(), ignoreCase = true)
    if (current || observed) {
        Surface(
            color = if (current) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.secondaryContainer,
            contentColor = if (current) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSecondaryContainer,
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(text, fontWeight = FontWeight.Bold, modifier = Modifier.padding(6.dp))
        }
    } else {
        Text(text)
    }
}

@Composable
private fun WeatherContent(weather: RemoteWeather?, clock: DeviceClock, currentOnly: Boolean = false) {
    Text(weather?.location?.ifBlank { "Weather" } ?: "Weather", style = MaterialTheme.typography.titleLarge)
    if (weather == null) {
        Text("No weather data was supplied by the device.")
        return
    }
    if (weather.stale) Warning("Weather data may be out of date.")
    if (weather.error.isNotBlank()) Warning(weather.error)
    weather.temperature?.let { Text("${it.roundToInt()} °C", style = MaterialTheme.typography.headlineLarge) }
        ?: Text("Temperature unavailable.")
    if (weather.description.isNotBlank()) Text(weather.description)
    weather.feelsLike?.let { Text("Feels like ${it.roundToInt()} °C") }
    weather.humidity?.let { Text("Humidity ${number(it)}%") }
    weather.windKmh?.let { Text("Wind ${number(it)} km/h") }
    if (!currentOnly) {
        weather.forecast.forEach { day ->
            RemoteSection(day.date) {
                Text("${day.low?.roundToInt() ?: "?"}–${day.high?.roundToInt() ?: "?"} °C")
                Text(day.description)
            }
        }
        if (weather.forecastError.isNotBlank()) Warning(weather.forecastError)
        Text("OpenWeather · Forecast ranges from 3-hour samples", style = MaterialTheme.typography.bodySmall)
    } else {
        Text("OpenWeather", style = MaterialTheme.typography.bodySmall)
    }
    weather.observedAt?.let { Text("Observed: ${epochTime(it, clock)}") }
}

@Composable
private fun RemoteSection(title: String, content: @Composable () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            content()
        }
    }
}

@Composable
private fun Warning(message: String) {
    Text(message, color = MaterialTheme.colorScheme.error)
}

private val CLOCK_FORMAT = DateTimeFormatter.ofPattern("HH:mm:ss", Locale.ROOT)
private val DATE_FORMAT get() = DateTimeFormatter.ofPattern("EEEE, d MMMM yyyy", Locale.getDefault())
private val UPDATED_FORMAT get() = DateTimeFormatter.ofPattern("d MMM HH:mm:ss xxx", Locale.getDefault())

private fun epochTime(epoch: Double, clock: DeviceClock): String = runCatching {
    Instant.ofEpochMilli((epoch * 1_000).toLong()).atZone(clock.zone).format(UPDATED_FORMAT)
}.getOrDefault("Unavailable")

private fun number(value: Double): String =
    if (value % 1.0 == 0.0) value.toLong().toString() else String.format(Locale.getDefault(), "%.1f", value)
