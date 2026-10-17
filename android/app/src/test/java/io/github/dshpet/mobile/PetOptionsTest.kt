package io.github.dshpet.mobile

import kotlin.random.Random
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PetOptionsTest {
    @Test
    fun eachModuleReadsItsSingleEnableBit() {
        val off = PetOptions(wander = false, speech = false, clickSound = false, companions = false,
            collision = false, dshLink = false)
        ModuleIds.ALL.forEach { assertFalse(it, off.enables(it)) }
        assertFalse(off.enables("no-such-module"))
        val on = PetOptions(wander = true, speech = true, clickSound = true, companions = true,
            collision = true, dshLink = true)
        ModuleIds.ALL.forEach { assertTrue(it, on.enables(it)) }
    }

    @Test
    fun newInstallsHaveAudibleTapFeedback() {
        assertTrue(PetOptions().clickSound)
        assertEquals(.7f, PetOptions().clickSoundVolume, .001f)
        assertEquals(true, PetOptions().companions)
        assertEquals(true, PetOptions().collision)
        assertEquals(Collision.RESTITUTION.toFloat(), PetOptions().collisionRestitution, .001f)
    }

    @Test
    fun anInvertedIntervalIsSwapNotAnError() {
        val fixed = PetOptions(speechMinSeconds = 300f, speechMaxSeconds = 10f).normalized()
        assertEquals(10f, fixed.speechMinSeconds, .001f)
        assertEquals(300f, fixed.speechMaxSeconds, .001f)
    }

    @Test
    fun valuesOutsideTheSlidersAreClamped() {
        val fixed = PetOptions(size = 99f, speechMinSeconds = 0f, speechMaxSeconds = 99_999f,
            speechSeconds = 0f, clickSoundVolume = 9f).normalized()
        assertEquals(1.6f, fixed.size, .001f)
        assertEquals(PetOptions.MIN_SECONDS, fixed.speechMinSeconds, .001f)
        assertEquals(PetOptions.MAX_SECONDS, fixed.speechMaxSeconds, .001f)
        assertEquals(PetOptions.MIN_SPEECH_SECONDS, fixed.speechSeconds, .001f)
        assertEquals(1f, fixed.clickSoundVolume, .001f)
    }

    @Test
    fun nonFiniteValuesFallBackToTheDefaults() {
        val fixed = PetOptions(size = Float.NaN, speechMinSeconds = Float.POSITIVE_INFINITY,
            speechSeconds = Float.NaN, clickSoundVolume = Float.NaN).normalized()
        assertEquals(1f, fixed.size, .001f)
        assertEquals(PetOptions.DEFAULT_MIN_SECONDS, fixed.speechMinSeconds, .001f)
        assertEquals(PetOptions.DEFAULT_SPEECH_SECONDS, fixed.speechSeconds, .001f)
        assertEquals(.7f, fixed.clickSoundVolume, .001f)
    }

    @Test
    fun theSpeechDelayStaysInsideTheConfiguredWindow() {
        val options = PetOptions(speechMinSeconds = 5f, speechMaxSeconds = 6f)
        val random = Random(11)
        repeat(200) {
            val delay = options.speechDelayMs(random)
            assertTrue("delay=$delay", delay in 5_000L..6_000L)
        }
        assertEquals(3_200L, PetOptions().speechDurationMs())
    }

    @Test
    fun aSingleValueWindowStillFires() {
        val options = PetOptions(speechMinSeconds = 5f, speechMaxSeconds = 5f)
        assertEquals(5_000L, options.speechDelayMs(Random(3)))
    }
}
