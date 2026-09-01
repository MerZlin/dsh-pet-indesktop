# dsh-pet-harness-indesktop

一个面向 **DeepSeek Harness（简称 DSH）** 的桌面宠物增强项目。

本项目基于上游 [MerZlin/dsh-pet-indesktop](https://github.com/MerZlin/dsh-pet-indesktop) 的桌宠、动画、聊天和设置能力，重点增强 Agent 联动体验：让桌宠理解 DSH 的工作过程，在后台持续陪伴、提醒异常，并通过可配置的语言风格反馈状态。

## 为什么做这个项目

原来的 Pet 交互能力偏弱，挂着时用户不太知道它在做什么，使用时间久了也容易失去吸引力。因此本项目围绕 DSH Agent 联动进行了增强：补充事件桥接、事件队列、session 级监听、探索循环检测和更完整的桌宠反馈。

## 相对上游的主要增量

本项目不是简单增加几个气泡事件，而是在保留上游基础状态事件的前提下，补充了一层统一的 Agent 事件驱动层：

```text
DSH / Agent 原始事件
        ↓
Bridge 事件规范化与关联
        ↓
session 级事件队列与生命周期
        ↓
Pet 状态、动画、台词、Watchdog 和控制
```

因此更准确地说，这是对原有事件能力的底层扩展和重构，而不是完全替换上游事件系统。上游的工作状态、完成状态和基础联动仍然保留；本项目新增的是事件的统一关联、排队、优先级、session 隔离、行为分析和控制闭环。

### Bridge 重构与事件队列

`integrations/dsh-pet-bridge/` 将 DSH 中的 Agent 状态、session 事件、工具调用、工具结果、审批、问题和控制结果统一转换为桌宠可消费的事件流。

- 按 DSH 进程写入 `dsh-<pid>.jsonl` 事件日志。
- 支持 `sessionId`、`step`、`callId` 等关联字段。
- 区分状态、工具、审批/问题和控制事件。
- Pet 端按优先级处理多会话事件，审批和用户问题不会被普通状态提示遮挡。
- 控制请求通过文件队列与 Bridge 回执闭环，记录成功、失败、超时、已结束等结果。

Bridge 只传结构化事实，不负责生成桌宠台词，便于更换角色和诊断事件是否丢失。

### session 级 Agent 事件监听

桌宠按 session 独立维护 Agent 行为窗口，而不是只看全局“工作中/空闲”状态：

- 监听 `turn/start`、`turn/end`、`step/start`、`step/end`。
- 监听 `tool/call`、`tool/result`、`command/run` 和工作流事件。
- 记录工具类型、目标、参数指纹、执行结果和新证据。
- 多个 Agent 或多个会话互不污染。
- turn 结束、Agent 空闲或任务完成时清理旧窗口，下一轮不会继承历史行为。

### Agent Exploration Loop Watchdog

对 Search、Read、Grep、Glob、导航和 Think 等低信息增益行为进行 step 级聚合检测，不把同一步内的并行调用简单当作多次决策。

- 使用最近 6 步和最近 10 步窗口。
- 区分同类工具重复、同一目标重复、完全相同指纹重复和多目标往返。
- 新目标、新证据、Edit、Run、Test、审批或用户回答会降低风险。
- 支持 Warning、Judge、Replan、Ask User 和 Stop；其中“自动优化/重新规划”目前仍是占位功能。
- Judge 只接收近期短上下文，不发送完整 session。
- 手动模式显示可操作提醒；自动模式的控制链已预留，但自动优化按钮尚未实现真正的规划注入。
- 使用 generation、session 状态和 cooldown，避免旧结果或过期弹窗重新出现。
- 超长 Think 由 Agent/Bridge 生成事件，桌宠负责监听；默认阈值为 60 秒。

> **当前限制与适用场景：** `qwen_v3_6_35B_a3b` 是当前常用的免费模型，通常会被用于同时运行较多并行 Agent；在这种运行方式下，更容易出现重复 Search/Read/Think 探索，因此 Watchdog 相关事件可能比较频繁。实际使用 DeepSeek V4 Flash 时偶尔也会出现类似现象，所以这里是针对“多 Agent 并行 + 重复探索”运行模式的优化，并不是把问题归因于某一个模型本身。提醒弹窗中的“自动优化”按钮暂时只是占位符，尚未向 Agent 注入真正的重新规划提示；当前点击后实际发送的控制事件与“终止”按钮相同。请不要把它当作已经可用的自动重规划功能。

事实描述与 Judge 判断分离。例如 A/B/A/B 会显示“在两个目标之间反复切换”，而不是误报为“重复访问同一目标”。

### 可切换的桌宠语言风格

桌宠设置中提供独立的“台词风格”页面，保留原有模式，并支持：

- 原有模式。
- `whale_maid` 鲸鱼娘女仆模式。
- `custom` 自定义模式。
- 每个事件单独配置多条候选文本。
- 控制、余额、审批、Watchdog、失败和完成等现有事件统一走语言模式。

Bridge 发送事件事实，Pet 根据当前模式渲染台词；按钮文本保持明确，不使用容易产生歧义的角色化表达。当前“自动优化”按钮的行为仍与“终止”相同，真正的独立重规划控制尚未接入。

## 下载与运行

Windows 发布包请前往 [Releases](https://github.com/Daliuq/dsh-pet-harness-indesktop/releases) 下载：

- 安装版：下载 `setup.exe`，按向导安装。
- 绿色版：下载 portable zip，解压后运行目录内的 exe。
- onedir 程序必须保留 exe 旁边的 `_internal` 目录，不能只复制单个 exe。

如果暂时没有 Release，可从 GitHub Actions 构建产物下载，或按下面的开发方式自行打包。

## DSH Bridge 部署

这里的 DSH 指 **DeepSeek Harness**。Bridge 不是单独运行的桌面程序，而是由 DSH 加载的插件。

1. 安装并启动 DeepSeek Harness。
2. 在桌宠菜单打开 Agent Link / DSH Bridge 设置。
3. 选择 DSH profile，执行 Bridge 安装或启用。
4. 重启 DSH，使插件被加载。
5. 启用 Agent Link，桌宠即可监听 DSH 事件。

Bridge 源码位于：

```text
integrations/dsh-pet-bridge/
├─ index.js
├─ package.json
└─ cordis.patch.yml
```

运行时事件日志默认位于：

```text
%APPDATA%\dsh-pet-bridge\dsh-<pid>.jsonl
```

控制操作没有生效时，优先检查 Pet 与 Bridge 是否使用同一个 `%APPDATA%\dsh-pet-bridge` 目录，以及日志中是否同时出现 `bridge/control-received` 和 `watchdog/control-result`。

## 语言文本与配置

默认台词文件可以直接编辑：

```text
pet/persona_phrases.json
```

key 与事件类型一一对应，例如：

```json
{
  "activity.read": ["正在查看文件。"],
  "watchdog.warning": ["好像在重复排查，请留意一下。"],
  "control.replan.success": ["新的规划已经交给 {name}。"],
  "balance.result": ["当前余额：{balance}"]
}
```

修改后重新启动桌宠即可加载。运行时用户配置保存在 `%APPDATA%` 下的桌宠数据目录；源码默认目录为 `%APPDATA%\dsh-pet-standalone\`。

## 从源码运行

建议 Python 3.10 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pet
```

Bridge 开发需要 Node.js；Bridge 的依赖和 DSH 插件描述位于 `integrations/dsh-pet-bridge/`。

## 打包

Windows onedir 构建：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm-chat
```

产物位于 `dist-onedir\`。完整说明见 [`docs/ONEDIR_PACKAGING.md`](docs/ONEDIR_PACKAGING.md)。发布时建议将安装包和 portable zip 上传到 GitHub Release，而不是把大型构建目录直接提交到 Git 仓库。

## 项目关系与许可证

本项目是对上游 [MerZlin/dsh-pet-indesktop](https://github.com/MerZlin/dsh-pet-indesktop) 的增量增强，尽量不改动上游已有的桌宠基础能力；新增内容主要集中在 DSH Bridge、Agent 事件监听、事件队列、Watchdog 和语言风格系统。

感谢上游项目及其贡献者提供的桌宠、动画、聊天和现代设置基础。

许可证以仓库中的 [`LICENSE`](LICENSE) 为准。
