package io.github.rbotha78.webdisplay.manager

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.text.DateFormat
import java.util.Date

/**
 * Native configuration for one enrolled device. Drafts live in [SettingsSession] so they
 * survive tab/section switches; nothing is written until an explicit save or action.
 */
@Composable
fun SettingsScreen(state: SettingsState, editor: SettingsSession, viewModel: ManagerViewModel, modifier: Modifier = Modifier) {
    var confirm by remember { mutableStateOf<Confirmation?>(null) }
    val ask: (Confirmation) -> Unit = { confirm = it }
    val dirty = state.dirtyParts()
    val working = state.working != null
    val leave = {
        if (dirty.isEmpty()) viewModel.closeSettings()
        else ask(Confirmation(
            "Discard unsaved changes?",
            "Unsaved changes in ${dirty.joinToString { it.label }} will be lost.",
            "Discard and close",
        ) { viewModel.closeSettings() })
    }
    BackHandler(onBack = leave)

    LazyColumn(
        modifier = modifier.padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { Header(state, dirty, working, editor, ask, leave) }
        if (state.tab == SettingsTab.GLOBAL) {
            item { DisplaySection(state, editor, working) }
            item { BluetoothSection(state, editor, working, ask) }
            item { SleepSection(state, editor, working) }
            item { PlexSection(state, editor, working, ask) }
            item { MusicSection(state, editor, working) }
            item { RailSection(state, editor, working, ask) }
            item { WeatherSection(state, editor, working, ask) }
            item { HostnameSection(state, editor, working, ask) }
            item { WifiSection(state, editor, working, ask) }
            item { UpdatesSection(state, editor, working, ask) }
            item { SshSection(state, viewModel) }
            item { ApiKeysNote() }
            item { RestartSection(state, editor, working, ask) }
        } else {
            item { CarouselEditor(state, editor, working, ask) }
        }
        item { Text("", Modifier.padding(bottom = 24.dp)) }
    }

    state.picker?.let { PlexPickerDialog(it, editor) }
    confirm?.let { request ->
        ConfirmDialog(
            title = request.title,
            text = request.text,
            confirm = request.confirm,
            onDismiss = { confirm = null },
            onConfirm = { confirm = null; request.action() },
        )
    }
}

@Composable
private fun Header(
    state: SettingsState,
    dirty: List<SettingsPart>,
    working: Boolean,
    editor: SettingsSession,
    ask: (Confirmation) -> Unit,
    leave: () -> Unit,
) {
    val identity = state.identity
    Column(Modifier.fillMaxWidth().padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = leave) { Text("‹ Back") }
            Column(Modifier.weight(1f)) {
                Text("Configure ${identity.name}", style = MaterialTheme.typography.titleLarge)
                Text("${identity.host}:${identity.port}" + (identity.hostname?.let { " · $it" } ?: "") +
                    (identity.version?.let { " · firmware $it" } ?: ""), style = MaterialTheme.typography.bodySmall)
            }
        }
        identity.fingerprint?.let {
            Text("Pinned certificate SHA-256: $it", style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
        }
        state.working?.let { label ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(18.dp))
                Text("$label…", Modifier.padding(start = 8.dp))
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            val globalDirty = dirty.count { it != SettingsPart.PLAYLIST }
            TabButton("Global settings" + if (globalDirty > 0) " •" else "", state.tab == SettingsTab.GLOBAL) {
                editor.setTab(SettingsTab.GLOBAL)
            }
            TabButton("Carousel" + if (SettingsPart.PLAYLIST in dirty) " •" else "", state.tab == SettingsTab.CAROUSEL) {
                editor.setTab(SettingsTab.CAROUSEL)
            }
            TextButton(onClick = {
                if (dirty.isEmpty()) editor.reload()
                else ask(Confirmation("Reload all settings?",
                    "Reloading replaces unsaved changes in ${dirty.joinToString { it.label }} with the device's current settings.",
                    "Discard and reload") { editor.reload() })
            }, enabled = !working) { Text("Reload all") }
            TextButton(onClick = leave) { Text("Close") }
        }
        if (dirty.isNotEmpty()) Hint("Unsaved: ${dirty.joinToString { it.label }}. Each section saves separately.")
    }
}

@Composable
private fun TabButton(text: String, selected: Boolean, onClick: () -> Unit) {
    if (selected) Button(onClick = onClick) { Text(text) } else OutlinedButton(onClick = onClick) { Text(text) }
}

@Composable
private fun DisplaySection(state: SettingsState, editor: SettingsSession, working: Boolean) {
    SectionCard("Display", state.display, SettingsPart.DISPLAY, editor, state.dirty(SettingsPart.DISPLAY), working) { server, draft ->
        draft ?: return@SectionCard
        DraftField(draft.url, { v -> editor.editDisplay { it.copy(url = v) } }, "Display address", placeholder = "https://example.com")
        Hint("Shown when the carousel is disabled. Leave blank to show the setup screen.")
        DraftField(draft.safeArea, { v -> editor.editDisplay { it.copy(safeArea = v) } }, "TV safe area (0–10 %)", number = true, maxLength = 2)
        CheckRow("Start setup Wi-Fi automatically when offline", draft.autoAp, { v -> editor.editDisplay { it.copy(autoAp = v) } }, switch = true)
        val audio = state.audio
        val outputs = audio.server
        when {
            outputs != null -> ChoiceField(
                "Audio output",
                (outputs.map { it.id to it.label } + listOfNotNull(
                    (server.audioOutput to "${server.audioOutput} (current)").takeIf { current -> outputs.none { it.id == current.first } },
                )),
                draft.audioOutput,
                { v -> editor.editDisplay { it.copy(audioOutput = v) } },
            )
            audio.loading -> Hint("Loading audio outputs…")
            else -> {
                Hint("Audio output: ${server.audioOutput}. " + (audio.error ?: "Outputs unavailable."))
                if (!audio.unsupported) TextButton(onClick = { editor.reload(SettingsPart.AUDIO) }) { Text("Retry audio outputs") }
            }
        }
        Button(onClick = editor::saveDisplay, enabled = !working && state.dirty(SettingsPart.DISPLAY)) { Text("Save display settings") }
    }
}

@Composable
private fun BluetoothSection(
    state: SettingsState,
    editor: SettingsSession,
    working: Boolean,
    ask: (Confirmation) -> Unit,
) {
    SectionCard("Bluetooth speaker", state.bluetooth, SettingsPart.BLUETOOTH, editor, false, working) { server, _ ->
        if (!server.supported) {
            Hint("This display's base image does not include Bluetooth audio. Flash a newer image to use a speaker.")
            return@SectionCard
        }
        if (server.adapter == false) {
            Hint("No Bluetooth adapter found. Raspberry Pi 2 needs a USB Bluetooth adapter.")
            return@SectionCard
        }

        val paired = server.address.isNotEmpty()
        if (paired) {
            Text(server.name.ifBlank { server.address }, fontWeight = FontWeight.SemiBold)
            Hint(when {
                server.active -> "Playing through speaker"
                server.enabled -> "Not connected · using wired output"
                else -> "Off · using wired output"
            })
            CheckRow(
                "Play video audio and music through this speaker",
                server.enabled,
                editor::setBluetoothEnabled,
                enabled = !working,
                switch = true,
            )
            var delay by remember(state.identity.id, server.address, server.delayMs) {
                mutableStateOf(server.delayMs.toString())
            }
            DraftField(delay, { delay = it }, "Extra speaker delay (ms)", number = true, signedNumber = true, maxLength = 5)
            val delayValue = delay.trim().toIntOrNull()
            if (delayValue == null || delayValue !in -1000..1000) {
                ErrorText("Enter a whole number from -1000 to 1000 ms.")
            }
            Hint("Only affects synced group music. The speaker's reported latency is already compensated; raise this if this display still sounds behind the others.")
            Button(
                onClick = { editor.saveBluetoothDelay(delay) },
                enabled = !working && delayValue?.let { it in -1000..1000 && it != server.delayMs } == true,
            ) { Text("Save delay") }
            OutlinedButton(onClick = {
                ask(Confirmation(
                    "Forget ${server.name.ifBlank { "this speaker" }}?",
                    "The speaker will be unpaired from this display. Audio will use the wired output.",
                    "Forget speaker",
                ) { editor.forgetBluetooth() })
            }, enabled = !working) { Text("Forget speaker") }
        } else {
            Hint("No speaker paired. Audio uses the wired output.")
            Hint("Put the speaker in pairing mode, then search. Pairing takes up to a minute. While the speaker is off or out of range, audio falls back to the wired output and the display keeps trying to reconnect.")
            val searching = state.working == "Searching for Bluetooth speakers"
            OutlinedButton(onClick = editor::scanBluetooth, enabled = !working) {
                Text(if (searching) "Searching…" else "Find speakers")
            }
            state.bluetoothScan?.let { devices ->
                if (devices.isEmpty()) Hint("No Bluetooth devices found.")
                else ChoiceField(
                    "Speakers found",
                    devices.map { device ->
                        device.address to (device.name.ifBlank { device.address } +
                            (if (device.audio) "" else " (not an audio device)") +
                            (if (device.paired) " · paired" else ""))
                    },
                    state.bluetoothSelectedAddress,
                    editor::selectBluetooth,
                    enabled = !working,
                )
                Button(onClick = editor::pairBluetooth, enabled = !working && state.bluetoothSelectedAddress.isNotBlank()) {
                    Text("Pair and use")
                }
            }
        }
        TextButton(onClick = editor::refreshBluetooth, enabled = !working) { Text("Refresh Bluetooth status") }
    }
}

@Composable
private fun SleepSection(state: SettingsState, editor: SettingsSession, working: Boolean) {
    SectionCard("Sleep schedule", state.sleep, SettingsPart.SLEEP, editor, state.dirty(SettingsPart.SLEEP), working,
        onConflictOverwrite = { editor.saveSleep(force = true) }) { server, draft ->
        draft ?: return@SectionCard
        Hint("Device time ${server.deviceTime} (${server.timezone}). " + when {
            server.wakeUntil != null -> "Awake until ${formatEpoch(server.wakeUntil)}."
            server.sleeping -> "Sleeping now."
            server.scheduledSleep -> "Inside a sleep window."
            else -> "Awake."
        })
        SettingsRules.DAYS.forEach { day ->
            val window = draft.days[day] ?: SleepWindow(false, "22:00", "07:00")
            fun set(change: (SleepWindow) -> SleepWindow) = editor.editSleep { it.copy(days = it.days + (day to change(window))) }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Column(Modifier.weight(1.3f)) {
                    CheckRow(day.replaceFirstChar { it.uppercase() }, window.enabled, { v -> set { it.copy(enabled = v) } })
                }
                DraftField(window.start, { v -> set { it.copy(start = v) } }, "Sleep", enabled = window.enabled,
                    maxLength = 5, modifier = Modifier.weight(1f))
                DraftField(window.end, { v -> set { it.copy(end = v) } }, "Wake", enabled = window.enabled,
                    maxLength = 5, modifier = Modifier.weight(1f))
            }
        }
        Hint("Times use 24-hour HH:MM. A window may cross midnight; start and end must differ.")
        Button(onClick = { editor.saveSleep() }, enabled = !working && state.dirty(SettingsPart.SLEEP)) { Text("Save sleep schedule") }
        Text("Wake the display now", fontWeight = FontWeight.SemiBold)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SettingsRules.WAKE_MINUTES.forEach { minutes ->
                OutlinedButton(onClick = { editor.wake(minutes) }, enabled = !working) {
                    Text(if (minutes < 60) "$minutes min" else "${minutes / 60} h")
                }
            }
            if (server.wakeUntil != null) {
                OutlinedButton(onClick = { editor.wake(0) }, enabled = !working) { Text("Resume schedule") }
            }
        }
    }
}

@Composable
private fun PlexSection(state: SettingsState, editor: SettingsSession, working: Boolean, ask: (Confirmation) -> Unit) {
    SectionCard("Plex", state.plex, SettingsPart.PLEX, editor, state.dirty(SettingsPart.PLEX), working) { server, draft ->
        draft ?: return@SectionCard
        Hint(if (server.configured) "Connected to ${server.serverName.ifBlank { server.serverUrl }}." else "Plex is not configured.")
        DraftField(draft.serverUrl, { v -> editor.editPlex { it.copy(serverUrl = v) } }, "Plex server URL", placeholder = "http://192.168.1.10:32400")
        DraftField(draft.token, { v -> editor.editPlex { it.copy(token = v) } }, "Plex token", secret = true,
            placeholder = if (server.configured) "Saved — leave blank to keep" else null)
        Hint("The token is write-only: it is never shown again. Leave blank to keep the saved token.")
        DraftField(draft.defaultBitrate, { v -> editor.editPlex { it.copy(defaultBitrate = v) } }, "Default video bitrate (kbps, 500–20000)",
            number = true, maxLength = 5)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = editor::savePlex, enabled = !working && state.dirty(SettingsPart.PLEX)) { Text("Verify and save") }
            if (server.configured) OutlinedButton(onClick = {
                ask(Confirmation("Remove Plex settings?",
                    "The server address and token are deleted from the device and background music is disabled. Plex video scenes stop playing.",
                    "Remove Plex") { editor.clearPlex() })
            }, enabled = !working) { Text("Remove Plex") }
        }
    }
}

private fun musicSourceTitle(key: String, stations: List<MusicStation>): String? {
    stations.firstOrNull { it.key == key }?.let {
        return "${it.library}: ${it.title}" + if (it.smart) " (smart playlist)" else ""
    }
    val kind = SettingsRules.mixKind(key) ?: return null
    val count = key.substringAfterLast(":").split(",").size
    return "Custom $kind mix ($count selections)"
}

private fun musicSourceOptions(emptyTitle: String, selected: String, stations: List<MusicStation>): List<Pair<String, String>> =
    buildList {
        add("" to emptyTitle)
        stations.forEach { add(it.key to (musicSourceTitle(it.key, stations) ?: it.title)) }
        if (selected.isNotEmpty() && none { it.first == selected }) {
            add(selected to (musicSourceTitle(selected, stations) ?: "Saved music source (currently unavailable)"))
        }
    }

@Composable
private fun MusicSection(state: SettingsState, editor: SettingsSession, working: Boolean) {
    SectionCard("Background music", state.music, SettingsPart.MUSIC, editor, state.dirty(SettingsPart.MUSIC), working,
        onConflictOverwrite = { editor.saveMusic(force = true) }) { server, draft ->
        draft ?: return@SectionCard
        val configured = state.plex.server?.configured == true
        if (!configured) {
            Hint("Configure Plex above to choose background music.")
            return@SectionCard
        }
        val status = server.status
        Text("Status: ${status.state}" + status.title.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty())
        if (status.error.isNotBlank()) ErrorText(status.error)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val primary = SettingsRules.primaryMusicAction(server)
            OutlinedButton(onClick = { editor.musicControl(primary) }, enabled = !working && server.stationKey.isNotEmpty()) {
                Text(primary.replaceFirstChar { it.uppercase() })
            }
            OutlinedButton(onClick = { editor.musicControl("skip") }, enabled = !working && server.enabled) { Text("Skip") }
            OutlinedButton(onClick = { editor.musicControl("stop") }, enabled = !working && server.enabled) { Text("Stop") }
            OutlinedButton(onClick = editor::likeMusic,
                enabled = !working && status.ratingKey.isNotEmpty() && !status.liked) {
                Text(if (status.liked) "Liked" else "Like song")
            }
            TextButton(onClick = editor::refreshMusic, enabled = !state.music.loading) { Text("Refresh status") }
        }
        Hint("Transport buttons act immediately on the saved music source; they do not save the draft below.")
        val stations = state.stations
        val list = stations.server
        when {
            list != null -> ChoiceField("Music source", musicSourceOptions("No music source", draft.stationKey, list),
                draft.stationKey, { v -> editor.editMusic { it.copy(stationKey = v) } })
            stations.loading -> Hint("Loading Plex music sources…")
            else -> {
                ErrorText(stations.error ?: "Music sources unavailable")
                TextButton(onClick = { editor.reload(SettingsPart.STATIONS) }) { Text("Retry music sources") }
            }
        }
        if (server.supportsDaySources) {
            Text("Weekday sources", fontWeight = FontWeight.SemiBold)
            Hint("Days switch at midnight; a playing song finishes first. Saving a change to today's source restarts playback immediately.")
            musicSourceTitle(server.activeStationKey, list.orEmpty())?.let { Hint("Playing today: $it") }
            SettingsRules.DAYS.forEach { day ->
                val selected = draft.daySources.getValue(day)
                val label = day.replaceFirstChar { it.uppercase() } + if (day == server.activeDay) " (today)" else ""
                ChoiceField(label, musicSourceOptions("Use default source", selected, list.orEmpty()), selected,
                    { value -> editor.editMusic { it.copy(daySources = it.daySources + (day to value)) } }, enabled = list != null)
            }
        }
        MusicBuilders(state, editor, draft, working)
        CheckRow("Play background music", draft.enabled, { v -> editor.editMusic { it.copy(enabled = v) } }, switch = true)
        Text("Volume: ${draft.volume}%")
        Slider(
            value = draft.volume.toFloat(),
            onValueChange = { v -> editor.editMusic { it.copy(volume = v.toInt().coerceIn(0, 100)) } },
            valueRange = 0f..100f,
            modifier = Modifier.widthIn(max = 480.dp),
        )
        Button(onClick = { editor.saveMusic() }, enabled = !working && state.dirty(SettingsPart.MUSIC)) { Text("Save music settings") }
    }
}

@Composable
private fun RailSection(state: SettingsState, editor: SettingsSession, working: Boolean, ask: (Confirmation) -> Unit) {
    SectionCard("National Rail", state.rail, SettingsPart.RAIL, editor, state.dirty(SettingsPart.RAIL), working) { server, draft ->
        draft ?: return@SectionCard
        Hint("Departure board key: ${if (server.boardConfigured) "saved" else "not set"} · Service details key: " +
            if (server.detailsConfigured) "saved" else "not set")
        DraftField(draft.boardKey, { v -> editor.editRail { it.copy(boardKey = v) } }, "Live departure board API key", secret = true,
            placeholder = if (server.boardConfigured) "Saved — leave blank to keep" else null)
        DraftField(draft.detailsKey, { v -> editor.editRail { it.copy(detailsKey = v) } }, "Service details API key (optional)", secret = true,
            placeholder = if (server.detailsConfigured) "Saved — leave blank to keep" else null)
        DraftField(draft.refreshSeconds, { v -> editor.editRail { it.copy(refreshSeconds = v) } }, "Refresh interval (seconds, at least 30)",
            number = true, maxLength = 5)
        Hint("API keys are write-only and never shown again.")
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = editor::saveRail, enabled = !working && state.dirty(SettingsPart.RAIL)) { Text("Save rail settings") }
            if (server.boardConfigured) OutlinedButton(onClick = {
                ask(Confirmation("Remove departure board key?", "Train scenes stop updating until a new key is saved.",
                    "Remove key") { editor.clearRail(board = true, details = false) })
            }, enabled = !working) { Text("Remove board key") }
            if (server.detailsConfigured) OutlinedButton(onClick = {
                ask(Confirmation("Remove service details key?", "Calling-point details stop updating until a new key is saved.",
                    "Remove key") { editor.clearRail(board = false, details = true) })
            }, enabled = !working) { Text("Remove details key") }
        }
    }
}

@Composable
private fun WeatherSection(state: SettingsState, editor: SettingsSession, working: Boolean, ask: (Confirmation) -> Unit) {
    SectionCard("Weather", state.weather, SettingsPart.WEATHER, editor, state.dirty(SettingsPart.WEATHER), working) { server, draft ->
        draft ?: return@SectionCard
        DraftField(draft.city, { v -> editor.editWeather { it.copy(city = v) } }, "City", maxLength = 100)
        DraftField(draft.country, { v -> editor.editWeather { it.copy(country = v.uppercase()) } }, "Country code (2 letters)", maxLength = 2)
        DraftField(draft.apiKey, { v -> editor.editWeather { it.copy(apiKey = v) } }, "OpenWeather API key", secret = true,
            placeholder = if (server.configured) "Saved — leave blank to keep" else null)
        Hint("API key: ${if (server.configured) "saved" else "not set"}. It is write-only and never shown again.")
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = editor::saveWeather, enabled = !working && state.dirty(SettingsPart.WEATHER)) { Text("Save weather settings") }
            if (server.configured) OutlinedButton(onClick = {
                ask(Confirmation("Remove OpenWeather key?", "Weather scenes and dashboard weather stop updating.", "Remove key") {
                    editor.clearWeather()
                })
            }, enabled = !working) { Text("Remove API key") }
        }
    }
}

@Composable
private fun HostnameSection(state: SettingsState, editor: SettingsSession, working: Boolean, ask: (Confirmation) -> Unit) {
    SectionCard("Hostname", state.status, SettingsPart.STATUS, editor, state.dirty(SettingsPart.STATUS), working) { server, draft ->
        draft ?: return@SectionCard
        Hint("Current: ${server.hostname}.local" + if (server.addresses.isNotEmpty()) " · ${server.addresses.joinToString()}" else "")
        DraftField(draft, editor::editHostname, "Hostname", maxLength = 63)
        Button(onClick = {
            ask(Confirmation("Rename ${state.identity.name}?",
                "The device will advertise itself as ${draft.trim().lowercase()}.local. This manager keeps using the pinned certificate and saved addresses.",
                "Rename") { editor.setHostname() })
        }, enabled = !working && state.dirty(SettingsPart.STATUS)) { Text("Change hostname") }
    }
}

@Composable
private fun WifiSection(state: SettingsState, editor: SettingsSession, working: Boolean, ask: (Confirmation) -> Unit) {
    SectionCard("Wi-Fi", state.network, SettingsPart.NETWORK, editor, state.dirty(SettingsPart.NETWORK), working) { server, draft ->
        draft ?: return@SectionCard
        Hint("Internet: ${server.internetOnline?.let { if (it) "online" else "offline" } ?: "unknown"}" +
            server.defaultRoute.takeIf { it.isNotBlank() }?.let { " via $it" }.orEmpty() +
            " · Saved network: ${if (!server.savedNetwork) "none" else server.savedSsid.ifBlank { "yes" }}" + if (server.wifiBlocked) " · Wi-Fi radio blocked" else "")
        server.devices.forEach { Hint("${it.device} (${it.type}): ${it.state}" + it.connection.takeIf { c -> c.isNotBlank() }?.let { c -> " · $c" }.orEmpty()) }
        if (server.error.isNotBlank()) ErrorText(server.error)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            DraftField(draft.country, { v -> editor.editWifi { it.copy(country = v.uppercase()) } }, "Regulatory country",
                maxLength = 2, modifier = Modifier.weight(1f))
            OutlinedButton(onClick = editor::setCountry, enabled = !working && draft.country.trim().uppercase() != server.country) {
                Text("Set country")
            }
        }
        OutlinedButton(onClick = editor::scanWifi, enabled = !working) { Text("Scan networks") }
        state.scan?.let { networks ->
            if (networks.isEmpty()) Hint("No networks found.")
            else ChoiceField("Networks found", networks.map { it.ssid to "${it.ssid} · ${it.signal}%" + if (it.secured) " · secured" else " · open" },
                draft.ssid, { v -> editor.editWifi { it.copy(ssid = v) } })
        }
        DraftField(draft.ssid, { v -> editor.editWifi { it.copy(ssid = v) } }, "Network name (SSID)", maxLength = 32)
        DraftField(draft.password, { v -> editor.editWifi { it.copy(password = v) } }, "Password (blank for open networks)",
            secret = true, maxLength = 63)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                ask(Confirmation("Connect to ${draft.ssid.trim()}?",
                    "The device leaves its current Wi-Fi network. If it cannot connect it restores the previous profile, but this manager may lose contact until it is reachable again.",
                    "Connect") { editor.connectWifi() })
            }, enabled = !working && draft.ssid.isNotBlank()) { Text("Connect") }
            if (server.savedNetwork) OutlinedButton(onClick = {
                ask(Confirmation("Forget the saved Wi-Fi network?",
                    "The device disconnects from Wi-Fi. Without Ethernet it becomes unreachable until it is set up again.",
                    "Forget network") { editor.forgetWifi() })
            }, enabled = !working) { Text("Forget saved network") }
        }
    }
}

@Composable
private fun UpdatesSection(state: SettingsState, editor: SettingsSession, working: Boolean, ask: (Confirmation) -> Unit) {
    SectionCard("Updates", state.updates, SettingsPart.UPDATES, editor, false, working) { server, _ ->
        Text("Running ${server.runningVersion} · installed ${server.installedVersion} · base image ${server.baseVersion}")
        server.pendingVersion?.let { Hint("Version $it is installed and applies after a restart.") }
        server.previousVersion?.let { Hint("Rollback version: $it") }
        if (server.failed.isNotEmpty()) Hint("Rejected after failed health checks: ${server.failed.joinToString()}")
        val check = state.updateCheck
        check?.let {
            Text(if (it.available) "Version ${it.latestVersion} is available" + (it.size?.let { s -> " (${s / 1024} KiB)" } ?: "")
                else "No newer published update.")
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = editor::checkUpdate, enabled = !working) { Text("Check for updates") }
            if (check?.available == true) Button(onClick = {
                ask(Confirmation("Install ${check.latestVersion}?",
                    "The signed update is downloaded and installed. The device will not restart automatically; restart it to apply.",
                    "Install update") { editor.installUpdate() })
            }, enabled = !working) { Text("Install ${check.latestVersion}") }
        }
        Hint("Uploading an update file is only available from the device admin page.")
    }
}

@Composable
private fun SshSection(state: SettingsState, viewModel: ManagerViewModel) {
    val busy by viewModel.busy.collectAsState()
    var open by remember { mutableStateOf(false) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Development SSH keys", style = MaterialTheme.typography.titleMedium)
            TextButton(onClick = { open = !open }) { Text(if (open) "Hide SSH keys" else "Manage SSH keys") }
            if (open) SshKeysPanel(state.identity.id, state.identity.name, "keys:${state.identity.id}" in busy, viewModel)
        }
    }
}

@Composable
private fun ApiKeysNote() {
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Device API keys and password", style = MaterialTheme.typography.titleMedium)
            Text("This manager signs in with its own API key, which the device deliberately does not allow to create, list or revoke API keys. " +
                "Manage API keys and the administrator password from the device admin page, which requires the device password.")
        }
    }
}

@Composable
private fun RestartSection(state: SettingsState, editor: SettingsSession, working: Boolean, ask: (Confirmation) -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Restart", style = MaterialTheme.typography.titleMedium)
            OutlinedButton(onClick = {
                ask(Confirmation("Restart ${state.identity.name}?", "The display is unavailable while the device restarts.", "Restart") {
                    editor.reboot()
                })
            }, enabled = !working) { Text("Restart device") }
        }
    }
}

internal fun formatEpoch(seconds: Double): String =
    DateFormat.getTimeInstance(DateFormat.SHORT).format(Date((seconds * 1000).toLong()))
