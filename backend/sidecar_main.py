from __future__ import annotations

import argparse
import importlib
import importlib.abc
import importlib.util
import os
import sys

import uvicorn


class _OnnxRuntimeTransformersAliasLoader(importlib.abc.Loader):
    def __init__(self, target_name: str) -> None:
        self.target_name = target_name

    def create_module(self, spec):  # type: ignore[override]
        module = importlib.import_module(self.target_name)
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module) -> None:  # type: ignore[override]
        # Module is already created by importing target_name in create_module.
        return None


class _OnnxRuntimeTransformersAliasFinder(importlib.abc.MetaPathFinder):
    """
    onnxruntime.transformers uses absolute imports like `from fusion_utils import ...`.
    In frozen/sidecar builds these top-level names are not importable by default.
    This finder maps unknown top-level names to onnxruntime.transformers.<name>.
    """

    def find_spec(self, fullname: str, path=None, target=None):  # type: ignore[override]
        if "." in fullname:
            return None
        if fullname in sys.builtin_module_names:
            return None

        alias_name = f"onnxruntime.transformers.{fullname}"
        alias_spec = importlib.util.find_spec(alias_name)
        if alias_spec is None:
            return None

        loader = _OnnxRuntimeTransformersAliasLoader(alias_name)
        return importlib.util.spec_from_loader(fullname, loader, origin=alias_spec.origin)


def install_onnxruntime_transformers_aliases() -> None:
    for finder in sys.meta_path:
        if isinstance(finder, _OnnxRuntimeTransformersAliasFinder):
            return
    sys.meta_path.insert(0, _OnnxRuntimeTransformersAliasFinder())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat UI backend sidecar")
    parser.add_argument("--host", default="127.0.0.1", help="Host bind address")
    parser.add_argument(
        "--port",
        default=8000,
        type=int,
        help="Port bind number",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional path to .env file for backend settings",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.env_file:
        os.environ.setdefault("CHAT_UI_ENV_FILE", args.env_file)

    install_onnxruntime_transformers_aliases()

    # Import app object directly so PyInstaller bundles the backend package.
    from backend.api.main import app as backend_app

    uvicorn.run(
        backend_app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
