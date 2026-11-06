# AstrBot Agent Link bridge

AstrBot 插件：把 AstrBot「收到需要 LLM 处理的消息 → 调用工具 → 回复发出」的
过程同步给 dsh-pet 桌宠的 **Agent 联动**（内置 `astrbot` 通道）。

## 安装

推荐在桌宠右键菜单 **Agent 联动 → AstrBot** 一键安装：

1. 选择 AstrBot 目录（含 `data` 文件夹的 AstrBot 根目录，或 `data` 目录本身；
   Docker 部署选宿主机上挂载的那个 `data` 目录）；
2. 同意安装后，桌宠把本目录复制到 `<data>/plugins/astrbot_plugin_dsh_pet_bridge/`
   （带 `.dsh-pet-bridge` 标记文件，关闭联动时只删带标记的这个目录）；
3. **重启 AstrBot，或在 AstrBot WebUI 的插件页重载插件**后联动生效。

手动安装：把 `main.py` 与 `metadata.yaml` 复制到
`<data>/plugins/astrbot_plugin_dsh_pet_bridge/`，再在桌宠里开启 AstrBot 联动并选择同一个 data 目录。

## 事件文件

```
<data>/plugin_data/astrbot_plugin_dsh_pet_bridge/astrbot.jsonl
```

`<data>` 取插件安装位置的上两级目录，所以 Docker 部署只要 data 目录挂载到宿主机，
桌宠就能直接读到。AstrBot 进程里设置 `DSH_PET_ASTRBOT_EVENTS` 可覆盖路径
（此时桌宠侧需改用 `agent_link.custom_agents` 指向同一文件）。

## 状态映射

| AstrBot 钩子 | 写出的状态 |
|---|---|
| 插件加载 / 卸载 | `idle`（收口上次异常退出留下的忙碌态） |
| `on_llm_request` | `thinking` |
| `on_using_llm_tool`（AstrBot > v4.23.1） | `working` + 工具名 |
| `on_llm_tool_respond`（AstrBot > v4.23.1） | 工具失败 → `error`；否则回到 `thinking`/`working` |
| `on_llm_response` | 响应角色为 `err` → `error` 并结束该回合 |
| `after_message_sent` | 结束该回合；全部回合结束 → `idle` |

多会话并发时按在途回合折叠：任一回合在调工具即 `working`，否则有在途回合即
`thinking`，全部结束才 `idle`。回合 600s 未结束、或拿到最终响应 30s 内没发出
消息时自动回收；在途回合表上限 256 条。

## 数据边界

每行只含 `ts` / `agent` / `inst`（插件本次加载的随机 UUID）/ `state`，working 时附
短工具名 `tool`。**不写**消息正文、提示词、回复内容、会话 / 用户 / 群号、工具参数与
结果。不发任何网络请求。写失败静默放弃，不影响 AstrBot。事件文件超过 1MB 轮转为
`.1`，只留一代。
