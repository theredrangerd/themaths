import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("THEMATHS_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "themaths.db"
PROMPTS_DIR = ROOT / "prompts"
STATIC_DIR = ROOT / "static"
TAXONOMY_PATH = ROOT / "taxonomy.yaml"

OLLAMA_URL = "http://127.0.0.1:11434"

DEFAULT_SETTINGS = {
    "reasoning_model": "qwen3:8b",
    "vision_model": "gemma4:latest",
    "think": False,
    "retries": 2,
}
