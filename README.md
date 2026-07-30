# 飞书 URL-to-PDF 机器人

在飞书中 @机器人 发送网址，自动抓取网页内容并生成排版精美的 PDF 文档。

## 工作流程

```
用户在飞书 @机器人 + URL
        ↓
机器人接收消息，提取 URL
        ↓
抓取网页正文，自动提取标题和主内容
        ↓
转换为结构化 Markdown
        ↓
生成排版精美的 PDF（支持中文）
        ↓
上传到飞书并回复给用户
```

## 快速开始

### 1. 创建飞书应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)，点击「创建企业自建应用」
2. 填写应用名称（如「网页转PDF」）和描述
3. 记录 **App ID** 和 **App Secret**

### 2. 配置权限

在应用管理页 → 「权限管理」，搜索并开通以下权限：

| 搜索关键词 | 权限 Scope | 说明 |
|-----------|-----------|------|
| 消息 | `im:message` | 收发消息的基础权限 |
| 机器人发送 | `im:message:send_as_bot` | 以机器人身份发送消息 |
| 资源 | `im:resource` | 上传/下载消息中的文件和图片 |
| 群聊 | `im:chat` | 获取群组信息 |

### 3. 配置事件订阅

1. 进入「事件订阅」页面
2. 设置请求地址为：`http://<你的服务器IP>:9000/webhook/event`
3. 记录 **Verification Token**
4. 添加事件：`im.message.receive_v1`（接收消息）
5. 发布应用版本

### 4. 部署服务

```bash
# 克隆/复制项目到服务器
cd feishu-url-to-pdf

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入飞书应用凭证

# 启动服务
python run.py
```

### 5. 暴露到公网（本地开发）

飞书需要 HTTPS 回调地址，本地开发可用 ngrok 或 cloudflare tunnel：

```bash
# 使用 ngrok
ngrok http 9000
# 将生成的 https://xxx.ngrok.io/webhook/event 填入飞书事件订阅地址

# 或使用 cloudflare tunnel
cloudflared tunnel --url http://localhost:9000
```

### 6. 使用

在飞书群聊中 @机器人 + 发送网址即可：

```
@网页转PDF https://example.com/article
```

机器人会自动抓取网页、整理内容、生成 PDF 并发送到群聊中。

## 项目结构

```
feishu-url-to-pdf/
├── app.py              # FastAPI 主应用，处理飞书 Webhook 事件
├── feishu_api.py       # 飞书 API 封装（Token、消息、文件上传）
├── web_scraper.py      # 网页抓取（URL 提取、正文解析、Markdown 转换）
├── pdf_generator.py    # PDF 生成（Markdown → PDF，支持中文排版）
├── config.py           # 配置管理
├── run.py              # 启动脚本
├── requirements.txt    # Python 依赖
├── .env.example        # 环境变量模板
└── README.md           # 本文件
```

## 配置说明

所有配置通过环境变量或 `.env` 文件管理：

| 变量 | 必填 | 说明 |
|------|------|------|
| `FEISHU_APP_ID` | 是 | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 是 | 飞书应用 App Secret |
| `FEISHU_VERIFICATION_TOKEN` | 是 | 事件订阅 Verification Token |
| `FEISHU_ENCRYPT_KEY` | 否 | 事件加密密钥（可选） |
| `HOST` | 否 | 服务监听地址，默认 `0.0.0.0` |
| `PORT` | 否 | 服务端口，默认 `9000` |
| `FONT_PATH` | 否 | 中文字体路径，默认 Windows 微软雅黑 |
| `FONT_BOLD_PATH` | 否 | 中文粗体字体路径 |

## PDF 排版特性

生成的 PDF 支持以下 Markdown 元素：

- 标题层级（H1-H4）
- 正文段落（自动两端对齐）
- 有序/无序列表
- 代码块（等宽字体 + 灰色背景）
- 引用块
- 粗体、斜体、行内代码
- 超链接
- 水平分隔线
- 完整中文支持（微软雅黑字体）

## 生产部署建议

**使用 systemd 管理（Linux）：**

```ini
[Unit]
Description=Feishu URL-to-PDF Bot
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/feishu-url-to-pdf
Environment="PATH=/opt/feishu-url-to-pdf/venv/bin"
ExecStart=/opt/feishu-url-to-pdf/venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**使用 Docker：**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 9000
CMD ["python", "run.py"]
```

## 常见问题

**Q: 字体报错？**
确保 `FONT_PATH` 指向系统中实际存在的中文字体文件。Linux 可安装 `fonts-noto-cjk`。

**Q: 网页内容提取不完整？**
部分网站使用 JavaScript 动态渲染，本工具仅支持静态 HTML 内容。如需支持动态页面，可集成 Playwright。

**Q: 飞书收不到回调？**
检查回调地址是否可从公网访问，确认 HTTPS 证书有效，检查防火墙设置。
