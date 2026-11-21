package com.jarvis.app

import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.util.Locale

class IncomingCallActivity : ComponentActivity() {
    companion object {
        const val EXTRA_REASON = "reason"
        const val EXTRA_GREETING = "greeting"
        const val EXTRA_ANSWER = "answer"
        const val EXTRA_DECLINED = "declined"
        private const val MISS_AFTER_MS = 45_000L
    }

    private var player: MediaPlayer? = null
    private var vibrator: Vibrator? = null
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var reason: String
    private lateinit var greeting: String
    private var done = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) {
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

        reason = intent.getStringExtra(EXTRA_REASON).orEmpty().ifBlank { "Jarvis needs you" }
        greeting = intent.getStringExtra(EXTRA_GREETING).orEmpty()

        if (intent.getBooleanExtra(EXTRA_DECLINED, false)) {
            decline()
            return
        }
        if (intent.getBooleanExtra(EXTRA_ANSWER, false)) {
            accept()
            return
        }

        setContent {
            JarvisTheme {
                IncomingScreen(
                    reason = reason,
                    onAccept = { accept() },
                    onDecline = { decline() },
                )
            }
        }
        beginRinging()
        handler.postDelayed({ if (!done) markMissed() }, MISS_AFTER_MS)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getBooleanExtra(EXTRA_DECLINED, false)) decline()
        else if (intent.getBooleanExtra(EXTRA_ANSWER, false)) accept()
    }

    private fun accept() {
        if (done) return
        done = true
        handler.removeCallbacksAndMessages(null)
        stopRinging()
        Notifier.dismissIncoming(this)
        Log.i("JarvisCall", "Incoming call accepted")
        runCatching {
            getSystemService(android.app.KeyguardManager::class.java)?.requestDismissKeyguard(this, null)
        }
        startActivity(
            Intent(this, CallActivity::class.java)
                .putExtra(CallActivity.EXTRA_GREETING, greeting.ifBlank { reason })
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        )
        finish()
    }

    private fun decline() {
        if (done) return
        done = true
        handler.removeCallbacksAndMessages(null)
        stopRinging()
        Notifier.dismissIncoming(this)
        finishAndRemoveTask()
    }

    private fun markMissed() {
        done = true
        stopRinging()
        Notifier.dismissIncoming(this)
        Notifier.showMissed(this, reason)
        finishAndRemoveTask()
    }

    private fun beginRinging() {
        runCatching {
            val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            player = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                )
                setDataSource(this@IncomingCallActivity, uri)
                isLooping = true
                setOnPreparedListener { it.start() }
                prepareAsync()
            }
        }.onFailure { Log.w("JarvisCall", "Ringtone unavailable", it) }
        runCatching {
            val device = if (Build.VERSION.SDK_INT >= 31) {
                getSystemService(VibratorManager::class.java).defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                getSystemService(Vibrator::class.java)
            }
            vibrator = device
            device.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 700, 500, 700), 0))
        }
    }

    private fun stopRinging() {
        runCatching {
            player?.let {
                if (it.isPlaying) it.stop()
                it.release()
            }
        }
        player = null
        runCatching { vibrator?.cancel() }
        vibrator = null
    }

    override fun onDestroy() {
        stopRinging()
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }
}

@Composable
private fun IncomingScreen(reason: String, onAccept: () -> Unit, onDecline: () -> Unit) {
    var ringing by remember { mutableStateOf(0) }
    val start = remember { SystemClock.elapsedRealtime() }

    Box(
        Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(Color(0xFF081321), Bg))),
    ) {
        LaunchedEffect(Unit) {
            while (true) {
                delay(1_000)
                ringing = ((SystemClock.elapsedRealtime() - start) / 1000).toInt()
            }
        }
        Column(
            Modifier.fillMaxSize().padding(horizontal = 26.dp, vertical = 42.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("INCOMING CALL", color = Amber, fontSize = 11.sp, letterSpacing = 4.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(10.dp))
            Text("J.A.R.V.I.S.", color = TextMain, fontSize = 34.sp, letterSpacing = 4.sp, fontWeight = FontWeight.Light)
            Spacer(Modifier.height(4.dp))
            Text("personal assistant · secure live link", color = TextDim, fontSize = 11.sp, letterSpacing = 1.sp)

            Spacer(Modifier.height(30.dp))
            JarvisCallOrb(tone = Amber, level = 0f, active = true, size = 196.dp)
            Spacer(Modifier.height(18.dp))
            Text(
                String.format(Locale.US, "RINGING  %02d:%02d", ringing / 60, ringing % 60),
                color = TextDim, fontSize = 11.sp, letterSpacing = 3.sp, fontFamily = FontFamily.Monospace,
            )

            Spacer(Modifier.height(26.dp))
            Surface(
                Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = Panel,
                border = androidx.compose.foundation.BorderStroke(1.dp, Amber.copy(alpha = 0.35f)),
            ) {
                Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(width = 3.dp, height = 38.dp).background(Amber, RoundedCornerShape(2.dp)))
                    Spacer(Modifier.width(12.dp))
                    Text(
                        reason,
                        color = TextMain,
                        fontSize = 14.sp,
                        textAlign = TextAlign.Start,
                        modifier = Modifier.weight(1f),
                    )
                }
            }

            Spacer(Modifier.weight(1f))
            Row(horizontalArrangement = Arrangement.spacedBy(70.dp), verticalAlignment = Alignment.CenterVertically) {
                CallControl("DECLINE", Danger, onDecline, size = 86.dp, filled = true)
                CallControl("ANSWER", Ok, onAccept, size = 86.dp, filled = true)
            }
            Spacer(Modifier.height(44.dp))
        }
    }
}
