package io.github.rbotha78.webdisplay.manager

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp

private val SyncImage = ImageVector.Builder(
    name = "Sync",
    defaultWidth = 24.dp,
    defaultHeight = 24.dp,
    viewportWidth = 24f,
    viewportHeight = 24f,
).apply {
    path(
        fill = null,
        stroke = SolidColor(Color.Black),
        strokeLineWidth = 2f,
        strokeLineCap = StrokeCap.Round,
        strokeLineJoin = StrokeJoin.Round,
    ) {
        moveTo(5f, 10f)
        curveTo(5f, 1f, 19f, 1f, 19f, 10f)
        lineTo(16f, 7f)
        moveTo(19f, 10f)
        lineTo(22f, 7f)
        moveTo(19f, 14f)
        curveTo(19f, 23f, 5f, 23f, 5f, 14f)
        lineTo(8f, 17f)
        moveTo(5f, 14f)
        lineTo(2f, 17f)
    }
}.build()

@Composable
internal fun SyncIcon(
    refreshing: Boolean,
    contentDescription: String,
    tint: Color = LocalContentColor.current,
) {
    val rotation = if (refreshing) {
        rememberInfiniteTransition(label = "Sync").animateFloat(
            initialValue = 0f,
            targetValue = 360f,
            animationSpec = infiniteRepeatable(tween(1000, easing = LinearEasing)),
            label = "Sync rotation",
        )
    } else null
    Icon(
        imageVector = SyncImage,
        contentDescription = contentDescription,
        tint = tint,
        modifier = Modifier.size(24.dp).graphicsLayer { rotationZ = rotation?.value ?: 0f },
    )
}

@Composable
internal fun PlaybackSyncIndicator(refreshing: Boolean, working: Boolean) {
    Box(Modifier.size(24.dp), contentAlignment = Alignment.Center) {
        if (refreshing || working) {
            SyncIcon(
                refreshing = true,
                contentDescription = if (working) "Sending playback command" else "Refreshing playback",
                tint = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

@Composable
internal fun PlayerControls(
    player: PlayerState,
    enabled: Boolean,
    onCommand: (PlayerCommand) -> Unit,
    onReenroll: () -> Unit,
) {
    val available = enabled && !player.working && !player.trustRequired
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("Background music", style = MaterialTheme.typography.titleSmall)
        val music = player.music.value
        when {
            player.music.error != null -> Text(player.music.error, color = MaterialTheme.colorScheme.error)
            music == null -> Text("Loading music status…")
            else -> {
                val status = music.status
                Text("Music: " + when {
                    !music.enabled -> "Stopped"
                    status.pausedByUser -> "Paused"
                    else -> status.state.replaceFirstChar { it.uppercase() }
                })
                if (status.title.isNotBlank()) Text(status.title, style = MaterialTheme.typography.bodyMedium)
                if (status.title.isNotBlank() && status.artist.isNotBlank()) Text(status.artist, style = MaterialTheme.typography.bodySmall)
                if (status.error.isNotBlank()) Text(status.error, color = MaterialTheme.colorScheme.error)
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            val primary = music?.let(SettingsRules::primaryMusicAction) ?: "play"
            val musicReady = available && player.music.available
            OutlinedButton(
                onClick = { onCommand(PlayerCommand.Music(primary)) },
                enabled = musicReady && !music?.stationKey.isNullOrBlank(),
            ) { Text(primary.replaceFirstChar { it.uppercase() }) }
            OutlinedButton(
                onClick = { onCommand(PlayerCommand.Music("skip")) },
                enabled = musicReady && music?.enabled == true,
            ) { Text("Skip") }
            OutlinedButton(
                onClick = { onCommand(PlayerCommand.Music("stop")) },
                enabled = musicReady && music?.enabled == true,
            ) { Text("Stop") }
            val track = music?.status
            OutlinedButton(
                onClick = { track?.let { onCommand(PlayerCommand.Like(it.ratingKey)) } },
                enabled = musicReady && track != null && track.ratingKey.matches(Regex("[0-9]{1,20}")) && !track.liked,
            ) { Text(if (track?.liked == true) "Liked" else "Like song") }
        }
        HorizontalDivider(Modifier.padding(vertical = 4.dp))
        Text("Carousel", style = MaterialTheme.typography.titleSmall)
        val carousel = player.carousel.value
        when {
            player.carousel.error != null -> Text(player.carousel.error, color = MaterialTheme.colorScheme.error)
            carousel == null -> Text("Loading carousel status…")
            else -> {
                Text(when {
                    !carousel.enabled -> "Disabled · enable in Configure device"
                    !carousel.available -> "Playback unavailable · kiosk is not responding"
                    carousel.paused -> "Paused"
                    else -> "Playing"
                })
                if (carousel.available && carousel.sceneTitle.isNotBlank()) {
                    Text(carousel.sceneTitle, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            val carouselReady = available && player.carousel.available && carousel?.let { it.enabled && it.available } == true
            val paused = carousel?.let { it.available && it.paused } == true
            OutlinedButton(
                onClick = { onCommand(PlayerCommand.Carousel(if (paused) CarouselAction.RESUME else CarouselAction.PAUSE)) },
                enabled = carouselReady,
            ) { Text(if (paused) "Resume" else "Pause") }
            OutlinedButton(
                onClick = { onCommand(PlayerCommand.Carousel(CarouselAction.NEXT)) },
                enabled = carouselReady,
            ) { Text("Next scene") }
        }
        player.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        player.commandError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (player.trustRequired) TextButton(onClick = onReenroll) { Text("Trust / enrol again") }
    }
}

/** Online sync-group leaders whose player controls also drive their followers. */
internal fun groupLeaders(statuses: List<DeviceStatus>, players: Map<String, PlayerState>): List<DeviceStatus> =
    statuses.filter { it.online && it.musicSync.role == SyncRole.LEADER && it.id in players }

@Composable
internal fun GroupPlayer(
    leaders: List<DeviceStatus>,
    players: Map<String, PlayerState>,
    onCommand: (String, PlayerCommand) -> Unit,
) {
    if (leaders.isEmpty()) return
    var selectedId by remember { mutableStateOf("") }
    var choosing by remember { mutableStateOf(false) }
    val leader = leaders.firstOrNull { it.id == selectedId } ?: leaders.first()
    val player = players[leader.id] ?: PlayerState()
    val music = player.music.value
    val ready = !player.working && !player.trustRequired && player.music.available
    fun label(device: DeviceStatus) = device.name + " group" +
        device.musicSync.members.size.let { if (it > 0) " (+$it)" else "" }
    Surface(
        tonalElevation = 3.dp, shadowElevation = 6.dp, shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (leaders.size > 1) {
                Box {
                    TextButton(onClick = { choosing = true }) { Text("Music group: ${label(leader)} ▾") }
                    DropdownMenu(expanded = choosing, onDismissRequest = { choosing = false }) {
                        leaders.forEach { device ->
                            DropdownMenuItem(text = { Text(label(device)) },
                                onClick = { selectedId = device.id; choosing = false })
                        }
                    }
                }
            } else {
                Text(label(leader), style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            when {
                player.music.error != null -> Text(player.music.error, color = MaterialTheme.colorScheme.error)
                music == null -> Text("Loading music status…")
                !music.enabled -> Text("Music stopped")
                else -> {
                    val status = music.status
                    Text(
                        status.title.ifBlank { if (status.pausedByUser) "Paused" else status.state.replaceFirstChar { it.uppercase() } },
                        style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                    val detail = listOfNotNull(
                        status.artist.takeIf { status.title.isNotBlank() && it.isNotBlank() },
                        "Paused".takeIf { status.pausedByUser && status.title.isNotBlank() },
                    ).joinToString(" · ")
                    if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                val primary = music?.let(SettingsRules::primaryMusicAction) ?: "play"
                OutlinedButton(onClick = { onCommand(leader.id, PlayerCommand.Music(primary)) },
                    enabled = ready && !music?.stationKey.isNullOrBlank(),
                ) { Text(primary.replaceFirstChar { it.uppercase() }) }
                OutlinedButton(onClick = { onCommand(leader.id, PlayerCommand.Music("skip")) },
                    enabled = ready && music?.enabled == true) { Text("Skip") }
                OutlinedButton(onClick = { onCommand(leader.id, PlayerCommand.Music("stop")) },
                    enabled = ready && music?.enabled == true) { Text("Stop") }
            }
            player.commandError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        }
    }
}
