import difflib
from functools import lru_cache

import yaml

from .config import TAXONOMY_PATH


@lru_cache(maxsize=1)
def load() -> dict:
    with open(TAXONOMY_PATH) as f:
        return yaml.safe_load(f)


def as_prompt_text() -> str:
    lines = []
    for track, spec in load().items():
        lines.append(f"Track {track} ({spec['name']}); difficulty scale: {', '.join(spec['difficulty'])}")
        for topic, subs in spec["topics"].items():
            lines.append(f"  - {topic}: {', '.join(subs)}")
    return "\n".join(lines)


def _closest(value: str, options: list[str]) -> str | None:
    if not value:
        return None
    lowered = {o.lower(): o for o in options}
    if value.lower() in lowered:
        return lowered[value.lower()]
    match = difflib.get_close_matches(value.lower(), list(lowered), n=1, cutoff=0.5)
    return lowered[match[0]] if match else None


def normalise_tags(track: str, topic: str, subtopic: str, difficulty: str) -> dict:
    """Snap model-supplied tags onto the fixed taxonomy so statistics group cleanly."""
    tax = load()
    track = (track or "").upper()
    if track not in tax:
        track = "AMC" if "AMC" in track else "IB"
    spec = tax[track]

    topic_n = _closest(topic, list(spec["topics"]))
    if topic_n is None:
        # The model may have put a subtopic in the topic field.
        for t, subs in spec["topics"].items():
            if _closest(topic, subs):
                topic_n = t
                break
    topic_n = topic_n or next(iter(spec["topics"]))

    sub_n = _closest(subtopic, spec["topics"][topic_n]) or subtopic or ""
    diff_n = _closest(str(difficulty), spec["difficulty"]) or spec["difficulty"][len(spec["difficulty"]) // 2]
    return {"track": track, "topic": topic_n, "subtopic": sub_n, "difficulty": diff_n}
