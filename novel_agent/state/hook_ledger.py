"""Hook ledger — foreshadowing lifecycle management.

Borrowed from inkos `packages/core/src/utils/hook-governance.ts`.

Operations:
  upsert   — Plant a new hook or update an existing one
  mention  — Record that a hook was referenced in a chapter
  resolve  — Mark a hook as paid off
  defer    — Push a hook's target resolution to a later chapter

Each hook has a lifecycle: PLANTED → MENTIONED → RESOLVED
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from novel_agent.state.schemas import Hook, HookStatus, HookType
from novel_agent.utils.files import ensure_dir, read_file_safe, write_file_atomic

logger = logging.getLogger(__name__)


class HookLedger:
    """Manages the hook/foreshadowing ledger for a novel."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = ensure_dir(Path(state_dir))
        self.ledger_path = self.state_dir / "hooks.json"

    # -- Load / Save -----------------------------------------------------------

    def load_all(self) -> list[Hook]:
        """Load all hooks from the ledger."""
        raw = read_file_safe(self.ledger_path)
        if raw:
            try:
                data = json.loads(raw)
                hooks = []
                for h in data:
                    # Backwards compat: old hooks may lack scope field
                    if "scope" not in h:
                        h["scope"] = "chapter"
                    hooks.append(Hook.model_validate(h))
                return hooks
            except Exception as e:
                logger.warning("Failed to parse hook ledger: %s", e)
        return []

    def save_all(self, hooks: list[Hook]) -> None:
        """Persist the hook ledger."""
        data = [h.model_dump(exclude_none=True) for h in hooks]
        write_file_atomic(
            self.ledger_path,
            json.dumps(data, ensure_ascii=False, indent=2),
        )

    # -- Operations ------------------------------------------------------------

    def upsert(
        self,
        hook_id: str,
        description: str,
        planted_chapter: int,
        hook_type: str = "direct",
        scope: str = "chapter",
        target_chapter: Optional[int] = None,
        related_characters: Optional[list[str]] = None,
        related_hooks: Optional[list[str]] = None,
    ) -> Hook:
        """Plant a new hook or update an existing one.

        Returns the created/updated Hook.
        """
        from novel_agent.state.schemas import HookScope

        hooks = self.load_all()

        existing = next((h for h in hooks if h.id == hook_id), None)

        if existing:
            existing.description = description
            existing.updated_at = datetime.now().isoformat()
            if target_chapter is not None:
                existing.target_chapter = target_chapter
            hook = existing
        else:
            hook = Hook(
                id=hook_id,
                description=description,
                hook_type=HookType(hook_type),
                scope=HookScope(scope),
                planted_chapter=planted_chapter,
                target_chapter=target_chapter,
                related_characters=related_characters or [],
                related_hooks=related_hooks or [],
            )
            hooks.append(hook)

        self.save_all(hooks)
        return hook

    def mention(self, hook_id: str, chapter_num: int) -> Optional[Hook]:
        """Record that a hook was referenced/advanced in a chapter."""
        hooks = self.load_all()
        hook = next((h for h in hooks if h.id == hook_id), None)

        if hook:
            if hook.status == HookStatus.PLANTED:
                hook.status = HookStatus.MENTIONED
            hook.updated_at = datetime.now().isoformat()
            self.save_all(hooks)
            return hook

        logger.warning("Hook '%s' not found for mention", hook_id)
        return None

    def resolve(self, hook_id: str, chapter_num: int) -> Optional[Hook]:
        """Mark a hook as resolved/paid off in a specific chapter."""
        hooks = self.load_all()
        hook = next((h for h in hooks if h.id == hook_id), None)

        if hook:
            hook.status = HookStatus.RESOLVED
            hook.resolved_chapter = chapter_num
            hook.updated_at = datetime.now().isoformat()
            self.save_all(hooks)
            return hook

        logger.warning("Hook '%s' not found for resolution", hook_id)
        return None

    def defer(self, hook_id: str, new_target_chapter: int) -> Optional[Hook]:
        """Push a hook's expected resolution to a later chapter."""
        hooks = self.load_all()
        hook = next((h for h in hooks if h.id == hook_id), None)

        if hook:
            hook.status = HookStatus.DEFERRED
            hook.target_chapter = new_target_chapter
            hook.updated_at = datetime.now().isoformat()
            self.save_all(hooks)
            return hook

        logger.warning("Hook '%s' not found for deferral", hook_id)
        return None

    # -- Queries ---------------------------------------------------------------

    def get_active(self) -> list[Hook]:
        """Get all unresolved hooks (planted, mentioned, deferred)."""
        return [
            h for h in self.load_all()
            if h.status in {HookStatus.PLANTED, HookStatus.MENTIONED, HookStatus.DEFERRED}
        ]

    def get_resolved(self) -> list[Hook]:
        """Get all resolved hooks."""
        return [h for h in self.load_all() if h.status == HookStatus.RESOLVED]

    def get_by_chapter(self, chapter_num: int) -> list[Hook]:
        """Get hooks planted or resolved in a specific chapter."""
        return [
            h for h in self.load_all()
            if h.planted_chapter == chapter_num or h.resolved_chapter == chapter_num
        ]

    def get_overdue(self, current_chapter: int) -> list[Hook]:
        """Get hooks that should have been resolved by now but aren't."""
        return [
            h for h in self.load_all()
            if h.target_chapter is not None
            and h.target_chapter < current_chapter
            and h.status not in {HookStatus.RESOLVED, HookStatus.ABANDONED}
        ]

    def get_dangling(self) -> list[Hook]:
        """Get resolved hooks whose planting is not recorded (data integrity check)."""
        resolved = self.get_resolved()
        all_ids = {h.id for h in self.load_all()}
        # This is more of a conceptual check — in practice, resolved hooks
        # are always in the ledger. The real check is: resolved but never planted.
        return [h for h in resolved if h.planted_chapter == 0]

    def get_stats(self) -> dict[str, int]:
        """Get summary statistics."""
        hooks = self.load_all()
        return {
            "total": len(hooks),
            "planted": sum(1 for h in hooks if h.status == HookStatus.PLANTED),
            "mentioned": sum(1 for h in hooks if h.status == HookStatus.MENTIONED),
            "resolved": sum(1 for h in hooks if h.status == HookStatus.RESOLVED),
            "deferred": sum(1 for h in hooks if h.status == HookStatus.DEFERRED),
            "active": sum(1 for h in hooks if h.status != HookStatus.RESOLVED),
        }

    def build_report(self, current_chapter: int) -> str:
        """Build a human-readable hook status report."""
        hooks = self.load_all()
        active = self.get_active()
        overdue = self.get_overdue(current_chapter)
        resolved = self.get_resolved()

        parts = [
            "# Hook Ledger Report",
            "",
            f"Total: {len(hooks)} hooks | Active: {len(active)} | Resolved: {len(resolved)}",
            "",
        ]

        if overdue:
            parts.append("## Overdue Hooks")
            for h in overdue:
                parts.append(f"- [{h.id}] {h.description} (target: Ch{h.target_chapter})")
            parts.append("")

        if active:
            parts.append("## Active Hooks")
            for h in active:
                target = f" → Ch{h.target_chapter}" if h.target_chapter else ""
                chars = f" [{', '.join(h.related_characters)}]" if h.related_characters else ""
                parts.append(f"- [{h.id}] {h.status.upper()}: {h.description}{target}{chars}")
            parts.append("")

        if resolved:
            parts.append("## Recently Resolved")
            for h in resolved[-5:]:
                parts.append(f"- [{h.id}] {h.description} (resolved Ch{h.resolved_chapter})")

        return "\n".join(parts)
