package io.github.dshpet.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 手机端 DSH 状态映射的对照测试。用例逐条对齐桌面 `tests/test_dsh_state.py`
 * 里的同名语义：手机与桌面读的是同一份桥接记录，同一个事件必须收敛成同一个
 * 状态，否则「电脑在忙、手机在睡」这类分歧会静默存在。
 */
class DshLinkStateTest {
    @Test
    fun everyModuleBitIsOffByDefaultForTheLink() {
        // 联动默认关：0.2.1 的「没有网络请求」口径靠这一条保住。
        assertEquals(false, PetOptions().dshLink)
        assertTrue(!PetOptions().enables(ModuleIds.DSH_LINK))
    }

    @Test
    fun aggregateStatusBaselineMapsToTheSameStatesAsDesktop() {
        assertEquals(DshLinkState.WORKING, DshLinkEvents.stateOf("AgentStatus", statusState = "working"))
        assertEquals(DshLinkState.IDLE, DshLinkEvents.stateOf("AgentStatus", statusState = "idle"))
        assertEquals(DshLinkState.THINKING, DshLinkEvents.stateOf("AgentStatus", statusState = "thinking"))
        assertEquals(DshLinkState.WAITING_APPROVAL,
            DshLinkEvents.stateOf("AgentStatus", statusState = "waiting_approval"))
        assertEquals(DshLinkState.SUCCESS, DshLinkEvents.stateOf("AgentStatus", statusState = "success"))
        assertEquals(DshLinkState.ERROR, DshLinkEvents.stateOf("AgentStatus", statusState = "error"))
        // 状态字段两侧空白会被脏数据带进来；桌面用 strip()，这里同口径。
        assertEquals(DshLinkState.WORKING, DshLinkEvents.stateOf("AgentStatus", statusState = " working "))
        assertNull(DshLinkEvents.stateOf("AgentStatus", statusState = "no-such-state"))
    }

    @Test
    fun rawSessionEventsMatchTheDesktopTable() {
        val cases = mapOf(
            "user/message" to DshLinkState.THINKING,
            "turn/start" to DshLinkState.THINKING,
            "assistant/message" to DshLinkState.WORKING,
            "tool/call" to DshLinkState.WORKING,
            "tool/result" to DshLinkState.WORKING,
            "step/start" to DshLinkState.WORKING,
            "step/end" to DshLinkState.WORKING,
            "command/run" to DshLinkState.WORKING,
            "command/done" to DshLinkState.WORKING,
            "question/requested" to DshLinkState.WAITING_QUESTION,
            "question/resolved" to DshLinkState.WORKING,
            "approval/request" to DshLinkState.WAITING_APPROVAL,
            "approval/decided" to DshLinkState.WORKING,
            "llm/retry" to DshLinkState.ERROR,
            "llm_error" to DshLinkState.ERROR,
        )
        for ((event, expected) in cases) {
            assertEquals(event, expected, DshLinkEvents.stateOf(event))
        }
    }

    @Test
    fun aBareApprovalAskedNeverLatchesApproval() {
        // approval/asked 连普通工具调用都会被宿主打上：不能当审批，桌面 PR57 起已明确。
        assertNull(DshLinkEvents.stateOf("approval/asked"))
    }

    @Test
    fun turnEndIsSplitByReasonKindSoAFailedTurnNeverReportsSuccess() {
        assertEquals(DshLinkState.SUCCESS, DshLinkEvents.stateOf("turn/end"))
        assertEquals(DshLinkState.SUCCESS, DshLinkEvents.stateOf("turn/end", reasonKind = "completed"))
        assertEquals(DshLinkState.IDLE, DshLinkEvents.stateOf("turn/end", reasonKind = "aborted"))
        assertEquals(DshLinkState.ERROR, DshLinkEvents.stateOf("turn/end", reasonKind = "error"))
    }

    @Test
    fun cordisRunOnlyLatchesWhenItReallyRequiresApproval() {
        assertEquals(DshLinkState.WAITING_APPROVAL,
            DshLinkEvents.stateOf("cordis/request-run", requiresApproval = true))
        // 非审批的 cordis 请求：忽略，绝不弹审批。
        assertNull(DshLinkEvents.stateOf("cordis/request-run", requiresApproval = false))
        assertEquals(DshLinkState.WORKING, DshLinkEvents.stateOf("cordis/request-run-resolved"))
    }

    @Test
    fun unknownEventsAreIgnoredInsteadOfGuessed() {
        assertNull(DshLinkEvents.stateOf("edit/requested"))
        assertNull(DshLinkEvents.stateOf(""))
        assertNull(DshLinkEvents.stateOf("bridge/control-result"))
    }

    @Test
    fun everyFinishedTurnGetsItsOwnBubbleKey() {
        // 真机缺陷回归：只按状态去重时第二个回合起都不弹（每回合都是 SUCCESS）。
        val first = DshLinkEvents.dedupeKey(DshLinkState.SUCCESS, step = "1")
        val second = DshLinkEvents.dedupeKey(DshLinkState.SUCCESS, step = "2")
        assertNotEquals("不同回合必须算两次不同的收尾", first, second)
        // 同一回合重复投递（镜像写者/重放）必须仍是同一个键，不能再弹一次。
        assertEquals(
            "带回合号时以回合号为准：同回合内 step 变了也不算新回合",
            DshLinkEvents.dedupeKey(DshLinkState.SUCCESS, sourceTurn = "turn-7", step = "7"),
            DshLinkEvents.dedupeKey(DshLinkState.SUCCESS, sourceTurn = "turn-7", step = "9"),
        )
        // 回合号缺失时退回 step。
        assertEquals(
            DshLinkEvents.dedupeKey(DshLinkState.SUCCESS, step = "12"),
            DshLinkEvents.dedupeKey(DshLinkState.SUCCESS, step = "12"),
        )
    }

    @Test
    fun eachQuestionAndApprovalHasItsOwnKey() {
        assertNotEquals(
            DshLinkEvents.dedupeKey(DshLinkState.WAITING_QUESTION, rpcId = "r1"),
            DshLinkEvents.dedupeKey(DshLinkState.WAITING_QUESTION, rpcId = "r2"),
        )
        assertNotEquals(
            DshLinkEvents.dedupeKey(DshLinkState.WAITING_APPROVAL, approvalId = "a1"),
            DshLinkEvents.dedupeKey(DshLinkState.WAITING_APPROVAL, approvalId = "a2"),
        )
        // 完全相同的交互重复投递：同一个键，只弹一次。
        assertEquals(
            DshLinkEvents.dedupeKey(DshLinkState.WAITING_APPROVAL, approvalId = "a1"),
            DshLinkEvents.dedupeKey(DshLinkState.WAITING_APPROVAL, approvalId = "a1"),
        )
    }

    @Test
    fun baselineStatesStillDedupeByStateAlone() {
        // working/idle 这类基线每回合都会刷一条；没有身份可依，重复即忽略。
        assertEquals(
            DshLinkEvents.dedupeKey(DshLinkState.WORKING, step = "1"),
            DshLinkEvents.dedupeKey(DshLinkState.WORKING, step = "99"),
        )
        assertEquals(DshLinkState.WORKING.name, DshLinkEvents.dedupeKey(DshLinkState.WORKING))
    }

    @Test
    fun eachAssistantMessageIsDeliveredOnceButOnlyOnce() {
        // 用户的诉求：投"正文"，不是复读预设台词。assistant/message 归在 WORKING、
        // 没有回合身份，所以用正文本身当指纹。
        val first = DshLinkEvents.dedupeKey(DshLinkState.WORKING, fingerprint = "第一句正文")
        val second = DshLinkEvents.dedupeKey(DshLinkState.WORKING, fingerprint = "第二句正文")
        assertNotEquals("两句不同的话必须各弹一次", first, second)
        assertEquals(
            "同一句话被重放时只弹一次",
            first,
            DshLinkEvents.dedupeKey(DshLinkState.WORKING, fingerprint = "第一句正文"),
        )
        // 没有正文时仍然退回"按状态去重"，不会因为指纹为空而误判成新消息。
        assertEquals(
            DshLinkEvents.dedupeKey(DshLinkState.WORKING),
            DshLinkEvents.dedupeKey(DshLinkState.WORKING, fingerprint = ""),
        )
    }

    @Test
    fun endpointInputIsNormalisedBeforeUse() {
        // 补 scheme / 去尾斜杠与空白 / 空白输入没有结果：与 normalizeBaseUrl 时代的
        // 三条断言等价，只是换到带白名单校验的新入口（validate）上，覆盖不变。
        assertEquals("http://192.168.1.5:8768", DshLinkClient.validate("192.168.1.5:8768").baseUrl)
        assertEquals("http://192.168.1.5:8768", DshLinkClient.validate(" 192.168.1.5:8768/ ").baseUrl)
        assertNull(DshLinkClient.validate("   ").baseUrl)
        assertNull(DshLinkClient.validate("").baseUrl)
        // 行为变更（有意为之）：主机名不再被原样放行，而是拒绝——域名可能解析到公网，
        // Bearer 令牌就会明文出网。理由见 DshLinkAddressTest.hostNamesAreNeverResolved。
        assertEquals(DshLinkReject.HOSTNAME, DshLinkClient.validate("http://pc.local:8768").reason)
    }
}
