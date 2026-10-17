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
}
