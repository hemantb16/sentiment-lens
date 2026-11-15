import logging
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import Settings
from app.main import app
from tests import fixtures as fx

client = TestClient(app)


def upload(filename: str, data: bytes | Iterator[bytes]) -> httpx2.Response:
    return client.post("/api/upload", content=data, headers={"X-Filename": quote(filename)})


def uploaded(frame_bytes: bytes, filename: str = "reviews.csv") -> dict[str, Any]:
    response = upload(filename, frame_bytes)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_upload_returns_preview_mapping_and_profile() -> None:
    body = uploaded(fx.csv_bytes(fx.reviews()))
    assert len(body["dataset_id"]) == 32
    assert body["filename"] == "reviews.csv"
    assert body["format"] == "csv"
    assert body["warnings"] == []
    assert body["mapping"]["text"] == "review_text"
    assert body["mapping"]["date"] == "date"
    assert body["mapping"]["category"] == "product"
    assert body["profile"]["rows"] == 60
    assert len(body["preview"]) == 10
    assert body["preview"][0]["review_text"] == fx.sentence(0)
    assert 0 < body["expires_in_seconds"] <= 1800


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("r.tsv", fx.csv_bytes(fx.reviews(), sep="\t")),
        ("r.txt", fx.txt_bytes()),
        ("r.xlsx", fx.xlsx_bytes(fx.reviews())),
        ("r.json", fx.json_bytes(fx.reviews())),
        ("r.jsonl", fx.jsonl_bytes(fx.reviews())),
        ("r.parquet", fx.parquet_bytes(fx.reviews())),
    ],
)
def test_every_format_uploads(filename: str, data: bytes) -> None:
    body = uploaded(data, filename)
    assert body["profile"]["rows"] in (20, 60)
    assert body["preview"]


def test_preview_is_json_safe_for_dates_and_missing_values() -> None:
    frame = fx.reviews(12)
    frame.loc[0, "product"] = None
    body = uploaded(
        fx.xlsx_bytes(frame.assign(date=lambda f: f["date"].astype("datetime64[ns]"))), "r.xlsx"
    )
    assert body["preview"][0]["product"] is None
    assert body["preview"][0]["date"].startswith("2026-01-01T00:00:00")


def test_path_parts_are_stripped_from_the_file_name() -> None:
    body = uploaded(fx.csv_bytes(fx.reviews()), "C:\\Users\\someone\\Desktop\\..\\reviews.csv")
    assert body["filename"] == "reviews.csv"


def test_declared_size_over_the_cap_is_refused_before_reading() -> None:
    response = client.post(
        "/api/upload",
        content=b"a,b\n1,2\n",
        headers={"X-Filename": "r.csv", "Content-Length": "40000001"},
    )
    assert response.status_code == 413
    assert response.json() == {"error": "This file is larger than 20 MB. Upload a smaller file."}


def test_body_just_over_the_cap_is_read_out_then_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "settings", Settings(max_upload_bytes=1000))
    kept: list[int] = []
    monkeypatch.setattr(routes, "_ingest", lambda name, data: kept.append(len(data)))
    response = upload("r.csv", b"a,b\n" + b"1,2\n" * 300)  # 1.2 KB against a 1 KB cap
    assert response.status_code == 413
    assert "Upload a smaller file" in response.json()["error"]
    assert kept == []  # the parser never saw it


def test_streamed_body_over_the_cap_is_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "settings", Settings(max_upload_bytes=1_000_000))
    sent = 0

    def chunks() -> Iterator[bytes]:
        nonlocal sent
        for _ in range(100):  # 6.4 MB in total, with no Content-Length header
            sent += 1
            yield b"x" * 64_000

    response = upload("r.csv", chunks())
    assert response.status_code == 413
    assert "larger than 1 MB" in response.json()["error"]


def test_unsupported_type_is_refused() -> None:
    response = upload("model.pkl", fx.PICKLE)
    assert response.status_code == 415
    assert "isn't supported" in response.json()["error"]


def test_missing_file_name() -> None:
    response = client.post("/api/upload", content=b"a,b\n1,2\n")
    assert response.status_code == 400
    assert response.json() == {"error": "The upload is missing a file name."}


@pytest.mark.parametrize(
    ("filename", "data", "status"),
    [
        ("big.xlsx", fx.zip_bomb_xlsx(), 413),
        ("report.csv", fx.EXECUTABLE, 415),
        ("report.xlsx", fx.plain_zip(), 415),
        ("deep.json", fx.deep_json(100_000), 400),
    ],
)
def test_hostile_files_get_friendly_errors(filename: str, data: bytes, status: int) -> None:
    response = upload(filename, data)
    assert response.status_code == status
    error = response.json()["error"]
    assert "Traceback" not in error and len(error) < 200


def test_formula_cells_come_back_as_inert_text() -> None:
    body = uploaded(fx.formula_csv(), "f.csv")
    assert [row["comment"] for row in body["preview"]][:4] == fx.FORMULA_CELLS


def test_get_dataset_returns_the_same_description() -> None:
    body = uploaded(fx.csv_bytes(fx.reviews()))
    again = client.get(f"/api/datasets/{body['dataset_id']}").json()
    assert again["profile"] == body["profile"]
    assert again["mapping"] == body["mapping"]


def test_mapping_override_updates_the_profile() -> None:
    body = uploaded(fx.csv_bytes(fx.tickets()), "tickets.csv")
    assert body["mapping"]["text"] == "body"
    response = client.put(
        f"/api/datasets/{body['dataset_id']}/mapping",
        json={"text": "subject", "date": None, "category": "channel"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["mapping"]["text"] == "subject"
    assert updated["profile"]["text"]["column"] == "subject"
    assert updated["profile"]["time"] is None
    # the choice is remembered
    assert client.get(f"/api/datasets/{body['dataset_id']}").json()["mapping"]["text"] == "subject"


def test_mapping_override_rejects_a_bad_column() -> None:
    body = uploaded(fx.csv_bytes(fx.tickets()), "tickets.csv")
    response = client.put(f"/api/datasets/{body['dataset_id']}/mapping", json={"text": "ticket_id"})
    assert response.status_code == 400
    assert "doesn't hold text" in response.json()["error"]


def test_malformed_requests_get_a_generic_error() -> None:
    body = uploaded(fx.csv_bytes(fx.reviews()))
    response = client.put(f"/api/datasets/{body['dataset_id']}/mapping", json={"date": "date"})
    assert response.status_code == 400
    assert response.json() == {"error": "The request wasn't understood."}
    assert client.get("/api/datasets/not-an-id").status_code == 400


def test_delete_removes_the_data_at_once() -> None:
    body = uploaded(fx.csv_bytes(fx.reviews()))
    assert client.delete(f"/api/datasets/{body['dataset_id']}").status_code == 204
    response = client.get(f"/api/datasets/{body['dataset_id']}")
    assert response.status_code == 404
    assert "expired or doesn't exist" in response.json()["error"]


def test_unknown_dataset_is_404() -> None:
    assert client.get("/api/datasets/" + "0" * 32).status_code == 404


def test_unexpected_errors_are_generic_and_logs_hold_no_row_content(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cell = "my card number is 1234"

    def explode(filename: str, data: bytes) -> dict[str, Any]:
        raise RuntimeError(f"could not convert value: {cell}")

    monkeypatch.setattr(routes, "_ingest", explode)
    # The default client re-raises server errors; if ours escaped, this call would raise.
    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/upload", content=b"a\n1\n", headers={"X-Filename": "r.csv"})
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "Something went wrong on our side. Please try again."
    # the user is given the same ID the log line carries, so a report can be traced
    assert body["request_id"] == response.headers["x-request-id"]
    assert f"request {body['request_id']}: unhandled RuntimeError at explode" in caplog.text
    assert "card number" not in caplog.text
