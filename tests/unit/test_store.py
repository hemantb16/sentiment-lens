import re

import pandas as pd
import pytest

from app.core.errors import UserError
from app.services.store import Dataset, DatasetStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def dataset() -> Dataset:
    return Dataset(pd.DataFrame({"text": ["a"]}), "a.csv", "csv", [], [], {})


def test_ids_are_random_uuids() -> None:
    store = DatasetStore()
    first, second = store.put(dataset()), store.put(dataset())
    assert first != second
    assert re.fullmatch(r"[0-9a-f]{32}", first)


def test_dataset_is_gone_after_the_ttl() -> None:
    clock = FakeClock()
    store = DatasetStore(ttl_seconds=1800, capacity=4, clock=clock)
    dataset_id = store.put(dataset())
    clock.now = 1799
    assert store.seconds_left(store.get(dataset_id)) == 1
    clock.now = 1800
    with pytest.raises(UserError) as caught:
        store.get(dataset_id)
    assert caught.value.status == 404
    assert len(store) == 0


def test_oldest_dataset_is_dropped_when_full() -> None:
    store = DatasetStore(ttl_seconds=60, capacity=2, clock=FakeClock())
    first, second, third = (store.put(dataset()) for _ in range(3))
    assert len(store) == 2
    with pytest.raises(UserError):
        store.get(first)
    assert store.get(second) and store.get(third)


def test_delete_is_immediate_and_repeatable() -> None:
    store = DatasetStore()
    dataset_id = store.put(dataset())
    store.delete(dataset_id)
    store.delete(dataset_id)
    with pytest.raises(UserError):
        store.get(dataset_id)
