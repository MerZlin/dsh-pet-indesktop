# 可选 Codex 桌宠版本

此入口把已登录的 Codex 账号连接到桌宠聊天，并显示账号额度和本机工作状态。普通 `python -m pet` 保持原有界面和提供商配置；本版本使用独立配置目录，并且当前只支持 PetWindow 渲染方式和含 Chat 的构建。

## 启动

安装本项目依赖及 Codex CLI，先执行 `codex login`。然后在项目根目录运行：

```sh
python -m pet --codex-companion --list-models
python -m pet --codex-companion
# 可选：指定从模型清单取得的 ID、Codex 可执行文件和配置位置
python -m pet --codex-companion --model MODEL_ID --codex /path/to/codex --profile /path/to/profile
```

默认采用 Codex 模型清单中的默认模型。模型清单不代表账号对每个模型均有使用权限；服务端仍可能拒绝生成请求。聊天使用低推理档，回复消耗账号模型额度。额度读取、工作状态和本地绘图不启动模型回合。

配置默认为平台用户数据目录中的 `dsh-codex-edition`。启动器在系统 keyring 保存仅供本机桥接使用的随机密码，`codex-bridge.json` 保存桥接端口、同一个本机密码、模型及可执行文件路径。OAuth 凭证继续由 Codex 管理。桥接只监听 `127.0.0.1`，检查 Host 和 Bearer 密码并拒绝浏览器 Origin；不要分享个人配置目录。keyring 不可用或配置无法保存时会明确失败。

含 Chat 的打包版本可使用同一组 `--codex-companion` 参数。Windows 开机启动使用独立的 `-codex` 注册表值；macOS/Linux 生成独立的 `.codex` 启动项，并保留 `--profile`。桌宠退出后，其启动器关闭所创建的桥接进程。

已有配置使用保存的模型元数据启动，不需要重新取得聊天额度或模型目录。首次读取模型目录失败时仍能开启桌宠；没有可选模型时聊天关闭，之后可用 `--list-models` / `--model` 配置。桥接健康检查仅表示本机监听可用，不证明登录或模型授权。Codex 桌面更新移除旧执行路径后，优先查找当前 PATH，并在 Windows 检查当前用户的 Codex bin 目录。聊天遇到 `UsageLimitExceeded` 返回 429 和额度不足提示，桌宠、工作状态与音乐继续运行；桥接进程无法启动也不阻止桌宠启动。

## 功能

| 功能 | 操作与行为 |
|---|---|
| 剩余额度 | 设置中开启 Codex 用量，点击桌宠显示短期/周额度，点击对应条目查看趋势。缺失数据保持未知。 |
| 用量趋势 | 五小时横轴从首次观测到消耗开始向后五小时；未来部分留白，超过三分钟未采样时分段。七天图显示每天观测到的周额度增长，未知日留空。 |
| 额度提醒 | 剩余量穿过 75%、50%、25% 时各提醒一次；保存已提醒状态，额度窗口重置后重新计数。 |
| 同时进行的工作 | 展开灵动岛逐项显示本机 Codex 工作，超过三行可滚动。收合时持续更新，多个活动任务显示数量。每个工作的状态和思考计时独立。 |
| 操作通知 | 需输入和错误提示优先展示，桌宠短暂显示文字气泡；实际看过的完成/输入通知逐项隐藏，新的状态重新出现。 |
| 状态文案 | 思考超过一分钟显示「雷霆大思考」，工具执行显示「大肥鱼敲代码」，提供中英三种语言。 |
| 独立外观 | 设置、右键菜单、文字气泡、额度、快速聊天和完整聊天各自选择深色/浅色/玻璃；灵动岛使用原有独立主题。 |
| 实时预览 | 调整设置立即预览，保存并退出才持久化；X/Esc 取消并恢复。预览有过期保护，避免崩溃后残留。 |
| 隐藏与聊天 | 隐藏桌宠保留灵动岛工作卡片；显示按钮高亮。隐藏时快速聊天嵌入灵动岛。聊天、歌词气泡与额度窗口避让。 |
| 图片/GIF | 设置窗口使用已有鱼图标；灵动岛头像可导入本地图片或 GIF，导入文件复制进该配置目录，取消清理尚未保存的副本。 |
| YouTube Music | Windows 原生媒体会话自动搜索浏览器/桌面播放器；暂停后继续控制原会话，关闭或丢失目标时不启动游戏回放。悬停灵动岛显示三个圆形播放控件。 |
| 歌词 | 使用项目已有歌词来源；首句出现前显示歌名，开始后只显示完整换行歌词。播放器没有同步歌词时显示歌名，暂停后不继续推进。 |

额度每分钟读一次桥接（桥接缓存 30 秒），开启监控的桌宠运行期间生效，关闭监控即停止轮询。历史最多九天/20,000 个采样，文件有界；目前每次保存重写 JSON。普通两窗口、九天满采样实测约 3.16 MB，一次保存约 35 ms。相应磁盘写入上界约 4.56 GB/天，应在需要持续监控时开启。查看图片/GIF、歌名和用户聊天内容不会经过界面文字翻译。

工作状态每 1.5 秒读取本机 Codex 的 `state_5.sqlite`、`thread_history_1.sqlite` 及增量 rollout 工具事件。仅读取状态/工具名和标题，不保存提示词、工具参数或结果；系统子代理不列成独立项目。多个 Codex 工作可以同时显示。已结束通知限制最近十二小时，运行中的长任务不受该期限限制。Codex 本地数据库格式变化时显示暂时不可用。

## 设置契约

新增普通键的默认值、reload 白名单和 schema 快照同步登记在 `pet/config.py` / `tests/test_config_schema.py`。所有普通键沿用 Config 的 per-slot 规则；本版本首先面向单桌宠，多个窗口显示同一份账号额度和本机工作列表。

| 键 | 默认/合法值 | 归属、恢复与保存 |
|---|---|---|
| `ui_language` | `zh_CN`；`zh_CN`/`zh_TW`/`en` | 界面语言；无效值恢复简中，用户输入与聊天不翻译。 |
| `codex_usage_enabled` | `false`，严格布尔 | 额度与提醒；独立版本首次启动置 true，此后保留用户选择。 |
| `codex_work_status_enabled` | `false`，严格布尔 | 工作通知；同上。 |
| `ytmusic_auto_connect` | `true`，严格布尔 | 浏览器音乐发现；非 Windows 沿用原媒体能力。 |
| `settings_ui_style` / `menu_ui_style` / `bubble_ui_style` / `quota_ui_style` / `quick_chat_ui_style` / `chat_window_ui_style` | `dark`；`dark`/`light`/`glass` | 各界面独立外观；无效值恢复 dark，预览不写正式配置。 |

继承并使用现有 `bubble_text_scale` 和 `dynamic_island` 头像/主题字段，不添加平行的大小或头像设置。设置保存保留原有原子写入、失败提示和独立设置进程合并语义。初次进入独立配置不会迁移或覆盖普通版本的历史和密钥。

## 可选 Hooks

读取本机工作状态不需要修改 Hooks。若要更准确获知权限请求，可生成下列附加配置。`EVENTS_DIR` 应为实际 `codex-bridge.json` 所在目录下的 `events`；启动器在 profile 内还会建立 `APP_DIR_NAME` 子目录，不是直接使用 profile 根目录：

```sh
python -m pet.codex_bridge.hooks --events EVENTS_DIR
# 明确选择安装时，保留已有配置并创建备份
python -m pet.codex_bridge.hooks --events EVENTS_DIR --install
```

再到 Codex Hooks 页面审阅并信任。程序不自动信任命令。Hook 每个 session 保存一个小文件，只包含状态、时间、事件与 session ID；不会保存请求内容。旧 session 文件目前不会自动清理。

## 验证与限制

接口根据 [官方 app-server 文档](https://learn.chatgpt.com/docs/app-server)，使用默认 stdio transport、`model/list`、`account/rateLimits/read` 和临时 `thread/start` / `turn/start`。聊天临时线程关闭 shell/apps/hooks/多代理，不参与本机工作清单；OpenAI-compatible 的流式响应在完整回复返回后分块发送，不提供实时首 token 流。

```sh
QT_QPA_PLATFORM=offscreen python scripts/probe_codex_companion.py --output /tmp/companion-ui --screenshots
QT_QPA_PLATFORM=offscreen python scripts/probe_codex_work_status.py --output /tmp/companion-status
QT_QPA_PLATFORM=offscreen python scripts/probe_codex_pet.py --output /tmp/companion-pet
QT_QPA_PLATFORM=offscreen python scripts/probe_codex_music_hover.py --output /tmp/companion-hover
python scripts/benchmark_codex_companion.py --output /tmp/companion-benchmark
python scripts/stress_codex_companion.py --output /tmp/companion-stress
```

Windows 实际代码、Qt 离屏界面及本机 Codex 已验证；不把离屏截图等同于直接查看使用者桌面。macOS/Linux 实机、完整发行构建及 overlay 拓扑仍需分别验证。原项目的歌词源、媒体上报和玻璃效果能力限制继续适用。详细证据见 [PR 报告](PR-REPORT-CODEX-COMPANION-2026-10-06.md)，入口索引见 [INDEX.md](INDEX.md)。
