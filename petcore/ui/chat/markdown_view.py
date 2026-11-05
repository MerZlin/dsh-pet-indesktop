"""AI 回复的 Markdown → Qt 富文本。

用 Qt 自带的 md4c 解析（QTextDocument.setMarkdown，GitHub 方言：表格、删除线、
任务列表、自动链接），不引入第三方依赖。几处取舍：

- ``MarkdownNoHTML``：模型输出里的原始 HTML 按字面显示，不渲染——联网搜索的网页
  内容可能诱导模型输出标签，渲染进 QLabel 没必要也不安全；
- 单个换行保留为换行：标准 Markdown 把单换行并成同一段，但桌宠的回复常用单换行
  分句（与 GitHub 评论 / 主流聊天界面的显示习惯一致）；代码块内不处理；
- 去掉文档级字体，让气泡沿用聊天窗口 QSS 的字体字号；代码换等宽字体并加浅底色。
"""

from __future__ import annotations

import html as html_lib
import re

from PySide6.QtGui import QTextDocument

#: 内联图片的默认显示宽度（逻辑像素）；高度由 Qt 等比算。
INLINE_IMAGE_WIDTH = 240
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_IMG_ATTR = re.compile(r'([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*"([^"]*)"')

_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_BODY_STYLE = re.compile(r'<body style="[^"]*">')
_LINK_COLOR = re.compile(r'(<a href="[^"]*">)<span style=" color:#[0-9a-fA-F]{6};">')
_TABLE_DELIM = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_FONT_DECL = re.compile(r"font-family:'[^']*';")
_MONO_STYLED = (
    "font-family:'Consolas','Courier New',monospace; background-color:rgba(127,127,127,0.16);"
)
# 聊天气泡里标题缩一档，避免 h1 比正文大太多
_HEADING_SIZE = re.compile(r"font-size:(xx-large|x-large);")
_HEADING_MAP = {"xx-large": "x-large", "x-large": "large"}


def _features():
    features = QTextDocument.MarkdownFeature
    return features(features.MarkdownDialectGitHub.value | features.MarkdownNoHTML.value)


def _is_table_header(lines, i) -> bool:
    return 0 <= i < len(lines) - 1 and "|" in lines[i] and bool(_TABLE_DELIM.match(lines[i + 1]))


def preserve_line_breaks(text: str) -> str:
    """段落内的单个换行改成硬换行（行尾两个空格）；围栏代码块内原样保留。

    另外：表格紧跟在文字行后面时补一个空行——md4c 不允许表格打断段落，而模型常写
    「如下：」换行就直接开始画表。
    """
    lines = str(text).split("\n")
    out = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        if _is_table_header(lines, i) and out and out[-1].strip() and "|" not in out[-1]:
            out.append("")
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if (
            line.strip()
            and nxt.strip()
            and not _FENCE.match(nxt)
            and not _is_table_header(lines, i + 1)
            and not line.endswith("  ")
            and not line.endswith("\\")
        ):
            line += "  "
        out.append(line)
    return "\n".join(out)


def _rewrite_images(html_text: str) -> str:
    """本地/data 图注入宽度；远程图改成可读占位（UI 线程不联网）。

    ``QTextDocument`` 对 ``https`` 图片不加载，QLabel 会显示一个 16×16 破图；
    这里换成文字占位，至少让用户知道模型给了图、并把 alt/链接语义保住。
    """
    def _replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        attrs = {
            name.lower(): value for name, value in _IMG_ATTR.findall(tag)
        }
        source = str(attrs.get("src") or "")
        alt = html_lib.unescape(str(attrs.get("alt") or "")).strip() or "远程图片"
        if source.lower().startswith(("http://", "https://")):
            return f"[图片：{html_lib.escape(alt)}]"
        if "width" in attrs:
            return tag
        return re.sub(r"\s*/?>$", f' width="{INLINE_IMAGE_WIDTH}" />', tag)

    return _IMG_TAG.sub(_replace, html_text)


def markdown_to_html(text: str, *, link_color: str = "#3994ff") -> str:
    doc = QTextDocument()
    doc.setMarkdown(preserve_line_breaks(text), _features())
    html = doc.toHtml()
    head, _sep, body = html.partition("<body")
    body = _BODY_STYLE.sub("<body>", "<body" + body, count=1)
    # 去掉文档级字体后，正文里剩下的 font-family 只来自代码片段（Qt 按平台写入不同的
    # 等宽族名），统一换成等宽字体 + 浅底色
    html = head + _FONT_DECL.sub(_MONO_STYLED, body)
    html = _HEADING_SIZE.sub(lambda m: f"font-size:{_HEADING_MAP[m.group(1)]};", html)
    html = _LINK_COLOR.sub(lambda m: f'{m.group(1)}<span style=" color:{link_color};">', html)
    return _rewrite_images(html)
