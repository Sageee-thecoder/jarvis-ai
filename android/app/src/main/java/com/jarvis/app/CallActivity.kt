package com.jarvis.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.delay
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class CallActivity : ComponentActivity() {
    companion object {
        const val EXTRA_GREETING = "greeting"
    }

    private var engine: CallEngine? = null
    private var status by mutableStateOf("Securing link…")
    private var live by mutableStateOf(false)
    private var muted by mutableStateOf(false)
    private var speakerOn by mutableStateOf(true)
    private var seconds by mutableStateOf(0)
    private var level by mutableStateOf(0f)
    private var ended by mutableStateOf(false)
    private var retryable by mutableStateOf(false)
    private val lines = mutableStateListOf<Pair<String, String>>()

    private val audio by lazy { getSystemService(AudioManager::class.java) }
    private var focusRequest: AudioFocusRequest? = null

    private val askMic = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) begin() else finish()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (android.os.Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
            )
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        Notifier.dismissIncoming(this)
        audio.mode = AudioManager.MODE_IN_COMMUNICATION
        audio.isSpeakerphoneOn = true
        runCatching {
            val attrs = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(attrs)
                .build()
            focusRequest = request
            audio.requestAudioFocus(request)
        }

        setContent {
            JarvisTheme {
                CallScreen(
                    status = status,
                    live = live,
                    seconds = seconds,
                    muted = muted,
                    speakerOn = speakerOn,
                    level = level,
                    ended = ended,
                    retryable = retryable,
                    lines = lines,
                    onMute = {
                        muted = !muted
                        engine?.muted = muted
                    },
                    onSpeaker = {
                        speakerOn = !speakerOn
                        audio.isSpeakerphoneOn = speakerOn
                    },
                    onEnd = { finish() },
                    onRetry = { retry() },
                )
                LaunchedEffect(live, ended) {
                    if (live && !ended) {
                        while (true) {
                            delay(1_000)
                            seconds += 1
                        }
                    }
                }
            }
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            begin()
        } else {
            askMic.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun begin() {
        if (engine != null) return
        val greeting = intent.getStringExtra(EXTRA_GREETING).orEmpty()
        val url = SettingsStore(this).liveUrl(greeting)
        status = "Securing link…"
        live = false
        ended = false
        retryable = false
        val built = CallEngine(
            context = applicationContext,
            url = url,
            onStatus = { status = it },
            onLive = {
                status = "Live"
                live = true
                ended = false
            },
            onEnd = { text, canRetry ->
                status = text
                live = false
                ended = true
                retryable = canRetry
                engine?.stop()
                engine = null
            },
            onTranscript = { role, text ->
                val last = lines.lastOrNull()
                if (last != null && last.first == role) {
                    val merged = if (text.startsWith(last.second)) text else last.second + text
                    lines[lines.lastIndex] = role to merged
                } else {
                    lines.add(role to text)
                }
            },
            onLevel = { level = it },
        )
        engine = built
        built.start()
    }

    private fun retry() {
        engine?.stop()
        engine = null
        lines.clear()
        seconds = 0
        level = 0f
        begin()
    }

    override fun onDestroy() {
        engine?.stop()
        engine = null
        runCatching { focusRequest?.let { audio.abandonAudioFocusRequest(it) } }
        focusRequest = null
        audio.isSpeakerphoneOn = false
        audio.mode = AudioManager.MODE_NORMAL
        super.onDestroy()
    }
}

@Composable
private fun CallScreen(
    status: String,
    live: Boolean,
    seconds: Int,
    muted: Boolean,
    speakerOn: Boolean,
    level: Float,
    ended: Boolean,
    retryable: Boolean,
    lines: List<Pair<String, String>>,
    onMute: () -> Unit,
    onSpeaker: () -> Unit,
    onEnd: () -> Unit,
    onRetry: () -> Unit,
) {
    val listState = rememberLazyListState()
    val tone = if (ended) Color(0xFF6C8299) else if (live) Cyan else Amber

    LaunchedEffect(lines.size) {
        if (lines.isNotEmpty()) runCatching { listState.animateScrollToItem(lines.lastIndex) }
    }

    Box(
        Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(Color(0xFF06111C), Bg))),
    ) {
        Column(
            Modifier.fillMaxSize().padding(horizontal = 22.dp, vertical = 22.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("SECURE LIVE LINK", color = TextDim, fontSize = 9.sp, letterSpacing = 4.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            Text("J.A.R.V.I.S.", color = TextMain, fontSize = 28.sp, letterSpacing = 4.sp, fontWeight = FontWeight.Light)
            Spacer(Modifier.height(6.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(6.dp).clip(CircleShape).background(tone))
                Spacer(Modifier.width(7.dp))
                Text(
                    if (ended) status.uppercase(Locale.US) else if (live) "LIVE" else status.uppercase(Locale.US),
                    color = tone,
                    fontSize = 10.sp,
                    letterSpacing = 2.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }

            Spacer(Modifier.height(10.dp))
            Text(
                String.format(Locale.US, "%02d:%02d", seconds / 60, seconds % 60),
                color = if (live) TextMain else TextDim,
                fontSize = 34.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Light,
            )

            Box(Modifier.height(224.dp).fillMaxWidth(), contentAlignment = Alignment.Center) {
                JarvisCallOrb(tone = tone, level = level, active = live, size = 190.dp)
            }

            Surface(
                Modifier.weight(1f).fillMaxWidth(),
                shape = RoundedCornerShape(18.dp),
                color = Panel,
                border = androidx.compose.foundation.BorderStroke(1.dp, Color(0xFF152337)),
            ) {
                if (lines.isEmpty()) {
                    Box(Modifier.fillMaxSize().padding(20.dp), contentAlignment = Alignment.Center) {
                        Text(
                            when {
                                ended -> "The link is closed."
                                live -> "Speak whenever you are ready."
                                else -> "Waking the voice link…"
                            },
                            color = TextDim,
                            fontSize = 12.sp,
                            textAlign = TextAlign.Center,
                        )
                    }
                } else {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize().padding(14.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        items(lines) { line ->
                            val isUser = line.first == "user"
                            Text(
                                (if (isUser) "YOU  " else "J.A.R.V.I.S.  ") + line.second,
                                color = if (isUser) TextDim else TextMain,
                                fontSize = 13.sp,
                                fontFamily = if (isUser) FontFamily.Monospace else FontFamily.Default,
                            )
                        }
                    }
                }
            }

            if (ended && retryable) {
                Spacer(Modifier.height(12.dp))
                Text(
                    "Link dropped. Same Wi-Fi, and check the PC is running.",
                    color = TextDim,
                    fontSize = 10.sp,
                    textAlign = TextAlign.Center,
                )
            }

            Spacer(Modifier.height(18.dp))
            if (ended) {
                Row(horizontalArrangement = Arrangement.spacedBy(34.dp)) {
                    if (retryable) CallControl("RETRY", Cyan, onRetry, size = 74.dp, filled = true)
                    CallControl("CLOSE", TextDim, onEnd, size = 74.dp)
                }
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                    CallControl("MUTE", if (muted) Amber else TextDim, onMute, filled = muted)
                    CallControl("SPEAKER", if (speakerOn) Cyan else TextDim, onSpeaker, filled = speakerOn)
                    CallControl("END", Danger, onEnd, filled = true)
                }
            }
            Spacer(Modifier.height(18.dp))
        }
    }
}

private class CallEngine(
    context: Context,
    private val url: String,
    private val onStatus: (String) -> Unit,
    private val onLive: () -> Unit,
    private val onEnd: (String, Boolean) -> Unit,
    private val onTranscript: (String, String) -> Unit,
    private val onLevel: (Float) -> Unit,
) {
    private companion object {
        const val TAG = "JarvisCall"
        const val MAX_RETRIES = 3
    }

    private val appContext = context.applicationContext
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    private val queue = LinkedBlockingQueue<ByteArray>()
    private var socket: WebSocket? = null
    private var record: AudioRecord? = null
    private var track: AudioTrack? = null
    private var micThread: Thread? = null
    private var playThread: Thread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    @Volatile private var running = true
    @Volatile private var serverEnded = false
    @Volatile private var generation = 0
    @Volatile private var attempt = 0
    @Volatile private var lastLevelAt = 0L
    @Volatile var muted = false

    fun start() {
        openSocket()
        Thread({
            try {
                setupAudio()
            } catch (exc: Exception) {
                Log.w(TAG, "Audio setup failed", exc)
                onEnd("Audio error: ${exc.message}", true)
                return@Thread
            }
            acquireLocks()
            playThread = Thread({ playbackLoop() }, "jarvis-play").also { it.start() }
            micThread = Thread({ micLoop() }, "jarvis-mic").also { it.start() }
        }, "jarvis-audio").start()
    }

    private fun openSocket() {
        if (!running) return
        val gen = ++generation
        Log.i(TAG, "Connecting live voice link")
        socket = client.newWebSocket(
            Request.Builder().url(url).build(),
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    if (!running || gen != generation) return
                    Log.i(TAG, "WebSocket open")
                    onStatus("Linking voice…")
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    if (!running || gen != generation) return
                    val data = runCatching { JSONObject(text) }.getOrNull() ?: return
                    when (data.optString("type")) {
                        "ready" -> onStatus("Waking J.A.R.V.I.S.…")
                        "connected" -> {
                            attempt = 0
                            Log.i(TAG, "Live session connected")
                            onLive()
                        }
                        "transcript" -> onTranscript(data.optString("role"), data.optString("text"))
                        "interrupted" -> {
                            queue.clear()
                            runCatching {
                                track?.pause()
                                track?.flush()
                                track?.play()
                            }
                        }
                        "error" -> {
                            serverEnded = true
                            val text = data.optString("text").ifBlank { "server error" }
                            Log.w(TAG, "Server ended call: $text")
                            onEnd("Call ended: $text", true)
                        }
                        "closed" -> {
                            serverEnded = true
                            onEnd("Call ended", false)
                        }
                    }
                }

                override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                    if (!running || gen != generation) return
                    queue.offer(bytes.toByteArray())
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    if (!running || gen != generation) return
                    Log.w(TAG, "WebSocket failure (attempt ${attempt + 1}/$MAX_RETRIES)", t)
                    if (serverEnded) {
                        onEnd("Call ended", false)
                        return
                    }
                    if (attempt < MAX_RETRIES) {
                        attempt += 1
                        onStatus("Reconnecting ${attempt}/$MAX_RETRIES…")
                        Thread({
                            try {
                                Thread.sleep(1_200L * attempt)
                            } catch (_: InterruptedException) {
                                return@Thread
                            }
                            if (running && !serverEnded) openSocket()
                        }, "jarvis-reconnect").start()
                    } else {
                        onEnd("Connection lost: ${t.message ?: "link dropped"}", true)
                    }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    if (!running || gen != generation) return
                    Log.i(TAG, "WebSocket closed $code $reason")
                    onEnd(
                        if (code == 4003) "Pairing token rejected" else "Call ended",
                        code == 1011,
                    )
                }
            }
        )
    }

    private fun acquireLocks() {
        runCatching {
            val power = appContext.getSystemService(PowerManager::class.java)
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:call").apply {
                setReferenceCounted(false)
                acquire(3 * 60 * 60 * 1000L)
            }
        }
        runCatching {
            val wifi = appContext.applicationContext.getSystemService(WifiManager::class.java)
            wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "jarvis:call").apply {
                setReferenceCounted(false)
                acquire()
            }
        }
    }

    private fun setupAudio() {
        Log.i(TAG, "Preparing audio path")
        val minTrack = AudioTrack.getMinBufferSize(24000, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(24000)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(maxOf(minTrack * 2, 9600))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        track?.play()

        val minRecord = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            16000,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minRecord * 2, 6400),
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            throw IllegalStateException("Microphone unavailable")
        }
        record = recorder
        Log.i(TAG, "Audio path ready")
    }

    private fun micLoop() {
        val recorder = record ?: return
        runCatching { AcousticEchoCanceler.create(recorder.audioSessionId)?.enabled = true }
        try {
            recorder.startRecording()
        } catch (exc: Exception) {
            onEnd("Mic error: ${exc.message}", false)
            return
        }
        val buffer = ByteArray(1280)
        while (running) {
            val read = recorder.read(buffer, 0, buffer.size)
            if (read <= 0) continue
            if (!muted) socket?.send(buffer.copyOf(read).toByteString())
            emitLevel(buffer, read)
        }
        runCatching { recorder.stop() }
    }

    private fun playbackLoop() {
        try {
            while (running) {
                val chunk = queue.take()
                track?.write(chunk, 0, chunk.size)
                emitLevel(chunk, chunk.size)
            }
        } catch (_: InterruptedException) {
        }
    }

    private fun emitLevel(bytes: ByteArray, length: Int) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastLevelAt < 90) return
        lastLevelAt = now
        var sum = 0.0
        var index = 0
        while (index + 1 < length) {
            val sample = ((bytes[index].toInt() and 0xFF) or (bytes[index + 1].toInt() shl 8)).toShort().toInt()
            sum += (sample * sample).toDouble()
            index += 2
        }
        val count = (length / 2).coerceAtLeast(1)
        onLevel((kotlin.math.sqrt(sum / count) / 8000.0).coerceIn(0.0, 1.0).toFloat())
    }

    fun stop() {
        if (!running) return
        running = false
        runCatching { socket?.close(1000, "bye") }
        socket = null
        runCatching { record?.stop() }
        runCatching { record?.release() }
        record = null
        runCatching {
            track?.pause()
            track?.flush()
            track?.release()
        }
        track = null
        micThread?.interrupt()
        playThread?.interrupt()
        micThread = null
        playThread = null
        runCatching { wakeLock?.takeIf { it.isHeld }?.release() }
        runCatching { wifiLock?.takeIf { it.isHeld }?.release() }
        wakeLock = null
        wifiLock = null
        queue.clear()
    }
}
