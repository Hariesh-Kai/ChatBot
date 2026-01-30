import os

# ==============================
# SOURCE DIRECTORIES
# ==============================

FRONTEND_DIR = r"D:\chat-ui\frontend\app"
BACKEND_DIR = r"D:\chat-ui\backend"

# ==============================
# OUTPUT DIRECTORY
# ==============================

OUTPUT_DIR = r"D:\chat-ui\code_dump"
FRONTEND_OUT = os.path.join(OUTPUT_DIR, "frontend")
BACKEND_OUT = os.path.join(OUTPUT_DIR, "backend")

# ==============================
# FILE TYPES TO DUMP
# ==============================

EXTENSIONS = (".py", ".ts", ".tsx")

os.makedirs(FRONTEND_OUT, exist_ok=True)
os.makedirs(BACKEND_OUT, exist_ok=True)


# ==========================================================
# DUMP EACH TOP-LEVEL SUBFOLDER INTO ONE TXT FILE
# ==========================================================

def dump_by_subfolder(source_dir: str, output_base: str):
    for item in os.listdir(source_dir):
        subfolder_path = os.path.join(source_dir, item)

        if not os.path.isdir(subfolder_path):
            continue  # skip files at root level

        output_file = os.path.join(output_base, f"{item}.txt")

        with open(output_file, "w", encoding="utf-8") as txt:
            for root, _, files in os.walk(subfolder_path):
                for file in files:
                    if not file.endswith(EXTENSIONS):
                        continue

                    file_path = os.path.join(root, file)

                    try:
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read().strip()
                    except Exception:
                        continue

                    if not content:
                        continue

                    header = (
                        "\n" + "=" * 60 +
                        f"\nFILE PATH:\n{file_path}\n" +
                        "=" * 60 + "\n\n"
                    )

                    txt.write(header)
                    txt.write(content)
                    txt.write("\n\n")

        print(f"Saved: {output_file}")


# ==============================
# MAIN
# ==============================

def dump_all_code():
    dump_by_subfolder(FRONTEND_DIR, FRONTEND_OUT)
    dump_by_subfolder(BACKEND_DIR, BACKEND_OUT)
    print("🚀 All subfolder code dumped successfully.")


if __name__ == "__main__":
    dump_all_code()
