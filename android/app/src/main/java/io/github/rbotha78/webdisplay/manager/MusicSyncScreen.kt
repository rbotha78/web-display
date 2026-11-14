package io.github.rbotha78.webdisplay.manager

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun MusicSyncScreen(
    devices: List<DeviceStatus>, busy: Boolean, results: List<MusicSyncOutcome>,
    onClose: () -> Unit, onRefresh: () -> Unit,
    onCreate: (String, Set<String>, Boolean) -> Unit,
    onDissolve: (MusicSyncGroup) -> Unit, onRemove: (DeviceStatus) -> Unit,
    modifier: Modifier = Modifier,
) {
    var creating by remember { mutableStateOf(false) }
    var leaderId by remember { mutableStateOf<String?>(null) }
    var followerIds by remember { mutableStateOf(emptySet<String>()) }
    var confirmCreate by remember { mutableStateOf(false) }
    var dissolve by remember { mutableStateOf<MusicSyncGroup?>(null) }
    var remove by remember { mutableStateOf<DeviceStatus?>(null) }
    val groups = MusicSyncRules.groups(devices)
    val eligibleIds = devices.filter { MusicSyncRules.unavailableReason(it) == null }.map { it.id }.toSet()
    val validSelection = leaderId in eligibleIds && followerIds.isNotEmpty() && followerIds.all { it in eligibleIds } &&
        leaderId !in followerIds
    val oldGroups = MusicSyncRules.reassignedGroups(devices, followerIds + listOfNotNull(leaderId))

    LazyColumn(modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = { if (creating) creating = false else onClose() }) {
                    Text(if (creating) "Back to groups" else "Back to devices")
                }
                TextButton(onClick = onRefresh, enabled = !busy) { Text("Refresh") }
            }
            Text(if (creating) "Create music sync group" else "Music sync groups", style = MaterialTheme.typography.headlineSmall)
            Text(MusicSyncRules.HELP, style = MaterialTheme.typography.bodySmall)
            if (busy) LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 8.dp))
        }
        if (results.isNotEmpty()) {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("Per-display results", style = MaterialTheme.typography.titleMedium)
                        results.forEach { Text("${it.name}: ${it.outcome}") }
                    }
                }
            }
        }
        if (creating) {
            item { Text("1. Choose the leader", style = MaterialTheme.typography.titleMedium) }
            items(devices, key = { "leader:${it.id}" }) { device ->
                SyncChoice(device, selected = leaderId == device.id, enabled = !busy, radio = true) {
                    leaderId = device.id
                    followerIds = followerIds - device.id
                }
            }
            item { Text("2. Choose one or more followers", style = MaterialTheme.typography.titleMedium) }
            items(devices, key = { "follower:${it.id}" }) { device ->
                SyncChoice(device, selected = device.id in followerIds, enabled = !busy,
                    extraReason = "Chosen leader".takeIf { leaderId == device.id }) {
                    followerIds = if (device.id in followerIds) followerIds - device.id else followerIds + device.id
                }
            }
            item {
                Button(onClick = { confirmCreate = true }, enabled = validSelection && !busy) { Text("Review group") }
                Text("Unsupported and offline displays cannot be selected. Update firmware or refresh after reconnecting.",
                    style = MaterialTheme.typography.bodySmall)
            }
        } else {
            item {
                Button(onClick = { creating = true }, enabled = !busy) { Text("Create group") }
                Text("Groups show enrolled displays with a known sync state. Offline or unknown displays may be missing. " +
                    "Leader counts include followers seen in the last 60 seconds.", style = MaterialTheme.typography.bodySmall)
            }
            if (groups.isEmpty()) item { Text("No known music sync groups. Refresh to check enrolled displays.") }
            items(groups, key = { it.id }) { group ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(group.name, style = MaterialTheme.typography.titleMedium)
                        if (group.leaders.isEmpty()) Text("Leader not enrolled or its sync state is unavailable.")
                        group.leaders.forEach {
                            Text("Leader: ${it.name} · ${it.musicSync.members.size} recently seen followers")
                        }
                        group.followers.forEach { follower ->
                            Column {
                                Text("Follower: ${follower.name}" +
                                    (MusicSyncRules.followerError(follower.musicSync)?.let { " — $it" } ?: ""))
                                TextButton(onClick = { remove = follower }, enabled = !busy) { Text("Remove follower") }
                            }
                        }
                        group.leaders.flatMap { it.musicSync.members }.distinctBy { it.name }
                            .filter { member -> group.followers.none { it.name == member.name } }.forEach {
                                Text("Recently seen: ${it.name}" + if (it.listening) " · listening" else " · not listening")
                            }
                        TextButton(onClick = { dissolve = group }, enabled = !busy) { Text("Dissolve") }
                    }
                }
            }
        }
        item { Text("", Modifier.padding(bottom = 8.dp)) }
    }

    if (confirmCreate) {
        AlertDialog(
            onDismissRequest = { confirmCreate = false },
            title = { Text("Create music sync group?") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Leader: ${devices.firstOrNull { it.id == leaderId }?.name.orEmpty()}")
                    Text("Followers: ${devices.filter { it.id in followerIds }.joinToString { it.name }}")
                    Text("The leader is configured first. If it fails, no followers are changed.")
                    if (oldGroups.isNotEmpty()) {
                        Text("Selected displays already belong to a group. Creating this group replaces their existing roles.")
                        oldGroups.forEach { group ->
                            val selectedLeaders = group.leaders.filter { it.id == leaderId || it.id in followerIds }
                            val seenFollowers = group.followers.isNotEmpty() || group.leaders.any { it.musicSync.members.isNotEmpty() }
                            if (selectedLeaders.isNotEmpty() && seenFollowers) {
                                Text("${selectedLeaders.joinToString { it.name }} currently leads followers. " +
                                    "Those followers will lose their leader unless the old group is dissolved first.",
                                    color = MaterialTheme.colorScheme.error)
                            }
                            if (group.leaders.isEmpty()) Text("${group.name}: the old leader cannot be cleared by this manager.")
                        }
                        Text("Dissolve first clears all known enrolled members of the selected old groups, including unselected displays. " +
                            "Offline or unenrolled members must be cleared separately. Any failure aborts creation.")
                    }
                }
            },
            confirmButton = {
                Column {
                    if (oldGroups.isNotEmpty()) TextButton(onClick = {
                        confirmCreate = false; creating = false
                        onCreate(requireNotNull(leaderId), followerIds, true)
                    }, enabled = validSelection && !busy) { Text("Dissolve old groups first") }
                    TextButton(onClick = {
                        confirmCreate = false; creating = false
                        onCreate(requireNotNull(leaderId), followerIds, false)
                    }, enabled = validSelection && !busy) {
                        Text(if (oldGroups.isEmpty()) "Create group" else "Reassign selected displays")
                    }
                }
            },
            dismissButton = { TextButton(onClick = { confirmCreate = false }) { Text("Cancel") } },
        )
    }
    dissolve?.let { group ->
        AlertDialog(
            onDismissRequest = { dissolve = null }, title = { Text("Dissolve ${group.name}?") },
            text = { Text("Clear music sync on known enrolled followers first, then the leader. " +
                "All members return to their own music settings. Unenrolled displays and those with unavailable sync state " +
                "cannot be cleared here; remove their groups separately. Per-display failures will be reported.") },
            confirmButton = { TextButton(onClick = { dissolve = null; onDissolve(group) }, enabled = !busy) { Text("Dissolve") } },
            dismissButton = { TextButton(onClick = { dissolve = null }) { Text("Cancel") } },
        )
    }
    remove?.let { follower ->
        AlertDialog(
            onDismissRequest = { remove = null }, title = { Text("Remove ${follower.name}?") },
            text = { Text("Only this follower leaves the group and returns to its own music settings.") },
            confirmButton = { TextButton(onClick = { remove = null; onRemove(follower) }, enabled = !busy) { Text("Remove follower") } },
            dismissButton = { TextButton(onClick = { remove = null }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun SyncChoice(
    device: DeviceStatus, selected: Boolean, enabled: Boolean, radio: Boolean = false,
    extraReason: String? = null, onSelect: () -> Unit,
) {
    val reason = MusicSyncRules.unavailableReason(device) ?: extraReason
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        if (radio) RadioButton(selected, onClick = onSelect, enabled = enabled && reason == null)
        else Checkbox(selected, onCheckedChange = { onSelect() }, enabled = enabled && reason == null)
        Column(Modifier.weight(1f)) {
            Text(device.name)
            Text(reason ?: MusicSyncRules.summary(device.musicSync), style = MaterialTheme.typography.bodySmall)
        }
    }
}
