import gc
import threading
import time
import weakref
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.api import routes
from app.core import config, limits
from app.core.config import Settings
from app.core.errors import UserError
from app.services.jobs import JobRunner
from app.services.store import Dataset, DatasetStore
from tests import fixtures as fx

client = TestClient(main.app)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def wait_for(runner: JobRunner, job_id: str, *statuses: str) -> Any:
    for _ in range(300):
        job = runner.get(job_id)
        if job.status in statuses:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job never reached {statuses}; it is {runner.get(job_id).status}")


# --- headers ------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/api/samples", "/healthz", "/nope"])
def test_every_reply_carries_the_security_headers(path: str) -> None:
    headers = client.get(path).headers
    assert headers["content-security-policy"] == main.CONTENT_SECURITY_POLICY
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["strict-transport-security"].startswith("max-age=31536000")
    assert "camera=()" in headers["permissions-policy"]
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert len(headers["x-request-id"]) == 12


def test_policy_allows_only_this_sites_own_files() -> None:
    policy = main.CONTENT_SECURITY_POLICY
    assert "default-src 'none'" in policy and "frame-ancestors 'none'" in policy
    assert "script-src 'self'" in policy and "style-src 'self'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert "http" not in policy and "*" not in policy


def test_cache_rules() -> None:
    assert client.get("/api/samples").headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"
    fonts = client.get("/static/fonts/IBMPlexSans-Regular.woff2").headers["cache-control"]
    assert "immutable" in fonts


def test_request_ids_differ_and_cors_is_not_opened() -> None:
    first = client.get("/healthz", headers={"Origin": "https://elsewhere.test"})
    second = client.get("/healthz")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert "access-control-allow-origin" not in first.headers


def test_uploaded_markup_only_ever_comes_back_as_json_text() -> None:
    frame = pd.DataFrame({"comment": ["<script>alert(1)</script> awful and broken"] * 12})
    response = client.post(
        "/api/upload",
        content=fx.csv_bytes(frame),
        headers={"X-Filename": "%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E.csv"},
    )
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.json()
    assert body["filename"] == "<img src=x onerror=alert(1)>.csv"
    assert body["preview"][0]["comment"].startswith("<script>")

    job_id = client.post("/api/analyse", json={"dataset_id": body["dataset_id"]}).json()["job_id"]
    for _ in range(300):
        if client.get(f"/api/jobs/{job_id}").json()["status"] == "done":
            break
        time.sleep(0.02)
    report = client.get(f"/api/report/{job_id}")
    assert report.headers["content-security-policy"].startswith("sandbox;")
    assert "attachment" in report.headers["content-disposition"]
    assert "<script" not in report.text and "<img" not in report.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report.text
    sheet = client.get(f"/api/report/{job_id}?format=csv").content.decode("utf-8-sig")
    assert "<script>alert(1)</script>" in sheet  # plain text in a CSV, never markup


def test_page_script_never_builds_html_from_data() -> None:
    script = client.get("/static/app.js").text
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert risky not in script


# --- rate limits ------------------------------------------------------------------------


def test_limiter_sliding_window() -> None:
    clock = Clock()
    limiter = limits.RateLimiter(clock)
    assert [limiter.check("a", 3, 60) for _ in range(3)] == [None, None, None]
    assert limiter.check("a", 3, 60) == 60
    assert limiter.check("b", 3, 60) is None  # another address is unaffected
    clock.now = 59.5
    assert limiter.check("a", 3, 60) == 1
    clock.now = 60.0
    assert limiter.check("a", 3, 60) is None  # the first request has aged out
    clock.now = 5000
    limiter.sweep()
    assert len(limiter) == 0


def test_wait_phrases() -> None:
    assert limits.wait_phrase(5) == "Try again in 5 seconds."
    assert limits.wait_phrase(1800) == "Try again in about 30 minutes."


@pytest.fixture
def real_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The limits a real deployment runs with, on a fresh limiter."""
    monkeypatch.setattr(
        config,
        "settings",
        Settings(
            rate_uploads_per_hour=10,
            rate_analyses_per_hour=20,
            rate_polls_per_minute=120,
            rate_requests_per_minute=300,
        ),
    )
    monkeypatch.setattr(limits, "limiter", limits.RateLimiter())


def test_eleventh_upload_in_an_hour_is_refused(real_limits: None) -> None:
    for _ in range(10):
        ok = client.post(
            "/api/upload", content=b"a\nhello there\n", headers={"X-Filename": "a.csv"}
        )
        assert ok.status_code == 200
    refused = client.post("/api/upload", content=b"a\nhello\n", headers={"X-Filename": "a.csv"})
    assert refused.status_code == 429
    assert 3590 <= int(refused.headers["retry-after"]) <= 3600
    assert refused.json()["error"] == (
        "You have uploaded 10 files in the last hour, which is the limit. "
        "Try again in about 60 minutes."
    )
    assert refused.headers["content-security-policy"] == main.CONTENT_SECURITY_POLICY
    # samples count as uploads; other calls and health checks still work
    assert client.post("/api/samples/reviews").status_code == 429
    assert client.get("/api/samples").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_polling_and_general_limits(real_limits: None) -> None:
    missing = "0" * 32
    codes = [client.get(f"/api/jobs/{missing}").status_code for _ in range(121)]
    assert codes[:120] == [404] * 120 and codes[120] == 429
    codes = [client.get("/api/samples").status_code for _ in range(301)]
    assert codes[299] == 200 and codes[300] == 429
    assert all(client.get("/healthz").status_code == 200 for _ in range(50))


def _request(forwarded: str | None, peer: str = "10.0.0.9") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_forged_forwarded_header_is_ignored_without_a_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert limits.client_address(_request("1.2.3.4")) == "10.0.0.9"
    monkeypatch.setattr(config, "settings", Settings(trusted_proxy_hops=1))
    # behind one proxy: the proxy appends the real address; anything before it is forged
    assert limits.client_address(_request("6.6.6.6, 203.0.113.7")) == "203.0.113.7"
    assert limits.client_address(_request(None)) == "10.0.0.9"


# --- concurrency, queue, timeout, cancellation -----------------------------------------------


def test_queue_is_bounded_and_reports_position() -> None:
    runner = JobRunner(workers=1, max_queued=2)
    gate = threading.Event()

    def blocked(progress: Any) -> str:
        gate.wait(5)
        return "ok"

    running = runner.submit(blocked)
    wait_for(runner, running, "running")
    second, third = runner.submit(blocked), runner.submit(blocked)
    assert (runner.position(second), runner.position(third)) == (0, 1)
    with pytest.raises(UserError) as full:
        runner.submit(blocked)
    assert full.value.status == 429 and full.value.retry_after == 10
    assert "busy" in full.value.message
    gate.set()
    assert wait_for(runner, third, "done").result == "ok"


def test_cancel_queued_and_running_jobs() -> None:
    runner = JobRunner(workers=1)
    started = threading.Event()

    def long_job(progress: Any) -> None:
        started.set()
        for step in range(500):
            progress(step, 500)
            time.sleep(0.01)

    running = runner.submit(long_job)
    queued = runner.submit(long_job)
    started.wait(2)
    runner.cancel(queued)
    assert runner.get(queued).status == "cancelled"  # never starts
    runner.cancel(running)
    stopped = wait_for(runner, running, "cancelled")
    assert stopped.done < 499 and stopped.result is None
    runner.cancel("not-a-job")  # harmless


def test_job_is_stopped_at_its_time_limit() -> None:
    clock = Clock()
    runner = JobRunner(workers=1, timeout_seconds=120, clock=clock)

    def slow(progress: Any) -> None:
        progress(1, 10)
        clock.now = 121
        progress(2, 10)

    job = wait_for(runner, runner.submit(slow), "failed")
    assert (
        job.error == "The analysis took longer than 2 minutes and was stopped. Try a smaller file."
    )


def test_too_many_uploads_at_once_get_a_clean_429(monkeypatch: pytest.MonkeyPatch) -> None:
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(routes, "_upload_slots", slots)
    response = client.post("/api/upload", content=b"a\nhi\n", headers={"X-Filename": "a.csv"})
    assert response.status_code == 429 and response.headers["retry-after"] == "5"
    slots.release()
    assert (
        client.post("/api/upload", content=b"a\nhi\n", headers={"X-Filename": "a.csv"}).status_code
        == 200
    )
    assert slots.acquire(blocking=False)  # the slot was handed back


def test_cancel_and_delete_through_the_api() -> None:
    dataset_id = client.post("/api/samples/shop-feedback").json()["dataset_id"]
    job_id = client.post("/api/analyse", json={"dataset_id": dataset_id}).json()["job_id"]
    assert "position" in client.get(f"/api/jobs/{job_id}").json()
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    for _ in range(300):  # a running job stops at its next batch of rows
        status = client.get(f"/api/jobs/{job_id}").json()["status"]
        if status != "running":
            break
        time.sleep(0.02)
    assert status in ("cancelled", "done")
    # deleting the upload removes its results too
    assert client.delete(f"/api/datasets/{dataset_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/datasets/{dataset_id}").status_code == 404


# --- nothing outlives its time to live ----------------------------------------------------------


def test_nothing_is_left_in_memory_after_the_ttl() -> None:
    clock = Clock()
    store = DatasetStore(ttl_seconds=1800, capacity=4, clock=clock)
    runner = JobRunner(workers=1, ttl_seconds=1800, clock=clock)

    frame = pd.DataFrame({"comment": ["private customer words"] * 100})
    alive = weakref.ref(frame)
    dataset_id = store.put(Dataset(frame, "a.csv", "csv", [], [], {}))
    job_id = runner.submit(lambda progress: store.get(dataset_id).frame.copy())
    result = weakref.ref(wait_for(runner, job_id, "done").result)
    del frame

    clock.now = 1799
    store.purge()
    runner.purge()
    assert len(store) == 1 and len(runner) == 1 and alive() is not None

    clock.now = 1800
    store.purge()
    runner.purge()
    gc.collect()
    assert len(store) == 0 and len(runner) == 0
    assert alive() is None and result() is None  # the rows themselves are gone


def test_background_sweeper_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(config, "settings", Settings(sweep_seconds=0))
    monkeypatch.setattr(main, "sweep", lambda: calls.append(1))
    with TestClient(main.app) as running:
        for _ in range(20):
            running.get("/healthz")
            if calls:
                break
    assert calls


def test_sweep_clears_the_shared_stores() -> None:
    main.sweep()  # runs against the real store, job runner and limiter without error
