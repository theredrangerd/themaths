import json
import logging
from collections.abc import AsyncIterator

import httpx

from .config import OLLAMA_URL

log = logging.getLogger("themaths.ollama")

NUM_CTX = 16384
MAX_REPLY_TOKENS = 4096
MAX_THINK_TOKENS = 12288
TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)


class OllamaError(RuntimeError):
    pass


async def list_models() -> list[str]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{OLLAMA_URL}/api/tags")
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))


async def chat(
    model: str,
    messages: list[dict],
    *,
    think: bool = True,
    format: dict | None = None,
    temperature: float | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Stream a chat completion, yielding ("thinking" | "content", text) pairs."""
    # Ollama's default context (4096) is too small for a problem plus its solution, and an
    # uncapped reply can loop forever under a JSON format constraint.
    options: dict = {"num_ctx": NUM_CTX, "num_predict": MAX_THINK_TOKENS if think else MAX_REPLY_TOKENS}
    if temperature is not None:
        options["temperature"] = temperature
    body: dict = {"model": model, "messages": messages, "stream": True, "think": think, "keep_alive": "30m", "options": options}
    if format is not None:
        body["format"] = format
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=body) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode(errors="replace")
                    raise OllamaError(f"Ollama returned {r.status_code}: {detail[:300]}")
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise OllamaError(chunk["error"])
                    if chunk.get("done"):
                        # Per-call timing, so a slow call can be diagnosed from the log.
                        tokens, secs = chunk.get("eval_count", 0), chunk.get("eval_duration", 0) / 1e9
                        log.warning("%s: %d tokens in %.0fs (%.1f/s), prompt %d tokens, total %.0fs, stop=%s",
                                    model, tokens, secs, tokens / secs if secs else 0, chunk.get("prompt_eval_count", 0),
                                    chunk.get("total_duration", 0) / 1e9, chunk.get("done_reason"))
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        yield "thinking", msg["thinking"]
                    if msg.get("content"):
                        yield "content", msg["content"]
    except httpx.ConnectError as exc:
        raise OllamaError("Ollama isn't running. Start it with `ollama serve` or open the Ollama app.") from exc
