import os
import re

BACKEND_DIR = "backend"

UI_EVENT_PREFIX = "__UI_EVENT__"

yield_pattern = re.compile(r"^\s*yield\s+(.*)$")

def classify_yield(line: str) -> str:
    if UI_EVENT_PREFIX in line:
        return "SAFE_UI_EVENT"
    if "text_event(" in line:
        return "SAFE_TEXT_EVENT"
    if "error_event(" in line:
        return "SAFE_ERROR_EVENT"
    if "system_message_event(" in line:
        return "SAFE_SYSTEM_EVENT"
    return "❌ RAW_YIELD"

results = []

for root, _, files in os.walk(BACKEND_DIR):
    for file in files:
        if not file.endswith(".py"):
            continue

        path = os.path.join(root, file)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for lineno, line in enumerate(f, start=1):
                match = yield_pattern.search(line)
                if match:
                    classification = classify_yield(match.group(1))
                    results.append(
                        (classification, path, lineno, line.strip())
                    )

# ---- REPORT ----
print("\n==== YIELD SCAN REPORT ====\n")

unsafe = 0
for cls, path, lineno, code in results:
    print(f"[{cls}] {path}:{lineno}")
    print(f"    {code}\n")
    if cls == "❌ RAW_YIELD":
        unsafe += 1

print("==== SUMMARY ====")
print(f"Total yields found: {len(results)}")
print(f"❌ Unsafe yields  : {unsafe}")

if unsafe == 0:
    print("🎉 All yields are UI-safe!")
else:
    print("⚠️  Fix RAW_YIELD occurrences.")
