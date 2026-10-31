"""ai.chat 的 prompt 与消息组装（纯函数，无 Qt、无网络）。

运行时的职责只剩「什么时候发」；这里负责「发什么」，便于单独测试文案与历史形状。
"""

from __future__ import annotations

DEFAULT_PROMPT = (
    "你是「{name}」，一只住在主人桌面上的鲸鱼娘女仆。\n"
    "性格：温和、细心，会把主人当成需要照顾的人，但不越界、不替主人做决定。\n"
    "说话方式：自然的中文口语，少量语气词；不要每句都带颜文字。\n"
    "回答长度：默认三到六句话；主人问步骤、code 或排查问题时可以分点讲清楚，"
    "最多不超过八句，不刷屏。\n"
    "边界：不确定就直说，不编造事实；不假装看到了屏幕或文件，除非对话里真的提供了内容。"
)

LOOK_SCREEN_PROMPT = "看看我的屏幕，用一两句话说说你现在看到什么。"
SCREEN_QUESTION_PROMPT = "看看这块区域里是什么，说清内容，能给出判断或建议时就直接给。"


def system_prompt(configured: str, character_name: str) -> str:
    text = str(configured or "").strip()
    return text or DEFAULT_PROMPT.format(name=character_name)


def _text_turns(history, limit: int) -> list[dict]:
    turns: list[dict] = []
    for item in list(history or [])[-max(1, int(limit)) :]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        content = str(item.get("content") or "")
        if content.strip():
            turns.append({"role": role, "content": content})
    return turns


def look_screen_messages(system: str, png_b64: str, app_info: str = "") -> list[dict]:
    prompt = LOOK_SCREEN_PROMPT
    if app_info:
        prompt += f"\n（前台应用：{app_info}）"
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + str(png_b64)},
                },
            ],
        },
    ]


def chat_messages(
    system: str,
    history,
    text: str,
    image_urls: list[str] | None = None,
    *,
    limit: int = 40,
) -> list[dict]:
    """主对话消息：历史 + 本轮问题；带图时本轮改成多模态消息。"""
    turns = _text_turns(history, limit)
    images = [url for url in (image_urls or []) if url]
    messages = [{"role": "system", "content": system}]
    if not images:
        messages.extend(turns)
        return messages
    # controller 在回调前已把本轮问题追加进会话；带图时会重发多模态版本，
    # 先去掉最后那条同文纯文本，避免同一句话出现两次。
    if (
        turns
        and turns[-1].get("role") == "user"
        and str(turns[-1].get("content") or "").strip() == str(text or "").strip()
    ):
        turns = turns[:-1]
    messages.extend(turns)
    messages.append(
        {
            "role": "user",
            "content": [{"type": "text", "text": str(text or "")}]
            + [{"type": "image_url", "image_url": {"url": url}} for url in images],
        }
    )
    return messages


def ask_messages(
    system: str,
    history,
    question: str,
    image_url: str,
    *,
    limit: int = 40,
) -> list[dict]:
    """截图问答消息：图只挂在本会话第一条 user 历史（或本轮问题）上。"""
    turns = _text_turns(history, limit)
    messages = [{"role": "system", "content": system}]
    attached = False
    for item in turns:
        if item["role"] == "user" and not attached:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": item["content"]},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            )
            attached = True
        else:
            messages.append(item)
    text = str(question or "").strip() or SCREEN_QUESTION_PROMPT
    if attached:
        messages.append({"role": "user", "content": text})
    else:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        )
    return messages


OCR_PROMPT = (
    "请完整提取图片中的所有文字，按原顺序与换行输出。不要翻译、不要解释、"
    "不要总结；没有文字就回复「没有识别到文字」。"
)
TRANSLATE_TEXT_PROMPT = (
    "把下面的文字翻译成{target}，保留原有换行与段落；只输出译文，不要解释。\n\n{text}"
)
TRANSLATE_IMAGE_PROMPT = (
    "先完整识别图片中的文字，再翻译成{target}；保留原有换行与段落，"
    "按「原文」与「译文」两段输出。"
)


def target_language(text: str) -> str:
    """自动目标语言：中日韩占多数 → 英文，否则中文（同主分支口径）。"""
    cjk = sum(1 for char in str(text or "") if "\u4e00" <= char <= "\u9fff"
              or "\u3040" <= char <= "\u30ff" or "\uac00" <= char <= "\ud7af")
    latin = sum(1 for char in str(text or "") if char.isascii() and char.isalpha())
    return "英文" if cjk > latin else "中文"


def ocr_messages(system: str, image_url: str) -> list[dict]:
    """提取文字：视觉模型，只要文字，不许改写。"""
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": OCR_PROMPT},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]


def translate_messages(
    system: str, source_text: str, image_url: str = "", *, target: str = ""
) -> list[dict]:
    """翻译：有本地 OCR 原文时走纯文本，没有才把图交给视觉模型。"""
    text = str(source_text or "").strip()
    language = str(target or "").strip() or target_language(text)
    messages = [{"role": "system", "content": system}]
    if text:
        messages.append(
            {
                "role": "user",
                "content": TRANSLATE_TEXT_PROMPT.format(target=language, text=text),
            }
        )
        return messages
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": TRANSLATE_IMAGE_PROMPT.format(target=language),
                },
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    )
    return messages
