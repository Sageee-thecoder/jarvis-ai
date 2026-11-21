package com.jarvis.app

import android.app.Service
import android.content.Intent
import android.os.IBinder
import org.json.JSONObject

class JarvisService : Service() {
    companion object {
        const val ACTION_STOP = "com.jarvis.app.STOP"
    }

    @Volatile
    private var running = false
    private var worker: Thread? = null

    override fun onCreate() {
        super.onCreate()
        Notifier.ensureChannels(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            running = false
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(Notifier.SERVICE_ID, Notifier.serviceNotification(this))
        if (!running) {
            running = true
            worker = Thread({ feedLoop() }, "jarvis-feed").also { it.start() }
        }
        return START_STICKY
    }

    private fun feedLoop() {
        val settings = SettingsStore(this)
        if (!settings.configured) {
            running = false
            return
        }
        Api(settings).feedLoop({ running }) { event -> handleEvent(event) }
    }

    private fun handleEvent(event: JSONObject) {
        val text = event.optString("text")
        if (text.isBlank()) return
        val settings = SettingsStore(this)
        val call = event.optBoolean("call")
        if (!call && !settings.notifyAll) return
        if (call) {
            Notifier.ring(this, text, text)
        } else {
            Notifier.showAlert(this, event.optString("kind"), text)
        }
    }

    override fun onDestroy() {
        running = false
        worker?.interrupt()
        worker = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
