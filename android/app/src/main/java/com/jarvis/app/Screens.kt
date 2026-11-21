package com.jarvis.app

import android.content.Intent
import android.provider.Settings
import android.speech.tts.TextToSpeech
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

@Composable
fun JarvisRoot(settings: SettingsStore) {
    var tab by remember { mutableStateOf(if (settings.configured) 0 else 2) }
    Scaffold(
        containerColor = Bg,
        bottomBar = { JarvisNav(tab) { tab = it } },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (tab) {
                0 -> HomeScreen(settings, onChat = { tab = 1 }, onSettings = { tab = 2 })
                1 -> ChatScreen(settings)
                else -> SettingsScreen(settings, onSaved = { tab = 0 })
            }
        }
    }
}

@Composable
private fun JarvisNav(tab: Int, onSelect: (Int) -> Unit) {
    Surface(color = Panel) {
        Row(
            Modifier.fillMaxWidth().navigationBarsPadding().padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceEvenly,
        ) {
            NavItem("HUD", tab == 0) { onSelect(0) }
            NavItem("CHAT", tab == 1) { onSelect(1) }
            NavItem("CONFIG", tab == 2) { onSelect(2) }
        }
    }
}

@Composable
private fun NavItem(label: String, active: Boolean, onClick: () -> Unit) {
    Column(
        Modifier.clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick)
            .padding(horizontal = 22.dp, vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            Modifier.size(if (active) 16.dp else 5.dp).clip(CircleShape)
                .background(if (active) Cyan else TextDim.copy(alpha = 0.35f))
        )
        Spacer(Modifier.height(5.dp))
        Text(label, color = if (active) TextMain else TextDim, fontSize = 10.sp, letterSpacing = 2.sp)
    }
}

// --------------------------------------------------------------------------
// HUD
// --------------------------------------------------------------------------
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HomeScreen(settings: SettingsStore, onChat: () -> Unit, onSettings: () -> Unit) {
    val context = LocalContext.current
    var status by remember { mutableStateOf<JSONObject?>(null) }
    var weather by remember { mutableStateOf<JSONObject?>(null) }

    LaunchedEffect(settings.host, settings.httpPort, settings.token) {
        if (!settings.configured) return@LaunchedEffect
        val api = Api(settings)
        while (true) {
            status = api.status()
            weather = api.weather()
            delay(15_000)
        }
    }

    val reachable = status != null
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Column {
                Text("J.A.R.V.I.S.", color = TextMain, fontSize = 24.sp, fontWeight = FontWeight.Bold, letterSpacing = 5.sp)
                Text(
                    if (reachable) "GUARDIAN LINK ACTIVE" else if (settings.configured) "LINK OFFLINE" else "NOT PAIRED",
                    color = if (reachable) Ok else if (settings.configured) Amber else TextDim,
                    fontSize = 9.sp, letterSpacing = 2.sp,
                )
            }
            Text(
                "CONFIG",
                color = TextDim, fontSize = 10.sp, letterSpacing = 2.sp,
                modifier = Modifier.clip(RoundedCornerShape(8.dp)).clickable(onClick = onSettings)
                    .border(1.dp, Color(0xFF1E2C42), RoundedCornerShape(8.dp)).padding(horizontal = 12.dp, vertical = 7.dp),
            )
        }

        Spacer(Modifier.height(10.dp))
        if (settings.configured) {
            ReactorOrb(active = reachable, modifier = Modifier.padding(vertical = 4.dp))
        } else {
            Box(Modifier.height(180.dp), contentAlignment = Alignment.Center) {
                Text("OFFLINE", color = TextDim, fontSize = 12.sp, letterSpacing = 6.sp)
            }
        }

        Clock()
        Spacer(Modifier.height(16.dp))

        if (!settings.configured) {
            SetupCard(onSettings)
        } else {
            WeatherCard(weather)
            Spacer(Modifier.height(10.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Chip("model", status?.optString("model").orEmpty().ifBlank { "—" })
                Chip("autonomy", if (status?.optBoolean("autonomy") == true) "armed" else "paused", Amber)
                Chip("desktop", if (status?.optBoolean("desktop") == true) "linked" else "off", Violet)
            }
            Spacer(Modifier.height(20.dp))
            ActionCard("CALL JARVIS", "Duplex live voice link", Cyan) {
                context.startActivity(
                    Intent(context, CallActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            }
            Spacer(Modifier.height(10.dp))
            ActionCard("OPEN CHAT", "Text, tools and multi-step tasks", Amber, onClick = onChat)
        }
    }
}

@Composable
private fun Clock() {
    var now by remember { mutableStateOf(LocalDateTime.now()) }
    LaunchedEffect(Unit) {
        while (true) {
            now = LocalDateTime.now()
            delay(1_000)
        }
    }
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            now.format(DateTimeFormatter.ofPattern("HH:mm")),
            color = TextMain, fontSize = 46.sp, fontWeight = FontWeight.Light, fontFamily = FontFamily.Monospace,
        )
        Text(
            now.format(DateTimeFormatter.ofPattern("EEEE · d MMMM", Locale.ENGLISH)).uppercase(Locale.ENGLISH),
            color = TextDim, fontSize = 11.sp, letterSpacing = 3.sp,
        )
    }
}

@Composable
private fun ReactorOrb(active: Boolean, modifier: Modifier = Modifier, size: androidx.compose.ui.unit.Dp = 230.dp) {
    val transition = rememberInfiniteTransition(label = "orb")
    val pulse by transition.animateFloat(
        0.9f, 1.06f,
        animationSpec = infiniteRepeatable(tween(1800, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "pulse",
    )
    val spin by transition.animateFloat(
        0f, 360f,
        animationSpec = infiniteRepeatable(tween(12_000, easing = LinearEasing)),
        label = "spin",
    )
    val glow = if (active) Cyan else Color(0xFF38506B)
    Canvas(modifier.size(size)) {
        val radius = this.size.minDimension / 2f
        drawCircle(
            Brush.radialGradient(listOf(glow.copy(alpha = 0.30f), Color.Transparent), center = center, radius = radius),
            radius, center,
        )
        drawCircle(glow.copy(alpha = 0.45f), radius * 0.33f, center, style = Stroke(1.5f))
        drawCircle(glow.copy(alpha = 0.20f), radius * 0.56f, center, style = Stroke(1f))
        drawCircle(glow.copy(alpha = 0.10f), radius * 0.82f, center, style = Stroke(1f))
        rotate(spin, center) {
            val topLeft = Offset(center.x - radius * 0.75f, center.y - radius * 0.75f)
            val arcSize = Size(radius * 1.5f, radius * 1.5f)
            drawArc(glow.copy(alpha = 0.9f), -40f, 100f, false, topLeft, arcSize, style = Stroke(3f, cap = StrokeCap.Round))
            drawArc(Amber.copy(alpha = 0.8f), 150f, 70f, false, topLeft, arcSize, style = Stroke(2f, cap = StrokeCap.Round))
        }
        rotate(-spin * 1.4f, center) {
            drawArc(
                Violet.copy(alpha = 0.5f), 40f, 40f, false,
                Offset(center.x - radius * 0.9f, center.y - radius * 0.9f),
                Size(radius * 1.8f, radius * 1.8f),
                style = Stroke(1.5f, cap = StrokeCap.Round),
            )
        }
        drawCircle(
            Brush.radialGradient(listOf(Color.White.copy(alpha = 0.95f), glow), center = center, radius = radius * 0.3f * pulse),
            radius * 0.20f * pulse, center,
        )
    }
}

@Composable
private fun Chip(label: String, value: String, tone: Color = Cyan) {
    Surface(shape = RoundedCornerShape(50), color = PanelHi, border = BorderStroke(1.dp, tone.copy(alpha = 0.35f))) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(tone))
            Spacer(Modifier.width(7.dp))
            Text(label.uppercase(Locale.US), color = TextDim, fontSize = 8.sp, letterSpacing = 1.sp)
            Spacer(Modifier.width(5.dp))
            Text(value, color = TextMain, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
private fun WeatherCard(weather: JSONObject?) {
    val current = weather?.optJSONObject("current")
    Surface(
        Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp), color = Panel,
        border = BorderStroke(1.dp, Color(0xFF152337)),
    ) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    current?.optString("city").orEmpty().ifBlank { "WEATHER" }.uppercase(Locale.US),
                    color = TextDim, fontSize = 9.sp, letterSpacing = 2.sp,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    if (current != null) current.optString("desc").replaceFirstChar { it.uppercase() } else "Waiting for the PC link",
                    color = TextMain, fontSize = 15.sp,
                )
                if (current != null) {
                    Text(
                        "feels ${current.optDouble("feels").toInt()}° · humidity ${current.optInt("humidity")}%",
                        color = TextDim, fontSize = 11.sp,
                    )
                }
            }
            Text(
                if (current != null) "${current.optDouble("temp").toInt()}°" else "—",
                color = Amber, fontSize = 32.sp, fontWeight = FontWeight.Light,
            )
        }
    }
}

@Composable
private fun ActionCard(title: String, subtitle: String, tone: Color, onClick: () -> Unit) {
    Surface(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).clickable(onClick = onClick),
        shape = RoundedCornerShape(16.dp), color = Panel,
        border = BorderStroke(1.dp, tone.copy(alpha = 0.45f)),
    ) {
        Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(9.dp).clip(CircleShape).background(tone))
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(title, color = TextMain, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 2.sp)
                Text(subtitle, color = TextDim, fontSize = 11.sp)
            }
            Text("›", color = tone, fontSize = 22.sp)
        }
    }
}

@Composable
private fun SetupCard(onSettings: () -> Unit) {
    Surface(
        Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp), color = Panel,
        border = BorderStroke(1.dp, Color(0xFF152337)),
    ) {
        Column(Modifier.padding(18.dp)) {
            Text("PAIR THIS PHONE", color = Cyan, fontSize = 11.sp, letterSpacing = 3.sp)
            Spacer(Modifier.height(10.dp))
            Text("1. On the PC run ipconfig and note the IPv4 address.", color = TextDim, fontSize = 12.sp)
            Text("2. Keep Jarvis running with HOST=0.0.0.0.", color = TextDim, fontSize = 12.sp)
            Text("3. Enter the address and the PHONE_TOKEN from .env here.", color = TextDim, fontSize = 12.sp)
            Spacer(Modifier.height(14.dp))
            Button(
                onClick = onSettings,
                colors = ButtonDefaults.buttonColors(containerColor = Cyan, contentColor = Color(0xFF00131B)),
            ) { Text("OPEN CONFIG", fontWeight = FontWeight.Bold, letterSpacing = 1.sp) }
        }
    }
}

// --------------------------------------------------------------------------
// Chat
// --------------------------------------------------------------------------
data class ChatMsg(val role: String, val text: String)

@Composable
private fun ChatScreen(settings: SettingsStore) {
    val context = LocalContext.current
    val speaker = remember { Speaker(context) }
    DisposableEffect(Unit) { onDispose { speaker.shutdown() } }
    var messages by remember { mutableStateOf(listOf(ChatMsg("jarvis", "Online. What do you need?"))) }
    var input by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()

    fun send() {
        val text = input.trim()
        if (text.isEmpty() || busy) return
        if (!settings.configured) {
            messages = messages + ChatMsg("error", "Pair the phone in CONFIG first.")
            return
        }
        input = ""
        messages = messages + ChatMsg("you", text) + ChatMsg("jarvis", "")
        busy = true
        scope.launch {
            val reply = StringBuilder()
            Api(settings).chatStream(text) { event ->
                when (event.optString("type")) {
                    "text" -> {
                        reply.append(event.optString("text"))
                        messages = messages.dropLast(1) + ChatMsg("jarvis", reply.toString())
                    }
                    "action" -> {
                        val updated = messages.toMutableList()
                        updated.add(updated.lastIndex, ChatMsg("action", event.optString("text")))
                        messages = updated
                    }
                    "done" -> speaker.speak(event.optString("reply"))
                    "error" -> messages = messages.dropLast(1) + ChatMsg("error", event.optString("text"))
                }
            }
            busy = false
        }
    }

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.lastIndex)
    }

    Column(Modifier.fillMaxSize().imePadding().padding(horizontal = 16.dp)) {
        Text(
            "CHANNEL", color = TextDim, fontSize = 9.sp, letterSpacing = 3.sp,
            modifier = Modifier.padding(top = 14.dp, bottom = 8.dp),
        )
        LazyColumn(Modifier.weight(1f), state = listState, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(messages) { message -> MessageBubble(message) }
            if (busy) item { Text("…", color = TextDim, fontSize = 12.sp, modifier = Modifier.padding(start = 8.dp)) }
            item { Spacer(Modifier.height(6.dp)) }
        }
        Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.Bottom) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Message Jarvis", color = TextDim, fontSize = 13.sp) },
                maxLines = 4,
                shape = RoundedCornerShape(14.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = TextMain,
                    unfocusedTextColor = TextMain,
                    focusedBorderColor = Cyan.copy(alpha = 0.6f),
                    unfocusedBorderColor = Color(0xFF1E2C42),
                    cursorColor = Cyan,
                    focusedContainerColor = Panel,
                    unfocusedContainerColor = Panel,
                ),
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = { send() },
                enabled = !busy && input.isNotBlank(),
                colors = ButtonDefaults.buttonColors(containerColor = Cyan, contentColor = Color(0xFF00131B)),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier.height(56.dp),
            ) { Text("SEND", fontWeight = FontWeight.Bold, fontSize = 11.sp, letterSpacing = 1.sp) }
        }
    }
}

@Composable
private fun MessageBubble(message: ChatMsg) {
    val isYou = message.role == "you"
    val isAction = message.role == "action"
    val isError = message.role == "error"
    val tone = when {
        isError -> Danger
        isAction -> Amber
        isYou -> Cyan
        else -> Color(0xFF22344D)
    }
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (isYou) Arrangement.End else Arrangement.Start,
    ) {
        Surface(
            shape = RoundedCornerShape(14.dp),
            color = if (isYou) PanelHi else Panel,
            border = BorderStroke(1.dp, tone.copy(alpha = if (isAction || isError) 0.6f else 0.25f)),
            modifier = Modifier.fillMaxWidth(if (isYou) 0.82f else 0.92f),
        ) {
            Text(
                message.text.ifBlank { "…" },
                color = if (isAction) Amber else if (isError) Danger else TextMain,
                fontSize = if (isAction) 11.sp else 14.sp,
                fontFamily = if (isAction) FontFamily.Monospace else FontFamily.Default,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                textAlign = TextAlign.Start,
            )
        }
    }
}

// --------------------------------------------------------------------------
// Config
// --------------------------------------------------------------------------
@Composable
private fun SettingsScreen(settings: SettingsStore, onSaved: () -> Unit) {
    val context = LocalContext.current
    var host by remember { mutableStateOf(settings.host) }
    var httpPort by remember { mutableStateOf(settings.httpPort.toString()) }
    var livePort by remember { mutableStateOf(settings.livePort.toString()) }
    var token by remember { mutableStateOf(settings.token) }
    var guardian by remember { mutableStateOf(settings.serviceEnabled) }
    var notifyAll by remember { mutableStateOf(settings.notifyAll) }
    var daily by remember { mutableStateOf(settings.dailyCall) }
    var dailyTime by remember { mutableStateOf(settings.dailyTime) }

    fun save() {
        settings.host = host
        settings.httpPort = httpPort.toIntOrNull() ?: 5000
        settings.livePort = livePort.toIntOrNull() ?: 5001
        settings.token = token
        settings.serviceEnabled = guardian
        settings.notifyAll = notifyAll
        settings.dailyCall = daily
        settings.dailyTime = dailyTime
        if (guardian && settings.configured) {
            androidx.core.content.ContextCompat.startForegroundService(
                context, Intent(context, JarvisService::class.java)
            )
        } else {
            context.stopService(Intent(context, JarvisService::class.java))
        }
        if (daily && settings.configured) DailyCall.schedule(context, dailyTime) else DailyCall.cancel(context)
        onSaved()
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(20.dp)
    ) {
        Text("CONFIG", color = TextDim, fontSize = 9.sp, letterSpacing = 3.sp)
        Spacer(Modifier.height(16.dp))
        Field("PC address (IPv4)", host) { host = it }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Box(Modifier.weight(1f)) { Field("HTTP port", httpPort, KeyboardType.Number) { httpPort = it } }
            Box(Modifier.weight(1f)) { Field("Live port", livePort, KeyboardType.Number) { livePort = it } }
        }
        Spacer(Modifier.height(10.dp))
        Field("Pairing token (PHONE_TOKEN)", token) { token = it }
        Spacer(Modifier.height(18.dp))
        Toggle("Guardian link", "Keeps the event feed open for alerts and calls", guardian) { guardian = it }
        Toggle("Notify everything", "Off means only calls get through", notifyAll) { notifyAll = it }
        Toggle("Daily check-in call", "Jarvis rings you once a day", daily) { daily = it }
        if (daily) {
            Spacer(Modifier.height(6.dp))
            Field("Daily time (HH:MM)", dailyTime) { dailyTime = it }
        }
        Spacer(Modifier.height(20.dp))
        Button(
            onClick = { save() },
            modifier = Modifier.fillMaxWidth().height(52.dp),
            colors = ButtonDefaults.buttonColors(containerColor = Cyan, contentColor = Color(0xFF00131B)),
            shape = RoundedCornerShape(14.dp),
        ) { Text("SAVE & CONNECT", fontWeight = FontWeight.Bold, letterSpacing = 1.sp) }

        Spacer(Modifier.height(10.dp))
        Button(
            onClick = {
                Notifier.ring(
                    context,
                    "Test call",
                    "This is a test call from your J.A.R.V.I.S. app. If you can hear me, the live voice link works.",
                )
            },
            modifier = Modifier.fillMaxWidth().height(52.dp),
            colors = ButtonDefaults.buttonColors(containerColor = Panel, contentColor = Amber),
            shape = RoundedCornerShape(14.dp),
        ) { Text("TEST INCOMING CALL", fontWeight = FontWeight.Bold, letterSpacing = 1.sp) }

        Spacer(Modifier.height(14.dp))
        FullScreenCallsCard()

        Spacer(Modifier.height(24.dp))
        Text(
            "The phone must be on the same Wi-Fi as the PC. Jarvis serves HTTP on port 5000 and live voice on 5001; " +
                "allow Python through Windows Firewall when prompted. The token is the PHONE_TOKEN line in .env.",
            color = TextDim, fontSize = 11.sp,
        )
    }
}

@Composable
private fun FullScreenCallsCard() {
    val context = LocalContext.current
    val power = remember { context.getSystemService(android.os.PowerManager::class.java) }
    var overlay by remember { mutableStateOf(Settings.canDrawOverlays(context)) }
    var battery by remember { mutableStateOf(power.isIgnoringBatteryOptimizations(context.packageName)) }
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                overlay = Settings.canDrawOverlays(context)
                battery = power.isIgnoringBatteryOptimizations(context.packageName)
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    Surface(
        Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp), color = Panel,
        border = BorderStroke(1.dp, if (overlay && battery) Ok.copy(alpha = 0.4f) else Amber.copy(alpha = 0.35f)),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(if (overlay && battery) Ok else Amber))
                Spacer(Modifier.width(8.dp))
                Text(
                    "CALL PERMISSIONS",
                    color = if (overlay && battery) Ok else Amber,
                    fontSize = 10.sp, letterSpacing = 2.sp, fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "Calls must take over the screen even when the phone is locked. " +
                    "On Xiaomi also enable Autostart and 'Show on lock screen' for J.A.R.V.I.S.",
                color = TextDim, fontSize = 11.sp,
            )
            Spacer(Modifier.height(10.dp))
            PermissionRow("Full-screen calls", overlay) {
                runCatching {
                    context.startActivity(
                        Intent(
                            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            android.net.Uri.parse("package:${context.packageName}"),
                        )
                    )
                }
            }
            PermissionRow("No battery restrictions", battery) {
                runCatching {
                    context.startActivity(
                        Intent(
                            Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                            android.net.Uri.parse("package:${context.packageName}"),
                        )
                    )
                }
            }
            PermissionRow("Xiaomi autostart & lock screen", false, label = "OPEN") {
                val editor = Intent("miui.intent.action.APP_PERM_EDITOR").apply {
                    putExtra("extra_pkgname", context.packageName)
                    setClassName(
                        "com.miui.securitycenter",
                        "com.miui.permcenter.permissions.PermissionsEditorActivity",
                    )
                }
                val ok = runCatching { context.startActivity(editor) }.isSuccess
                if (!ok) runCatching {
                    context.startActivity(
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                            .setData(android.net.Uri.parse("package:${context.packageName}"))
                    )
                }
            }
        }
    }
}

@Composable
private fun PermissionRow(title: String, granted: Boolean, label: String = "ENABLE", onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, color = TextMain, fontSize = 12.sp, modifier = Modifier.weight(1f))
        Text(
            if (granted) "GRANTED" else label,
            color = if (granted) Ok else Cyan,
            fontSize = 10.sp,
            letterSpacing = 1.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier
                .clip(RoundedCornerShape(8.dp))
                .border(1.dp, (if (granted) Ok else Cyan).copy(alpha = 0.5f), RoundedCornerShape(8.dp))
                .clickable(enabled = !granted, onClick = onClick)
                .padding(horizontal = 12.dp, vertical = 6.dp),
        )
    }
}

@Composable
private fun Field(label: String, value: String, keyboard: KeyboardType = KeyboardType.Text, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label, color = TextDim, fontSize = 11.sp) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard),
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth(),
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = TextMain,
            unfocusedTextColor = TextMain,
            focusedBorderColor = Cyan.copy(alpha = 0.6f),
            unfocusedBorderColor = Color(0xFF1E2C42),
            cursorColor = Cyan,
            focusedContainerColor = Panel,
            unfocusedContainerColor = Panel,
            focusedLabelColor = Cyan,
            unfocusedLabelColor = TextDim,
        ),
    )
}

@Composable
private fun Toggle(title: String, subtitle: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, color = TextMain, fontSize = 14.sp)
            Text(subtitle, color = TextDim, fontSize = 11.sp)
        }
        Switch(
            checked = checked,
            onCheckedChange = onChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = Color(0xFF00131B),
                checkedTrackColor = Cyan,
                uncheckedThumbColor = TextDim,
                uncheckedTrackColor = PanelHi,
            ),
        )
    }
}

class Speaker(context: android.content.Context) : TextToSpeech.OnInitListener {
    private var tts: TextToSpeech? = TextToSpeech(context.applicationContext, this)
    private var ready = false

    override fun onInit(status: Int) {
        ready = status == TextToSpeech.SUCCESS
        if (ready) runCatching { tts?.language = Locale.getDefault() }
    }

    fun speak(text: String) {
        if (text.isBlank() || !ready) return
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis")
    }

    fun shutdown() {
        runCatching { tts?.stop(); tts?.shutdown() }
        tts = null
    }
}
