package io.github.dshpet.mobile

import android.content.Context
import android.content.SharedPreferences
import kotlin.random.Random

/** Phone-side preferences. Every optional capability has exactly one enable bit
 *  here, and that bit is what the module registry reads. */
data class PetOptions(
    val size: Float = 1f,
    val wander: Boolean = true,
    val speech: Boolean = true,
    val phrases: List<String> = emptyList(),
    val speechMinSeconds: Float = DEFAULT_MIN_SECONDS,
    val speechMaxSeconds: Float = DEFAULT_MAX_SECONDS,
    val speechSeconds: Float = DEFAULT_SPEECH_SECONDS,
    val clickSound: Boolean = true,
    val clickSoundVolume: Float = .7f,
    val clickSoundUri: String? = null,
    val companions: Boolean = true,
    val collision: Boolean = true,
    val collisionRestitution: Float = DEFAULT_RESTITUTION,
    /** 电脑端 DSH 联动：默认关，关闭时模块不会被创建（0.2.1 的「无网络请求」口径）。 */
    val dshLink: Boolean = false,
    /** 电脑端只读事件流地址，如 `192.168.1.5:8768`；空 = 未配对。 */
    val dshLinkUrl: String = "",
    /** 与地址配对的令牌（电脑上 `<桥目录>/lan-token.txt`）。 */
    val dshLinkToken: String = "",
) {
    companion object {
        const val DEFAULT_MIN_SECONDS = 20f
        const val DEFAULT_MAX_SECONDS = 60f
        const val DEFAULT_SPEECH_SECONDS = 3.2f
        const val MIN_SECONDS = 5f
        const val MAX_SECONDS = 600f
        const val MIN_SPEECH_SECONDS = 1f
        const val MAX_SPEECH_SECONDS = 30f

        /** The desktop's `collision_restitution` default. */
        const val DEFAULT_RESTITUTION = Collision.RESTITUTION.toFloat()
    }

    /** A configured interval can never invert: min ≤ max, both inside range. */
    fun normalized(): PetOptions {
        val min = finite(speechMinSeconds, DEFAULT_MIN_SECONDS).coerceIn(MIN_SECONDS, MAX_SECONDS)
        val max = finite(speechMaxSeconds, DEFAULT_MAX_SECONDS).coerceIn(MIN_SECONDS, MAX_SECONDS)
        return copy(
            size = finite(size, 1f).coerceIn(.6f, 1.6f),
            clickSoundVolume = finite(clickSoundVolume, .7f).coerceIn(0f, 1f),
            speechMinSeconds = minOf(min, max),
            speechMaxSeconds = maxOf(min, max),
            speechSeconds = finite(speechSeconds, DEFAULT_SPEECH_SECONDS)
                .coerceIn(MIN_SPEECH_SECONDS, MAX_SPEECH_SECONDS),
            collisionRestitution = finite(collisionRestitution, DEFAULT_RESTITUTION)
                .coerceIn(0f, 1f),
            dshLinkUrl = dshLinkUrl.trim(),
            dshLinkToken = dshLinkToken.trim(),
        )
    }

    fun speechDelayMs(random: Random = Random.Default): Long {
        val normalized = normalized()
        val low = (normalized.speechMinSeconds * 1000).toLong()
        val high = (normalized.speechMaxSeconds * 1000).toLong()
        return if (high <= low) low else low + random.nextLong(high - low + 1)
    }

    fun speechDurationMs(): Long = (normalized().speechSeconds * 1000).toLong()

    /** The single enable bit for each module; the module list and the user
     *  switches cannot disagree because both read this. */
    fun enables(moduleId: String): Boolean = when (moduleId) {
        ModuleIds.WANDER -> wander
        ModuleIds.SPEECH -> speech
        ModuleIds.CLICK_SOUND -> clickSound
        ModuleIds.COMPANION -> companions
        ModuleIds.COLLISION -> collision
        ModuleIds.DSH_LINK -> dshLink
        else -> false
    }

    private fun finite(value: Float, fallback: Float) = if (value.isFinite()) value else fallback
}

class PetPreferences(context: Context) {
    companion object {
        // Module enable bits reuse the feature keys that already existed.
        const val KEY_WANDER = "wander"
        const val KEY_SPEECH = "speech"
        const val KEY_SIZE = "size"
        private const val KEY_PHRASES = "phrases"
        private const val KEY_SPEECH_MIN = "speechMin"
        private const val KEY_SPEECH_MAX = "speechMax"
        private const val KEY_SPEECH_SECONDS = "speechSeconds"
        private const val KEY_CLICK_SOUND = "clickSound"
        private const val KEY_CLICK_VOLUME = "clickVolume"
        private const val KEY_CLICK_URI = "clickUri"
        private const val KEY_COMPANIONS = "companions"
        private const val KEY_COMPANION_LAYOUT = "companionLayout"
        private const val KEY_COLLISION = "collision"
        private const val KEY_COLLISION_RESTITUTION = "collisionRestitution"
        private const val KEY_DSH_LINK = "dshLink"
        private const val KEY_DSH_LINK_URL = "dshLinkUrl"
        private const val KEY_DSH_LINK_TOKEN = "dshLinkToken"
        private const val KEY_POSITION_X = "x"
        private const val KEY_POSITION_Y = "y"

        val WATCHED_KEYS = setOf(KEY_SIZE, KEY_WANDER, KEY_SPEECH, KEY_PHRASES, KEY_SPEECH_MIN,
            KEY_SPEECH_MAX, KEY_SPEECH_SECONDS, KEY_CLICK_SOUND, KEY_CLICK_VOLUME, KEY_CLICK_URI,
            KEY_COMPANIONS, KEY_COLLISION, KEY_COLLISION_RESTITUTION, KEY_DSH_LINK,
            KEY_DSH_LINK_URL, KEY_DSH_LINK_TOKEN)
    }

    private val prefs = context.getSharedPreferences("pet", Context.MODE_PRIVATE)

    fun options() = PetOptions(
        size = prefs.getFloat(KEY_SIZE, 1f),
        wander = prefs.getBoolean(KEY_WANDER, true),
        speech = prefs.getBoolean(KEY_SPEECH, true),
        phrases = PhraseBook.clean(prefs.getString(KEY_PHRASES, "").orEmpty().split('\n')),
        speechMinSeconds = prefs.getFloat(KEY_SPEECH_MIN, PetOptions.DEFAULT_MIN_SECONDS),
        speechMaxSeconds = prefs.getFloat(KEY_SPEECH_MAX, PetOptions.DEFAULT_MAX_SECONDS),
        speechSeconds = prefs.getFloat(KEY_SPEECH_SECONDS, PetOptions.DEFAULT_SPEECH_SECONDS),
        clickSound = prefs.getBoolean(KEY_CLICK_SOUND, true),
        clickSoundVolume = prefs.getFloat(KEY_CLICK_VOLUME, .7f),
        clickSoundUri = prefs.getString(KEY_CLICK_URI, null),
        companions = prefs.getBoolean(KEY_COMPANIONS, true),
        collision = prefs.getBoolean(KEY_COLLISION, true),
        collisionRestitution = prefs.getFloat(KEY_COLLISION_RESTITUTION,
            PetOptions.DEFAULT_RESTITUTION),
        dshLink = prefs.getBoolean(KEY_DSH_LINK, false),
        dshLinkUrl = prefs.getString(KEY_DSH_LINK_URL, "").orEmpty(),
        dshLinkToken = prefs.getString(KEY_DSH_LINK_TOKEN, "").orEmpty(),
    ).normalized()

    fun save(options: PetOptions) {
        val normalized = options.normalized()
        prefs.edit()
            .putFloat(KEY_SIZE, normalized.size)
            .putBoolean(KEY_WANDER, normalized.wander)
            .putBoolean(KEY_SPEECH, normalized.speech)
            .putString(KEY_PHRASES, PhraseBook.asText(PhraseBook.clean(normalized.phrases)))
            .putFloat(KEY_SPEECH_MIN, normalized.speechMinSeconds)
            .putFloat(KEY_SPEECH_MAX, normalized.speechMaxSeconds)
            .putFloat(KEY_SPEECH_SECONDS, normalized.speechSeconds)
            .putBoolean(KEY_CLICK_SOUND, normalized.clickSound)
            .putFloat(KEY_CLICK_VOLUME, normalized.clickSoundVolume)
            .putString(KEY_CLICK_URI, normalized.clickSoundUri)
            .putBoolean(KEY_COMPANIONS, normalized.companions)
            .putBoolean(KEY_COLLISION, normalized.collision)
            .putFloat(KEY_COLLISION_RESTITUTION, normalized.collisionRestitution)
            .putBoolean(KEY_DSH_LINK, normalized.dshLink)
            .putString(KEY_DSH_LINK_URL, normalized.dshLinkUrl)
            .putString(KEY_DSH_LINK_TOKEN, normalized.dshLinkToken)
            .apply()
    }

    fun position(): Pair<Double, Double> {
        fun coordinate(key: String, fallback: Float) =
            (prefs.getFloat(key, fallback).takeIf { it.isFinite() } ?: fallback).coerceIn(0f, 1f).toDouble()
        return coordinate(KEY_POSITION_X, .7f) to coordinate(KEY_POSITION_Y, 1f)
    }

    fun savePosition(x: Double, y: Double) {
        if (x.isFinite() && y.isFinite()) {
            prefs.edit().putFloat(KEY_POSITION_X, x.coerceIn(0.0, 1.0).toFloat())
                .putFloat(KEY_POSITION_Y, y.coerceIn(0.0, 1.0).toFloat()).apply()
        }
    }

    fun companionLayout(): List<Pair<Double, Double>> =
        CompanionCodec.decode(prefs.getString(KEY_COMPANION_LAYOUT, null))

    fun saveCompanionLayout(layout: List<Pair<Double, Double>>) {
        prefs.edit().putString(KEY_COMPANION_LAYOUT, CompanionCodec.encode(layout)).apply()
    }

    fun reset() = prefs.edit().clear().apply()

    fun subscribe(listener: SharedPreferences.OnSharedPreferenceChangeListener) =
        prefs.registerOnSharedPreferenceChangeListener(listener)

    fun unsubscribe(listener: SharedPreferences.OnSharedPreferenceChangeListener) =
        prefs.unregisterOnSharedPreferenceChangeListener(listener)
}
