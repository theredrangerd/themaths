#!/bin/sh
# One-time setup. Run while online.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "Python packages installed."
echo "Make sure your models are pulled, for example:"
echo "  ollama pull qwen3:8b"
echo "  ollama pull gemma4"
echo "Then check everything with: ./run.sh --check"
