"""Everything the dashboard shows, with a plain-English takeaway for each section."""

from dataclasses import dataclass
from typing import Any

import pandas as pd
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

from app.core.errors import UserError
from app.services import sentiment
from app.services.columns import parse_dates
from app.services.jobs import Progress
from app.services.profile import build_profile
from app.services.store import Dataset

LABELS = [sentiment.POSITIVE, sentiment.NEUTRAL, sentiment.NEGATIVE]
_EXAMPLES = 50
_EXAMPLE_CHARS = 600
_TOP_CATEGORIES = 20
_MAX_PERIODS = 260
_BIGRAM_ROWS = 20_000


@dataclass
class Analysis:
    payload: dict[str, Any]  # JSON-safe; what the page and the HTML report render
    table: pd.DataFrame  # the uploaded rows plus their scores, for CSV / XLSX export


def analyse(dataset: Dataset, progress: Progress | None = None) -> Analysis:
    text_column = dataset.mapping.get("text")
    if not text_column:
        raise UserError("Choose the column that holds the text before analysing.")
    frame = dataset.frame
    scored = sentiment.score(frame[text_column], progress=progress)
    rows = scored.rows
    summary = scored.summary

    dates = parse_dates(frame[dataset.mapping["date"]]) if dataset.mapping.get("date") else None
    category = (
        frame[dataset.mapping["category"]].astype("str")
        if dataset.mapping.get("category")
        else None
    )
    texts = frame[text_column].astype("str")
    payload = {
        "filename": dataset.filename,
        "mapping": {k: dataset.mapping.get(k) for k in ("text", "date", "category")},
        "warnings": dataset.warnings,
        "summary": {**summary, "takeaway": _summary_takeaway(summary)},
        "profile": build_profile(frame, dataset.infos, dataset.mapping),
        "time": _over_time(dates, rows["label"]) if dates is not None else None,
        "category": _by_category(category, rows["label"]) if category is not None else None,
        "drivers": _drivers(scored, texts, rows["label"]),
        "examples": _examples(texts, rows, dates, category),
        "model": {**scored.model, "takeaway": _model_takeaway(scored.model)},
    }
    return Analysis(payload, _export_table(frame, rows))


def _pct(part: float, whole: float) -> int:
    # Halves round up, as the page does; Python's round() would turn 12.5 into 12.
    return int(100 * part / whole + 0.5) if whole else 0


def _summary_takeaway(summary: dict[str, Any]) -> str:
    scored = summary["scored"]
    if not scored:
        return "No rows could be scored: every row was empty or not in English."
    counts = summary["counts"]
    sentence = (
        f"{_pct(counts['positive'], scored)}% of rows are positive, "
        f"{_pct(counts['negative'], scored)}% are negative and "
        f"{_pct(counts['neutral'], scored)}% show no clear lean."
    )
    skipped = summary["rows"] - scored
    if skipped:
        sentence += f" {skipped:,} of {summary['rows']:,} rows were skipped."
    return sentence


def _model_takeaway(model: dict[str, Any]) -> str:
    return (
        f"Scored with a {model['kind']} model trained on Amazon product reviews. It was right "
        f"{model['test_accuracy']:.1%} of the time on reviews it had not seen and "
        f"{model['cross_domain_accuracy']:.1%} on other kinds of text. {model['limits']} "
        'A row is "neutral" when the model sees no clear lean either way.'
    )


def _period_label(start: pd.Timestamp, granularity: str) -> str:
    if granularity == "month":
        return start.strftime("%B %Y")
    return f"the week of {start.day} {start.strftime('%b %Y')}"


def _over_time(dates: pd.Series, labels: pd.Series) -> dict[str, Any] | None:
    valid = dates.notna() & labels.notna()
    if not valid.any():
        return None
    series: dict[str, list[dict[str, Any]]] = {}
    for granularity, freq in (("week", "W-SUN"), ("month", "M")):
        periods = dates[valid].dt.to_period(freq)
        full = pd.period_range(periods.min(), periods.max(), freq=freq)
        if len(full) > _MAX_PERIODS:
            continue
        table = (
            pd.crosstab(periods, labels[valid])
            .reindex(index=full, columns=LABELS, fill_value=0)
            .astype(int)
        )
        starts = [period.start_time for period in full]
        series[granularity] = [
            {
                "period": start.date().isoformat(),
                "label": _period_label(start, granularity),
                **{name: int(row[name]) for name in LABELS},
                "total": int(row.sum()),
            }
            for start, (_, row) in zip(starts, table.iterrows(), strict=True)
        ]
    if not series:
        return None
    default = "week" if len(series.get("week", [])) in range(2, 41) else "month"
    if default not in series:
        default = next(iter(series))

    points = series[default]
    busy = [p for p in points if p["total"] >= max(5, 0.02 * int(valid.sum()))]
    if len(points) < 2 or not busy:
        takeaway = f"All dated rows fall in one {default}, so there is no trend to show."
    else:
        worst = max(busy, key=lambda p: p["negative"] / p["total"])
        best = max(busy, key=lambda p: p["positive"] / p["total"])
        takeaway = (
            f"Negativity peaks in {worst['label']} "
            f"({_pct(worst['negative'], worst['total'])}% of {worst['total']:,} rows); "
            f"the most positive stretch is {best['label']} "
            f"({_pct(best['positive'], best['total'])}%)."
        )
    return {
        "default": default,
        "series": series,
        "undated": int((~dates.notna() & labels.notna()).sum()),
        "takeaway": takeaway,
    }


def _by_category(category: pd.Series, labels: pd.Series) -> dict[str, Any] | None:
    valid = labels.notna() & category.notna()
    if not valid.any():
        return None
    values = category[valid]
    top = values.value_counts().head(_TOP_CATEGORIES).index
    values = values.where(values.isin(top), "Other")
    table = pd.crosstab(values, labels[valid]).reindex(columns=LABELS, fill_value=0).astype(int)
    groups: list[dict[str, Any]] = [
        {
            "value": str(name),
            **{label: int(row[label]) for label in LABELS},
            "total": int(row.sum()),
            "net": round(float((row["positive"] - row["negative"]) / row.sum()), 4),
        }
        for name, row in table.iterrows()
    ]
    groups.sort(key=lambda g: g["net"], reverse=True)
    solid = [g for g in groups if g["total"] >= 5] or groups
    best, worst = solid[0], solid[-1]
    if len(groups) < 2:
        takeaway = "There is only one category, so there is nothing to compare."
    else:
        takeaway = (
            f"{best['value']} is the most positive "
            f"({_pct(best['positive'], best['total'])}% positive); "
            f"{worst['value']} is the most negative "
            f"({_pct(worst['negative'], worst['total'])}% negative)."
        )
    return {"groups": groups, "takeaway": takeaway}


def _bigrams(texts: list[str]) -> list[dict[str, Any]]:
    """Two-word phrases that appear in the most rows, ignoring filler words."""
    try:
        counter = CountVectorizer(ngram_range=(2, 2), binary=True, min_df=2)
        matrix = counter.fit_transform(texts[:_BIGRAM_ROWS])
    except ValueError:  # too little text to form any phrase twice
        return []
    counts = matrix.sum(axis=0).A1
    names = counter.get_feature_names_out()
    phrases = []
    for i in counts.argsort()[::-1]:
        # Keep only phrases of two real words that stood next to each other.
        if not any(word in ENGLISH_STOP_WORDS for word in str(names[i]).split()):
            phrases.append({"term": str(names[i]), "rows": int(counts[i])})
            if len(phrases) == 10:
                break
    return phrases


def _drivers(scored: sentiment.Scored, texts: pd.Series, labels: pd.Series) -> dict[str, Any]:
    drivers = scored.drivers

    def quoted(label: str) -> str:
        return ", ".join(f'"{d["term"]}"' for d in drivers[label][:3])

    if drivers["positive"] and drivers["negative"]:
        takeaway = (
            f"Positive rows are driven by {quoted('positive')}; "
            f"negative rows by {quoted('negative')}."
        )
    elif drivers["positive"] or drivers["negative"]:
        side = "positive" if drivers["positive"] else "negative"
        takeaway = f"The {side} rows are driven by {quoted(side)}."
    else:
        takeaway = "No terms stand out."
    return {
        "terms": drivers,
        "phrases": {
            name: _bigrams(texts[labels == name].tolist()) for name in ("positive", "negative")
        },
        "takeaway": takeaway,
    }


def _examples(
    texts: pd.Series, rows: pd.DataFrame, dates: pd.Series | None, category: pd.Series | None
) -> list[dict[str, Any]]:
    scored = rows[rows["label"].notna()]
    # Show each distinct text once, or a repeated comment fills the whole table.
    scored = scored[~texts[scored.index].duplicated()]
    distance = (scored["positive_probability"] - 0.5).abs()
    groups = {
        "Most positive": scored["positive_probability"].nlargest(_EXAMPLES).index,
        "Most negative": scored["positive_probability"].nsmallest(_EXAMPLES).index,
        "Least confident": distance.nsmallest(_EXAMPLES).index,
    }
    examples = []
    for group, index in groups.items():
        for i in index:
            row = rows.loc[i]
            when = dates[i] if dates is not None else None
            examples.append(
                {
                    "group": group,
                    "row": int(i) + 1,
                    "text": texts[i][:_EXAMPLE_CHARS],
                    "label": row["label"],
                    "probability": float(row["positive_probability"]),
                    "confidence": float(row["confidence"]),
                    "terms": [t["term"] for t in row["terms"]],
                    "category": None
                    if category is None or pd.isna(category[i])
                    else str(category[i]),
                    "date": None if when is None or pd.isna(when) else when.date().isoformat(),
                }
            )
    return examples


def _export_table(frame: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    table = frame.copy()
    taken = set(table.columns)

    def free(name: str) -> str:
        while name in taken:
            name += "_"
        taken.add(name)
        return name

    table[free("sentiment")] = rows["label"]
    table[free("positive_probability")] = rows["positive_probability"]
    table[free("confidence")] = rows["confidence"]
    table[free("not_scored_because")] = rows["skipped"]
    table[free("top_terms")] = rows["terms"].map(lambda terms: "; ".join(t["term"] for t in terms))
    return table
