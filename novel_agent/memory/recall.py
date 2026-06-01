"""Smart memory recall via side query.

Borrowed from claude-code `src/memdir/findRelevantMemories.ts`.

Uses a lightweight LLM call to select the most relevant memories
based on the user's current query and memory file headers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SELECT_MEMORIES_PROMPT = """You are selecting memories that will be useful to a novel-writing AI agent as it processes the user's query. You will be given the user's query and a list of available memory files with their names, types, and descriptions.

Return a list of filenames for the memories that will clearly be useful to the agent (up to 5). Only include memories that you are certain will be helpful based on their name, type, and description.
- If you are unsure if a memory will be useful, do not include it. Be selective.
- If there are no memories clearly useful, return an empty list.
- Prioritize memories that are directly relevant to what the user is asking about.
"""


async def find_relevant_memories(
    client: Any,
    model: str,
    query: str,
    all_memories: list[dict[str, str]],
    limit: int = 5,
) -> list[str]:
    """Select the most relevant memory filenames for a query.

    Args:
        client: Anthropic client instance.
        model: Model name to use for selection.
        query: The user's current query.
        all_memories: List of memory headers (from store.scan_memory_headers()).
        limit: Max number of memories to select.

    Returns:
        List of filenames of the most relevant memories.
    """
    if not all_memories:
        return []

    # Build manifest
    manifest_lines = []
    for m in all_memories:
        manifest_lines.append(
            f"- [{m['type']}] {m['name']}: {m['description']} "
            f"(file: {m['filename']})"
        )
    manifest = "\n".join(manifest_lines)

    valid_filenames = {m["filename"] for m in all_memories}

    try:
        response = client.messages.create(
            model=model,
            max_tokens=256,
            temperature=0,
            system=SELECT_MEMORIES_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Query: {query}\n\nAvailable memories:\n{manifest}",
            }],
        )

        text = response.content[0].text if response.content else ""
        # Parse filenames from response
        selected = _parse_filenames(text, valid_filenames)
        return selected[:limit]

    except Exception as e:
        logger.warning("Memory recall side-query failed: %s", e)
        return []


def _parse_filenames(text: str, valid: set[str]) -> list[str]:
    """Extract filenames from the LLM response. Tries JSON first, then markdown."""
    # Try JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "memories" in data:
            return [f for f in data["memories"] if f in valid]
        if isinstance(data, dict) and "selected_memories" in data:
            return [f for f in data["selected_memories"] if f in valid]
    except json.JSONDecodeError:
        pass

    # Fallback: extract filenames from text
    import re
    found = re.findall(r'[\w-]+\.md', text)
    return [f for f in found if f in valid]
