"""
PDF 生成模块
负责：Markdown → 格式化 PDF（支持中文，保留完整结构）

使用 reportlab + Platypus 排版引擎，注册微软雅黑字体处理中文。
"""
import os
import re
import logging
import tempfile
from urllib.parse import urlparse

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Preformatted,
    PageBreak,
    HRFlowable,
    KeepTogether,
    Table,
    TableStyle,
    Image as RLImage,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from config import Config

logger = logging.getLogger(__name__)

# ── 颜色常量 ──────────────────────────────────────────────────────
COLOR_TITLE = HexColor("#1a1a1a")
COLOR_BODY = HexColor("#333333")
COLOR_CODE_BG = HexColor("#f5f5f5")
COLOR_CODE_BORDER = HexColor("#e0e0e0")
COLOR_QUOTE = HexColor("#6a737d")
COLOR_QUOTE_BG = HexColor("#f6f8fa")
COLOR_LINK = HexColor("#0366d6")
COLOR_RULE = HexColor("#dfe2e5")

PAGE_W, PAGE_H = A4
MARGIN_LEFT = 25 * mm
MARGIN_RIGHT = 25 * mm
MARGIN_TOP = 20 * mm
MARGIN_BOTTOM = 20 * mm
CONTENT_W = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT


def _register_fonts():
    """注册中文字体，返回字体族名称"""
    font_path = Config.FONT_PATH
    bold_path = Config.FONT_BOLD_PATH

    if not os.path.exists(font_path):
        raise FileNotFoundError(
            f"字体文件不存在: {font_path}\n"
            "请在 .env 中配置 FONT_PATH 指向一个支持中文的 TTF/TTC 字体"
        )

    pdfmetrics.registerFont(TTFont("CJK", font_path))
    if os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont("CJK-Bold", bold_path))
    else:
        pdfmetrics.registerFont(TTFont("CJK-Bold", font_path))

    return "CJK"


def _escape_xml(text: str) -> str:
    """转义 XML 特殊字符（用于 reportlab Paragraph）"""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _inline_format(text: str) -> str:
    """
    将 Markdown 行内格式转为 reportlab XML 标记
    **bold** → <b>bold</b>
    *italic* → <i>italic</i>
    `code`   → <font face="Courier" color="#e83e8c">code</font>
    [text](url) → <a href="url" color="blue">text</a>
    """
    # 先转义 XML
    text = _escape_xml(text)

    # 粗体 **text**
    text = re.sub(
        r"\*\*(.+?)\*\*",
        r'<b>\1</b>',
        text,
    )
    # 斜体 *text*
    text = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
        r'<i>\1</i>',
        text,
    )
    # 行内代码 `code`
    text = re.sub(
        r"`([^`]+)`",
        r'<font face="Courier" size="9" color="#e83e8c">\1</font>',
        text,
    )
    # 链接 [text](url)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" color="#0366d6"><u>\1</u></a>',
        text,
    )
    return text


class PDFGenerator:
    """Markdown → PDF 生成器"""

    def __init__(self):
        self._font = _register_fonts()
        self._styles = self._build_styles()

    # ── 样式定义 ──────────────────────────────────────────────────

    def _build_styles(self) -> dict:
        """构建段落样式表"""
        s = {}

        s["title"] = ParagraphStyle(
            "Title",
            fontName="CJK-Bold",
            fontSize=20,
            leading=28,
            textColor=COLOR_TITLE,
            spaceAfter=16,
            alignment=TA_LEFT,
        )
        s["h1"] = ParagraphStyle(
            "H1",
            fontName="CJK-Bold",
            fontSize=18,
            leading=26,
            textColor=COLOR_TITLE,
            spaceBefore=20,
            spaceAfter=10,
        )
        s["h2"] = ParagraphStyle(
            "H2",
            fontName="CJK-Bold",
            fontSize=15,
            leading=22,
            textColor=COLOR_TITLE,
            spaceBefore=16,
            spaceAfter=8,
        )
        s["h3"] = ParagraphStyle(
            "H3",
            fontName="CJK-Bold",
            fontSize=13,
            leading=20,
            textColor=COLOR_TITLE,
            spaceBefore=12,
            spaceAfter=6,
        )
        s["h4"] = ParagraphStyle(
            "H4",
            fontName="CJK-Bold",
            fontSize=12,
            leading=18,
            textColor=COLOR_BODY,
            spaceBefore=10,
            spaceAfter=4,
        )
        s["body"] = ParagraphStyle(
            "Body",
            fontName="CJK",
            fontSize=10.5,
            leading=18,
            textColor=COLOR_BODY,
            spaceAfter=6,
            alignment=TA_JUSTIFY,
            firstLineIndent=0,
        )
        s["bullet"] = ParagraphStyle(
            "Bullet",
            fontName="CJK",
            fontSize=10.5,
            leading=18,
            textColor=COLOR_BODY,
            leftIndent=18,
            bulletIndent=6,
            spaceAfter=3,
        )
        s["code"] = ParagraphStyle(
            "Code",
            fontName="Courier",
            fontSize=8.5,
            leading=13,
            textColor=HexColor("#24292e"),
            backColor=COLOR_CODE_BG,
            borderWidth=0.5,
            borderColor=COLOR_CODE_BORDER,
            borderPadding=8,
            spaceBefore=6,
            spaceAfter=6,
            leftIndent=6,
            rightIndent=6,
        )
        s["quote"] = ParagraphStyle(
            "Quote",
            fontName="CJK",
            fontSize=10,
            leading=16,
            textColor=COLOR_QUOTE,
            leftIndent=16,
            borderWidth=0,
            spaceBefore=6,
            spaceAfter=6,
        )
        s["source"] = ParagraphStyle(
            "Source",
            fontName="CJK",
            fontSize=9,
            leading=14,
            textColor=COLOR_LINK,
            spaceBefore=8,
            spaceAfter=4,
        )
        s["footer"] = ParagraphStyle(
            "Footer",
            fontName="CJK",
            fontSize=8,
            leading=12,
            textColor=HexColor("#999999"),
            alignment=TA_CENTER,
        )
        return s

    # ── 主入口 ────────────────────────────────────────────────────

    def generate(
        self,
        markdown_text: str,
        title: str,
        source_url: str,
        output_path: str,
    ) -> str:
        """
        将 Markdown 内容生成为 PDF 文件
        :return: 生成的 PDF 文件路径
        """
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=MARGIN_LEFT,
            rightMargin=MARGIN_RIGHT,
            topMargin=MARGIN_TOP,
            bottomMargin=MARGIN_BOTTOM,
            title=title,
        )

        elements = []

        # 标题
        elements.append(Paragraph(_escape_xml(title), self._styles["title"]))

        # 来源链接
        if source_url:
            safe_url = _escape_xml(source_url)
            elements.append(
                Paragraph(
                    f'来源: <a href="{safe_url}" color="#0366d6">{safe_url}</a>',
                    self._styles["source"],
                )
            )

        # 分隔线
        elements.append(Spacer(1, 6))
        elements.append(
            HRFlowable(
                width="100%",
                thickness=0.5,
                color=COLOR_RULE,
                spaceAfter=12,
            )
        )

        # 解析 Markdown 正文
        body_elements = self._parse_markdown(markdown_text)
        elements.extend(body_elements)

        # 页脚
        elements.append(Spacer(1, 20))
        elements.append(
            HRFlowable(
                width="100%",
                thickness=0.5,
                color=COLOR_RULE,
                spaceBefore=10,
                spaceAfter=6,
            )
        )
        elements.append(
            Paragraph(
                "由飞书 URL-to-PDF 机器人自动生成",
                self._styles["footer"],
            )
        )

        doc.build(elements)
        logger.info(f"PDF 已生成: {output_path}")
        return output_path

    # ── Markdown 解析 ─────────────────────────────────────────────

    def _parse_markdown(self, text: str) -> list:
        """将 Markdown 文本转为 reportlab flowable 元素列表"""
        elements = []
        lines = text.split("\n")
        i = 0
        in_code = False
        code_buf: list[str] = []
        code_lang = ""
        para_buf: list[str] = []

        def flush_para():
            if para_buf:
                content = " ".join(para_buf)
                elements.append(
                    Paragraph(_inline_format(content), self._styles["body"])
                )
                para_buf.clear()

        while i < len(lines):
            line = lines[i]

            # ── 代码块 ────────────────────────────────────────
            if line.strip().startswith("```"):
                if in_code:
                    # 代码块结束
                    code_text = "\n".join(code_buf)
                    elements.append(
                        Preformatted(code_text, self._styles["code"])
                    )
                    code_buf = []
                    in_code = False
                else:
                    # 代码块开始
                    flush_para()
                    in_code = True
                    code_lang = line.strip()[3:].strip()
                i += 1
                continue

            if in_code:
                code_buf.append(line)
                i += 1
                continue

            stripped = line.strip()

            # ── 空行 ──────────────────────────────────────────
            if not stripped:
                flush_para()
                i += 1
                continue

            # ── 水平线 ────────────────────────────────────────
            if re.match(r"^[-*_]{3,}$", stripped):
                flush_para()
                elements.append(
                    HRFlowable(
                        width="100%",
                        thickness=0.5,
                        color=COLOR_RULE,
                        spaceBefore=8,
                        spaceAfter=8,
                    )
                )
                i += 1
                continue

            # ── 标题 ──────────────────────────────────────────
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if heading_match:
                flush_para()
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2)
                style_key = f"h{min(level, 4)}"
                elements.append(
                    Paragraph(
                        _inline_format(heading_text), self._styles[style_key]
                    )
                )
                i += 1
                continue

            # ── 引用块 ────────────────────────────────────────
            if stripped.startswith(">"):
                flush_para()
                quote_lines = []
                while i < len(lines) and lines[i].strip().startswith(">"):
                    q = re.sub(r"^>\s?", "", lines[i].strip())
                    quote_lines.append(q)
                    i += 1
                quote_text = " ".join(quote_lines)
                elements.append(
                    Paragraph(
                        _inline_format(quote_text), self._styles["quote"]
                    )
                )
                continue

            # ── 无序列表 ──────────────────────────────────────
            bullet_match = re.match(r"^[-*+]\s+(.+)$", stripped)
            if bullet_match:
                flush_para()
                list_items = []
                while i < len(lines):
                    bm = re.match(r"^[-*+]\s+(.+)$", lines[i].strip())
                    if bm:
                        list_items.append(bm.group(1))
                        i += 1
                    elif lines[i].strip() == "":
                        break
                    else:
                        break
                for item in list_items:
                    elements.append(
                        Paragraph(
                            f"\u2022 {_inline_format(item)}",
                            self._styles["bullet"],
                        )
                    )
                continue

            # ── 有序列表 ──────────────────────────────────────
            num_match = re.match(r"^\d+[.)]\s+(.+)$", stripped)
            if num_match:
                flush_para()
                list_items = []
                while i < len(lines):
                    nm = re.match(r"^\d+[.)]\s+(.+)$", lines[i].strip())
                    if nm:
                        list_items.append(nm.group(1))
                        i += 1
                    elif lines[i].strip() == "":
                        break
                    else:
                        break
                for idx, item in enumerate(list_items, 1):
                    elements.append(
                        Paragraph(
                            f"{idx}. {_inline_format(item)}",
                            self._styles["bullet"],
                        )
                    )
                continue

            # ── 普通段落（累积合并） ─────────────────────────
            para_buf.append(stripped)
            i += 1

        flush_para()
        return elements

    # ── 便捷方法 ──────────────────────────────────────────────────

    def generate_to_temp(
        self, markdown_text: str, title: str, source_url: str
    ) -> str:
        """生成 PDF 到临时文件，返回文件路径"""
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)[:80]
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix=f"url_{safe_title}_")
        os.close(fd)
        return self.generate(markdown_text, title, source_url, path)
