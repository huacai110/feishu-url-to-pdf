"""
网页内容抓取模块
负责：从消息中提取 URL、抓取网页正文、转换为 Markdown
对于 JS 动态渲染的页面（如 e签宝），使用 Playwright 浏览器直接生成 PDF
"""
import os
import re
import logging
import tempfile
import httpx
from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as md

logger = logging.getLogger(__name__)

# URL 匹配正则
URL_PATTERN = re.compile(
    r"https?://[^\s<>\"'\)\]）】，。、；：！？\u3000]+"
)


def _find_chromium_executable() -> str:
    """
    在 Playwright 缓存目录中查找 Chromium/Chrome 可执行文件。
    解决 playwright install chromium 下载 headless shell 但路径不匹配的问题。
    """
    cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    if not os.path.isdir(cache_dir):
        return ""  # 回退到默认路径

    # 可能的可执行文件名
    candidate_names = [
        "chrome-headless-shell",
        "chrome",
        "chromium",
        "chrome-headless-shell-linux64",
    ]

    # 搜索所有子目录（不检查执行权限，只要文件存在就用）
    for root, dirs, files in os.walk(cache_dir):
        for name in candidate_names:
            if name in files:
                full_path = os.path.join(root, name)
                if os.path.isfile(full_path):
                    # 确保有执行权限
                    os.chmod(full_path, 0o755)
                    logger.info(f"[Chromium] 找到可执行文件: {full_path}")
                    return full_path

    # 如果没找到精确匹配，尝试找任何 chrome* 文件
    for root, dirs, files in os.walk(cache_dir):
        for f in files:
            if f.startswith("chrome"):
                full_path = os.path.join(root, f)
                if os.path.isfile(full_path):
                    os.chmod(full_path, 0o755)
                    logger.info(f"[Chromium] 兜底找到: {full_path}")
                    return full_path

    logger.warning("[Chromium] 未找到可执行文件，将使用默认路径")
    return ""

# 需要用浏览器渲染的域名/关键词
BROWSER_DOMAINS = [
    "esign.cn",
    "feishu.cn/docx",
    "feishu.cn/wiki",
    "yuque.com",
]

# 常见正文区域的 CSS 选择器（按优先级排列）
CONTENT_SELECTORS = [
    "article",
    "main",
    "[role='main']",
    ".post-content",
    ".article-content",
    ".entry-content",
    ".content",
    ".post-body",
    ".article-body",
    "#content",
    "#article",
    ".markdown-body",       # GitHub
    ".rich_media_content",  # 微信公众号
    ".Post-RichText",       # 知乎
    ".article",
]

# 需要移除的无关元素
NOISE_TAGS = [
    "nav", "footer", "header", "aside",
    "script", "style", "noscript", "iframe",
    "svg", "button", "input", "form",
]
NOISE_CLASSES = [
    "nav", "navbar", "menu", "sidebar", "footer",
    "header", "banner", "ad", "advertisement",
    "comment", "comments", "related", "recommend",
    "share", "social", "toolbar",
    # 更多导航/布局噪音
    "navigation", "breadcrumb", "tab", "tabs",
    "dropdown", "mega-menu", "sub-menu", "main-menu",
    "top-bar", "bottom-bar", "side-bar",
    "mobile-nav", "desktop-nav", "hamburger",
    "carousel", "slider", "swiper",
    "cookie", "consent", "popup", "modal", "overlay",
    "search-bar", "search-box",
]


class WebScraper:
    """网页内容抓取器"""

    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    # ── URL 提取 ──────────────────────────────────────────────────

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        """从文本中提取所有 URL"""
        return URL_PATTERN.findall(text)

    @staticmethod
    def needs_browser(url: str) -> bool:
        """判断 URL 是否需要浏览器渲染（JS 动态页面）"""
        return any(domain in url for domain in BROWSER_DOMAINS)

    async def fetch_with_browser(self, url: str) -> dict:
        """
        用 Playwright 浏览器打开页面，提取合同原始图片并合成 PDF
        适用于 JS 动态渲染的页面（如 e签宝合同预览）

        核心策略：拦截 oss.esign.cn/pdf-service/ 的原始图片 URL，
        直接下载原始高清图片（1190x1684 JPEG），用 PIL 合成 PDF。
        这样完全没有 UI 干扰，画质最好。

        兜底：如果拦截失败，回退到元素级截图。
        :return: {"title": str, "markdown": str, "url": str, "pdf_path": str}
        """
        from playwright.async_api import async_playwright
        from io import BytesIO
        from PIL import Image

        logger.info(f"使用浏览器渲染: {url}")
        pdf_path = None

        async with async_playwright() as p:
            # 自动查找 Chromium 可执行文件路径（解决 headless shell 路径不匹配问题）
            chromium_path = _find_chromium_executable()
            launch_kwargs = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-gpu"],
            }
            if chromium_path:
                launch_kwargs["executable_path"] = chromium_path

            browser = await p.chromium.launch(**launch_kwargs)
            page = await browser.new_page(
                viewport={"width": 1280, "height": 900}
            )

            try:
                logger.info(f"[浏览器] 打开页面: {url}")

                # ── 拦截 oss.esign.cn 的合同图片 URL ──
                oss_image_urls = []  # [(page_num, url), ...]

                async def _capture_oss_images(response):
                    resp_url = response.url
                    if "oss.esign.cn/pdf-service/" in resp_url:
                        ct = response.headers.get("content-type", "")
                        if "image" in ct or "octet-stream" in ct:
                            m = re.search(r'_(\d+)_(\d+)_max', resp_url)
                            if m:
                                page_num = int(m.group(2))
                                oss_image_urls.append((page_num, resp_url))
                                logger.info(f"[拦截] 捕获第 {page_num} 页图片 URL")

                page.on("response", _capture_oss_images)

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                logger.info("[浏览器] 等待 JS 渲染完成")
                await page.wait_for_timeout(5000)

                title = await page.title()
                if not title:
                    title = url

                # ── 获取总页数 ──
                page_info = await page.evaluate("""
                    () => {
                        const docPageNum = document.querySelector('.doc-page-num');
                        if (docPageNum) {
                            const text = docPageNum.textContent?.trim() || '';
                            const m = text.match(/\\d+/);
                            if (m) {
                                const total = parseInt(m[0]);
                                if (total > 0 && total <= 100) {
                                    return { total: total, source: 'doc-page-num' };
                                }
                            }
                        }
                        const allText = document.body?.textContent || '';
                        const m1 = allText.match(/共\\d+份[，,]\\s*(\\d+)份签/);
                        if (m1 && parseInt(m1[1]) > 1 && parseInt(m1[1]) <= 50) {
                            return { total: parseInt(m1[1]), source: '份签' };
                        }
                        const els = document.querySelectorAll('*');
                        for (const el of els) {
                            const text = el.textContent?.trim() || '';
                            const m2 = text.match(/^\\s*(\\d{1,2})\\s*\\/\\s*(\\d{1,2})\\s*$/);
                            if (m2) {
                                const cur = parseInt(m2[1]);
                                const tot = parseInt(m2[2]);
                                if (tot > 1 && tot <= 50 && cur <= tot
                                    && !(cur <= 12 && tot >= 28 && tot <= 31)) {
                                    return { current: cur, total: tot, source: 'slash' };
                                }
                            }
                        }
                        return { total: 1, source: 'default' };
                    }
                """)
                total_pages = page_info.get("total", 1)
                logger.info(f"[浏览器] 检测到合同共 {total_pages} 页 (来源: {page_info.get('source')})")

                # ── 滚动触发懒加载，让所有页的图片都加载出来 ──
                logger.info("[浏览器] 滚动页面触发懒加载...")
                scroll_result = await page.evaluate("""
                    async () => {
                        const pdfView = document.querySelector('.sa-pdf-view');
                        if (!pdfView) return { scrolled: false, reason: 'no sa-pdf-view' };

                        const scrollHeight = pdfView.scrollHeight;
                        const clientHeight = pdfView.clientHeight;
                        let currentScroll = 0;
                        const step = clientHeight || 500;

                        while (currentScroll < scrollHeight) {
                            currentScroll += step;
                            pdfView.scrollTop = currentScroll;
                            await new Promise(r => setTimeout(r, 400));
                        }

                        pdfView.scrollTop = 0;
                        await new Promise(r => setTimeout(r, 1000));

                        const pages = document.querySelectorAll('.sa-pdf-page');
                        return { scrolled: true, pages_after_scroll: pages.length, scrollHeight };
                    }
                """)
                logger.info(f"[浏览器] 滚动结果: {scroll_result}")
                await page.wait_for_timeout(3000)

                # ── 翻页导航：强制加载剩余页面的图片 ──
                # 滚动可能无法触发所有页面的懒加载，需要逐页点击翻页按钮
                captured_pages = set(pn for pn, _ in oss_image_urls)
                logger.info(f"[浏览器] 滚动后已拦截页: {sorted(captured_pages)}, 共 {len(captured_pages)}/{total_pages} 页")

                if len(captured_pages) < total_pages:
                    logger.info("[浏览器] 开始翻页导航，强制加载剩余页面...")
                    for target_page in range(1, total_pages + 1):
                        if target_page in captured_pages:
                            continue

                        # 点击下一页按钮
                        clicked = await page.evaluate("""
                            () => {
                                // 尝试多种翻页按钮选择器
                                const selectors = [
                                    '.placeholder-btn.next-page',
                                    '.next-page',
                                    '[class*="next-page"]',
                                    'i[class*="arrow-right"]',
                                    '[class*="page-next"]',
                                ];
                                for (const sel of selectors) {
                                    const btn = document.querySelector(sel);
                                    if (btn && !btn.className.includes('disabled')
                                        && !btn.className.includes('active-disabled')) {
                                        btn.click();
                                        return sel;
                                    }
                                }
                                // 兜底：找所有箭头图标
                                const arrows = document.querySelectorAll('i[class*="arrow"]');
                                for (const a of arrows) {
                                    if (!a.className.includes('disabled')
                                        && !a.className.includes('active-disabled')) {
                                        a.click();
                                        return 'arrow-fallback';
                                    }
                                }
                                return null;
                            }
                        """)

                        if not clicked:
                            logger.warning(f"[浏览器] 无法翻到第 {target_page} 页，停止翻页")
                            break

                        logger.info(f"[浏览器] 已点击翻页按钮 (选择器: {clicked})，等待第 {target_page} 页加载...")
                        await page.wait_for_timeout(3000)

                        # 检查是否捕获到新页面的图片
                        new_captured = set(pn for pn, _ in oss_image_urls)
                        logger.info(f"[浏览器] 翻页后已拦截页: {sorted(new_captured)}")

                        if target_page in new_captured:
                            logger.info(f"[浏览器] 第 {target_page} 页图片已加载")
                        else:
                            # 再多等一下，有些页面加载较慢
                            await page.wait_for_timeout(2000)
                            new_captured = set(pn for pn, _ in oss_image_urls)
                            if target_page not in new_captured:
                                logger.warning(f"[浏览器] 第 {target_page} 页图片仍未加载")

                    logger.info(f"[浏览器] 翻页导航完成，共拦截到 {len(oss_image_urls)} 个 OSS 图片 URL")

                logger.info(f"[浏览器] 最终共拦截到 {len(oss_image_urls)} 个 OSS 图片 URL")

                page_images = []

                # ── 策略一：下载拦截到的原始图片 ──
                if oss_image_urls:
                    logger.info("[浏览器] 使用 OSS 原始图片下载模式")
                    oss_image_urls.sort(key=lambda x: x[0])
                    seen_pages = set()
                    unique_urls = []
                    for page_num, img_url in oss_image_urls:
                        if page_num not in seen_pages:
                            seen_pages.add(page_num)
                            unique_urls.append((page_num, img_url))

                    logger.info(f"[浏览器] 去重后 {len(unique_urls)} 页图片")

                    async with httpx.AsyncClient(
                        follow_redirects=True, timeout=30
                    ) as client:
                        for page_num, img_url in unique_urls:
                            try:
                                resp = await client.get(img_url)
                                resp.raise_for_status()
                                page_images.append(resp.content)
                                logger.info(f"[浏览器] 第 {page_num} 页图片下载完成 ({len(resp.content)} bytes)")
                            except Exception as e:
                                logger.warning(f"[浏览器] 第 {page_num} 页图片下载失败: {type(e).__name__}: {e}")

                # ── 策略二（兜底）：.sa-pdf-page 元素级截图 ─
                if not page_images:
                    logger.info("[浏览器] OSS 图片不可用，回退到元素级截图模式")
                    sa_pdf_pages = await page.query_selector_all('.sa-pdf-page')
                    logger.info(f"[浏览器] 找到 {len(sa_pdf_pages)} 个 .sa-pdf-page 元素")

                    for idx, el in enumerate(sa_pdf_pages):
                        try:
                            await el.scroll_into_view_if_needed()
                            await page.wait_for_timeout(500)
                            screenshot = await el.screenshot(type="png")
                            page_images.append(screenshot)
                            logger.info(f"[浏览器] 第 {idx + 1} 页元素截图完成 ({len(screenshot)} bytes)")
                        except Exception as e:
                            logger.warning(f"[浏览器] 第 {idx + 1} 个元素截图失败: {type(e).__name__}: {e}")

                # ── 策略三（最终兜底）：隐藏 UI + 全页截图 ──
                if not page_images:
                    logger.info("[浏览器] 元素截图也失败，使用 UI 隐藏 + 全页截图")
                    await page.add_style_tag(content="""
                        .sa-toggle-aside, .sign-right-receiver-wrapper,
                        .sign-right-doc-receiver, .sign-seal-flow-tab,
                        .tab-context, .sign-flow-info, .sa-toggle-block,
                        .sa-toggle-block-right,
                        [class*="sign-page-init-container-top"],
                        [class*="sign-page-init-container-left"],
                        [class*="sign-page-init-container-right"],
                        [class*="preview-header"], [class*="PreviewHeader"],
                        [class*="file-info"], [class*="FileInfo"],
                        [class*="timeline"], [class*="Timeline"],
                        [class*="sign-detail"], [class*="SignDetail"],
                        [class*="sign-right"], [class*="SignRight"],
                        [class*="sign-left-bar"],
                        [class*="page-indicator"], [class*="PageIndicator"],
                        [class*="viewer-toolbar"], [class*="ViewerToolbar"],
                        [class*="viewer-controls"], [class*="toolbar-bottom"],
                        [class*="ToolbarBottom"], [class*="page-nav"],
                        [class*="PageNav"], [class*="zoom-control"],
                        [class*="ZoomControl"], [class*="top-bar"],
                        [class*="TopBar"], [class*="error-banner"],
                        [class*="ErrorBanner"], [class*="reload-banner"],
                        header, nav, aside, footer,
                        [style*="position: fixed"], [style*="position:fixed"] {
                            display: none !important;
                            visibility: hidden !important;
                            width: 0 !important; height: 0 !important;
                            overflow: hidden !important;
                            margin: 0 !important; padding: 0 !important;
                        }
                        body { margin: 0 !important; padding: 0 !important; overflow: hidden !important; }
                    """)
                    await page.wait_for_timeout(1500)

                    prev_screenshot_size = 0
                    duplicate_count = 0
                    for i in range(1, total_pages + 1):
                        if i > 1:
                            clicked = await page.evaluate("""
                                () => {
                                    const nextBtn = document.querySelector('.placeholder-btn.next-page, .next-page, [class*="next-page"]');
                                    if (nextBtn) { nextBtn.click(); return true; }
                                    return false;
                                }
                            """)
                            if not clicked:
                                logger.warning(f"[浏览器] 无法翻到第 {i} 页")
                                break
                            await page.wait_for_timeout(2000)

                        screenshot = await page.screenshot(type="png")
                        page_images.append(screenshot)
                        if len(screenshot) == prev_screenshot_size and i > 1:
                            duplicate_count += 1
                            if duplicate_count >= 2:
                                break
                        else:
                            duplicate_count = 0
                        prev_screenshot_size = len(screenshot)

                if not page_images:
                    raise Exception("未能获取到任何页面内容")

                # ── 合成 PDF ──
                images = []
                for img_data in page_images:
                    img = Image.open(BytesIO(img_data))
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    images.append(img)

                pdf_fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
                os.close(pdf_fd)
                images[0].save(
                    pdf_path,
                    "PDF",
                    save_all=True,
                    append_images=images[1:],
                    resolution=150,
                )
                logger.info(f"合同 PDF 已生成: {pdf_path} ({os.path.getsize(pdf_path) / 1024:.1f} KB, {len(images)} 页)")

                return {
                    "title": title,
                    "markdown": "",
                    "url": url,
                    "pdf_path": pdf_path,
                }
            except Exception as e:
                logger.error(f"[浏览器] 出错: {type(e).__name__}: {e}")
                if pdf_path and os.path.exists(pdf_path):
                    os.unlink(pdf_path)
                raise e
            finally:
                await browser.close()

    # ── 网页抓取 ──────────────────────────────────────────────────

    async def fetch(self, url: str) -> dict:
        """
        抓取网页并返回结构化内容
        :return: {"title": str, "markdown": str, "url": str}
        """
        html = await self._fetch_html(url)
        soup = BeautifulSoup(html, "lxml")

        # 提取标题
        title = self._extract_title(soup, url)

        # 提取正文
        content_el = self._find_main_content(soup)
        if not content_el:
            return {
                "title": title,
                "markdown": f"> 无法提取网页正文，请直接访问: {url}",
                "url": url,
            }

        # 清理噪音
        self._clean_noise(content_el)

        # 转为 Markdown
        markdown_text = md(
            str(content_el),
            heading_style="ATX",
            bullets="-",
            convert=[
                "p", "h1", "h2", "h3", "h4", "h5", "h6",
                "ul", "ol", "li", "pre", "code", "blockquote",
                "strong", "em", "a", "br", "hr", "table",
                "thead", "tbody", "tr", "th", "td", "img",
            ],
        )

        # 清理多余空行
        markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text).strip()

        # ── 内容去重：移除连续重复的段落 ──
        markdown_text = self._deduplicate_content(markdown_text)

        # ── 过滤导航噪音：移除过短的纯链接文字行 ──
        markdown_text = self._filter_nav_noise(markdown_text)

        if len(markdown_text) < 50:
            raw_text = content_el.get_text(strip=True)
            if len(raw_text) > 100:
                markdown_text = self._fallback_text(raw_text)
            else:
                return {
                    "title": title,
                    "markdown": f"> 网页内容过少，可能无法正确提取。请访问原始链接: {url}",
                    "url": url,
                }

        return {"title": title, "markdown": markdown_text, "url": url}

    # ── 内部方法 ──────────────────────────────────────────────────

    async def _fetch_html(self, url: str) -> str:
        """获取网页 HTML"""
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15
        ) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.text

    @staticmethod
    def _extract_title(soup: BeautifulSoup, url: str) -> str:
        """提取网页标题"""
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()

        if soup.title and soup.title.string:
            return soup.title.string.strip()

        from urllib.parse import urlparse
        return urlparse(url).netloc

    @staticmethod
    def _find_main_content(soup: BeautifulSoup):
        """用多策略定位正文区域"""
        for selector in CONTENT_SELECTORS:
            el = soup.select_one(selector)
            if el and len(el.get_text(strip=True)) > 100:
                return el

        body = soup.find("body")
        if body:
            return body
        return None

    @staticmethod
    def _clean_noise(element):
        """移除页面噪音元素"""
        # 移除 HTML 注释
        for comment in element.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        # 移除噪音标签
        for tag in element.find_all(NOISE_TAGS):
            tag.decompose()

        # 移除带导航/布局 role 的元素
        for role_val in ["navigation", "banner", "complementary", "search"]:
            for el in element.find_all(attrs={"role": role_val}):
                el.decompose()

        # 移除带噪音 class 的元素
        for cls in NOISE_CLASSES:
            for el in element.find_all(class_=re.compile(cls, re.I)):
                el.decompose()

        # 移除 aria-label 含导航关键词的元素
        for keyword in ["navigation", "menu", "breadcrumb", "social", "share"]:
            for el in element.find_all(attrs={"aria-label": re.compile(keyword, re.I)}):
                el.decompose()

    @staticmethod
    def _fallback_text(text: str) -> str:
        """纯文本兜底方案"""
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            line = line.strip()
            if line:
                cleaned.append(line)
        return "\n\n".join(cleaned)

    @staticmethod
    def _deduplicate_content(text: str) -> str:
        """
        移除连续重复的段落。
        有些网站有桌面版+手机版两套内容，会导致正文重复。
        策略：按双换行分段，如果连续两段完全相同则只保留一段。
        如果整个后半部分和前半部分高度相似，则截断后半部分。
        """
        paragraphs = re.split(r"\n\n+", text)
        if len(paragraphs) <= 1:
            return text

        # 移除连续重复段落
        deduped = [paragraphs[0]]
        for i in range(1, len(paragraphs)):
            if paragraphs[i].strip() != deduped[-1].strip():
                deduped.append(paragraphs[i])

        # 检测整体重复：如果后一半和前一半高度相似，截断
        if len(deduped) >= 4:
            mid = len(deduped) // 2
            first_half = "\n".join(deduped[:mid])
            second_half = "\n".join(deduped[mid:])
            # 简单的相似度检测：后一半的段落是否大部分出现在前一半中
            second_paras = [p.strip() for p in deduped[mid:] if p.strip()]
            first_paras_set = set(p.strip() for p in deduped[:mid] if p.strip())
            duplicate_ratio = sum(1 for p in second_paras if p in first_paras_set) / max(len(second_paras), 1)
            if duplicate_ratio > 0.5:
                logger.info(f"[去重] 检测到整体内容重复（重复率 {duplicate_ratio:.0%}），截断后半部分")
                deduped = deduped[:mid]

        return "\n\n".join(deduped)

    @staticmethod
    def _filter_nav_noise(text: str) -> str:
        """
        过滤导航噪音行。
        有些网站的导航链接文字会混入正文，特征是：
        - 行很短（< 30 字符）
        - 包含大量链接 [...](...) 格式
        - 不含正常的句子结构
        """
        lines = text.split("\n")
        filtered = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                filtered.append(line)
                continue

            # 计算链接密度
            link_count = len(re.findall(r'\[([^\]]+)\]\([^)]+\)', stripped))
            plain_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', stripped)

            # 如果行很短且链接占比很高，跳过（可能是导航项）
            if len(stripped) < 40 and link_count >= 2:
                link_ratio = link_count / max(len(plain_text.split()) , 1)
                if link_ratio > 0.3:
                    logger.info(f"[过滤] 移除导航噪音行: {stripped[:50]}...")
                    continue

            filtered.append(line)

        result = "\n".join(filtered)
        # 清理因删除产生的多余空行
        result = re.sub(r"\n{3,}", "\n\n", result).strip()
        return result
