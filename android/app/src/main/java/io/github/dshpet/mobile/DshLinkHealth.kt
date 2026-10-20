package io.github.dshpet.mobile

/**
 * 「连不上电脑」的判定与播报时机（纯逻辑，可单测）。
 *
 * 为什么需要它：端点在电脑上，电脑关机/睡眠/换网/端点重启时手机**拿不到任何数据**。
 * 如果这时什么都不说，用户看到的现象是"没有新消息"，与"真的没有消息"完全无法区分
 * ——2026-10-17 到 18 一整天什么都没收到，就是因为这个静默。
 */
object DshLinkHealth {
    /**
     * 连续失败几次才播一次「连不上电脑了」。
     *
     * 轮询 3 秒一次、失败后退避到 [DshLinkModule.OFFLINE_MS]（15 秒），3 次 ≈ 半分钟：
     * 足够过滤"端点重启一下就好"的抖动，又能在真正断线时及时提醒。
     */
    const val LOST_AFTER_FAILURES = 3

    /**
     * 这次失败要不要播报？
     *
     * @param consecutiveFailures 连续失败次数（成功一次即清零）
     * @param alreadyLost 这一轮断线是不是已经播报过（只提醒一次，别刷屏）
     */
    fun shouldAnnounceLost(consecutiveFailures: Long, alreadyLost: Boolean): Boolean =
        !alreadyLost && consecutiveFailures >= LOST_AFTER_FAILURES

    /**
     * 断开多久之后，重连时**不再补播积压**（只播最新一条）。
     *
     * 电脑关机一夜、手机没开数据半天，游标还停在旧位置；照单全播会把几个小时的旧气泡
     * 一次性糊到用户脸上（真机反馈："积压的一堆气泡全部冒出来，MOSS 模型部署信息等"）。
     * 5 分钟以内算"眨眼"，照常播；超过就只播最新一条。
     */
    const val RESYNC_GAP_MS = 5 * 60_000L

    /** 记录超过这个年龄就不再弹（正常轮询里的兜底，防止陈旧事件迟到补播）。 */
    const val STALE_SECONDS = 120.0

    /** 一次轮询最多播几条（防突刺；超出只保留最新几条）。 */
    const val MAX_SIGNALS_PER_TICK = 3

    /** 这次重连算不算"断开太久"（要跳过积压）。 */
    fun shouldResync(gapMs: Long): Boolean = gapMs >= RESYNC_GAP_MS

    /** 这条记录是不是太旧了（[tsSeconds] 是记录里的 epoch 秒）。 */
    fun isStale(tsSeconds: Double, nowMs: Long): Boolean =
        tsSeconds > 0.0 && (nowMs / 1000.0 - tsSeconds) > STALE_SECONDS
}
