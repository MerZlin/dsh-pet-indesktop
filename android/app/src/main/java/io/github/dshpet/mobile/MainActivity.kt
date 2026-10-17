package io.github.dshpet.mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.OpenableColumns
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import io.github.dshpet.mobile.ui.AppearanceSection
import io.github.dshpet.mobile.ui.ClickSoundSection
import io.github.dshpet.mobile.ui.CollisionSection
import io.github.dshpet.mobile.ui.CompanionSection
import io.github.dshpet.mobile.ui.DshLinkSection
import io.github.dshpet.mobile.ui.SectionTitle
import io.github.dshpet.mobile.ui.SpeechSection
import io.github.dshpet.mobile.ui.WanderSection

class MainActivity : ComponentActivity() {
    private lateinit var preferences: PetPreferences
    private var overlayAllowed by mutableStateOf(false)
    private var options by mutableStateOf(PetOptions())
    private var audioName by mutableStateOf<String?>(null)
    private var builtinCount by mutableStateOf(0)
    private var preview: ClickAudio? = null
    private var previewState by mutableStateOf(ClickAudio.State.IDLE)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        preferences = PetPreferences(this)
        options = preferences.options()
        audioName = resolveAudioName(options.clickSoundUri)
        builtinCount = runCatching { AssetCatalog.load(assets).phrases.size }.getOrDefault(0)
        setContent { PetSettings() }
    }

    override fun onResume() {
        super.onResume()
        overlayAllowed = Settings.canDrawOverlays(this)
        if (::preferences.isInitialized) {
            options = preferences.options()
            audioName = resolveAudioName(options.clickSoundUri)
        }
    }

    override fun onStop() {
        preview?.close()
        preview = null
        super.onStop()
    }

    private fun previewSound() {
        preview?.close()
        preview = ClickAudio({ uri, changed -> AndroidClickOutput(this, uri, changed) },
            SystemClock::elapsedRealtime, { previewState = it }).also {
            it.configure(options.clickSoundUri, options.clickSoundVolume)
            it.press()
        }
    }

    /** The picked file's own name, so the row never shows an opaque URI. */
    private fun resolveAudioName(uri: String?): String? {
        if (uri == null) return null
        return runCatching {
            contentResolver.query(Uri.parse(uri), arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
                ?.use { cursor ->
                    val column = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (column >= 0 && cursor.moveToFirst()) cursor.getString(column) else null
                }
        }.getOrNull()
    }

    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun PetSettings() {
        val runtime by PetRuntime.state.collectAsState()
        val audioError by PetRuntime.audioError.collectAsState()
        val context = LocalContext.current
        val sizeLabel = stringResource(R.string.pet_size)
        var resetConfirmation by remember { mutableStateOf(false) }
        val permissionRequest = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
            PetOverlayService.command(context, PetOverlayService.START)
        }
        val audioPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) {
                runCatching {
                    context.contentResolver.takePersistableUriPermission(uri,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                save(options.copy(clickSoundUri = uri.toString()))
                audioName = resolveAudioName(uri.toString())
            }
        }
        val scheme = if (isSystemInDarkTheme()) darkColorScheme(
            primary = Color(0xFF76D5BD), onPrimary = Color(0xFF00382F),
            background = Color(0xFF191C1C), surface = Color(0xFF191C1C),
            secondaryContainer = Color(0xFF374944), onSecondaryContainer = Color(0xFFDDE5E1),
            surfaceVariant = Color(0xFF3F4844), outlineVariant = Color(0xFF56615B),
            tertiary = Color(0xFFFFB4A2)) else lightColorScheme(
            primary = Color(0xFF006B5C), background = Color(0xFFF8FAF9), surface = Color(0xFFF8FAF9),
            secondaryContainer = Color(0xFFE1E9E5), onSecondaryContainer = Color(0xFF293A34),
            surfaceVariant = Color(0xFFE7ECE9), outlineVariant = Color(0xFFCFD8D2),
            tertiary = Color(0xFFAD4C38))
        MaterialTheme(colorScheme = scheme) {
            Surface(modifier = Modifier.fillMaxSize()) {
                Column(Modifier.fillMaxSize().safeDrawingPadding().verticalScroll(rememberScrollState())
                    .padding(horizontal = 24.dp, vertical = 20.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                        Image(painterResource(R.drawable.pet_icon), null, Modifier.size(96.dp))
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(stringResource(R.string.app_name), style = MaterialTheme.typography.headlineSmall)
                            Text(stringResource(statusText(runtime)),
                                color = MaterialTheme.colorScheme.primary,
                                style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                    runtime.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                    if (!overlayAllowed) {
                        Text(stringResource(R.string.permission_required))
                        Button(onClick = {
                            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                Uri.parse("package:$packageName")))
                        }, modifier = Modifier.fillMaxWidth()) { Text(stringResource(R.string.grant_overlay)) }
                    }
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                        maxItemsInEachRow = if (LocalDensity.current.fontScale > 1.3f) 1 else 2) {
                        Button(onClick = {
                            if (runtime.running) PetOverlayService.command(context,
                                if (runtime.paused) PetOverlayService.RESUME else PetOverlayService.PAUSE)
                            else if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(
                                    context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                                permissionRequest.launch(Manifest.permission.POST_NOTIFICATIONS)
                            else PetOverlayService.command(context, PetOverlayService.START)
                        }, enabled = overlayAllowed, modifier = Modifier.weight(1f)) {
                            Icon(if (runtime.running && !runtime.paused) Icons.Default.Pause else Icons.Default.PlayArrow, null)
                            Spacer(Modifier.width(8.dp))
                            Text(stringResource(if (!runtime.running) R.string.start else
                                if (runtime.paused) R.string.resume else R.string.pause))
                        }
                        OutlinedButton(onClick = { PetOverlayService.command(context, PetOverlayService.STOP) },
                            enabled = runtime.running, modifier = Modifier.weight(1f)) {
                            Icon(Icons.Default.Stop, null)
                            Spacer(Modifier.width(8.dp))
                            Text(stringResource(R.string.stop))
                        }
                    }
                    HorizontalDivider()
                    SectionTitle(R.string.appearance)
                    AppearanceSection(options) { save(it) }
                    HorizontalDivider()
                    SectionTitle(R.string.interaction)
                    WanderSection(options) { save(it) }
                    SpeechSection(options, builtinCount) { save(it) }
                    ClickSoundSection(options, audioName, { save(it) },
                        onPick = { audioPicker.launch(arrayOf("audio/*")) },
                        onPreview = ::previewSound,
                        loading = previewState == ClickAudio.State.LOADING,
                        failed = previewState == ClickAudio.State.ERROR || audioError)
                    CompanionSection(options, runtime.companions) { save(it) }
                    CollisionSection(options) { save(it) }
                    DshLinkSection(options) { save(it) }
                    HorizontalDivider()
                    TextButton(onClick = { resetConfirmation = true }) {
                        Icon(Icons.Default.Refresh, null)
                        Spacer(Modifier.width(8.dp))
                        Text(stringResource(R.string.reset))
                    }
                    Text(stringResource(R.string.version, BuildConfig.VERSION_NAME), color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall)
                }
                if (resetConfirmation) AlertDialog(onDismissRequest = { resetConfirmation = false },
                    title = { Text(stringResource(R.string.reset)) },
                    text = { Text(stringResource(R.string.reset_message)) },
                    confirmButton = { TextButton(onClick = {
                        preferences.reset()
                        options = preferences.options()
                        preview?.close()
                        preview = null
                        audioName = null
                        if (runtime.running) PetOverlayService.command(context, PetOverlayService.RESET)
                        resetConfirmation = false
                    }) { Text(stringResource(R.string.reset)) } },
                    dismissButton = { TextButton(onClick = { resetConfirmation = false }) {
                        Text(stringResource(R.string.cancel))
                    } })
            }
        }
    }

    private fun statusText(runtime: RuntimeState): Int = when {
        !runtime.running -> R.string.not_running
        runtime.paused -> R.string.paused
        else -> R.string.running
    }

    /** One writer for preferences: the editor state and the store never diverge. */
    private fun save(next: PetOptions) {
        if (!next.clickSound || next.clickSoundUri != options.clickSoundUri) {
            preview?.close()
            preview = null
        } else preview?.configure(next.clickSoundUri, next.clickSoundVolume)
        options = next
        preferences.save(next)
    }
}
