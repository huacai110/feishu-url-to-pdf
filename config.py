"""
配置管理模块
从 .env 文件或环境变量读取配置
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── 飞书应用凭证 ──────────────────────────────────────────────
    APP_ID: str = os.getenv("FEISHU_APP_ID", "")
    APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")

    # ── 事件订阅验证 ──────────────────────────────────────────────
    VERIFICATION_TOKEN: str = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
    ENCRYPT_KEY: str = os.getenv("FEISHU_ENCRYPT_KEY", "")  # 可选，用于事件加密

    # ── 服务配置 ──────────────────────────────────────────────────
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "9000"))

    # ── PDF 字体配置 ──────────────────────────────────────────────
    # 优先使用环境变量指定的路径，否则按操作系统自动选择
    _font_path = os.getenv("FONT_PATH", "")
    _font_bold_path = os.getenv("FONT_BOLD_PATH", "")

    if _font_path and os.path.exists(_font_path):
        FONT_PATH: str = _font_path
    elif os.name == "nt":
        # Windows
        FONT_PATH: str = r"C:\Windows\Fonts\msyh.ttc"
    else:
        # Linux (Render.com 等) — fonts-noto-cjk 包
        FONT_PATH: str = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

    if _font_bold_path and os.path.exists(_font_bold_path):
        FONT_BOLD_PATH: str = _font_bold_path
    elif os.name == "nt":
        FONT_BOLD_PATH: str = r"C:\Windows\Fonts\msyhbd.ttc"
    else:
        FONT_BOLD_PATH: str = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

    # ── 飞书 API 基础地址 ─────────────────────────────────────────
    FEISHU_BASE_URL: str = "https://open.feishu.cn/open-apis"
