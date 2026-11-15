package io.github.rbotha78.webdisplay.manager

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

@Composable
fun RadioSettings(editor: SettingsSession, draft: MusicDraft, working: Boolean) {
    var name by remember { mutableStateOf("") }
    var tag by remember { mutableStateOf("") }
    var country by remember { mutableStateOf("") }
    var query by remember { mutableStateOf(Triple("", "", "")) }
    var results by remember { mutableStateOf<RadioSearchResult?>(null) }
    var searching by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val stations = draft.radioStations ?: return

    fun search(offset: Int, terms: Triple<String, String, String>) {
        searching = true
        error = null
        if (offset == 0) { query = terms; results = null }
        editor.lookup(SettingsRules.radioSearchPath(terms.first, terms.second, terms.third, offset), SettingsRules::parseRadioSearch) { result ->
            searching = false
            result.onSuccess { page -> results = SettingsRules.mergeRadioResults(if (offset == 0) null else results, page) }
                .onFailure { error = it.message ?: "Could not search radio stations" }
        }
    }

    Text("Internet radio", fontWeight = FontWeight.SemiBold)
    Hint("Search Radio Browser for MP3 and AAC stations. Radio does not need Plex; live radio cannot skip songs. Save music settings to keep changes.")
    stations.forEach { station ->
        StationRow(station.name, listOf(station.country, codecLabel(station)).filter { it.isNotBlank() }.joinToString(" · ")) {
            TextButton(onClick = { editor.editMusic { SettingsRules.removeRadioStation(it, station.id) } }, enabled = !working,
                modifier = Modifier.semantics { contentDescription = "Remove ${station.name}" }) { Text("Remove") }
        }
    }
    DraftField(name, { name = it }, "Station name", enabled = !working && !searching, maxLength = 100)
    DraftField(tag, { tag = it }, "Genre", enabled = !working && !searching, placeholder = "e.g. jazz", maxLength = 100)
    DraftField(country, { country = it }, "Country code", enabled = !working && !searching, placeholder = "e.g. GB", maxLength = 2)
    Button(onClick = { search(0, Triple(name, tag, country)) }, enabled = !working && !searching && (name.isNotBlank() || tag.isNotBlank())) { Text("Search radio") }
    if (searching) Hint("Searching radio stations…")
    error?.let { ErrorText(it) }
    results?.let { page ->
        if (page.items.isEmpty()) Hint("No supported stations found.")
        page.items.forEach { item ->
            val station = item.station
            val saved = stations.any { it.id == station.id }
            StationRow(station.name, listOf(station.country, codecLabel(station), item.tags).filter { it.isNotBlank() }.joinToString(" · ")) {
                TextButton(onClick = { editor.editMusic { SettingsRules.addRadioStation(it, station) } },
                    enabled = !working && !saved && stations.size < 50,
                    modifier = Modifier.semantics { contentDescription = if (saved) "${station.name} added" else "Add ${station.name}" }) {
                    Text(if (saved) "Added" else "Add")
                }
            }
        }
        if (page.more) TextButton(onClick = { search(page.offset + 30, query) }, enabled = !working && !searching && page.offset + 30 <= 1000) { Text("More stations") }
    }
}

private fun codecLabel(station: RadioStation) = "${station.codec}${if (station.bitrate > 0) " ${station.bitrate} kbps" else ""}"

@Composable
private fun StationRow(name: String, details: String, action: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(name, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (details.isNotBlank()) Text(details, maxLines = 1, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodySmall)
        }
        action()
    }
}
