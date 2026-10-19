#!/bin/sh
# Start themaths and open it in the browser. `./run.sh --check` checks offline readiness.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Not set up yet. Run ./setup.sh while online."
  exit 1
fi

if [ "$1" = "--check" ]; then
  exec .venv/bin/python -m app.check
fi

PORT="${PORT:-8737}"
URL="http://127.0.0.1:$PORT"
(sleep 1.5 && open "$URL") &
echo "themaths is running at $URL (Ctrl+C to stop)"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning
