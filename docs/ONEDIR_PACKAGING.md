---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: e1503192c50cbf4a9392ca071882b90f_d44cd8a3b12211f18039525400461939
    ReservedCode1: 5WS8ZIlf31sY8kOZGi3OuLj7wtTR4orlRD+uQa4vpN/zbG9icu3IcPiWHfrwX3FNAnkDwfOlDoaC6pO5on6QU9u6BhQY8Zvk9FgkMQ7xBy5xT9qvgmVNFq1i/Rd205JLy3QoCQjQoilIbKr0n59e8Zxm5NeUzvEkLeDmjfwJRUMj8DxzCVnfPdOP0Hs=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: e1503192c50cbf4a9392ca071882b90f_d44cd8a3b12211f18039525400461939
    ReservedCode2: 5WS8ZIlf31sY8kOZGi3OuLj7wtTR4orlRD+uQa4vpN/zbG9icu3IcPiWHfrwX3FNAnkDwfOlDoaC6pO5on6QU9u6BhQY8Zvk9FgkMQ7xBy5xT9qvgmVNFq1i/Rd205JLy3QoCQjQoilIbKr0n59e8Zxm5NeUzvEkLeDmjfwJRUMj8DxzCVnfPdOP0Hs=
---

# onedir 打包流水线（绿色版 zip + Inno Setup 安装包）

Android 原生测试包使用独立 Gradle 工程，共用角色素材；其工具链、资源导出和
验收见 [Android 悬浮桌宠](ANDROID.md)，不使用下述 PyInstaller 流程。

目标：**运行期零解压**——不再产生 `C:\...\Temp\_MEIxxxxxx` 缓存。

- onefile：每次启动把全部素材解压到系统临时目录；崩溃/强杀/断电残留；启动慢（GIF 版 800MB 每次全解压）
- onedir：直接从安装目录加载，任何盘都不产生 `_MEI`，启动快，卸载即净

## 一、构建 onedir + zip 绿色版

应用图标由待机封面帧生成（`python scripts/make_icon.py` → `assets/icon.ico`），
exe、运行期窗口图标与安装包共用同一份；换形象后重新生成即可（见下面
[运行期应用图标](#运行期应用图标assetsiconico三端都带)）。

```powershell
# 全部变体：
#   webm-chat（默认）| webm | gif-chat | gif（GIF 变体加 -Gif 先生成 GIF 素材）
powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm-chat
```

产物：

```
dist-onedir\dsh-pet-standalone-webm-chat\          ← onedir 目录（绿色版 = 整个文件夹）
dist-onedir\dsh-pet-standalone-webm-chat-portable.zip
```

绿色版用法：解压 zip 到任意盘（E:\、D:\、U 盘均可），双击 exe 即用；无安装、无缓存。

### 语音依赖门禁（4.3.1）

4.3.1 的语音依赖门禁在三端构建中均强制执行：构建前运行
`python scripts/verify_bundle_tts.py --environment`，要求已安装的 edge-tts 满足
`requirements.txt` 的 `>=7.3.1,<8`；冻结后运行同脚本的 `--app-dir` 检查，
同时核对模块闭包、包内发行元数据和构建环境版本。旧版本、缺元数据或版本不一致
均中止打包。环境依赖可用 `python -m pip install -r requirements.txt` 更新。
不能只在系统 Python 升级：必须用实际运行 PyInstaller 的解释器安装和检查。
协议变更的旧版 403 对照及真实试听证据见
[4.3.1 交付报告](PR-REPORT-DESKTOP-431-2026-10-16.md)。
**`--app-dir` 那一份清单从 2026-10-17 起还要求 `keyring`**（收费后端的凭据存储，
四个变体都要有；理由见下方第四节「`keyring` 四个变体都带」）。

### 包体瘦身（默认开启，`-SkipSlim` 可关闭）

构建在 Qt runtime 复制之后、中文编码自检之前自动执行 `python scripts\slim_bundle.py`：

- **移除白名单**：Qt Quick/QML 栈（`Qt6Quick` / `Qt6Qml*`）、`Qt6VirtualKeyboard` + 平台输入法插件、`Qt6OpenGL`、`Qt6Pdf` + `qpdf.dll` 图像插件、`opengl32sw.dll`、非中英 `*.qm` 翻译（保留 12 个中英）、PIL `_avif` 扩展、OpenCV 的 FFMPEG 视频后端（`_internal/cv2/opencv_videoio_ffmpeg*.dll`，本机实测 28 MB —— 本地 OCR 只用 cv2 的图像算子，不碰视频 I/O；删掉后冻结包 OCR 实测照常认出文字，见下文「随包的本地 OCR 模型」）—— 合计 125 文件 / 81 MB；
- **安全校验**：移除前做依赖闭包校验（pefile 反向 import 检测，保留的二进制若仍引用待删文件即中止）与必需清单校验（核心运行文件齐全），任一失败即中止构建；
- **效果**：onedir 目录 309 MB → 256 MB，portable zip 176.2 MB → 146.5 MB（−12.8%）；
  **注意口径**：上表与这组数字都是 **2026-09 的实测**（当时的包体还没有本地 OCR 的
  numpy / cv2 依赖）。本地 OCR 把它们带回包之后，产品变体**没有重测过**体积；
  手边有实测数字的是 OCR 探针包（2026-10-13，`--console` 最小依赖，不是产品变体）：
  `du -sm` 334 MB，删掉那一个 OpenCV 视频后端（28 MB）后 306 MB，移除清单命中
  100 项、违例 0（证据：`.scratch/screen-capture/build_ocr_hidden.out`）。
  产品变体下次出包时应该顺手量一遍并回填这里，而不是继续引用 2026-09 的数字；
- 后续需要完整 Qt 栈（例如启用 QML 界面）时加 `-SkipSlim`；
- 冒烟建议：把配置切到 `voice_chime_schedule=every_minute` 启动 exe，确认 `%APPDATA%\dsh-pet-standalone-<variant>\voice_chime_cache` 新增 mp3（edge-tts 合成落盘），验证完恢复配置。

### 运行期应用图标：`assets/icon.ico`（三端都带）

macOS 的正式构建在最终签名前写入 `LSUIElement=true`：独立设置进程从启动起
作为 Accessory 运行，避免先登记成 Dock 最近应用再隐藏。主进程仍按
`show_dock_icon` 显式切换 Regular / Accessory；这不是取消用户的 Dock 显示设置。
实机记录见 [macOS 多屏与 Dock 修复报告](PR-REPORT-MACOS-DESKTOP-2026-10-21.md)。

`--icon assets/icon.ico`（Windows）/ `--icon assets/icon.icns`（macOS）只决定
**可执行文件 / `.app` 的图标资源**；窗口标题栏那一个图标是运行期由
`pet/application_icon.py::load_application_icon()` 从 `<_MEIPASS>/assets/icon.ico`
读出来的（源码运行时取仓库根的同一相对路径）。所以这一份本身**也必须进包**，
三个构建脚本各有一行：

| 脚本 | 那一行 |
|---|---|
| `scripts/build_onedir.ps1` | `--add-data "assets\icon.ico;assets"` |
| `scripts/build_macos.sh` | `--add-data "assets/icon.ico:assets"` |
| `scripts/build_linux.sh` | `--add-data "assets/icon.ico:assets"` |

- **为什么 `--icon` 不算数**：它写的是**文件图标**，冻结进程里窗口图标读的是
  `_MEIPASS` 布局；而**漏掉这一行不会有任何构建报错**——包照出、图标静默没有。
  （反过来，源文件**不存在**时 PyInstaller 会直接报 `Unable to find '<源>'` 并中止
  构建：本机实测 6.22.2，文件源与目录源都是这个行为。所以这里不需要再加一道存在性
  门，要守的是"这一行在不在、指向对不对"。）
- **真机实测的落点**：`--add-data "assets/icon.ico;assets"` 在冻结进程里就是
  `<_MEIPASS>/assets/icon.ico`（onedir 的 `_MEIPASS` = 安装目录里的 `_internal`；
  本机用只带这一行的最小 onedir 探针包实测：产物 `_internal/assets/icon.ico`
  419 110 字节，冻结进程里 `is_file()` 为真）。
- **macOS 上它同样在 `_MEIPASS` 里看得见（源码级推断，未在 Mac 上跑）**：
  PyInstaller 6 把**数据**目录放 `Contents/Resources`，再把整个目录交叉链接（符号
  链接）回 `Contents/Frameworks`，而 `sys._MEIPASS` 就是 `Contents/Frameworks`
  ——两条规则写在同一段源码注释里（`PyInstaller/building/osx.py`，本机装的是
  6.22.2）。`verify_rapidocr_models.py` 找产物时同时试
  `Contents/Resources/<rel>` 与 `Contents/Frameworks/<rel>`，正是同一机制的既有证据。
  Linux 无此层，`_MEIPASS` 就是产物根。
- **Linux 上更要靠它**：PyInstaller 在 Linux 忽略 `--icon`（见 `build-linux.yml`
  的说明），窗口/任务栏图标只能来自这条 `--add-data`。
- **图标本体是构建期生成的**：`scripts/make_icon.py` 从待机封面帧出
  `assets/icon.ico`（Windows 构建第 [0/3] 步；macOS 另跑 `--icns` 出 `.icns`；
  Linux 不重生成，随包用的就是入库的那一份）。入库的是**多尺寸** ICO（实测 10 档：
  256/128/96/64/48/40/32/24/20/16，419 110 字节），只为保证干净克隆与 CI 能构建
  （`.gitignore` 里是白名单）。
- **安装包不用额外条目**：`packaging/dsh-pet.iss` 的 `[Files]` 递归拷整个 onedir
  目录，`SetupIconFile` 另用同一份 ICO 作安装包图标。
- **契约测试**：`tests/test_application_icon.py` 钉住三件事——三端脚本里的
  `--add-data` 真的挂在 PyInstaller 调用上、目标目录等于产品常量
  `pet/application_icon.py::APPLICATION_ICON_RELATIVE_PATH`（`("assets", "icon.ico")`，
  唯一真相源，脚本里不抄第二份路径）、多尺寸与生成器 `make_icon.ICON_SIZES` 一致；
  顺带钉住"没有 Qt GUI 应用时不许构造 QIcon"（真实崩溃形态：无应用时
  `QIcon(<文件>)` 让进程以退出码 127 死掉，空的 `QIcon()` 无碍）与
  "缺件/坏件只回退成空图标、不抛异常"。

### 随包分发的第三方二进制：`resources/presentmon/`

游戏帧率段（灵动岛 `fps`）的读数是桌宠**起一个独立子进程**跑官方 PresentMon 拿到的，
程序本体随 Windows 包分发：

| 文件 | 说明 |
|---|---|
| `resources/presentmon/PresentMon-2.6.0-x64.exe` | 上游 v2.6.0 release 的独立控制台程序（x86-64），sha256 `b2a706bc…f1af`，980320 字节 |
| `resources/presentmon/LICENSE.txt` | 同一 release 源码树里的 MIT 许可原文（逐字节相同），随二进制一起进包 |

- **只 Windows**：`--add-data` 只写在 `scripts/build_onedir.ps1`，四个 Windows 变体
  （webm / webm-chat / gif / gif-chat）都带；`scripts/build_linux.sh` 与
  `scripts/build_macos.sh` 不加载也不捆它（`tests/test_presentmon_packaging.py` 钉住）。
  非 Windows 平台上 `pet/game_fps.py` 整段按「不支持」呈现。
- **架构只有 x86-64**：上游 release 里 x64 与 ARM64 是两个资产，产品只支持 x64，
  构建门会校验 PE 头里的机器类型（ARM64 二进制混进来即中止）。没有也不要有 arm64 变体。
  **运行期也判架构**（`game_fps.platform_problem`：读 `PROCESSOR_ARCHITECTURE` /
  `PROCESSOR_ARCHITEW6432`，ARM64 与其它架构按 `unsupported` 降级并说明「ARM64 未验证」）
  ——构建期那道门只管"包里是哪个二进制"，管不了"跑在什么机器上"。
- **两道门**（都在 `scripts/build_onedir.ps1` 里，非零即中止构建）：
  1. PyInstaller **之前**：`python scripts\verify_presentmon.py` 校验源目录里的文件；
  2. **产物里**：`python scripts\verify_presentmon.py --path <app>\_internal\resources\presentmon\<exe>`
     再验一次——`--add-data` 在源目录缺失时一个错都不报，产物会静默少一个后端，而
     界面看不出来（那一段只按「缺少内置采集程序」呈现，功能永远不出数）。
  判据只有一份，来自 `pet/game_fps.py` 的 `BACKEND_EXE_NAME` / `REQUIRED_SHA256`；
  本目录、构建脚本与验证脚本都不再写第二份字面量。
- **升级 PresentMon**：换 exe 后同时改 `pet/game_fps.py` 的 `BACKEND_EXE_NAME` /
  `REQUIRED_SHA256` / `SOURCE_TEXT`、本节的表格与 `THIRD_PARTY_NOTICES.md` 第 6 节。
  文件名自带版本号，就是为了不让「同名不同版」成为静默替换。
- **手工取得（换机 / 升级时）**：`curl -L -o resources/presentmon/PresentMon-2.6.0-x64.exe <release 资产 URL>`，
  然后 `python scripts/verify_presentmon.py` 自证；许可文本从同一 release 的源码树取
  `LICENSE.txt`。构建过程本身**不联网**：仓库带二进制，CI 与干净克隆才能离线复现，
  这也是本仓库对「构建必需二进制」的既有处置方式（同 `assets/icon.ico`）。

### 随包的本地 OCR 模型：`resources/rapidocr_models/`

「截图提取文字」（快速识屏）走**随包**的三个 ONNX 模型，加载路径上没有任何下载分支：

| 文件 | 说明 |
|---|---|
| `resources/rapidocr_models/PP-OCRv6_det_small.onnx` | 文本检测（9.5 MiB），sha256 `090f04ab…f94f` |
| `resources/rapidocr_models/PP-OCRv6_rec_small.onnx` | 文本识别（20.2 MiB），sha256 `6f327246…4884` |
| `resources/rapidocr_models/ch_ppocr_mobile_v2.0_cls_mobile.onnx` | 方向分类（0.6 MiB），sha256 `e47acedf…215c` |
| `resources/rapidocr_models/LICENSE.txt`、`MODEL_LICENSES.md` | 上游 Apache-2.0 全文与模型许可/出处声明（逐字节拷贝，随模型进包） |
| `resources/rapidocr_models/NOTICE.md` | 本目录的出处与许可说明（本项目编写） |

三个 ONNX 合计 **30.3 MiB / 31.7 MB**（31 749 509 字节，2026-10-13 实测；
`du -sm` 报 31 MB），加上许可证与说明三份文本共 22 659 字节。

- **三端都带**：`--add-data "resources/rapidocr_models;…"` 写在三个构建脚本里
  （`build_onedir.ps1` / `build_linux.sh` / `build_macos.sh`），四个变体都有。
- **引擎模块本身还要 hidden-import**（2026-10-13 交叉审查 B1）：截图的引擎走的是
  **动态 seam**——`pet/screen_capture/actions.py::_resolve_engine` 用 `importlib` 按
  字符串（`OCR_MODULE = "ocr"`）解析 `pet.screen_capture.ocr`，仓库 `pet/` 下没有一处
  静态 import 指向它，PyInstaller 的静态分析从入口摸不到。少了这条参数，模型与配置
  都在包里、**引擎模块不在**：冻结包里解析不出引擎，产品不崩，只是右键
  「截图 → 提取文字」永远置灰（本机实测：不带那条参数的包 `ocr_engine_available()`
  为假，带上的包认出字，见 `.scratch/screen-capture/build_ocr_hidden.out`）。
  三个构建脚本因此都带 `--hidden-import pet.screen_capture.ocr`
  （守卫：`tests/test_screen_capture_packaging.py`）。
  **它还决定整条依赖链在不在包里**：`rapidocr` / `cv2` / `numpy` / `onnxruntime` 都是
  靠 `ocr.py` 的静态 import 才进图的。OCR 探针包实测：不带的产物 200 MB（本地 OCR
  整条链都不在，却还扛着 31 MB 用不上的模型），带上的 334 MB——**这条 hidden-import
  是本地 OCR 在冻结包里是否存在的那一个开关**；本地 OCR 在包里的边际体积 ≈ 134 MB
  （含 31 MB 模型；探针包口径，非产品变体）。
- **还要带 rapidocr 的两份配置数据**：`config.yaml` / `default_models.yaml` 从已装的
  `rapidocr` 包里显式取（构建脚本用 `python -c "import rapidocr; …"` 解析包目录），
  落点 `_internal/rapidocr/`（macOS 的 `.app` 按 bundle 规范落在 `Contents/Resources/`
  下同名路径——产物侧的门按"存在性"找这两处，不按平台猜）。不这么做的话冻结包里引擎
  连构造都失败（2026-10-13 实测）；
  而用 `--collect-data rapidocr` 会把它 wheel 自带的同一批 30.3 MiB 模型**再收一份**，
  所以只补这两份 yaml。
- **判据只有一份，来自 `pet/screen_capture/ocr.py`**（`MODEL_FILES` / `MODEL_SHA256` /
  `MODEL_RELATIVE_DIR`）**与 `actions.py` 的 seam**（`OCR_MODULE`，脚本用
  `required_bundle_modules()` 现算，不抄第二份字面量）。缺件或换版不会让程序崩，
  只会让识别静默变差（错字/漏行）或直接报「请重新安装」，所以必须在打包阶段拦住。
- **两道门 × 三端的实际接线**（`scripts/verify_rapidocr_models.py`）：
  1. **构建前**：`python scripts/verify_rapidocr_models.py` 校验源目录里的三个模型
     （三端都有，排在 PyInstaller 之前）；
  2. **产物里**：`python scripts/verify_rapidocr_models.py --app-dir <产物根>` 从**产物**
     里判三件事——引擎模块在不在产物的模块图里（读内嵌 PYZ 的 TOC，读不出来即判失败）、
     模型在不在且 sha256 对不对、rapidocr 的两份配置在不在。三端脚本都调了它
     （Windows 在 `build_onedir.ps1`，Linux/macOS 在各自 shell 脚本里）。
  **真机跑过的只有 Windows**：`--app-dir` 在 Windows 上实测过（正例与反例都跑过，
  见 `.scratch/screen-capture/build_ocr_hidden.out`）；Linux/macOS 那一行是按同一份
  实现接的线，**从未在本机构建或运行过**（本机是 Windows），别把它当作已验证。
- **`numpy` / `cv2` 不再是「构建机环境污染」**：本地 OCR 直接 import numpy、rapidocr
  直接 import cv2，两者都是随包依赖（`requirements.txt` 已声明）。构建脚本的
  `--exclude-module` 与 `slim_bundle.py` 的 `FORBIDDEN_GLOBS` 里的 numpy 条目都已删除
  ——留着会让**每一次**构建红在一个完全正常的产物上；而排除它们曾让冻结包的 OCR
  全线不可用（2026-10-13 实测，v3 变体）。
- **升级模型**：换文件后同时改 `pet/screen_capture/ocr.py` 的 `MODEL_FILES` /
  `MODEL_SHA256`、本节的表格与 `THIRD_PARTY_NOTICES.md` 第 7 节。文件名自带版本号
  （`PP-OCRv6_*`）就是为了不让「同名不同版」成为静默替换。

### 随包的许可与声明：`LICENSE` + `THIRD_PARTY_NOTICES.md`（2026-10-17）

三个构建脚本各加两行 `--add-data`（`build_onedir.ps1` 是 `"LICENSE;."` 与
`"THIRD_PARTY_NOTICES.md;."`，两个 shell 脚本用 `:` 分隔），落在**包的数据根**——
onedir 里就是 `_internal/`，安装包整目录拷贝即随分发：

| 文件 | 为什么必须随包 |
|---|---|
| `LICENSE` | 项目自身的 MIT 许可全文 |
| `THIRD_PARTY_NOTICES.md` | 第 1 节是内置点击音（`assets/sounds/duck/Ya1.mp3` / `Ya2.mp3`，MeteorNOX）的**完整 MIT 正文**；其余各节是角色素材署名限制、PresentMon / OCR 模型 / lunar-python 等随包第三方组件的许可与来源 |

- **为什么不能只靠源码仓库**：`assets/sounds` 一直在打（`--add-data "assets\sounds;assets\sounds"`），
  而许可正文只在仓根那份文档里。产物是独立分发的复制件，MIT 要求许可正文随复制件一起走，
  「见源码仓库的 THIRD_PARTY_NOTICES.md」对拿到 zip / 安装包 / APK 的用户不成立。
- **漏了不报错**：`--add-data` 在源文件存在时静默拷贝，删掉这两行不会有任何构建错误，
  只是产物里少两份声明（同 `assets/icon.ico` 那类失败模式）。守卫是
  [`tests/test_packaging_keyring_notices.py`](../tests/test_packaging_keyring_notices.py)：
  三端脚本里那两行必须真的挂在 `--add-data` 上、目标目录是本节的 `.`。
- **本机实测的落点**：`_internal/LICENSE` 与 `_internal/THIRD_PARTY_NOTICES.md`
  （`_MEIPASS` 指向 `_internal`，探针源码 `probe_frozen.py` 与原始输出见
  `.scratch/packaging-final-20261017/evidence.txt`；最小探针，不是产品变体）。
- **Android 侧同一件事**：`scripts/export_android_assets.py::copy_notices` 把
  `android/NOTICE.txt`、`android/LICENSE-APACHE-2.0.txt` 与仓根
  `THIRD_PARTY_NOTICES.md` 一起拷进 APK 资产根（见 [Android 文档](ANDROID.md)）。
- **新增/替换随包素材或第三方二进制时**：同步维护 `THIRD_PARTY_NOTICES.md` 的对应小节
  （换版本要同时改产品侧常量与本节表格，见各条目的「升级路径」）。

### 游戏帧率的采集权限（不随包分发任何提权组件）

PresentMon 的 ETW 会话只对管理员或 `Performance Log Users`（SID `S-1-5-32-559`）成员
开放。**包里没有提权助手**：2026-10-11 的「按需 UAC 独立助手」（`resources/fps-helper/`、
`native/fps_helper/`、`scripts/build_fps_helper.ps1`、`verify_fps_helper.py`）已整体归档到
`.scratch/product-fps-uac-20261011/archived-uac-helper/`，`build_onedir.ps1` 不再编译、校验或
打包它。

- **权限怎么拿**：设置页灵动岛帧率段的「授予采集权限…」按钮（`pet/fps_permission.py`），
  弹**一次**系统管理员授权窗口，把当前账户加入该组；桌宠本体始终普通权限运行。
  组成员关系只进**新登录令牌**，所以需要注销并重新登录后生效。
- **打包侧影响**：只用系统自带的 `powershell.exe` / `whoami.exe`，**不新增任何随包文件**，
  构建管线不变。
- **没验证的**：真实提权加组、重新登录后真实 ETW 会话、任何真实帧读数（本机从未执行）。
  口径见 [`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md) 2026-10-12 条。

## 二、Inno Setup 安装包（正式分发）

本机已装：`E:\tools\InnoSetup6\ISCC.exe`（便携模式，免管理员）。
通用脚本 `packaging\dsh-pet.iss` 用 `/D` 定义编译任意变体：

```powershell
# webm-chat（脚本默认值）
E:\tools\InnoSetup6\ISCC.exe packaging\dsh-pet.iss

# webm
E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-webm /DMyAppExeName=dsh-pet-standalone-webm.exe /DMyAppDir=..\dist-onedir\dsh-pet-standalone-webm "/DMyAppId={{ED2590E4-A968-4E8D-B7C4-75DFE012D0E9}}" "/DMyAppDisplay=dsh-pet-standalone (WebM)" packaging\dsh-pet.iss

# gif-chat
E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-gif-chat /DMyAppExeName=dsh-pet-standalone-gif-chat.exe /DMyAppDir=..\dist-onedir\dsh-pet-standalone-gif-chat "/DMyAppId={{7FE1EEDD-91DB-4F4B-834D-894DFE782256}}" "/DMyAppDisplay=dsh-pet-standalone (GIF Chat)" packaging\dsh-pet.iss

# gif
E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-gif /DMyAppExeName=dsh-pet-standalone-gif.exe /DMyAppDir=..\dist-onedir\dsh-pet-standalone-gif "/DMyAppId={{308454BF-3FD5-4A30-B0FF-1D23BF31DCF1}}" "/DMyAppDisplay=dsh-pet-standalone (GIF)" packaging\dsh-pet.iss
```

产物：`dist-onedir\<shortname>-setup.exe`

安装包特性：

- 免管理员（`PrivilegesRequired=lowest`），默认装 `%LOCALAPPDATA%\Programs\...`，向导中**用户可自行选择任意盘符**
- 创建开始菜单/桌面快捷方式；装完可选立即运行
- 控制面板可卸载；卸载时在删除 exe **之前**调用 `--uninstall-cleanup`（自启项 /
  Claude hooks / DSH 桥接插件），退出码与失败记录见 `[Code]` 段与配置目录里的
  `uninstall-cleanup.log`。安装目录里的 `_MEI*` **不再按名字通配删除**（见第五节）
- 语言：简体中文 + English

## 三、冒烟验证清单

1. 启动 `dist-onedir\<name>\<name>.exe`，8 秒后确认进程存活
2. **系统临时目录无新增 `_MEI*`**（onedir 根本不解压）
3. exe 同目录无 `_MEI*` 残留
4. 退出后进程全部结束
5. `_internal\resources\presentmon\` 下有采集后端与它的 `LICENSE.txt`（构建期已自动校验
   名称 / sha256 / x86-64，见上面「随包分发的第三方二进制」）
6. `_internal\resources\` 下**没有** `fps-helper\`（提权助手已归档，不随包）
7. `_internal\resources\rapidocr_models\` 下有三个 `.onnx` 与 `LICENSE.txt` /
   `MODEL_LICENSES.md`，`_internal\rapidocr\` 下有 `config.yaml` /
   `default_models.yaml`（构建期已自动校验模型名称 / sha256，见上面「随包的本地 OCR 模型」）；
   点一次「截图 → 提取文字」应能认出选区里的文字。
   **注意**：上面那三样是**资源**，看得见；第二件致命的东西是**引擎模块本身**
   （`pet.screen_capture.ocr`，动态 seam 的目标，只在产物内嵌的 PYZ 里，目录里看不到），
   它由 `verify_rapidocr_models.py --app-dir` 从模块图里读出来判——手工冒烟时如果
   「提取文字」是灰的，先跑这条门，别再去看模型目录（模型全在也照样会灰）
8. **FPS 开关打开、当前账户不在 `Performance Log Users` 组时**：岛上那段不显示任何数字，
   而是采集链路自己的权限原文；设置页帧率段出现「授予采集权限…」按钮
   （本机没有验证过真实加组后的采集，见上面「游戏帧率的采集权限」）
9. `_internal\assets\icon.ico` 在包里（419 110 字节、10 档尺寸），启动后窗口标题栏
   是桌宠图标而不是系统通用图标。缺了这一件产品不崩、构建也不报错，只是图标静默
   消失，所以接线由 `tests/test_application_icon.py` 的打包契约测试守住
   （见上面「运行期应用图标」）。
   **本机实测**（真实 `windows` 平台插件，非 offscreen）：`setWindowIcon(load_application_icon())`
   之后 `WM_GETICON(ICON_BIG)` 拿到真实 HICON 句柄，不调那一行时 big/small 都返回 0
   （= 系统通用图标）；标题栏截图见 `.scratch/appicon/titlebar.png`。
10. `_internal\LICENSE` 与 `_internal\THIRD_PARTY_NOTICES.md` 在包里，后者含内置点击音的
   完整 MIT 正文。同样是"缺了不报错"的一类，见上面「随包的许可与声明」。
   **本机实测**（最小 onedir 探针，非产品变体）：`_MEIPASS/LICENSE` 与
   `_MEIPASS/THIRD_PARTY_NOTICES.md` 都在，MIT 正文逐字命中；原始输出见
   `.scratch/packaging-final-20261017/evidence.txt`。
11. **no-chat 变体的收费 TTS 凭据能存住**：`_internal` 里有 `keyring` 包，冻结进程里
   `keyring.get_keyring()` 解析出真实后端（Windows = WinVaultKeyring）。少了它，
   MiMo / 豆包这类要 API Key 的后端在 no-chat 包里**永远**自报「没配 Key」，而设置页
   还会报「已保存」。**本机实测**同上（同一次探针；消融对照：修复前参数的同类探针
   `import keyring` 直接 `ModuleNotFoundError`，而**构建阶段不报任何错**）。现在这一条由
   产物侧清单兜住：`verify_bundle_tts.py --app-dir` 的 `MINIMUM_MODULES` 含 `keyring`，
   真产物实测"修复前参数判缺、修复后不缺"（同 `evidence.txt`）；构建期以退出码 4 中止。

## 四、无 Chat 变体兼容性

无 Chat 入口使用 `packaging/pet_entry_no_chat.py`，构建规格明确排除 `pet.chat`
（**只排它**）。配置仍可能来自曾经启用 Chat 的用户目录，因此运行时
`Config._migrate_plaintext_keys_to_keyring()` 对缺失的 `pet.chat` 只跳过迁移，
不能让配置加载失败；除该明确缺失模块外的导入错误仍应抛出。

### `keyring` 四个变体都带（2026-10-17 修正）

2026-10-17 之前，三个构建脚本把 `keyring` 与 `pet.chat` 一起排除在 no-chat 变体之外
（Windows 是 `--exclude-module keyring`，Linux/macOS 是 `excludes="pet.chat,keyring"`），
收集参数 `--collect-all keyring` 只挂在 `*-chat` 分支里。这个组合是错的：

- 语音报时设置页（`pet/voice_chime_settings.py`）**刻意不依赖 `pet.chat`**（历史事故：
  设置页在打包版整体打不开），它在 chat / no-chat 两条链路上都注册；MiMo 与豆包这两个
  收费后端的 API Key 字段因此对所有变体都可见；
- 凭据只走 `pet/tts_secrets`：钥匙串优先，写不进去就退化为**写它的那个进程**的内存；
- 设置页默认跑在**独立进程**（`settings_process_isolation`，见 `pet/config.py`），
  真正合成的 `VoiceChimeService` 在主宠进程。于是包外无 keyring 时：用户填了 Key、
  界面报「已保存（仅本次运行）」，主进程读到的却是空串，收费后端永远自报「没配 Key」，
  而界面上没有任何一处说得出原因。

所以现在：`--collect-all keyring` 在**四个变体 × 三端**都无条件生效，`keyring` 不再出现
在任何排除清单里；no-chat 只排除 `pet.chat`。守卫是
[`tests/test_packaging_keyring_notices.py`](../tests/test_packaging_keyring_notices.py)
（构建参数清单）＋ [`tests/test_tts_secrets.py`](../tests/test_tts_secrets.py)（跨进程读不到
的行为回归）。界面对这条链路的说法：写失败时行状态是「没能保存上（只在本进程内存，重启
就没了）」并弹一次提示，**不再**出现「已保存」或「本次运行有效」这类承诺。

**产物侧还有一道门**：`scripts/verify_bundle_tts.py` 的兜底清单
（`MINIMUM_MODULES`）里加了 `keyring`——三端构建在 PyInstaller **之后**都会跑它的
`--app-dir`，少带 keyring 即以退出码 4 中止。只钉脚本里的 `--collect-all` 不够：
`--collect-all` 在包收集不到时只打一行 WARNING 就继续（这正是那个脚本存在的理由）。
真产物实测：修复后的参数 keyring 在包里、修复前的参数（`--exclude-module keyring`）判缺，
原始输出见 `.scratch/packaging-final-20261017/evidence.txt`。

打包验收至少包含：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_chat_subsystem.py::test_no_chat_packaging_uses_isolated_entrypoint
python -m pytest -q tests/test_config_key_migration.py
python -m pytest -q tests/test_packaging_keyring_notices.py tests/test_tts_secrets.py
```

## 五、注意事项

- **开机自启**：onedir 不需要 `start /D` 切目录（无解压），`pet/autostart.py` 现有命令无害可保留
- **`_MEI` 遗留清理按归属，不按名字**：`pet/app.py` 启动时仍调用
  `_cleanup_stale_runtime_dirs`，但 `pet/runtime_cleanup.py` 现在的候选前提是本产品
  自己的归属标记（`_MEIPASS` 里的 `.dsh-pet-runtime-owner`，两个冻结入口启动时写）：
  名字（`_MEI数字`）+ 年龄只用于筛选，**没有标记的目录一个字节都不动**——系统 Temp
  里的同名目录属于别的 PyInstaller 应用，按名字通配删除等于清别人的解包资源。
  链接/junction/重解析点不跟随，删除前复核归属与身份（防扫描与删除之间的替换）。
  因此**旧 onefile 版本留下的 `_MEI`（无标记）不会被自动清理**：这是刻意的安全降级，
  要清就人工确认后删。卸载侧同理：ISS 已去掉 `{app}\_MEI*` 通配条目
- **冻结入口的参数分流**：`--uninstall-cleanup` 与 `--settings` 都在 `import pet.app`
  之前分流（否则会拉成一只完整桌宠）；卸载清理的退出码非 0 时，卸载日志记录退出码，
  交互式卸载还会弹提示，结果同时追加到配置目录的 `uninstall-cleanup.log`
- **本机遗留旧自启项**：注册表 `HKCU\...\Run` 里的 `DesktopPet = E:\software\AI\AI的有用工具\打字统计\dist\DesktopPet.exe` 是 7 月的旧 onefile 构建（无 `start /D`、解压在 C 盘 Temp），建议删除或替换，避免开机双桌宠 + 继续污染 C 盘
- GIF 变体体积大（800MB+），zip/安装包较慢；WebM 变体约 124MB

## 六、CI 验证与三端手动构建（2026-10-11）

### 手动运行三条构建工作流

`build-windows.yml` / `build-macos.yml` / `build-linux.yml` 都支持
Actions → *Run workflow* 手动运行，两个输入：

| 输入 | 默认 | 作用 |
|---|---|---|
| `source_ref` | 空 | 要构建的 ref/SHA。留空 = 触发本 workflow 的 ref（事件默认检出）。三端对齐同一棵树时三端填**同一个提交** |
| `upload_artifacts` | `false` | 是否把产物上传为 Actions artifact。手动默认**不上传**（不往公共仓库堆大产物）；`v*` tag 推送始终上传 |

手动运行**不会**创建或改写 Release：`Publish Release` 只在 `push` + `refs/tags/` 时执行，
tag 推送的既有发布行为不变。每个工作流会把「这次构建的是哪棵树 + 装了什么」写进 run
summary（`git rev-parse HEAD`、`github.sha`、`python --version`、`node --version`、
`pip freeze`）——三端 artifact 对不对得上，看这一步的 SHA。

`pr-test.yml` 同样是 PR/push/手动三种触发：手动运行也有 `source_ref`，用于三端跨平台
对齐同一棵树。**PR 事件仍检出合并提交**（`refs/pull/N/merge`，见 run summary 里同时
记录的 head sha 与 merge sha）——"集成后才有红绿判定"的语义不为对齐需求让步。

### 帧序列素材在 CI 里怎么来

`assets/characters/<角色>/frameseq/` 是刻意不入库的派生产物（`.gitignore`），干净检出里
必然没有；而构建对素材整树拷贝 `assets/characters`（缺了不报错）。因此三条 build
工作流在构建前都会跑一次：

```bash
python scripts/prepare_frameseq_bundle.py --character shenshen
```

顺序是「先验证 → 缺才转 → 转完再验」：素材已完整（缓存命中）直接跳过转换；不完整才跑
`tools/convert_frameseq.py --character shenshen --include random,events`（`--include`
不可省：该 CLI 默认只转热集 11 段，而构建门要**全树 106 段**），全量冷转约 28 min。
两个变体共用这一次素材准备，不重复转。

- **缓存**：`actions/cache` 缓存 `assets/characters/*/frameseq`，key 由
  `python scripts/prepare_frameseq_bundle.py --print-cache-key` 算出 = 源 webm 摘要 +
  转换器/供给实现摘要 + 当前编码档 + 平台/Python/Pillow/imageio-ffmpeg 版本。命中即近零
  成本；**坏缓存不会被盲跳过**（唯一放行依据是验证器的真实退出码，陈旧档 / 半成品世代
  目录 / 源身份不符都会被判红并触发重转）。
- 缓存内容是**本仓库公开源素材的派生物**，不含任何 secret，也不是发布产物。
- **硬门在脚本里，不在 workflow 里**：`scripts/build_onedir.ps1`（非 GIF 变体）、
  `scripts/build_macos.sh`、`scripts/build_linux.sh` 都在 PyInstaller 起跑前调用
  `scripts/check_frameseq_bundle.py`，非零即中止构建。GIF 变体跳过本门：它打包
  `assets/characters_gif`（`convert_to_gif.py` 直出），没有 frameseq 这一层。
  本地构建与 CI 因此是同一口径。

### 本批未覆盖（如实）

CI 这一层只做了 test/build 门与元数据记录：**没有**新增启动/退出冒烟
（`--uninstall-cleanup` 会动 hooks 与注册表，不当冒烟用），启动与退出验证仍是实机欠项。
macOS / Linux 的真实构建必须在对应 runner 上跑过才算通过——本机（Windows）只验证了
工作流的静态结构、shell 脚本语法与素材供给入口的真实命令行为。

*（内容由AI生成，仅供参考）*
