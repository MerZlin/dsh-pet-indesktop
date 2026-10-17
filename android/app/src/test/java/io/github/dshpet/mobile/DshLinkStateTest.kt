package io.github.dshpet.mobile

import org.junit.Assert.assertEquals
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
    fun endpointInputIsNormalisedBeforeUse() {
        assertEquals("http://192.168.1.5:8768", DshLinkClient.normalizeBaseUrl("192.168.1.5:8768"))
        assertEquals("http://192.168.1.5:8768", DshLinkClient.normalizeBaseUrl(" 192.168.1.5:8768/ "))
        assertEquals("http://pc.local:8768", DshLinkClient.normalizeBaseUrl("http://pc.local:8768"))
        assertNull(DshLinkClient.normalizeBaseUrl("   "))
        assertNull(DshLinkClient.normalizeBaseUrl(""))
    }
}
