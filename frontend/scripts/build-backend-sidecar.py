from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def detect_target_triple() -> str:
    explicit = sys.argv[1] if len(sys.argv) > 1 else ""
    if explicit:
        return explicit

    env_target = (
        os.environ.get("TAURI_TARGET")
        or os.environ.get("CARGO_BUILD_TARGET")
        or os.environ.get("RUST_TARGET")
    )
    if env_target:
        return env_target

    try:
        result = subprocess.run(
            ["rustc", "-vV"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        # Allow sidecar build even when rustc isn't on PATH.
        if sys.platform == "win32":
            machine = platform.machine().lower()
            if machine in ("arm64", "aarch64"):
                return "aarch64-pc-windows-msvc"
            if machine in ("x86", "i386", "i686"):
                return "i686-pc-windows-msvc"
            return "x86_64-pc-windows-msvc"
        raise RuntimeError(
            "Unable to detect Rust target triple. Install Rust or pass the "
            "target triple as the first script argument."
        ) from exc

    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.split("host: ", 1)[1].strip()

    raise RuntimeError("Could not parse target triple from `rustc -vV` output.")


def build_sidecar(repo_root: Path, target_triple: str) -> Path:
    tauri_dir = repo_root / "frontend" / "src-tauri"
    binaries_dir = tauri_dir / "binaries"
    suffix = (
        ".exe"
        if target_triple.endswith("windows-msvc") or target_triple.endswith("windows-gnu")
        else ""
    )
    target_binary = binaries_dir / f"chat-ui-backend-{target_triple}{suffix}"

    temp_dir = tauri_dir / ".sidecar-build"
    dist_dir = temp_dir / "dist"
    work_dir = temp_dir / "work"
    spec_dir = temp_dir / "spec"

    binaries_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    if importlib.util.find_spec("PyInstaller") is None:
        prebuilt = resolve_prebuilt_sidecar()
        if prebuilt is None:
            raise RuntimeError(
                "PyInstaller is required to build a fresh backend sidecar. "
                "Install it with: "
                f"{sys.executable} -m pip install pyinstaller "
                "or pass an explicit binary path with CHAT_UI_SIDECAR_BIN."
            )

        shutil.copy2(prebuilt, target_binary)
        print(
            "PyInstaller not found; using explicit CHAT_UI_SIDECAR_BIN binary: "
            f"{prebuilt}"
        )
        print(f"Sidecar ready: {target_binary}")
        return target_binary

    entrypoint = repo_root / "backend" / "sidecar_main.py"
    if not entrypoint.is_file():
        raise FileNotFoundError(f"Sidecar entrypoint not found: {entrypoint}")

    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--paths",
        str(repo_root),
        "--hidden-import",
        "backend",
        "--collect-submodules",
        "backend",
        "--collect-submodules",
        "transformers",
        "--collect-submodules",
        "sentence_transformers",
        "--collect-submodules",
        "onnxruntime.transformers",
        "--collect-submodules",
        "onnxruntime.quantization",
        "--collect-submodules",
        "onnxruntime.tools",
        "--collect-data",
        "backend",
        "--name",
        "chat-ui-backend",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        str(entrypoint),
    ]

    print("Building backend sidecar with PyInstaller...")
    subprocess.run(pyinstaller_cmd, check=True, cwd=str(repo_root))

    source_binary = dist_dir / (
        "chat-ui-backend.exe" if sys.platform == "win32" else "chat-ui-backend"
    )
    if not source_binary.is_file():
        raise FileNotFoundError(f"PyInstaller output missing: {source_binary}")

    shutil.copy2(source_binary, target_binary)
    print(f"Sidecar ready: {target_binary}")
    return target_binary


def resolve_prebuilt_sidecar() -> Path | None:
    explicit = os.environ.get("CHAT_UI_SIDECAR_BIN")
    if not explicit:
        return None

    candidate = Path(explicit)
    if candidate.is_file():
        return candidate
    raise RuntimeError(
        "CHAT_UI_SIDECAR_BIN was set but file does not exist: "
        f"{candidate}"
    )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    target_triple = detect_target_triple()
    build_sidecar(repo_root, target_triple)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
