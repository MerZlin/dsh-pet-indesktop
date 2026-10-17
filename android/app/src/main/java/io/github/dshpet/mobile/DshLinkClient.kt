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

        /** `integrations/dsh-pet-bridge/lan-stream.js` 的默认端口。 */
        const val DEFAULT_PORT = 8768

        /**
         * 用户手输的地址 → 校验后的目标。**不做任何 DNS 解析**：只认 IP 字面量，
         * 主机名一律拒绝（见 [DshLinkReject.HOSTNAME]）。拒绝时绝不发请求、不把
         * 令牌送出去。
         *
         * 为什么要有这层：manifest 里 `usesCleartextTraffic="true"` 是为局域网明文
         * 端点留的（Android 的 `networkSecurityConfig` 只能按域名白名单，表达不了
         * "任意私网 IP"），所以"只连私网"这条必须由**运行时**守住。否则用户把地址
         * 填成公网（或一个会解析到公网的域名），Bearer 令牌就会明文出网。
         */
        fun validate(raw: String): DshLinkTarget {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) return DshLinkTarget.rejected("", DshLinkReject.EMPTY)
            val withScheme = if (trimmed.contains("://")) trimmed else "http://$trimmed"
            val schemeEnd = withScheme.indexOf("://")
            if (!withScheme.substring(0, schemeEnd).equals("http", ignoreCase = true)) {
                return DshLinkTarget.rejected("", DshLinkReject.NOT_HTTP)
            }
            var authority = withScheme.substring(schemeEnd + 3).trimEnd('/')
            // 只接受「主机[:端口]」：带路径/查询的写法会被客户端再拼一次 /v1/events。
            if (authority.isEmpty() || authority.any { it == '/' || it == '?' || it == '#' }) {
                return DshLinkTarget.rejected("", DshLinkReject.MALFORMED)
            }

            var host: String
            var port = DEFAULT_PORT
            if (authority.startsWith("[")) {
                val close = authority.indexOf(']')
                if (close < 0) return DshLinkTarget.rejected("", DshLinkReject.MALFORMED)
                host = authority.substring(1, close)
                val rest = authority.substring(close + 1)
                if (rest.isNotEmpty()) {
                    if (!rest.startsWith(":")) return DshLinkTarget.rejected("", DshLinkReject.MALFORMED)
                    port = parsePort(rest.substring(1)) ?: return DshLinkTarget.rejected("", DshLinkReject.PORT)
                }
                val bytes = parseIpv6(host) ?: return DshLinkTarget.rejected("", DshLinkReject.MALFORMED)
                if (!isPrivateIpv6(bytes)) return DshLinkTarget.rejected("", DshLinkReject.NOT_PRIVATE)
                host = "[$host]"
            } else {
                val colon = authority.lastIndexOf(':')
                if (colon >= 0) {
                    // 多个冒号 = 没加方括号的 IPv6；URL 里不合法，要求方括号写法。
                    if (authority.indexOf(':') != colon) return DshLinkTarget.rejected("", DshLinkReject.MALFORMED)
                    host = authority.substring(0, colon)
                    port = parsePort(authority.substring(colon + 1))
                        ?: return DshLinkTarget.rejected("", DshLinkReject.PORT)
                } else {
                    host = authority
                }
                val octets = parseIpv4(host) ?: return DshLinkTarget.rejected("", DshLinkReject.HOSTNAME)
                if (!isPrivateIpv4(octets)) return DshLinkTarget.rejected("", DshLinkReject.NOT_PRIVATE)
            }
            return DshLinkTarget("http://$host:$port", host, port, null)
        }

        /** 端口 1..65535；不合法返回 null（调用方转成 [DshLinkReject.PORT]）。 */
        private fun parsePort(text: String): Int? {
            if (text.isEmpty() || text.length > 5 || text.any { it !in '0'..'9' }) return null
            val value = text.toIntOrNull() ?: return null
            return if (value in 1..65535) value else null
        }

        /**
         * 严格点分十进制。**拒绝前导零**（`0177.0.0.1` 这类写法在不同解析器里可能是八进制，
         * 是用来绕过朴素私网判定的经典手法）。
         */
        private fun parseIpv4(text: String): IntArray? {
            val parts = text.split('.')
            if (parts.size != 4) return null
            val octets = IntArray(4)
            for ((index, part) in parts.withIndex()) {
                if (part.isEmpty() || part.length > 3) return null
                if (part.any { it !in '0'..'9' }) return null
                if (part.length > 1 && part[0] == '0') return null
                val value = part.toIntOrNull() ?: return null
                if (value > 255) return null
                octets[index] = value
            }
            return octets
        }

        /** IPv6 字面量 → 16 字节；不合法返回 null。支持 `::` 压缩与末段 IPv4 写法。 */
        private fun parseIpv6(text: String): ByteArray? {
            if (text.isEmpty()) return null
            val halves = text.split("::")
            if (halves.size > 2) return null
            val head = if (halves[0].isEmpty()) emptyList() else halves[0].split(':')
            val tail = if (halves.size == 2) {
                if (halves[1].isEmpty()) emptyList() else halves[1].split(':')
            } else {
                emptyList()
            }
            // 末段可以是 IPv4（如 ::ffff:192.168.1.5），先把它折算成两个 16 位组。
            val groups = ArrayList<Int>()
            for ((index, part) in (head + tail).withIndex()) {
                val isLast = index == head.size + tail.size - 1
                if (part.contains('.')) {
                    if (!isLast) return null
                    val octets = parseIpv4(part) ?: return null
                    groups.add((octets[0] shl 8) or octets[1])
                    groups.add((octets[2] shl 8) or octets[3])
                } else {
                    if (part.isEmpty() || part.length > 4) return null
                    val value = part.toIntOrNull(16) ?: return null
                    groups.add(value)
                }
            }
            val missing = 8 - groups.size
            if (halves.size == 2) {
                if (missing < 1) return null
            } else if (missing != 0) {
                return null
            }
            val full = ArrayList<Int>(8)
            full.addAll(groups.subList(0, head.size))
            repeat(missing) { full.add(0) }
            full.addAll(groups.subList(head.size, groups.size))
            val bytes = ByteArray(16)
            for (index in 0 until 8) {
                bytes[index * 2] = ((full[index] shr 8) and 0xff).toByte()
                bytes[index * 2 + 1] = (full[index] and 0xff).toByte()
            }
            return bytes
        }

        private fun isPrivateIpv4(octets: IntArray): Boolean {
            val first = octets[0]
            val second = octets[1]
            return first == 10 ||                                    // 10.0.0.0/8
                (first == 172 && second in 16..31) ||                 // 172.16.0.0/12
                (first == 192 && second == 168) ||                    // 192.168.0.0/16
                (first == 169 && second == 254) ||                    // 169.254.0.0/16 链路本地
                first == 127                                          // 127.0.0.0/8 回环
        }

        private fun isPrivateIpv6(bytes: ByteArray): Boolean {
            val zeroPrefix = (0 until 10).all { bytes[it].toInt() == 0 }
            val allZero = (0 until 16).all { bytes[it].toInt() == 0 }
            // ::1 回环
            if (zeroPrefix && bytes[10].toInt() == 0 && bytes[11].toInt() == 0 &&
                bytes[12].toInt() == 0 && bytes[13].toInt() == 0 &&
                bytes[14].toInt() == 0 && bytes[15].toInt() == 1
            ) {
                return true
            }
            // IPv4-mapped：::ffff:a.b.c.d → 按 IPv4 规则判
            val mappedPrefix = zeroPrefix && (bytes[10].toInt() and 0xff) == 0xff &&
                (bytes[11].toInt() and 0xff) == 0xff
            if (mappedPrefix) {
                val octets = IntArray(4) { bytes[12 + it].toInt() and 0xff }
                return isPrivateIpv4(octets)
            }
            if (allZero) return false
            val first = bytes[0].toInt() and 0xff
            val second = bytes[1].toInt() and 0xff
            // fe80::/10 链路本地
            if (first == 0xfe && (second and 0xc0) == 0x80) return true
            // fc00::/7 唯一本地（ULA）
            return (first and 0xfe) == 0xfc
        }
    }
}

/** 地址校验的拒绝理由；非空即"拒绝，绝不能发请求"。文案在 strings.xml 里本地化。 */
enum class DshLinkReject {
    /** 空输入 = 还没配置，不是错误。 */
    EMPTY,

    /** 完全不是 `主机[:端口]` 的形状（带路径、方括号不闭合、IPv6 没加方括号等）。 */
    MALFORMED,

    /** 只支持 `http://`（局域网明文端点）。 */
    NOT_HTTP,

    /** 只认 IP 字面量：主机名（含 localhost）一律拒绝，避免任何 DNS 解析。 */
    HOSTNAME,

    /** 不是私网/回环地址：会把令牌明文发往公网。 */
    NOT_PRIVATE,

    /** 端口不是 1..65535。 */
    PORT,
}

/**
 * 校验结果：[reason] 为 null 即放行，[baseUrl] 是补全 scheme/默认端口后的地址。
 */
data class DshLinkTarget(
    val baseUrl: String?,
    val host: String,
    val port: Int,
    val reason: DshLinkReject?,
) {
    val ok: Boolean get() = reason == null && baseUrl != null

    companion object {
        fun rejected(host: String, reason: DshLinkReject) = DshLinkTarget(null, host, 0, reason)
    }
}
