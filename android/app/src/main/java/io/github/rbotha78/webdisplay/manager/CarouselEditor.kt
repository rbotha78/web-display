package io.github.rbotha78.webdisplay.manager

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

private val KIND_LABELS = mapOf(
    "web" to "Web page", "dashboard" to "Dashboard", "video" to "Video", "train" to "Train departures", "weather" to "Weather",
)
private val LAYOUT_LABELS = listOf("clock-notice" to "Clock and notice", "clock" to "Clock only", "notice" to "Notice only")

@Composable
fun CarouselEditor(state: SettingsState, editor: SettingsSession, working: Boolean, ask: (Confirmation) -> Unit) {
    val dirty = state.dirty(SettingsPart.PLAYLIST)
    SectionCard("Carousel", state.playlist, SettingsPart.PLAYLIST, editor, dirty, working,
        onConflictOverwrite = { editor.savePlaylist(force = true) }) { _, draft ->
        draft ?: return@SectionCard
        CheckRow("Carousel enabled", draft.enabled, { v -> editor.editPlaylist { it.copy(enabled = v) } }, switch = true)
        Hint("When enabled, the display rotates through enabled scenes instead of the display address. " +
            "${draft.scenes.size} of ${SettingsRules.MAX_SCENES} scenes.")
        val problem = SettingsRules.playlistProblem(draft)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { editor.savePlaylist() }, enabled = !working && dirty && problem == null) { Text("Save and apply") }
            OutlinedButton(onClick = editor::checkPlayback) { Text("Check playback errors") }
        }
        if (dirty && problem != null) ErrorText(problem)
        state.playback?.let { status ->
            Hint(if (status.failures.isEmpty()) "No scene playback failures reported." else "${status.failures.size} scene(s) reported playback failures.")
        }
        draft.scenes.forEachIndexed { index, scene ->
            SceneCard(index, scene, draft.scenes.size, state, editor, ask)
        }
        if (draft.scenes.size < SettingsRules.MAX_SCENES) {
            Text("Add scene", fontWeight = FontWeight.SemiBold)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SettingsRules.SCENE_KINDS.forEach { kind ->
                    OutlinedButton(onClick = {
                        editor.editPlaylist { it.copy(scenes = it.scenes + SettingsRules.newScene(kind)) }
                    }) { Text("+ ${KIND_LABELS[kind]}") }
                }
            }
        }
        Hint("Changes stay in this draft until Save and apply. Saving restarts playback at the first enabled scene.")
    }
}

@Composable
private fun SceneCard(index: Int, scene: Scene, count: Int, state: SettingsState, editor: SettingsSession, ask: (Confirmation) -> Unit) {
    fun edit(change: (Scene) -> Scene) = editor.editScene(scene.id, change)
    val failure = state.playback?.failures?.get(scene.id)
    val problem = SettingsRules.sceneProblem(scene)
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${index + 1}. ${scene.title.ifBlank { KIND_LABELS[scene.kind] ?: scene.kind }}",
                    style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                Text(KIND_LABELS[scene.kind] ?: scene.kind, style = MaterialTheme.typography.labelMedium)
            }
            failure?.let { ErrorText("Last playback failure: ${it.message}") }
            CheckRow("Scene enabled", scene.enabled, { v -> edit { it.copy(enabled = v) } })
            DraftField(scene.title, { v -> edit { it.copy(title = v) } }, "Title (optional)", maxLength = 80)
            DraftField(scene.duration, { v -> edit { it.copy(duration = v.filter(Char::isDigit)) } },
                if (scene.kind == "video") "Duration in seconds (blank plays to the end)" else "Duration in seconds (5–86400)",
                number = true, maxLength = 5)
            when (scene.kind) {
                "web" -> DraftField(scene.url, { v -> edit { it.copy(url = v) } }, "Page URL", placeholder = "https://example.com")
                "video" -> VideoFields(scene, editor, ::edit)
                "dashboard" -> {
                    ChoiceField("Layout", LAYOUT_LABELS, scene.layout, { v -> edit { it.copy(layout = v) } })
                    if (scene.layout != "clock") DraftField(scene.text, { v -> edit { it.copy(text = v) } }, "Notice text",
                        singleLine = false, maxLength = 2000)
                    CheckRow("Show weather", scene.showWeather, { v -> edit { it.copy(showWeather = v) } })
                }
                "train" -> {
                    DraftField(scene.stationCrs, { v -> edit { it.copy(stationCrs = v.uppercase()) } }, "Station CRS code (e.g. PAD)", maxLength = 3)
                    DraftField(scene.destinationCrs, { v -> edit { it.copy(destinationCrs = v.uppercase()) } },
                        "Only trains to (CRS, optional)", maxLength = 3)
                    DraftField(scene.filterStationCrs, { v -> edit { it.copy(filterStationCrs = v.uppercase()) } },
                        "Only trains calling at (CRS, optional)", maxLength = 3)
                    CheckRow("Show the previous departure", scene.showPreviousDeparture, { v -> edit { it.copy(showPreviousDeparture = v) } })
                    Hint("Requires a National Rail departure board key in Global settings.")
                }
                "weather" -> Hint("Shows the forecast for the city configured in Global settings.")
            }
            problem?.let { ErrorText(it) }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(onClick = { editor.editPlaylist { SettingsRules.move(it, index, -1) } }, enabled = index > 0) { Text("Move up") }
                TextButton(onClick = { editor.editPlaylist { SettingsRules.move(it, index, 1) } }, enabled = index < count - 1) { Text("Move down") }
                TextButton(onClick = {
                    ask(Confirmation("Remove scene ${index + 1}?",
                        "The scene is removed from the draft. The device keeps it until you save the carousel.", "Remove scene") {
                        editor.editPlaylist { p -> p.copy(scenes = p.scenes.filterNot { it.id == scene.id }) }
                    })
                }) { Text("Remove") }
            }
        }
    }
}

@Composable
private fun VideoFields(scene: Scene, editor: SettingsSession, edit: ((Scene) -> Scene) -> Unit) {
    ChoiceField("Video source", listOf("url" to "Video URL", "plex" to "Plex library"), scene.source,
        { v -> edit { SettingsRules.withSource(it, v) } })
    if (scene.source == "plex") {
        Text(if (scene.plexRatingKey.isEmpty()) "No Plex media chosen" else "Plex item ${scene.plexRatingKey}")
        OutlinedButton(onClick = { editor.openPicker(scene.id) }) { Text("Browse Plex") }
        DraftField(scene.plexBitrate, { v -> edit { it.copy(plexBitrate = v.filter(Char::isDigit)) } },
            "Bitrate kbps (blank uses the Plex default)", number = true, maxLength = 5)
    } else {
        DraftField(scene.url, { v -> edit { it.copy(url = v) } }, "Video URL", placeholder = "https://example.com/video.mp4")
    }
    CheckRow("Play video audio", scene.audioEnabled, { v -> edit { it.copy(audioEnabled = v) } })
}

@Composable
fun PlexPickerDialog(picker: PlexPicker, editor: SettingsSession) {
    AlertDialog(
        onDismissRequest = editor::closePicker,
        title = { Text("Choose Plex media") },
        text = {
            Column(Modifier.heightIn(max = 480.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Hint(picker.path.joinToString(" › ") { it.first })
                when {
                    picker.loading -> Text("Loading…")
                    picker.error != null -> {
                        ErrorText(picker.error)
                        TextButton(onClick = editor::retryPicker) { Text("Retry") }
                    }
                    picker.items.isEmpty() -> Text("Nothing here.")
                    else -> picker.items.forEach { item ->
                        TextButton(onClick = { editor.pickerOpen(item) }, modifier = Modifier.fillMaxWidth()) {
                            Text((if (item.container) "▸ " else "▶ ") + item.title +
                                item.type.takeIf { it.isNotBlank() }?.let { " ($it)" }.orEmpty(), Modifier.weight(1f))
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = editor::closePicker) { Text("Cancel") } },
        dismissButton = if (picker.path.size > 1) ({ TextButton(onClick = editor::pickerBack) { Text("Back") } }) else null,
    )
}
