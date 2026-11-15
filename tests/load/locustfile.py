"""Load test: visitors who browse the page and run whole analyses.

    locust -f tests/load/locustfile.py --headless -u 20 -r 5 -t 60s --host http://127.0.0.1:8041

A 429 is the app saying "busy, come back later", which is the designed answer to
overload, so it is counted as a success here. Any 5xx is a failure.
"""

import time

from locust import HttpUser, between, task
from locust.clients import ResponseContextManager

from tests import fixtures as fx

UPLOAD = fx.csv_bytes(fx.tickets(2_000))  # about 300 KB, built once


def accept(response: ResponseContextManager, *expected: int) -> bool:
    """Mark the response; return True only if the flow should carry on."""
    if response.status_code == 429:
        response.success()
        return False
    if response.status_code in expected:
        response.success()
        return True
    response.failure(f"unexpected status {response.status_code}")
    return False


class Visitor(HttpUser):
    wait_time = between(1, 3)

    @task(4)
    def open_page(self) -> None:
        for path in ("/", "/static/app.css", "/static/app.js"):
            with self.client.get(path, catch_response=True) as response:
                accept(response, 200)

    @task(3)
    def list_samples(self) -> None:
        with self.client.get("/api/samples", catch_response=True) as response:
            accept(response, 200)

    @task(1)
    def health(self) -> None:
        self.client.get("/healthz")

    @task(2)
    def analyse_a_file(self) -> None:
        with self.client.post(
            "/api/upload",
            data=UPLOAD,
            headers={"X-Filename": "tickets.csv"},
            catch_response=True,
        ) as response:
            if not accept(response, 200):
                return
            dataset_id = response.json()["dataset_id"]

        with self.client.post(
            "/api/analyse", json={"dataset_id": dataset_id}, catch_response=True
        ) as response:
            started = accept(response, 202)
            job_id = response.json().get("job_id") if started else None

        status = None
        for _ in range(90):
            if job_id is None:
                break
            with self.client.get(
                f"/api/jobs/{job_id}", name="/api/jobs/[id]", catch_response=True
            ) as response:
                if not accept(response, 200):
                    break
                status = response.json()["status"]
            if status in ("done", "failed", "cancelled"):
                break
            time.sleep(1)

        if status == "done":
            with self.client.get(
                f"/api/report/{job_id}?format=csv", name="/api/report/[id]", catch_response=True
            ) as response:
                accept(response, 200)
        with self.client.delete(
            f"/api/datasets/{dataset_id}", name="/api/datasets/[id]", catch_response=True
        ) as response:
            accept(response, 204)
