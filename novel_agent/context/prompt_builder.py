"""System prompt builder — assembles all prompt components with budget control.

Borrowed from inkos context-filter.ts — each section has a character budget.
"""

from __future__ import annotations

from novel_agent.context.token_counter import estimate_tokens


# Context budgets (in estimated tokens)
BUDGET_CRAFT = 8000       # CRAFT.md — always loaded
BUDGET_ANTI_SLOP = 3000   # ANTI_SLOP.md — always loaded
BUDGET_MEMORY = 4000      # Memory system prompt + index
BUDGET_TRUTH_FILES = 3000 # Current state + recent summaries
BUDGET_SKILLS = 2000      # Skills listing
BUDGET_CONVERSATION = 150000  # Conversation history (the bulk)


class PromptBuilder:
    """Assembles the system prompt from components with budget awareness."""

    def __init__(self):
        self._components: dict[str, str] = {}

    def add(self, name: str, content: str) -> None:
        """Add or update a prompt component."""
        self._components[name] = content

    def remove(self, name: str) -> None:
        """Remove a component."""
        self._components.pop(name, None)

    def build(self) -> str:
        """Assemble all components into the final system prompt."""
        parts = []
        for name, content in self._components.items():
            if content and content.strip():
                parts.append(content)
        return "\n\n".join(parts)

    def estimate_total_tokens(self) -> int:
        """Estimate total token count for the assembled prompt."""
        return estimate_tokens(self.build())

    def get_component_tokens(self) -> dict[str, int]:
        """Get token estimates per component."""
        return {name: estimate_tokens(content) for name, content in self._components.items()}
