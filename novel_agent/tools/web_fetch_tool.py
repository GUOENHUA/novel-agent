"""Web fetch tool — extract content from web pages as markdown.

Borrowed from hermes-agent `tools/web_tools.py`.

Uses httpx to fetch URLs and converts to readable text.
For production use, consider integrating a service like Firecrawl or Jina AI.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from novel_agent.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 10000  # chars per page
REQUEST_TIMEOUT = 20  # seconds per URL


def _is_safe_url(url: str) -> bool:
    """Basic URL safety check."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    # Block local/internal addresses
    hostname = parsed.hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return False
    if hostname.startswith("192.168.") or hostname.startswith("10."):
        return False
    return True


def _html_to_text(html: str) -> str:
    """Ultra-simple HTML to text converter. Removes tags, scripts, styles."""
    # Remove script and style blocks
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Collapse whitespace
    text = re.sub(r"[\r\n]+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


def web_fetch_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle web fetch requests."""
    urls = args.get("urls", [])
    if isinstance(urls, str):
        urls = [urls]
    if not urls:
        return tool_error("urls is required (list of URLs).")

    urls = urls[:5]  # Limit to 5 URLs

    results = []
    for url in urls:
        if not _is_safe_url(url):
            results.append({"url": url, "success": False, "error": "Unsafe URL"})
            continue

        try:
            resp = httpx.get(
                url,
                headers={
                    "User-Agent": "novel-agent/0.1 (research bot; contact@example.com)",
                },
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                text = _html_to_text(resp.text)
            elif "text/plain" in content_type:
                text = resp.text
            else:
                text = _html_to_text(resp.text)  # Best effort

            if len(text) > MAX_CONTENT_LENGTH:
                text = text[:MAX_CONTENT_LENGTH] + f"\n\n...[truncated at {MAX_CONTENT_LENGTH} chars]"

            results.append({
                "url": url,
                "success": True,
                "content": text,
                "content_length": len(text),
                "status_code": resp.status_code,
            })

        except httpx.HTTPError as e:
            logger.warning("Fetch failed for %s: %s", url, e)
            results.append({"url": url, "success": False, "error": str(e)})
        except Exception as e:
            logger.exception("Fetch error for %s", url)
            results.append({"url": url, "success": False, "error": str(e)})

    return tool_result(
        success=True,
        results=results,
        fetched=sum(1 for r in results if r["success"]),
        failed=sum(1 for r in results if not r["success"]),
    )


# -- Tool schema ---------------------------------------------------------------

WEB_FETCH_SCHEMA = {
    "name": "web_fetch",
    "description": (
        "提取网页内容为文本。传入 URL 列表（最多 5 个），返回页面文本内容。\n"
        "自动去除 HTML 标签、脚本和样式，保留正文。\n"
        "每页最多返回 {max_chars} 字符。\n\n"
        "适合查阅百科、参考资料、文献等网页内容。"
    ).format(max_chars=MAX_CONTENT_LENGTH),
    "input_schema": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要提取的 URL 列表（最多 5 个）。",
                "maxItems": 5,
            },
        },
        "required": ["urls"],
    },
}

registry.register(
    name="web_fetch",
    toolset="web",
    schema=WEB_FETCH_SCHEMA,
    handler=web_fetch_tool_handler,
    description="提取网页内容为文本（查阅百科、参考资料）",
    emoji="📄",
)
