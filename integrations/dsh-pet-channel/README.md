# dsh-pet-channel

桌宠与 DSH 之间的本机双向通道。与 `dsh-pet-bridge`（只写事件 JSONL）互补：

- 桌宠 → DSH：`POST /pet/prompt`，SSE `GET /pet/events` 收思考/工具/正文/审批；
- DSH → 桌宠：`pet_control` 工具或 `/pet say|play|notify` 写 `pet/command` 记录；
- 历史：`GET /pet/history` 只读镜像；
- 审批：`POST /pet/answer`；取消：`POST /pet/cancel`。

只绑 `127.0.0.1`，每个请求带 `x-pet-token`；token 持久化在
`~/.dsh/pet-channel/state.json`，并把 `{origin,port,token,pid}` 写到
`%APPDATA%/dsh-pet-bridge/pet-channel.json`。

## 安装

由桌宠「设置 → 常规 → DSH 连接 → 安装/更新 DSH 通道」写入
`~/.dsh/local-plugins/` 与 `profiles/desktop/package.json`，重启 DSH 生效。
`cordis.patch.yml` 随包提供，不需要手改 profile 的 cordis 配置。

## 安全边界

- 只监听回环；token 明文保存在本机文件，任何本机进程读到即可调用；
- `sandboxMode: danger-full-access` 由 profile 的 patch 决定，按需在
  `cordis.patch.yml` 里改；
- 审批闸（`askApproval`）默认关闭；打开后危险命令会先推到桌宠等同意。
