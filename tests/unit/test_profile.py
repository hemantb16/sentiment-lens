import time
from typing import Any

import pandas as pd

from app.services import columns
from app.services.profile import build_profile
from tests import fixtures as fx


def profile_of(frame: pd.DataFrame) -> dict[str, Any]:
    infos = columns.describe_columns(frame)
    return build_profile(frame, infos, columns.detect(infos, len(frame)))


def test_shape_columns_and_missing_values() -> None:
    frame = fx.reviews(50)
    frame.loc[:9, "product"] = None
    profile = profile_of(frame)
    assert profile["rows"] == 50
    by_name = {column["name"]: column for column in profile["columns"]}
    assert by_name["product"] == {
        "name": "product",
        "kind": "category",
        "missing": 10,
        "missing_pct": 20.0,
        "unique": 3,
    }
    assert by_name["review_text"]["kind"] == "text"
    assert by_name["date"]["kind"] == "date"
    assert by_name["rating"]["kind"] == "number"


def test_duplicates_are_counted() -> None:
    frame = pd.concat([fx.comments(30), fx.comments(30).head(5)], ignore_index=True)
    profile = profile_of(frame)
    assert profile["duplicate_rows"] == 5
    assert profile["text"]["duplicates"] == 5


def test_text_length_stats_and_histogram() -> None:
    texts: list[str | None] = ["a b c " + "x" * n for n in range(1, 101)]
    frame = pd.DataFrame({"comment": [*texts, "", None]})
    text = profile_of(frame)["text"]
    assert text["column"] == "comment"
    assert text["empty"] == 2
    assert text["length"]["min"] == 7 and text["length"]["max"] == 106
    assert text["length"]["median"] == 56.5
    assert len(text["histogram"]) == 12
    counted = sum(bin_["count"] for bin_ in text["histogram"]) + text["longer_than_histogram"]
    assert counted == 100


def test_text_profile_when_every_text_is_blank() -> None:
    frame = pd.DataFrame({"comment": [fx.sentence(i) for i in range(20)]})
    infos = columns.describe_columns(frame)
    mapping = columns.detect(infos, len(frame))
    frame["comment"] = ""
    text = build_profile(frame, infos, mapping)["text"]
    assert text["empty"] == 20 and text["length"] is None and text["histogram"] == []


def test_time_profile_picks_a_sensible_granularity() -> None:
    def time_of(periods: int) -> dict[str, Any]:
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=periods, freq="D").strftime("%Y-%m-%d"),
                "comment": [fx.sentence(i) for i in range(periods)],
            }
        )
        result: dict[str, Any] = profile_of(frame)["time"]
        return result

    days = time_of(30)
    assert days["granularity"] == "day" and len(days["counts"]) == 30
    assert days["start"].startswith("2024-01-01") and days["end"].startswith("2024-01-30")
    assert time_of(200)["granularity"] == "week"
    months = time_of(900)
    assert months["granularity"] == "month"
    assert months["counts"][0] == {"period": "2024-01-01", "count": 31}


def test_time_profile_counts_unreadable_dates() -> None:
    frame = fx.reviews(40)
    frame.loc[:3, "date"] = "not a date"
    time_profile = profile_of(frame)["time"]
    assert time_profile["unreadable"] == 4


def test_category_profile_groups_the_tail() -> None:
    frame = fx.reviews(60)
    category = profile_of(frame)["category"]
    assert category == {
        "column": "product",
        "unique": 3,
        "top": [
            {"value": "Kettle", "count": 20},
            {"value": "Toaster", "count": 20},
            {"value": "Blender", "count": 20},
        ],
        "other": 0,
    }


def test_sections_are_absent_without_matching_columns() -> None:
    profile = profile_of(pd.DataFrame({"a": range(20), "b": [i / 3 for i in range(20)]}))
    assert profile["text"] is None and profile["time"] is None and profile["category"] is None


def test_profile_of_50k_rows_takes_under_three_seconds() -> None:
    frame = fx.tickets(50_000)
    started = time.perf_counter()
    profile = profile_of(frame)
    elapsed = time.perf_counter() - started
    assert profile["rows"] == 50_000
    assert elapsed < 3, f"profiling took {elapsed:.2f}s"
