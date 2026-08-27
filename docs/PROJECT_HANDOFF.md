# 项目交接文档 — dsh-pet-pr（主动识屏 + 多 Agent 联动）

> 文档用途：把当前工作区状态、已完成功能、关键设计决策、遗留 TODO 与交接要点完整固化，供 Kimi 终审、以及后续开发/维护接手。
> 交接对象：Kimi 终审 / 后续维护者。

---

## 0. 当前基线快照

- 工作目录：`D:\dsh-pet-pr`
- 当前分支：`perf/startup-and-hidden-cpu`（基于 `origin/main` v3.1.1，已含 5 个提交）
- 测试基线：
  ```powershell
  $env:QT_QPA_PLATFORM='offscreen'
  .\.venv\Scripts\python.exe -m pytest -q
  # 当前结果：194 passed / 4 skipped（K3 两轮终审后，全量稳定）
  ```
- `python -m compileall -q pet tests`：通过
- **工作区尚未 commit / push**。所有主动识屏相关改动（`pet/proactive.py`、`pet/config.py`、`pet/settings_dialog.py`、`pet/vision.py`、`pet/window.py`、`tests/test_proactive.py`、`tests/test_vision.py`）目前是未提交状态，均在当前分支工作区。

---

## 1. 项目血缘与定位

- 上游：`MerZlin/dsh-pet-indesktop`（PySide6 独立桌面宠物）
- fork：`klxxya/dsh-pet-indesktop`
- 已合并 PR #5（拖拽物理 / 全屏隐藏 / 看看屏幕 / 主题背景 / 裁切取景 / 文字免镜像 / 置顶看门狗等）
- 我们在此之上做「主动识屏 + 多 Agent 联动」长期功能。

---

## 2. 已完成功能主线

### 2.1 历史性能与稳定性优化（已并入 perf 分支的前 5 个提交）
1. `perf: cut idle CPU with lazy clips, pause-on-hide, multi-instance isolation`
   - 隐藏暂停解码、素材懒加载、分级预热、多开配置隔离
2. `fix: keep fullscreen watcher alive during auto-hide`
   - 自动隐藏期间不能停 `_fullscreen_timer`，否则退出全屏无法恢复
3. `feat: context-menu toggle for fullscreen auto-hide (win32)`
4. `fix(assets): add 深度思考碎碎念 to no_mirror`
5. `feat: custom character display names; collision-aware multi-instance positioning`
   - 角色显示名、`--instance` 启动避让

### 2.2 主动识屏（Phase 1 → Phase 5 当前首项）
一句话：桌宠按用户配置的白名单，在特定条件下主动截取前台窗口区域、调用视觉模型、以气泡方式关怀；并拥有短期陪伴记忆，能记住用户上次在干嘛，切换活动时自然吐槽/关心。

#### 触发漏斗（最终版）
```
8s 心跳（仅 enabled + 白名单非空 + 可见时存在；隐藏时随 _pause_activity 暂停）
 ├─ G1 桌宠守卫：可见；鼠标穿透默认放行（可关）
 ├─ G2 白名单：进程名/标题 fnmatch（大小写不敏感，支持 title:）
 ├─ G3 停留 ≥ dwell_seconds
 ├─ G4 闲置：require_idle=true 时才判定（默认关）
 ├─ G5 后台线程抓窗口区域 → 9×8 dHash
 │     └─ 与上次触发哈希差异 < change_threshold → 丢弃
 ├─ G6 频控：冷却 / 每日上限 / 60s 最小请求间隔 / 连续3次失败熔断
 └─ 通过 → 真实模式：
       ├─ 先兆气泡（pre_cue，可选）
       ├─ 内存 JPEG(bytes) → _post_vision_request
       ├─ 成功 → record_success + 写入短期记忆
       └─ 失败 → record_failure
      dry_run 模式：只 logging，不调模型、不写记忆、不消耗真实额度
```

#### 短期陪伴记忆（Phase 5 首项）
- 文件：`<config.dir>/proactive_screen_memory.json`
- 条目：`{ts, process, activity}`（**不落窗口标题**，gpt-5.6-sol 终审修订），最多 20 条，头部最新，`.tmp` 原子替换，损坏回退空
- `classify_activity(process, title)`：本地关键词分类，零网络/零模型
- `build_memory_context(last, current)`：活动不同时生成“上次看到你在X，这次看到你在Y。”
- 注入：`_post_vision_request(..., memory_context="")` 追加到用户提示词
- 隐私：**只存元数据，绝不存截图**；不写 AI 会话；手动「看看屏幕」不受影响

#### 关键配置（config.json 新增）
```jsonc
"proactive_screen": {
  "enabled": false,
  "dry_run": false,
  "preset": "balanced",        // quiet / balanced / active / custom
  "allow_when_mouse_through": true,
  "whitelist": [],
  "dwell_seconds": 45,
  "require_idle": false,
  "min_idle_seconds": 30,
  "cooldown_minutes": 5,
  "daily_cap": 15,
  "min_request_interval_seconds": 60,
  "change_threshold": 8,
  "prefer_free_provider": true,
  "pre_cue": true
}
```

### 2.3 多 Agent 联动（当前为框架）
- 配置块 `agent_link` 已定义；
- 右键二级菜单「Agent 联动」已实现四个开关，双向同步配置；
- **监视器本体未实现**，代码中标 `# TODO Phase 5`；
- 原则：默认全关；勾选才启动；绝不安装/修改 hooks 未征得用户同意。

---

## 3. 模块与文件地图（当前）

| 文件 | 作用 |
|---|---|
| `pet/proactive.py` | 主动识屏核心：纯函数、频控 `ProactiveLimiter`、短期记忆 `ProactiveMemory`、`ProactiveScreenWatcher` |
| `pet/vision.py` | 新增 `foreground_window_info()` / `get_foreground_window_rect()` / `get_system_idle_seconds()` / `capture_window_rect()`；`_post_vision_request()`；`ask_about_screen()` 变薄封装 |
| `pet/config.py` | 新增 `proactive_screen` / `agent_link` 默认块与深合并 |
| `pet/window.py` | `PetWindow` 集成 watcher（子成员）、右键二级菜单、`_build_context_menu()` 解耦、开关方法 |
| `pet/settings_dialog.py` | 主动识屏设置组、白名单一键添加、清除陪伴记忆按钮 |
| `tests/test_proactive.py` | Phase 1~5 全部专项测试（当前 20+ 项） |
| `tests/test_vision.py` | 更新 `_post_vision_request` 源码检查用例 |
| `docs/PROACTIVE_SCREEN_PLAN.md` | 主动识屏原始方案 |
| `docs/PROACTIVE_SCREEN_IMPLEMENTATION_MANUAL.md` | 实施手册（含 §12 终审修订、§6 dry-run） |
| `docs/OPTIMIZATION_CHECKLIST.md` | 早期性能优化复核清单 |

---

## 4. 关键设计决策与红线（不可违反）

1. **主动识屏 v1 仅 Windows**（GetForegroundWindow / GetLastInputInfo / DWM）；非 Windows 不显示入口。
2. **截图不落盘**：主动识屏只用内存 JPEG bytes；严禁写临时文件。
3. **短期记忆只存元数据**：ts/process/title/activity，绝不存截图。
4. **功能全部默认关闭**：主动识屏、dry-run、Agent 联动均默认关。
5. **多 Agent 感知不用 mtime 轮询**：用官方 hooks + byte-offset tail / DSH 插件事件（Phase 5 落地）。
6. **主线程不做 ImageGrab**；抓图/模型请求都在后台 daemon 线程，信号回主线程。
7. **watcher 必须是 `PetWindow` 子对象**，不得做成 app 单例（角色热切换会双开）。
8. **不引入新第三方依赖**；只用标准库 + 既有 Pillow/PySide6。
9. **不改变现有「看看屏幕」行为**；`ask_about_screen` 签名不动，`_post_vision_request` 是新增内部函数。
10. **日志模式/dry-run 不消耗真实每日额度**，不写真实记忆。

---

## 5. 遗留 TODO（Phase 5 后续）

- [ ] 各 Agent 监视器真实落地：
  - Claude Code：官方 hooks + 事件文件 byte-offset tail
  - Cursor：`~/.cursor/projects/**/agent-transcripts/*.jsonl` tail（Path.home() 拼接）
  - DSH：DSH 插件事件（需先读 DSH `docs/event-producer-consumer.md` 核实现版本事件名）
- [ ] 可选项：本地 VLM provider（Ollama / LM Studio）
- [ ] 可选项：TTS 语音播报
- [ ] 测试覆盖小缺口（非阻塞）：`test_watcher_real_mode_memory_injection` 是手动传 memory_ctx，未覆盖 `_on_frame_ready` 自动生成记忆上下文的完整链路；建议后续补一条 `_on_frame_ready` → 记忆上下文自动注入的单测。

---

## 6. 给 Kimi 终审的检查点

1. `window.py` 右键菜单 import 是否已修：`effective_proactive_config` + `Any`。
2. `_build_context_menu()` 是否可测；`test_context_menu_proactive_build_no_name_error` 是否真实覆盖。
3. `_post_vision_request` 重构是否保持 `ask_about_screen` 行为不变；`test_vision.py` 源码检查是否指向新函数。
4. dry_run 是否确实：只打日志、不更新真实额度、不写真实记忆、真实状态文件不被创建。
5. 短期记忆：仅元数据、20 条上限、原子替换、损坏回退、清除按钮可用。
6. 线程模型：watcher 为 PetWindow 子对象；后台线程只做抓图/模型请求；Signal 回主线程。
7. 低功耗：功能关闭时零定时器/零截图/零网络；隐藏时暂停。
8. 无新增第三方依赖；无 commit/push。

---

## 7. 常用命令

```powershell
# 全量测试
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q

# 只跑主动识屏专项
.\.venv\Scripts\python.exe -m pytest tests\test_proactive.py -q

# 编译检查
.\.venv\Scripts\python.exe -m compileall -q pet tests

# 源码启动（Windows）
python -m pet

# 多开第二个实例
python -m pet --instance pet2
```

---

## 8. 交接注意事项

- 当前所有主动识屏改动**未 commit**，交接时需在 `perf/startup-and-hidden-cpu` 工作区继续；
- **不要提交 / 推送**，除非用户明确授权；
- 若 Kimi 终审通过，建议先在本地用真实桌面验证一轮再考虑提交；
- 验证重点：右键菜单不崩；主动识屏开启后白名单为空不启动；dry-run 日志模式不消耗额度；短期记忆能记住上次活动并在切换时注入上下文。

---

## 9. Kimi K3 终审记录（2026-08-27，已完成实机验证）

终审方式：逐行审查全部新增/改动代码 → 派 dsh 修复 UX 暗坑 → 真机源码实例（`--instance verify`）+ dry-run 端到端验证。

### 终审修复（6 项，全部有回归测试）
1. **【实机抓到的真 bug】`vision.foreground_window_info()` 永远返回 None**：函数体中间的
   `import ctypes.wintypes` 使 `ctypes` 成为局部变量，函数开头的 `ctypes.windll` 直接
   UnboundLocalError 并被 except 吞掉。mock 全覆盖的测试完全测不出来，实机才暴露。
   修复：移到模块顶部导入；新增真实调用回归测试（仅 Windows）。
2. **【实机抓到的真 bug】Qt Signal 32 位溢出**：`frame_ready = Signal(..., int, int, ...)`，
   64 位 dHash 触发 libshiboken Overflow，投递行为未定义。修复：hwnd/dhash 改用 object 传参；
   新增 64 位大数信号传递回归测试。
3. **【实机抓到的真 bug】watcher 首次启动永不工作**：`apply_config` 以 `isVisible()` 为启动条件，
   但构造时窗口未显示，且 `showEvent` 只在曾经隐藏过才 resume → 定时器永远不起。
   修复：启动条件只看平台+enabled+白名单，可见性由 `_on_tick` 的 G1 逐 tick 判定；
   新增"构造期未显示也应启动"回归测试。
4. **设置页"从当前前台窗口添加"暗坑**：点击时前台是设置窗口自己。修复：点击后禁用按钮并提示
   "3 秒内切换到目标窗口"，延迟采样。
5. **设置保存丢字段暗坑**：`_save` 重建 dict 会冲掉未暴露字段（min_request_interval_seconds 等）。
   修复：在现有配置基础上 update。
6. **UX 反馈补齐**：开启主动识屏但白名单为空时气泡明确提示需添加白名单；
   勾选 Agent 联动时气泡告知"后续版本实装"（当前为框架）；`apply_config` 增加 win32 平台守卫。

### 实机验证结果（源码实例 + dry-run，未消耗任何模型额度）
- 触发漏斗全链路：白名单命中 → 停留 15s → 截图 → dHash → 频控 → dry-run 日志，两次触发间隔 48s ≥ 30s 下限 ✔
- 功耗：开启主动识屏 dry-run 的实例 15.1% CPU vs 打包旧版 14.9%（均为可见动画状态），开销 ≈ 0.2s/60s ✔
- 运行日志无任何 error/warning/traceback ✔
- 全量测试 **175 passed / 4 skipped**（连续 3 次全量运行稳定；含 flaky 竞态测试的同步化修复）✔
- `compileall` 通过；diff 无密钥；无新增第三方依赖；未 commit/push ✔

### 已知非阻塞事项
- ~~Agent 联动监视器本体未实现~~ → 已实现并经 K3 终审 + 实机验证（见 §10）；
- DSH / OpenCode 的**事件写入插件包**仍未实现（桌宠侧监视器已完成并验证，
  需 DSH/opencode 插件把事件写入 `agent-events/<agent>.jsonl`，可作为下一子任务）；
- `docs/PROACTIVE_SCREEN_PLAN.md` 为原始方案稿，与最终实现的差异以实施手册 §12 与本节为准。

---

## 10. Agent 联动终审记录（2026-08-27，Kimi K3 实机验证）

终审输入：dsh 对 gemini 实现的审查报告（6 项问题，经逐条核实**全部属实**）。

### 修复清单（每项均有回归测试）
1. **Claude hooks 格式错误**：gemini 把 hooks 写成字符串，Claude Code 官方规范是
   数组对象（`[{"matcher":"", "hooks":[{"type":"command","command":...}]}]`）。已重写，
   幂等安装、以 `claude_event_hook` 标记识别自己的条目、**用户已有 hooks 原样保留**。
2. **打包版 hooks 命令必挂**：原实现用 `sys.executable -c`，PyInstaller 打包后是桌宠 exe。
   修复：Windows 落地 `claude_event_hook.ps1`（纯 PowerShell，零 Python 依赖），
   非 Windows 落地 `.py` 脚本，frozen 时退化为 `python3`。
3. **授权拒绝菜单不回弹**：`_toggle_agent_link` 现在带 action 引用，失败时
   `blockSignals + setChecked` 回滚勾选态。
4. **状态无去抖**：AgentLinkManager 增加同状态去抖 + 每 Agent 2s 最小切换间隔
   （构造可注入 `min_interval`/`clock`，测试用 0 关闭）。
5. **ByteOffsetTailer 半行缓冲**：未完成行存入 `_partial` 下次拼接，带 64KB 防呆上限。
6. **install_hooks 失败被忽略**：现在返回 False → 弹警告框 → 不开启。
7. **K3 实机额外抓到**：`_ensure_hook_script` 未 mkdir 导致 install 必失败
   （→ 触发未 mock 的 warning 弹窗，offscreen 下全量测试卡死）；hooks 脚本目录现在自动创建。
8. **K3 实机额外抓到**：PowerShell `Add-Content -Encoding UTF8` 在文件首行写 BOM，
   第一条事件会解析失败被丢——tailer 解码改 `utf-8-sig` 兼容。
9. **未知事件过度触发**：`normalize_event_state` 未知事件从默认 working 改为忽略（返回空串）。
10. **关闭 Claude 联动时自动卸载 hooks**（`uninstall_hooks`，只删带标记条目）。

### 测试污染事故（已清理）
gemini 的 `test_window_menu_proactive_and_agent_toggles` 只 mock 了确认框、没 mock
`install_hooks`，跑测试时把**旧版错误格式的字符串 hooks 写进了用户真实的
`~/.claude/settings.json`**。已清理（用户自有的 PreCompact/SessionEnd/SubagentStart
三条 list 格式 hooks 原样保留，备份在 `settings.json.bak-dshpet-cleanup`），
测试已补上 install_hooks mock 防止再犯。

### 实机验证（源码实例 --instance verify，全部通过）
- DSH 链路：手动注入 `dsh.jsonl` → error 气泡弹出（截图实证）✔
- Claude 链路（沙盘 USERPROFILE）：install → 数组格式正确 → ps1 用 Claude Code
  同款方式真实执行 → jsonl 写入 → tailer 正确解析（含 BOM）✔
- 功耗：3 个监视器全开 16.8% CPU vs 动画基线 ~15%（1.5s 有界 tail 开销 ≈ 1-2%）✔
- 日志零 error/warning ✔
- 全量测试 **194 passed / 4 skipped** ✔

### 第三轮：UX 修复（2026-08-27 晚，用户实机体验反馈）
1. **冷却间隔粒度**：整分钟太粗 → 设置页改 0.5 分钟步进（0.5~120），`effective_proactive_config`
   的 clamp 同步放宽为浮点；自定义档新增暴露「最小请求间隔（秒）」。
2. **白名单添加 UX**：「从当前前台窗口添加」不再把进程名+截断标题两行都塞进去，
   改为弹窗明示「按软件（推荐）/ 按标题关键词」二选一，白名单上方加规则说明文案。
3. **气泡连环顶掉**：新增 `hold_bubble(seconds)` 占用机制——主动识屏「让我看看……」
   到模型答复整个窗口期（30s 上限）占用气泡位，自言自语让路；`show_bubble` 自动占用
   自身时长+2s。有回归测试（`test_self_talk_yields_to_important_bubble`）。
4. 全量测试 197 passed / 4 skipped。

### 第四轮：Agent 联动免插件化 + 实机真跑（2026-08-27 深夜，用户要求"别让别人还得装插件"）
1. **OpenCode 免插件**：改为直读其本地 SQLite 事件库（`opencode.db` 的 `event` 表，
   rowid 增量轮询，只读模式）。实机验证：一次真实 `opencode run` → 桌宠监视器实时捕获
   idle→thinking→working→idle 完整生命周期 ✔
2. **DSH 桥接插件内置 + 一键安装**：`integrations/dsh-pet-bridge/`（零依赖 ESM 插件，
   订阅 `agent/created`/`agent/status`，写固定桥目录 `<base>/dsh-pet-bridge/dsh.jsonl`）。
   关键发现：插件必须在 package.json 声明 `dsh.bundle` + 自带 `cordis.patch.yml`，
   否则 pnpm 装了也不会成为 profile layer（不加载）。开启联动时弹确认框一键安装到
   所有 dsh profile（web/headless），关闭自动卸载。实机验证：真实 `dsh --profile headless`
   任务 → 桥文件产出 idle→working→idle ✔
   格式映射有单测 + 真实样本依据，tail 链路本身已实测。
4. **冷却间隔双单位**：设置页 秒/分钟 下拉切换（30 秒 ~ 120 分钟，内部统一存分钟），
   自定义档另暴露「最小请求间隔（秒）」。
5. 全量测试 202 passed / 4 skipped。

### 第五轮：体验与安全性收尾（2026-08-27 深夜，用户实机体验反馈）
1. **气泡时长自适应**：识屏答复气泡从固定 6s 改为按字数缩放（6s 起步，每字 +150ms，封顶 20s）。
2. **DSH 桥接插件安全性实证**：零依赖、纯订阅事件（`ctx.on`）、无 UI patch、写文件全程
   try/catch 静默——不碰路由/配置/其他插件，冲突面趋近于零；实测 dsh web profile 装插件后
   正常启动（HTTP 200）。安装为本地 link，**零网络下载**（pnpm 日志 `downloaded 0`）。
3. **硬编码路径审查**：全部新增代码无写死路径（一律 Path.home()/APPDATA/_MEIPASS 推导），
   开源分发安全。
4. 全量测试 202 passed / 4 skipped；已重打包重部署。

### 第六轮：gpt-5.6-sol 独立终审 + 修复（2026-08-27，opencode 执行评审）
评审输出按严重度分级，K3 逐条分诊后修复：

**严重（全部修复）**
1. 截图 TOCTOU：派发后前台窗口可能已切换 → worker 抓图前重新核对 hwnd，不一致丢弃。
2. 关闭/隐藏不作废在飞任务 → 代次令牌（generation），pause/关闭后迟到帧一律丢弃。
3. 请求未结束可再派新 pipeline → `_request_in_flight` 互斥；空回复改计失败。
4. 无 Chat 变体主动识屏必炸 → 设置页/菜单/ watcher 均按 `enable_chat`/`on_open_chat` 门控。

**中等（修复 12 项）**：记忆不落窗口标题；Claude settings.json 原子写；tailer 超长行
apply_config 用 `_running` 修暂停态误判；vision.py Win32 全签名声明（HWND/HANDLE 不截断）；
无前缀白名单规则改为仅进程名；dHash 基线哨兵 0→None；设置页延迟采样改对话框自有
QTimer（销毁即取消）；DSH 多 profile 安装失败自动回滚；构建脚本补 integrations add-data。

**建议（采纳 2 项）**：桥接插件加 disposer 生命周期 + 事件文件 1MB 轮转。
未采纳：多实例共享状态（有意设计）、双份默认配置（无实际漂移风险）、异常日志内容
（`_safe_detail` 已截断清洗）。

测试：206 passed / 4 skipped；DSH 桥接插件重写后实机复验通过（idle→working→idle）。

### 第七轮：gpt-5.6-sol 全项目交付终审（2026-08-27，opencode 执行）
**修复（本次新增类）**
1. Claude settings.json 解析失败时回退空字典会**覆盖清空用户配置** → 改为中止安装。
2. 截图前后双重前台 hwnd 复核（TOCTOU 二次防护）。
3. worker 截图成功后 emit/dHash 抛异常会永久卡死 `_worker_busy` → 改 emitted 标记释放。
4. 状态/缓存临时文件名带 PID（多实例互抢 + 共享 /tmp 符号链接预占攻击）。
5. 余额缓存绑定 provider（base_url），多实例不同账号不串余额。
6. DSH 卸载返回成功状态并记日志；**其他实例仍开启时不卸载全局 hooks/插件**（扫描兄弟配置）。
7. cmd /c 调 dsh 时带空格参数自行加引号（安装路径含空格不再炸）。
8. 桥接插件：轮转前 rm 旧备份（Windows rename 不覆盖）；disposer 改挂 agent.ctx 生命周期。

**上游既有（本 PR 顺带修 1 项）**：角色包无随机动作素材时 `random.choice([])` 崩溃 → 兜底待机。
**明确不修（有理由）**：视觉独立端点留空复用聊天 key（上游 PR 既有设计，UI 已明示）；
多实例频控的 lost-update 容忍（手册 §4.3 已声明"最多丢一次计数"）；GUI 线程 1.5s 有界
tail（实测开销可忽略，目录发现已降频 30s）；Linux 自启路径引用（上游既有，留给上游 issue）。

测试：208 passed / 4 skipped。

### 第八轮：同步上游现代版大改版（2026-08-28 凌晨，PR #11 合并适配）
上游深夜合并了 1.3 万行的现代化重写（新菜单模板系统、现代设置面板、macOS 层级方案、
肥鱼彩蛋等）。合并冲突 4 文件全部手解，要点：
- **我们的功能全部移植到新结构**：菜单组挂入 context_menus（modern+legacy 两套），
  主动识屏设置页移植进 ModernSettingsDialog 侧边栏（新增「主动识屏」页）；
- **隐藏暂停/全屏自动隐藏**：上游重写把实现误删了（配置和 UI 还在），我们重新接回
  新的 hide()/showEvent 结构——这也算帮上游修了个回归；
- 置顶看门狗随上游原生层级方案退役（他们改对了，我们不再重复造）；
- 应上游品牌政策移除了某 CLI 工具的联动（上游有测试禁止仓库出现该名字）；
- 合并后测试 318 passed / 5 skipped / 1 failed（test_menu_font_select 为上游
  offscreen 环境既有问题，纯上游 main 同样失败）；
- 冒烟：合并版源码与打包版均正常启动渲染。
- 朋友分享包已换合并版（新文件名日期 20260828）。

### 仍未做（明确交接）
- ~~DSH / OpenCode 插件包（事件生产者侧）~~ → 第四轮已解决（见上：DSH 内置桥接插件
  一键安装、OpenCode 原生 SQLite 直读）；

### 打包（frozen）验证（2026-08-27 收尾）
- 按 WORKSPACE.md 命令完整 PyInstaller 打包（webm-chat 变体），产物在
  `dist-onedir/dsh-pet-standalone-webm-chat/`；
- 打包版以 `--instance verify` 真实启动：Agent 监视器正常启动、注入事件后气泡真实弹出
  （截图实证），证明 frozen 环境下联动链路完整可用（hooks 已改纯 PowerShell 脚本，
  不依赖 sys.executable，打包版无此隐患）；
- 用户本人也实际打开了打包版右键菜单：二级菜单结构、勾选状态渲染正常；
- 验证残留（config-verify.json / agent-events / 截图 / 验证进程）已全部清理，
  用户日常打包版（PID 12368）全程未受影响。
