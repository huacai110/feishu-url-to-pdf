"""
飞书 API 封装模块
负责：Token 管理、消息发送、文件上传
"""
import time
import logging
import httpx
from config import Config

logger = logging.getLogger(__name__)


class FeishuAPI:
    """飞书开放平台 API 客户端"""

    def __init__(self):
        self.app_id = Config.APP_ID
        self.app_secret = Config.APP_SECRET
        self.base_url = Config.FEISHU_BASE_URL
        self._token: str = ""
        self._token_expires_at: float = 0

    # ── Token 管理 ────────────────────────────────────────────────

    async def _get_tenant_access_token(self) -> str:
        """获取 tenant_access_token，自动缓存并在过期前刷新"""
        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"获取 token 失败: {data}")

        self._token = data["tenant_access_token"]
        self._token_expires_at = now + data.get("expire", 7200)
        logger.info("tenant_access_token 已刷新")
        return self._token

    async def _headers(self) -> dict:
        token = await self._get_tenant_access_token()
        return {"Authorization": f"Bearer {token}"}

    # ── 消息发送 ──────────────────────────────────────────────────

    async def send_text(self, chat_id: str, text: str) -> dict:
        """发送文本消息到指定会话"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers=await self._headers(),
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": f'{{"text":"{text}"}}',
                },
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"发送文本失败: {data}")
            return data

    async def send_file(self, chat_id: str, file_key: str) -> dict:
        """发送文件消息到指定会话"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers=await self._headers(),
                json={
                    "receive_id": chat_id,
                    "msg_type": "file",
                    "content": f'{{"file_key":"{file_key}"}}',
                },
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"发送文件失败: {data}")
            return data

    # ── 文件上传 ──────────────────────────────────────────────────

    async def upload_file(self, file_path: str, file_name: str) -> str:
        """
        上传文件到飞书，返回 file_key
        :param file_path: 本地文件路径
        :param file_name: 上传后显示的文件名
        """
        import os

        file_size = os.path.getsize(file_path)

        async with httpx.AsyncClient(timeout=60) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    f"{self.base_url}/im/v1/files",
                    headers=await self._headers(),
                    data={
                        "file_type": "stream",
                        "file_name": file_name,
                    },
                    files={"file": (file_name, f, "application/pdf")},
                )

        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"上传文件失败: {data}")

        file_key = data["data"]["file_key"]
        logger.info(f"文件已上传: {file_name} → {file_key}")
        return file_key

    # ── 消息回复 ──────────────────────────────────────────────────

    async def reply_text(self, message_id: str, text: str) -> dict:
        """回复指定消息"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/im/v1/messages/{message_id}/reply",
                headers=await self._headers(),
                json={
                    "msg_type": "text",
                    "content": f'{{"text":"{text}"}}',
                },
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"回复消息失败: {data}")
            return data

    async def reply_file(self, message_id: str, file_key: str) -> dict:
        """以回复方式发送文件"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/im/v1/messages/{message_id}/reply",
                headers=await self._headers(),
                json={
                    "msg_type": "file",
                    "content": f'{{"file_key":"{file_key}"}}',
                },
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"回复文件失败: {data}")
            return data

    # ── 便捷方法 ──────────────────────────────────────────────────

    async def send_pdf_to_chat(
        self, chat_id: str, pdf_path: str, title: str
    ) -> bool:
        """
        一站式：上传 PDF 并发送到会话
        :return: 是否成功
        """
        import os

        file_name = f"{title}.pdf"
        try:
            file_key = await self.upload_file(pdf_path, file_name)
            result = await self.send_file(chat_id, file_key)
            return result.get("code") == 0
        except Exception as e:
            logger.exception(f"发送 PDF 到会话失败: {e}")
            return False
