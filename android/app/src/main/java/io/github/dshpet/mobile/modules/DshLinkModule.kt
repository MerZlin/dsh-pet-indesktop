package io.github.dshpet.mobile.modules

import io.github.dshpet.mobile.DshLinkClient
import io.github.dshpet.mobile.DshLinkEvents
import io.github.dshpet.mobile.DshLinkHealth
import io.github.dshpet.mobile.DshLinkReject
import io.github.dshpet.mobile.DshLinkState
import io.github.dshpet.mobile.DshLinkText
import io.github.dshpet.mobile.ModuleIds
import io.github.dshpet.mobile.R
import org.json.JSONObject
import java.util.Locale

/**
 * 电脑端 DSH 联动：把桥接目录的事件拉回手机，让手机上的桌宠跟着 PC 上的 agent
 * 走完一个回合时动一下、说一句。
 *
 * 默认关闭——0.2.1「没有网络请求」的既有口径由开关本身保证：关闭时模块根本不会
 * 被创建（见 `ModuleRegistry.apply`），没有线程、没有 socket、没有轮询。
 *
 * 线程边界：网络在一条独立工作线程上轮询（默认 3 秒一次、2 秒超时），它**只把
 * 结论写进 volatile 字段**；气泡与窗口操作一律回主线程在 `onTick` 里做。
 * 模块拿到的 host 是服务对象，在 worker 线程上动 Android 窗口会炸。
 *
 * 只在「状态真正变化」时反应（edge-trigger，与桌面 `dsh_state` 同口径）：同一个
 * working 事件连发一百条也只动一次。
 */
class DshLinkModule : HostBoundModule() {
    override val id = ModuleIds.DSH_LINK

    @Volatile private var running = false
    private var worker: Thread? = null

    /** 服务端游标：不透明字符串，客户端只负责原样带回。 */
    @Volatile private var cursor: String? = null

    /** worker 线程 → 主线程的唯一通道。 */
    @Volatile private var pending: List<Signal> = emptyList()

    /** 上次成功轮询的墙钟（毫秒）；用于判断重连时要不要跳过积压。 */
    private var lastOkAt = 0L

    /** 这一轮断线是否已经播报过（成功一次即复位）。 */
    @Volatile private var lostAnnounced = false

    @Volatile var polls = 0L
        private set

    @Volatile var failures = 0L
        private set

    /** 地址校验的拒绝理由（null = 通过或还没配置）；只用于诊断与设置页文案。 */
    @Volatile var rejected: DshLinkReject? = null
        private set

    private var applied = DshLinkState.OFFLINE
    /** 最近几次已反应的键（有界），用于跨轮询去重；见 [DshLinkEvents.dedupeKey]。 */
    private val recentKeys = ArrayDeque<String>()

    private fun remembered(key: String) {
        recentKeys.addLast(key)
        while (recentKeys.size > RECENT_KEYS) recentKeys.removeFirst()
    }

    private var reacted = 0L

    override fun onEnable() {
        cursor = null
        pending = emptyList()
        applied = DshLinkState.OFFLINE
        recentKeys.clear()
        rejected = null
        polls = 0
        failures = 0
        reacted = 0
        running = true
        worker = Thread(::loop, "dsh-link").apply {
            isDaemon = true
            start()
        }
    }

    override fun onDisable() {
        running = false
        worker?.interrupt()
        worker = null
        pending = emptyList()
    }

    /** 主线程：本帧开始时把 worker 攒下的最新结论消费掉。 */
    override fun onTick(dt: Double) {
        val batch = pending
        pending = emptyList()
        if (batch.isEmpty()) return
        // 一轮里可能有多会话、多条事件：**逐条都播**（按优先级从低到高，最后留下的
        // 气泡是优先级最高的那条），并逐条记键——否则"一轮只挑一条"会把同轮里其它
        // 会话的事件直接丢掉（真机表现为少弹）。
        val fresh = batch
            // 连接播报不走去重表（它们本来就有"只播一次"的状态机在管），
            // 事件仍按"身份键"去重。
            .filter { it.notice != null || it.key !in recentKeys }
            .sortedBy { if (it.notice != null) NOTICE_ORDER else DshLinkEvents.priorityOf(it.state) }
        for (signal in fresh) {
            applied = signal.state
            if (signal.notice == null) remembered(signal.key)
            react(signal)
        }
    }

    override fun diagnostics(): String =
        "polls=$polls failures=$failures state=${applied.name.lowercase(Locale.ROOT)} " +
            "reacted=$reacted rejected=${rejected?.name?.lowercase(Locale.ROOT) ?: "none"}"

    private fun react(signal: Signal) {
        // 只有一条母宠说话：小鲸鱼不说台词（与 SpeechModule 同口径）。
        val pet = host.pets.firstOrNull { !it.isCompanion } ?: return
        // 连接播报（断线/恢复）：不点 Q 弹、不加状态音，就是把话说明白。
        signal.notice?.let { notice ->
            val text = host.context.getString(
                if (notice == Notice.LOST) R.string.dsh_link_lost else R.string.dsh_link_back,
            )
            val readMs = DshLinkText.readingMs(text)
            host.showPhrase(pet, text, readMs)
            host.holdBubble(readMs)
            return
        }
        // 带正文就直接念正文（已压成 ≤100 字一句）；没有正文才退回固定短句。
        // 带正文时不看状态：assistant/message 归在 working，同样值得念出来。
        val fallback = if (signal.text == null) {
            when (signal.state) {
                DshLinkState.WAITING_APPROVAL -> R.string.dsh_link_waiting_approval
                DshLinkState.WAITING_QUESTION -> R.string.dsh_link_waiting_question
                DshLinkState.SUCCESS -> R.string.dsh_link_success
                DshLinkState.ERROR -> R.string.dsh_link_error
                // 忙/闲的来回切换不弹气泡：那是动画的活，弹了就是噪音。
                else -> return
            }
        } else {
            0
        }
        reacted++
        val text = signal.text ?: host.context.getString(fallback)
        // 停留按字数给（正文可能 100 字，3.2 秒读不完），并在同一窗口内占住气泡：
        // SpeechModule 看到 bubbleHeld() 就不发言，避免预设台词把这条顶掉。
        val readMs = DshLinkText.readingMs(text)
        host.showPhrase(pet, text, readMs)
        host.holdBubble(readMs)
        if (signal.state == DshLinkState.SUCCESS || signal.state == DshLinkState.ERROR) {
            pet.engine.click()
        }
    }

    private fun loop() {
        while (running) {
            var sleep = POLL_MS
            try {
                val target = DshLinkClient.validate(host.options.dshLinkUrl)
                rejected = target.reason
                val token = host.options.dshLinkToken
                if (!target.ok || token.isEmpty()) {
                    // 没配对、或地址没通过私网校验：**一个请求都不发**，令牌不出内存。
                    sleep = IDLE_MS
                } else {
                    val nowMs = System.currentTimeMillis()
                    val gapMs = if (lastOkAt > 0L) nowMs - lastOkAt else 0L
                    val page = DshLinkClient(target.baseUrl!!, token).fetch(cursor)
                    polls++
                    page.cursor?.let { cursor = it }
                    lastOkAt = nowMs
                    // 断线恢复：先说一句"连上了"，再处理这一页里的事件。
                    if (lostAnnounced) {
                        lostAnnounced = false
                        pending = pending + Signal(DshLinkState.OFFLINE, null, "notice:${Notice.BACK}", Notice.BACK)
                    }
                    // **不补播历史**：断开久了（电脑关机一整夜、手机没开数据）再连上时，游标还停在
                    // 旧位置，端点会把这段时间的记录一次性吐出来。若照单全播，用户会被几十条旧气泡
                    // 连珠炮糊脸（真机反馈："积压的一堆气泡全部冒出来，MOSS 模型部署信息等"）。
                    // 断得久 → 只播最新一条（告诉用户"刚刚发生了什么"），其余当已读。
                    val signals = stateOf(page, nowMs)
                    val picked = if (DshLinkHealth.shouldResync(gapMs)) {
                        signals.takeLast(1)
                    } else {
                        signals.takeLast(DshLinkHealth.MAX_SIGNALS_PER_TICK)
                    }
                    if (picked.isNotEmpty()) pending = pending + picked
                    // 服务端说还有增量就立刻接着拉，别等下一个整周期。
                    sleep = if (page.truncated) 0L else POLL_MS
                }
            } catch (interrupted: InterruptedException) {
                return
            } catch (failed: Exception) {
                // 连不上是常态（PC 睡了/换网/端点在重启）：不是错误态，只是慢下来重试。
                // 但**不能一直静默**——否则"没有新消息"和"连接断了"无法区分。
                failures++
                if (DshLinkHealth.shouldAnnounceLost(failures, lostAnnounced)) {
                    lostAnnounced = true
                    pending = pending + Signal(DshLinkState.OFFLINE, null, "notice:${Notice.LOST}", Notice.LOST)
                }
                // 判定"真断线"之前按正常节奏重试（3 秒），这样 3 次失败 ≈ 9 秒就能提醒；
                // 判定之后才退避到 15 秒，避免长时间断线时空转。
                sleep = if (failures < DshLinkHealth.LOST_AFTER_FAILURES) POLL_MS else OFFLINE_MS
            }
            try {
                if (sleep > 0L) Thread.sleep(sleep) else Thread.yield()
            } catch (interrupted: InterruptedException) {
                return
            }
        }
    }

    /**
     * 一轮轮询里能识别出来的**全部**信号（按 key 去重，同 key 只留最后一条）。
     *
     * 早先这里只返回一条（"最后一条/最优先一条"），多会话同时有事件时其余会话的事件
     * 会被静默丢掉（真机现象：A、B 两个会话同号回合收尾，只弹了一次）。现在整批交给
     * `onTick` 逐条播、逐条记 key。
     */
    private fun stateOf(page: DshLinkClient.Page, nowMs: Long): List<Signal> {
        val byKey = LinkedHashMap<String, Signal>()
        for (record in page.records) {
            val state = stateOf(record) ?: continue
            // 兜底：比 STALE_SECONDS 还旧的记录不弹。断开久了再连上时，端点的游标会把这些
            // 陈年记录一起吐出来；即使走了"只播最新一条"，也保证这一条本身不是隔夜货。
            if (DshLinkHealth.isStale(record.optDouble("ts", 0.0), nowMs)) continue
            val text = textOf(record, state)
            val signal = Signal(state, text, keyOf(record, state, text))
            byKey[signal.key] = signal // 同 key 后到覆盖先到
        }
        return byKey.values.toList()
    }

    /** 记录里的稳定身份 → 去重键；规则本身在纯函数 `DshLinkEvents.dedupeKey`。 */
    private fun keyOf(record: JSONObject, state: DshLinkState, text: String?): String =
        DshLinkEvents.dedupeKey(
            state = state,
            sessionId = record.optString("sessionId"),
            sourceTurn = record.optString("sourceTurn"),
            step = record.optString("step"),
            rpcId = record.optString("rpcId"),
            callId = record.optString("callId"),
            approvalId = record.optString("approvalId"),
            requestId = record.optString("requestId"),
            fingerprint = text.orEmpty(),
        )

    /**
     * 气泡要显示的**正文**（已压成 ≤100 字一句），没有可展示正文时返回 null。
     *
     * 优先级：`assistant/message` 的 `text`（模型这段回复的正文）→ `question/requested`
     * 的 `questions[0].question`（缺省回退 `header`）。其余事件没有正文，由调用方
     * 退回固定短句。
     *
     * 注意：仓库 main 的桥接按 #226 脱敏**不写助手正文**，所以只有桥接记录里带 `text`
     * 时这里才有值——没有就自然退回短句，不会编造。
     */
    private fun textOf(record: JSONObject, state: DshLinkState): String? {
        val candidate = when (state) {
            DshLinkState.WAITING_QUESTION -> {
                val first = record.optJSONArray("questions")?.optJSONObject(0)
                first?.optString("question").orEmpty().ifBlank { first?.optString("header").orEmpty() }
            }
            // assistant/message 归到 WORKING；只有带正文时才值得弹。
            DshLinkState.WORKING -> record.optString("text").ifBlank { record.optString("message") }
            else -> ""
        }
        return DshLinkText.brief(candidate)
    }

    /** JSON 提取留在这一层：映射规则本身在纯函数 `DshLinkEvents` 里，可单测。 */
    private fun stateOf(record: JSONObject): DshLinkState? {
        val nested = record.optJSONObject("payload")
        val requiresApproval = if (nested != null && nested.has("requiresApproval")) {
            nested.optBoolean("requiresApproval", false)
        } else {
            record.optBoolean("requiresApproval", false)
        }
        return DshLinkEvents.stateOf(
            event = record.optString("event"),
            statusState = record.optString("state"),
            reasonKind = record.optString("reasonKind"),
            requiresApproval = requiresApproval,
        )
    }

    companion object {
        /** 去重记忆长度：够覆盖"迟到的重放/被顶掉的旧回合"，又不会无限增长。 */
        const val RECENT_KEYS = 32
        /** worker → 主线程的最小载荷：状态 + 已压短的可选文案。 */
        private data class Signal(
            val state: DshLinkState,
            val text: String?,
            val key: String,
            /** 非空表示这是"连接状态"播报（断线/恢复），不参与事件去重。 */
            val notice: Notice? = null,
        )

        /** 连接层的播报：断线一次、恢复一次，不刷屏。 */
        private enum class Notice { LOST, BACK }
        /** 正常轮询间隔。状态变化的可见延迟上限就是它。 */
        const val POLL_MS = 3_000L

        /** 连接播报在排序里排最后（气泡最终留下优先级最高的一条）。 */
        private const val NOTICE_ORDER = 9

        /** 服务端说 truncated 时的立即重拉上限（避免空转）。 */
        const val OFFLINE_MS = 15_000L

        /** 没配对时的空转间隔：只是让开关改动能被感知，不发请求。 */
        const val IDLE_MS = 5_000L
    }
}

