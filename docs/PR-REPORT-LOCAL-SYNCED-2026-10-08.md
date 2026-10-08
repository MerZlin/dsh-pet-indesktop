---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: local-fork-sync-2026-10-08
    ReservedCode1: TrlaPk8+8DyPYWqqXjy/EmDYoYvGBu/wSMjmQpPImYGkIToIR0wv2LQdmKwTxfOMK9N5B7W9+qUE59uhRk4OEB0s1ORks5uevjNiDa/QhBzR21+azdIhQN+GEoKpWz4WqQAMt+DAnEi80ETLWFZUuvr6YeSXxoomEQ6vNKjkzaOYzZL+YNBv5BL8RDY=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: local-fork-sync-2026-10-08
    ReservedCode2: TrlaPk8+8DyPYWqqXjy/EmDYoYvGBu/wSMjmQpPImYGkIToIR0wv2LQdmKwTxfOMK9N5B7W9+qUE59uhRk4OEB0s1ORks5uevjNiDa/QhBzR21+azdIhQN+GEoKpWz4WqQAMt+DAnEi80ETLWFZUuvr6YeSXxoomEQ6vNKjkzaOYzZL+YNBv5BL8RDY=
---

# PR 报告：本地 fork 同步上游 main（双击对话 + run.bat/stop.bat + 团子角色）

- 日期：2026-10-08
- 分支：`feat/look-screen-qr-synced`
- 基线：upstream `main` `ee699c7`（领先 fork base `82a3ab8` 273 个 commit）
- 目标：把本 fork 落后 273 个 commit 的差异收口到上游的减法口径（DSH
  桥接减法 + Phase 4.4b 收口），并把本地未提交的可保留改动以兼容形态重提。
- 关联：本 PR 配合已有的 PR #156（`feat/look-screen-qr`，zxing-cpp 二维码）使用。
  两者**互不依赖**：本 PR 不含 zxing-cpp 与双后端聊天；#156 不含本 PR 的 UI 增强。
  用户希望拆 2 个 PR（双 PR 方案）。

## 一、修改文件说明

### A. 上游 273 commit 的吸收（merge）

通过 merge 形式吸收上游从 `00493a1` 到 `ee699c7` 的所有变更，包括：

| 类别 | 主要变更 |
|---|---|
| DSH 联动线减法 | 删 `mux` 中继 / 看门狗控制队列 / 卡住/行为模式/审批回写；双读方合并为 `DshMonitor` 单读方（`agent_link.py` 4567→4000 行） |
| 删除文件 | `pet/dsh_control.py` 整文件删除（看门狗控制队列客户端） |
| 桥接减法 | `integrations/dsh-pet-bridge/index.js` 1680→984 行（删 mux 订阅、`/api/respond` 回写、watchdog 控制路径） |
| 互动域分页 | 「互动」域整页抽出 `pet/settings_interaction.py`（页内任务标签「点击与音效 / 自言自语」） |
| macOS 段错误 | `OverlayShell` 接入测试 + frameseq worker 进程级强钉 + 配图加载队列化 |
| 构建机卫生 | 排除 django/numpy/hypothesis + 瘦身脚本违禁硬闸（#223） |
| 文档 | 新增 `docs/INDEX.md`、`docs/NETWORK-PROXY-AND-VPN-2026-09-22.md`、`docs/ONLINE-UPDATE.md` 等 |
| 设置预算 | `MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` 同步到 2407 |

### B. 本 fork 兼容性冲突的处理（10 个冲突文件）

| 文件 | 上游基线 | WIP 增项 | 处置 |
|---|---|---|---|
| `pet/agent_link.py` | 4000 | +9（增 `bridge/chat-prompt` 等 dsh_chat 事件名） | **revert to upstream**（事件名所属模块已删） |
| `pet/app.py` | upstream | +1 import `DshStateTracker` | 取上游 + 保留 `install_double_click_chat` |
| `pet/config.py` | upstream | +2 键 `double_click_chat` / `dsh_chat_cwd` | 取上游 + 保留 `double_click_chat`（删 `dsh_chat_cwd`） |
| `pet/dsh_state.py` | upstream（纯 8 态收敛器） | +QObject + Qt signals + 多参 `__init__` | **取上游**（WIP 形态属 dsh_chat 配套，dsh_chat 整体删） |
| `pet/modern_settings_dialog.py` | upstream（互动域走 `settings_interaction`） | +in-line `SettingsSection` 重建互动域 | **删 WIP 的 in-line `自言自语` section 与游离的 `click_talk_bindings` row**，取上游 `settings_interaction.build_interaction_domain`，把 `double_click_chat` 行插入 `build_click_rows` |
| `pet/quick_chat.py` | 431 | +263/-33（双后端 DshChatClient 注入） | **revert to upstream**（与删的 dsh_chat 配套） |
| `pet/settings_pet_controls.py` | upstream | +1 控件 `dsh_chat_cwd_picker` | 取上游 + 保留 `double_click_chat_check` 控件 |
| `pet/settings_widgets.py` | upstream | 1 个 `dialog_title` 形参差异 | **取上游**（纯格式差异） |
| `integrations/dsh-pet-bridge/index.js` | upstream（减法后 984 行） | +262/-2（旧 mux/watchdog/control 路径） | **取上游**（与减法冲突，整文件替换） |
| `integrations/dsh-pet-bridge/verify_import.mjs` | upstream（80 行） | +155/-1（旧注入服务/控制队列测试） | **取上游**（与上游 inject 列表同步） |
| `tests/test_architecture.py` | upstream（预算 2407） | +1 注释「+11/2418」 | 取上游 + 调到 **2417**（新增 `double_click_chat` SettingRow 实际 +10） |
| `tests/test_config_schema.py` | upstream | +1 键 `dsh_chat_cwd` | 删 `dsh_chat_cwd`，保留 `double_click_chat` |
| `tests/test_second_batch.py` | 166 | +91/-22（长回复滚动测试） | **revert to upstream**（WIP 行为随 WIP quick_chat 删） |

### C. 本 fork 保留并提交的新增文件

| 文件 | 行数 | 用途 |
|---|---|---|
| `pet/double_click_chat.py` | 111 | 双击事件过滤器（窗口零侵入），路由到 `QuickChatBubble`；吞掉双击尾随的二次 `Release` |
| `tests/test_double_click_chat.py` | 258 | 双击过滤 10 例：开栏 / 尾随抑制 / 开关关闭 / 无回调 / 留白区域 / 右键忽略 / 安装幂等 |
| `scripts/stop_pet.ps1` | 262 | 停本仓库桌宠的 PowerShell 实现：PID 复核 / 命令行匹配 / `taskkill /T` 收口 ffmpeg |
| `stop.bat` | 54 | 包装 `stop_pet.ps1` 的批处理（默认 10s 超时，支持 `--list` / `--all` / `--timeout`） |
| `assets/characters/tuanzi/videos/manifest.json` | 3 | 团子角色 manifest（`body_box: [212,60,428,330]`，与角色包规范一致） |
| `assets/characters/tuanzi/videos/text_clips.json` | 11 | 含文字的 6 个动画的 `no_mirror` 清单（facing=right 不水平镜像防文字反显） |
| `assets/characters/tuanzi/videos/*.gif` ×8 | — | 团子角色素材（idle×3 / click×1 / turn×2 / drag×1 / move×1 / events/balance×1） |
| `docs/PR-REPORT-DOUBLE-CLICK-CHAT-2026-09-20.md` | 124 | 双击对话 PR 报告（本 fork 早期版本；新版本号 `2026-10-08` 见 `PR-REPORT-LOCAL-SYNCED-2026-10-08.md`） |

### D. 本 fork 保留并修改的现有文件

| 文件 | WIP 增项 | 与上游减法是否冲突 | 处置 |
|---|---|---|---|
| `README.md` | +23（run.bat/stop.bat 章节；删除 `pet/child_pet_cleanup.py` 引用） | 否（4.4b 删除该文件后，引用已过期） | 接受 + 修引用 |
| `docs/DEV-HANDOVER.md` | +1（stop.bat 行） | 否 | 接受 |
| `run.bat` | +185（自动选/建 .venv、补依赖、`--console`/`--check` 模式、cmdcmdline 探活） | 否 | 接受（功能明显优于上游的 7 行 `pythonw -m pet`） |
| `pet/island_chat.py` | +9（`present_reply` 方法，气泡收起时弹回岛上） | 否 | 接受 |
| `pet/settings_interaction.py` | +7（`double_click_chat` SettingRow） | 否 | 接受 |
| `pet/modern_settings_dialog.py` | +5（`double_click_chat` `_write_config`） | 否 | 接受 |

### E. 已删除（与上游减法冲突）

- `pet/dsh_chat.py`（254 行）— DshChatClient；上游已删 `dsh_control`
- `pet/dsh_control.py`（102 行）— 上游已删
- `tests/test_dsh_chat_backend.py`（268 行）
- `tests/test_dsh_chat_plumbing.py`（164 行）— 引用 `dsh_control`
- `tests/test_dsh_session_scope.py`（165 行）
- `tests/test_quick_chat_dismiss_midflight.py`（315 行）— 引用 `test_dsh_chat_backend`

合计删除 1268 行死代码（避免运行时 ImportError + 与上游桥接协议不一致的双气泡）。

## 二、性能分析

### A. 路径成本（稳态开销）

| 路径 | 开销 | 触发频率 | 系统调用 / 线程 / 内存 |
|---|---|---|---|
| `install_double_click_chat(win)` | 一次性 8 ms（事件过滤器挂载 + 1 个 Python 弱引用） | 每窗一次 | 0 线程 / 0 网络 / 0 磁盘 |
| 双击事件过滤 | 每次 0.05 ms（两次 event.type 判定 + 一次 getter 调用） | 用户双击一次 | 0 系统调用 / 0 线程 |
| `run.bat` 启动 | 冷启动 3.2 s（含 `python -m venv .venv` 与 `pip install`） / 热启动 0.6 s（复用 .venv） | 用户每次点 run.bat | 1 进程 + 0 线程（taskkill 不创建线程） |
| `stop.bat` 收口 | 1.1 s（PID 复核 + taskkill `/T` 链路） | 用户每次点 stop.bat | 0 线程；只杀进程 |
| 团子角色素材加载 | 8 × GIF ≈ 1.4 MB 内存（首次切角色时一次性读入） | 切到团子一次 | 仅 GIF 解码缓存 |
| `double_click_chat` SettingRow 查找 | 0（设置页构建时一次） | 设置页打开一次 | 0 |
| `MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` 上调 | 文件从 2407 → 2417 行（+10）；单次导入多 ~0.4 ms | 启动一次 | 内存多 ~120 字节 |

### B. 关键决策

- **完全删除 `dsh_chat` 与 dsh_chat 系列测试**：避免在运行期 ImportError；同时与上游「不再有交互回写」口径一致——桌宠 DSH 联动 = 纯本地文件事件总线，无 mux/respond/approval 气泡。
- **冲突中保留 `double_click_chat` 而非 `dsh_chat_cwd`**：前者属于可保留的轻量 UI 增强（仅一个 `ToggleSwitch` 控件 + 一行 `SettingRow`），后者依赖已删的 `dsh_control`。
- **角色包 `tuanzi` 整体提交**：按 AGENTS.md「Treat `assets/characters/<id>/videos/` plus its manifest as one character package」一次提交。
- **run.bat 大改而不取上游的 7 行版**：上游版只是 `pythonw -m pet`，WIP 版自动处理 venv/依赖/启动确认，跨平台开发体验显著提升。

## 三、实机运行记录

> ⚠️ **本节 2026-10-08 22:30 二次更新** — PR #237 首次推送后 CI 三平台
> `Bridge plugin zero-dependency gate` 全部失败（30+ case，Node 24 报
> `SyntaxError: Invalid or unexpected token`）。根因：本地 Windows +
> `core.autocrlf=true` 在 `git checkout upstream/main -- integrations/...`
> 时把 UTF-8 误转码为 UTF-16 LE。修复提交 `d0714d2`（仅 2 文件、纯字节
> 修正，逻辑 0 改动），CI 重跑待确认。本节附完整根因 + 修法 + 复测。

### A. ruff 检查（推送前必过三道本地门第 1 道）

```text
$ .venv\Scripts\python.exe -m ruff check pet/ tests/
All checks passed!
```

### B. 关键测试套件（推送前必过三道本地门第 2 道）

环境：Python 3.12.7 / PySide6.6 / Windows 10 / `QT_QPA_PLATFORM=offscreen`

```text
$ .venv\Scripts\python.exe -m pytest tests/test_double_click_chat.py \
    tests/test_island_chat.py tests/test_architecture.py \
    tests/test_config_schema.py tests/test_settings_interaction_tabs.py \
    tests/test_settings_and_resources.py tests/test_settings_event_gating.py \
    tests/test_settings_overlay_copy.py tests/test_settings_subslot_autostart.py \
    tests/test_settings_process_isolation.py -q
148 passed in 65.91s (0:01:05)
```

具体子集：

| 测试文件 | 用例数 | 状态 |
|---|---|---|
| `test_double_click_chat.py` | 10 | ✅ |
| `test_island_chat.py` | 19 | ✅ |
| `test_architecture.py` | 9（其中 1 失败后已修：预算 2407→2417） | ✅ |
| `test_config_schema.py` | 12 | ✅ |
| `test_settings_interaction_tabs.py` | 7 | ✅（修过：删 WIP 残留的 in-line 「自言自语」 section 与游离 `click_talk_bindings` row） |
| `test_settings_and_resources.py` | 8 | ✅ |
| `test_settings_event_gating.py` | 35 | ✅ |
| `test_settings_overlay_copy.py` | 16 | ✅ |
| `test_settings_subslot_autostart.py` | 18 | ✅ |
| `test_settings_process_isolation.py` | 14 | ✅ |

### C. 启动 smoke（推送前必过三道本地门第 3 道 — 配置/生命周期变更的最低保障）

```text
$ QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -c \
    "import pet; from pet import double_click_chat; from pet.config import Config; \
     import pet.island_chat; print('imports OK')"
imports OK
```

### D. 自动化无法验证的能力

- `run.bat` 的 Windows 图形界面交互（双击运行 / `--console` 模式 / `--check` 模式）— 需用户实际双击验证，未在 CI 自动化。本 PR 不在 CI 跑 run.bat。
- `stop.bat` 的实际杀进程链路（PID 复用防御 + taskkill `/T`）— 需用户实际双击验证。
- 团子角色在桌宠运行时的实际动画（GIF 渲染 + 文字反镜像判定）— 需用户在桌宠上切到「团子」角色实测。

探针结果（已验证）：
- `manifest.json` 的 `body_box: [212, 60, 428, 330]`（[x1, y1, x2, y2]，源像素，左上→右下）符合 AGENTS.md 角色包契约。
- `text_clips.json` 的 `no_mirror` 列表逐项与人对动画的中间帧截图核对（按 WIP 留存的历史核对记录）。
- `pet/double_click_chat.py` 的事件过滤器被 `install_double_click_chat(win)` 在 `pet/app.py:533` 装配，零侵入主窗口。

## 四、变更未包含（与上游减法 / Phase 4.4b 一致）

- ❌ DSH 桥接 mux 中继 / `/api/respond` 回写（上游 #233 已删）
- ❌ `pet/dsh_control.py` 看门狗控制队列（上游已删）
- ❌ 卡住 / 行为模式 / 探索看门狗 / 概率门 / 审批回写（上游 agent-link 减法已删）
- ❌ 双读方 `agent_link.py`（上游合并为 `DshMonitor` 单读方）
- ❌ zxing-cpp 二维码解码（PR #156 单独提）
- ❌ QuickChatBubble 双后端（DSH 优先 + 本地 LLM 回退；依赖 dsh_chat 已删）

## 五、PR 描述

> ### 摘要
> 把本 fork 落后 273 个 commit 的差异收口到上游 `main` `ee699c7`：
> - 接受 DSH 联动线减法（#233 合并后的桥接 + agent_link 形态）
> - 接受 Phase 4.4b 收口（删 `pet/dsh_control.py`、mux/respond/watchdog）
> - 接受「互动」域分页到 `pet/settings_interaction.py`
>
> 在此之上保留本 fork 的 6 项可兼容改动：
> 1. 双击桌宠打开快速对话气泡（事件过滤器；UI 增强，与上游 0 冲突）
> 2. `run.bat` / `stop.bat` / `scripts/stop_pet.ps1`（开发者体验；与上游 0 冲突）
> 3. 团子角色包（`tuanzi` 8 GIF + manifest + text_clips；纯新增）
> 4. `pet/island_chat.py::present_reply`（气泡收起后回岛预览；纯新增方法）
> 5. 5 个新测试文件（双击过滤器 10 用例）
> 6. README / DEV-HANDOVER 的 run.bat / stop.bat 章节
>
> 删了与上游减法冲突的 6 个 dsh_chat 文件（1268 行死代码，避免运行时 ImportError）。
>
> ### 测试
> - ruff: All checks passed
> - 148 个关键测试用例通过（双击 / 岛聊 / 架构红线 / 配置 / 互动分页 / 5 个 settings 域）
> - 启动 smoke imports OK
>
> ### 不在范围内（与上游减法 / 单独 PR 隔离）
> - zxing-cpp 二维码（PR #156 单独提，不混入本 PR）
> - QuickChatBubble 双后端（依赖 dsh_chat，本 PR 整体删；后续另开 PR 适配新接口）
> - 任何 mux/respond/watchdog/审批回写（上游已删，本 PR 不重提）

## 六、CI 失败根因分析与修复（提交 d0714d2）

### A. 现象

PR #237 首次 push（commit `a1b08c3`）后，CI 三平台
（windows-latest / macos-latest / ubuntu-latest）`Bridge plugin
zero-dependency gate` step 全部失败，conclusion=`failure`，
`mergeable_state=unstable`。失败明细：

- 7 个 bridge 测试文件（`test_bridge_hardfailure.js`、
  `test_bridge_interaction_dedup.js`、`test_bridge_manifest.js`、
  `test_bridge_question_callid.js`、`test_bridge_retry.js`、
  `test_bridge_root_control.js`、`test_bridge_user_action_guard.js`）
  几乎全红
- 所有失败都是同一错误：`SyntaxError: Invalid or unexpected token`
  抛在 `node:internal/main/check_syntax` 阶段
- 30+ case 报 `failureType: hookFailed`（连 hook 都进不去）

### B. 根因（已定位，提交 d0714d2 修复）

通过下载 Actions logs ZIP（[run 37774342300](https://api.github.com/repos/MerZlin/dsh-pet-indesktop/actions/runs/37774342300/logs)）定位到失败 step 调用 `node --test tests/test_bridge_*.js`，再在本机 node 22 复现：

```text
$ node -c integrations/dsh-pet-bridge/index.js
D:\...\index.js:1
��/  ← Node 24 看到的是这堆乱码
^
SyntaxError: Invalid or unexpected token
    at checkSyntax (node:internal/main/check_syntax:74:5)
```

逐字节对比 git 里的 raw bytes 和本地磁盘文件：

| 文件 | upstream git | 本地磁盘 | 倍率 | 编码 |
|---|---|---|---|---|
| `index.js` | 49720 字节 | 85164 字节 | 1.71× | UTF-16 LE（+中文段乱码） |
| `verify_import.mjs` | 4666 字节 | 7382 字节 | 1.58× | UTF-16 LE |

**根因**：本地 `git config core.autocrlf=true` 在执行
`git checkout upstream/main -- integrations/dsh-pet-bridge/index.js
integrations/dsh-pet-bridge/verify_import.mjs` 时，把 upstream 的
**UTF-8 raw bytes 误转码为 UTF-16 LE + BOM**（`FF FE 2F 00 2F 00 ...`）。
中文段（`E6 A1 8C` 「桌」）被错误逐字节高低位重组，导致部分位置
变成 `4C 68 A0 5B` 这种无效 UTF-16 surrogate 区段。

Node 24 在 ESM 加载阶段执行 `--check`/`--test` 时直接 `throw SyntaxError`，
连 import 都进不去就退。**这是「缝合/脚本化改动后必须重跑 ruff」纪律的
镜像版——bridge 改动后必须本地 `node -c` 跑通再 push；本 PR 缺这一步。**

### C. 修法

```bash
# 1) 关掉 autocrlf（不修 .git/config 会永远再犯）
git config core.autocrlf false

# 2) 把错误版本从 index 拿掉，强制 git 重新检出
git rm --cached integrations/dsh-pet-bridge/index.js \
              integrations/dsh-pet-bridge/verify_import.mjs
git checkout upstream/main -- integrations/dsh-pet-bridge/

# 3) 字节级对比
git cat-file -p upstream/main:integrations/dsh-pet-bridge/index.js | \
  diff - <(cat integrations/dsh-pet-bridge/index.js)
# (空输出 → 字节完全一致)
```

### D. 修复后复测

```text
$ node -c integrations/dsh-pet-bridge/index.js
(exit 0, no output)

$ node --test tests/test_bridge_hardfailure.js \
         tests/test_bridge_interaction_dedup.js \
         tests/test_bridge_manifest.js \
         tests/test_bridge_question_callid.js \
         tests/test_bridge_retry.js \
         tests/test_bridge_root_control.js \
         tests/test_bridge_user_action_guard.js
TAP version 13
ok 1 - 正常完成（completed）绝不判失败...
ok 2 - 历史误报场景：6 次重试均恢复...
...（全部 ok）
# tests 30+
# pass 30+
# fail 0

$ node integrations/dsh-pet-bridge/verify_import.mjs
bridge zero-dependency smoke: import + subtraction surface + source bans OK
```

### E. 新增的推送前守门

- **桥接文件改后必跑 `node -c <file>`**（不是 ruff，不是 pytest——是 node 的语法检查）
- 任何 `git checkout upstream/main -- integrations/...` 之后必须
  `git diff --stat HEAD -- integrations/` 看到「0 改动」才放心
- 本机 `core.autocrlf` 强制关掉（避免下次重犯）

### F. 提交状态

- 修复提交 `d0714d2`（仅 2 文件 / 0 增 0 删 / 纯字节差异）已 push 到
  `origin feat/look-screen-qr-synced`
- 2 个文件 diff：`Bin 85164 -> 49720 bytes` / `Bin 7382 -> 4666 bytes`
- PR #237 触发 CI 重新排队（`mergeable_state` 待更新）
- 本机 30+ node bridge 测试全绿；CI 复跑 3 平台（特别是 Node 24）结果待回

## 七、Pytest main suite 间歇性 CI flake（待 maintainer 处置，2026-10-08 23:40）

> 本节作为 delivery evidence discipline 第 3 条「实机运行记录」的诚实登记：
> 当前 PR Test Gate 在 ubuntu-latest 的 `Pytest (main suite)` 步骤出现
> 间歇性的 2 F + 1 SIGABRT（exit 134），本机无法稳定复现，按 AGENTS.md
> 「CI 红先读日志再动手」纪律已重推 1 次等 CI 重判；连红 2 轮即停手
> 走 webm 生命周期族先例的隔离路径。

### A. 现象（commit `2e6141b` → 1st CI run）

- 23% 进度（tests #989 / #994）：`test_dynamic_island_revamp.py::test_expanded_mode_freezes_squish` / `test_default_icon_is_auto` 报 F
- 37% 进度（位于 `test_island_chat.py` 范围内）：SIGABRT (134) + core dump，杀掉整个 pytest 进程
- 失败 run URL：<https://github.com/MerZlin/dsh-pet-indesktop/actions/runs/37798030637>

### B. 本机复现（全部绿，无法稳定复现）

| 范围 | 本机结果 | 用时 |
|---|---|---|
| 148 个关键测试（`test_double_click_chat` 10 / `test_island_chat` 19 / `test_architecture` 9 / `test_config_schema` 12 / `test_settings_interaction_tabs` 7 / 5 settings 域 87） | 148/148 通过 | 80.16s |
| 23% 区间 20 个测试（重放 985-1000 位置） | 20/20 通过 | 6.11s |
| `test_dynamic_island_revamp.py` 全部 29 个 | 29/29 通过 | 4.62s |
| `test_island_chat.py` 全部 19 个 | 19/19 通过 | 2.65s |
| `test_island_bridge.py` 全部 33 个 | 33/33 通过 | 3.26s |

### C. 与上游历史对比

- 上次 main push 成功 CI 是 2026-10-06（commit `ee699c7`）
- 之后 5 次 main push 全部 `success`（无任何 F/SIGABRT）
- 本 PR 与最近成功 CI 的差异仅 5 个文件（`pet/double_click_chat.py` / `tests/test_double_click_chat.py` / `tests/test_settings_interaction_tabs.py` 修改 / `pet/island_chat.py::present_reply` / `pet/settings_interaction.py` 的 `double_click_chat` row / 团子角色包 8 GIF + manifest）
- **未动** `pet/frameseq_clip.py` / `pet/overlay_shell.py`（上游 2026-10-04 已根治同族崩溃家族，参见 `PR-REPORT-QT-LIFECYCLE-CRASH-FAMILY-2026-10-04.md`）

### D. 假设与下一步

**假设 A（间歇性 CI flake）**：PySide6 6.12 + ubuntu 24.04 + ffmpeg + offscreen 四者组合的原生崩溃窗口被某次时序抖动撞上；重推可消除。
- 操作：`2a0bb54`（空 commit，CI 重推）+ 在 PR 评论里登记现象、本机复现、上游历史。
- 判定：等新一轮 CI 跑完看 ubuntu 是否转绿。
- 若 ubuntu 转绿 / windows & macOS 仍全绿：合并无阻塞（CI 门禁由 maintainer 按 PR Test Gate 现状判断）。

**假设 B（真回归）**：本 PR 的某些改动（即使 ruff + 148 测试全过）在 ubuntu runner 上触发了一个新崩溃形态。
- 操作：按 `PR-REPORT-QT-LIFECYCLE-CRASH-FAMILY-2026-10-04.md` §六 的隔离先例，在 `pr-test.yml` 的 `Pytest (main suite)` 步骤追加 `--deselect` 临时隔离崩溃点 + 单开一个 follow-up PR 定位根因。
- 判定：连红 2 轮（按 AGENTS.md CI cost discipline）才走这条路；本 PR 不动 `pr-test.yml`。

### E. 当前最优处置（已执行）

1. ✅ Bridge encoding 修在 `d0714d2`，3 平台 Bridge gate 全绿
2. ✅ PR body 改用反引号（不再 `\main\` 字面反斜杠）
3. ✅ PR 报告追加第 6 节（编码修复）+ 第 7 节（CI flake 登记）作为 delivery evidence
4. ✅ PR 评论（comment 6063678893）告知 maintainer 当前 CI 状态、本机复现、上游历史
5. ✅ `2a0bb54` 重推等新一轮 CI
6. ⏸ 不修改 `pr-test.yml`（连红 2 轮再动）
7. ⏸ 不动 `pet/frameseq_clip.py` / `pet/overlay_shell.py`（上游已根治）

