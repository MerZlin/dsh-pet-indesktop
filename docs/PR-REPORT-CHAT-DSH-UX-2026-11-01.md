# 聊天/DSH 视图、审批投递与网易云识别 — 2026-11-01

> 用户报障批次 4。7 条现场问题全部先取证再改；核心结论是「审批卡不是没弹，是
> 桌宠在 AI 视图时根本没订阅」以及「DSH 文本被裁是 QLabel 最小宽把时间线撑宽」。

## 一、核心特性

1. **审批不再丢**：待审批提升为 `DshHub` 级状态，聊天窗存在且 `chat_dsh_mode` 打开时
   常驻订阅 SSE；审批到达冒泡 + 自动切到 DSH 视图；历史回放后从 Hub 补画卡片。
2. **DSH 视图重排**：行宽只由 viewport 决定（长哈希/长路径不再把时间线撑到 1186px），
   工具过程与思考默认折叠，assistant 走 Markdown。
3. **窗口**：右下角圆角修复（透明子面 + 壳 mask）；新增最大化按钮与真最大化行为
   （去外环/去圆角、跟随桌宠与边拖不再取消最大化）。
4. **侧栏**：`chat_dsh_mode` 关闭时隐藏 DSH 分组；日期组头与渠道组头字体分层；
   切视图同步切会话通道（关掉 DSH 后输入框不再发给 DSH）。
5. **切换不卡**：`DshController.stop()` 移出 GUI 线程（实测 0.0ms，旧实现等满 1.0s）。
6. **审批续跑不重复弹卡**：同一条命令 + 非危险装饰直接放行（真机模型「这是第四次」的现场）。
7. **网易云**：识别诊断有界（8s 预算 + 15s 降级窗）、SMTC 可重置、app_id 取 basename、
   网易云标题按播放器格式切分、空闲标题不判曲目。

## 二、修改文件说明

### 实现

| 文件 | 改了什么 | 为什么 | +/− |
|---|---|---|---|
| `petcore/ui/chat/dsh_controller.py` | `DshHub` 增 `pending_approvals/clear_pending/approval_pending`、`acquire_persistent/release_persistent`；`stop()` 改后台关流 | 审批是 Hub 级事件，视图可见性不该决定订阅；GUI 线程关 SSE 会被 socket 读挡住 | +170/−25 |
| `petcore/ui/chat/dsh_view.py` | `_WrapLabel`（最小宽 0）、`_Collapsible`（工具卡/思考行）、Markdown 正文、视口 resize 重排、审批卡回放 | 1186px 最小宽 + 关闭横向滚动 = 每行裁 450px；用户要求 DSH 同款折叠 | +293/−49 |
| `petcore/ui/chat/window.py` | 圆角 mask、最大化按钮/`changeEvent`、`view_changed`、DSH 分组过滤、组头字体 | 方白子控件盖住四角；最大化只是「撑到工作区」；视图与会话脱节 | +246/−46 |
| `petcore/ui/chat/controller.py` | `_on_view_changed`、`_on_dsh_approval`、`_channel_session`、窗口构造传 `dsh_enabled` | 关掉 DSH 仍往 DSH 发消息；审批到达没人通知 | 本批约 +120 |
| `petcore/ui/chat/styles.py` | 栈页/DSH 表面透明、`#window-maximize-button`、`#dsh-allow/#dsh-deny/#dsh-error/#dsh-thinking` | 圆角根因；审批按钮实测白字白底 | +24/−7 |
| `petcore/ui/chat/icons.py` | `maximize`/`restore` 字形 | 新按钮 | +5/−0 |
| `petcore/config.py` | `chat_dsh_mode` 三处登记（默认 False） | 侧栏 DSH 分组开关 | 本批 +3 |
| `integrations/dsh-pet-bridge/channel.js` | assistant 帧带 `reasoning`；`sameCommandFamily()` + grant 保留 | 思考行数据；续跑重复弹卡 | 本批约 +75/−45 |
| `examples/.../media.music/host/sampling.py` | `_SourceWorker` 常驻线程 + 8s 预算 + 15s 降级窗；`Sample.audio_error/window_error` | 音频会话 5 处同步 COM 无超时，设置页永久转圈 | +165/−5 |
| `.../settings_page.py` | `_detect` 有界化、`finally` 恢复按钮、每次 `reader.reset()` | 同上 + 「已停止重试」永久粘住 | +78/−20 |
| `.../diagnostics.py` | 超时行、候选清单、app_id basename | 用户贴出的诊断要能定位到哪一层 | +66/−2 |
| `.../now_playing.py` | `normalized_app_id` 取 basename、`SmtcReader.reset()` | 识别范围误杀；诊断不可重试 | +23/−3 |
| `.../title_clean.py` | `cloudmusic.exe` 标题格式 + 空闲名判空 | 网易云标题整条被当歌名 | +23/−7 |

### 测试

- 新增 `tests/test_petcore_chat_dsh_ui.py`（12 条）：长 token 不撑宽、窗口缩放重排、
  工具/思考默认折叠、失败自动展开、右下角 alpha=0（素色 + 壁纸）、最大化铺满、
  跟随桌宠不取消最大化、最大化无缩放边、DSH 分组开关。
- 新增 `tests/test_petcore_music_bounded_diagnostics.py`（9 条）：audio 挂死仍返回、
  超时行、候选清单、app_id 不泄漏路径、降级窗。
- 新增 `tests/test_bridge_reasoning_frames.js`（7 条）、`tests/test_bridge_approval_grant.js`（5 条）。
- 扩充 `tests/test_petcore_dsh.py`（+4）：无视图时挂起审批、历史回放补卡、`stop()` 不阻塞、
  常驻订阅在视图关闭后仍保持连接。
- 扩充 `tests/test_petcore_chat_ui.py`（+4）：DSH 分组默认隐藏、组头字号/字重分层、
  关掉 DSH 回落最新 AI 会话、打开 DSH 选中 DSH 会话。
- 音乐侧扩充 `test_petcore_music_{sources,title,smtc_timeout,mod}.py`（agent 交接，测试先行
  14 红 → 绿）。

## 三、实现要点

- **为什么审批会丢**：`DshView.deactivate()` → `hub.release()` → `_views == 0` → `stop()`。
  用户停在 AI 视图时这条链一直是断的；桥把 `approval-request` 推给 SSE 时没有任何消费者。
  改成「窗口 + 模式」持有连接、视图只算引用计数后，AI 视图也收得到。
- **为什么文本被裁**：`QScrollArea(widgetResizable=True)` 用内容控件的
  `minimumSizeHint` 做下限；`QLabel` 的该值是最长不可断词宽度（实测 849/719px），
  一条 tool-call 的参数把时间线顶到 1186px，而横向滚动条是 `AlwaysOff`。
- **为什么圆角失效**：QSS 的 `border-radius` 不裁剪子控件；`#chat-view-stack` /
  `#chat-ai-view` 是方形白底，正好覆盖壳体的四个角。隐藏 `chat_main` 的子控件后
  同一像素变透明，反证成立。
- **为什么最大化「不铺满」**：`showMaximized()` 只把窗口撑到工作区，但 `phone_shell`
  仍带 7px 外环与 14px 圆角；另外最大化后任何 `move()/setGeometry()` 都会静默取消最大化。
- **为什么续跑会再弹卡**：DSH 同意后由模型重发同一条命令并追加 `; "exit=$LASTEXITCODE"`，
  `argsHash` 变化 ⇒ 通行证永不匹配。按「同一命令 + 非危险装饰」放行，追加危险动词仍重审。

## 四、性能分析

| 路径 | 实测 | 说明 |
|---|---|---|
| DSH 视图排版 | `timeline == viewport == 706`，`hbar.maximum() == 0`（960 窗） | 修前 timeline 1186、hbar 480 |
| 视图切换 | `set_view("ai")` **0.0ms**（fake `close_active` 睡 1.0s） | 修前同步关流会等满 1.0s（真机 30s 超时上限） |
| 审批全链 | 请求 → 卡片 **7.6s**（含模型思考）；点允许 → 续跑完成 **4.0s** | 真实 DSH 44.7.0 |
| 窗口重排 | 960→620 后 `_reflow()` + 折行一次，事件循环内完成 | `setFixedWidth` + `adjustSize`，无网络/磁盘 |
| 圆角 mask | 每次 `resizeEvent` 重建一个圆角 `QRegion`（约百点） | 仅在用户拖边缩放时发生，无定时器 |
| 音乐采样 | median **4.02–5.01ms**、p95 4.16–5.17、冷启动 181–192ms（n=4） | 修前同机 4.8ms；常驻线程 2 条（audio/windows），无新增网络/磁盘 |

- **稳态开销**：DSH 常驻订阅只在 `chat_dsh_mode` 打开且窗口存在时建立，本地 HTTP
  长连接 + 15s keepalive；无轮询定时器（原实现也没有）。
- **新增线程**：音乐 mod +2 条 daemon（每源一条，复用 COM 亲和）；DSH 每次 `stop()`
  起一条短命 daemon 关流。无新增进程。
- **内存**：审批卡上限 20 张、Hub 历史 200 条（未改）；DSH 时间线行数与消息数同阶。

## 五、实机运行记录

环境：Windows 11、`D:\APP\DSH\DeepSeek Harness.exe`（外壳 44.7.0 / 插件 v2.0.0）、
真实 `ChatController + ChatWindow + DshHub + DshClient`（不 mock 桥），网易云 3.1.42。

1. **审批（AI 视图）**：停在 AI 视图发送「用 pwsh 执行：Remove-Item -LiteralPath
   E:/tmp/nonexistent.txt -Force」→ `approval-request` 到达，气泡
   「需要你确认：要跑一下 pwsh（AI 对话 → DSH）」（`force=True, duration_ms=8000`），
   视图自动切到 DSH 并出现卡片；点「允许」→ 4.0s 后恢复，真实执行
   `[stderr] Remove-Item: 找不到路径… [exit code: 1]`，**没有第二张卡**。
2. **思考/工具折叠**：同一会话渲染出 `▸ 已思考 342 字`、`▾ pwsh · 失败`（失败自动展开）、
   `▸ 已思考 1879 字`；正文 Markdown（列表/行内代码）正常。
3. **圆角**：同一窗口 `grab()` 的右下角像素断言（壳内角 alpha=0、中心 alpha=255），
   素色与 `builtin:whale` 两种背景都通过；截图与放大对照已留档。
4. **网易云**：空闲时 `SMTC: cloudmusic.exe MUSIC(1) 非播放`、音频 `inactive` →
   「暂停 · 曲目《Love Song》—方大同」；用媒体键播放后 `SMTC PLAYING / peak=0.65` →
   「在播 · 有音频证据」；候选清单两行都能点名 `cloudmusic.exe`。
5. **构建产物**：`rel-chat-ux-2026-11-01/dsh-pet-core-setup.exe`（301,134,920 B，未签名）；
   打包 onedir 真机启动 10 秒：进程存活、窗口 `dsh-pet-core`、`Responding=True`。

操作痕迹：验证后已暂停网易云并关闭、关闭 DSH（开工前两者都未运行）；设置页数据目录
`.scratch/verify-chat-root` 与截图在 `.scratch/`，不入库。

## 六、测试与验证

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py -q
# 2768 passed, 1 skipped in 234.99s
node --test tests/test_bridge_*.js
# 60 passed
.venv/Scripts/ruff check petcore/ tests/ integrations/ examples/
# All checks passed
```

构建：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 -Clean
-Installer -OutputRoot rel-chat-ux-2026-11-01` → Inno `Successful compile (112.718 sec)`，exit 0。

| 产物 | 体积 | 校验 |
|---|---|---|
| `rel-chat-ux-2026-11-01/dsh-pet-core-setup.exe` | 301,134,920 B | SHA-256 `70ea1230efc90b727af031e918805d852cf1877d2058b8a9e2c2ccc522c58363`（未签名） |
| `rel-chat-ux-2026-11-01/dsh-pet-core/dsh-pet-core.exe` | 15,847,279 B | 真机启动 10s：进程存活 / 窗口 `dsh-pet-core` / `Responding=True` |

打包 `--check --strict`：`strict.problems=[]`、`menu_template=json`、`daily=1200/1200`、
`version=0.3.0`；包内 `_internal/integrations/dsh-pet-bridge/channel.js` 含
`sameCommandFamily` 与 `reasoningOf`（升级路径把新插件带到用户机器上）。

## 七、已知限制与后续

- 审批到达会自动切视图并 `show()` 窗口（10 分钟过期，静默错过等于丢事件）；全屏游戏时
  仍会出现，是否加「全屏时不抢焦点」需要产品决定。
- `grantMinutes`（默认 5）内对「同一条命令 + 非危险装饰」不再弹卡；这是「允许」的语义，
  但比一次性通行证宽松，已用单测钉住「追加危险动词必须重审」。
- 最大化只做几何断言，没有真机截图对照；圆角有像素级断言。
- 音乐侧：未能制造真实 COM 卡死，有界返回用注入 `sleep` 验证；网易云只验证了
  「暂停 + 在播」两条，未验证蓝牙/独占端点。

## 八、风险与回滚

- 回滚方式：`chat_dsh_mode` 置 False 即回到「DSH 分组隐藏 + 视图内订阅」的旧行为；
  审批投递（Hub 级）与视图无关，回滚需还原 `dsh_controller.py` 的三个生命周期方法。
- 风险集中在 `DshHub` 的引用计数（常驻 + 视图）：`shutdown()` 同时清零两者，
  既有 `test_hub_shares_one_stream_between_two_views` 仍绿。
