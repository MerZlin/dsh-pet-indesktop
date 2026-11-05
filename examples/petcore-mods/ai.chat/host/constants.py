"""ai.chat 的稳定标识与节流参数。"""

from __future__ import annotations

OWNER = "ai.chat"

#: 发给模型的历史轮数上限（按消息条数计，不是问答对数）。
MAX_HISTORY = 40

#: 快速对话只带最近几轮，气泡不是完整会话窗。
QUICK_HISTORY = 8

#: 看看屏幕的最短触发间隔，避免连续点击把额度打满（同主分支 4 秒）。
LOOK_SCREEN_COOLDOWN_S = 4.0

#: 发给模型的表情包清单最多列多少张（目录更大时等距抽样）。
MEME_CATALOG_LIMIT = 60

#: 模型只回工具调用（core 不执行工具）时给用户看的可见说明；不能留空气泡。
TOOL_UNSUPPORTED_TEXT = (
    "我这边没有工具、命令执行或文件读写能力，没法直接替你操作电脑。"
    "你可以告诉我具体想做什么，我可以给出步骤或建议。"
)
