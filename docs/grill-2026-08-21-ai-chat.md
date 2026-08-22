# 桌宠 AI 对话功能需求对齐记录

- 日期：2026-08-21
- 任务：为现有 PySide6 桌宠增加可配置、多 Provider、流式、多轮上下文的 AI 对话能力
- 实际开发目录：`E:\AI\test\Gametest\dsh-pet-standalone`
- 分支：`codex/ai-chat`

## 已确认决策

- 实现完整可运行版本，而不是只提供架构草图。
- 首期支持多个 OpenAI Chat Completions 兼容 Provider；不实现 Gemini 原生协议。
- 保存 JSON 会话，支持恢复、清空、失败重试和停止生成。
- 使用独立聊天窗口；桌宠通过状态、短气泡和可选动作联动，不改透明 mask/动画状态机核心。
- 使用标准库 HTTP + QThread；API Key 优先系统钥匙串，失败时允许配置文件回退。
- system prompt 优先级固定为：当前角色用户覆盖 > 角色 manifest > 全局默认。
- 上下文使用消息数和字符数双重裁剪；角色切换时隔离上下文。
- 本轮只创建和修改 `codex/ai-chat`，不执行 `git add`、`git commit`、`git push`。