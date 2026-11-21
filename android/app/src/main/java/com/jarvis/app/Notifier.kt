package com.jarvis.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.content.ContextCompat

object Notifier {
    const val CH_SERVICE = "jarvis_service"
    const val CH_ALERTS = "jarvis_alerts"
    const val CH_CALLS = "jarvis_calls_v2"
    const val CH_CALLS_FALLBACK = "jarvis_calls_loud_v2"
    const val INCOMING_ID = 4242
    const val SERVICE_ID = 4243

    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CH_SERVICE, "Guardian link", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Keeps the connection to Jarvis alive"
                setShowBadge(false)
            }
        )
        manager.createNotificationChannel(
            NotificationChannel(CH_ALERTS, "Jarvis alerts", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Reminders, weather alerts and autonomy reports"
            }
        )
        manager.createNotificationChannel(
            NotificationChannel(CH_CALLS, "Jarvis calls", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Full-screen calls when something important happens"
                setSound(null, null)
                enableVibration(false)
                setShowBadge(false)
            }
        )
        manager.createNotificationChannel(
            NotificationChannel(CH_CALLS_FALLBACK, "Jarvis calls (fallback)", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Rings when the full-screen call screen cannot open"
                setSound(
                    RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE),
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build(),
                )
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 700, 500, 700)
                setShowBadge(false)
            }
        )
    }

    fun serviceNotification(context: Context): Notification {
        ensureChannels(context)
        val open = PendingIntent.getActivity(
            context, 1, Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stop = PendingIntent.getService(
            context, 2,
            Intent(context, JarvisService::class.java).setAction(JarvisService.ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(context, CH_SERVICE)
            .setSmallIcon(R.drawable.ic_jarvis)
            .setContentTitle("Jarvis is watching")
            .setContentText("Link to your PC is active")
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .build()
    }

    fun showAlert(context: Context, kind: String, text: String) {
        ensureChannels(context)
        val open = PendingIntent.getActivity(
            context, 3, Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(context, CH_ALERTS)
            .setSmallIcon(R.drawable.ic_jarvis)
            .setContentTitle(titleFor(kind))
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open)
            .build()
        post(context, notification, (System.currentTimeMillis() % 100_000).toInt())
    }

    fun showMissed(context: Context, reason: String) {
        ensureChannels(context)
        val callBack = PendingIntent.getActivity(
            context, 7,
            Intent(context, CallActivity::class.java)
                .putExtra(CallActivity.EXTRA_GREETING, "Calling you back. What did I miss?")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(context, CH_ALERTS)
            .setSmallIcon(R.drawable.ic_jarvis)
            .setContentTitle("Missed call from J.A.R.V.I.S.")
            .setContentText(reason.take(120))
            .setStyle(NotificationCompat.BigTextStyle().bigText(reason))
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(callBack)
            .addAction(0, "Call back", callBack)
            .build()
        post(context, notification, (INCOMING_ID + 1))
    }

    fun showIncomingCall(context: Context, reason: String, greeting: String) {
        ensureChannels(context)
        val callIntent = Intent(context, IncomingCallActivity::class.java)
            .putExtra(IncomingCallActivity.EXTRA_REASON, reason)
            .putExtra(IncomingCallActivity.EXTRA_GREETING, greeting)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val full = PendingIntent.getActivity(
            context, 4, callIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val answer = PendingIntent.getActivity(
            context, 5,
            Intent(context, IncomingCallActivity::class.java)
                .putExtra(IncomingCallActivity.EXTRA_REASON, reason)
                .putExtra(IncomingCallActivity.EXTRA_GREETING, greeting)
                .putExtra(IncomingCallActivity.EXTRA_ANSWER, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val decline = PendingIntent.getActivity(
            context, 6,
            Intent(context, IncomingCallActivity::class.java)
                .putExtra(IncomingCallActivity.EXTRA_DECLINED, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val overlay = Settings.canDrawOverlays(context)
        val channel = if (overlay) CH_CALLS else CH_CALLS_FALLBACK
        val person = Person.Builder()
            .setName("J.A.R.V.I.S.")
            .setImportant(true)
            .build()
        val builder = NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_jarvis)
            .setContentTitle("J.A.R.V.I.S.")
            .setContentText(reason.take(120))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setAutoCancel(false)
            .setFullScreenIntent(full, true)
            .setContentIntent(full)
            .setDeleteIntent(decline)
            .setStyle(NotificationCompat.CallStyle.forIncomingCall(person, decline, answer))
        post(context, builder.build(), INCOMING_ID)
    }

    /** Ring the phone and, when the app may draw over other apps, bring up the call screen directly. */
    fun ring(context: Context, reason: String, greeting: String) {
        showIncomingCall(context, reason, greeting)
        if (!Settings.canDrawOverlays(context)) {
            Log.w("JarvisCall", "Overlay permission missing; relying on full-screen intent")
            return
        }
        runCatching {
            ContextCompat.startActivity(
                context,
                Intent(context, IncomingCallActivity::class.java)
                    .putExtra(IncomingCallActivity.EXTRA_REASON, reason)
                    .putExtra(IncomingCallActivity.EXTRA_GREETING, greeting)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                null,
            )
        }.onFailure { Log.w("JarvisCall", "Could not open call screen", it) }
    }

    fun dismissIncoming(context: Context) {
        NotificationManagerCompat.from(context).cancel(INCOMING_ID)
    }

    private fun post(context: Context, notification: Notification, id: Int) {
        val manager = NotificationManagerCompat.from(context)
        if (manager.areNotificationsEnabled()) manager.notify(id, notification)
    }

    private fun titleFor(kind: String): String = when (kind) {
        "reminder" -> "Reminder"
        "weather" -> "Weather alert"
        "autonomy" -> "Jarvis report"
        "mail" -> "New mail"
        "error" -> "Jarvis issue"
        else -> "Jarvis"
    }
}
