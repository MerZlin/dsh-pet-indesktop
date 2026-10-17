package io.github.dshpet.mobile.modules

import io.github.dshpet.mobile.PetOptions
import kotlin.random.Random
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SpeechModuleTest {
    private fun fixture(options: PetOptions = PetOptions()): Pair<FakeHost, SpeechModule> {
        val host = FakeHost(options)
        host.pets += FakeHost.pet(0)
        host.pets += FakeHost.pet(1)
        val module = SpeechModule(Random(5))
        module.enable(host)
        return host to module
    }

    @Test
    fun aClickShowsOneOfTheUsersLines() {
        val (host, module) = fixture(PetOptions(phrases = listOf("只属于我的台词")))
        module.onEvent(PetEvent.Clicked(host.pets[0]))
        assertEquals(listOf("只属于我的台词"), host.phrases)
    }

    @Test
    fun anEmptyUserPoolFallsBackToTheBuiltInLines() {
        val (host, module) = fixture(PetOptions(phrases = emptyList()))
        module.onEvent(PetEvent.Clicked(host.pets[0]))
        assertEquals(listOf("好女孩……"), host.phrases)
    }

    @Test
    fun nothingIsShownWhenEvenTheBuiltInsAreGone() {
        val host = FakeHost(PetOptions())
        host.builtinPhrases.clear()
        val module = SpeechModule(Random(5))
        module.enable(host)
        module.onFrame(FakeHost.pet(), .016)
        module.onEvent(PetEvent.Clicked(FakeHost.pet()))
        assertEquals(0, host.phrases.size)
    }

    @Test
    fun theConfiguredGapGatesTheTimedLines() {
        val (host, module) = fixture(PetOptions(speechMinSeconds = 10f, speechMaxSeconds = 10f))
        module.onFrame(host.pets[0], .016)
        assertEquals(0, host.phrases.size)
        host.clock += 9_000
        module.onFrame(host.pets[0], .016)
        assertEquals(0, host.phrases.size)
        host.clock += 1_500
        module.onFrame(host.pets[0], .016)
        assertEquals(1, host.phrases.size)
    }

    @Test
    fun selfTalkYieldsWhileAnotherModuleHoldsTheBubble() {
        // 联动消息（DSH 正文）显示期间，自说自话必须让位——否则气泡被顶掉、
        // 用户读不完。占用窗口由显示方 holdBubble() 声明。
        val (host, module) = fixture(PetOptions(speechMinSeconds = 5f, speechMaxSeconds = 5f))
        host.holdBubble(8_000) // 占用到 clock+8000 = 9000
        host.clock += 5_100 // 6100：已过自说自话间隔，但仍在占用窗口内
        module.onFrame(host.pets[0], .016)
        assertEquals("占用期间不许发言", 0, host.phrases.size)

        host.clock += 3_000 // 9100：占用窗口已过期
        module.onFrame(host.pets[0], .016) // 这一拍只是排定下一次
        host.clock += 5_100
        module.onFrame(host.pets[0], .016)
        assertEquals("窗口过期后恢复自说自话", 1, host.phrases.size)
    }

    @Test
    fun companionsStayQuiet() {
        val (host, module) = fixture()
        module.onEvent(PetEvent.Clicked(host.pets[1]))
        module.onFrame(host.pets[1], .016)
        assertEquals(0, host.phrases.size)
    }

    @Test
    fun disablingClearsWhatIsOnScreenAndForgetsTheSchedule() {
        val (host, module) = fixture(PetOptions(speechMinSeconds = 5f, speechMaxSeconds = 5f))
        module.onFrame(host.pets[0], .016)
        host.clock += 6_000
        module.onFrame(host.pets[0], .016)
        assertEquals(1, host.phrases.size)
        module.disable()
        assertTrue(host.cleared.containsAll(listOf(0, 1)))
    }

    @Test
    fun aNewIntervalAppliesFromTheNextLine() {
        val (host, module) = fixture(PetOptions(speechMinSeconds = 600f, speechMaxSeconds = 600f))
        module.onFrame(host.pets[0], .016)
        host.clock += 601_000
        module.onFrame(host.pets[0], .016)
        assertEquals(1, host.phrases.size)
        host.options = host.options.copy(speechMinSeconds = 5f, speechMaxSeconds = 5f)
        module.onOptionsChanged()
        module.onFrame(host.pets[0], .016)
        assertEquals(1, host.phrases.size)
        host.clock += 6_000
        module.onFrame(host.pets[0], .016)
        assertEquals(2, host.phrases.size)
    }
}
