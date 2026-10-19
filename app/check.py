"""Offline-readiness check: `./run.sh --check`."""

import asyncio
import importlib
import sys

from . import db, ollama
from .config import STATIC_DIR


def line(ok: bool, text: str) -> bool:
    print(f"  [{'ok' if ok else '!!'}] {text}")
    return ok


async def main() -> int:
    print("themaths offline check")
    ok = True
    for mod in ("fastapi", "uvicorn", "httpx", "sympy", "yaml", "antlr4"):
        try:
            importlib.import_module(mod)
            ok &= line(True, f"Python package {mod}")
        except ImportError:
            ok &= line(False, f"Python package {mod} is missing. Run ./setup.sh while online.")
    for path in ("vendor/katex/katex.min.js", "vendor/marked/marked.min.js", "vendor/dompurify/purify.min.js"):
        ok &= line((STATIC_DIR / path).exists(), f"Frontend file {path}")

    db.init()
    settings = db.get_settings()
    try:
        models = await ollama.list_models()
        line(True, "Ollama is running")
        for role in ("reasoning_model", "vision_model"):
            name = settings[role]
            present = name in models
            ok &= line(present, f"{role.replace('_', ' ').capitalize()} {name}" + ("" if present else f" is not installed. Run: ollama pull {name}"))
    except Exception:
        ok &= line(False, "Ollama is not running. Open the Ollama app or run `ollama serve`.")

    print("Ready to use offline." if ok else "Fix the items marked !! before going offline.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
