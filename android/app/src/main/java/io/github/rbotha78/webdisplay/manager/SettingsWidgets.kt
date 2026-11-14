package io.github.rbotha78.webdisplay.manager

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

/** Confirmation requested by a settings section; shown by the settings screen. */
data class Confirmation(val title: String, val text: String, val confirm: String, val action: () -> Unit)

/**
 * Text field with local editing state so typing never races the session flow; external
 * resets (save, discard, reload) are adopted when the session value changes.
 */
@Composable
fun DraftField(
    value: String,
    onChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    secret: Boolean = false,
    number: Boolean = false,
    singleLine: Boolean = true,
    placeholder: String? = null,
    maxLength: Int? = null,
) {
    var text by remember { mutableStateOf(value) }
    LaunchedEffect(value) { if (value != text) text = value }
    OutlinedTextField(
        value = text,
        onValueChange = { next ->
            if (maxLength == null || next.length <= maxLength) {
                text = next
                onChange(next)
            }
        },
        label = { Text(label) },
        placeholder = placeholder?.let { { Text(it) } },
        enabled = enabled,
        singleLine = singleLine,
        minLines = if (singleLine) 1 else 3,
        visualTransformation = if (secret) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
        keyboardOptions = KeyboardOptions(
            keyboardType = when {
                secret -> KeyboardType.Password
                number -> KeyboardType.Number
                else -> KeyboardType.Text
            },
            autoCorrectEnabled = !secret,
        ),
        modifier = modifier.fillMaxWidth(),
    )
}

@Composable
fun ChoiceField(
    label: String,
    options: List<Pair<String, String>>,
    selected: String,
    onSelect: (String) -> Unit,
    enabled: Boolean = true,
) {
    var open by remember { mutableStateOf(false) }
    val current = options.firstOrNull { it.first == selected }?.second ?: selected.ifEmpty { "None selected" }
    Column(Modifier.fillMaxWidth()) {
        Text(label, style = MaterialTheme.typography.labelMedium)
        Box {
            OutlinedButton(onClick = { open = true }, enabled = enabled, modifier = Modifier.fillMaxWidth()) {
                Text(current, modifier = Modifier.weight(1f))
                Text("▾")
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                options.forEach { (value, text) ->
                    DropdownMenuItem(text = { Text(text) }, onClick = { open = false; onSelect(value) })
                }
            }
        }
    }
}

@Composable
fun CheckRow(text: String, checked: Boolean, onChange: (Boolean) -> Unit, enabled: Boolean = true, switch: Boolean = false) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        if (switch) Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
        else Checkbox(checked = checked, onCheckedChange = onChange, enabled = enabled)
        Text(text, Modifier.padding(start = 8.dp).weight(1f))
    }
}

@Composable
fun Hint(text: String) = Text(text, style = MaterialTheme.typography.bodySmall)

@Composable
fun ErrorText(text: String) = Text(text, color = MaterialTheme.colorScheme.error)

/**
 * Card wrapper with explicit loading, load-failure, unsupported, conflict and dirty states.
 * [content] only runs when the device returned valid data.
 */
@Composable
fun <S, D> SectionCard(
    title: String,
    section: Section<S, D>,
    part: SettingsPart,
    editor: SettingsSession,
    dirty: Boolean,
    working: Boolean,
    onConflictOverwrite: (() -> Unit)? = null,
    content: @Composable (server: S, draft: D?) -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                if (dirty) Text("Unsaved", color = MaterialTheme.colorScheme.tertiary, fontWeight = FontWeight.SemiBold)
                if (section.loading) CircularProgressIndicator(Modifier.padding(start = 8.dp).size(20.dp))
            }
            val server = section.server
            when {
                server == null && section.loading -> Text("Loading from the device…")
                server == null && section.unsupported -> Hint(section.error ?: SettingsSession.UNSUPPORTED)
                server == null -> {
                    ErrorText(section.error ?: "Not loaded")
                    OutlinedButton(onClick = { editor.reload(part) }, enabled = !section.loading) { Text("Retry") }
                }
                else -> {
                    content(server, section.draft)
                    section.error?.let { ErrorText(it) }
                    if (section.conflict) FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        onConflictOverwrite?.let { Button(onClick = it, enabled = !working) { Text("Overwrite device") } }
                        OutlinedButton(onClick = { editor.reload(part) }, enabled = !working) { Text("Discard draft and reload") }
                    }
                    if (dirty && !section.conflict) {
                        TextButton(onClick = { editor.discard(part) }, enabled = !working) { Text("Discard changes") }
                    }
                }
            }
        }
    }
}
