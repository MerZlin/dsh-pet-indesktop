package io.github.dshpet.mobile.modules

import io.github.dshpet.mobile.ModuleIds
import io.github.dshpet.mobile.PetHandle
import io.github.dshpet.mobile.PhraseBook
import kotlin.random.Random

/**
 * The speech bubble and the self-talk schedule. Disabled, no bubble window is
 * ever created and no per-frame timer is evaluated; the interval and duration
 * come from the phone settings (20–60 s and 3.2 s by default, like the desktop).
 */
class SpeechModule(private val random: Random = Random.Default) : HostBoundModule() {
    override val id = ModuleIds.SPEECH

    private val nextAt = HashMap<Int, Long>()
    private var spoken = 0L

    override fun onFrame(pet: PetHandle, dt: Double) {
        if (pet.isCompanion || pet.engine.paused) return
        // 气泡只有一个槽位：联动消息（DSH 事件）刚显示时让位，否则用户读不完就被
        // 下一句自说自话顶掉了。占用窗口由显示方调用 holdBubble() 声明。
        if (host.bubbleHeld()) return
        val pool = host.effectivePhrases()
        if (pool.isEmpty()) return
        val now = host.now()
        val due = nextAt.getOrPut(pet.id) { now + host.options.speechDelayMs(random) }
        if (now < due) return
        nextAt[pet.id] = now + host.options.speechDelayMs(random)
        speak(pet, pool)
    }

    override fun onEvent(event: PetEvent) {
        if (event !is PetEvent.Clicked || event.pet.isCompanion) return
        speak(event.pet, host.effectivePhrases())
    }

    override fun onOptionsChanged() {
        // A new interval or a new pool applies from the next line, not the next session.
        nextAt.clear()
    }

    override fun onDisable() {
        host.pets.forEach { host.clearPhrase(it) }
        nextAt.clear()
    }

    override fun diagnostics() = "spoken=$spoken"

    private fun speak(pet: PetHandle, pool: List<String>) {
        val line = PhraseBook.pick(pool, random) ?: return
        spoken++
        host.showPhrase(pet, line, host.options.speechDurationMs())
    }
}
