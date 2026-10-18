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
}
