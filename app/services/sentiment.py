"""Score a column of text: label, probability, confidence and the terms behind each score."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.ml import registry
from app.ml.language import not_english
from app.ml.preprocess import clean_many

POSITIVE, NEUTRAL, NEGATIVE = "positive", "neutral", "negative"
SKIPPED_EMPTY = "empty"
SKIPPED_LANGUAGE = "not English"
_DRIVERS = 15

Progress = Callable[[int, int], None]


@dataclass
class Scored:
    # One row per input row, same index: label, positive_probability, confidence,
    # skipped, terms. Skipped rows have no label and no numbers.
    rows: pd.DataFrame
    summary: dict[str, Any]
    drivers: dict[str, list[dict[str, Any]]]
    model: dict[str, Any]


def readable(term: str) -> str:
    """Undo the negation marking: "not not_worth" -> "not worth"."""
    words = term.replace("not_", "not ").split()
    return " ".join(
        w for i, w in enumerate(words) if not (w == "not" and i and words[i - 1] == "not")
    )


def score(
    texts: pd.Series,
    batch_size: int = 5000,
    progress: Progress | None = None,
    terms_per_row: int = 3,
    model: registry.LoadedModel | None = None,
) -> Scored:
    model = model or registry.load()
    low, high = model.neutral_band
    features = model.pipeline.named_steps["features"]
    classifier = model.pipeline.named_steps["classifier"]
    weights = classifier.coef_[0]
    names, word_weights = model.word_names, model.word_weights

    text = texts.astype("str").fillna("").str.strip()
    empty = (text == "") | texts.isna()
    foreign = not_english(text) & ~empty
    positions = np.flatnonzero(~(empty | foreign).to_numpy())
    values = text.to_numpy(dtype=object)

    count = len(text)
    probability = np.full(count, np.nan)
    terms: list[list[dict[str, Any]]] = [[] for _ in range(count)]
    # Per class: summed contribution of each term, and how many rows contain it.
    pull = {POSITIVE: np.zeros(len(names)), NEGATIVE: np.zeros(len(names))}
    seen = {POSITIVE: np.zeros(len(names)), NEGATIVE: np.zeros(len(names))}

    for start in range(0, len(positions), batch_size):
        batch = positions[start : start + batch_size]
        matrix = features.transform(clean_many(values[batch])).tocsr()
        margin = matrix @ weights + classifier.intercept_[0]
        batch_probability = 1.0 / (1.0 + np.exp(-margin))
        probability[batch] = batch_probability

        contribution = matrix[:, : len(names)].multiply(word_weights).tocsr()
        for label, mask in (
            (POSITIVE, batch_probability >= high),
            (NEGATIVE, batch_probability <= low),
        ):
            if mask.any():
                chosen = contribution[np.flatnonzero(mask)]
                pull[label] += np.asarray(chosen.sum(axis=0)).ravel()
                seen[label] += np.bincount(chosen.indices, minlength=len(names))

        pointers, columns, data = contribution.indptr, contribution.indices, contribution.data
        for offset, row in enumerate(batch):
            begin, end = pointers[offset], pointers[offset + 1]
            if begin == end:
                continue
            row_data = data[begin:end]
            p = batch_probability[offset]
            # Show the terms that pushed towards the label the row received.
            key = -row_data if p >= high else row_data if p <= low else -np.abs(row_data)
            best = np.argsort(key)[:terms_per_row]
            terms[row] = [
                {
                    "term": readable(str(names[columns[begin + i]])),
                    "weight": round(float(row_data[i]), 3),
                }
                for i in best
                if row_data[i] != 0
            ]
        if progress:
            progress(min(start + batch_size, len(positions)), len(positions))

    scored = ~np.isnan(probability)
    labels = np.full(count, None, dtype=object)
    labels[scored] = NEUTRAL
    labels[scored & (probability >= high)] = POSITIVE
    labels[scored & (probability <= low)] = NEGATIVE
    skipped = np.full(count, None, dtype=object)
    skipped[empty.to_numpy()] = SKIPPED_EMPTY
    skipped[foreign.to_numpy()] = SKIPPED_LANGUAGE

    rows = pd.DataFrame(
        {
            # object dtype keeps "no value" as None rather than NaN
            "label": pd.Series(labels, dtype=object, index=texts.index),
            "positive_probability": np.round(probability, 4),
            # How sure the model is of the side it leans to: 0.5 is a coin toss, 1 is certain.
            "confidence": np.round(np.maximum(probability, 1 - probability), 4),
            "skipped": pd.Series(skipped, dtype=object, index=texts.index),
            "terms": terms,
        },
        index=texts.index,
    )
    return Scored(rows, _summary(rows), _drivers(names, pull, seen), _model_note(model))


def _summary(rows: pd.DataFrame) -> dict[str, Any]:
    scored = rows[rows["label"].notna()]
    total = len(scored)
    counts = scored["label"].value_counts()
    return {
        "rows": len(rows),
        "scored": total,
        "skipped": {reason: int(n) for reason, n in rows["skipped"].value_counts().items() if n},
        "counts": {name: int(counts.get(name, 0)) for name in (POSITIVE, NEUTRAL, NEGATIVE)},
        "share": {
            name: round(float(counts.get(name, 0)) / total, 4) if total else 0.0
            for name in (POSITIVE, NEUTRAL, NEGATIVE)
        },
        "mean_confidence": round(float(scored["confidence"].mean()), 4) if total else None,
    }


def _drivers(
    names: np.ndarray[Any, Any],
    pull: dict[str, np.ndarray[Any, Any]],
    seen: dict[str, np.ndarray[Any, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """The terms that moved this dataset's rows most towards each class."""
    result: dict[str, list[dict[str, Any]]] = {}
    for label, sign in ((POSITIVE, 1), (NEGATIVE, -1)):
        strength = pull[label] * sign
        order = np.argsort(-strength)[:_DRIVERS]
        result[label] = [
            {
                "term": readable(str(names[i])),
                "weight": round(float(strength[i]), 2),
                "rows": int(seen[label][i]),
            }
            for i in order
            if strength[i] > 0
        ]
    return result


def _model_note(model: registry.LoadedModel) -> dict[str, Any]:
    meta = model.metadata
    return {
        "version": meta["version"],
        "kind": "TF-IDF + logistic regression",
        "trained_on": meta["training"]["dataset"],
        "test_accuracy": meta["in_domain"]["model"]["accuracy"],
        "cross_domain_accuracy": meta["cross_domain"]["all"]["model"]["accuracy"],
        "neutral_band": meta["neutral_band"],
        "limits": (
            "Trained on English product reviews. Expect lower accuracy on other kinds of "
            "text, and on sarcasm or mixed opinions."
        ),
    }
