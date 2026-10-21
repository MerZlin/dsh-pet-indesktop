# -*- coding: utf-8 -*-
"""节日提醒：西方节日的英文引文库（纯数据，零依赖）。

用途
----
``pet.festival_data`` 命中西方节日（``category == "west"``）时，可按节日 id
从本表取一组英文引文，作为气泡提醒的点缀文案。本模块不导入 Qt、不导入本包
其它模块，纯数据、零依赖，可被纯逻辑层与测试直接使用。

只收录公有领域内容（维护者硬性要求）
------------------------------------
维护者的原则是「内置只放公有领域内容，非公有领域留给用户自定义」，因此本表
**一律不收录**：

- 电影、电视剧、电子游戏台词：这些作品的著作权全部仍在保护期内，哪怕只引
  一句也属于受保护作品的片段，内置即构成侵权风险（维护者特别强调过这一点）。
- 现代流行歌曲歌词、现代小说/现代诗歌（《哈利·波特》《魔戒》之类）：作者
  大多在世或逝世不足 70 年，同样受保护。

收录范围仅限已进入公有领域（public domain）的英文作品，满足二者之一：

- 作者逝世已超过 70 年（如莎士比亚 d.1616、狄更斯 d.1870、狄金森 d.1886），或
- 作品首次发表早于 1929 年（美国公有领域分界线；本表全部作品都远早于此线）。

涉及作者/文本：莎士比亚、狄更斯、简·奥斯汀、马克·吐温、爱伦·坡、刘易斯·
卡罗尔、KJV 圣经（1611）、伊丽莎白·巴雷特·布朗宁、弥尔顿、华兹华斯、济慈、
雪莱、惠特曼、狄金森、丁尼生、克里斯蒂娜·罗塞蒂、乔治·赫伯特、安妮·布拉
德斯特里特、朗费罗、惠蒂尔、霍普金斯、培根、克莱门特·克拉克·摩尔、华盛顿·
欧文、J. F. 扬（《平安夜》英译，1859）。

选材原则
--------
1. 氛围贴合节日：情人节谈爱情，愚人节取机智荒诞，复活节写重生与春天，母亲节
   / 父亲节写养育与教导，万圣节写幽暗夜色，平安夜写静夜
   与期待，圣诞节写仁爱与团聚。
2. 单行短句（20~120 字符），可直接进气泡一行展示或语音播报，不含换行。
3. 只取「文学/文化引语」层面：圣经引文按文化经典使用，不涉教义争论；全表不涉
   政治，不涉任何争议性议题。
4. 万圣节只取幽暗、鬼魅、夜与荒诞的氛围，不取血腥恐怖内容。
5. 同一键内不重复，跨键亦不重复。

出处清单（供 THIRD_PARTY_NOTICES.md 登记；顺序与下方各元组一一对应）
--------------------------------------------------------------------
valentine
  1. William Shakespeare, Sonnet 18 (1609)
  2. William Shakespeare, Sonnet 116 (1609)
  3. Elizabeth Barrett Browning, Sonnets from the Portuguese, 43 (1850)
  4. William Shakespeare, Romeo and Juliet, Act II Scene ii (c.1597)
  5. William Shakespeare, Hamlet, Act II Scene ii (c.1600)
  6. Jane Austen, Pride and Prejudice, ch. 34 (1813)
april_fools
  1. William Shakespeare, A Midsummer Night's Dream, Act III Scene ii (c.1595)
  2. William Shakespeare, Twelfth Night, Act I Scene v (c.1601)
  3. William Shakespeare, As You Like It, Act V Scene i (c.1599)
  4. Lewis Carroll, Through the Looking-Glass, ch. 5 (1871)
  5. Lewis Carroll, Alice's Adventures in Wonderland, ch. 6 (1865)
  6. Mark Twain, The Mysterious Stranger (1916)
easter
  1. KJV Bible, Matthew 28:6 (1611)
  2. KJV Bible, Song of Solomon 2:11 (1611)
  3. John Keats, "On the Grasshopper and Cricket" (1817)
  4. Gerard Manley Hopkins, "Spring" (written 1877, published 1918)
  5. Percy Bysshe Shelley, "Ode to the West Wind" (1819)
  6. John Milton, Paradise Lost, Book IV (1667)
mothers_day
  1. KJV Bible, Isaiah 66:13 (1611)
  2. KJV Bible, Proverbs 31:28 (1611)
  3. William Shakespeare, The Tempest, Act I Scene ii (c.1611)
  4. Anne Bradstreet, "In Reference to her Children, 23 June 1659" (written 1659; publ. 1678)
  5. Rudyard Kipling, "Mother o' Mine" (1891)
  6. Walt Whitman, "There Was a Child Went Forth" (1855)
fathers_day
  1. William Shakespeare, The Merchant of Venice, Act II Scene ii (c.1597)
  2. Francis Bacon, "Of Parents and Children", Essays (1612/1625)
  3. KJV Bible, Proverbs 17:6 (1611)
  4. William Shakespeare, The Tempest, Act I Scene ii (c.1611)
  5. William Wordsworth, "My Heart Leaps Up" (1807)
  6. Henry Wadsworth Longfellow, "My Lost Youth" (1855)
halloween
  1. William Shakespeare, Macbeth, Act IV Scene i (c.1606)
  2. William Shakespeare, Macbeth, Act IV Scene i (c.1606)
  3. William Shakespeare, Hamlet, Act I Scene v (c.1600)
  4. Edgar Allan Poe, "The Raven" (1845)
  5. Christina Rossetti, "Goblin Market" (1862)
  6. Washington Irving, "The Legend of Sleepy Hollow" (1820)
christmas_eve
  1. Clement Clarke Moore, "A Visit from St. Nicholas" (1823; authorship also
     attributed to Henry Livingston Jr.)
  2. KJV Bible, Luke 2:8 (1611)
  3. KJV Bible, Luke 2:14 (1611)
  4. Alfred, Lord Tennyson, In Memoriam A.H.H., section XXVIII (1850)
  5. Christina Rossetti, "In the Bleak Midwinter" (1872)
  6. Joseph Mohr, "Stille Nacht" (1818); English trans. John Freeman Young (1859)
christmas
  1. Charles Dickens, A Christmas Carol, Stave IV (1843)
  2. Charles Dickens, A Christmas Carol, Stave III (1843)
  3. Charles Dickens, A Christmas Carol, Stave I (1843)
  4. KJV Bible, Luke 2:11 (1611)
  5. KJV Bible, Isaiah 9:6 (1611)
  6. Christina Rossetti, "Love Came Down at Christmas" (1885)
"""

QUOTES_WEST: dict[str, tuple[str, ...]] = {
    # 情人节（2/14）：爱情、倾慕
    "valentine": (
        "Shall I compare thee to a summer's day? Thou art more lovely and more temperate.",
        "Love is not love which alters when it alteration finds.",
        "How do I love thee? Let me count the ways.",
        "My bounty is as boundless as the sea, my love as deep; the more I give to thee, the more I have.",
        "Doubt thou the stars are fire, doubt that the sun doth move, doubt truth to be a liar, but never doubt I love.",
        "You must allow me to tell you how ardently I admire and love you.",
    ),
    # 愚人节（4/1）：机智、玩笑、荒诞
    "april_fools": (
        "Lord, what fools these mortals be!",
        "Better a witty fool than a foolish wit.",
        "The fool doth think he is wise, but the wise man knows himself to be a fool.",
        "Why, sometimes I've believed as many as six impossible things before breakfast.",
        "We're all mad here. I'm mad. You're mad.",
        "Against the assault of laughter nothing can stand.",
    ),
    # 复活节：重生、春天、希望
    "easter": (
        "He is not here: for he is risen, as he said.",
        "For, lo, the winter is past, the rain is over and gone.",
        "The poetry of earth is never dead.",
        "Nothing is so beautiful as spring.",
        "If Winter comes, can Spring be far behind?",
        "Sweet is the breath of morn, her rising sweet.",
    ),
    # 母亲节：母爱、养育
    "mothers_day": (
        "As one whom his mother comforteth, so will I comfort you.",
        "Her children arise up, and call her blessed.",
        "Thy mother was a piece of virtue, and she said thou wast my daughter.",
        "I had eight birds hatcht in one nest, four cocks were there, and hens the rest.",
        "If I were hanged on the highest hill, I know whose love would follow me still.",
        "There was a child went forth every day, and the first object he look'd upon, that object he became.",
    ),
    # 父亲节：父爱、教导、责任
    "fathers_day": (
        "It is a wise father that knows his own child.",
        "The joys of parents are secret; and so are their griefs and fears.",
        "Children's children are the crown of old men; and the glory of children are their fathers.",
        "I have done nothing but in care of thee, of thee my dear one, thee my daughter.",
        "The Child is father of the Man.",
        "A boy's will is the wind's will, and the thoughts of youth are long, long thoughts.",
    ),
    # 万圣节（10/31）：幽暗、鬼魅、夜（不涉血腥恐怖）
    "halloween": (
        "Double, double toil and trouble; fire burn, and cauldron bubble.",
        "By the pricking of my thumbs, something wicked this way comes.",
        "There are more things in heaven and earth, Horatio, than are dreamt of in your philosophy.",
        "Once upon a midnight dreary, while I pondered, weak and weary.",
        "We must not look at goblin men, we must not buy their fruits.",
        "A drowsy, dreamy influence seems to hang over the land.",
    ),
    # 平安夜（12/24）：平安、静夜、期待
    "christmas_eve": (
        "'Twas the night before Christmas, when all through the house not a creature was stirring, not even a mouse.",
        "And there were in the same country shepherds abiding in the field, keeping watch over their flock by night.",
        "Glory to God in the highest, and on earth peace, good will toward men.",
        "The Christmas bells from hill to hill answer each other in the mist.",
        "In the bleak midwinter, frosty wind made moan, earth stood hard as iron, water like a stone.",
        "Silent night, holy night, all is calm, all is bright.",
    ),
    # 圣诞节（12/25）：圣诞、仁爱、团聚
    "christmas": (
        "I will honour Christmas in my heart, and try to keep it all the year.",
        "God bless us, every one!",
        "The only time I know of, when men and women seem by one consent to open their shut-up hearts freely.",
        "For unto you is born this day in the city of David a Saviour, which is Christ the Lord.",
        "For unto us a child is born, unto us a son is given.",
        "Love came down at Christmas, love all lovely, love divine.",
    ),
}
