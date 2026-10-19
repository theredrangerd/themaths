import json
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).with_name("sandbox_runner.py")


def run(code: str, timeout: float = 15.0) -> dict:
    """Run verification code in a separate, restricted Python process."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(RUNNER)],
                input=json.dumps({"code": code}),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp,
                env={"PATH": "/usr/bin:/bin", "HOME": tmp},
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "verification timed out"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return {"ok": False, "error": f"verification crashed: {tail[0]}"}
