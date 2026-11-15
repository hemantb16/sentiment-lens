import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from openpyxl import load_workbook

from app.core.errors import UserError
from app.services import columns, dashboard, export
from app.services.ingest import parse_upload
from app.services.jobs import JobRunner
from app.services.store import Dataset
from tests import fixtures as fx

SAMPLES = Path(__file__).resolve().parents[2] / "app" / "static" / "samples"


def dataset_from(filename: str, data: bytes) -> Dataset:
    parsed = parse_upload(filename, data)
    infos = columns.describe_columns(parsed.frame)
    mapping = columns.detect(infos, len(parsed.frame))
    return Dataset(parsed.frame, filename, parsed.format, parsed.warnings, infos, mapping)


@pytest.fixture(scope="module")
def shop() -> dashboard.Analysis:
    data = (SAMPLES / "shop_feedback.csv").read_bytes()
    return dashboard.analyse(dataset_from("shop_feedback.csv", data))


# --- dashboard payload ---------------------------------------------------------------


def test_sample_columns_are_detected() -> None:
    shop_data = dataset_from("shop_feedback.csv", (SAMPLES / "shop_feedback.csv").read_bytes())
    assert shop_data.mapping["text"] == "comment"
    assert shop_data.mapping["date"] == "date"
    assert shop_data.mapping["category"] == "category"
    reviews = dataset_from("reviews.csv", (SAMPLES / "reviews.csv").read_bytes())
    assert (reviews.mapping["text"], reviews.mapping["category"]) == ("sentence", "site")
    assert len(reviews.frame) == 3000


def test_payload_is_json_and_has_every_section(shop: dashboard.Analysis) -> None:
    payload = json.loads(json.dumps(shop.payload))
    assert set(payload) == {
        "filename",
        "mapping",
        "warnings",
        "summary",
        "profile",
        "time",
        "category",
        "drivers",
        "examples",
        "model",
    }
    summary = payload["summary"]
    assert summary["rows"] == summary["scored"] == 600
    assert sum(summary["counts"].values()) == 600
    assert summary["takeaway"].endswith("show no clear lean.")
    assert "% of rows are positive" in summary["takeaway"]


def test_time_section_finds_the_bad_month(shop: dashboard.Analysis) -> None:
    time_section = shop.payload["time"]
    assert set(time_section["series"]) == {"week", "month"}
    assert time_section["default"] == "week"
    months = time_section["series"]["month"]
    assert [m["period"] for m in months][:3] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert sum(m["total"] for m in months) == 600
    worst = max(months, key=lambda m: m["negative"] / m["total"])
    assert worst["label"] == "March 2026"  # the sample has a bad month for deliveries
    assert time_section["takeaway"].startswith("Negativity peaks in the week of")
    assert time_section["undated"] == 0


def test_category_section_is_sorted_and_explained(shop: dashboard.Analysis) -> None:
    category = shop.payload["category"]
    groups = category["groups"]
    assert {g["value"] for g in groups} == {"Delivery", "Product quality", "Support", "Mobile app"}
    assert [g["net"] for g in groups] == sorted((g["net"] for g in groups), reverse=True)
    assert category["takeaway"].startswith(f"{groups[0]['value']} is the most positive")
    assert f"{groups[-1]['value']} is the most negative" in category["takeaway"]
    assert all(g["total"] == g["positive"] + g["neutral"] + g["negative"] for g in groups)


def test_drivers_and_examples(shop: dashboard.Analysis) -> None:
    drivers = shop.payload["drivers"]
    assert drivers["takeaway"].startswith("Positive rows are driven by")
    assert drivers["terms"]["positive"] and drivers["terms"]["negative"]
    assert drivers["phrases"]["negative"][0]["rows"] >= 2
    examples = shop.payload["examples"]
    assert {e["group"] for e in examples} == {"Most positive", "Most negative", "Least confident"}
    top = [e for e in examples if e["group"] == "Most positive"]
    # the sample repeats 38 comments many times; each should be shown once
    assert len({e["text"] for e in top}) == len(top) <= 38
    assert not any(
        word in {"the", "to", "was", "and"}
        for phrase in drivers["phrases"]["positive"]
        for word in phrase["term"].split()
    )
    assert top[0]["label"] == "positive" and top[0]["probability"] >= top[-1]["probability"]
    assert top[0]["date"].startswith("2026-") and top[0]["category"] in {
        "Delivery",
        "Product quality",
        "Support",
        "Mobile app",
    }
    assert "92.9%" in shop.payload["model"]["takeaway"]


def test_sections_are_empty_without_date_or_category() -> None:
    analysis = dashboard.analyse(dataset_from("c.csv", fx.csv_bytes(fx.comments(30))))
    assert analysis.payload["time"] is None and analysis.payload["category"] is None
    assert analysis.payload["examples"][0]["date"] is None


def test_single_period_and_single_category_takeaways() -> None:
    frame = pd.DataFrame(
        {
            "comment": [fx.sentence(i) for i in range(20)],
            "date": ["2026-03-02"] * 20,
            "team": ["red"] * 19 + [None],
        }
    )
    dataset = dataset_from("c.csv", fx.csv_bytes(frame))
    dataset.mapping.update(category="team")
    payload = dashboard.analyse(dataset).payload
    assert "no trend to show" in payload["time"]["takeaway"]
    assert "only one category" in payload["category"]["takeaway"]


def test_nothing_scorable_is_explained() -> None:
    frame = pd.DataFrame({"comment": ["Das Produkt ist sehr gut und ich bin zufrieden"] * 12})
    payload = dashboard.analyse(dataset_from("c.csv", fx.csv_bytes(frame))).payload
    assert payload["summary"]["takeaway"].startswith("No rows could be scored")
    assert payload["examples"] == [] and payload["drivers"]["takeaway"] == "No terms stand out."
    assert "Sentiment report" in export.to_html(payload)


def test_analyse_needs_a_text_column() -> None:
    dataset = dataset_from("n.csv", b"a,b\n1,2\n3,4\n")
    with pytest.raises(UserError, match="Choose the column that holds the text"):
        dashboard.analyse(dataset)


# --- exports ----------------------------------------------------------------------------


def hostile() -> dashboard.Analysis:
    frame = pd.DataFrame(
        {
            "=cmd|' /C calc'!A0": ['=HYPERLINK("http://x.test")', "+1+1 great product", "fine"] * 4,
            "comment": ["<script>alert(1)</script> terrible, broken and useless"] * 12,
            "group": ["<b>bold</b>", "@SUM(A1)", "plain"] * 4,
            "when": pd.date_range("2026-01-01", periods=12).strftime("%Y-%m-%d"),
            "sentiment": ["x"] * 12,
        }
    )
    dataset = dataset_from("<img src=x onerror=alert(1)>.csv", fx.csv_bytes(frame))
    dataset.mapping.update(text="comment", category="group", date="when")
    return dashboard.analyse(dataset)


def test_csv_export_is_formula_safe() -> None:
    analysis = hostile()
    text = export.to_csv(analysis.table).decode("utf-8-sig")
    table = pd.read_csv(io.StringIO(text))
    assert export.to_csv(analysis.table).startswith(b"\xef\xbb\xbf")
    assert table.columns[0] == "'=cmd|' /C calc'!A0"
    assert table.iloc[0, 0] == '\'=HYPERLINK("http://x.test")'
    assert table.iloc[1, 0] == "'+1+1 great product"
    assert table["group"][1] == "'@SUM(A1)"
    # the uploaded "sentiment" column is kept; ours gets a different name
    assert list(table.columns[-6:]) == [
        "sentiment",
        "sentiment_",
        "positive_probability",
        "confidence",
        "not_scored_because",
        "top_terms",
    ]
    assert table["sentiment_"][0] == "negative"
    assert not any(
        str(v).startswith(("=", "+", "-", "@")) for v in table.select_dtypes("object").stack()
    )


def test_xlsx_export_has_no_formulas() -> None:
    analysis = hostile()
    workbook = load_workbook(io.BytesIO(export.to_xlsx(analysis.table)))
    sheet = workbook["Scored rows"]
    cells = [cell for row in sheet.iter_rows() for cell in row]
    assert len(list(sheet.iter_rows())) == 13
    assert not any(cell.data_type == "f" for cell in cells)
    assert sheet["A2"].value == '\'=HYPERLINK("http://x.test")'
    assert isinstance(sheet["D2"].value, str | pd.Timestamp) or sheet["D2"].value is not None


def test_html_report_escapes_everything() -> None:
    html = export.to_html(hostile().payload)
    assert "<script>" not in html and "<img" not in html and "<b>bold" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;.csv" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    for heading in ("Summary", "Sentiment over time", "Sentiment by group", "What drives it"):
        assert heading in html
    assert html.count("<script") == 0


def test_html_report_for_the_sample(shop: dashboard.Analysis) -> None:
    html = export.to_html(shop.payload)
    assert html.startswith("<!doctype html>")
    assert "is the most negative" in html and "Examples: most negative" in html


# --- job runner -------------------------------------------------------------------------


def wait(runner: JobRunner, job_id: str) -> Any:
    for _ in range(200):
        job = runner.get(job_id)
        if job.status in ("done", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_job_runs_and_reports_progress() -> None:
    runner = JobRunner(workers=1)
    release = threading.Event()

    def work(progress: Any) -> str:
        progress(3, 10)
        release.wait(2)
        return "result"

    job_id = runner.submit(work)
    for _ in range(200):
        if runner.get(job_id).total:
            break
        time.sleep(0.01)
    running = runner.get(job_id)
    assert (running.status, running.done, running.total) == ("running", 3, 10)
    release.set()
    done = wait(runner, job_id)
    assert (done.status, done.result, done.error) == ("done", "result", None)


def test_job_failures_are_reported_safely(caplog: pytest.LogCaptureFixture) -> None:
    runner = JobRunner(workers=2)

    def user_problem(progress: Any) -> None:
        raise UserError("Choose a text column.")

    def crash(progress: Any) -> None:
        raise RuntimeError("cell said: secret words")

    friendly = wait(runner, runner.submit(user_problem))
    broken = wait(runner, runner.submit(crash))
    assert (friendly.status, friendly.error) == ("failed", "Choose a text column.")
    assert broken.error == "The analysis failed on our side. Please try again."
    assert "RuntimeError" in caplog.text and "secret words" not in caplog.text


def test_jobs_expire_and_old_results_make_room() -> None:
    now = [0.0]
    runner = JobRunner(workers=1, ttl_seconds=60, capacity=2, clock=lambda: now[0])
    first = runner.submit(lambda progress: 1)
    wait(runner, first)
    second = runner.submit(lambda progress: 2)
    wait(runner, second)
    third = runner.submit(lambda progress: 3)
    assert wait(runner, third).result == 3
    with pytest.raises(UserError) as gone:
        runner.get(first)  # dropped to make room
    assert gone.value.status == 404
    now[0] = 61
    with pytest.raises(UserError):
        runner.get(third)  # past its time to live
