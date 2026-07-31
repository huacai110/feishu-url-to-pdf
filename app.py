"""
飞书 URL-to-PDF 机器人 — 主应用
流程：@机器人 + URL → 抓取网页 → Markdown 整理 → 生成 PDF → 回复下载

启动方式:
    uvicorn app:app --host 0.0.0.0 --port 9000
    或
    python run.py
"""
import os
import json
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from config import Config
from feishu_api import FeishuAPI
from web_scraper import WebScraper
from pdf_generator import PDFGenerator

# ── 日志 ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("url-to-pdf")

# ── 全局实例 ──────────────────────────────────────────────────────
feishu = FeishuAPI()
scraper = WebScraper()
pdf_gen = PDFGenerator()

# 已处理的消息 ID 集合（防止飞书重试导致重复处理）
_processed_msgs: set[str] = set()
MAX_PROCESSED_CACHE = 1000


# ── 应用生命周期 ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("URL-to-PDF 机器人启动中...")
    _validate_config()
    logger.info(f"服务就绪 → http://{Config.HOST}:{Config.PORT}")
    yield
    logger.info("服务已关闭")


app = FastAPI(title="Feishu URL-to-PDF Bot", lifespan=lifespan)


def _validate_config():
    """启动时检查必要配置"""
    missing = []
    if not Config.APP_ID:
        missing.append("FEISHU_APP_ID")
    if not Config.APP_SECRET:
        missing.append("FEISHU_APP_SECRET")
    if not Config.VERIFICATION_TOKEN:
        missing.append("FEISHU_VERIFICATION_TOKEN")
    if missing:
        logger.warning(
            f"以下配置项未设置: {', '.join(missing)}，"
            "请在 .env 文件中配置"
        )


# ── Webhook 入口 ──────────────────────────────────────────────────
@app.post("/webhook/event")
async def handle_event(request: Request):
    """接收飞书事件回调"""
    body = await request.json()

    # ── 1. URL 验证（首次配置回调地址时飞书会发送） ────────────
    if body.get("type") == "url_verification":
        challenge = body.get("challenge", "")
        logger.info("收到 URL 验证请求，返回 challenge")
        return JSONResponse({"challenge": challenge})

    # ── 2. Token 校验 ─────────────────────────────────────────
    # v2.0 事件: token 在 header.token
    # v1.0 事件: token 在 body.token
    header = body.get("header", {})
    token = header.get("token", "") or body.get("token", "")
    if Config.VERIFICATION_TOKEN and token != Config.VERIFICATION_TOKEN:
        logger.warning(f"Token 校验失败: {token}")
        raise HTTPException(status_code=403, detail="Invalid token")

    # ── 3. 解析 v2.0 事件 ─────────────────────────────────────
    event = body.get("event", {})
    event_type = header.get("event_type", "")

    # 只处理消息事件
    if event_type != "im.message.receive_v1":
        return JSONResponse({"code": 0})

    message = event.get("message", {})
    message_id = message.get("message_id", "")
    chat_id = message.get("chat_id", "")
    msg_type = message.get("message_type", "")
    chat_type = message.get("chat_type", "")  # "p2p" 或 "group"

    # 去重
    if message_id in _processed_msgs:
        return JSONResponse({"code": 0})
    _processed_msgs.add(message_id)
    if len(_processed_msgs) > MAX_PROCESSED_CACHE:
        # 简单清理：保留最近一半
        to_remove = list(_processed_msgs)[: MAX_PROCESSED_CACHE // 2]
        for m in to_remove:
            _processed_msgs.discard(m)

    # ── 4. 提取文本内容 ───────────────────────────────────────
    if msg_type == "text":
        try:
            content = json.loads(message.get("content", "{}"))
            text = content.get("text", "")
        except (json.JSONDecodeError, AttributeError):
            text = ""
    else:
        # 非文本消息，忽略
        return JSONResponse({"code": 0})

    if not text.strip():
        return JSONResponse({"code": 0})

    # ── 5. 异步处理（先快速返回 200，避免飞书超时） ───────────
    asyncio.create_task(
        _process_message(chat_id, message_id, text, chat_type)
    )

    return JSONResponse({"code": 0})


# ── 消息处理核心 ──────────────────────────────────────────────────
async def _process_message(
    chat_id: str, message_id: str, text: str, chat_type: str
):
    """
    处理用户消息的完整流程
    """
    try:
        # 提取 URL
        urls = scraper.extract_urls(text)
        if not urls:
            await feishu.reply_text(
                message_id,
                "请发送一个有效的网址链接，我会帮你整理成 PDF 文档。\n"
                "用法：@我 + 网址",
            )
            return

        # 取第一个 URL
        url = urls[0]
        logger.info(f"处理 URL: {url} (来自 chat={chat_id})")

        # 先回复一条提示
        await feishu.reply_text(message_id, f"正在处理: {url}\n请稍候，正在抓取网页内容...")

        # 判断是否需要浏览器渲染
        pdf_path = None
        title = ""

        if scraper.needs_browser(url):
            # JS 动态页面：用 Playwright 直接生成 PDF
            try:
                result = await scraper.fetch_with_browser(url)
                title = result["title"]
                pdf_path = result.get("pdf_path")
                logger.info(f"浏览器模式完成: {title}")
            except Exception as e:
                logger.exception(f"浏览器渲染失败: {type(e).__name__}: {e}")
                await feishu.reply_text(message_id, f"浏览器渲染失败: {type(e).__name__}: {str(e)[:200]}")
                return
        else:
            # 普通网页：抓取 → Markdown → 生成 PDF
            try:
                result = await scraper.fetch(url)
            except Exception as e:
                logger.exception(f"抓取网页失败: {type(e).__name__}: {e}")
                await feishu.reply_text(message_id, f"抓取网页失败: {type(e).__name__}: {str(e)[:200]}")
                return

            title = result["title"]
            markdown = result["markdown"]
            logger.info(f"网页已抓取: {title} ({len(markdown)} 字符)")

            try:
                pdf_path = pdf_gen.generate_to_temp(markdown, title, url)
            except Exception as e:
                logger.exception(f"生成 PDF 失败: {e}")
                await feishu.reply_text(message_id, f"生成 PDF 失败: {str(e)[:200]}")
                return

        # 上传并发送
        try:
            success = await feishu.send_pdf_to_chat(chat_id, pdf_path, title)
            if success:
                await feishu.reply_text(
                    message_id,
                    f"已将「{title}」整理为 PDF 文档，请查看上方文件。",
                )
            else:
                await feishu.reply_text(
                    message_id,
                    "PDF 已生成，但发送到会话时出错，请联系管理员。",
                )
        finally:
            # 清理临时文件
            try:
                os.unlink(pdf_path)
            except OSError:
                pass

    except Exception as e:
        logger.exception(f"处理消息异常: {e}")
        try:
            await feishu.reply_text(message_id, f"处理出错: {str(e)[:200]}")
        except Exception:
            pass


# ── 健康检查 ──────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "feishu-url-to-pdf"}


@app.get("/")
async def index():
    return {
        "service": "Feishu URL-to-PDF Bot",
        "version": "1.0.1",
        "endpoints": {
            "webhook": "/webhook/event",
            "health": "/health",
        },
    }
