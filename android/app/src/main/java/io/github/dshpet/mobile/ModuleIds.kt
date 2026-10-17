package io.github.dshpet.mobile

/**
 * The module ids. `PetOptions` gates every optional capability through these,
 * and each module declares its own id from the same table, so an enable bit can
 * never drift away from the module it belongs to.
 */
object ModuleIds {
    const val WANDER = "wander"
    const val SPEECH = "speech"
    const val CLICK_SOUND = "clickSound"
    const val COMPANION = "companion"
    const val COLLISION = "collision"
    const val DSH_LINK = "dshLink"

    val ALL = listOf(WANDER, SPEECH, CLICK_SOUND, COMPANION, COLLISION, DSH_LINK)
}
