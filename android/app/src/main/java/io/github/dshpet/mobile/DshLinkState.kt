package io.github.dshpet.mobile

/**
 * 手机端可见的 DSH 统一状态。
 *
 * 与桌面 `pet/dsh_state.py::DshState` 同名同义：事件映射表逐条对齐，因为手机和
 * 桌面消费的是**同一份桥接记录**（`integrations/dsh-pet-bridge` 写盘的
 * `dsh-{pid}.jsonl`）。两边各发明一套「什么算在忙」就会对同一次对话给出两个
 * 结论，所以这里的口径只能跟着桌面走。
 */
enum class DshLinkState {
    OFFLINE, IDLE, THINKING, WORKING, WAITING_APPROVAL, WAITING_QUESTION, SUCCESS, ERROR;

    companion object {
        fun fromName(value: String): DshLinkState? =
            values().firstOrNull { it.name.equals(value, ignoreCase = true) }
    }
}

/**
 * 记录 → 统一状态的纯映射层：不碰 Android、不碰网络、不碰 JSON。
 *
 * 事件表照抄 `pet/dsh_state.py`（`_EVENT_TO_STATE` / `_AGENT_STATUS_STATE` /
 * `_TURN_END_*_REASONS`）。抽成纯函数是为了能用 JVM 单测钉住——JSON 解析出错
 * 一眼能看出来，映射悄悄漂移却不会，那才是真正要防的。
 */
object DshLinkEvents {
    const val AGENT_STATUS = "AgentStatus"

    /** `{"event":"AgentStatus","state":"working"}` 聚合基线的 state 字段。 */
    private val STATUS = mapOf(
        "idle" to DshLinkState.IDLE,
        "working" to DshLinkState.WORKING,
        "thinking" to DshLinkState.THINKING,
        "waiting_approval" to DshLinkState.WAITING_APPROVAL,
        "waiting_question" to DshLinkState.WAITING_QUESTION,
        "success" to DshLinkState.SUCCESS,
        "error" to DshLinkState.ERROR,
    )

    /** 原始 session 事件 → 状态。表与桌面 `_EVENT_TO_STATE` 一致。 */
    private val EVENT = mapOf(
        "user/message" to DshLinkState.THINKING,
        "turn/start" to DshLinkState.THINKING,
        "assistant/message" to DshLinkState.WORKING,
        "tool/call" to DshLinkState.WORKING,
        "tool/result" to DshLinkState.WORKING,
        "step/start" to DshLinkState.WORKING,
        "step/end" to DshLinkState.WORKING,
        "command/run" to DshLinkState.WORKING,
        "command/done" to DshLinkState.WORKING,
        "tool-workflow/run-start" to DshLinkState.WORKING,
        "tool-workflow/run-end" to DshLinkState.WORKING,
        "cordis/request-run" to DshLinkState.WAITING_APPROVAL,
        "cordis/request-run-resolved" to DshLinkState.WORKING,
        "approval/request" to DshLinkState.WAITING_APPROVAL,
        "approval/decided" to DshLinkState.WORKING,
        "question/requested" to DshLinkState.WAITING_QUESTION,
        "question/resolved" to DshLinkState.WORKING,
        "turn/end" to DshLinkState.SUCCESS,
        "llm/retry" to DshLinkState.ERROR,
        "llm_error" to DshLinkState.ERROR,
    )

    /** 用户主动中止：静默收尾，不报「干完活啦」。 */
    private val TURN_END_SILENT = setOf("aborted")

    /** 本回合以出错终止：落错误终态，绝不报成功。 */
    private val TURN_END_ERROR = setOf("error")

    /**
     * [requiresApproval] 只在 `cordis/request-run` 上有意义：桌面只在桥写出的
     * `requiresApproval` **严格为真**时锁存审批（旧桥/平铺形状再核一次），
     * 其它情况一律忽略，避免把普通工具调用当审批。
     */
    fun stateOf(
        event: String,
        statusState: String = "",
        reasonKind: String = "",
        requiresApproval: Boolean = false,
    ): DshLinkState? {
        if (event == AGENT_STATUS) return STATUS[statusState.trim()]
        if (event == "cordis/request-run" && !requiresApproval) return null
        if (event == "turn/end") {
            val reason = reasonKind.trim()
            if (reason in TURN_END_SILENT) return DshLinkState.IDLE
            if (reason in TURN_END_ERROR) return DshLinkState.ERROR
        }
        return EVENT[event]
    }

    /**
     * 一次「该不该再弹」的身份键。
     *
     * 只按状态去重会漏报：`turn/end` 每次都是 `SUCCESS`，第二个回合起会被当成重复
     * 吞掉（用户看到的现象就是"只有第一回合弹了干完活啦"）。桌面端用回合身份
     * （`pet/dsh_state.py` 的 `TurnCompletionTracker`，按 `(会话, 回合号)` 发 token）
     * 保证每回合各报一次；手机端这里用同样的思路，取记录里的稳定身份：
     *
     * - 回合结束/出错：`sourceTurn` → `step` → `callId`
     * - 问用户：`rpcId` → `callId`
     * - 审批：`rpcId` → `approvalId` → `requestId` → `callId`
     * - 其余（working/idle 这类基线切换）：只用状态名，重复即忽略
     *
     * 取不到身份时退回状态名——宁可少弹，也不要把同一条记录弹两次。
     */
    fun dedupeKey(
        state: DshLinkState,
        sessionId: String = "",
        sourceTurn: String = "",
        step: String = "",
        rpcId: String = "",
        callId: String = "",
        approvalId: String = "",
        requestId: String = "",
        fingerprint: String = "",
    ): String {
        val identity = when (state) {
            DshLinkState.SUCCESS, DshLinkState.ERROR ->
                sourceTurn.ifBlank { step }.ifBlank { callId }
            DshLinkState.WAITING_QUESTION -> rpcId.ifBlank { callId }
            DshLinkState.WAITING_APPROVAL ->
                rpcId.ifBlank { approvalId }.ifBlank { requestId }.ifBlank { callId }
            else -> ""
        }.trim()
        // 会话是身份的一部分：DSH 可以并发多个会话（子代理/多窗口），两个会话各自的
        // "第 1 步收尾"或"rpc-1"在语义上不是同一件事——不带 sessionId 会把后到的
        // 那条当成重复吞掉（漏报）。
        val scoped = if (sessionId.isBlank()) identity else "$sessionId@$identity"
        val base = if (scoped.isEmpty()) state.name else "${state.name}:$scoped"
        // 带正文的记录（assistant/message）没有回合身份可用：用正文本身当指纹，
        // 这样"同一句话重放"只弹一次，而"每句不同的回复"各自弹一次。
        return if (fingerprint.isBlank()) base else "$base#$fingerprint"
    }

    /**
     * 一次轮询里有多条可播报事件时的取舍优先级（越大越优先）。
     *
     * 手机气泡只有一个槽位，一轮里多个会话同时有事件时总得挑一条。**等用户处理的交互
     * 优先于别的会话的"收尾播报"**——否则 B 会话问你个问题的同时 A 会话正好干完活，
     * 问题就被顶掉了。同优先级再比"谁后到"（调用方取后到的）。
     */
    fun priorityOf(state: DshLinkState): Int = when (state) {
        DshLinkState.WAITING_APPROVAL, DshLinkState.WAITING_QUESTION -> 4
        DshLinkState.ERROR -> 3
        DshLinkState.SUCCESS -> 2
        DshLinkState.WORKING -> 1
        else -> 0
    }
}
