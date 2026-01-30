import pathlib
import re

UI_PREFIX = 'UI_EVENT_PREFIX'
TEXT_EVENT = 'text_event'

PROJECT_ROOT = pathlib.Path(__file__).parent / "backend"

TARGET_FILES = [
    PROJECT_ROOT / "api" / "chat.py",
    PROJECT_ROOT / "llm" / "generate.py",
]

def fix_chat_py(text: str) -> str:
    """
    Fix ONLY:
    - yield chunk  --> UI-safe TEXT event
    """
    pattern = re.compile(r"^\s*yield\s+chunk\s*$", re.MULTILINE)

    replacement = (
        "            if chunk.startswith(UI_EVENT_PREFIX):\n"
        "                yield chunk\n"
        "            else:\n"
        "                yield UI_EVENT_PREFIX + json.dumps(\n"
        "                    text_event(chunk)\n"
        "                ) + \"\\n\""
    )

    return pattern.sub(replacement, text)


def fix_generate_py(text: str) -> str:
    """
    Fix ONLY:
    - yield \"\"
    - yield \"Generation failed.\"
    - raw text yields
    """
    fixes = [
        # yield ""
        (
            r'^\s*yield\s+""\s*$',
            '        yield UI_EVENT_PREFIX + json.dumps(text_event("")) + "\\n"',
        ),
        # yield "Generation failed."
        (
            r'^\s*yield\s+"Generation failed\."\s*$',
            '        yield UI_EVENT_PREFIX + json.dumps(text_event("Generation failed.")) + "\\n"',
        ),
    ]

    for pattern, repl in fixes:
        text = re.sub(pattern, repl, text, flags=re.MULTILINE)

    return text


def process_file(path: pathlib.Path):
    original = path.read_text(encoding="utf-8")

    updated = original
    if path.name == "chat.py":
        updated = fix_chat_py(updated)
    elif path.name == "generate.py":
        updated = fix_generate_py(updated)

    if updated != original:
        path.write_text(updated, encoding="utf-8")
        print(f"✅ Fixed: {path}")
    else:
        print(f"⏭️  No changes: {path}")


def main():
    print("🔧 Running UI yield auto-fixer...\n")
    for file in TARGET_FILES:
        if file.exists():
            process_file(file)
        else:
            print(f"⚠️ Missing: {file}")

    print("\n🎯 Done. Run scan_yields.py again to verify.")


if __name__ == "__main__":
    main()
