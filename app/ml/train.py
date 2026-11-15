"""Train and evaluate the sentiment model, end to end and reproducibly.

    python -m app.ml.train                     # the shipped configuration
    python -m app.ml.train --features word+char --no-save   # the comparison run

Data is downloaded once from its official source into ./data (git-ignored). Sources
and licences are listed in docs/LICENSES.md. Needs the dev extras (VADER baseline).
"""

import argparse
import json
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pyarrow.parquet as pq
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import FunctionTransformer

from app.ml.preprocess import clean_many
from app.ml.registry import sha256, word_features

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
MODELS = ROOT / "models"
VERSION = "v1"
SEED = 42
NEUTRAL_BAND = (0.35, 0.65)  # see docs/ADRs/002-neutral-class.md

_HF = "https://huggingface.co/datasets/fancyzhx/amazon_polarity/resolve/main/amazon_polarity/"
SOURCES = {
    "amazon_train": (_HF + "train-00000-of-00004.parquet", "amazon_polarity/train-0.parquet"),
    "amazon_test": (_HF + "test-00000-of-00001.parquet", "amazon_polarity/test.parquet"),
    "uci": (
        "https://archive.ics.uci.edu/static/public/331/sentiment+labelled+sentences.zip",
        "uci_sentences/sentences.zip",
    ),
}
_UCI_FILES = {
    "restaurants (Yelp)": "yelp_labelled.txt",
    "films (IMDb)": "imdb_labelled.txt",
    "phones (Amazon)": "amazon_cells_labelled.txt",
}

Texts = list[str]
Labels = np.ndarray[Any, np.dtype[np.int64]]


def download(name: str) -> Path:  # pragma: no cover - network
    url, relative = SOURCES[name]
    path = DATA / relative
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    partial = path.with_suffix(path.suffix + ".part")
    # The URLs are the fixed https addresses in SOURCES, never user input.
    request = urllib.request.Request(url, headers={"User-Agent": "sentiment-lens"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310  # nosec B310
        with partial.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
    partial.replace(path)
    return path


def load_amazon(path: Path, rows: int, seed: int) -> tuple[Texts, Labels]:
    """A seeded random sample of reviews as "title. content", with 0 = negative, 1 = positive."""
    table = pq.read_table(path, columns=["label", "title", "content"])
    picked = np.sort(np.random.default_rng(seed).permutation(table.num_rows)[:rows])
    table = table.take(picked)
    titles = table.column("title").to_pylist()
    contents = table.column("content").to_pylist()
    texts = [
        f"{title}. {content}" if title else content
        for title, content in zip(titles, contents, strict=True)
    ]
    return texts, np.asarray(table.column("label").to_pylist(), dtype=np.int64)


def load_uci(path: Path) -> dict[str, tuple[Texts, Labels]]:
    """Each file holds one `sentence<TAB>label` per line."""
    domains: dict[str, tuple[Texts, Labels]] = {}
    with zipfile.ZipFile(path) as archive:
        members = {Path(name).name: name for name in archive.namelist()}
        for domain, filename in _UCI_FILES.items():
            texts: Texts = []
            labels: list[int] = []
            for line in archive.read(members[filename]).decode("utf-8").splitlines():
                sentence, _, label = line.rpartition("\t")
                if sentence and label.strip() in ("0", "1"):
                    texts.append(sentence.strip())
                    labels.append(int(label))
            domains[domain] = (texts, np.asarray(labels, dtype=np.int64))
    return domains


def build_pipeline(features: str = "word", seed: int = SEED, min_df: int = 3) -> Pipeline:
    word = TfidfVectorizer(
        lowercase=False,  # clean() already lower-cases
        ngram_range=(1, 2),
        min_df=min_df,
        max_features=200_000,
        sublinear_tf=True,
        dtype=np.float32,
    )
    vectoriser: Any = word
    if features == "word+char":
        chars = TfidfVectorizer(
            lowercase=False,
            analyzer="char_wb",
            ngram_range=(3, 5),
            min_df=min_df,
            max_features=200_000,
            sublinear_tf=True,
            dtype=np.float32,
        )
        vectoriser = FeatureUnion([("word", word), ("char", chars)])
    elif features != "word":
        raise ValueError(f"unknown feature set: {features}")
    return Pipeline(
        [
            ("clean", FunctionTransformer(clean_many)),
            ("features", vectoriser),
            ("classifier", LogisticRegression(C=4.0, max_iter=1000, random_state=seed)),
        ]
    )


def calibration(
    probability: np.ndarray[Any, Any], labels: Labels, bins: int = 10
) -> dict[str, Any]:
    """Reliability table: in each band of predicted probability, how many were positive?"""
    edges = np.linspace(0, 1, bins + 1)
    index = np.minimum(np.digitize(probability, edges[1:-1]), bins - 1)
    table = []
    ece = 0.0
    for b in range(bins):
        mask = index == b
        count = int(mask.sum())
        if not count:
            continue
        predicted, actual = float(probability[mask].mean()), float(labels[mask].mean())
        ece += count / len(labels) * abs(predicted - actual)
        table.append(
            {
                "band": f"{edges[b]:.1f}-{edges[b + 1]:.1f}",
                "rows": count,
                "mean_predicted": round(predicted, 3),
                "actually_positive": round(actual, 3),
            }
        )
    return {"expected_calibration_error": round(ece, 4), "bins": table}


def classification(labels: Labels, predicted: np.ndarray[Any, Any]) -> dict[str, Any]:
    return {
        "rows": int(len(labels)),
        "accuracy": round(float(accuracy_score(labels, predicted)), 4),
        "macro_f1": round(float(f1_score(labels, predicted, average="macro")), 4),
        # rows = actual (negative, positive), columns = predicted (negative, positive)
        "confusion_matrix": confusion_matrix(labels, predicted, labels=[0, 1]).tolist(),
    }


def evaluate(model: Pipeline, texts: Texts, labels: Labels) -> dict[str, Any]:
    probability = model.predict_proba(texts)[:, 1]
    result = classification(labels, (probability >= 0.5).astype(np.int64))
    result["calibration"] = calibration(probability, labels)
    low, high = NEUTRAL_BAND
    unsure = (probability > low) & (probability < high)
    correct = (probability >= 0.5).astype(np.int64) == labels
    result["neutral_band"] = {
        "band": list(NEUTRAL_BAND),
        "share_of_rows": round(float(unsure.mean()), 4),
        "accuracy_inside": round(float(correct[unsure].mean()), 4) if unsure.any() else None,
        "accuracy_outside": round(float(correct[~unsure].mean()), 4) if (~unsure).any() else None,
    }
    return result


def evaluate_vader(texts: Texts, labels: Labels) -> dict[str, Any]:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    analyser = SentimentIntensityAnalyzer()
    scores = np.array([analyser.polarity_scores(text)["compound"] for text in texts])
    return classification(labels, (scores >= 0).astype(np.int64))


def top_terms(model: Pipeline, count: int = 25) -> dict[str, list[str]]:
    """The words and phrases with the largest weights, read from the model itself."""
    names, weights = word_features(model)
    order = np.argsort(weights)
    return {
        "negative": [str(names[i]) for i in order[:count]],
        "positive": [str(names[i]) for i in order[::-1][:count]],
    }


def save(model: Pipeline, metadata: dict[str, Any], directory: Path = MODELS) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"sentiment-{VERSION}.joblib"
    joblib.dump(model, path, compress=3)
    metadata = {
        **metadata,
        "artefact": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    (directory / f"sentiment-{VERSION}.json").write_text(
        json.dumps(metadata, indent=2) + "\n", "utf-8"
    )
    return path


def main() -> None:  # pragma: no cover - needs the downloaded data
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", default="word", choices=["word", "word+char"])
    parser.add_argument("--train-rows", type=int, default=150_000)
    parser.add_argument("--test-rows", type=int, default=50_000)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    train_texts, train_labels = load_amazon(download("amazon_train"), args.train_rows, SEED)
    test_texts, test_labels = load_amazon(download("amazon_test"), args.test_rows, SEED)
    cross = load_uci(download("uci"))

    model = build_pipeline(args.features)
    started = time.perf_counter()
    model.fit(train_texts, train_labels)
    fit_seconds = round(time.perf_counter() - started, 1)

    started = time.perf_counter()
    in_domain = evaluate(model, test_texts, test_labels)
    rows_per_second = round(len(test_texts) / (time.perf_counter() - started))

    cross_texts = [text for texts, _ in cross.values() for text in texts]
    cross_labels = np.concatenate([labels for _, labels in cross.values()])
    metadata: dict[str, Any] = {
        "version": VERSION,
        "features": args.features,
        "seed": SEED,
        "scikit_learn": sklearn.__version__,
        "neutral_band": list(NEUTRAL_BAND),
        "training": {
            "dataset": "Amazon Polarity (fancyzhx/amazon_polarity), Apache-2.0",
            "rows": len(train_texts),
            "positive_share": round(float(train_labels.mean()), 4),
            "fit_seconds": fit_seconds,
        },
        "features_count": int(model.named_steps["classifier"].coef_.shape[1]),
        "scoring_rows_per_second": rows_per_second,
        "in_domain": {"model": in_domain, "vader": evaluate_vader(test_texts, test_labels)},
        "cross_domain": {
            "all": {
                "model": evaluate(model, cross_texts, cross_labels),
                "vader": evaluate_vader(cross_texts, cross_labels),
            },
            **{
                domain: {
                    "model": evaluate(model, texts, labels),
                    "vader": evaluate_vader(texts, labels),
                }
                for domain, (texts, labels) in cross.items()
            },
        },
        "top_terms": top_terms(model),
    }
    if not args.no_save:
        path = save(model, metadata)
        print(f"saved {path} ({path.stat().st_size / 1e6:.1f} MB)")
    summary = {k: v for k, v in metadata.items() if k not in ("top_terms",)}
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":  # pragma: no cover
    main()
