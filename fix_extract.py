"""Fix _extract_chapter_from_code_block to return None on failure."""
f = 'novel_agent/conversation_loop.py'
with open(f, encoding='utf-8') as fp:
    lines = fp.readlines()

func_start = None
func_end = None
for i, line in enumerate(lines):
    if 'def _extract_chapter_from_code_block' in line:
        func_start = i
    elif func_start is not None and line.startswith('    def '):
        func_end = i
        break

new_func = (
    '    @staticmethod\n'
    '    def _extract_chapter_from_code_block(text: str):\n'
    '        """Extract from chapter code block. Returns None if not found."""\n'
    '        import re\n'
    '        m = re.search(r"```章节\\s*\\n(.*?)```", text, re.DOTALL)\n'
    '        if m:\n'
    '            return m.group(1).strip()\n'
    '        for marker in ("```章节", "```chapter", "``` 章节"):\n'
    '            start = text.find(marker)\n'
    '            if start >= 0:\n'
    '                body_start = text.find("\\n", start) + 1\n'
    '                end = text.find("\\n```", body_start)\n'
    '                if end < 0: end = text.find("```", body_start)\n'
    '                if end > body_start:\n'
    '                    return text[body_start:end].strip()\n'
    '        return None\n'
    '\n'
)

new_lines = lines[:func_start] + [new_func] + lines[func_end:]
with open(f, 'w', encoding='utf-8') as fp:
    fp.writelines(new_lines)
print(f'Fixed: replaced lines {func_start}-{func_end}')
