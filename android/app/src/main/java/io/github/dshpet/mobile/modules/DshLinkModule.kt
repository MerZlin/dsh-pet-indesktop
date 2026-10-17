package io.github.dshpet.mobile.modules

import io.github.dshpet.mobile.DshLinkClient
import io.github.dshpet.mobile.DshLinkEvents
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
    @Volatile private var pending: Signal? = null

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
        pending = null
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
        pending = null
    }

    /** 主线程：本帧开始时把 worker 攒下的最新结论消费掉。 */
    override fun onTick(dt: Double) {
        val next = pending ?: return
        pending = null
        // 按「身份键」去重，不是按状态：turn/end 每回合都收敛成 SUCCESS，只比状态会
        // 把第二回合起全部吞掉（真机现象＝只有第一回合弹过「干完活啦」）。
        // 记最近一串键而不是只记最后一个：桥接是多写者分文件的，同一回合迟到的重放
        // （以及被后面的回合顶掉之后才到的旧记录）不该再弹一次。
        if (next.key in recentKeys) return
        applied = next.state
        remembered(next.key)
        react(next)
    }

    override fun diagnostics(): String =
        "polls=$polls failures=$failures state=${applied.name.lowercase(Locale.ROOT)} " +
            "reacted=$reacted rejected=${rejected?.name?.lowercase(Locale.ROOT) ?: "none"}"

    private fun react(signal: Signal) {
        // 只有一条母宠说话：小鲸鱼不说台词（与 SpeechModule 同口径）。
        val pet = host.pets.firstOrNull { !it.isCompanion } ?: return
        // 问题带原文就用原文（已按 DshLinkText 压成 ≤100 字一句），否则退回固定短句。
        val fallback = when (signal.state) {
            DshLinkState.WAITING_APPROVAL -> R.string.dsh_link_waiting_approval
            DshLinkState.WAITING_QUESTION -> R.string.dsh_link_waiting_question
            DshLinkState.SUCCESS -> R.string.dsh_link_success
            DshLinkState.ERROR -> R.string.dsh_link_error
            // 忙/闲的来回切换不弹气泡：那是动画的活，弹了就是噪音。
            else -> return
        }
        reacted++
        val text = signal.text ?: host.context.getString(fallback)
        host.showPhrase(pet, text, host.options.speechDurationMs())
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
                    val page = DshLinkClient(target.baseUrl!!, token).fetch(cursor)
                    polls++
                    page.cursor?.let { cursor = it }
                    stateOf(page)?.let { pending = it }
                    // 服务端说还有增量就立刻接着拉，别等下一个整周期。
                    sleep = if (page.truncated) 0L else POLL_MS
                }
            } catch (interrupted: InterruptedException) {
                return
            } catch (failed: Exception) {
                // 连不上是常态（PC 睡了/换网/端点在重启）：不是错误态，只是慢下来重试。
                failures++
                sleep = OFFLINE_MS
            }
            try {
                if (sleep > 0L) Thread.sleep(sleep) else Thread.yield()
            } catch (interrupted: InterruptedException) {
                return
            }
        }
    }

    /** 一页里取**最后一条**能识别的状态：中间的事件已被后面的覆盖，不必逐条反应。 */
    private fun stateOf(page: DshLinkClient.Page): Signal? {
        var latest: Signal? = null
        for (record in page.records) {
            val state = stateOf(record) ?: continue
            latest = Signal(state, detailOf(record, state), keyOf(record, state))
        }
        return latest
    }

    /** 记录里的稳定身份 → 去重键；规则本身在纯函数 `DshLinkEvents.dedupeKey`。 */
    private fun keyOf(record: JSONObject, state: DshLinkState): String = DshLinkEvents.dedupeKey(
        state = state,
        sourceTurn = record.optString("sourceTurn"),
        step = record.optString("step"),
        rpcId = record.optString("rpcId"),
        callId = record.optString("callId"),
        approvalId = record.optString("approvalId"),
        requestId = record.optString("requestId"),
    )

    /**
     * 气泡能带的**短文本**：只有"问用户"这类事件本身就有可展示的原文，其余一律用固定短句。
     * 长度已由 [DshLinkText.brief] 收口（≤100 字，超长截成一句）。
     */
    private fun detailOf(record: JSONObject, state: DshLinkState): String? {
        if (state != DshLinkState.WAITING_QUESTION) return null
        val first = record.optJSONArray("questions")?.optJSONObject(0) ?: return null
        val question = first.optString("question").ifBlank { first.optString("header") }
        return DshLinkText.brief(question)
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
        private data class Signal(val state: DshLinkState, val text: String?, val key: String)        /** 正常轮询间隔。状态变化的可见延迟上限就是它。 */
        const val POLL_MS = 3_000L

        /** 服务端说 truncated 时的立即重拉上限（避免空转）。 */
        const val OFFLINE_MS = 15_000L

        /** 没配对时的空转间隔：只是让开关改动能被感知，不发请求。 */
        const val IDLE_MS = 5_000L
    }
}
