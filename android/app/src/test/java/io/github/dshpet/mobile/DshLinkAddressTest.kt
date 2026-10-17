package io.github.dshpet.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 地址白名单的运行时可测面：`usesCleartextTraffic="true"` 是为了局域网明文端点留的
 * （`networkSecurityConfig` 只能按域名放行，表达不了"任意私网 IP"），所以"只连私网"
 * 必须由这一层守住——令牌是 Bearer 明文，一旦目标能是公网就会明文出网。
 *
 * 规则：只认 IP 字面量（**不做 DNS 解析**）。
 * 放行 IPv4 10/8、172.16/12、192.168/16、169.254/16、127/8；
 * IPv6 ::1、fe80::/10、fc00::/7、以及 IPv4-mapped 的上述私网段。
 * 公网地址与任何主机名一律拒绝。
 */
class DshLinkAddressTest {
    private fun ok(raw: String): DshLinkTarget =
        DshLinkClient.validate(raw).also {
            assertTrue("应放行：$raw（实际 ${it.reason}）", it.ok)
            assertNull(it.reason)
        }

    private fun rejected(raw: String, reason: DshLinkReject): DshLinkTarget =
        DshLinkClient.validate(raw).also {
            assertEquals("拒绝理由不对：$raw", reason, it.reason)
            assertFalse("拒绝时不得给出可用地址：$raw", it.ok)
            assertNull("拒绝时不得给出 baseUrl：$raw", it.baseUrl)
        }

    @Test
    fun theThreePrivateIpv4RangesAreAllowed() {
        assertEquals("http://10.1.2.3:8768", ok("10.1.2.3:8768").baseUrl)
        assertEquals("http://172.16.0.9:8768", ok("172.16.0.9:8768").baseUrl)
        assertEquals("http://172.31.255.254:8768", ok("172.31.255.254:8768").baseUrl)
        assertEquals("http://192.168.1.5:8768", ok("192.168.1.5:8768").baseUrl)
    }

    @Test
    fun loopbackAndLinkLocalAreAllowed() {
        assertEquals("http://127.0.0.1:8768", ok("127.0.0.1:8768").baseUrl)
        assertEquals("http://127.1.2.3:8768", ok("127.1.2.3").baseUrl)
        assertEquals("http://169.254.10.20:8768", ok("169.254.10.20").baseUrl)
    }

    @Test
    fun carrierGradeNatIsAllowedForTailnets() {
        // Tailscale 用 100.64.0.0/10（RFC 6598 CGNAT）。手机走 tailnet 连自己电脑就是
        // 这个段；不放行的话「安全的虚拟网接手机」直接不可用。
        assertEquals("http://100.117.129.92:8768", ok("100.117.129.92:8768").baseUrl)
        assertEquals("http://100.64.0.1:8768", ok("100.64.0.1").baseUrl)        // 下边界
        assertEquals("http://100.127.255.254:8768", ok("100.127.255.254").baseUrl) // 上边界
        // 边界外一位都不行：100.63/16 与 100.128/16 不是 CGNAT。
        rejected("100.63.255.255", DshLinkReject.NOT_PRIVATE)
        rejected("100.128.0.1", DshLinkReject.NOT_PRIVATE)
    }

    @Test
    fun ipv6LoopbackUniqueLocalAndLinkLocalAreAllowed() {
        assertEquals("http://[::1]:8768", ok("[::1]:8768").baseUrl)
        assertEquals("http://[fc00::1]:8768", ok("[fc00::1]").baseUrl)
        assertEquals("http://[fd12:3456:789a::1]:8768", ok("[fd12:3456:789a::1]").baseUrl)
        assertEquals("http://[fe80::1234]:8768", ok("[fe80::1234]:8768").baseUrl)
    }

    @Test
    fun ipv4MappedIpv6FollowsTheIpv4Rules() {
        assertEquals("http://[::ffff:192.168.1.5]:8768", ok("[::ffff:192.168.1.5]").baseUrl)
        assertEquals("http://[::ffff:10.0.0.2]:8768", ok("[::ffff:10.0.0.2]").baseUrl)
        rejected("[::ffff:8.8.8.8]", DshLinkReject.NOT_PRIVATE)
        rejected("[::ffff:1.1.1.1]:8768", DshLinkReject.NOT_PRIVATE)
    }

    @Test
    fun publicAddressesAreRefused() {
        rejected("8.8.8.8", DshLinkReject.NOT_PRIVATE)
        rejected("1.1.1.1:8768", DshLinkReject.NOT_PRIVATE)
        rejected("172.15.0.1", DshLinkReject.NOT_PRIVATE)   // 172.15 不在 172.16/12 内
        rejected("172.32.0.1", DshLinkReject.NOT_PRIVATE)   // 172.32 也不在
        rejected("192.169.0.1", DshLinkReject.NOT_PRIVATE)
        rejected("11.0.0.1", DshLinkReject.NOT_PRIVATE)
        rejected("[2001:4860:4860::8888]", DshLinkReject.NOT_PRIVATE)
        rejected("[::2]", DshLinkReject.NOT_PRIVATE)
        rejected("[::]", DshLinkReject.NOT_PRIVATE)
    }

    @Test
    fun hostNamesAreNeverResolved() {
        // 拒绝主机名正是为了不发 DNS 查询：域名可能解析到公网，令牌就会明文出网。
        rejected("localhost", DshLinkReject.HOSTNAME)
        rejected("localhost:8768", DshLinkReject.HOSTNAME)
        rejected("my-pc.local:8768", DshLinkReject.HOSTNAME)
        rejected("pet.example.com", DshLinkReject.HOSTNAME)
        rejected("http://192.168.1.5.nip.io:8768", DshLinkReject.HOSTNAME)
    }

    @Test
    fun omittedPortFallsBackToTheEndpointDefault() {
        assertEquals(8768, ok("192.168.1.5").port)
        assertEquals("http://192.168.1.5:8768", ok("192.168.1.5").baseUrl)
        assertEquals(9000, ok("192.168.1.5:9000").port)
        assertEquals("http://192.168.1.5:9000", ok("192.168.1.5:9000").baseUrl)
    }

    @Test
    fun malformedPortsAreRefused() {
        for (bad in listOf("192.168.1.5:0", "192.168.1.5:70000", "192.168.1.5:abc",
            "192.168.1.5:", "192.168.1.5:123456")) {
            rejected(bad, DshLinkReject.PORT)
        }
    }

    @Test
    fun blankInputIsUnconfiguredNotAnError() {
        val verdict = DshLinkClient.validate("   ")
        assertEquals(DshLinkReject.EMPTY, verdict.reason)
        assertFalse(verdict.ok)
        assertNull(verdict.baseUrl)
        assertEquals(DshLinkReject.EMPTY, DshLinkClient.validate("").reason)
    }

    @Test
    fun schemeMustBePlainHttp() {
        assertEquals("http://192.168.1.5:8768", ok("HTTP://192.168.1.5:8768").baseUrl)
        rejected("https://192.168.1.5:8768", DshLinkReject.NOT_HTTP)
        rejected("http://192.168.1.5:8768/v1/events", DshLinkReject.MALFORMED)
        rejected("192.168.1.5:8768?token=x", DshLinkReject.MALFORMED)
    }

    @Test
    fun obfuscatedIpv4SpellingsAreRefused() {
        // 前导零在别的解析器里可能是八进制：0177.0.0.1 == 127.0.0.1。一律不认。
        rejected("0177.0.0.1", DshLinkReject.HOSTNAME)
        rejected("192.168.001.5", DshLinkReject.HOSTNAME)
        rejected("192.168.1.5.7", DshLinkReject.HOSTNAME)
        rejected("192.168.1", DshLinkReject.HOSTNAME)
        rejected("192.168.1.256", DshLinkReject.HOSTNAME)
        rejected("192.168.1.-5", DshLinkReject.HOSTNAME)
    }

    @Test
    fun unbracketedIpv6IsRefusedInsteadOfGuessed() {
        rejected("::1:8768", DshLinkReject.MALFORMED)
        rejected("[::1", DshLinkReject.MALFORMED)
        rejected("[]", DshLinkReject.MALFORMED)
        rejected("[1.2.3.4]", DshLinkReject.MALFORMED)
    }
}
