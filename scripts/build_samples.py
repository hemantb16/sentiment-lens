"""Build the two "Try with sample data" files in app/static/samples.

    python scripts/build_samples.py

1. reviews.csv  - 3,000 real sentences from the UCI "Sentiment Labelled Sentences"
   dataset (Kotzias, 2015; CC BY 4.0), with the site each came from. The labels
   are left out: the point is to see what the model makes of them.
2. shop_feedback.csv - 600 made-up comments with a date, a category and a rating.
   Written for this project; no real customer wrote any of them.

Needs the UCI zip, which `python -m app.ml.train` downloads into data/.
"""

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ml import train  # noqa: E402

OUT = ROOT / "app" / "static" / "samples"

_SITES = {"restaurants (Yelp)": "Yelp", "films (IMDb)": "IMDb", "phones (Amazon)": "Amazon"}

_TOPICS = {
    "Delivery": {
        "good": [
            "The parcel arrived a day early and the courier was friendly.",
            "Fast delivery, well packed, nothing damaged.",
            "Tracking was accurate and it came exactly when promised.",
            "Delivered on a Sunday, which I did not expect. Great service.",
        ],
        "bad": [
            "The parcel was late by more than a week and nobody told me.",
            "The box arrived crushed and the item inside was broken.",
            "Tracking said delivered but nothing came. Very disappointed.",
            "The courier left it in the rain. Terrible handling.",
            "Three delivery dates were missed in a row. Unacceptable.",
        ],
        "mixed": [
            "It arrived on time but the box was a bit dented.",
            "Delivery took longer than promised, though the driver was polite.",
        ],
    },
    "Product quality": {
        "good": [
            "Excellent build quality, it feels solid and well made.",
            "Works perfectly and looks even better than in the photos.",
            "I have used it every day for a month and I love it.",
            "Great value for the price, I would buy it again.",
        ],
        "bad": [
            "It stopped working after two weeks. Poor quality.",
            "The material feels cheap and the stitching came apart.",
            "Nothing like the photos. A waste of money.",
            "The battery barely lasts an hour. Useless.",
        ],
        "mixed": [
            "The product is fine but nothing special for the price.",
            "Good design, although the lid does not close properly.",
        ],
    },
    "Support": {
        "good": [
            "Support answered within minutes and solved my problem.",
            "The agent was patient and really helpful. Thank you.",
            "My refund was processed the same day. Impressive.",
        ],
        "bad": [
            "I waited forty minutes on the phone and got no answer.",
            "Support kept sending the same useless reply.",
            "Nobody has responded to my emails. Awful service.",
            "They refused the refund even though the item was faulty.",
        ],
        "mixed": [
            "The agent was kind but could not fix the issue.",
            "It took three emails, but the problem was solved in the end.",
        ],
    },
    "Mobile app": {
        "good": [
            "The app is easy to use and checkout takes seconds.",
            "Love the new order tracking screen, very clear.",
            "Smooth and fast, the best shopping app I have tried.",
        ],
        "bad": [
            "The app crashes every time I open my basket.",
            "Login keeps failing after the update. Frustrating.",
            "Search is slow and the filters do not work.",
        ],
        "mixed": [
            "The app works, but it logs me out too often.",
            "Nice design, although the search could be better.",
        ],
    },
}
_STARS = {"good": (5, 5, 4), "bad": (1, 1, 2), "mixed": (3, 3, 4, 2)}


def reviews() -> list[dict[str, str]]:
    domains = train.load_uci(ROOT / "data" / "uci_sentences" / "sentences.zip")
    rows = [
        {"site": _SITES[domain], "sentence": text}
        for domain, (texts, _) in domains.items()
        for text in texts
    ]
    random.Random(42).shuffle(rows)
    return rows


def shop_feedback(count: int = 600) -> list[dict[str, str]]:
    rng = random.Random(42)
    start = date(2026, 1, 5)
    rows = []
    for _ in range(count):
        day = start + timedelta(days=rng.randrange(0, 175))
        topic = rng.choice(list(_TOPICS))
        bad = 0.22
        if topic == "Delivery" and day.month == 3:
            bad = 0.7  # a rough month for deliveries, so the time chart has a story
        if topic == "Mobile app" and day.month >= 5:
            bad = 0.1
        roll = rng.random()
        mood = "bad" if roll < bad else "mixed" if roll < bad + 0.14 else "good"
        rows.append(
            {
                "date": day.isoformat(),
                "category": topic,
                "rating": str(rng.choice(_STARS[mood])),
                "comment": rng.choice(_TOPICS[topic][mood]),
            }
        )
    rows.sort(key=lambda row: row["date"])
    return rows


def write(name: str, rows: list[dict[str, str]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    write("reviews.csv", reviews())
    write("shop_feedback.csv", shop_feedback())
