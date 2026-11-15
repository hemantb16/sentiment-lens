"""Describe a dataset: size, columns, missing values, duplicates, text lengths, time spread."""

from typing import Any

import numpy as np
import pandas as pd

from app.services.columns import ColumnInfo, parse_dates

_HISTOGRAM_BINS = 12
_TOP_CATEGORIES = 20


def build_profile(
    frame: pd.DataFrame, infos: list[ColumnInfo], mapping: dict[str, Any]
) -> dict[str, Any]:
    rows = len(frame)
    profile: dict[str, Any] = {
        "rows": rows,
        "columns": [
            {
                "name": info.name,
                "kind": info.kind,
                "missing": info.missing,
                "missing_pct": round(100 * info.missing / rows, 1),
                "unique": info.unique,
            }
            for info in infos
        ],
        "duplicate_rows": int(frame.duplicated().sum()),
        "text": None,
        "time": None,
        "category": None,
    }
    if mapping["text"]:
        profile["text"] = _text_profile(frame[mapping["text"]])
    if mapping["date"]:
        profile["time"] = _time_profile(frame[mapping["date"]])
    if mapping["category"]:
        profile["category"] = _category_profile(frame[mapping["category"]])
    return profile


def _text_profile(series: pd.Series) -> dict[str, Any]:
    text = series.dropna().astype("str").str.strip()
    text = text[text != ""]
    result: dict[str, Any] = {
        "column": str(series.name),
        "empty": int(len(series) - len(text)),
        "duplicates": int(text.duplicated().sum()),
        "length": None,
        "histogram": [],
    }
    if text.empty:
        return result
    lengths = text.str.len().to_numpy(dtype=np.int64)
    result["length"] = {
        "min": int(lengths.min()),
        "median": float(np.median(lengths)),
        "mean": round(float(lengths.mean()), 1),
        "p95": float(np.percentile(lengths, 95)),
        "max": int(lengths.max()),
        "mean_words": round(float(text.str.count(r"\S+").mean()), 1),
    }
    # Bins stop at the 99th percentile so a few very long texts don't flatten the chart.
    upper = max(int(np.percentile(lengths, 99)), 1)
    counts, edges = np.histogram(lengths[lengths <= upper], bins=_HISTOGRAM_BINS, range=(0, upper))
    result["histogram"] = [
        {"from": int(round(edges[i])), "to": int(round(edges[i + 1])), "count": int(counts[i])}
        for i in range(_HISTOGRAM_BINS)
    ]
    result["longer_than_histogram"] = int((lengths > upper).sum())
    return result


def _time_profile(series: pd.Series) -> dict[str, Any]:
    dates = parse_dates(series).dropna()
    result: dict[str, Any] = {
        "column": str(series.name),
        "unreadable": int(series.notna().sum() - len(dates)),
        "start": None,
        "end": None,
        "granularity": None,
        "counts": [],
    }
    if dates.empty:
        return result
    start, end = dates.min(), dates.max()
    span_days = (end - start).days
    granularity, freq = ("day", "D") if span_days <= 62 else ("week", "W")
    if span_days > 730:
        granularity, freq = "month", "M"
    periods = dates.dt.to_period(freq).value_counts().sort_index()
    result.update(
        start=start.isoformat(),
        end=end.isoformat(),
        granularity=granularity,
        counts=[
            {"period": period.start_time.date().isoformat(), "count": int(count)}
            for period, count in periods.items()
        ],
    )
    return result


def _category_profile(series: pd.Series) -> dict[str, Any]:
    counts = series.dropna().astype("str").value_counts()
    top = counts.head(_TOP_CATEGORIES)
    return {
        "column": str(series.name),
        "unique": int(len(counts)),
        "top": [{"value": str(value), "count": int(count)} for value, count in top.items()],
        "other": int(counts.sum() - top.sum()),
    }
