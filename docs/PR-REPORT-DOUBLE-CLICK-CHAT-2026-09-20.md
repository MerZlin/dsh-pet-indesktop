# PR 报告：双击桌宠打开轻度对话栏（双后端 LLM 对话）

- 日期：2026-09-20
- 分支：`feat/double-click-chat`
- 目标：双击桌宠本体 → 弹出头顶轻量对话气泡；后端优先接入 DSH 会话
  （新建/续用），DSH 不可用时回退桌宠自带 LLM。

## 一、核心特性

| 能力 | 说明 |
|---|---|
| 双击开栏 | 双击桌宠本体打开既有的 `QuickChatBubble`（单行输入 + 气泡内回复 + 超出可视区靠滚动条 + 「查看完整回复」），**续用最近会话**，与右键「快速对话（气泡）」同一实例语义 |
| 双后端 | 有 DSH（装了桥接且在线）→ 新建/续用一条 DSH session；不可用 → 本条消息立即回退桌宠自带 `ChatService`，并进入 120s 冷却 |
| 设置项 | `double_click_chat`（默认开）、`dsh_chat_cwd`（DSH 会话工作目录，留空由 DSH 决定） |
| 可关闭 | 关掉开关后双击完整回落为原有行为（两次单击回应） |

## 二、修改文件清单

**新增**

| 文件 | 意图 |
|---|---|
| `pet/double_click_chat.py` | 双击事件过滤器：路由到 `win.on_open_quick_chat`，并吞掉双击尾随的第二段 `Release` |
| `pet/dsh_chat.py` | DSH 后端客户端：create-or-reuse session + prompt，回复经 tracker 信号回流；传输层可注入 |
| `tests/test_double_click_chat.py` | 8 例：开栏一次、尾随抑制不泄漏、开关关闭/无回调/留白区域回落、右键忽略、安装幂等 |
| `tests/test_dsh_chat_plumbing.py` | 11 例：`chat_prompt` 操作语义（唯一允许空 sessionId）+ 助手回复回流信号 |
| `tests/test_dsh_chat_backend.py` | 7 例：双后端路由、回退与冷却、多步累积、跨会话不误认领、无 tracker 时行为不变 |

**修改**

| 文件 | 改动 |
|---|---|
| `pet/config.py` | 两个新键的默认值 + reload 白名单 + 归一化（布尔走 `_bool_or_default` 防字符串误开；路径去空白截断 512） |
| `pet/settings_pet_controls.py` | 新增开关与目录选择控件（控件本体按既有约定落在此文件） |
| `pet/modern_settings_dialog.py` | 「互动 · 点击反馈」两行 `SettingRow` + `_write_config` 回写 + 域 `claim` 登记 |
| `pet/settings_widgets.py` | `ResourcePathPicker` 新增可选 `dialog_title`（原对话框标题写死「选择图片目录」） |
| `pet/app.py` | 装配双击过滤器；给 `QuickChatBubble` 注入 `DshStateTracker` |
| `pet/quick_chat.py` | `_send` 拆出本地通路 `_send_local`，按 DSH 优先路由；新增 DSH 回调；回复落库抽成 `_persist_assistant_reply` 供两条后端共用 |
| `pet/dsh_control.py` | 新增 `chat_prompt` 操作（唯一允许空 sessionId）+ `cwd` 字段 |
| `pet/dsh_state.py` | 新增 `assistant_message` / `turn_finished` 信号（在 `map_event_to_state` 早退之前处理） |
| `integrations/dsh-pet-bridge/index.js` | 新增 `handleChatPrompt`：走宿主 `ctx.get("sessionController")` 的 `create` → `prompt`，成功回带 sessionId |
| `tests/test_architecture.py` | `MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` 2401 → 2418（带日期 + 理由，见文件内注释） |
| `tests/test_config_schema.py` | 两个新键进 reload 白名单快照 |

## 三、实现要点

### 3.1 为什么双击用事件过滤器，而不是覆写 `mouseDoubleClickEvent`

1. 实测 `PetWindow` 的 MRO 是 `[PetWindow, QWidget, QObject, QPaintDevice, Object,
   WindowFeatureGateMixin, object]`——`QWidget` 排在混入类之前，
   `PetWindow.mouseDoubleClickEvent` 解析到 **QWidget 的实现**，在混入类里写这个
   虚函数会被**静默遮蔽**；
2. `window.py` 行数预算顶满（4616/4616），加处理器必然越线。

过滤器在窗口自身处理之前拿到事件，因此既能开栏、也能吞掉尾随 `Release`。
`window.py` 因此**零改动**。

### 3.2 尾随 Release 必须抑制（实测）

Windows 真实双击消息序是 `Press → Release → DblClick → Release`：

| 发送的事件 | `_on_click` 次数 |
|---|---|
| 仅 `DblClick` | 0（但 `_press_global` 被置位、状态变 `PRESS_CANDIDATE`） |
| `Press → Release → DblClick → Release` | 2 |
| 同位置两次独立单击 | 2 |

若双击处理器只 `accept()` 不设状态，`DblClick` 会被 QWidget 默认实现转成
`mousePressEvent`；而尾随的第二个 `Release` 因 `_press_global` 已清空、位移为 0，
会再落进 `window.py` 的点击分支，多出一次点击回应。过滤器用 `_swallow_release`
闩精确吞掉**紧随双击的那一个** `Release`，并在新的 `Press` 上复位，避免泄漏到
后续交互（`test_trailing_release_is_swallowed_only_once` 锁定）。

### 3.3 DSH 通路

DSH 宿主侧 `SessionController`（`@deepseek-ai/dsh-api-session-controller`）提供
`create({cwd})` → `{sessionId}` 与 `prompt({requestId, sessionId, mode, content})`，
本实现据此在桥接里新增 `chat_prompt` 操作——它与 `interrupt`/`replan` 的关键差别是
**不要求已有存活 agent**（新建会话本来就没有）。

回复不经控制响应返回（一次对话可能数分钟），而是随桥接事件流回流：
`assistant/message`（每 step 一条完整文本，纯工具步无 `text`）+ `turn/end`
（本轮结束锚点），桌宠侧按 sessionId 认领。

`chat_prompt` 是控制队列里**唯一允许空 sessionId** 的操作；其余操作的空 sessionId
语义（`missing-session-id`）保持不变，有回归护栏。

## 四、测试与验证

```powershell
python -m ruff check pet/ tests/
python -m pytest -q
```

- `ruff`：All checks passed
- 新增用例：`test_double_click_chat.py` 8 例、`test_dsh_chat_plumbing.py` 11 例、
  `test_dsh_chat_backend.py` 7 例
- 受影响族：`test_agent_link` + 上述三族 = 214 passed / 1 skipped；
  `test_architecture` + `test_menu_layout` + `test_config_schema` + `test_second_batch`
  + `test_pet_interaction_locks` + `test_click_sound` = 143 passed / 1 skipped
- 桥接插件：`node --check` 通过；`import` 仍全为 `node:*`（零依赖红线）

## 五、已知限制与后续

1. **DSH 回复非逐字流式**：桥接**刻意不转发** `assistant/chunk`（`index.js:859/902`），
   故气泡按 step 整段刷新。要逐字流式需改用 `sessionController.follow()` 的
   assistant-stream 帧。
2. **端到端未验证**：`ctx.get("sessionController")` 在 web profile 是否已注册、
   新建会话是否出现在 GUI 会话列表，均需一次 DSH 重启实测（重启会中断当前 GUI 会话，
   故未擅自执行）。取不到该服务时返回 `session-controller-unavailable` → 自动回退，
   不会坏。
3. **DSH sessionId 目前只存在内存**（随气泡实例），重启桌宠后会新建一条；
   跨重启续用需要把 sessionId 落进桌宠会话记录。
4. **灵动岛对话仍走本地 LLM**：本次只给双击/快速对话路径注入 tracker，未扩大改动面。
5. `modern_settings_dialog.py` 预算再次上调；拆出独立 `*_settings.py` 页仍是待办。

## 六、风险与回滚

| 风险 | 处置 |
|---|---|
| 双击行为变化影响老用户 | `double_click_chat` 开关默认开，关掉即完整回落 |
| 桥接插件改动拖垮 DSH 启动（历史事故类型） | 新增代码只在控制请求到达时执行，加载期只多两个函数定义；`node --check` 通过、零新依赖 |
| DSH 不可用时点击卡顿 | 控制往返在后台线程；不可用即回退并冷却 120s，回复兜底 300s 超时 |
| 回滚 | 单分支改动，`git revert` 即可；两个新配置键对旧版本无害（旧版本读不到会用默认） |
