package io.github.dshpet.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DshLinkHealthTest {
    @Test
    fun aShortBlipStaysQuiet() {
        // 端点重启、网络抖一下：一两次失败不值得惊动用户。
        assertFalse(DshLinkHealth.shouldAnnounceLost(consecutiveFailures = 1L, alreadyLost = false))
        assertFalse(DshLinkHealth.shouldAnnounceLost(consecutiveFailures = 2L, alreadyLost = false))
    }

    @Test
    fun crossingTheThresholdAnnouncesOnce() {
        assertTrue(DshLinkHealth.shouldAnnounceLost(consecutiveFailures = 3L, alreadyLost = false))
        // 已经播过就不再刷屏——断线期间一直失败，用户不需要每 15 秒听一次。
        assertFalse(DshLinkHealth.shouldAnnounceLost(consecutiveFailures = 4L, alreadyLost = true))
        assertFalse(DshLinkHealth.shouldAnnounceLost(consecutiveFailures = 99L, alreadyLost = true))
    }

    @Test
    fun theNextOutageAnnouncesAgainAfterRecovery() {
        // 恢复后 alreadyLost 复位，下一次真断线必须再提醒。
        assertTrue(DshLinkHealth.shouldAnnounceLost(consecutiveFailures = 3L, alreadyLost = false))
    }

    @Test
    fun aBriefBlipStillReplaysNormally() {
        // 3 秒的网络抖动、端点重启一下：不该被当成"断开太久"。
        assertFalse(DshLinkHealth.shouldResync(gapMs = 3_000L))
        assertFalse(DshLinkHealth.shouldResync(gapMs = 60_000L))
        assertFalse(DshLinkHealth.shouldResync(gapMs = DshLinkHealth.RESYNC_GAP_MS - 1))
    }

    @Test
    fun aLongGapSkipsTheBacklog() {
        // 电脑关机一夜、手机半天没开数据：重连只播最新一条，别把几小时前的气泡全放出来。
        assertTrue(DshLinkHealth.shouldResync(gapMs = DshLinkHealth.RESYNC_GAP_MS))
        assertTrue(DshLinkHealth.shouldResync(gapMs = 6 * 60 * 60 * 1000L))
    }

    @Test
    fun staleRecordsAreDropped() {
        val nowMs = 1_700_000_000_000L          // 记录时间戳是 epoch 秒
        val nowSec = nowMs / 1000.0
        assertFalse("刚发生的照常播", DshLinkHealth.isStale(nowSec - 5, nowMs))
        assertFalse("2 分钟以内还算新鲜", DshLinkHealth.isStale(nowSec - 60, nowMs))
        assertTrue("隔夜的不补播", DshLinkHealth.isStale(nowSec - 3600, nowMs))
        // 没有 ts 字段（旧桥/手工 fixture）时不在这一层拦截，交给重连策略处理。
        assertFalse(DshLinkHealth.isStale(0.0, nowMs))
    }

    @Test
    fun aTickNeverFloodsMoreThanAFewBubbles() {
        assertEquals(3, DshLinkHealth.MAX_SIGNALS_PER_TICK)
    }
}
