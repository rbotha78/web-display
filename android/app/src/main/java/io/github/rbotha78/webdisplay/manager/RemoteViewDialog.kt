package io.github.rbotha78.webdisplay.manager

import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

@Composable
fun RemoteViewDialog(
    state: RemoteViewState,
    player: PlayerState,
    onCommand: (PlayerCommand) -> Unit,
    onReenroll: () -> Unit,
    onClose: () -> Unit,
) {
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
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 6.dp)
                .widthIn(max = 1100.dp).fillMaxWidth().fillMaxHeight(),
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            "${state.name} · Remote view",
                            style = MaterialTheme.typography.titleLarge,
                        )
                    }
                    PlaybackSyncIndicator(state.refreshing || player.refreshing, player.working)
                    TextButton(onClick = onClose) { Text("Close") }
                }
                BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
                    if (maxWidth >= 760.dp) {
                        Row(
                            Modifier.fillMaxSize(),
                            horizontalArrangement = Arrangement.spacedBy(16.dp),
                        ) {
                            Column(
                                Modifier.weight(3f).verticalScroll(rememberScrollState()),
                                verticalArrangement = Arrangement.spacedBy(12.dp),
                            ) {
                                PracticalPreview(state, snapshot, player, elapsed, onCommand, onReenroll)
                            }
                            Column(
                                Modifier.weight(2f).verticalScroll(rememberScrollState()),
                                verticalArrangement = Arrangement.spacedBy(12.dp),
                            ) {
                                RemoteDetails(state, snapshot, elapsed)
                            }
                        }
                    } else {
                        Column(
                            Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            PracticalPreview(state, snapshot, player, elapsed, onCommand, onReenroll)
                            RemoteDetails(state, snapshot, elapsed)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PracticalPreview(
    state: RemoteViewState,
    snapshot: RemoteSnapshot?,
    player: PlayerState,
    elapsed: Long,
    onCommand: (PlayerCommand) -> Unit,
    onReenroll: () -> Unit,
) {
    if (snapshot == null) {
        RemoteSection("Current display") {
            Text(if (state.error == null) "Loading device state…" else "No device state is available.")
        }
    } else {
        DisplayFrame(snapshot, state, elapsed)
        RemotePlaybackControls(state, snapshot, player, onCommand, onReenroll)
    }
}

@Composable
private fun DisplayFrame(snapshot: RemoteSnapshot, state: RemoteViewState, elapsed: Long) {
    Card(
        Modifier.fillMaxWidth().aspectRatio(4f / 3f),
        colors = CardDefaults.cardColors(containerColor = Color.Black),
        shape = RoundedCornerShape(14.dp),
    ) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(if (state.stale) Color(0xFF8A4B08) else Color(0xFF087A55))
                    .padding(horizontal = 12.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    snapshot.scene?.title?.ifBlank { "Web Display" } ?: "Web Display",
                    color = Color.White,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (snapshot.mode == "carousel") {
                    Text(
                        if (snapshot.carouselPaused) "Paused" else "Carousel",
                        color = Color.White,
                        style = MaterialTheme.typography.labelSmall,
                    )
                    Spacer(Modifier.width(8.dp))
                }
                Text(
                    snapshot.clock.at(elapsed).format(CLOCK_FORMAT),
                    color = Color.White,
                    fontWeight = FontWeight.Bold,
                )
            }
            if (state.stale) {
                Text(
                    if (state.error != null) "STALE · ${state.error}" else "STALE · Reconnecting",
                    color = Color.White,
                    style = MaterialTheme.typography.labelSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.fillMaxWidth().background(Color(0xFF6B3D04)).padding(6.dp),
                )
            }
            Box(Modifier.weight(1f).fillMaxWidth().padding(12.dp)) {
                when {
                    snapshot.sleeping -> DisplayMessage(
                        "Display sleeping",
                        if (snapshot.scheduledSleep) "Scheduled sleep is active." else "The physical display is asleep.",
                    )
                    snapshot.mode == "recovery" -> DisplayMessage(
                        "Recovery screen",
                        snapshot.message.ifBlank { "The display is waiting for setup or recovery." },
                    )
                    snapshot.scene == null -> DisplayMessage(
                        "No active scene",
                        snapshot.message.ifBlank { "No current scene was reported by the device." },
                    )
                    else -> DisplayScene(snapshot.scene, snapshot, elapsed)
                }
            }
        }
    }
}

@Composable
private fun DisplayMessage(title: String, detail: String) {
    Column(
        Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(title, color = Color.White, style = MaterialTheme.typography.headlineSmall)
        Text(
            detail,
            color = Color(0xFFB9C2D0),
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 8.dp),
        )
    }
}

@Composable
private fun DisplayScene(scene: RemoteScene, snapshot: RemoteSnapshot, elapsed: Long) {
    when (scene) {
        is RemoteScene.Dashboard -> DashboardPreview(scene, snapshot, elapsed)
        is RemoteScene.Weather -> WeatherPreview(snapshot.weather)
        is RemoteScene.Train -> TrainPreview(snapshot.train, snapshot.weather)
        is RemoteScene.Music -> MusicPreview(scene)
        is RemoteScene.Website -> GenericScenePreview(
            "WEB",
            scene.title.ifBlank { "Website" },
            scene.url.ifBlank { "Private or unavailable URL" },
        )
        is RemoteScene.Video -> GenericScenePreview(
            "VIDEO",
            scene.title.ifBlank { "Video" },
            buildString {
                append(scene.source.ifBlank { "Video scene" })
                scene.duration?.let { append(" · ${number(it)} sec") }
                scene.audioEnabled?.let { append(if (it) " · audio enabled" else " · audio muted") }
            },
        )
    }
}

@Composable
private fun DashboardPreview(
    scene: RemoteScene.Dashboard,
    snapshot: RemoteSnapshot,
    elapsed: Long,
) {
    Column(
        Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.SpaceBetween,
    ) {
        if (scene.layout != "notice") {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    snapshot.clock.at(elapsed).format(SHORT_CLOCK_FORMAT),
                    color = Color.White,
                    style = MaterialTheme.typography.displayMedium,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    snapshot.clock.at(elapsed).format(DATE_FORMAT),
                    color = Color(0xFF84D7FF),
                )
            }
        }
        if (scene.layout != "clock") {
            Text(
                scene.text.ifBlank { "No notice text." },
                color = Color.White,
                style = MaterialTheme.typography.titleMedium,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            )
        }
        if (scene.showWeather && snapshot.weather != null) {
            CompactWeather(snapshot.weather, dark = true)
        }
    }
}

@Composable
private fun WeatherPreview(weather: RemoteWeather?) {
    if (weather == null) {
        DisplayMessage("Weather", "No weather data was supplied by the device.")
        return
    }
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.SpaceBetween) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    weather.location.ifBlank { "Weather" },
                    color = Color.White,
                    style = MaterialTheme.typography.headlineSmall,
                )
                Text(
                    weather.temperature?.let { "${it.roundToInt()}°" } ?: "--°",
                    color = Color.White,
                    style = MaterialTheme.typography.displayLarge,
                    fontWeight = FontWeight.Bold,
                )
                Text(weather.description, color = Color(0xFF84D7FF))
            }
            WeatherGlyph(weather.icon, Modifier.size(84.dp), Color.Black)
        }
        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                buildList {
                    weather.feelsLike?.let { add("Feels like ${it.roundToInt()}°") }
                    weather.humidity?.let { add("Humidity ${number(it)}%") }
                    weather.windKmh?.let { add("Wind ${number(it)} km/h") }
                }.joinToString("  ·  "),
                color = Color(0xFFC5CBD3),
            )
            if (weather.stale || weather.error.isNotBlank()) {
                Text(
                    weather.error.ifBlank { "Weather data may be out of date." },
                    color = Color(0xFFFFB4AB),
                )
            }
        }
        if (weather.forecast.isNotEmpty()) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                weather.forecast.take(4).forEach { day ->
                    Surface(
                        Modifier.weight(1f),
                        color = Color(0xFF171B20),
                        shape = RoundedCornerShape(8.dp),
                    ) {
                        Column(
                            Modifier.padding(7.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            WeatherGlyph(day.icon, Modifier.size(30.dp), Color(0xFF171B20))
                            Text(shortForecastDate(day.date), color = Color.White,
                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(
                                "${day.low?.roundToInt() ?: "?"}°–${day.high?.roundToInt() ?: "?"}°",
                                color = Color(0xFF84D7FF),
                                style = MaterialTheme.typography.labelMedium,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun CompactWeather(weather: RemoteWeather, dark: Boolean = false) {
    val text = if (dark) Color.White else MaterialTheme.colorScheme.onSurface
    val muted = if (dark) Color(0xFFB9C2D0) else MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
            .background(if (dark) Color(0xFF20252B) else MaterialTheme.colorScheme.surfaceVariant)
            .padding(8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        WeatherGlyph(
            weather.icon,
            Modifier.size(44.dp),
            if (dark) Color(0xFF20252B) else MaterialTheme.colorScheme.surfaceVariant,
        )
        Column(Modifier.weight(1f)) {
            Text(weather.location.ifBlank { "Weather" }, color = text, fontWeight = FontWeight.Bold)
            Text(weather.description, color = muted, style = MaterialTheme.typography.bodySmall)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(weather.temperature?.let { "${it.roundToInt()}°" } ?: "--°", color = text)
            Text(
                buildList {
                    weather.humidity?.let { add("H ${number(it)}%") }
                    weather.windKmh?.let { add("W ${number(it)}") }
                }.joinToString(" · "),
                color = muted,
                style = MaterialTheme.typography.labelSmall,
            )
        }
    }
}

@Composable
private fun TrainPreview(board: RemoteTrain?, weather: RemoteWeather?) {
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.SpaceBetween) {
        Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
            Text(
                board?.station?.ifBlank { "Departures" } ?: "Departures",
                color = Color.White,
                style = MaterialTheme.typography.headlineSmall,
            )
            if (board == null || board.rows.isEmpty()) {
                Text(
                    if (board?.error?.isNotBlank() == true) board.error else "No departures available.",
                    color = Color(0xFFC5CBD3),
                )
            } else {
                board.rows.take(3).forEachIndexed { index, row ->
                    Row(
                        Modifier.fillMaxWidth()
                            .background(if (index % 2 == 0) Color(0xFF121820) else Color(0xFF1A222C))
                            .padding(horizontal = 8.dp, vertical = 7.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            row.scheduled,
                            color = Color.White,
                            fontWeight = FontWeight.Bold,
                            style = MaterialTheme.typography.bodySmall,
                            maxLines = 1,
                            modifier = Modifier.width(56.dp),
                        )
                        Text(
                            row.destination.ifBlank { "Destination unavailable" },
                            color = Color.White,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f),
                        )
                        if (board.platformAvailable) {
                            Text(row.platform.ifBlank { "TBC" }, color = Color(0xFF84D7FF),
                                modifier = Modifier.width(32.dp), textAlign = TextAlign.End)
                        }
                        Spacer(Modifier.width(8.dp))
                        Text(
                            departureStatus(row),
                            color = when {
                                row.cancelled -> Color(0xFFFF8A80)
                                row.state == "delayed" -> Color(0xFFFFC86B)
                                else -> Color(0xFF7CE0A6)
                            },
                            maxLines = 2,
                            style = MaterialTheme.typography.labelSmall,
                            textAlign = TextAlign.End,
                            modifier = Modifier.widthIn(max = 105.dp),
                        )
                    }
                }
            }
        }
        if (weather != null) CompactWeather(weather, dark = true)
    }
}

@Composable
private fun MusicPreview(scene: RemoteScene.Music) {
    Column(
        Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            when (scene.state) {
                "paused" -> "NOW PLAYING · PAUSED"
                "playing" -> "NOW PLAYING"
                else -> "MUSIC"
            },
            color = Color(0xFF84D7FF),
            style = MaterialTheme.typography.labelLarge,
        )
        Text(
            scene.song.ifBlank { "Nothing playing" },
            color = Color.White,
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 12.dp),
        )
        if (scene.artist.isNotBlank()) {
            Text(scene.artist, color = Color(0xFFC5CBD3), textAlign = TextAlign.Center)
        }
        if (scene.album.isNotBlank()) {
            Text(scene.album, color = Color(0xFF8E98A6), style = MaterialTheme.typography.bodySmall)
        }
        if (scene.nextSong.isNotBlank()) {
            HorizontalDivider(Modifier.fillMaxWidth(0.7f).padding(vertical = 18.dp), color = Color(0xFF39414A))
            Text(
                "NEXT · ${scene.nextSong}" +
                    if (scene.nextArtist.isNotBlank()) " — ${scene.nextArtist}" else "",
                color = Color(0xFF84D7FF),
                textAlign = TextAlign.Center,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun GenericScenePreview(kind: String, title: String, detail: String) {
    Column(
        Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(kind, color = Color(0xFF84D7FF), fontWeight = FontWeight.Bold)
        Text(
            title,
            color = Color.White,
            style = MaterialTheme.typography.headlineSmall,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(vertical = 10.dp),
        )
        Text(
            detail,
            color = Color(0xFFC5CBD3),
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun RemotePlaybackControls(
    state: RemoteViewState,
    snapshot: RemoteSnapshot,
    player: PlayerState,
    onCommand: (PlayerCommand) -> Unit,
    onReenroll: () -> Unit,
) {
    val baseReady = !state.stale && !snapshot.sleeping && !player.working && !player.trustRequired
    val carousel = player.carousel.value
    val carouselReady = baseReady &&
        snapshot.mode == "carousel" &&
        player.carousel.available &&
        carousel?.let { it.enabled && it.available } == true
    val music = player.music.value
    val musicReady = baseReady && player.music.available
    val primary = music?.let(SettingsRules::primaryMusicAction) ?: "play"

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Text("Display controls", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text("Carousel", style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                val paused = carousel?.paused ?: snapshot.carouselPaused
                OutlinedButton(
                    onClick = {
                        onCommand(
                            PlayerCommand.Carousel(
                                if (paused) CarouselAction.RESUME else CarouselAction.PAUSE,
                            ),
                        )
                    },
                    enabled = carouselReady,
                ) { Text(if (paused) "Resume" else "Pause") }
                OutlinedButton(
                    onClick = { onCommand(PlayerCommand.Carousel(CarouselAction.NEXT)) },
                    enabled = carouselReady,
                ) { Text("Next scene") }
            }
            if (player.carousel.error != null) {
                Text(player.carousel.error, color = MaterialTheme.colorScheme.error)
            } else if (snapshot.mode != "carousel") {
                Text("Scene controls are available in carousel mode.",
                    style = MaterialTheme.typography.bodySmall)
            }
            HorizontalDivider()
            Text("Background music", style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedButton(
                    onClick = { onCommand(PlayerCommand.Music(primary)) },
                    enabled = musicReady && !music?.stationKey.isNullOrBlank(),
                ) { Text(primary.replaceFirstChar { it.uppercase() }) }
                OutlinedButton(
                    onClick = { onCommand(PlayerCommand.Music("skip")) },
                    enabled = musicReady && music?.enabled == true && music.status.source != "radio",
                ) { Text("Skip") }
                OutlinedButton(
                    onClick = { onCommand(PlayerCommand.Music("stop")) },
                    enabled = musicReady && music?.enabled == true,
                ) { Text("Stop") }
            }
            player.music.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            player.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            player.commandError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            if (player.trustRequired) {
                TextButton(onClick = onReenroll) { Text("Trust / enrol again") }
            } else if (player.working) {
                Text("Sending command and refreshing device status…",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun RemoteDetails(state: RemoteViewState, snapshot: RemoteSnapshot?, elapsed: Long) {
    state.error?.let { Warning(it) }
    if (state.stale && snapshot != null) {
        Warning("Stale — showing the last received device state.")
    }
    if (state.paused) {
        Text("Paused while the app is in the background.")
    }
    if (snapshot == null) return

    if (snapshot.message.isNotBlank() || snapshot.wakeUntil != null) {
        RemoteSection("Display notice") {
        if (snapshot.message.isNotBlank()) Text(snapshot.message)
        snapshot.wakeUntil?.let {
            Text("Wake override until: ${epochTime(it, snapshot.clock)}")
        }
        }
    }
    if (!snapshot.sleeping) {
        when (val scene = snapshot.scene) {
            is RemoteScene.Website -> RemoteSection("Website details") {
                Text(scene.url.ifBlank { "No website address was reported." })
                Text(
                    "This website is not loaded by the manager. Private URL details may be omitted.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            is RemoteScene.Video -> RemoteSection("Video details") {
                Text("Source: ${scene.source.ifBlank { "Not reported" }}")
                Text("Audio: ${scene.audioEnabled?.let { if (it) "Enabled" else "Muted" } ?: "Not reported"}")
                scene.duration?.let { Text("Scene duration: ${number(it)} seconds") }
                if (scene.ratingKey.isNotBlank()) Text("Plex rating key: ${scene.ratingKey}")
                Text("Video and audio are not streamed or played here.",
                    style = MaterialTheme.typography.bodySmall)
            }
            is RemoteScene.Train -> RemoteSection("Departure details") {
                TrainContent(snapshot.train, snapshot.clock)
            }
            is RemoteScene.Weather -> RemoteSection("Weather details") {
                WeatherContent(snapshot.weather, snapshot.clock)
            }
            is RemoteScene.Dashboard -> RemoteSection("Dashboard details") {
                if (scene.layout != "notice") Text(snapshot.clock.at(elapsed).format(CLOCK_FORMAT))
                if (scene.layout != "clock") Text(scene.text.ifBlank { "No notice text." })
                if (scene.showWeather) WeatherContent(snapshot.weather, snapshot.clock)
            }
            is RemoteScene.Music -> RemoteSection("Music details") {
                Text(if (scene.state == "paused") "Now playing · paused" else "Now playing",
                    fontWeight = FontWeight.Bold)
                Text(scene.song.ifBlank { "No music is playing." })
                if (scene.artist.isNotBlank()) Text(scene.artist)
                if (scene.album.isNotBlank()) Text(scene.album)
                if (scene.nextSong.isNotBlank()) {
                    Text("Up next: ${scene.nextSong}" +
                        if (scene.nextArtist.isNotBlank()) " — ${scene.nextArtist}" else "")
                }
                Text("Album covers and audio are not shown in this preview.",
                    style = MaterialTheme.typography.bodySmall)
            }
            null -> Unit
        }
    }
}

@Composable
private fun WeatherGlyph(icon: String, modifier: Modifier, background: Color) {
    Canvas(modifier) {
        val unit = size.minDimension / 24f
        val code = icon.take(2)
        val night = icon.endsWith("n")
        when (code) {
            "01" -> if (night) {
                drawCircle(Color(0xFFB9D9FF), unit * 6f, Offset(unit * 11f, unit * 12f))
                drawCircle(background, unit * 6f, Offset(unit * 14f, unit * 10f))
            } else {
                for (index in 0 until 8) {
                    val angle = index * Math.PI.toFloat() / 4f
                    line(
                        Offset(unit * (12 + cos(angle) * 7), unit * (12 + sin(angle) * 7)),
                        Offset(unit * (12 + cos(angle) * 10), unit * (12 + sin(angle) * 10)),
                        Color(0xFFFFD54F), unit * 1.2f,
                    )
                }
                drawCircle(Color(0xFFFFC107), unit * 5f)
                drawCircle(Color(0xFFFFEB3B), unit * 3.8f)
            }
            "02" -> {
                if (night) {
                    drawCircle(Color(0xFFB9D9FF), unit * 4f, Offset(unit * 7f, unit * 7f))
                    drawCircle(background, unit * 4f, Offset(unit * 9f, unit * 5f))
                } else {
                    drawCircle(Color(0xFFFFD54F), unit * 4f, Offset(unit * 7f, unit * 7f))
                }
                cloud(unit, Color(0xFFD7DEE6))
            }
            "03" -> cloud(unit, Color(0xFFD7DEE6))
            "04" -> {
                cloud(unit, Color(0xFF87929E), Offset(unit * -1f, unit * -2f))
                cloud(unit, Color(0xFFBCC4CD), Offset(unit * 2f, unit * 2f))
            }
            "09", "10" -> {
                cloud(unit, Color(0xFF9CA8B4), Offset(unit * 0f, unit * -1f))
                for (index in 0 until 3) {
                    line(
                        Offset(unit * (6 + index * 6), unit * 16),
                        Offset(unit * (4 + index * 6), unit * 22),
                        if (index == 1) Color(0xFF5DDCFF) else Color(0xFF35BFF0),
                        unit * 1.4f,
                    )
                }
            }
            "11" -> {
                cloud(unit, Color(0xFF697681), Offset(unit * 0f, unit * -2f))
                drawPath(Path().apply {
                    moveTo(unit * 13, unit * 11)
                    lineTo(unit * 8, unit * 23)
                    lineTo(unit * 13, unit * 18)
                    lineTo(unit * 12, unit * 23)
                    lineTo(unit * 18, unit * 14)
                    close()
                }, Color(0xFFFFD54F))
            }
            "13" -> {
                drawCircle(Color.White, unit * 1.8f, Offset(unit * 12f, unit * 12f))
                for (index in 0 until 6) {
                    val angle = index * Math.PI.toFloat() / 3f
                    line(
                        Offset(unit * 12, unit * 12),
                        Offset(unit * (12 + cos(angle) * 10), unit * (12 + sin(angle) * 10)),
                        Color.White, unit * 1.1f,
                    )
                }
            }
            "50" -> {
                line(Offset(unit * 2, unit * 6), Offset(unit * 22, unit * 6), Color(0xFFB9C2CD), unit * 1.4f)
                line(Offset(unit * 5, unit * 12), Offset(unit * 23, unit * 12), Color(0xFFD7DEE6), unit * 1.4f)
                line(Offset(unit * 1, unit * 18), Offset(unit * 20, unit * 18), Color(0xFF929EAA), unit * 1.4f)
            }
            else -> {
                cloud(unit, Color(0xFFD7DEE6))
                drawCircle(Color(0xFF87929E), unit * 0.9f, Offset(unit * 12f, unit * 13f))
                line(Offset(unit * 12, unit * 15), Offset(unit * 12, unit * 17), Color(0xFF87929E), unit)
            }
        }
    }
}

private fun DrawScope.line(start: Offset, end: Offset, color: Color, width: Float) {
    drawLine(color, start, end, strokeWidth = width, cap = StrokeCap.Round)
}

private fun DrawScope.cloud(
    unit: Float,
    color: Color,
    offset: Offset = Offset.Zero,
) {
    drawRoundRect(
        color,
        Offset(offset.x + unit * 3, offset.y + unit * 10),
        Size(unit * 18, unit * 8),
        androidx.compose.ui.geometry.CornerRadius(unit * 4),
    )
    drawCircle(color, unit * 5, Offset(offset.x + unit * 8, offset.y + unit * 10))
    drawCircle(color, unit * 6, Offset(offset.x + unit * 15, offset.y + unit * 9))
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
            Text(departureStatus(row), fontWeight = FontWeight.Bold)
            if (row.bus) Text("Rail replacement bus")
            if (board.platformAvailable) {
                Text(if (row.bus) "Bus stop" else "Platform: ${row.platform.ifBlank { "TBC" }}")
            }
            if (row.operator.isNotBlank()) Text(row.operator)
            row.alerts.forEach { Text("Service notice: $it") }
            if (index == 0) {
                if (!row.cancelled && row.state != "cancelled") {
                    val current = board.nextStation?.let {
                        it.copy(
                            scheduled = it.scheduled.ifBlank { row.scheduled },
                            estimated = it.estimated.ifBlank { row.estimated },
                        )
                    }
                    val stops = board.previousStops.flatten() +
                        listOfNotNull(current) + board.callingAt.flatten()
                    stops.forEach {
                        StationTime(it.station, board.station, it.compactDescription(),
                            board.lastSeenStop?.station.orEmpty())
                    }
                } else {
                    board.lastSeenStop?.let {
                        StationTime(it.station, board.station, it.compactDescription(), it.station)
                    }
                }
                board.lastSeenStop?.let {
                    Text("Last seen: ${it.station} at ${it.actual}",
                        color = MaterialTheme.colorScheme.primary)
                }
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

private fun departureStatus(row: RemoteDeparture): String = when {
    row.cancelled || row.state == "cancelled" -> "Cancelled"
    row.status.isNotBlank() -> row.status
    row.estimated.isNotBlank() -> row.estimated
    else -> "Status unavailable"
}

@Composable
private fun StationTime(
    station: String,
    currentStation: String,
    text: String,
    lastSeenStation: String = "",
) {
    val current = currentStation.isNotBlank() &&
        station.trim().equals(currentStation.trim(), ignoreCase = true)
    val observed = lastSeenStation.isNotBlank() &&
        station.trim().equals(lastSeenStation.trim(), ignoreCase = true)
    if (current || observed) {
        Surface(
            color = if (current) {
                MaterialTheme.colorScheme.primaryContainer
            } else {
                MaterialTheme.colorScheme.secondaryContainer
            },
            contentColor = if (current) {
                MaterialTheme.colorScheme.onPrimaryContainer
            } else {
                MaterialTheme.colorScheme.onSecondaryContainer
            },
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
private fun WeatherContent(weather: RemoteWeather?, clock: DeviceClock) {
    Text(weather?.location?.ifBlank { "Weather" } ?: "Weather",
        style = MaterialTheme.typography.titleLarge)
    if (weather == null) {
        Text("No weather data was supplied by the device.")
        return
    }
    if (weather.stale) Warning("Weather data may be out of date.")
    if (weather.error.isNotBlank()) Warning(weather.error)
    weather.temperature?.let { Text("${it.roundToInt()} °C",
        style = MaterialTheme.typography.headlineLarge) }
        ?: Text("Temperature unavailable.")
    if (weather.description.isNotBlank()) Text(weather.description)
    weather.feelsLike?.let { Text("Feels like ${it.roundToInt()} °C") }
    weather.humidity?.let { Text("Humidity ${number(it)}%") }
    weather.windKmh?.let { Text("Wind ${number(it)} km/h") }
    weather.forecast.forEach { day ->
        RemoteSection(day.date) {
            Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                WeatherGlyph(day.icon, Modifier.size(34.dp), MaterialTheme.colorScheme.surface)
                Column {
                    Text("${day.low?.roundToInt() ?: "?"}–${day.high?.roundToInt() ?: "?"} °C")
                    Text(day.description)
                }
            }
        }
    }
    if (weather.forecastError.isNotBlank()) Warning(weather.forecastError)
    Text("OpenWeather · Forecast ranges from 3-hour samples",
        style = MaterialTheme.typography.bodySmall)
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

private val SHORT_CLOCK_FORMAT = DateTimeFormatter.ofPattern("HH:mm", Locale.ROOT)
private val CLOCK_FORMAT = DateTimeFormatter.ofPattern("HH:mm:ss", Locale.ROOT)
private val DATE_FORMAT get() = DateTimeFormatter.ofPattern("EEEE, d MMMM yyyy", Locale.getDefault())
private val UPDATED_FORMAT get() = DateTimeFormatter.ofPattern("d MMM HH:mm:ss xxx", Locale.getDefault())

private fun epochTime(epoch: Double, clock: DeviceClock): String = runCatching {
    Instant.ofEpochMilli((epoch * 1_000).toLong()).atZone(clock.zone).format(UPDATED_FORMAT)
}.getOrDefault("Unavailable")

private fun number(value: Double): String =
    if (value % 1.0 == 0.0) value.toLong().toString()
    else String.format(Locale.getDefault(), "%.1f", value)

private fun shortForecastDate(value: String): String = value
    .takeLast(5)
    .replace('-', '/')
