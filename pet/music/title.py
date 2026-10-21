# -*- coding: utf-8 -*-
"""把「原始标题」洗成「取词查询词（歌名 / 歌手）」。纯函数、零 Qt。

两个来源的标题都需要清洗，而且都不是结构化的：

1. **浏览器媒体会话**（``now_playing.is_browser_session`` 认得的会话）：SMTC 给的
   title 就是网页标题。B 站是「【4K】周杰伦《晴天》官方MV_哔哩哔哩_bilibili」，
   直接拿去搜歌词命中率极低（issue #228 的 B 站侧）。
2. **foobar2000 窗口标题**（issue #228 本体）：foobar 默认不注册 SMTC，只能从
   窗口标题拿曲目信息；而窗口标题是**用户可配置的 titleformat 串**，出厂默认值
   随版本/主题变化，社区常见的有 ``%artist% - %title%``、``[%artist%][ - %title%]``
   等（见 foobar2000 的「显示 → 默认用户界面 → 窗口标题」）。所以解析要么按用户
   填的格式串切，要么不猜。

## 三条硬规则

**只删片段，不改写。** 清洗结果必须是原始输入的**子序列**：不补字、不改字、不
翻译。这样"洗坏"只可能洗成"搜不到"，不可能洗成"搜到别人"。

**空/过短整体退回原文。** 清洗后歌名不足 2 字（或为空）时，连歌手一起退回调用方
传来的原值——这是仓库明文反对白名单式误伤的同一条纪律：宁可这条搜不到，也不能
把用户的歌名换成一个搜得到但不对的东西。

**顺序不可知时不猜。** ``A - B`` 到底是「歌名 - 歌手」还是「歌手 - 歌名」，从字符串
本身看不出来（YouTube 标题是前者、中文曲库是后者，两种都常见）。此时把整条清洗
结果**同时**当作歌名与歌手，让下游自己收敛：

- 取词搜索串是 ``title + " " + artist``（拼接，与顺序无关）；
- 歌手校验 ``music_lyric._name_matches`` 是**子串包含**（两侧都写成多歌手形态时，
  分隔符归一后再逐段**相等**比一次），而整条标题必然包含歌手名。

于是两种顺序都能命中，且不必押注哪一侧是歌手。押错了的代价不是"搜不到"而是
"把歌名当歌手名去校验"，那会把任意同名的歌贴上来——比搜不到更糟。

## 括号的两套待遇（这是刻意的，不是疏漏）

- ``【】`` / ``[]`` / ``（）`` / ``()``：**删括号、留内容**。它们既可能是质量标签
  （``【4K】``，命中噪声表 → 整组删掉），也可能是歌手/来源前缀（``【周杰伦】``、
  ``【某某UP】``）——留着内容，后者还能被当成歌手，前者也不会因为没登记就丢掉歌名。
- ``「」`` / ``『』``：按需求口径当作**分区标签**，整组（含内容）删掉。

## 噪声词是标签不是内容

``官方MV`` / ``高清`` / ``4K`` / ``无损`` / ``现场版`` / ``Live`` / ``歌词版`` 等一律
按"整段标签"剥除，而不是把关键字从文本里抠掉：抠字会把 ``4K`` 从 ``4K修复`` 里
挖出一个洞，剩下一堆碎片反而更难搜。
"""

from __future__ import annotations

import re
from functools import lru_cache

# 清洗结果的可用下限（去空白与分隔符后的字符数）。低于它一律退回原文。
MIN_QUERY_CHARS = 2

# foobar 窗口标题格式串的长度上限：手改配置写进超长正则会把每次采样的匹配代价
# 拖上去，且格式串本来就该是一行。超出部分截断。
FOOBAR_FORMAT_MAX_CHARS = 120

# ascii 站位：紧凑化时去掉的字符（空白 + 各类分隔符/引号）。
_COMPACT_RE = re.compile(r"[\s\-–—|·_/、,，;；:：'\"“”‘’（）()【】\[\]「」『』《》]+")
# 分段分隔符（**不含空白**）：歌手名里常有空格（"Jay Chou"），不能按空白切。
_SPLIT_RE = re.compile(r"[-–—|·/、,，;；:：]+")
# 数"段数"用的分隔符（**含空白**）：判断标题里除了歌名还有没有别的东西。
_TOKEN_RE = re.compile(r"[\s\-–—|·/、,，;；:：]+")
# 至少一个"有意义的字"（字母/数字/CJK 等），防止结果只剩标点。
_WORD_RE = re.compile(r"\w")

# 站点后缀：形如「... - 哔哩哔哩」「..._哔哩哔哩_bilibili」「... - YouTube Music」。
_SITE_TAIL_RE = re.compile(
    r"[\s_\-–—|·]*(?:哔哩哔哩(?:_bilibili)?|bilibili(?:\.com)?|youtube(?:\s+music)?|"
    r"网易云音乐|酷狗音乐|qq音乐|咪咕音乐|抖音|快手|微博|腾讯视频|爱奇艺|优酷|西瓜视频)"
    r"\s*$",
    re.IGNORECASE,
)

# 来源段：``UP主: 某某某`` / ``作者：某某某``。前面的否定后顾是防 "Su**up**er: xxx"
# 这类拉丁词被误切；内容段停在下一个分隔符处。
_SOURCE_SEGMENT_RE = re.compile(
    r"[\s\-–—|·]*(?:(?<![A-Za-z])UP主?|作者|投稿人|投稿者|发布者|搬运)"
    r"\s*[:：]\s*[^\s\-–—|·_]*",
    re.IGNORECASE,
)

# 书名号里的歌名（优先）。中文标题里《》最无歧义。
_SONG_RE = re.compile(r"《([^》]+)》")

# 括号配对：第一组删括号留内容，第二组（「」）整组删掉。
_UNWRAP_PAIRS = (("【", "】"), ("[", "]"), ("（", "）"), ("(", ")"))
_RIPPLE_PAIRS = (("「", "」"), ("『", "』"))

# 噪声标签：**整组**（含括号）删掉，或整段（裸标签）跳过。
#
# exact 只做整串相等匹配（短拉丁标签如 mv/hd，做子串匹配会误伤
# 「[周杰伦MV精选]」这类带内容的括号）；loose 做子串匹配（多字词足够长，
# 误伤面小），专门吃掉「【4K修复】」「【Hi-Res无损】」这种带修饰的写法。
_NOISE_EXACT = frozenset(
    {
        "mv", "hd", "hq", "4k", "8k", "2k", "1080p", "720p", "2160p",
        "flac", "ape", "wav", "mp3", "hires", "m4a",
        "op", "ed", "pv", "mad", "amv", "cover", "b站", "bilibili",
    }
)
_NOISE_LOOSE = (
    "官方mv", "官方视频", "官方音频", "官方版", "official",
    "无损", "高清", "超清", "蓝光", "高音质", "音质", "修复版", "重制版",
    "现场版", "演唱会", "完整版", "纯享", "试听", "母带",
    "歌词版", "动态歌词", "字幕", "中字", "双语", "花絮", "预告", "片头", "片尾",
    "合集", "全集", "翻唱", "纯音乐", "歌曲", "歌单",
    "4k", "8k", "1080p", "720p", "2160p", "hires",
)


def _compact(text: str) -> str:
    """紧凑形式（去空白/分隔符/引号 + 小写），只用于比较与判长短。"""
    return _COMPACT_RE.sub("", str(text or "")).lower()


def _usable(value: str) -> bool:
    """这个查询词能不能用：至少 :data:`MIN_QUERY_CHARS` 个有意义的字、且不是纯数字。"""
    compact = _compact(value)
    return len(compact) >= MIN_QUERY_CHARS and not compact.isdigit()


def _is_noise(text: str) -> bool:
    """该片段是不是"标签"（噪声词）——整段相等或包含长噪声词都算。"""
    compact = _compact(text)
    if not compact:
        return False
    if compact in _NOISE_EXACT:
        return True
    return any(token in compact for token in _NOISE_LOOSE)


def _collapse(text: str) -> str:
    """收敛空白、去掉首尾残留的分隔符（删掉一段后常留下悬空的 " - "）。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = text.strip(" \t-–—|·_/、,，;；:：")
    return re.sub(r"\s+", " ", text).strip()


def _strip_sites(title: str) -> str:
    """反复剥站点后缀（部分标题同时带 "网站名_站点名" 两段）。"""
    text = title
    for _ in range(3):
        stripped = _SITE_TAIL_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return text


def _strip_source_segments(title: str) -> str:
    """剥 ``UP主: xxx`` 这类来源段。"""
    return _SOURCE_SEGMENT_RE.sub(" ", title)


def _strip_brackets(title: str, *, drop_ripple: bool) -> str:
    """括号处理：``【】[]（）()`` 删括号留内容；``「」『』`` 按需整组删掉。

    ``drop_ripple=False`` 时「」也只删括号留内容——用于生成歌手 haystack
    （见 :func:`_haystack_artist`），让被当标签删掉的内容仍能参与歌手校验。
    """
    text = title
    for open_ch, close_ch in _UNWRAP_PAIRS + _RIPPLE_PAIRS:
        drop_content = drop_ripple and (open_ch, close_ch) in _RIPPLE_PAIRS
        out: list[str] = []
        cursor = 0
        while True:
            start = text.find(open_ch, cursor)
            if start < 0:
                out.append(text[cursor:])
                break
            end = text.find(close_ch, start + 1)
            if end < 0:
                # 括号没闭合（残缺标题）：原样留着，别把后半条一起吃掉。
                out.append(text[cursor:])
                break
            out.append(text[cursor:start])
            content = text[start + 1:end]
            if not drop_content and not _is_noise(content):
                out.append(content)
            cursor = end + 1
        text = "".join(out)
    return text


def _strip_chrome(title: str, *, drop_ripple: bool) -> str:
    """站点后缀 → 来源段 → 括号 → 收敛空白。清洗的完整一遍。"""
    text = _strip_source_segments(_strip_sites(title))
    text = _strip_brackets(text, drop_ripple=drop_ripple)
    return _collapse(text)


def _first_song_name(text: str) -> str:
    """取第一个不是标签的书名号内容；没有就返回空串。"""
    for match in _SONG_RE.finditer(text):
        content = match.group(1).strip()
        if content and not _is_noise(content):
            return content
    return ""


def _singer_near_song(text: str, song: str) -> str:
    """书名号外的"最近一段"就是歌手：前面取最后一段、后面取第一段。

    前取"最后"、后取"第一"是因为距离歌名越近越可能是歌手——``【某某UP】周杰伦
    《晴天》【某某UP】`` 这种两头都挂来源段的标题，取到的仍是贴着书名号的歌手。
    逐段跳过噪声标签，于是 ``《晴天》官方MV`` 不会把 "官方MV" 当成歌手。

    定位的是**歌名所在的那对书名号**（不是"第一对"）：歌名可能来自靠后的书名号
    （前面那对是 ``《官方MV》`` 这类噪声段）。定位不到就返回空——不猜。
    """
    found = None
    for match in _SONG_RE.finditer(text):
        if match.group(1).strip() == song:
            found = match
            break
    if found is None:
        return ""
    for chunk, take_last in (
        # 别的书名号段（如前面那对噪声段）不该混进歌手名。
        (_SONG_RE.sub(" ", text[: found.start()]), True),
        (_SONG_RE.sub(" ", text[found.end():]), False),
    ):
        parts = [part.strip() for part in _SPLIT_RE.split(chunk)]
        parts = [part for part in parts if part]
        for part in reversed(parts) if take_last else parts:
            if part and not _is_noise(part):
                return part
    return ""


def _haystack_artist(main: str, haystack: str) -> str:
    """顺序不可知时的歌手位：整条清洗文本，只在这个位置用。

    两条不用的理由（都属于"宁可搜不到"）：

    - 标题只有一个词、且没有任何被剥掉的内容（``晴天``）：它本身不含歌手信息，
      塞进歌手位只会让 ``_name_matches`` 拿歌名当歌手名去匹配；
    - 括号内容被按标签删掉、原文又只剩一段时，同样无助于校验。

    其余情况（有分隔符、或括号内容带来了额外信息）用整条文本，两种顺序都能过
    ``_name_matches`` 的子串校验（多歌手的「、」「/」等写法差异由分隔符归一后的
    逐段相等比对补上）。
    """
    if _compact(haystack) == _compact(main) and len(_tokens(main)) < 2:
        return ""
    return haystack.strip()


def _tokens(text: str) -> list[str]:
    return [part for part in _TOKEN_RE.split(str(text or "")) if part]


def _pick_side_by_artist(text: str, artist: str) -> str:
    """已知歌手时用它把两段切开：等于歌手名的那段摘出去，剩下的是歌名。"""
    parts = [part.strip() for part in _SPLIT_RE.split(text)]
    parts = [part for part in parts if part]
    if len(parts) != 2:
        return text
    wanted = _compact(artist)
    if not wanted:
        return text
    for index, part in enumerate(parts):
        compact = _compact(part)
        if compact and (compact in wanted or wanted in compact):
            other = parts[1 - index]
            return other if other else text
    return text


def clean_web_title(raw_title: str, raw_artist: str = "") -> tuple[str, str]:
    """网页/浏览器会话标题 → ``(歌名, 歌手)`` 取词查询词。

    ``raw_artist`` 是 SMTC 给的歌手字段（浏览器通常为空）。有值时以它为准，并用
    它把 ``A - B`` 的顺序切开（见 :func:`_pick_side_by_artist`）。

    **清洗失败（结果为空或不足 :data:`MIN_QUERY_CHARS` 字）时原样返回入参**，
    调用方拿到的仍然是能用的东西——只是这一条搜不到，绝不会被改成一个错的歌名。
    """
    original = str(raw_title or "")
    artist_in = str(raw_artist or "").strip()
    if not _usable(original):
        return original, str(raw_artist or "")

    main = _strip_chrome(original, drop_ripple=True)
    song = _first_song_name(main)
    if song:
        title = song
        artist = artist_in or _singer_near_song(main, song)
    else:
        # 没有书名号：先看歌手字段能不能切开顺序，不能就不猜。
        title = _pick_side_by_artist(main, artist_in) if artist_in else main
        artist = artist_in or _haystack_artist(
            main, _strip_chrome(original, drop_ripple=False)
        )

    title = _collapse(title)
    artist = _collapse(artist)
    if not _usable(title):
        return original, str(raw_artist or "")
    return title, artist_in or (artist if _usable(artist) else "")


# ------------------------------------------------------ foobar2000 窗口标题解析
#
# **这里刻意不用"把格式串编译成正则"的做法**（2026-10 扩大终审 M7）：占位符翻译
# 成 `.+?`、条件段翻译成可选组之后，"一段相邻的不受限字段 + 一个在标题里找不到的
# 字面量"就是组合爆炸——匹配失败前要把 n 个字符切给 k 个字段的所有分法试一遍。
# 本机实测（修复前）`"%title%"+"%album%"*8+"X"` 匹配 50 个 "a" 的标题要 34 秒，
# 而这段代码跑在歌词采样线程上（**Python 正则回溯持 GIL**，钉住它等于歌词与切歌
# 检测一起停摆）。改成下面的**分段扫描**：每个字面量用 `str.find` / 无回溯的
# 小正则定位，字段取两段字面量之间的文本；所有循环都有硬上限。

# titleformat 里的字段占位符、条件段方括号。
_FMT_TOKEN_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_ ]*)%|(\[)|(\])")
# 只有程序名时 foobar 没在播歌（窗口标题格式化后只剩这些）。
_IDLE_RE = re.compile(r"^foobar2000(?:\s+v?[\d.]+)?$", re.IGNORECASE)
_TITLE_FIELD = "title"
_ARTIST_FIELD = "artist"
_CARED_FIELDS = (_TITLE_FIELD, _ARTIST_FIELD)

# 条件段（`[...]`）的展开上限。titleformat 的 `[]` 是"里面字段非空才输出"，
# 解析时既可能展开也可能不展开，所以候选数是 2^n；超过上限就判定"这不是我们能
# 可靠解析的格式串"，直接退回不猜——**不猜好过指数展开**。16 条 = 4 个条件段，
# 真实窗口标题格式串通常 0~2 个。
_FORMAT_MAX_VARIANTS = 16
# 参与格式串匹配的标题长度上限。窗口标题理论上可到 3 万字符，而"歌名 - 歌手"
# 不可能那么长；超长的一律判为"不是曲目标题"，只走不猜路径，匹配代价因此有界。
_FORMAT_MAX_MATCH_CHARS = 400
# 字面量正则缓存：字面量集合来自用户配置的格式串，去重后条数极少。
_MAX_LITERAL_PATTERNS = 128
_WHITESPACE_RE = re.compile(r"\s")


class _Literal:
    """格式串里的字面文本（原样出现在窗口标题里）。"""

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


class _Field:
    """格式串里的 ``%field%`` 占位符。"""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name


class _Optional:
    """格式串里的 ``[...]`` 条件段：整体出现或整体不出现。"""

    __slots__ = ("tokens",)

    def __init__(self, tokens: tuple):
        self.tokens = tokens


def _tokenize_format(fmt: str) -> tuple | None:
    """把 titleformat 串切成 ``_Literal`` / ``_Field`` / ``_Optional`` 序列。

    ``'...'`` 是 titleformat 的字面量界定符，本身不输出；``$if(...)`` 这类函数
    一律按字面文本处理（匹配不上就退回不猜，不会猜错）。方括号不配对时返回
    ``None``——畸形格式串不值得猜。
    """
    text = fmt.replace("'", "")
    stack: list[list] = []
    tokens: list = []
    cursor = 0
    for match in _FMT_TOKEN_RE.finditer(text):
        literal = text[cursor:match.start()]
        if literal:
            tokens.append(_Literal(literal))
        placeholder, open_bracket, close_bracket = match.groups()
        if open_bracket:
            stack.append(tokens)
            tokens = []
        elif close_bracket:
            if not stack:
                return None  # 多出来的 ]
            inner = tuple(tokens)
            tokens = stack.pop()
            tokens.append(_Optional(inner))
        else:
            tokens.append(_Field(placeholder.strip().lower()))
        cursor = match.end()
    if stack:
        return None  # [ 没闭合
    trailing = text[cursor:]
    if trailing:
        tokens.append(_Literal(trailing))
    return tuple(tokens)


def _format_field_names(tokens: tuple) -> set[str]:
    """格式串里出现过的字段名（含条件段内的）。"""
    names: set[str] = set()
    for token in tokens:
        if isinstance(token, _Field):
            names.add(token.name)
        elif isinstance(token, _Optional):
            names |= _format_field_names(token.tokens)
    return names


def _trim_blank_literals(items: tuple) -> tuple:
    """去掉首尾**只由空白组成**的字面量。

    调用方读到窗口标题后会收敛并去掉首尾空白（``parse_foobar_title`` 的第一步），
    而 foobar 按格式串渲染时首尾空白是真的——所以 ``%artist% - %title% `` 这种
    末尾带空格的格式串，不能因为"少了一个空格"就整条判成解析不了。

    夹在两个字段之间的空白字面量**不trim**：那里的空白是分隔符，必须真吃到
    （否则 ``%title% %artist%`` 会在位置 0 匹配到空段，切出碎片）。
    """
    start, end = 0, len(items)
    while start < end and isinstance(items[start], _Literal) and not items[start].text.strip():
        start += 1
    while end > start and isinstance(items[end - 1], _Literal) and not items[end - 1].text.strip():
        end -= 1
    return items[start:end]


def _expand_optionals(tokens: tuple) -> list[tuple] | None:
    """把条件段展开成若干条"只有字面量与字段"的候选；超过上限返回 ``None``。

    "出现"的候选排在"不出现"前面：``[%artist% - ]%title%`` 遇到
    ``周杰伦 - 晴天`` 时先按"歌手非空"解析，这才是用户实际配置它时的常见形态。

    每条候选都会过一遍 :func:`_trim_blank_literals`——条件段的不出现分支经常
    正好让某个空白字面量落到末尾。
    """
    variants: list[tuple] = [()]
    for token in tokens:
        if not isinstance(token, _Optional):
            variants = [variant + (token,) for variant in variants]
            continue
        inner = _expand_optionals(token.tokens)
        if inner is None:
            return None
        grown: list[tuple] = []
        for variant in variants:
            for tail in inner:
                grown.append(variant + tail)
        for variant in variants:
            grown.append(variant)
        if len(grown) > _FORMAT_MAX_VARIANTS:
            return None
        variants = grown
    return [_trim_blank_literals(variant) for variant in variants]


@lru_cache(maxsize=_MAX_LITERAL_PATTERNS)
def _literal_pattern(literal: str) -> re.Pattern:
    """字面量 → **无回溯**的小正则（只有字面字符与空白类，不存在嵌套量词）。"""
    if not literal.strip():
        # 整段都是空白：必须真吃到空白。旧实现把它放宽成 `\\s*`，于是
        # `%title% %artist%` 会在位置 0 匹配空段，切出 (晴, 天 周杰伦) 这种碎片。
        return re.compile(r"\s+")
    return re.compile(re.escape(literal).replace("\\ ", r"\s*"))


def _find_literal(text: str, literal: str, start: int) -> tuple[int, int] | None:
    """在 ``text[start:]`` 里找字面量，返回 ``(起, 止)``；找不到返回 ``None``。

    空白放宽：格式串里的空格对应窗口标题里的任意空白（用户常把 ``%artist% -``
    写成带空格的形式，而标题里可能是 ``-``）。无空白的字面量直接 ``str.find``。
    """
    if _WHITESPACE_RE.search(literal) is None:
        at = text.find(literal, start)
        return None if at < 0 else (at, at + len(literal))
    match = _literal_pattern(literal).search(text, start)
    if match is None:
        return None
    return (match.start(), match.end())


def _assign_fields(values: dict, pending: list, chunk: str) -> bool:
    """把两段字面量之间的文本分给"待定字段"。

    相邻字段之间没有字面量时，这段文本的切法原理上不可知（``%album%%title%``）。
    规则：恰好只有一个字段是我们要的就整段给它（其余字段不关心内容）；一个都不
    要也放行；要两个以上就**判这次匹配失败**——不猜好过乱切。
    """
    if not pending:
        # 两段字面量之间夹着没人认领的文本：这个格式串描述不出这条标题。
        return chunk == ""
    if len(pending) == 1:
        name = pending[0]
        if name not in values:  # 同一字段出现两次时以第一次为准（与旧行为一致）
            values[name] = chunk
        return True
    cared = [name for name in pending if name in _CARED_FIELDS and name not in values]
    if len(cared) > 1:
        return False
    if cared:
        values[cared[0]] = chunk
    return True


def _find_literal_right(text: str, literal: str, start: int) -> tuple[int, int] | None:
    """从右往左找字面量（**最后一个**字面量专用，见 ``_match_items``）。"""
    if _WHITESPACE_RE.search(literal) is None:
        at = text.rfind(literal, start)
        return None if at < 0 else (at, at + len(literal))
    last = None
    for match in _literal_pattern(literal).finditer(text, start):
        last = match
    if last is None:
        return None
    return (last.start(), last.end())


def _match_items(items: tuple, text: str) -> dict | None:
    """按 ``字面量 / 字段`` 序列消费 ``text``，返回字段取值；对不上返回 ``None``。

    单向扫描，**没有回溯**：中间字面量取它在当前位置之后的**最左**出现，于是
    "前一个字段尽可能短、最后一个字段吃掉剩余"。**最后一个字面量取最右出现**
    （歌词族终审应修 2）：歌名常带空格，``%artist% - %title% [%length%]`` 配
    ``Eagles - Hotel California 06:30`` 时，最左匹配会把 ``Hotel`` 当歌名、
    把 ``California 06:30`` 全塞给 %length%——最右匹配才切得出 ``Hotel
    California``。循环次数受 items 与 text 长度共同约束，最坏
    O(len(items) × len(text))。
    """
    last_literal_at = -1
    for index, item in enumerate(items):
        if not isinstance(item, _Field) and item.text:
            last_literal_at = index
    values: dict = {}
    start = 0
    pending: list[str] = []
    for index, item in enumerate(items):
        if isinstance(item, _Field):
            pending.append(item.name)
            continue
        if not item.text:
            continue
        if index == last_literal_at:
            found = _find_literal_right(text, item.text, start)
        else:
            found = _find_literal(text, item.text, start)
        if found is None:
            return None
        if not _assign_fields(values, pending, text[start:found[0]]):
            return None
        pending = []
        start = found[1]
    if not _assign_fields(values, pending, text[start:]):
        return None
    return values


def _parse_with_format(text: str, fmt: str):
    """按用户给的 titleformat 串切出 ``(歌名, 歌手)``；切不出来返回 ``None``。"""
    if len(text) > _FORMAT_MAX_MATCH_CHARS:
        return None
    tokens = _tokenize_format(fmt)
    if tokens is None or _TITLE_FIELD not in _format_field_names(tokens):
        # 没闭合的方括号、或压根没有 %title% 的格式串都拿不到歌名。
        return None
    variants = _expand_optionals(tokens)
    if variants is None:
        return None
    for items in variants:
        values = _match_items(items, text)
        if values is not None:
            return str(values.get(_TITLE_FIELD, "")), str(values.get(_ARTIST_FIELD, ""))
    return None


def _parse_auto(text: str) -> tuple[str, str]:
    """没有格式串时不猜顺序：多段就整条同时作歌名与歌手（见模块 docstring）。"""
    if len(_tokens(text)) >= 2:
        return text, text
    return text, ""


def parse_foobar_title(window_title: str, fmt: str = "") -> tuple[str, str] | None:
    """foobar2000 窗口标题 → ``(歌名, 歌手)``；窗口里没有曲目时返回 ``None``。

    ``fmt`` 是用户在 foobar「显示 → 默认用户界面 → 窗口标题」里配置的 titleformat
    串（配置键 ``foobar_title_format``）。为空 = 自动（不猜顺序）。

    没在播歌时 foobar 的窗口标题只剩程序名或空的占位符，这里按"无曲目"返回
    ``None``——这是**启发式**：窗口标题本身不含播放状态，只能靠"解析不出歌名"
    判断，所以本能力的文档里如实写了这条局限。
    """
    text = re.sub(r"\s+", " ", str(window_title or "")).strip()
    if not _usable(text) or _IDLE_RE.match(text):
        return None

    parsed = None
    if str(fmt or "").strip():
        parsed = _parse_with_format(text, str(fmt)[:FOOBAR_FORMAT_MAX_CHARS])
    if parsed is None:
        parsed = _parse_auto(text)

    title = _collapse(parsed[0])
    artist = _collapse(parsed[1])
    if not _usable(title):
        return None
    return title, artist if _usable(artist) else ""
