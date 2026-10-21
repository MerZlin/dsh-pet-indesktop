# -*- coding: utf-8 -*-
"""节日提醒：西方节日的经典电影台词库（受版权保护，独立模块便于剥离）。

版权说明（务必先读）
--------------------
本模块收录的是**受版权保护的电影台词**：下列影片的著作权全部仍在保护期内。
本表所取虽只是广为流传的**短句引用**，仍属受保护作品的片段，与项目自身的
MIT 许可存在张力。维护者已知情，并明确要求把这些经典台词内置进节日提醒。

之所以与 ``pet/festival_quotes_west.py``（纯公有领域英文引文库）**物理隔离**
成两个文件，是为了让需要「纯公有领域版本」的人可以：删除本文件，
``pet/festival.py`` 的取词处会因 ImportError 降级为空表，即可在不改动其它
代码的前提下剥离全部受版权内容。因此本模块**不导入**本包其它模块、不导入 Qt，
纯数据、零依赖。

选材原则
--------
1. **只取影史级、被反复引用的经典短句**，不取生僻对白，不取整段独白。
2. 单行英文原句，无换行，长度 15~110 字符。
3. **氛围必须与节日匹配**，不张冠李戴。
4. **敏感议题规避**：不涉政治立场、战争宣传、宗教教义争论。
5. **万圣节不涉血腥恐怖**：只取幽暗、神秘、夜色氛围。
6. **选片范围**：豆瓣电影 Top 250 中的西方（欧美）**电影**——不含华语片、
   日本动画，也**不含电视剧**（电视剧是另一类版权客体，与"电影台词"不该混在
   一个库里）。
7. **不与公有领域库重复**：属于公有领域原著的句子（如狄更斯《圣诞颂歌》
   的 "God bless us, every one!"）一律留在 ``festival_quotes_west.py``，
   本库不再收一遍。
8. 同一键内不重复，**跨键亦不重复**。

出处标注方式
------------
出处**以行内注释紧跟在每条台词之后**，而不是集中在 docstring 里列清单——
集中清单会随增删台词悄悄漂移，而该清单要用于第三方版权声明，标错比不标更糟。
行内注释在结构上不可能漂移。
"""

QUOTES_WEST_MOVIE: dict[str, tuple[str, ...]] = {
    # 情人节（2/14）：爱情、倾慕
    "valentine": (
        "Here's looking at you, kid.",                                          # Casablanca (1942)
        "Of all the gin joints in all the towns in all the world, she walks into mine.",  # Casablanca (1942)
        "Rome. By all means, Rome.",                                            # Roman Holiday (1953)
        "You have bewitched me, body and soul.",                                # Pride & Prejudice (2005)
        "Here's to the ones who dream, foolish as they may seem.",              # La La Land (2016)
    ),
    # 愚人节（4/1）：玩笑、荒诞、人生如戏
    "april_fools": (
        "Life is like a box of chocolates. You never know what you're gonna get.",  # Forrest Gump (1994)
        "Good morning, and in case I don't see ya, good afternoon, good evening, and good night!",  # The Truman Show (1998)
        "We accept the reality of the world with which we're presented.",       # The Truman Show (1998)
        "Stupid is as stupid does.",                                            # Forrest Gump (1994)
        "Pay no attention to that man behind the curtain.",                     # The Wizard of Oz (1939)
    ),
    # 复活节：希望、重生、救赎
    "easter": (
        "Hope is a good thing, maybe the best of things.",                      # The Shawshank Redemption (1994)
        "Get busy living, or get busy dying.",                                  # The Shawshank Redemption (1994)
        "Just keep swimming.",                                                  # Finding Nemo (2003)
        "Good morning, Princess!",                                              # Life Is Beautiful (1997)
        "Life isn't like in the movies. Life is much harder.",                  # Cinema Paradiso (1988)
    ),
    # 母亲节：母爱、养育、牵挂
    "mothers_day": (
        "My mama always said you've got to put the past behind you before you can move on.",  # Forrest Gump (1994)
        "I want you to be the very best version of yourself that you can be.",  # Lady Bird (2017)
        "Remember me, though I have to say goodbye.",                           # Coco (2017)
    ),
    # 父亲节：父爱、教导、责任
    "fathers_day": (
        "Don't ever let somebody tell you that you can't do something.",        # The Pursuit of Happyness (2006)
        "A man who doesn't spend time with his family can never be a real man.",  # The Godfather (1972)
        "Remember who you are.",                                                # The Lion King (1994)
        "If you build it, he will come.",                                       # Field of Dreams (1989)
        "Love is the one thing we're capable of perceiving that transcends time and space.",  # Interstellar (2014)
    ),
    # 万圣节（10/31）：幽暗、神秘、夜色（不涉血腥恐怖）
    "halloween": (
        "I ain't afraid of no ghost.",                                          # Ghostbusters (1984)
        "I'm the ghost with the most, babe.",                                   # Beetlejuice (1988)
        "Life's no fun without a good scare.",                                  # The Nightmare Before Christmas (1993)
        "I'm not afraid of you.",                                               # Edward Scissorhands (1990)
    ),
    # 平安夜（12/24）：平安、温暖、团聚
    "christmas_eve": (
        "To me, you are perfect.",                                              # Love Actually (2003)
        "If you look for it, you'll find that love actually is all around.",    # Love Actually (2003)
        "The best way to spread Christmas cheer is singing loud for all to hear.",  # Elf (2003)
        "This is my house. I have to defend it.",                               # Home Alone (1990)
    ),
    # 圣诞节（12/25）：圣诞、仁爱、善意
    "christmas": (
        "Every time a bell rings, an angel gets his wings.",                    # It's a Wonderful Life (1946)
        "Merry Christmas, you filthy animal.",                                  # Home Alone (1990)
        "The bell still rings for all who truly believe.",                      # The Polar Express (2004)
        "Faith is believing in things when common sense tells you not to.",     # Miracle on 34th Street (1947)
    ),
}
