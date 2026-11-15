import io
import time
from typing import Any

import pandas as pd
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app

client = TestClient(app)


def finished_job(dataset_id: str) -> tuple[str, dict[str, Any]]:
    response = client.post("/api/analyse", json={"dataset_id": dataset_id})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    for _ in range(600):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job_id, job
        time.sleep(0.02)
    raise AssertionError("analysis did not finish")


def test_page_and_assets_are_served() -> None:
    page = client.get("/")
    assert page.status_code == 200 and "<title>Sentiment Lens</title>" in page.text
    assert '<script src="/static/app.js" defer></script>' in page.text
    assert page.text.count("<script") == 1 and "style=" not in page.text  # nothing inline
    for path, kind in (
        ("/static/app.css", "text/css"),
        ("/static/app.js", "javascript"),
        ("/static/fonts/IBMPlexSans-Regular.woff2", "font/woff2"),
    ):
        asset = client.get(path)
        assert asset.status_code == 200 and kind in asset.headers["content-type"], path


def test_samples_are_listed_and_load() -> None:
    samples = client.get("/api/samples").json()
    assert [s["id"] for s in samples] == ["reviews", "shop-feedback"]
    assert all(s["title"] and s["note"] for s in samples)
    loaded = client.post("/api/samples/shop-feedback").json()
    assert loaded["profile"]["rows"] == 600 and loaded["mapping"]["text"] == "comment"
    assert client.post("/api/samples/nope").status_code == 404


def test_upload_to_dashboard_to_downloads() -> None:
    dataset_id = client.post("/api/samples/shop-feedback").json()["dataset_id"]
    job_id, job = finished_job(dataset_id)
    assert job["status"] == "done" and job["error"] is None
    assert job["done"] == job["total"] == 600
    result = job["result"]
    assert result["summary"]["scored"] == 600
    assert len(result["category"]["groups"]) == 4

    report = client.get(f"/api/report/{job_id}")
    assert report.status_code == 200 and report.headers["content-type"].startswith("text/html")
    assert 'filename="sentiment-report.html"' in report.headers["content-disposition"]
    assert "Sentiment report" in report.text

    csv = client.get(f"/api/report/{job_id}?format=csv")
    table = pd.read_csv(io.BytesIO(csv.content))
    assert len(table) == 600 and "sentiment" in table.columns and "top_terms" in table.columns

    xlsx = client.get(f"/api/report/{job_id}?format=xlsx")
    sheet = load_workbook(io.BytesIO(xlsx.content))["Scored rows"]
    assert sheet.max_row == 601
    assert client.get(f"/api/report/{job_id}?format=pdf").status_code == 400


def test_analysis_without_a_text_column_fails_politely() -> None:
    upload = client.post("/api/upload", content=b"a,b\n1,2\n3,4\n", headers={"X-Filename": "n.csv"})
    _, job = finished_job(upload.json()["dataset_id"])
    assert job["status"] == "failed" and job["result"] is None
    assert job["error"] == "Choose the column that holds the text before analysing."


def test_unknown_ids_and_bad_requests() -> None:
    missing = "0" * 32
    assert client.post("/api/analyse", json={"dataset_id": missing}).status_code == 404
    assert client.post("/api/analyse", json={"dataset_id": "x"}).status_code == 400
    assert client.get(f"/api/jobs/{missing}").status_code == 404
    assert client.get(f"/api/report/{missing}").status_code == 404
