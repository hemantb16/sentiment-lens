import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.core.config import settings
from app.ml import registry, train
from app.services import sentiment

_GOOD = ["great", "excellent", "love it", "wonderful", "works perfectly", "happy with it"]
_BAD = ["terrible", "awful", "hate it", "broken", "waste of money", "not good"]


def _corpus(count: int = 240) -> tuple[list[str], train.Labels]:
    texts, labels = [], []
    for i in range(count):
        positive = i % 2 == 0
        words = _GOOD if positive else _BAD
        texts.append(
            f"The item is {words[i % 6]} and the service was {words[(i + 1) % 6]}, order {i}"
        )
        labels.append(int(positive))
    return texts, np.asarray(labels, dtype=np.int64)


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> registry.LoadedModel:
    """A tiny model trained on made-up sentences, saved and loaded like the real one."""
    texts, labels = _corpus()
    pipeline = train.build_pipeline("word", min_df=1).fit(texts, labels)
    metadata = {
        "version": "v1",
        "neutral_band": list(train.NEUTRAL_BAND),
        "training": {"dataset": "synthetic test sentences"},
        "in_domain": {"model": train.evaluate(pipeline, texts, labels)},
        "cross_domain": {"all": {"model": train.evaluate(pipeline, texts, labels)}},
    }
    directory = tmp_path_factory.mktemp("models")
    train.save(pipeline, metadata, directory)
    return registry.load_from(directory)


# --- training helpers --------------------------------------------------------------


def test_training_is_reproducible() -> None:
    texts, labels = _corpus()
    first = train.build_pipeline("word", min_df=1).fit(texts, labels).predict_proba(texts[:20])
    second = train.build_pipeline("word", min_df=1).fit(texts, labels).predict_proba(texts[:20])
    assert np.array_equal(first, second)


def test_word_and_character_features_can_be_combined() -> None:
    texts, labels = _corpus()
    pipeline = train.build_pipeline("word+char", min_df=1).fit(texts, labels)
    names, weights = registry.word_features(pipeline)
    assert len(names) == len(weights) < pipeline.named_steps["classifier"].coef_.shape[1]
    assert "great" in names and all(" " in n or n.isalnum() or "_" in n for n in names[:50])
    with pytest.raises(ValueError, match="unknown feature set"):
        train.build_pipeline("emoji")


def test_evaluate_reports_every_metric(model: registry.LoadedModel) -> None:
    texts, labels = _corpus()
    report = train.evaluate(model.pipeline, texts, labels)
    assert report["rows"] == 240
    assert report["accuracy"] == 1.0 and report["macro_f1"] == 1.0
    assert report["confusion_matrix"] == [[120, 0], [0, 120]]
    assert sum(b["rows"] for b in report["calibration"]["bins"]) == 240
    assert 0 <= report["calibration"]["expected_calibration_error"] < 0.5
    assert report["neutral_band"]["band"] == [0.35, 0.65]


def test_calibration_table_on_known_numbers() -> None:
    probability = np.array([0.05, 0.05, 0.95, 0.95])
    result = train.calibration(probability, np.array([0, 1, 1, 1]))
    assert result["bins"] == [
        {"band": "0.0-0.1", "rows": 2, "mean_predicted": 0.05, "actually_positive": 0.5},
        {"band": "0.9-1.0", "rows": 2, "mean_predicted": 0.95, "actually_positive": 1.0},
    ]
    assert result["expected_calibration_error"] == 0.25


def test_vader_baseline_runs() -> None:
    report = train.evaluate_vader(
        ["I love this, it is great", "I hate this, it is awful"], np.array([1, 0])
    )
    assert report["accuracy"] == 1.0


def test_top_terms_come_from_the_model(model: registry.LoadedModel) -> None:
    terms = train.top_terms(model.pipeline, count=10)
    assert set(terms["positive"]) & {"great", "excellent", "wonderful", "love", "happy"}
    assert set(terms["negative"]) & {"terrible", "awful", "hate", "broken", "waste"}


def test_load_amazon_samples_and_joins_title(tmp_path: Path) -> None:
    table = pa.table(
        {
            "label": [0, 1] * 50,
            "title": [f"Title {i}" if i % 10 else "" for i in range(100)],
            "content": [f"Content {i}" for i in range(100)],
        }
    )
    path = tmp_path / "sample.parquet"
    pq.write_table(table, path)
    texts, labels = train.load_amazon(path, 30, seed=1)
    again, _ = train.load_amazon(path, 30, seed=1)
    assert len(texts) == 30 and texts == again
    assert all(t.startswith(("Title", "Content")) for t in texts)
    assert set(labels.tolist()) == {0, 1}


def test_load_uci_reads_each_domain(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in ("yelp_labelled.txt", "imdb_labelled.txt", "amazon_cells_labelled.txt"):
            archive.writestr(f"folder/{name}", "Good food.\t1\nSlow\tservice.\t0\nno label here\n")
    path = tmp_path / "uci.zip"
    path.write_bytes(buffer.getvalue())
    domains = train.load_uci(path)
    assert len(domains) == 3
    texts, labels = domains["restaurants (Yelp)"]
    assert texts == ["Good food.", "Slow\tservice."] and labels.tolist() == [1, 0]


# --- registry ------------------------------------------------------------------------


def test_registry_refuses_a_model_file_that_was_changed(
    model: registry.LoadedModel, tmp_path: Path
) -> None:
    source = Path(tmp_path.parent) / "tampered"
    source.mkdir(exist_ok=True)
    train.save(model.pipeline, dict(model.metadata), source)
    artefact = source / "sentiment-v1.joblib"
    artefact.write_bytes(artefact.read_bytes() + b"x")
    with pytest.raises(RuntimeError, match="does not match the hash"):
        registry.load_from(source)


def test_shipped_model_loads_and_meets_its_limits() -> None:
    shipped = registry.load()
    directory = Path(settings.model_dir)
    assert (directory / shipped.metadata["artefact"]).stat().st_size < 20_000_000
    assert shipped.metadata["in_domain"]["model"]["macro_f1"] > 0.85
    result = sentiment.score(
        pd.Series(
            [
                "Absolutely love it, works perfectly and arrived early.",
                "Terrible quality, it broke after two days. Complete waste of money.",
            ]
        )
    )
    assert result.rows["label"].tolist() == ["positive", "negative"]


# --- scoring --------------------------------------------------------------------------


def test_scores_labels_and_confidence(model: registry.LoadedModel) -> None:
    texts = pd.Series(
        ["The item is great and wonderful", "The item is terrible and broken", "order"],
        index=[10, 20, 30],
    )
    rows = sentiment.score(texts, model=model).rows
    assert rows.index.tolist() == [10, 20, 30]
    assert rows["label"].tolist() == ["positive", "negative", "neutral"]
    assert rows["positive_probability"][10] > 0.65 > 0.35 > rows["positive_probability"][20]
    assert (rows["confidence"] >= 0.5).all() and (rows["confidence"] <= 1).all()
    assert rows["confidence"][30] < 0.65


def test_terms_explain_each_row(model: registry.LoadedModel) -> None:
    rows = sentiment.score(
        pd.Series(["The item is great and wonderful", "It is not good, a waste of money"]),
        model=model,
    ).rows
    positive_terms = rows["terms"][0]
    assert 1 <= len(positive_terms) <= 3
    assert all(t["weight"] > 0 for t in positive_terms)
    assert {t["term"] for t in positive_terms} & {
        "great",
        "wonderful",
        "great and",
        "and wonderful",
    }
    negative_terms = rows["terms"][1]
    assert all(t["weight"] < 0 for t in negative_terms)
    assert not any("_" in t["term"] for t in negative_terms)  # shown in plain words


def test_empty_and_non_english_rows_are_skipped_not_scored(model: registry.LoadedModel) -> None:
    texts = pd.Series(
        ["The item is great", None, "   ", "Das Produkt ist sehr gut und ich bin zufrieden"],
        dtype="str",
    )
    result = sentiment.score(texts, model=model)
    assert result.rows["skipped"].tolist() == [None, "empty", "empty", "not English"]
    assert result.rows["label"].tolist()[1:] == [None, None, None]
    assert result.rows["positive_probability"].isna().tolist() == [False, True, True, True]
    assert result.summary["skipped"] == {"empty": 2, "not English": 1}
    assert result.summary["rows"] == 4 and result.summary["scored"] == 1


def test_summary_and_drivers(model: registry.LoadedModel) -> None:
    texts, _ = _corpus(120)
    result = sentiment.score(pd.Series(texts), batch_size=50, model=model)
    assert result.summary["counts"] == {"positive": 60, "neutral": 0, "negative": 60}
    assert result.summary["share"]["positive"] == 0.5
    assert 0.65 < result.summary["mean_confidence"] <= 1
    top_positive = [d["term"] for d in result.drivers["positive"]]
    top_negative = [d["term"] for d in result.drivers["negative"]]
    assert set(top_positive) & {"great", "excellent", "wonderful"}
    assert set(top_negative) & {"terrible", "awful", "broken"}
    assert all(d["rows"] > 0 and d["weight"] > 0 for d in result.drivers["positive"])
    assert result.model["kind"] == "TF-IDF + logistic regression"
    json.dumps(result.summary), json.dumps(result.drivers), json.dumps(result.model)


def test_batches_give_the_same_answer_and_report_progress(model: registry.LoadedModel) -> None:
    texts = pd.Series(_corpus(60)[0])
    calls: list[tuple[int, int]] = []
    batched = sentiment.score(
        texts, batch_size=25, progress=lambda d, t: calls.append((d, t)), model=model
    )
    whole = sentiment.score(texts, model=model)
    assert calls == [(25, 60), (50, 60), (60, 60)]
    assert (
        batched.rows["positive_probability"].tolist() == whole.rows["positive_probability"].tolist()
    )


def test_nothing_to_score(model: registry.LoadedModel) -> None:
    result = sentiment.score(pd.Series([None, ""], dtype="str"), model=model)
    assert result.summary["scored"] == 0 and result.summary["mean_confidence"] is None
    assert result.drivers == {"positive": [], "negative": []}


@pytest.mark.parametrize(
    ("term", "shown"),
    [
        ("not_good", "not good"),
        ("not not_worth", "not worth"),
        ("great", "great"),
        ("do not", "do not"),
    ],
)
def test_readable_terms(term: str, shown: str) -> None:
    assert sentiment.readable(term) == shown
