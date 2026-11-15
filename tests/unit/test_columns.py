from collections.abc import Callable

import pandas as pd
import pytest

from app.core.errors import UserError
from app.services import columns
from app.services.ingest import parse_upload
from tests import fixtures as fx

# (dataset, expected text column, expected date column, expected category column)
SAMPLES: list[tuple[Callable[[], pd.DataFrame], str, str | None, str | None]] = [
    (fx.reviews, "review_text", "date", "product"),
    (fx.tweets, "text", "created_at", "lang"),
    (fx.tickets, "body", "opened", "channel"),
    (fx.survey, "answer", "submitted_on", "department"),
    (fx.comments, "comment", None, None),
]


def mapping_for(frame: pd.DataFrame) -> dict[str, object]:
    # Go through CSV so types are inferred the way they are for a real upload.
    parsed = parse_upload("sample.csv", fx.csv_bytes(frame)).frame
    return columns.detect(columns.describe_columns(parsed), len(parsed))


@pytest.mark.parametrize(("build", "text", "date", "category"), SAMPLES)
def test_detection_on_sample_datasets(
    build: Callable[[], pd.DataFrame], text: str, date: str | None, category: str | None
) -> None:
    mapping = mapping_for(build())
    assert (mapping["text"], mapping["date"], mapping["category"]) == (text, date, category)


def test_candidates_list_the_alternatives() -> None:
    mapping = mapping_for(fx.reviews())
    assert mapping["candidates"] == {
        "text": ["review_text"],
        "date": ["date"],
        "category": ["product", "rating"],
    }


def test_column_kinds() -> None:
    frame = pd.DataFrame(
        {
            "flag": [True, False] * 30,
            "blank": [None] * 60,
            "amount": [i * 1.5 for i in range(60)],
            "email": [f"person{i}@example.test" for i in range(60)],
            "when": pd.date_range("2026-01-01", periods=60),
            "words": [fx.sentence(i) for i in range(60)],
            "team": ["red", "blue", "green"] * 20,
        }
    )
    kinds = {info.name: info.kind for info in columns.describe_columns(frame)}
    assert kinds == {
        "flag": "boolean",
        "blank": "empty",
        "amount": "number",
        "email": "identifier",
        "when": "date",
        "words": "text",
        "team": "category",
    }


def test_numbers_and_codes_are_not_mistaken_for_dates() -> None:
    frame = pd.DataFrame(
        {"code": ["A-12", "B-07", "C-33"] * 5, "version": ["1.2.3", "2.0.1"] * 7 + ["3"]}
    )
    kinds = {info.name: info.kind for info in columns.describe_columns(frame)}
    assert "date" not in kinds.values()


def test_day_first_dates_are_read_consistently() -> None:
    parsed = columns.parse_dates(pd.Series(["05/01/2026", "13/01/2026", "31/01/2026"], dtype="str"))
    assert parsed.dt.month.tolist() == [1, 1, 1]
    assert parsed.dt.day.tolist() == [5, 13, 31]


def test_short_texts_are_still_offered_as_text() -> None:
    frame = pd.DataFrame({"id": range(40), "reply": [f"Love it {i}" for i in range(40)]})
    mapping = columns.detect(columns.describe_columns(frame), len(frame))
    assert mapping["text"] == "reply"


def test_no_text_column_at_all() -> None:
    frame = pd.DataFrame({"a": range(20), "b": [i / 3 for i in range(20)]})
    mapping = columns.detect(columns.describe_columns(frame), len(frame))
    assert mapping["text"] is None


# --- user override -------------------------------------------------------------


def override(
    frame: pd.DataFrame, text: str, date: str | None = None, category: str | None = None
) -> dict[str, object]:
    infos = columns.describe_columns(frame)
    detected = columns.detect(infos, len(frame))
    return columns.apply_override(frame, infos, detected, text, date, category)


def test_override_accepts_a_valid_choice() -> None:
    mapping = override(fx.tickets(), "subject", "opened", "channel")
    assert (mapping["text"], mapping["date"], mapping["category"]) == (
        "subject",
        "opened",
        "channel",
    )
    assert "candidates" in mapping


def test_override_can_clear_date_and_category() -> None:
    mapping = override(fx.reviews(), "review_text")
    assert mapping["date"] is None and mapping["category"] is None


@pytest.mark.parametrize(
    ("text", "date", "category", "expected"),
    [
        ("missing", None, None, "isn't in this file"),
        ("body", "nope", None, "isn't in this file"),
        ("ticket_id", None, None, "doesn't hold text"),
        ("body", "channel", None, "doesn't look like a date"),
        ("body", None, "ticket_id", "distinct values"),
        ("body", "opened", "opened", "different column for each role"),
        ("body", None, "body", "different column for each role"),
    ],
)
def test_override_rejects_bad_choices(
    text: str, date: str | None, category: str | None, expected: str
) -> None:
    with pytest.raises(UserError) as caught:
        override(fx.tickets(), text, date, category)
    assert expected in caught.value.message
