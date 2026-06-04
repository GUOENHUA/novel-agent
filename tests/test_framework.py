"""Comprehensive framework test — structure, memory, tools, state, content quality."""
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ============================================================================
# 1. CODE STRUCTURE & IMPORTS
# ============================================================================
def test_imports():
    """Verify all core modules import correctly."""
    errors = []
    modules = [
        "novel_agent.agent",
        "novel_agent.conversation_loop",
        "novel_agent.memory.memory_manager",
        "novel_agent.memory.memory_store",
        "novel_agent.memory.builtin_provider",
        "novel_agent.memory.memory_types",
        "novel_agent.tools.registry",
        "novel_agent.tools.memory_tool",
        "novel_agent.state.truth_files",
        "novel_agent.state.hook_ledger",
        "novel_agent.state.schemas",
        "novel_agent.context.compressor",
        "novel_agent.skills.loader",
        "novel_agent.utils.files",
        "novel_agent.utils.constants",
    ]
    for mod in modules:
        try:
            __import__(mod)
            print(f"  [OK] {mod}")
        except Exception as e:
            errors.append(f"  [FAIL] {mod}: {e}")
            print(f"  [FAIL] {mod}: {e}")
    return errors


def test_tool_registry():
    """Verify all expected tools are registered."""
    from novel_agent.tools.registry import registry
    errors = []
    expected = {
        "outline_plot": ["save", "load"],
        "memory": ["add", "update", "delete", "search"],
        "track_hooks": ["add", "list", "find", "resolve", "status"],
    }
    registered = registry.get_all_tool_names()
    for tool_name, actions in expected.items():
        if tool_name in registered:
            print(f"  [OK] {tool_name}: registered")
        else:
            # outline_plot is loaded dynamically by skills — may not be present in unit test
            print(f"  [WARN] {tool_name}: not registered (loaded dynamically)")
    # At minimum, memory and track_hooks should always be registered
    if "memory" not in registered:
        errors.append("Core tool 'memory' not registered!")
    if "track_hooks" not in registered:
        errors.append("Core tool 'track_hooks' not registered!")
    return errors


# ============================================================================
# 2. MEMORY SYSTEM
# ============================================================================
def test_memory_store():
    """Test memory store CRUD with a temporary directory."""
    import tempfile, shutil
    from novel_agent.memory.memory_store import MemoryStore

    errors = []
    tmp = Path(tempfile.mkdtemp())
    try:
        store = MemoryStore(tmp)

        # Write
        store.write_memory(
            "char-test-hero.md",
            {"name": "Test Hero", "description": "A test character", "type": "character"},
            "This is the hero. Brave and kind.",
        )
        print("  [OK] write_memory")

        # Read
        content = store.read_memory("char-test-hero.md")
        assert content and "Test Hero" in content, "Read content mismatch"
        print("  [OK] read_memory")

        # Index
        store.add_to_index("Test Hero", "char-test-hero.md", "A test character")
        idx = store.read_index()
        assert "Test Hero" in idx, "Index entry missing"
        print("  [OK] add_to_index + read_index")

        # Scan headers
        headers = store.scan_memory_headers()
        assert any(h["filename"] == "char-test-hero.md" for h in headers), "Header missing"
        print("  [OK] scan_memory_headers")

        # List files
        files = store.list_memory_files()
        assert len(files) >= 1, "No files listed"
        print("  [OK] list_memory_files")

        # Delete
        store.delete_memory("char-test-hero.md")
        assert store.read_memory("char-test-hero.md") is None, "Delete failed"
        print("  [OK] delete_memory")

    except Exception as e:
        errors.append(f"Memory store error: {e}")
        print(f"  [FAIL] {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def test_builtin_provider():
    """Test memory add/update/delete/search via BuiltinProvider."""
    import tempfile, shutil
    from novel_agent.memory.builtin_provider import BuiltinProvider
    from novel_agent.memory.memory_store import MemoryStore

    errors = []
    tmp = Path(tempfile.mkdtemp())
    try:
        provider = BuiltinProvider()
        provider.initialize(str(tmp))

        # Add
        result = json.loads(provider.handle_tool_call("memory", {
            "action": "add",
            "type": "character",
            "name": "Test Hero",
            "description": "Main protagonist",
            "content": "A brave warrior from the north.",
        }))
        assert result.get("success"), f"Add failed: {result}"
        print(f"  [OK] memory add: {result.get('filename', '?')}")

        # Update
        result2 = json.loads(provider.handle_tool_call("memory", {
            "action": "update",
            "type": "character",
            "name": "Test Hero",
            "content": "A brave warrior from the north. Updated: has a scar.",
        }))
        assert result2.get("success"), f"Update failed: {result2}"
        print("  [OK] memory update")

        # Search
        # Search by name (works since we search name+description, not content)
        result3 = json.loads(provider.handle_tool_call("memory", {
            "action": "search",
            "query": "protagonist",
        }))
        mems = result3.get("memories", [])
        assert len(mems) >= 1, f"Search failed: {result3}"
        print(f"  [OK] memory search: {len(mems)} results (note: searches name+desc only, not full-text)")

        # Delete
        result4 = json.loads(provider.handle_tool_call("memory", {
            "action": "delete",
            "name": "Test Hero",
        }))
        assert result4.get("success"), f"Delete failed: {result4}"
        print("  [OK] memory delete")

    except Exception as e:
        errors.append(f"Provider error: {e}")
        print(f"  [FAIL] {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


# ============================================================================
# 3. TOOL DISPATCH
# ============================================================================
def test_tool_dispatch():
    """Test tool registry dispatch for core tools."""
    from novel_agent.tools.registry import registry
    from novel_agent.memory.builtin_provider import BuiltinProvider
    from novel_agent.tools.memory_tool import set_provider
    import tempfile, shutil
    from pathlib import Path

    errors = []
    tmp = Path(tempfile.mkdtemp())

    # Initialize memory provider before dispatching
    provider = BuiltinProvider()
    provider.initialize(str(tmp))
    set_provider(provider)

    # Test memory dispatch
    result = registry.dispatch(
        "memory",
        {"action": "add", "type": "plot", "name": "Hero's Journey",
         "description": "Main plot arc", "content": "The hero leaves home..."},
        chapters_dir=str(tmp / "chapters"),
        project_dir=str(tmp),
    )
    parsed = json.loads(result)
    if parsed.get("success"):
        print(f"  [OK] memory dispatch: {parsed.get('filename', '?')}")
    else:
        print(f"  [FAIL] memory dispatch: {parsed}")
        errors.append(f"Memory dispatch failed: {parsed}")

    # Test outline dispatch (may fail if outline_plot toolset not loaded)
    outline_dir = tmp / "outline"
    outline_dir.mkdir(parents=True, exist_ok=True)
    result2 = registry.dispatch(
        "outline_plot",
        {"action": "save", "content": "# Test Outline\n## Act 1\nHero begins journey."},
        chapters_dir=str(tmp / "chapters"),
        project_dir=str(tmp),
    )
    parsed2 = json.loads(result2)
    if parsed2.get("success"):
        print(f"  [OK] outline dispatch: saved")
    else:
        # outline_plot is loaded dynamically — may not be registered in unit test
        print(f"  [WARN] outline dispatch not available (loaded dynamically): {parsed2.get('error', '?')}")

    shutil.rmtree(tmp, ignore_errors=True)
    return errors


# ============================================================================
# 4. STATE MANAGEMENT
# ============================================================================
def test_state_management():
    """Test truth files and hook ledger."""
    import tempfile, shutil
    from pathlib import Path
    from novel_agent.state.truth_files import TruthFileManager
    from novel_agent.state.hook_ledger import HookLedger

    errors = []
    tmp = Path(tempfile.mkdtemp())
    try:
        # Truth files
        tfm = TruthFileManager(tmp)
        state = tfm.load_state()
        # Default chapter 0 = no chapters written yet. This is correct behavior.
        assert state.current_chapter == 0, f"Default chapter should be 0, got {state.current_chapter}"
        print(f"  [OK] truth_files: default state OK (chapter={state.current_chapter})")

        state.current_chapter = 5
        tfm.save_state(state)
        state2 = tfm.load_state()
        assert state2.current_chapter == 5, f"State persistence failed: {state2.current_chapter}"
        print("  [OK] truth_files: save/load round-trip")

        # Hook ledger
        hl = HookLedger(tmp)
        hook = hl.upsert(
            hook_id="hook-test-001",
            description="A mysterious stranger appears at the inn",
            planted_chapter=2,
            hook_type="direct",
            target_chapter=10,
        )
        assert hook.id == "hook-test-001", f"Hook upsert failed: {hook}"
        print(f"  [OK] hook_ledger: upsert (id={hook.id})")

        hooks = hl.load_all()
        assert len(hooks) >= 1, "Hook list empty after upsert"
        print(f"  [OK] hook_ledger: load_all ({len(hooks)} hooks)")

        # Mention the hook
        hl.mention("hook-test-001", chapter_num=5)
        hooks2 = hl.load_all()
        mentioned = next(h for h in hooks2 if h.id == "hook-test-001")
        assert mentioned.status.value == "mentioned", f"Expected mentioned status, got {mentioned.status}"
        print(f"  [OK] hook_ledger: mention (status={mentioned.status.value})")

        # Resolve the hook
        hl.resolve("hook-test-001", chapter_num=10)
        hooks3 = hl.load_all()
        resolved = next(h for h in hooks3 if h.id == "hook-test-001")
        assert resolved.status.value == "resolved", f"Expected resolved status, got {resolved.status}"
        print(f"  [OK] hook_ledger: resolve (chapter={resolved.resolved_chapter})")

    except Exception as e:
        errors.append(f"State error: {e}")
        print(f"  [FAIL] {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


# ============================================================================
# 5. NOVEL CONTENT QUALITY
# ============================================================================
def analyze_chapter(path: Path) -> dict:
    """Analyze a single chapter for quality metrics."""
    if not path.exists():
        return {"error": f"File not found: {path}"}
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Remove frontmatter (between --- markers)
    # Skip the title header line (e.g., "# 第1章: 醒来")
    body_lines = []
    skip_header = True
    for line in lines:
        if skip_header and (line.startswith("# 第") or line.startswith("# Chapter")):
            skip_header = False
            continue
        if skip_header and line.strip() == "":
            continue
        skip_header = False
        body_lines.append(line)
    body = "\n".join(body_lines).strip()

    chars_total = len(body)
    # Count Chinese characters
    chinese_chars = sum(1 for c in body if '一' <= c <= '鿿')
    # Words (Chinese chars + English words)
    import re
    english_words = len(re.findall(r'[a-zA-Z]+', body))
    word_count = chinese_chars + english_words

    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    paragraph_count = len(paragraphs)
    avg_para_len = chars_total / max(paragraph_count, 1)

    # Dialogue ratio
    dialogue_lines = sum(1 for line in body_lines if line.strip().startswith('"') or line.strip().startswith('「'))
    dialogue_ratio = dialogue_lines / max(len(body_lines), 1)

    # Sensory detail density (check for descriptive markers)
    sensory_markers = ['看', '听', '闻', '摸', '感觉', '光', '声', '味', '冷', '热', '暗', '亮',
                       '颜色', '声音', '气味', '触感', '温度']
    sensory_count = sum(body.count(m) for m in sensory_markers)
    sensory_density = sensory_count / max(word_count, 1) * 1000  # per 1000 words

    # Paragraph length variance (good novels vary rhythm)
    para_lens = [len(p) for p in paragraphs]
    if len(para_lens) > 1:
        avg = sum(para_lens) / len(para_lens)
        variance = sum((l - avg) ** 2 for l in para_lens) / len(para_lens)
        import math
        para_std = math.sqrt(variance)
    else:
        para_std = 0

    return {
        "file": path.name,
        "chars": chars_total,
        "words": word_count,
        "paragraphs": paragraph_count,
        "avg_para_len": round(avg_para_len),
        "para_std": round(para_std),
        "dialogue_ratio": round(dialogue_ratio * 100, 1),
        "sensory_density": round(sensory_density, 1),
    }


def test_content_quality():
    """Analyze existing chapter content for quality metrics."""
    errors = []
    project_dir = Path(__file__).parent.parent / "my-novel"
    chapters_dir = project_dir / "chapters"
    memory_dir = project_dir / "memory"
    outline_dir = project_dir / "outline"

    print("\n  --- Chapter Analysis ---")
    chapter_files = sorted(chapters_dir.glob("ch_*.md"))
    if not chapter_files:
        print("  [WARN] No chapter files found")
        errors.append("No chapter files found — need to run the agent to generate content")
    else:
        for cf in chapter_files:
            metrics = analyze_chapter(cf)
            # Use ASCII-safe filename display
            fname = cf.name.encode('ascii', 'replace').decode('ascii')
            if "error" in metrics:
                print(f"  [FAIL] {cf.name}: {metrics['error']}")
                errors.append(metrics["error"])
            else:
                print(f"  [BOOK] {fname}: {metrics['words']} words, "
                      f"{metrics['paragraphs']} paras, "
                      f"avg para {metrics['avg_para_len']} chars, "
                      f"σ={metrics['para_std']}")
                print(f"     dialogue {metrics['dialogue_ratio']}%, "
                      f"sensory {metrics['sensory_density']}/1k words")

                # Quality checks
                if metrics["words"] < 1500:
                    print(f"     [WARN] Short chapter (<1500 words)")
                if metrics["words"] > 8000:
                    print(f"     [WARN] Very long chapter (>8000 words)")
                if metrics["dialogue_ratio"] < 5:
                    print(f"     [WARN] Very low dialogue ratio — consider adding conversations")
                if metrics["dialogue_ratio"] > 40:
                    print(f"     [WARN] Very high dialogue ratio — may be 'talking heads'")
                if metrics["sensory_density"] < 3:
                    print(f"     [WARN] Low sensory detail — add more sights, sounds, textures")
                if metrics["para_std"] < 30 and metrics["paragraphs"] > 5:
                    print(f"     [WARN] Uniform paragraph length — vary rhythm more")

    # Memory / world-building
    print("\n  --- Memory Check ---")
    memory_files = sorted(memory_dir.glob("*.md")) if memory_dir.exists() else []
    char_files = [f for f in memory_files if f.name.startswith("char-")]
    world_files = [f for f in memory_files if f.name.startswith("world-")]
    plot_files = [f for f in memory_files if f.name.startswith("plot-")]
    style_files = [f for f in memory_files if f.name.startswith("style-")]
    print(f"  Characters: {len(char_files)} | World: {len(world_files)} | "
          f"Plot: {len(plot_files)} | Style: {len(style_files)}")

    if len(char_files) < 3:
        print("  [WARN] Few characters defined — recommend at least protagonist + antagonist + ally")
    if len(world_files) < 1:
        print("  [WARN] No world-building files — add world rules, locations, magic system")

    # Outline
    print("\n  --- Outline Check ---")
    outline_path = outline_dir / "full.md"
    if outline_path.exists():
        outline_text = outline_path.read_text(encoding="utf-8")
        outline_chars = len(outline_text)
        vol_count = outline_text.count("## 第") or outline_text.count("## Volume") or outline_text.count("## 卷")
        print(f"  Outline: {outline_chars} chars, ~{vol_count} volumes/acts")
        if outline_chars < 1000:
            print("  [WARN] Outline is very short — more detail needed for 800-chapter plan")
    else:
        print("  [WARN] No outline found")
        errors.append("Missing outline file")

    # Consistency check
    print("\n  --- Consistency Check ---")
    session_path = project_dir / "session.json"
    if session_path.exists():
        session = json.loads(session_path.read_text(encoding="utf-8"))
        history = session.get("history", [])
        total_text = ""
        for msg in history:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            total_text += block.get("text", "")
        # Check for potential AI-slop words
        slop_words = ["颤栗", "战栗", "喉结滚动", "瞳孔微缩", "嘴角勾起", "沉声",
                       "眸子", "薄唇", "邪魅", "轻笑一声", "冷冷"]
        found_slop = []
        for word in slop_words:
            count = total_text.count(word)
            if count > 5:
                found_slop.append(f"{word}({count})")
        if found_slop:
            print(f"  [WARN] Frequent AI-slop words: {', '.join(found_slop)}")
        else:
            print("  [OK] No excessive AI-slop patterns detected")

        # Check total word count
        all_chars = sum(len(msg.get("content", "")) if isinstance(msg.get("content", ""), str)
                       else sum(len(b.get("text", "")) for b in msg.get("content", [])
                                if isinstance(b, dict) and b.get("type") == "text")
                       for msg in history)
        print(f"  Total generated text: ~{all_chars} chars across {len(history)} messages")

    return errors


# ============================================================================
# MAIN
# ============================================================================
def main():
    print("=" * 60)
    print("NOVEL AGENT FRAMEWORK TEST")
    print("=" * 60)

    all_errors = []

    sections = [
        ("1. Imports", test_imports),
        ("2. Tool Registry", test_tool_registry),
        ("3. Memory Store", test_memory_store),
        ("4. Builtin Provider", test_builtin_provider),
        ("5. Tool Dispatch", test_tool_dispatch),
        ("6. State Management", test_state_management),
        ("7. Content Quality", test_content_quality),
    ]

    for label, func in sections:
        print(f"\n{'─' * 40}")
        print(f"  {label}")
        print(f"{'─' * 40}")
        errors = func()
        all_errors.extend(errors)

    print(f"\n{'=' * 60}")
    if all_errors:
        print(f"  [FAIL] {len(all_errors)} ERROR(S) FOUND:")
        for e in all_errors:
            print(f"     - {e}")
    else:
        print("  [OK] ALL TESTS PASSED")
    print(f"{'=' * 60}")

    return len(all_errors)


if __name__ == "__main__":
    exit(main())
