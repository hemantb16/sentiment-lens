"""Work out what each column is, and which ones hold the text, the date and a category."""

import warnings
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd
from pandas.api import types as pdt

from app.core.errors import UserError

_TEXT_HINTS = (
    "text",
    "review",
    "comment",
    "content",
    "message",
    "body",
    "tweet",
    "feedback",
    "description",
    "answer",
    "response",
    "note",
    "post",
)
_DATE_HINTS = ("date", "time", "created", "posted", "submitted", "opened", "_at", "_on")
_CATEGORY_HINTS = (
    "category",
    "product",
    "rating",
    "stars",
    "score",
    "label",
    "source",
    "type",
    "channel",
    "region",
    "department",
    "country",
    "brand",
    "segment",
    "topic",
)

# 2026-01-31, 31/01/2026, 31 Jan 2026, January 31, 2026 (a time may follow)
_DATE_SHAPE = (
    r"^\s*(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{2,4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{2,4})"
)
_SAMPLE = 2000
MAX_CATEGORIES = 50


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    kind: str  # empty | boolean | date | number | text | category | identifier
    missing: int
    unique: int
    mean_words: float


def _day_first(series: pd.Series) -> bool:
    """True when a column of 31/01/2026-style dates clearly puts the day first.

    Decided once per column, so 05/01 and 13/01 are never read in different orders.
    A column where every first number is 12 or less stays month-first.
    """
    if not pdt.is_string_dtype(series):
        return False
    firsts = (
        series.dropna()
        .head(_SAMPLE)
        .str.extract(r"^\s*(\d{1,2})[-/.]\d{1,2}[-/.]\d{2,4}")[0]
        .dropna()
    )
    return bool((firsts.astype("int64") > 12).any())


def parse_dates(series: pd.Series) -> pd.Series:
    """Parse a column as timestamps (UTC, then made naive). Unreadable values become NaT."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pandas warns when it has to guess day/month order
        parsed = pd.to_datetime(
            series, errors="coerce", format="mixed", dayfirst=_day_first(series), utc=True
        )
    return parsed.dt.tz_localize(None)


def _is_date_like(series: pd.Series) -> bool:
    if pdt.is_datetime64_any_dtype(series):
        return True
    if not pdt.is_string_dtype(series):
        return False
    sample = series.dropna().head(500)
    if sample.empty or sample.str.match(_DATE_SHAPE).mean() < 0.9:
        return False
    return bool(parse_dates(sample).notna().mean() >= 0.9)


def describe_column(series: pd.Series) -> ColumnInfo:
    name = str(series.name)
    present = series.dropna()
    missing = int(len(series) - len(present))
    if present.empty:
        return ColumnInfo(name, "empty", missing, 0, 0.0)
    unique = int(present.nunique())
    if pdt.is_bool_dtype(series):
        return ColumnInfo(name, "boolean", missing, unique, 0.0)
    if _is_date_like(series):
        return ColumnInfo(name, "date", missing, unique, 0.0)
    if pdt.is_numeric_dtype(series):
        return ColumnInfo(name, "number", missing, unique, 0.0)

    sample = present.head(_SAMPLE).astype("str")
    mean_words = float(sample.str.count(r"\S+").mean())
    if mean_words >= 3:
        kind = "text"
    elif unique <= max(MAX_CATEGORIES, len(present) // 20) and unique < len(present):
        kind = "category"
    else:
        kind = "identifier"
    return ColumnInfo(name, kind, missing, unique, round(mean_words, 2))


def describe_columns(frame: pd.DataFrame) -> list[ColumnInfo]:
    return [describe_column(frame[name]) for name in frame.columns]


def _hinted(name: str, hints: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in hints)


def _can_group(info: ColumnInfo, rows: int) -> bool:
    if info.kind in ("category", "boolean"):
        return 2 <= info.unique <= MAX_CATEGORIES
    # A number with a handful of values (a 1-5 rating) works as a category too.
    return info.kind == "number" and 2 <= info.unique <= 10 and info.unique < rows


def detect(infos: list[ColumnInfo], rows: int) -> dict[str, Any]:
    """Pick the most likely text, date and category columns, plus the alternatives."""
    texts = [i for i in infos if i.kind == "text"]
    texts.sort(
        key=lambda i: i.mean_words * (2 if _hinted(i.name, _TEXT_HINTS) else 1), reverse=True
    )
    if not texts:  # very short texts ("love it") look like labels; still offer them
        texts = [i for i in infos if i.kind in ("category", "identifier")]
        texts.sort(key=lambda i: (_hinted(i.name, _TEXT_HINTS), i.unique), reverse=True)

    dates = [i for i in infos if i.kind == "date"]
    dates.sort(key=lambda i: not _hinted(i.name, _DATE_HINTS))

    text_name = texts[0].name if texts else None
    groups = [i for i in infos if i.name != text_name and _can_group(i, rows)]
    groups.sort(key=lambda i: (not _hinted(i.name, _CATEGORY_HINTS), i.unique))

    return {
        "text": text_name,
        "date": dates[0].name if dates else None,
        "category": groups[0].name if groups else None,
        "candidates": {
            "text": [i.name for i in texts],
            "date": [i.name for i in dates],
            "category": [i.name for i in groups],
        },
    }


def apply_override(
    frame: pd.DataFrame,
    infos: list[ColumnInfo],
    detected: dict[str, Any],
    text: str,
    date: str | None,
    category: str | None,
) -> dict[str, Any]:
    """Check the user's own choice of columns and return the updated mapping."""
    by_name = {info.name: info for info in infos}
    for chosen in (text, date, category):
        if chosen is not None and chosen not in by_name:
            raise UserError("One of the chosen columns isn't in this file.")
    if len({c for c in (text, date, category) if c is not None}) < sum(
        c is not None for c in (text, date, category)
    ):
        raise UserError("Choose a different column for each role.")
    if by_name[text].kind not in ("text", "category", "identifier"):
        raise UserError(f"\"{text}\" doesn't hold text, so it can't be analysed for sentiment.")
    if date is not None and by_name[date].kind != "date":
        if parse_dates(frame[date].dropna().head(500).astype("str")).notna().mean() < 0.5:
            raise UserError(f'"{date}" doesn\'t look like a date column.')
    if category is not None and not 2 <= by_name[category].unique <= MAX_CATEGORIES:
        raise UserError(
            f'"{category}" has {by_name[category].unique:,} distinct values. '
            f"A category column needs between 2 and {MAX_CATEGORIES}."
        )
    return {**detected, "text": text, "date": date, "category": category}


def infos_as_dicts(infos: list[ColumnInfo]) -> list[dict[str, Any]]:
    return [asdict(info) for info in infos]
