# -*- coding: utf-8 -*-
"""节日提醒：西方节日的经典电子游戏台词库（受版权保护，独立模块便于剥离）。

版权说明（务必先读）
--------------------
本模块收录的是**受版权保护的电子游戏台词**：下列作品的著作权全部仍在保护期内。
本表所取虽只是广为流传的**短句引用**，仍属受保护作品的片段，与项目自身的
MIT 许可存在张力。维护者已知情，并明确要求把这些经典台词内置进节日提醒。

之所以与 ``pet/festival_quotes_west.py``（纯公有领域英文引文库）**物理隔离**
成两个文件，是为了让需要「纯公有领域版本」的人可以：删除本文件，
``pet/festival.py`` 的取词处会因 ImportError 降级为空表，即可在不改动其它
代码的前提下剥离全部受版权内容。因此本模块**不导入**本包其它模块、不导入 Qt，
纯数据、零依赖。

为什么本库**不覆盖全部 8 个西方节日**
-------------------------------------
游戏台词里"广为流传 + 氛围确实对得上某个节日"的交集比电影小得多。本库只收
**真正契合**的：宁可某个节日没有游戏台词（该节日仍由公有领域库与电影库覆盖），
也不为凑满键数硬塞弱相关句子——张冠李戴比缺项更糟。具体缺口见文末说明。

选材原则
--------
1. **只取广为流传的名句**，不取生僻对白，不取整段独白。
2. 单行英文原句，无换行，长度 15~110 字符。
3. **氛围必须与节日匹配**。
4. **敏感议题规避**：不取战争宣传、真实战争暴行描写、政治口号。战争题材
   作品（使命召唤 / 战地风云）只取反战、苍凉、人性一面的名句。
5. **万圣节不涉血腥猎奇**：只取幽暗、亡灵、夜色的氛围。
6. 同一键内不重复，**跨键亦不重复**。
7. **出处必须准确**：标注到"作品（年份）"级别；不确定归属的句子宁可不用。

出处标注方式
------------
出处**以行内注释紧跟在每条台词之后**，不集中在 docstring 列清单——集中清单
会随增删悄悄漂移，而该清单要用于第三方版权声明。行内注释在结构上不会漂移。
"""

QUOTES_WEST_GAME: dict[str, tuple[str, ...]] = {
    # 情人节（2/14）：羁绊、珍视、不离不弃
    "valentine": (
        "Despite everything, it's still you.",                                  # Undertale (2015)
        "I'm Commander Shepard, and this is my favorite store on the Citadel.",  # Mass Effect 2 (2010)
        "Don't you dare go Hollow.",                                            # Dark Souls (2011)
    ),
    # 愚人节（4/1）：玩笑、荒诞、真假难辨
    "april_fools": (
        "The cake is a lie.",                                                   # Portal (2007)
        "This was a triumph. I'm making a note here: huge success.",            # Portal 2 (2011)
        "I used to be an adventurer like you, then I took an arrow in the knee.",  # The Elder Scrolls V: Skyrim (2011)
        "Would you kindly.",                                                    # BioShock (2007)
        "Nothing is true, everything is permitted.",                            # Assassin's Creed (2007)
    ),
    # 复活节：希望、重生、新生
    "easter": (
        "Praise the Sun!",                                                      # Dark Souls (2011)
        "Rise, ye Tarnished.",                                                  # Elden Ring (2022)
        "Endure and survive.",                                                  # The Last of Us (2013)
        "Tomorrow is in your hands.",                                           # Death Stranding (2019)
    ),
    # 母亲节：母性与守护（游戏里广为流传的母性名句很少，故本键条目偏少）
    "mothers_day": (
        "Hear me, Demigods. My children beloved.",                              # Elden Ring (2022)
        "No cost too great.",                                                   # Hollow Knight (2017)
    ),
    # 父亲节：父与子、教导、责任（游戏里最丰沛的一类主题）
    "fathers_day": (
        "Do not be sorry. Be better.",                                          # God of War (2018)
        "Keep your expectations low, boy, and you will never be disappointed.",  # God of War (2018)
        "I struggled a long time with surviving. But no matter what, you keep finding something to fight for.",  # The Last of Us (2013)
    ),
    # 万圣节（10/31）：幽暗、亡灵、夜色（不涉血腥猎奇）
    "halloween": (
        "Fear the old blood.",                                                  # Bloodborne (2015)
        "I am Malenia, Blade of Miquella.",                                     # Elden Ring (2022)
        "We're more ghosts than people.",                                       # Red Dead Redemption 2 (2018)
        "50,000 people used to live here. Now it's a ghost town.",              # Call of Duty 4: Modern Warfare (2007)
        "You are not expected to survive.",                                     # Battlefield 1 (2016)
    ),
}
