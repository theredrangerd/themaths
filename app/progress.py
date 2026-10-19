import datetime as dt
from collections import defaultdict

from . import db, taxonomy

MIN_SAMPLE = 3


def _rate(rows: list[dict]) -> float | None:
    return sum(r["outcome"] == "correct" for r in rows) / len(rows) if rows else None


def summary() -> dict:
    rows = db.engaged_attempts()
    counted = [r for r in rows if r["outcome"] != "flagged" and not r["flagged"]]
    tax = taxonomy.load()

    by_topic: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in counted:
        by_topic[(r["track"], r["topic"])].append(r)

    tracks = []
    for track, spec in tax.items():
        topics = []
        for topic in spec["topics"]:
            rs = by_topic.get((track, topic), [])
            solved = [r for r in rs if r["outcome"] == "correct"]
            rate = _rate(rs)
            avg_hint = sum(r["hint_level"] for r in rs) / len(rs) if rs else None
            trend = None
            if len(rs) >= 2 * MIN_SAMPLE:
                half = len(rs) // 2
                trend = _rate(rs[half:]) - _rate(rs[:half])
            topics.append(
                {
                    "topic": topic,
                    "attempted": len(rs),
                    "solved": len(solved),
                    "solved_unaided": sum(r["hint_level"] == 0 for r in solved),
                    "solve_rate": rate,
                    "avg_hint": avg_hint,
                    "trend": trend,
                    "weak": len(rs) >= MIN_SAMPLE and (rate < 0.6 or avg_hint >= 2),
                }
            )
        tracks.append({"track": track, "name": spec["name"], "topics": topics})

    # Problems solved in each of the last 12 weeks, keyed by the Monday each week starts.
    this_monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    weeks = {this_monday - dt.timedelta(weeks=i): 0 for i in range(11, -1, -1)}
    for r in counted:
        if r["outcome"] == "correct":
            d = dt.date.fromtimestamp(r["finished_at"] or r["started_at"])
            monday = d - dt.timedelta(days=d.weekday())
            if monday in weeks:
                weeks[monday] += 1

    solved = [r for r in counted if r["outcome"] == "correct"]
    return {
        "totals": {
            "attempted": len(counted),
            "solved": len(solved),
            "solved_unaided": sum(r["hint_level"] == 0 for r in solved),
            "avg_hint": (sum(r["hint_level"] for r in counted) / len(counted)) if counted else None,
            "flagged": sum(1 for r in rows if r["outcome"] == "flagged" or r["flagged"]),
        },
        "tracks": tracks,
        "weeks": [{"start": k.isoformat(), "solved": v} for k, v in weeks.items()],
    }
