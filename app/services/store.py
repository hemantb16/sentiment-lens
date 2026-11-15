"""Uploaded datasets, held in memory only and forgotten after a short time.

Nothing is written to disk. IDs are random UUIDs, so one user cannot guess another's.
"""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.core.config import settings
from app.core.errors import UserError
from app.services.columns import ColumnInfo


@dataclass
class Dataset:
    frame: pd.DataFrame
    filename: str
    format: str
    warnings: list[str]
    infos: list[ColumnInfo]
    mapping: dict[str, Any]
    expires_at: float = field(default=0.0)


class DatasetStore:
    # shortcut: the store lives in one process, so the app must run with one worker.
    # Sprint 5 documents the swap to Redis for more than one replica.
    def __init__(
        self,
        ttl_seconds: int = settings.dataset_ttl_seconds,
        capacity: int = settings.max_datasets,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._clock = clock
        self._items: dict[str, Dataset] = {}
        self._lock = threading.Lock()

    def put(self, dataset: Dataset) -> str:
        dataset_id = uuid.uuid4().hex
        with self._lock:
            self._purge()
            while len(self._items) >= self._capacity:
                # dicts keep insertion order, so the first key is the oldest upload
                del self._items[next(iter(self._items))]
            dataset.expires_at = self._clock() + self._ttl
            self._items[dataset_id] = dataset
        return dataset_id

    def get(self, dataset_id: str) -> Dataset:
        with self._lock:
            self._purge()
            dataset = self._items.get(dataset_id)
        if dataset is None:
            raise UserError("This upload has expired or doesn't exist. Upload the file again.", 404)
        return dataset

    def delete(self, dataset_id: str) -> None:
        with self._lock:
            self._items.pop(dataset_id, None)

    def purge(self) -> None:
        with self._lock:
            self._purge()

    def seconds_left(self, dataset: Dataset) -> int:
        return max(int(dataset.expires_at - self._clock()), 0)

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._items)

    def _purge(self) -> None:
        now = self._clock()
        for dataset_id in [k for k, v in self._items.items() if v.expires_at <= now]:
            del self._items[dataset_id]


store = DatasetStore()
