package io.github.rbotha78.webdisplay.manager

import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.core.net.toUri
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ManagerScreen(viewModel: ManagerViewModel) {
    val statuses by viewModel.statuses.collectAsState()
    val found by viewModel.found.collectAsState()
    val busy by viewModel.busy.collectAsState()
    val notice by viewModel.notice.collectAsState()
    val storeError by viewModel.storeError.collectAsState()
    val managerVersion by viewModel.managerVersion.collectAsState()
    var addDevice by remember { mutableStateOf<AddRequest?>(null) }
    var confirmUpdateAll by remember { mutableStateOf(false) }
    var updateResults by remember { mutableStateOf<List<UpdateOutcome>?>(null) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    LaunchedEffect(notice) {
        notice?.let {
            snackbar.showSnackbar(it)
            viewModel.dismissNotice()
        }
    }

    MaterialTheme(colorScheme = lightColorScheme()) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = {
                        Column {
                            Text("Web Display Manager")
                            Text("Android · $managerVersion", style = MaterialTheme.typography.labelMedium)
                        }
                    },
                )
            },
            snackbarHost = { SnackbarHost(snackbar) },
        ) { padding ->
            Column(
                modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = viewModel::refresh, enabled = "refresh" !in busy) {
                        Text(if ("refresh" in busy) "Refreshing…" else "Refresh")
                    }
                    OutlinedButton(
                        onClick = { confirmUpdateAll = true },
                        enabled = statuses.isNotEmpty() && "update-all" !in busy,
                    ) { Text("Update all") }
                    Button(onClick = { addDevice = AddRequest() }) { Text("Add device") }
                }
                if (storeError != null) {
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                        Column(Modifier.padding(16.dp)) {
                            Text("Secure storage problem", fontWeight = FontWeight.Bold)
                            Text(storeError.orEmpty())
                            Text("No saved records were cleared. Reinstalling or clearing app data will forget enrolled devices.")
                        }
                    }
                }
                LazyVerticalGrid(
                    columns = GridCells.Adaptive(minSize = 340.dp),
                    modifier = Modifier.weight(1f).fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    val unpaired = found.filter { item ->
                        statuses.none { it.host == item.host && it.port == item.port } &&
                            !statuses.any { status -> status.hostname.equals(item.name, ignoreCase = true) }
                    }
                    if (unpaired.isNotEmpty()) {
                        item(span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) }) {
                            FoundDevices(unpaired) { item ->
                                addDevice = AddRequest(item.host, item.port.toString(), item.name)
                            }
                        }
                    }
                    items(statuses, key = { it.id }) { status ->
                        DeviceCard(
                            status = status,
                            deviceBusy = "device:${status.id}" in busy,
                            keyBusy = "keys:${status.id}" in busy,
                            viewModel = viewModel,
                            onReenroll = { addDevice = AddRequest(status.host, status.port.toString(), status.name, status.id) },
                        )
                    }
                    if (statuses.isEmpty() && storeError == null) {
                        item(span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) }) {
                            EmptyDevices("No devices are enrolled. Add a device to get started.")
                        }
                    }
                }
            }
        }
    }

    addDevice?.let { request ->
        AddDeviceDialog(
            request = request,
            found = found,
            viewModel = viewModel,
            onDismiss = { addDevice = null },
            onEnrolled = {
                addDevice = null
                scope.launch { snackbar.showSnackbar("Device added.") }
            },
        )
    }

    if (confirmUpdateAll) {
        ConfirmDialog(
            title = "Update all devices?",
            text = "The manager checks and installs updates on all enrolled devices. Devices will not be rebooted automatically.",
            confirm = "Check and install",
            onDismiss = { confirmUpdateAll = false },
            onConfirm = {
                confirmUpdateAll = false
                viewModel.updateAll { updateResults = it }
            },
        )
    }
    updateResults?.let { results ->
        AlertDialog(
            onDismissRequest = { updateResults = null },
            title = { Text("Update all results") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    results.forEach { result ->
                        Text("${result.name}: ${result.outcome}" +
                            (result.version?.let { " ($it)" } ?: "") +
                            (result.error?.let { " — $it" } ?: ""))
                    }
                }
            },
            confirmButton = { TextButton(onClick = { updateResults = null }) { Text("Close") } },
        )
    }
}

private data class AddRequest(
    val host: String = "",
    val port: String = ManagerRules.DEFAULT_PORT.toString(),
    val name: String = "",
    val replaceId: String? = null,
)

@Composable
private fun FoundDevices(items: List<FoundDevice>, onAdd: (FoundDevice) -> Unit) {
    Card {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Found on your network", style = MaterialTheme.typography.titleMedium)
            items.forEach { item ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("${item.name} · ${item.host}:${item.port}", Modifier.weight(1f))
                    OutlinedButton(onClick = { onAdd(item) }) { Text("Add") }
                }
            }
            Text("Devices are discovered on the local Wi-Fi network; there is no PC or cloud service.")
        }
    }
}

@Composable
private fun EmptyDevices(message: String) {
    Card {
        Column(Modifier.padding(24.dp)) {
            Text(message, style = MaterialTheme.typography.titleMedium)
            Text("Use Add device to scan the LAN or enter a device hostname or IPv4 address.")
        }
    }
}

@Composable
private fun DeviceCard(
    status: DeviceStatus,
    deviceBusy: Boolean,
    keyBusy: Boolean,
    viewModel: ManagerViewModel,
    onReenroll: () -> Unit,
) {
    val context = LocalContext.current
    var url by remember(status.id) { mutableStateOf(status.url.orEmpty()) }
    var editing by remember(status.id) { mutableStateOf(false) }
    var available by remember(status.id) { mutableStateOf<String?>(null) }
    var confirmReboot by remember { mutableStateOf(false) }
    var confirmInstall by remember { mutableStateOf(false) }
    var confirmRemove by remember { mutableStateOf(false) }
    var keysOpen by remember { mutableStateOf(false) }
    var keys by remember(status.id) { mutableStateOf<SshKeys?>(null) }
    var newKey by remember { mutableStateOf("") }
    var keyName by remember { mutableStateOf("") }
    var removeKey by remember { mutableStateOf<SshKey?>(null) }

    LaunchedEffect(status.url, editing) {
        if (!editing) url = status.url.orEmpty()
    }
    LaunchedEffect(keysOpen, status.id) {
        if (keysOpen) viewModel.debugKeys(status.id) { keys = it }
    }

    val connection = if (status.defaultRoute == null) "Unknown" else
        if (status.wifiConnected == true && status.defaultRoute.startsWith("wl")) "Wi-Fi" else status.defaultRoute
    val adminUrl = deviceAdminUrl(status.host, status.port)
    val healthy = status.online && status.error == null

    Card {
        Column(
            Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(status.name, style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
                StatePill(if (status.online) "Online" else "Offline", status.online)
            }
            Text(
                (status.addresses.takeIf { it.size > 1 }?.joinToString(", ") ?: status.host) +
                    ":${status.port}" + (status.hostname?.let { " · $it" } ?: ""),
                style = MaterialTheme.typography.bodySmall,
            )
            if (status.certificateChanged) {
                Text("The device certificate changed. Confirm its new fingerprint before trusting it again.",
                    color = MaterialTheme.colorScheme.error)
                OutlinedButton(onClick = onReenroll) { Text("Trust again") }
            }
            if (status.keyRejected) {
                Text("The device rejected this manager's key.", color = MaterialTheme.colorScheme.error)
                OutlinedButton(onClick = onReenroll) { Text("Enrol again") }
            }
            if (status.firmwareOutdated) {
                Text("Firmware ${status.version} is older than ${status.minFirmware}. Update from the device admin page.",
                    color = MaterialTheme.colorScheme.error)
            }
            if (status.error != null && !status.certificateChanged && !status.keyRejected) {
                Text(status.error, color = MaterialTheme.colorScheme.error)
            }
            if (healthy) {
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text("Firmware: ${status.version ?: "unknown"}" +
                        if (status.restartNeeded) " · ${status.installedVersion} installed; restart to apply" else "")
                    Text("Connection: ${connection ?: "Unknown"}")
                    Text("Internet: ${status.internet?.let { if (it) "Online" else "Offline" } ?: "Unknown"}")
                }
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it; editing = true },
                    label = { Text("Display address") },
                    placeholder = { Text("https://example.com") },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !deviceBusy,
                    singleLine = true,
                )
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = { viewModel.setUrl(status.id, url.trim()) { editing = false } },
                        enabled = !deviceBusy && editing,
                    ) { Text("Save address") }
                    OutlinedButton(onClick = {
                        viewModel.checkUpdate(status.id) { version ->
                            available = version
                            viewModel.reportNotice(if (version == null) "${status.name} is up to date" else "Version $version is available")
                        }
                    }, enabled = !deviceBusy) { Text("Check updates") }
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (available != null) {
                        Button(onClick = { confirmInstall = true }, enabled = !deviceBusy) {
                            Text("Install ${available}")
                        }
                    }
                    OutlinedButton(onClick = { confirmReboot = true }, enabled = !deviceBusy) { Text("Restart") }
                }

                TextButton(onClick = { keysOpen = !keysOpen }) {
                    Text(if (keysOpen) "Hide development SSH keys" else "Development SSH keys")
                }
                if (keysOpen) {
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Public keys can log in as webdebug with full root access.")
                        if (keyBusy && keys == null) CircularProgressIndicator(Modifier.size(24.dp))
                        keys?.let { data ->
                            if (!data.enabled) Text("SSH key management is disabled on this device.")
                            else if (data.keys.isEmpty()) Text("No SSH keys are authorised.")
                            else data.keys.forEach { item ->
                                Column {
                                    Text(item.comment.ifBlank { "(no name)" }, fontWeight = FontWeight.SemiBold)
                                    Text("${item.type} · ${item.fingerprint}", style = MaterialTheme.typography.bodySmall)
                                    TextButton(onClick = { removeKey = item }, enabled = !keyBusy) { Text("Remove key") }
                                }
                            }
                        }
                        if (keys?.enabled == true) {
                            OutlinedTextField(
                                value = newKey,
                                onValueChange = { newKey = it },
                                label = { Text("Public key") },
                                placeholder = { Text("ssh-ed25519 AAAA… name") },
                                modifier = Modifier.fillMaxWidth(),
                                minLines = 2,
                            )
                            OutlinedTextField(
                                value = keyName,
                                onValueChange = { if (it.length <= 40) keyName = it },
                                label = { Text("Name (optional)") },
                                modifier = Modifier.fillMaxWidth(),
                                singleLine = true,
                            )
                            OutlinedButton(onClick = {
                                viewModel.addDebugKey(status.id, newKey.trim(), keyName.trim()) {
                                    keys = it
                                    newKey = ""
                                    keyName = ""
                                }
                            }, enabled = !keyBusy && newKey.isNotBlank()) { Text("Add SSH key") }
                        }
                    }
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (healthy) {
                    OutlinedButton(onClick = {
                        viewModel.adminUrl(status.id) { url ->
                            runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, url.toUri())) }
                                .onFailure { viewModel.reportNotice("No browser is available to open the admin page.") }
                        }
                    }, enabled = !deviceBusy) { Text("Open admin page") }
                }
                OutlinedButton(onClick = {
                    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, adminUrl.toUri())) }
                        .onFailure { viewModel.reportNotice("No browser is available to open the admin page.") }
                }) { Text(if (healthy) "Open without login" else "Open admin page") }
                TextButton(onClick = { confirmRemove = true }, enabled = !deviceBusy) { Text("Remove") }
            }
            if (healthy) {
                Text("Automatic admin login opens your browser. The browser independently verifies TLS; do not bypass certificate warnings unless you have checked the device identity.",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
    }

    if (confirmReboot) ConfirmDialog(
        title = "Restart ${status.name}?",
        text = "The display will be unavailable while the device restarts.",
        confirm = "Restart",
        onDismiss = { confirmReboot = false },
        onConfirm = { confirmReboot = false; viewModel.reboot(status.id) },
    )
    if (confirmInstall) ConfirmDialog(
        title = "Install update on ${status.name}?",
        text = "The signed update will be installed. The device will not reboot automatically; restart it separately to apply the update.",
        confirm = "Install update",
        onDismiss = { confirmInstall = false },
        onConfirm = {
            confirmInstall = false
            viewModel.installUpdate(status.id) {
                available = null
                viewModel.reportNotice("$it installed on ${status.name}; restart to apply it.")
            }
        },
    )
    if (confirmRemove) ConfirmDialog(
        title = "Remove ${status.name}?",
        text = "This only removes the saved device from this Android manager. It does NOT revoke the API key on the device. Revoke that key separately in the device admin page.",
        confirm = "Remove locally",
        onDismiss = { confirmRemove = false },
        onConfirm = { confirmRemove = false; viewModel.forget(status.id) },
    )
    removeKey?.let { item ->
        ConfirmDialog(
            title = "Remove SSH key?",
            text = "Remove ${item.comment.ifBlank { item.fingerprint }} from ${status.name}?" +
                if (keys?.keys?.size == 1) " This is the last key; nobody will be able to log in over SSH." else "",
            confirm = "Remove key",
            onDismiss = { removeKey = null },
            onConfirm = {
                removeKey = null
                viewModel.removeDebugKey(status.id, item.id) { keys = it }
            },
        )
    }
}

@Composable
private fun StatePill(text: String, positive: Boolean) {
    Surface(
        color = if (positive) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.errorContainer,
        shape = MaterialTheme.shapes.small,
    ) { Text(text, Modifier.padding(horizontal = 10.dp, vertical = 5.dp)) }
}

@Composable
private fun ConfirmDialog(
    title: String,
    text: String,
    confirm: String,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(text) },
        confirmButton = { Button(onClick = onConfirm) { Text(confirm) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun AddDeviceDialog(
    request: AddRequest,
    found: List<FoundDevice>,
    viewModel: ManagerViewModel,
    onDismiss: () -> Unit,
    onEnrolled: () -> Unit,
) {
    var host by remember(request.host) { mutableStateOf(request.host) }
    var port by remember(request.port) { mutableStateOf(request.port) }
    var name by remember(request.name) { mutableStateOf(request.name) }
    var pin by remember { mutableStateOf<String?>(null) }
    var password by remember { mutableStateOf("") }
    val dialogActive = remember { booleanArrayOf(true) }
    DisposableEffect(Unit) {
        onDispose { dialogActive[0] = false }
    }
    val busy by viewModel.busy.collectAsState()
    val targetBusy = "fingerprint" in busy || "enroll" in busy
    val foundElsewhere = found.distinctBy { it.host to it.port }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (request.replaceId == null) "Add a device" else "Trust device again") },
        text = {
            Column(
                Modifier.fillMaxWidth().verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                if (foundElsewhere.isNotEmpty()) {
                    Text("Found on the local network", style = MaterialTheme.typography.titleSmall)
                    foundElsewhere.forEach { item ->
                        TextButton(enabled = !targetBusy, onClick = {
                            host = item.host
                            port = item.port.toString()
                            name = item.name
                            pin = null
                        }) { Text("${item.name} · ${item.host}:${item.port}") }
                    }
                }
                OutlinedTextField(
                    value = host, onValueChange = { host = it; pin = null },
                    label = { Text("Hostname or IPv4 address") }, singleLine = true,
                    enabled = !targetBusy,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = port, onValueChange = { port = it; pin = null },
                    label = { Text("HTTPS port") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    enabled = !targetBusy,
                    singleLine = true, modifier = Modifier.fillMaxWidth(),
                )
                if (pin == null) {
                    Button(
                        onClick = { viewModel.fingerprint(host, port) { (address, value) ->
                            if (dialogActive[0]) {
                                host = address.host; port = address.port.toString(); pin = value
                            }
                        } },
                        enabled = !targetBusy && host.isNotBlank() && port.isNotBlank(),
                    ) { Text(if ("fingerprint" in busy) "Looking up…" else "Look up certificate") }
                } else {
                    Text("Compare this SHA-256 fingerprint with the one displayed by the device. Only continue if they match.")
                    Text(pin.orEmpty(), fontWeight = FontWeight.Bold)
                    OutlinedTextField(
                        value = name, onValueChange = { if (it.length <= 40) name = it },
                        label = { Text("Device name") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = password, onValueChange = { password = it },
                        label = { Text("Device administrator password") },
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        singleLine = true, modifier = Modifier.fillMaxWidth(),
                    )
                    Text("The password is used once to create an API key, then discarded. Only the encrypted API key is stored on this device.")
                }
            }
        },
        confirmButton = {
            if (pin != null) Button(
                enabled = "enroll" !in busy && password.isNotEmpty(),
                onClick = {
                    val passwordToSend = password
                    password = ""
                    viewModel.enroll(host, port, pin.orEmpty(), passwordToSend, name, request.replaceId) {
                        if (dialogActive[0]) onEnrolled()
                    }
                },
            ) { Text(if ("enroll" in busy) "Adding…" else "Trust and add") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

private fun deviceAdminUrl(host: String, port: Int): String {
    val formatted = if (host.contains(':') && !host.startsWith("[")) "[$host]" else host
    return "https://$formatted:$port/"
}
