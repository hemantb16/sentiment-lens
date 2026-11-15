"""Loads the trained model that ships with the app.

The artefact is a joblib file, which can run code when loaded. It is only ever
the file this project trained itself: its SHA-256 is recorded next to it at
training time and checked here before loading. Uploaded files never reach joblib.
"""

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
from sklearn.pipeline import FeatureUnion, Pipeline

from app.core.config import settings


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def word_features(model: Pipeline) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    """Names and weights of the readable (word and phrase) features."""
    vectoriser = model.named_steps["features"]
    word = vectoriser.transformer_list[0][1] if isinstance(vectoriser, FeatureUnion) else vectoriser
    names = word.get_feature_names_out()
    return names, model.named_steps["classifier"].coef_[0][: len(names)]


@dataclass(frozen=True)
class LoadedModel:
    pipeline: Pipeline
    metadata: dict[str, Any]
    word_names: np.ndarray[Any, Any]
    # Weights used for explanations only: filler words are zeroed (see _informative).
    word_weights: np.ndarray[Any, Any]

    @property
    def neutral_band(self) -> tuple[float, float]:
        low, high = self.metadata["neutral_band"]
        return float(low), float(high)


def load_from(directory: Path, version: str = "v1") -> LoadedModel:
    metadata: dict[str, Any] = json.loads(
        (directory / f"sentiment-{version}.json").read_text("utf-8")
    )
    artefact = directory / Path(metadata["artefact"]).name
    if sha256(artefact) != metadata["sha256"]:
        raise RuntimeError("The model file does not match the hash recorded when it was trained.")
    pipeline = joblib.load(artefact)
    names, weights = word_features(pipeline)
    return LoadedModel(pipeline, metadata, names, weights * _informative(names))


# "and", "was", "you" carry small weights but appear everywhere, so they would top
# every list of "what drives the sentiment". They still count towards the score;
# they are only left out of explanations. A lone "not" or "no" explains nothing
# either, but stays visible inside a phrase such as "not good".
_FILLER = ENGLISH_STOP_WORDS | {"not", "no", "never", "don", "didn", "doesn", "isn", "wasn"}


def _informative(names: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    """1.0 for terms worth showing in an explanation, 0.0 for filler."""
    return np.array(
        [
            float(any(word.removeprefix("not_") not in _FILLER for word in str(name).split()))
            for name in names
        ]
    )


@lru_cache(maxsize=1)
def load() -> LoadedModel:
    return load_from(Path(settings.model_dir))
