package io.github.dshpet.mobile.modules

import android.content.Context
import io.github.dshpet.mobile.PetHandle
import io.github.dshpet.mobile.PetOptions
import io.github.dshpet.mobile.SourceBox

/**
 * Everything a module may reach. Kept deliberately small: a module decides, the
 * service owns Android state (windows, decoders, the clock).
 */
interface ModuleHost {
    /** Pure snapshot of the current preferences; module gating reads it. */
    val options: PetOptions

    /** The built-in lines shipped in the asset manifest. */
    val builtinPhrases: List<String>

    /** Snapshot of every live pet. */
    val pets: List<PetHandle>

    /** The character's body box in source pixels; the collision anchor. */
    val bodyBox: SourceBox

    /** For modules that talk to framework services (audio). */
    val context: Context

    fun now(): Long

    /** How much bigger this pet is drawn than the mother (1.0 for the mother). */
    fun scaleRatio(pet: PetHandle): Double

    /** The user pool when it is non-empty, otherwise the built-in lines. */
    fun effectivePhrases(): List<String>

    fun showPhrase(pet: PetHandle, text: String, durationMs: Long)

    fun clearPhrase(pet: PetHandle)

    /**
     * 占用气泡到 `now() + durationMs`：期间别的模块（自说自话）不要抢。
     *
     * 气泡只有一个槽位（`PetOverlayService.showPhrase` 改的是同一个 view），联动消息
     * 刚显示就被下一句预设台词顶掉的话，用户根本读不完。
     */
    fun holdBubble(durationMs: Long)

    /** 气泡是否还被别人占着（`SpeechModule` 让位用）。 */
    fun bubbleHeld(): Boolean

    /** The desktop's Q 弹 feedback, used by collision hits. */
    fun squash(pet: PetHandle)

    fun companionCount(): Int

    fun spawnCompanion(): Boolean

    /** Exits [pet] when it is a companion, otherwise the newest companion. */
    fun exitCompanion(pet: PetHandle): Boolean
}

/** A long-press menu entry contributed by a module. */
data class ModuleMenu(val labelRes: Int, val action: () -> Unit)

sealed class PetEvent {
    abstract val pet: PetHandle

    data class Pressed(override val pet: PetHandle) : PetEvent()

    data class Clicked(override val pet: PetHandle) : PetEvent()

    data class Spawned(override val pet: PetHandle) : PetEvent()

    data class Exited(override val pet: PetHandle) : PetEvent()
}

/**
 * One optional capability. A module that is switched off is never constructed,
 * so it owns no window, no thread, no decoder cursor and receives no per-frame
 * callback: "off" costs nothing to run.
 */
interface PetModule {
    val id: String

    /** Called once when the module becomes enabled; keep what you need. */
    fun enable(host: ModuleHost)

    /** Called once when it becomes disabled; release everything you kept. */
    fun disable() {}

    /**
     * Once per frame, before the per-pet callbacks, for modules that need the
     * whole household at once (collision). [host].pets is the current snapshot.
     */
    fun onTick(dt: Double) {}

    fun onFrame(pet: PetHandle, dt: Double) {}

    fun onEvent(event: PetEvent) {}

    fun menuItems(pet: PetHandle): List<ModuleMenu> = emptyList()

    /** Live preferences changed while the module stays enabled. */
    fun onOptionsChanged() {}

    /** Extra `dump()` fields; empty when the module has nothing to report. */
    fun diagnostics(): String = ""
}

/** Removes the enable/disable bookkeeping from each module. */
abstract class HostBoundModule : PetModule {
    protected lateinit var host: ModuleHost
    private var enabled = false

    final override fun enable(host: ModuleHost) {
        if (enabled) return
        this.host = host
        enabled = true
        onEnable()
    }

    final override fun disable() {
        if (!enabled) return
        enabled = false
        onDisable()
    }

    protected open fun onEnable() {}

    protected open fun onDisable() {}
}

/** A module id plus the factory that creates it, so a disabled module is never built. */
data class ModuleFactory(val id: String, val create: () -> PetModule)
