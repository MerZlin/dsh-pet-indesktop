package io.github.dshpet.mobile

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
}
