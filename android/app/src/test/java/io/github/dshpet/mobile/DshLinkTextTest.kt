package io.github.dshpet.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 气泡「短字符投递」规则：一句话、最多 100 字。
 *
 * 手机屏幕上一段长文本会把桌宠整个埋掉；桥接记录里的 `questions[].question` 常常是
 * 整段话。这里钉住的是**用户能看懂的上限**：≤100 字原样显示，超了截成一句加省略号。
 */
class DshLinkTextTest {

    /** 生成 n 个汉字（不带标点），便于精确控制长度。 */
    private fun cjk(n: Int): String = "深".repeat(n)

    @Test
    fun blankInputHasNoText() {
        assertNull(DshLinkText.brief(null))
        assertNull(DshLinkText.brief(""))
        assertNull(DshLinkText.brief("   \n\t "))
    }

    @Test
    fun shortTextIsShownVerbatim() {
        assertEquals("干完活啦", DshLinkText.brief("干完活啦"))
        assertEquals("要不要现在跑测试？", DshLinkText.brief("  要不要现在跑测试？  "))
    }

    @Test
    fun exactlyTheLimitIsStillComplete() {
        val text = cjk(DshLinkText.MAX_CHARS)
        assertEquals(DshLinkText.MAX_CHARS, text.length)
        assertEquals("100 字必须完整显示（不能提前截）", text, DshLinkText.brief(text))
    }

    @Test
    fun oneCharOverTheLimitIsCut() {
        val text = cjk(DshLinkText.MAX_CHARS + 1)
        val brief = DshLinkText.brief(text)!!
        assertTrue("结果必须 ≤ 上限，实际 ${brief.length}", brief.length <= DshLinkText.MAX_CHARS)
        assertTrue("截断必须带省略号", brief.endsWith("…"))
    }

    @Test
    fun longParagraphIsCutAtASentenceBoundary() {
        // 句号落在靠后的位置（85 字处，在最后 30% 窗口内）：应该停在句号处。
        val text = cjk(85) + "。" + cjk(85)
        val brief = DshLinkText.brief(text)!!
        assertTrue(brief.length <= DshLinkText.MAX_CHARS)
        assertTrue("应停在句子边界：len=${brief.length}", brief.endsWith("。…"))
    }

    @Test
    fun aBoundaryTooEarlyIsIgnoredInsteadOfCuttingHalfTheMessage() {
        // 句号在 20 字处（远早于窗口下界）：不许为了"停在标点"把 100 字砍成 20 字。
        val text = cjk(20) + "。" + cjk(200)
        val brief = DshLinkText.brief(text)!!
        assertEquals(DshLinkText.MAX_CHARS, brief.length)
        assertTrue(brief.endsWith("…"))
    }

    @Test
    fun noPunctuationStillCutsAtTheLimit() {
        val brief = DshLinkText.brief(cjk(500))!!
        assertEquals(DshLinkText.MAX_CHARS, brief.length)
        assertTrue(brief.endsWith("…"))
    }

    @Test
    fun newlinesAndRepeatedSpacesCollapseIntoOneLine() {
        val brief = DshLinkText.brief("第一行\n\n第二行\t第三行")!!
        assertEquals("第一行 第二行 第三行", brief)
        assertTrue("气泡必须是一行", !brief.contains('\n'))
    }

    @Test
    fun theLimitIsConfigurable() {
        assertEquals("ab…", DshLinkText.brief("abcdef", max = 3))
        assertEquals("abc", DshLinkText.brief("abc", max = 3))
    }
}
