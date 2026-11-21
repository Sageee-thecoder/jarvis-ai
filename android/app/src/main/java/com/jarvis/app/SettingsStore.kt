package com.jarvis.app

import android.content.Context

class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("jarvis", Context.MODE_PRIVATE)

    var host: String
        get() = prefs.getString("host", "").orEmpty()
        set(value) = prefs.edit().putString("host", value.trim()).apply()

    var httpPort: Int
        get() = prefs.getInt("httpPort", 5000)
        set(value) = prefs.edit().putInt("httpPort", value).apply()

    var livePort: Int
        get() = prefs.getInt("livePort", 5001)
        set(value) = prefs.edit().putInt("livePort", value).apply()

    var token: String
        get() = prefs.getString("token", "").orEmpty()
        set(value) = prefs.edit().putString("token", value.trim()).apply()

    var serviceEnabled: Boolean
        get() = prefs.getBoolean("serviceEnabled", false)
        set(value) = prefs.edit().putBoolean("serviceEnabled", value).apply()

    var notifyAll: Boolean
        get() = prefs.getBoolean("notifyAll", true)
        set(value) = prefs.edit().putBoolean("notifyAll", value).apply()

    var dailyCall: Boolean
        get() = prefs.getBoolean("dailyCall", false)
        set(value) = prefs.edit().putBoolean("dailyCall", value).apply()

    var dailyTime: String
        get() = prefs.getString("dailyTime", "07:30").orEmpty()
        set(value) = prefs.edit().putString("dailyTime", value.trim()).apply()

    val configured: Boolean
        get() = host.isNotBlank()

    private val hostOnly: String
        get() = host.substringAfter("://").substringBefore("/").substringBefore(":")

    val httpBase: String
        get() = "http://$hostOnly:$httpPort"

    fun liveUrl(greeting: String): String {
        val base = "ws://$hostOnly:$livePort/live?token=${urlEncode(token)}"
        return if (greeting.isBlank()) base else "$base&greet=${urlEncode(greeting.take(1400))}"
    }

    companion object {
        fun urlEncode(value: String): String =
            java.net.URLEncoder.encode(value, "UTF-8").replace("+", "%20")
    }
}
