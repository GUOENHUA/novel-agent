"""Web search tool — search the internet for research and reference.

Borrowed from hermes-agent `tools/web_tools.py`.

Uses Tavily Search API (free tier: 1000 queries/month) for structured results.
Falls back to a simple helper message if no API key is configured.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import dotenv
import httpx

from novel_agent.tools.registry import registry, tool_error, tool_result

dotenv.load_dotenv()
logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


def web_search_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle web search requests."""
    query = args.get("query", "").strip()
    if not query:
        return tool_error("query is required.")

    limit = min(args.get("limit", 5), 10)

    if not TAVILY_API_KEY:
        return tool_result(
            success=False,
            error="TAVILY_API_KEY not configured.",
            hint=(
                "Web search requires a Tavily API key. Get one at https://tavily.com "
                "(free tier: 1000 queries/month). Set TAVILY_API_KEY in your .env file."
            ),
        )

    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for r in data.get("results", [])[:limit]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:500],
                "score": r.get("score", 0),
            })

        return tool_result(
            success=True,
            query=query,
            results=results,
            count=len(results),
            answer=data.get("answer", ""),
        )

    except httpx.HTTPError as e:
        logger.error("Web search failed: %s", e)
        return tool_error(f"Search failed: {e}")
    except Exception as e:
        logger.exception("Web search error")
        return tool_error(str(e))


# -- Tool schema ---------------------------------------------------------------

WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": (
        "联网搜索信息，用于考据和资料查询。适合搜索：\n"
        "- 历史、地理、文化、风俗等事实性信息\n"
        "- 专业领域知识（武术、医学、军事、手工艺等）\n"
        "- 神话传说、民间故事、宗教仪式等文化参考\n"
        "- 真实事件/人物作为创作灵感\n\n"
        "返回标题、URL、内容摘要。支持搜索运算符（site:、-排除词等）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询词。可使用 site:domain、-排除词 等运算符。"
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量（默认 5，最大 10）。",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

registry.register(
    name="web_search",
    toolset="web",
    schema=WEB_SEARCH_SCHEMA,
    handler=web_search_tool_handler,
    description="联网搜索（考据、资料查询、文化参考）",
    emoji="🌐",
)
