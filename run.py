"""
启动脚本
用法: python run.py
"""
import uvicorn
from config import Config

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=Config.HOST,
        port=Config.PORT,
        reload=False,
        log_level="info",
    )
