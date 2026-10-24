# Third-Party Software Notices and Information

This project includes third-party software and assets subject to their respective licenses.

The Android client carries the character attribution below and Android-specific
notices in `android/NOTICE.txt`; its build includes these notices in the APK.

---

## 1. DeepSeek Balance Whale Widget Sound Assets

- **Files**:
  - `assets/sounds/duck/Ya1.mp3`
  - `assets/sounds/duck/Ya2.mp3`
- **Origin / Source Repository**: [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)
- **License**: MIT License（正文见本节末尾）
- **分发方式**（2026-10-17 起）：这两个文件随桌面三端产物与 APK 一起分发，**本节
  连同下面的 MIT 正文也随之分发**——桌面 onedir 包落 `_internal/THIRD_PARTY_NOTICES.md`
  （同目录还有项目自身的 `LICENSE`），APK 落资产根。把 mp3 打进包、把声明留在源码
  仓库，不构成「许可正文随复制件分发」。

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

## 2. Character Animation Assets（深深 / 小鲸鱼 webm 素材）

- **Files**: `assets/characters/**`（640×360 / 24fps / VP9-alpha 透明 webm）
- **Origin / Source Repository**: [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)（动作素材做法与素材来源；本项目经整理搬运）
- **Notes**: 角色 OC「溟月」出自画师上善无形，素材由社区成员整理制作。
  此类同人素材按 CC BY-NC-SA 类条款发布，**仅限个人非商业使用**，
  使用须保留署名与来源，不得用于任何商业/盈利场景。

---

## 3. lunar-python（农历与 24 节气换算）

- **Package**: [`lunar-python`](https://pypi.org/project/lunar-python/)（import 名 `lunar_python`）
- **Author**: 6tail
- **License**: MIT License
- **Used by**: `pet/festival/calendar.py`（节日提醒的农历/节气计算）
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

---

## 6. PresentMon 2.6.0（游戏帧率采集后端，随 Windows 包分发）

- **Files**:
  - `resources/presentmon/PresentMon-2.6.0-x64.exe`（980320 字节，x86-64）
  - `resources/presentmon/LICENSE.txt`（上游原文逐字，未改动）
- **Origin / Source Repository**:
  [GameTechDev/PresentMon](https://github.com/GameTechDev/PresentMon) 的 release
  [`v2.6.0`](https://github.com/GameTechDev/PresentMon/releases/tag/v2.6.0)，
  资产 `PresentMon-2.6.0-x64.exe`
- **License**: MIT License（`Copyright (C) 2017-2024 Intel Corporation`）
- **Used by**: `pet/island/game_fps.py` 与 `pet/island/fps_monitor.py`——灵动岛「游戏帧率」段的
  数据源（读数口径是**目标应用的 Present 帧率**，来自该程序输出的
  `MsBetweenPresents`）
- **校验**：exe 的 sha256 `b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af`
  （与 GitHub release API 的 `digest` 字段一致）；`LICENSE.txt` 与 v2.6.0 源码树里的
  同名文件逐字节相同。构建期由 `scripts/verify_presentmon.py` 按「文件名 + sha256 +
  x86-64 架构」三重校验，任一不符即中止构建（少一个后端不会让程序崩，只会让这一段
  永远不出数，所以必须在打包阶段拦住）
- **为什么不是 pip 依赖**：上游没有 PyPI 包；官方支持的集成方式就是分发这个独立
  控制台程序，由它在系统层（ETW）采集显卡 Present 事件——本应用不自己实现采集
- **分发方式与边界**：**不装 MSI、不注册服务、不装驱动、不写注册表**。只在用户
  自己打开「游戏帧率」指标、且存在采集目标时，它才作为子进程按需启动；关闭开关、
  目标消失或桌宠退出即收口。仅 Windows 打包变体携带它（macOS / Linux 包里没有这个
  目录），非 Windows 平台上这一段按「不支持」呈现
- **安装包里的位置**：随 onedir 产物落在 `_internal/resources/presentmon/`，安装器
  整目录拷贝，因此 `LICENSE.txt` 与二进制一同随包分发（MIT 要求的声明随附）
- **升级路径**：换版本必须同时改 `pet/island/game_fps.py` 的 `BACKEND_EXE_NAME`、
  `REQUIRED_SHA256`、`SOURCE_TEXT` 与本文件——文件名带版本号（`PresentMon-2.6.0-x64.exe`）
  就是为了不让"同名不同版"成为静默替换

### MIT License Text

```text
Copyright (C) 2017-2024 Intel Corporation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom
the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## 7. 本地 OCR（快速识屏：RapidOCR + PP-OCR 模型 + ONNXRuntime）

- **随包模型 Files**（`resources/rapidocr_models/`，共 30.3 MiB / 31.7 MB，
  31,772,168 字节——下表逐项相加所得，2026-10-13 实测；其中三个 `.onnx` 占
  31,749,509 字节）：

  | 文件 | 字节 | sha256 |
  | --- | --- | --- |
  | `PP-OCRv6_det_small.onnx`（文本检测） | 9,929,594 | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
  | `PP-OCRv6_rec_small.onnx`（文本识别） | 21,234,383 | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` |
  | `ch_ppocr_mobile_v2.0_cls_mobile.onnx`（方向分类） | 585,532 | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |
  | `LICENSE.txt`（Apache-2.0 全文） | 11,422 | `3e0af25fdd06aa9586ae97adb00ea927ebe5a3805ac77d2d3a81ce5f55693333` |
  | `MODEL_LICENSES.md`（上游模型许可与出处声明） | 8,398 | `c5fd0e8603d3df743355b546121e6fe0f4ef8ff455c5a85c1681fd3ae520e153` |
  | `NOTICE.md`（本目录的出处/许可说明） | 2,839 | 由本项目编写 |

- **Origin / Source**：三个 `.onnx` 逐字节取自 PyPI 上 `rapidocr` wheel 自带的
  `rapidocr/models/`（本项目钉 `rapidocr>=3.10,<4`，本机 3.10.0）；上游权重来自
  PaddleOCR 官方模型（`PP-OCRv6_small_det` / `PP-OCRv6_small_rec` 与旧版
  `ch_ppocr_mobile_v2.0_cls`），由 RapidOCR 转换为 ONNX 后按 Apache-2.0 分发。
  取出命令写在 `pet/screen_capture/ocr.py` 的模块说明里；摘要与上游
  `MODEL_LICENSES.md` 第 6 节登记值逐项一致。
- **License**: **Apache License 2.0**（模型与应用同许可）。权重的版权归
  **Baidu 和/或相应的 PaddleOCR 权利人**；RapidOCR 不主张上游权重所有权。
  `LICENSE.txt` 与 `MODEL_LICENSES.md` 是 `rapidocr` wheel 内同名文件的
  **逐字拷贝，未改动**，随模型一起进包（安装器整目录拷贝）。
  Apache-2.0 §4 要求的"给接收者一份许可证"由 `LICENSE.txt` 满足；
  §4(c)/(d) 的署名与上游声明由 `MODEL_LICENSES.md` 原文满足；§4(b) 的"改动声明"
  不适用——我们没有修改模型文件（原样拷贝，文件名与字节都未变），
  上游对"转换后产物"建议的声明文本随 `NOTICE.md` 一并附上。
- **Used by**: `pet/screen_capture/ocr.py`（本地 OCR，纯离线）与
  `pet/screen_capture/actions.py`（「截图 → 提取文字」动作）。
  **隐私边界：识别全程在本机内存里完成，不联网、不落盘**——模型与字符表都在
  包里（字符表内嵌在识别模型的 ONNX 元数据里，没有额外的字典文件被下载）；
  加载路径上不存在下载分支（自建 ONNXRuntime 会话注入 rapidocr，短路其"模型路径
  为空就去网上下"的分支）。
- **校验**：构建期由 `scripts/verify_rapidocr_models.py` 按「文件名 + sha256」
  三重核对（源目录一次、产物里再一次），任一不符即中止打包。缺件或换版时产品
  **不会崩**——只会让识别静默变差（错字/漏行）或直接报"请重新安装"，所以必须在
  打包阶段拦住。运行期不做摘要校验（每次启动多读 31 MB 只为一件事 CI 该做的事）。
- **测试数据**：`rapidocr/models/` 之外的一切（推理引擎、字符表、配置）不进本仓库。

### 新增运行时依赖（`requirements.txt`）

| 发行包 | 版本约束 | License | 用途 |
| --- | --- | --- | --- |
| [`rapidocr`](https://pypi.org/project/rapidocr/) | `>=3.10,<4` | Apache-2.0 | OCR 流水线（检测/识别/方向分类的预处理与后处理） |
| [`onnxruntime`](https://pypi.org/project/onnxruntime/) | `>=1.20` | MIT | 推理引擎（CPU-only，显式关自旋与 GPU） |
| [`numpy`](https://pypi.org/project/numpy/) | `>=1.26` | BSD-3-Clause | `QImage` 像素缓冲 → BGR 数组；输出块坐标 |
| [`omegaconf`](https://pypi.org/project/omegaconf/) | `>=2.3` | BSD-3-Clause | rapidocr 的配置容器（用 `allow_objects` 注入自建会话） |

许可名取自各发行包自带的 LICENSE 文件（`rapidocr` 为 Apache-2.0、
`onnxruntime/LICENSE` 为 MIT、`numpy` 与 `omegaconf` 为 BSD-3-Clause）。
这四个包之外，`rapidocr` 还会带进一批传递依赖，它们同样随包分发；各自的许可与
版权声明随其 wheel 一起（PyPI 页面与 wheel 内的 `dist-info/licenses/`），
全部是宽松许可（GEOS 的 LGPL 除外，且是随 shapely 动态链接的独立库）：
`opencv-python` / `opencv-python-headless`（Apache-2.0；rapidocr 声明的发行名是
headless，本机两个发行名同版本并存、都提供 `cv2` 顶层模块与同名的 FFMPEG 视频
后端 DLL）、`shapely`（BSD-3-Clause，其二进制发行还捆绑 GEOS，**LGPLv2.1**，
条款见 wheel 内 `LICENSE_GEOS`）、`pyclipper`（MIT）、`six`（MIT）、
`PyYAML`（MIT）、`tqdm`（MPL-2.0 AND MIT）、`colorlog`（MIT）、
`requests`（Apache-2.0，项目本身已依赖）。
`numpy`/`cv2` **不再**出现在构建脚本的"构建机环境污染"排除清单里，也不再被
`scripts/slim_bundle.py` 的违禁清单拦——它们现在是产品依赖（排除它们曾让冻结包
的 OCR 全线不可用，2026-10-13 实测）。

## 8. DeepSeek 鲸鱼标识（识屏加载动画：ds_whale.png）

- **File**：`assets/chat/ds_whale.png`（512×512 透明 PNG，由本项目用 QtSvg 渲染生成）。
- **Origin / Source**：形状路径取自 simple-icons 仓库的 `icons/deepseek.svg`
  （https://github.com/simple-icons/simple-icons ，**CC0 1.0**）；颜色为本项目
  按界面 accent 令牌施加的蓝紫渐变（#3563EA → #6A5CFF）。
- **License / 权利口径**：simple-icons 的图标路径数据按 CC0 分发；**"DeepSeek"
  名称与鲸鱼标识的商标权归深度求索（DeepSeek）所有**——本项目是其桌面宠物衍生
  作品，按品牌本意作指示性使用（不代表官方出品/背书）。

## 9. Android 客户端与构建工具

- Kotlin 及 kotlinx.coroutines：JetBrains 与贡献者，Apache-2.0。
  https://github.com/JetBrains/kotlin ，https://github.com/Kotlin/kotlinx.coroutines 。
- AndroidX / Jetpack Compose / Material Components：Android Open Source Project
  与贡献者，Apache-2.0，https://android.googlesource.com/platform/frameworks/support/ 。
- Gradle Wrapper：Gradle Inc. 与贡献者，Apache-2.0，仅作为构建工具。
  https://github.com/gradle/gradle 。Wrapper JAR 与分发 ZIP 按官方摘要校验。
- Apache-2.0 全文：`android/LICENSE-APACHE-2.0.txt`，随 APK 分发。
- APK 资产里同时分发本文件（`THIRD_PARTY_NOTICES.md`）与 `android/NOTICE.txt`：
  第 1 节的点击音 MIT 正文因此随 APK 一起走（见
  `scripts/export_android_assets.py::copy_notices`，2026-10-17 起）。
- 手机使用的 WebP 动画与图标由第 2 节角色素材派生，继续保留其署名和
  个人非商业使用限制。工具与验收说明见 [Android 文档](docs/ANDROID.md)。

---

## 10. 可选离线 AI 增强工具

- `tools/export_ai_upscale_model.py` 的 SRVGGNetCompact 网络改编自
  [xinntao/Real-ESRGAN v0.2.5.0](https://github.com/xinntao/Real-ESRGAN/blob/v0.2.5.0/realesrgan/archs/srvgg_arch.py)。
  Copyright (c) 2021, Xintao Wang，BSD-3-Clause；完整许可随本仓库保留于
  [`tools/licenses/Real-ESRGAN-BSD-3-Clause.txt`](tools/licenses/Real-ESRGAN-BSD-3-Clause.txt)。
- 官方 `realesr-animevideov3.pth` checkpoint 由使用者显式下载，模型和生成的
  ONNX 不随仓库提交；导出器核对固定摘要并仅以 `weights_only=True` 加载。
- PyTorch（BSD-style）、ONNX（Apache-2.0）和 ONNX Runtime（MIT）仅用于
  可选的独立工具环境，不新增桌宠运行时依赖。
- `docs/images/ai-upscale-2x-preview.png` 由第 2 节的角色动画派生，保留
  原画作者上善无形与素材来源署名；继续遵循非商业及同方式共享限制，
  不把 AI 生成细节当作原创角色或原生高清母版。
