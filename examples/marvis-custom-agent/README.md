# Marvis 自定义 Agent 联动示例

通过 `agent_link.custom_agents` 把本机 **Marvis 助手**接入桌宠联动（零代码接入，不改桌宠源码）。

## 背景

Marvis 是本地 AI 助手（云端核心），本地组件无公开对话 API、无 hook 机制、也没有现成的 transcript / SQLite 事件源，因此无法走 DSH 插件订阅、Claude hooks、Cursor transcript、OpenCode 直读这四种内置模式。但它能执行任意命令，可以按 [docs/AGENT_LINK_PROTOCOL.md](../docs/AGENT_LINK_PROTOCOL.md) §4「自定义 Agent 通道」接入：桌宠只需监听一个 JSONL 事件文件。

## 文件说明

| 文件 | 作用 |
|---|---|
| `agent_link.config.json` | `config.json` 中 `agent_link` 段的配置片段 |
| `push_marvis_event.py` | 极简事件推送脚本（统一协议，追加写 JSONL，零依赖） |

## 配置步骤

1. 把 `agent_link.config.json` 中的 `agent_link` 块合并进桌宠配置文件 `<config.dir>/config.json`（Windows 为 `%APPDATA%\dsh-pet-standalone[-变体]\config.json`），按你的安装变体修正 `path` 中的实际目录。

2. 重启桌宠，右键菜单「Agent 联动」出现 Marvis 开关并启用。

3. Marvis 侧在干活时调用推送脚本写状态（事件文件不存在时静默等待，无需预创建）：

```powershell
# 开工：thinking
python push_marvis_event.py --state thinking --tool web_search
# 执行中：working（也可用事件名 PreToolUse）
python push_marvis_event.py --event PreToolUse --tool bash
# 收工：idle
python push_marvis_event.py --state idle
# 出错：error
python push_marvis_event.py --state error
```

也可在 Marvis 的本地钩子 / 插件里调用脚本或直接追加写同一文件。

## 验证

- 桌宠日志出现 `Agent 监视器 [marvis] 已启动`
- 推送 `working` / `idle` 后桌宠出现「开始干活」「干完活啦」气泡
- 事件文件格式符合统一协议（见 AGENT_LINK_PROTOCOL.md §2.2）

## 注意事项

- 事件文件只用追加写（append），UTF-8 编码；连续相同状态不要重复落盘
- 超过约 1MB 时轮转（`marvis.jsonl` → `marvis.jsonl.1`）
- 只写状态 / 事件名 / 工具名元数据，不写对话正文、命令全文、文件内容
- 联动开关默认关闭，桌宠隐藏时监视器自动暂停
