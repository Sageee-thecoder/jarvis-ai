package com.jarvis.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class Api(private val settings: SettingsStore) {
    private val jsonType = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()
    private val streamClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(65, TimeUnit.SECONDS) // server pings every 20 s; the timeout lets a stopped service exit
        .build()

    private fun get(path: String): Request = Request.Builder()
        .url(settings.httpBase + path)
        .header("X-Jarvis-Token", settings.token)
        .build()

    suspend fun status(): JSONObject? = withContext(Dispatchers.IO) {
        runCatching {
            client.newCall(get("/api/status")).execute().use { response ->
                if (!response.isSuccessful) null
                else JSONObject(response.body?.string().orEmpty())
            }
        }.getOrNull()
    }

    suspend fun weather(): JSONObject? = withContext(Dispatchers.IO) {
        runCatching {
            client.newCall(get("/api/weather")).execute().use { response ->
                if (!response.isSuccessful) null
                else JSONObject(response.body?.string().orEmpty())
            }
        }.getOrNull()
    }

    suspend fun briefing(): String = withContext(Dispatchers.IO) {
        runCatching {
            client.newCall(get("/api/briefing")).execute().use { response ->
                if (!response.isSuccessful) ""
                else JSONObject(response.body?.string().orEmpty()).optString("text")
            }
        }.getOrDefault("")
    }

    suspend fun chatStream(message: String, onEvent: (JSONObject) -> Unit) = withContext(Dispatchers.IO) {
        val body = JSONObject().put("message", message).toString().toRequestBody(jsonType)
        val request = Request.Builder()
            .url(settings.httpBase + "/api/chat-stream")
            .header("X-Jarvis-Token", settings.token)
            .post(body)
            .build()
        try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    onEvent(JSONObject().put("type", "error").put("text", "Server returned ${response.code}"))
                    return@use
                }
                val source = response.body?.source() ?: return@use
                while (!source.exhausted()) {
                    val line = source.readUtf8Line() ?: break
                    if (line.isBlank()) continue
                    runCatching { JSONObject(line) }.getOrNull()?.let(onEvent)
                }
            }
        } catch (exc: Exception) {
            onEvent(JSONObject().put("type", "error").put("text", exc.message ?: "Connection failed"))
        }
    }

    /** Blocking loop for the foreground service; reconnects with backoff until shouldRun turns false. */
    fun feedLoop(shouldRun: () -> Boolean, onEvent: (JSONObject) -> Unit) {
        var backoff = 3_000L
        while (shouldRun()) {
            try {
                val request = get("/api/feed").newBuilder()
                    .header("Accept", "text/event-stream")
                    .build()
                streamClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) throw IllegalStateException("feed ${response.code}")
                    backoff = 3_000L
                    val source = response.body?.source() ?: return@use
                    val data = StringBuilder()
                    while (shouldRun()) {
                        val line = source.readUtf8Line() ?: break
                        when {
                            line.startsWith("data:") -> data.append(line.removePrefix("data:").trim())
                            line.isEmpty() && data.isNotEmpty() -> {
                                runCatching { JSONObject(data.toString()) }.getOrNull()?.let(onEvent)
                                data.clear()
                            }
                        }
                    }
                }
            } catch (_: Exception) {
                // Offline or server restarting; retry below.
            }
            if (!shouldRun()) break
            try {
                Thread.sleep(backoff)
            } catch (_: InterruptedException) {
                break
            }
            backoff = (backoff * 2).coerceAtMost(30_000L)
        }
    }
}
