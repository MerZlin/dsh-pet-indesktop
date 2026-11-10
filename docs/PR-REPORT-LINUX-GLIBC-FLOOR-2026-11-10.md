# PR 报告：Linux 发布包 glibc 基线钉回 Ubuntu 22.04（issue #432）

> **基线**：`34d810b`（main，v4.4.0 后）
> **分支**：`fix/linux-glibc-floor`　**日期**：`2026-11-10`
> **范围**：12 个文件（workflow 1、构建脚本 2、产品 2、测试 3、文档 2、README 1）
> **关联**：issue #432（Ubuntu 22.04 启动报 `GLIBC_2.38 not found`）

## 一、核心特性

发布的 Linux 包此前**从未能在 Ubuntu 22.04（glibc 2.35）上启动**——根因不是任何
一次仓库提交：`build-linux.yml` 从诞生起写 `ubuntu-latest`（GitHub 2025 年中已整体
漂到 24.04，2026-10-19 起又公告迁 26.04），PyInstaller 在 Linux 上会把**构建机上解析
到的系统库整份拷进 `_internal/`**（v4.4.0 实测 35 个 noble 系统库要求 GLIBC_2.38）；
叠加 `actions/setup-python` 的 CPython 工件本身被上游换到 24.04 基线重编
（`libpython3.11.so.1.0` 要求 2.38）。本 PR 把构建基线钉死为 `container: ubuntu:22.04`
+ uv 管的 CPython 3.11（实测 libpython 底线 GLIBC_2.17），并新增 ELF 符号版本门禁，
把「产物地板 ≤ 22.04 系统」变成每次出包的硬校验。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 22.04 可跑 | 修复后产物在本机 22.04 实测启动存活；356 个 ELF 最高 GLIBC 2.35 / GLIBCXX 3.4.30 |
| 2 | ELF 地板门禁 | `scripts/check_elf_symbol_floor.py` 在 `build_linux.sh` 每变体出包后硬门；v4.4.0 旧包实测点名 42 个超标文件、exit 1 |
| 3 | 基线防漂移 | 守卫测试钉住容器与 uv 接线（`TestLinuxBuildBaseline`）；runner 别名/CPython 工件再漂移时出包当场红 |
| 4 | Fcitx 插件可维护性 | 编译失败摊开 compile.log（此前被链接期 `cannot find *.o` 掩盖）；根治 `nm \| grep -q` 的 SIGPIPE 假阳性 |
| 5 | 顺手修 3 例 main 既有红 | lunar-python 新版行为翻转 ×2 + 记忆读取绕过注入时钟 ×1（与本 PR 无关但挡 CI） |

**红线 / 不变量**：Windows/macOS 构建路径零改动；`pr-test.yml` 不动；Fcitx 插件的
「按 PySide6 Qt 精确版本取私有头 + 链接改写回 PySide6 Qt」设计不变；22.04 以下
（20.04/Debian 11）仍不支持（README 已声明走源码）。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `.github/workflows/build-linux.yml` | +50/−20 | job 钉 `container: ubuntu:22.04`；apt 去 sudo 并补容器工具（git/perl/binutils/build-essential/zip/unzip/zstd/tzdata/qt6-base-dev/libgl1-mesa-dev 等）；setup-python → `astral-sh/setup-uv@v10.3.3` + `uv venv`；apt 挪到 Checkout 前（裸容器无 git 时 checkout 退化 tarball 解压不留 .git）；`safe.directory` 放行（root 与宿主 UID 不同）；记录步骤去 node 行、加容器基线行 |
| `scripts/build_pyside6_fcitx_plugin.sh` | +51/−6 | ① overlay 兼容头：`QT_GUI_REMOVED_SINCE` 是 6.8+ 的版本化移除宏（只在构建好的 Qt 里定义），jammy Qt 6.2 dev 没有 → 6.11 overlay 头预处理器报错；按目标 ABI 语义定义为 0。② 编译失败判定改为「link.txt 对象清单」：cmake 对系统 Qt 的链接失败是设计内常态（overlay 头引用的符号只在 PySide6 Qt 里），编译失败才致命，且必须摊开 compile.log。③ 产物检查失败时输出全量诊断；`nm \| grep -q` 改整量捕获——`grep -q` 早退让 nm 吃 SIGPIPE 退 141，`pipefail` 把命中判成失败（noble 时代就潜伏的时序 flake，本轮根治） |
| `scripts/build_linux.sh` | +4/−0 | 每变体出包后调用 `check_elf_symbol_floor.py --app-dir`（与既有 verify 系列同口径，本地构建同样受门） |
| `scripts/check_elf_symbol_floor.py` | +172（新增） | 纯 stdlib 门禁：遍历产物找 ELF（魔数）→ `readelf --dyn-syms --wide` 提 GLIBC/GLIBCXX 最高引用版本 → 超 2.35/3.4.30（jammy 上限）即 exit 1；零 ELF 也判失败（防路径手滑静默过） |
| `pet/chat/memory.py` | +3/−1 | `ChatMemory.load()` 的超期隐式归档判定从 `datetime.now()` 改走注入时钟 `self._clock()`（生产默认不变）——绕过注入时钟让固定 NOW 的用例越过 14 天阈值后必红 |
| `pet/festival/calendar.py` | +9/−4 | 仅 docstring：`statutory_holiday_name` 的「表外日期」语义随 lunar-python 版本而异（新版对元旦/清明/劳动/端午/中秋/国庆按规则计算，春节仍 None）；None=未知→调用方降级，语义不变 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_elf_symbol_floor.py` | +175（新增） | 版本提取纯函数（含字典序陷阱边界：2.4 < 2.35）、恰等地板通过/超一线失败、`/bin/true` 真 ELF 集成（linux+readelf 守卫）、非 ELF 忽略、零 ELF 失败、CLI 退出码、`build_linux.sh` 接线断言 |
| `tests/test_ci_workflows.py` | +33/−1 | `TestLinuxBuildBaseline`：容器钉 22.04、无 setup-python、uv 接线（防「简化」回漂）；`test_records_resolved_sha_and_toolchain` 对 linux 不再要求 node（容器无 node 且构建链不用） |
| `tests/test_balance.py` | +25/−12 | 两条过期断言改用跨 lunar-python 版本稳定的表外普通工作日（2027-03-10 周三）锁「无数据→星期规则」降级语义；原 2027-01-01 断言随版本翻转（新版已是元旦节→idle，产品行为其实变好） |
| `tests/test_memory_recall.py` | +5/−1 | 有限刻度迭代器加耗尽兜底（load() 现在也走注入时钟） |

### 未改动（故意）

- `pr-test.yml`：测试门不打包，runner 基线与产物地板无关；
- `build-windows.yml` / `build-macos.yml`：宿主 runner 构建，无此问题；
- `republish-assets.yml`：发布/重发由维护者决定，本 PR 不动发布流程；
- `requirements.txt` 不给 lunar-python 加上限：行为漂移由测试断言侧适配（锁跨版本稳定日期），加 ceilings 会腐烂。

## 三、实现要点

**为什么容器而不是 `runs-on: ubuntu-22.04`**：托管镜像别名会漂（本案元凶）、会被
退役（jammy 镜像退役时静默复发）；`ubuntu:22.04` 容器全生命周期 glibc 恒 2.35，且
宿主 runner（ubuntu-latest → 26.04）只负责跑容器，基线不受宿主影响。

**为什么 uv Python 而不是 setup-python**：setup-python 的 CPython 工件本身是移动靶
（本案第二元凶，libpython 要求 2.38，放进 22.04 容器连启动都过不了——天然报警器）；
uv 管的 python-build-standalone 系实测底线 GLIBC_2.17，带校验和。

**为什么地板是 2.35/3.4.30**：jammy 系统自带上限（glibc 2.35 / libstdc++ 6.0.30）。
产物地板 = 构建机地板（PyInstaller 拷库机制），钉构建机即钉产物。若未来某 wheel
要求更高 GLIBCXX，门禁当场红，升级为显式决策而不是静默抬高基线。

**jammy 容器的六个坑（每条都有独立提交）**：setup-uv 无浮动主版本 tag；裸容器无
git（checkout 退化 tarball）；root/宿主 UID 不同触发 dubious ownership；无 tzdata
（`tzset` 切不动 Asia/Shanghai，差 8h）；`qt6-base-private-dev` 不连带 `qt6-base-dev`
（noble 连带）；Qt6Gui config 依赖 WrapOpenGL（缺 GL 头文件）。

**frameseq 缓存不受影响**：key 只含平台串/py3.11/Pillow/imageio-ffmpeg 版本，不含
发行版——切换容器不触发 28min 素材重转（fork 实测素材步骤秒级命中）。

## 四、性能分析

**方法（可复现）**：`python scripts/check_elf_symbol_floor.py --app-dir .venv/lib/python3.11/site-packages`
环境：Ubuntu 22.04.5 / CPython 3.11.15(uv) / binutils readelf；样本：561 个 ELF

| 指标 | 实测 | 归属 |
|---|---|---|
| 门禁扫描速率 | 561 ELF / 5.26s ≈ **9.4ms/ELF**（561 个 .so 含 PySide6 全量） | 新增（每变体一次，~356 ELF ≈ 1.5s） |
| CI 构建总时长 | 修复后 1508s（fork run 42712906137，冷启动容器）；对照 v4.4.0 上游构建 3666s（1h0m6s，同流程） | 新增容器装配 ~2min，被 uv 秒级装包抵消 |
| ELF 扫描自身 I/O | 每文件一次 readelf 子进程，纯只读，无网络/磁盘写 | 新增，频率=每次出包 2 次（两变体） |

**结论**：① 稳态（用户运行期）开销**零变化**——门禁只在构建期跑，不进产物；② 新增
路径绝对成本 ~3s/次构建，触发频率=每次出包；③ 无新增系统调用面（readelf 只读）、
无网络、无线程；④ 无内存增长（扫描为流式子进程）。CI 侧 uv venv + 装包 4.2s 对比
原 setup-python+pip ~50s 反而更快。

## 五、实机运行记录

本机 = **Ubuntu 22.04.5 LTS / glibc 2.35-0ubuntu3.15 / libstdc++ 6.0.30**（就是 issue #432 报告的环境）。

- **根因现场复现（修复前）**：`./dsh-pet-standalone-webm --help` →
  `Failed to load Python shared library '_internal/libpython3.11.so.1.0': /lib/x86_64-linux-gnu/libm.so.6: version 'GLIBC_2.38' not found`（v4.4.0 与 v4.1.0 同错，证据 `/tmp/issue432/evidence/`）。
- **修复产物实测（CI run 42712906137 的 artifact）**：同机解压运行，
  `QT_QPA_PLATFORM=offscreen timeout 12 ./dsh-pet-standalone-webm` → exit 124（12 秒
  存活后被 timeout 收掉=正常启动，Qt/ffmpeg 后端日志正常，无任何 loader 报错）。
- **门禁双向验证**：同一门禁扫 v4.4.0 旧包 → 列出 42 个 GLIBC_2.38 超标文件、
  exit 1（该红必须红）；扫修复产物（webm 与 webm-chat 两变体）→「扫描 356 个 ELF，
  最高 GLIBC 2.35 / GLIBCXX 3.4.30」、exit 0。
- **边界与失败路径**：`--glibc-floor 2.0` 强制红用例（`/bin/true` 被点名）；零 ELF
  目录判失败；非 ELF 文件忽略。
- **无法自动验证的**：真实桌面（X11 合成窗、托盘、鼠标交互）与 Fcitx5 输入法实弹
  ——需要真实桌面会话与 fcitx5 环境，本机验证为 offscreen 启动存活 + 插件随包存在
  且 `handleExtendedKeyEvent` 符号校验通过（`nm` 断言，构建内建）。发布后建议
  22.04 + Fcitx5 用户实弹确认中文输入。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet/ tests/ scripts/` | All checks passed |
| 聚焦 | `pytest -q tests/test_elf_symbol_floor.py tests/test_ci_workflows.py tests/test_balance.py tests/test_memory_recall.py tests/test_frameseq_bundle_gate.py tests/test_verify_rapidocr_models.py tests/test_packaging_keyring_notices.py tests/test_application_icon.py tests/test_screen_capture_packaging.py` | 362 passed |
| 全量 | `python -m pytest -q <主套件，与 CI 同口径>` | **8935 passed / 85 skipped / 0 failed**（1302.78s，本机 22.04） |
| 断言有效性 | 门禁对 v4.4.0（真实缺陷样本）红、对修复产物绿；`--glibc-floor 2.0` 强制红 | 区分度实证 |
| CI 端到端 | fork run 42712906137（含临时跳过的版本）：pytest 四组全绿 → 素材 → 插件 → PyInstaller 双变体 → 门禁 → zip → 上传 | success, 1508s |
| 报告纪律 | `pytest tests/test_pr_report_discipline.py` | 229 passed |

注：最终分支（撤除临时跳过 + 补 3 例测试修复）因 fork Actions 被禁用未再跑 CI 端到端，
以本机全量主套件 + 聚焦门代替；上游 PR 的 CI 会按同口径复验。

## 七、已知限制与后续

- Fcitx5 实弹输入未验（需真实 fcitx5 桌面会话）；插件符号校验（`handleExtendedKeyEvent`）
  与随包存在性是构建期证据。
- 真双屏/Wayland/合成器行为与本次无关，未涉及。
- 基线只到 22.04（glibc 2.35）：更老发行版 README 声明走源码；如需更低需换
  manylinux 基线容器，属新决策。
- ubuntu-latest → 26.04 的宿主迁移（2026-10-19 公告）对本作业无影响（宿主只跑容器），
  run 15 日志里的官方公告即为佐证。
- main 的 3 例既有红已随本 PR 修复；若维护者希望单独拆分，`fix(tests)` 提交可独立摘出。

## 八、风险与回滚

- 影响面：仅 Linux 发布构建链与 3 例测试；无新增配置键、无数据迁移；Windows/macOS
  产物字节级不变。
- 回滚：`git revert` 整个分支即可；门禁若在未来的合法场景误红（如主动决定抬高基线），
  正确动作是改 `check_elf_symbol_floor.py` 的地板常量并同步 README/文档，不是绕过门禁。
- 残留状态：无（构建期工具，不写任何用户态数据）。
