"""Token estimation utilities.

Simple character-based estimation since exact token counting requires
the model's tokenizer. Chinese characters ≈ 1.5 tokens each, English ≈ 0.25 tokens/char.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Rough token count estimation.

    Chinese: ~1.5 tokens per character
    English/other: ~0.25 tokens per character
    """
    chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.25)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a list of messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    total += estimate_tokens(block["text"])
        total += 20  # Role and metadata overhead per message
    return total
