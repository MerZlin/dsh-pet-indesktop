package io.github.dshpet.mobile.modules

import android.content.Context
import io.github.dshpet.mobile.ClipSpec
import io.github.dshpet.mobile.CompanionCodec
import io.github.dshpet.mobile.PetEngine
import io.github.dshpet.mobile.PetHandle
import io.github.dshpet.mobile.PetOptions
import io.github.dshpet.mobile.PhraseBook
import io.github.dshpet.mobile.SourceBox

/** A host with no Android in it, so modules can be exercised on the JVM. */
internal class FakeHost(override var options: PetOptions) : ModuleHost {
    override val builtinPhrases: MutableList<String> = mutableListOf("好女孩……")
    override val pets: MutableList<PetHandle> = mutableListOf()
    override val bodyBox = SourceBox(212.0, 60.0, 428.0, 330.0)
    override val context: Context
        get() = throw UnsupportedOperationException("unit tests have no Android context")

    var clock = 1_000L
    val phrases = mutableListOf<String>()
    val cleared = mutableListOf<Int>()
    val squashed = mutableListOf<Int>()
    var spawnCalls = 0
    var exitCalls = 0
    var liveCompanions = 0

    override fun now() = clock

    override fun scaleRatio(pet: PetHandle) =
        if (pet.isCompanion) CompanionCodec.COMPANION_SCALE else 1.0

    override fun effectivePhrases() = PhraseBook.resolve(options.phrases, builtinPhrases)

    override fun showPhrase(pet: PetHandle, text: String, durationMs: Long) {
        phrases += text
    }

    override fun clearPhrase(pet: PetHandle) {
        cleared += pet.id
    }

    /** 气泡占用窗口（测试里用假时钟推进）。 */
    var bubbleHoldUntil = 0L

    override fun holdBubble(durationMs: Long) {
        bubbleHoldUntil = clock + durationMs
    }

    override fun bubbleHeld(): Boolean = clock < bubbleHoldUntil

    override fun squash(pet: PetHandle) {
        squashed += pet.id
    }

    override fun companionCount() = liveCompanions

    override fun spawnCompanion(): Boolean {
        spawnCalls++
        liveCompanions++
        return true
    }

    override fun exitCompanion(pet: PetHandle): Boolean {
        exitCalls++
        liveCompanions = (liveCompanions - 1).coerceAtLeast(0)
        return true
    }

    companion object {
        val CLIPS = listOf(
            ClipSpec("idle", "idle", 24.0, 4, crop = SourceBox(0.0, 0.0, 10.0, 10.0)),
            ClipSpec("turn", "turn", 24.0, 4, crop = SourceBox(0.0, 0.0, 10.0, 10.0)),
            ClipSpec("move", "move", 24.0, 4, crop = SourceBox(0.0, 0.0, 10.0, 10.0)),
            ClipSpec("click", "click", 24.0, 4, crop = SourceBox(0.0, 0.0, 10.0, 10.0)),
            ClipSpec("drag", "drag", 24.0, 4, crop = SourceBox(0.0, 0.0, 10.0, 10.0)),
        )

        fun pet(id: Int = 0): PetHandle = PetHandle(id, PetEngine(CLIPS))
    }
}
