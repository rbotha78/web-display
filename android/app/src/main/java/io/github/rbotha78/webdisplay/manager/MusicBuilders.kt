package io.github.rbotha78.webdisplay.manager

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.net.URLEncoder

private fun enc(value: String) = URLEncoder.encode(value, "UTF-8")

/** Artist/album mix builder and smart playlist builder; both only change the music draft or act on explicit buttons. */
@Composable
fun MusicBuilders(state: SettingsState, editor: SettingsSession, draft: MusicDraft, working: Boolean) {
    var builder by remember { mutableStateOf<String?>(null) }
    var smart by remember { mutableStateOf(false) }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = { builder = "artist"; smart = false }) { Text("Build artist mix") }
        OutlinedButton(onClick = { builder = "album"; smart = false }) { Text("Build album mix") }
        OutlinedButton(onClick = { smart = true; builder = null }) { Text("Create smart playlist") }
    }
    builder?.let { kind -> MixBuilder(kind, editor, draft) { builder = null } }
    if (smart) SmartPlaylistBuilder(editor, working) { smart = false }
}

@Composable
private fun LibraryChoice(editor: SettingsSession, library: String, onLibrary: (String) -> Unit) {
    var libraries by remember { mutableStateOf<List<MusicBrowseItem>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var attempt by remember { mutableIntStateOf(0) }
    LaunchedEffect(attempt) {
        error = null
        editor.lookup("/api/plex/music/browse", SettingsRules::parseMusicBrowse) { result ->
            result.onSuccess { value ->
                libraries = value.items
                if (value.items.none { it.id == library }) value.items.firstOrNull()?.let { onLibrary(it.id) }
            }.onFailure { error = it.message ?: "Could not load music libraries" }
        }
    }
    val list = libraries
    when {
        list != null && list.isEmpty() -> Hint("No Plex music libraries were found.")
        list != null -> ChoiceField("Music library", list.map { it.id to it.title }, library, onLibrary)
        error != null -> Row(verticalAlignment = Alignment.CenterVertically) {
            ErrorText(error.orEmpty())
            TextButton(onClick = { attempt++ }) { Text("Retry") }
        }
        else -> Hint("Loading music libraries…")
    }
}

@Composable
private fun MixBuilder(kind: String, editor: SettingsSession, draft: MusicDraft, onClose: () -> Unit) {
    var library by remember(kind) { mutableStateOf("") }
    var query by remember(kind) { mutableStateOf("") }
    var search by remember(kind) { mutableStateOf("") }
    var offset by remember(kind) { mutableIntStateOf(0) }
    var page by remember(kind) { mutableStateOf<MusicBrowseResult?>(null) }
    var pageError by remember(kind) { mutableStateOf<String?>(null) }
    var selected by remember(kind) { mutableStateOf<List<MusicBrowseItem>?>(null) }
    var savedError by remember(kind) { mutableStateOf<String?>(null) }

    LaunchedEffect(kind) {
        if (SettingsRules.mixKind(draft.stationKey) == kind) {
            editor.lookup("/api/plex/music/browse?key=${enc(draft.stationKey)}", SettingsRules::parseMusicBrowse) { result ->
                result.onSuccess { selected = it.items }.onFailure { savedError = it.message ?: "Could not load the saved mix" }
            }
        } else selected = emptyList()
    }
    LaunchedEffect(library, search, offset) {
        if (library.isEmpty()) return@LaunchedEffect
        page = null
        pageError = null
        editor.lookup("/api/plex/music/browse?library=${enc(library)}&kind=$kind&search=${enc(search)}&offset=$offset",
            SettingsRules::parseMusicBrowse) { result ->
            result.onSuccess { page = it }.onFailure { pageError = it.message ?: "Could not browse Plex music" }
        }
    }

    fun choose(items: List<MusicBrowseItem>) {
        selected = items
        editor.editMusic { it.copy(stationKey = SettingsRules.mixKey(kind, items)) }
    }

    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        HorizontalDivider()
        Text("Custom $kind mix", fontWeight = FontWeight.SemiBold)
        Hint("Choose up to ${SettingsRules.MAX_MIX_ITEMS} ${kind}s. Plex shuffles their tracks; the mix becomes the music source draft.")
        savedError?.let { ErrorText(it) }
        val chosen = selected
        if (chosen == null) Hint("Loading saved mix…") else {
            chosen.forEach { item ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(item.title + item.artist.takeIf { it.isNotBlank() }?.let { " — $it" }.orEmpty(), Modifier.weight(1f))
                    TextButton(onClick = { choose(chosen - item) }) { Text("Remove") }
                }
            }
            LibraryChoice(editor, library) { library = it; offset = 0 }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                DraftField(query, { query = it }, "Search ${kind}s", maxLength = 100, modifier = Modifier.weight(1f))
                OutlinedButton(onClick = { search = query.trim(); offset = 0 }) { Text("Search") }
            }
            pageError?.let { ErrorText(it) }
            page?.let { result ->
                if (result.items.isEmpty()) Hint("No matching ${kind}s.")
                result.items.forEach { item ->
                    val isChosen = chosen.any { it.id == item.id }
                    CheckRow(item.title + item.artist.takeIf { it.isNotBlank() }?.let { " — $it" }.orEmpty(), isChosen, { on ->
                        choose(if (on) chosen + item else chosen.filterNot { it.id == item.id })
                    }, enabled = isChosen || chosen.size < SettingsRules.MAX_MIX_ITEMS)
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (offset > 0) OutlinedButton(onClick = { offset = maxOf(0, offset - 100) }) { Text("Previous") }
                    if (offset + result.items.size < result.total) OutlinedButton(onClick = { offset += 100 }) { Text("Next") }
                    Hint("${result.total} total")
                }
            } ?: if (pageError == null && library.isNotEmpty()) Hint("Loading…") else Unit
        }
        TextButton(onClick = onClose) { Text("Close mix builder") }
        HorizontalDivider()
    }
}

@Composable
private fun SmartPlaylistBuilder(editor: SettingsSession, working: Boolean, onClose: () -> Unit) {
    var library by remember { mutableStateOf("") }
    var filters by remember { mutableStateOf<MusicFilters?>(null) }
    var filterError by remember { mutableStateOf<String?>(null) }
    var decade by remember { mutableStateOf("") }
    var genre by remember { mutableStateOf("") }
    var mood by remember { mutableStateOf("") }
    var title by remember { mutableStateOf("") }
    var count by remember { mutableStateOf<Int?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(library) {
        if (library.isEmpty()) return@LaunchedEffect
        filters = null
        filterError = null
        decade = ""; genre = ""; mood = ""; count = null
        editor.lookup("/api/plex/music/filters?library=${enc(library)}", SettingsRules::parseMusicFilters) { result ->
            result.onSuccess { filters = it }.onFailure { filterError = it.message ?: "Could not load music filters" }
        }
    }

    fun body(withTitle: Boolean) = try {
        problem = null
        SettingsRules.smartPlaylistPayload(library, decade, genre, mood, if (withTitle) title else null)
    } catch (error: IllegalArgumentException) {
        problem = error.message
        null
    }

    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        HorizontalDivider()
        Text("Smart playlist", fontWeight = FontWeight.SemiBold)
        Hint("Creates a Plex smart playlist from your filters and selects it as the music source draft.")
        LibraryChoice(editor, library) { library = it }
        filterError?.let { ErrorText(it) }
        filters?.let { f ->
            fun options(list: List<FilterChoice>) = listOf("" to "Any") + list.map { it.id to it.title }
            ChoiceField("Decade", options(f.decade), decade, { decade = it; count = null })
            ChoiceField("Genre", options(f.genre), genre, { genre = it; count = null })
            ChoiceField("Mood", options(f.mood), mood, { mood = it; count = null })
            count?.let { Text("$it matching tracks") }
            DraftField(title, { title = it }, "Playlist name", maxLength = 100)
            problem?.let { ErrorText(it) }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = {
                    body(false)?.let { editor.plexAction("Counting matching tracks", "/api/plex/music/playlists/preview", it,
                        { json -> json.getInt("count") }) { value -> count = value } }
                }, enabled = !working) { Text("Preview count") }
                Button(onClick = {
                    body(true)?.let { editor.plexAction("Creating smart playlist", "/api/plex/music/playlists", it,
                        SettingsRules::parseStation) { station ->
                        editor.addStation(station)
                        editor.editMusic { draft -> draft.copy(stationKey = station.key) }
                        onClose()
                    } }
                }, enabled = !working) { Text("Create in Plex") }
            }
        } ?: if (filterError == null && library.isNotEmpty()) Hint("Loading filters…") else Unit
        TextButton(onClick = onClose) { Text("Close smart playlist builder") }
        HorizontalDivider()
    }
}
