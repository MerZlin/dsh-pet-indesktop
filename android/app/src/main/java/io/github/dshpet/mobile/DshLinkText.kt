package io.github.dshpet.mobile

/**
 * 气泡文案的「短字符投递」规则：**一句话、最多 100 字**。
 *
 * 桥接记录里能带长文本（`question/requested` 的 `questions[].question` 常常是一整段），
 * 而桌宠气泡只适合一句话：手机上 100 字以内已经要占两行。所以统一在这里收口——
 * ≤100 字原样显示，超过就截成一句并加省略号，绝不让一整段话糊在桌宠头上。
 *
 * 纯函数、无 Android 依赖，便于单测。设备上不做"语义总结"（没有可用的模型，
 * 现编摘要等于编造），只做**按标点/空格的截断**；文案里出现的换行先压成空格。
 */
object DshLinkText {
    /** 气泡文案的字数上限（按 `String.length`，CJK 一字算一个）。 */
    const val MAX_CHARS = 100

    /** 截断时往前找标点的窗口占比：在最后 30% 里找得到标点就停在那儿。 */
    private const val SENTENCE_BREAK_RATIO = 0.7

    private val BREAKS = charArrayOf('。', '！', '？', '；', '，', '、', '!', '?', ';', ',', ' ')

    /** 气泡最短/最长停留：太短读不完，太长挡着桌宠。 */
    const val MIN_READ_MS = 5_000L
    const val MAX_READ_MS = 15_000L

    /** 每个字给多少阅读时间（正文是给人读的中文，80ms/字 ≈ 12.5 字/秒）。 */
    const val PER_CHAR_MS = 80L

    /**
     * 这条文案该在屏幕上停多久（毫秒）。
     *
     * 桌宠原本统一用「台词停留 3.2 秒」，那是给 5–10 字预设台词定的；联动消息可能是
     * 100 字正文，3.2 秒读不完，还会被下一句自说自话顶掉。这里按字数给时间并夹在
     * [MIN_READ_MS]..[MAX_READ_MS]。
     */
    fun readingMs(text: String): Long =
        (MIN_READ_MS + text.length.toLong() * PER_CHAR_MS).coerceIn(MIN_READ_MS, MAX_READ_MS)

    /**
     * 把 [raw] 压成一句 ≤[max] 字的文案；空白/null 返回 null（= 没有文案可用）。
     */
    fun brief(raw: String?, max: Int = MAX_CHARS): String? {
        if (raw == null) return null
        val flat = raw.replace(Regex("\\s+"), " ").trim()
        if (flat.isEmpty()) return null
        if (max <= 1) return if (flat.length <= max) flat else "…"
        if (flat.length <= max) return flat

        // 留一个字符给省略号，并尽量在句子边界收尾（不退到 max 的 30% 之前）。
        val limit = max - 1
        val floor = (limit * SENTENCE_BREAK_RATIO).toInt().coerceAtLeast(1)
        var cut = limit
        for (index in limit - 1 downTo floor) {
            if (flat[index] in BREAKS) {
                cut = index + 1
                break
            }
        }
        return flat.substring(0, cut).trimEnd() + "…"
    }
}
