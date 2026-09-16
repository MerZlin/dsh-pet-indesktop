# -*- coding: utf-8 -*-
"""节日提醒：西方节日的流行歌曲歌词库（受版权保护，独立模块便于剥离）。

版权说明（务必先读）
--------------------
本模块收录的是**受版权保护的流行歌曲歌词**：下列词曲的著作权全部仍在保护期内。
所取虽只是广为流传的**单句引用**，仍属受保护作品的片段，与项目自身的 MIT 许可
存在张力。维护者已知情，并明确要求把这些歌词内置进节日提醒。

之所以与 ``pet/festival_quotes_west.py``（纯公有领域引文库）以及电影/游戏两个
受版权库都**分文件物理隔离**，是为了让需要「纯公有领域版本」的人可以删除对应
模块；``pet/festival.py`` 的取词处会因 ImportError 降级为空表，无需改动其它代码。

选材原则
--------
1. **只取广为流传的单句歌词**，不取整段主歌/副歌，不取需要上下文才成立的句子。
2. 单行英文原句，无换行，长度 15~110 字符。
3. **氛围必须与节日匹配**。
4. **敏感议题规避**：不涉政治、宗教教义争论、情感纠纷叙事（如失恋主题虽在圣诞
   歌曲里很常见，但不适合做节日祝福，故不取）。
5. 同一键内不重复，跨键亦不重复。

覆盖范围说明
------------
本库**只覆盖维护者明确要求的两个节日**（平安夜、圣诞节）——圣诞歌曲是流行音乐里
唯一与节日强绑定、且名句密度足够高的一类；其它节日没有同等质量的歌曲素材，
故不硬凑（缺口由公有领域库、电影库、游戏库覆盖）。

出处标注方式
------------
出处**以行内注释紧跟在每条歌词之后**（词曲作者 + 年份 + 歌名），不集中在
docstring 列清单——集中清单会随增删悄悄漂移，而该清单要用于第三方版权声明。
"""

QUOTES_WEST_SONG: dict[str, tuple[str, ...]] = {
    # 平安夜（12/24）：静谧、温暖、团聚
    "christmas_eve": (
        "Have yourself a merry little Christmas, let your heart be light.",      # Have Yourself a Merry Little Christmas (1944), Hugh Martin & Ralph Blane
        "Chestnuts roasting on an open fire, Jack Frost nipping at your nose.",  # The Christmas Song (1945), Mel Tormé & Robert Wells
        "Oh the weather outside is frightful, but the fire is so delightful.",  # Let It Snow! Let It Snow! Let It Snow! (1945), Sammy Cahn & Jule Styne
        "I'm dreaming of a white Christmas, just like the ones I used to know.",  # White Christmas (1942), Irving Berlin
        "Sleigh bells ring, are you listening?",                                # Winter Wonderland (1934), Felix Bernard & Richard B. Smith
    ),
    # 圣诞节（12/25）：欢庆、祝福、赠礼
    "christmas": (
        "All I want for Christmas is you.",                                     # All I Want for Christmas Is You (1994), Mariah Carey & Walter Afanasieff
        "You better watch out, you better not cry.",                            # Santa Claus Is Comin' to Town (1934), J. Fred Coots & Haven Gillespie
        "Rockin' around the Christmas tree at the Christmas party hop.",        # Rockin' Around the Christmas Tree (1958), Johnny Marks
        "Rudolph with your nose so bright, won't you guide my sleigh tonight?",  # Rudolph the Red-Nosed Reindeer (1949), Johnny Marks
        "Last Christmas, I gave you my heart.",                                 # Last Christmas (1984), George Michael
    ),
}
