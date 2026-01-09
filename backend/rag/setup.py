import subprocess
import sys
import shutil


def install_packages():
    """
    Installs all required Python packages for the RAG pipeline.
    """
    packages = [
        "unstructured[all-docs]",
        "langchain",
        "langchain-community",
        "langchain-text-splitters",
        "pandas",
        "tiktoken",
        "langchain-postgres",
        "langchain-huggingface",
        "psycopg[binary]"
    ]

    print("⏳ Installing Python packages...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install"] + packages
        )
        print("✅ Python packages installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Package installation failed: {e}")
        sys.exit(1)


def verify_system_tools():
    """
    Verifies required system tools (Poppler & Tesseract).
    """
    print("\n🔍 Verifying system tools...")

    required_tools = {
        "tesseract": "https://github.com/UB-Mannheim/tesseract/wiki",
        "pdftoppm": "https://github.com/oschwartz10612/poppler-windows/releases/"
    }

    missing = False

    for tool, url in required_tools.items():
        if shutil.which(tool):
            print(f"   [OK] {tool} found")
        else:
            print(f"   [ERROR] {tool} not found")
            print(f"       Install from: {url}")
            missing = True

    if missing:
        print("\n❌ Missing system dependencies. Install them and rerun setup.py.")
        sys.exit(1)

    print("\n✅ System environment is ready.")


if __name__ == "__main__":
    install_packages()
    verify_system_tools()
