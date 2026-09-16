# Third-Party Software Notices and Information

This project includes third-party software and assets subject to their respective licenses.

---

## 1. DeepSeek Balance Whale Widget Sound Assets

- **Files**:
  - `assets/sounds/duck/Ya1.mp3`
  - `assets/sounds/duck/Ya2.mp3`
- **Origin / Source Repository**: [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)
- **License**: MIT License

---

## 2. Character Animation Assets（深深 / 小鲸鱼 webm 素材）

- **Files**: `assets/characters/**`（640×360 / 24fps / VP9-alpha 透明 webm）
- **Origin / Source Repository**: [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)（动作素材做法与素材来源；本项目经整理搬运）
- **Notes**: 角色 OC「溟月」出自画师上善无形，素材由社区成员整理制作。
  此类同人素材按 CC BY-NC-SA 类条款发布，**仅限个人非商业使用**，
  使用须保留署名与来源，不得用于任何商业/盈利场景。

### MIT License Text

```text
MIT License

Copyright (c) 2025 MeteorNOX

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 3. lunar-python（农历与 24 节气换算）

- **Package**: [`lunar-python`](https://pypi.org/project/lunar-python/)（import 名 `lunar_python`）
- **Author**: 6tail
- **License**: MIT License
- **Used by**: `pet/festival_calendar.py`（节日提醒的农历/节气计算）
- **为何选它**：流传更广的 `lunardate` / `zhdate` 均为 **GPL-3.0-or-later**
  （同源于 1988 年 Fung F. Lee / Ricky Yeung 的 GPLv2 `lunar` 项目），与本项目
  的 MIT 许可不兼容，**不可引入**。`lunar-python` 为 MIT、纯 Python、无数据
  文件、无传递依赖。

---

## 4. 节日提醒内置文案（古诗词 / 公有领域引文）

`pet/festival_quotes_cn.py` 与 `pet/festival_quotes_west.py` 内置的文案**全部取自公有领域作品**，
不含任何受版权保护的影视、电视剧、电子游戏台词或现代歌词。

- **中文库（`festival_quotes_cn.py`，199 条）**：中国古典诗词与经典古籍 —《诗经》、
  汉魏乐府与陶渊明、唐诗（杜甫、白居易、杜牧、韩愈、刘禹锡、王维、孟浩然、柳宗元、
  李商隐等）、宋词宋诗（苏轼、陆游、辛弃疾、欧阳修、李清照、范成大、杨万里等）、
  《论语》《孟子》《史记》，以及流传广泛的节气农谚。作者最晚止于清代
  （纳兰性德、林则徐 1842 年句），著作权保护期早已届满。
- **西文库（`festival_quotes_west.py`，48 条）**：莎士比亚、弥尔顿、培根、奥斯汀、
  济慈、雪莱、狄更斯、爱伦·坡、惠特曼、狄金森、朗费罗、丁尼生、C. 罗塞蒂、
  霍普金斯、卡罗尔、马克·吐温、伊丽莎白·巴雷特·布朗宁、安妮·布拉德斯特里特、
  乔治·赫伯特、华盛顿·欧文、惠蒂尔，以及 **KJV 圣经**（1611）。除 Kipling
  （d.1936，所引 "Mother o' Mine" 首发 1891，早于美国 1929 年公有领域分界线）
  外全部作者逝世逾 70 年；最晚发表年份为 1918。
- **注意（地域差异）**：KJV 圣经在美国属公有领域，但在英国由 Crown letters patent
  长期管理。若日后在英国分发，建议就此加一句说明。
- 用户仍可在设置页的「自定义中文/西文文案」中自行增补文案（**追加**到内置库之后）。

---

## 5. ⚠️ 节日提醒内置的受版权内容（电影 / 电子游戏 / 流行歌曲）

> **重要披露**：本节所列内容**受版权保护**，与第 4 节的公有领域文案性质不同。
> 维护者已知情并决定内置（仅在项目自身分发时适用）。这些内容以**短句引用**
> 形式收录，不含完整对白段落；但"内置进分发的软件产物"比单纯的评论引用更强，
> 若你计划再分发或在其他法域发布，请自行评估。

### 5.1 电影台词 — `pet/festival_quotes_west_movie.py`（35 条）

| 影片 | 条数 |
|---|---|
| Casablanca (1942) | 2 |
| Roman Holiday (1953) | 1 |
| Pride & Prejudice (2005) | 1 |
| La La Land (2016) | 1 |
| Forrest Gump (1994) | 3 |
| The Truman Show (1998) | 2 |
| The Wizard of Oz (1939) | 1 |
| The Shawshank Redemption (1994) | 2 |
| Finding Nemo (2003) | 1 |
| Life Is Beautiful (1997) | 1 |
| Cinema Paradiso (1988) | 1 |
| Lady Bird (2017) | 1 |
| Coco (2017) | 1 |
| The Pursuit of Happyness (2006) | 1 |
| The Godfather (1972) | 1 |
| The Lion King (1994) | 1 |
| Field of Dreams (1989) | 1 |
| Interstellar (2014) | 1 |
| Ghostbusters (1984) | 1 |
| Beetlejuice (1988) | 1 |
| The Nightmare Before Christmas (1993) | 1 |
| Edward Scissorhands (1990) | 1 |
| It's a Wonderful Life (1946) | 1 |
| Love Actually (2003) | 2 |
| Elf (2003) | 1 |
| Home Alone (1990) | 2 |
| The Polar Express (2004) | 1 |
| Miracle on 34th Street (1947) | 1 |

### 5.2 电子游戏台词 — `pet/festival_quotes_west_game.py`（22 条）

| 作品 | 条数 |
|---|---|
| Elden Ring (2022) | 3 |
| God of War (2018) | 2 |
| Dark Souls (2011) | 2 |
| The Last of Us (2013) | 2 |
| Portal (2007) | 1 |
| Portal 2 (2011) | 1 |
| The Elder Scrolls V: Skyrim (2011) | 1 |
| BioShock (2007) | 1 |
| Assassin's Creed (2007) | 1 |
| Undertale (2015) | 1 |
| Mass Effect 2 (2010) | 1 |
| Death Stranding (2019) | 1 |
| Hollow Knight (2017) | 1 |
| Bloodborne (2015) | 1 |
| Red Dead Redemption 2 (2018) | 1 |
| Call of Duty 4: Modern Warfare (2007) | 1 |
| Battlefield 1 (2016) | 1 |

> 游戏库**有意不覆盖全部西方节日**：游戏里"广为流传 + 氛围确实契合某节日"
> 的交集远小于电影，宁可缺项也不硬塞弱相关句子。缺口由第 4 节公有领域库、
> 5.1 电影库与 5.3 歌曲库覆盖。

### 5.3 流行歌曲歌词 — `pet/festival_quotes_west_song.py`（10 条）

| 歌曲 | 词曲作者 | 年份 | 条数 |
|---|---|---|---|
| Have Yourself a Merry Little Christmas | Hugh Martin & Ralph Blane | 1944 | 1 |
| The Christmas Song | Mel Tormé & Robert Wells | 1945 | 1 |
| Let It Snow! Let It Snow! Let It Snow! | Sammy Cahn & Jule Styne | 1945 | 1 |
| White Christmas | Irving Berlin | 1942 | 1 |
| Winter Wonderland | Felix Bernard & Richard B. Smith | 1934 | 1 |
| All I Want for Christmas Is You | Mariah Carey & Walter Afanasieff | 1994 | 1 |
| Santa Claus Is Comin' to Town | J. Fred Coots & Haven Gillespie | 1934 | 1 |
| Rockin' Around the Christmas Tree | Johnny Marks | 1958 | 1 |
| Rudolph the Red-Nosed Reindeer | Johnny Marks | 1949 | 1 |
| Last Christmas | George Michael | 1984 | 1 |

> 本库**只覆盖平安夜与圣诞节**：圣诞歌曲是流行音乐里唯一与节日强绑定、且名句
> 密度足够高的一类；其它节日无同等质量素材，故不硬凑。

### 5.4 如何剥离这些内容（得到「纯公有领域」版本）

三个受版权模块与公有领域库是**物理隔离**的独立文件，`pet/festival.py` 的取词
处用 `try/except ImportError` 导入它们（且刻意使用真实 `import` 语句而非
`importlib`，以保证 PyInstaller 静态分析能收集到；已实测确认三个模块均进包）。因此：

1. 删除 `pet/festival_quotes_west_movie.py`、`pet/festival_quotes_west_game.py`
   与 `pet/festival_quotes_west_song.py`（三者可**任意组合删除**）；
2. 无需改动任何其它代码——取词自动降级，西方节日回退到公有领域引文库与其余留存库。

`tests/test_festival.py` 中有断言守卫这三个库的存在（`test_pop_culture_libraries_are_present_and_non_empty`）；
若你为合规主动删除它们，请一并移除或调整该断言。
