package io.github.dshpet.mobile

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * 电脑端只读事件流的客户端（`integrations/dsh-pet-bridge/lan-stream.js`）。
 *
 * 只用 JDK 自带的 `HttpURLConnection` 和平台自带的 `org.json`：Android 侧不新增
 * 任何依赖（与桌宠项目「能少一个依赖就少一个」的口径一致），也就不需要新的
 * 混淆/打包配置。协议是 `GET <base>/v1/events[?since=<cursor>]` +
 * `Authorization: Bearer <令牌>`，响应的 `records` 就是桥接目录里的原始记录。
 */
class DshLinkClient(private val baseUrl: String, private val token: String) {

    /** 一页增量：[cursor] 原样带回下一次请求，[records] 是桥接原始记录。 */
    data class Page(val cursor: String?, val records: List<JSONObject>, val truncated: Boolean)

    fun fetch(since: String?, connectTimeoutMs: Int = CONNECT_TIMEOUT_MS,
              readTimeoutMs: Int = READ_TIMEOUT_MS): Page {
        val query = if (since.isNullOrEmpty()) "" else "?since=${URLEncoder.encode(since, "UTF-8")}"
        val connection = (URL("$baseUrl/v1/events$query").openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Accept", "application/json")
            connectTimeout = connectTimeoutMs
            readTimeout = readTimeoutMs
        }
        try {
            if (connection.responseCode != HttpURLConnection.HTTP_OK) {
                throw IOException("HTTP ${connection.responseCode}")
            }
            val body = connection.inputStream.bufferedReader().use { it.readText() }
            val root = JSONObject(body)
            val array = root.optJSONArray("records") ?: JSONArray()
            val records = ArrayList<JSONObject>(array.length())
            for (index in 0 until array.length()) {
                array.optJSONObject(index)?.let(records::add)
            }
            return Page(
                cursor = root.optString("cursor").takeIf { it.isNotEmpty() },
                records = records,
                truncated = root.optBoolean("truncated", false),
            )
        } finally {
            connection.disconnect()
        }
    }

    companion object {
        const val CONNECT_TIMEOUT_MS = 2_000
        const val READ_TIMEOUT_MS = 2_000

        /**
         * 用户手输的地址可能是 `192.168.0.5:8768`（没有 scheme）。补成 `http://`
         * 并去掉尾斜杠；空白返回 null（= 没配置）。局域网端点只有明文 HTTP，
         * 因此这里不接受 https 之外的其它猜法。
         */
        fun normalizeBaseUrl(raw: String): String? {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) return null
            val withScheme = if (trimmed.contains("://")) trimmed else "http://$trimmed"
            return withScheme.trimEnd('/')
        }
    }
}
