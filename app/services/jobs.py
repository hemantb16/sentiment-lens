"""Runs analysis jobs on a small pool of threads and remembers their results for a while.

Traffic control lives here: at most `workers` jobs run at once, at most
`max_queued` wait, and anything beyond that is turned away with a 429. A job can
be cancelled, and is stopped if it runs past its time limit.

Callers only see submit / get / cancel. A Redis + RQ implementation with the same
methods could replace this class without touching the rest of the app.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.errors import UserError, busy

logger = logging.getLogger(__name__)

Progress = Callable[[int, int], None]
Work = Callable[[Progress], Any]
_FINISHED = ("done", "failed", "cancelled")


class _Stop(Exception):
    """Raised inside a job, from its progress callback, to end it early."""


@dataclass
class Job:
    status: str = "queued"  # queued | running | done | failed | cancelled
    done: int = 0
    total: int = 0
    result: Any = None
    error: str | None = None
    owner: str | None = None  # the dataset this job belongs to
    cancel_requested: bool = False
    started_at: float = 0.0
    expires_at: float = 0.0


class JobRunner:
    def __init__(
        self,
        workers: int = settings.max_concurrent_jobs,
        max_queued: int = settings.max_queued_jobs,
        timeout_seconds: int = settings.job_timeout_seconds,
        ttl_seconds: int = settings.dataset_ttl_seconds,
        capacity: int = settings.max_datasets,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analysis")
        self._max_queued = max_queued
        self._capacity = capacity
        self._timeout = timeout_seconds
        self._ttl = ttl_seconds
        self._clock = clock
        self._jobs: dict[str, Job] = {}  # insertion order = submission order
        self._lock = threading.Lock()

    def submit(self, work: Work, owner: str | None = None) -> str:
        job_id = uuid.uuid4().hex
        job = Job(owner=owner, expires_at=self._clock() + self._ttl)
        with self._lock:
            self._purge()
            if sum(j.status == "queued" for j in self._jobs.values()) >= self._max_queued:
                raise busy("The service is busy right now. Try again in a few seconds.", 10)
            finished = [k for k, v in self._jobs.items() if v.status in _FINISHED]
            while len(self._jobs) >= self._capacity and finished:
                self._jobs.pop(finished.pop(0)).result = None  # oldest result makes room
            self._jobs[job_id] = job
        self._pool.submit(self._run, job, work)
        return job_id

    def get(self, job_id: str) -> Job:
        with self._lock:
            self._purge()
            job = self._jobs.get(job_id)
        if job is None:
            raise UserError("This analysis has expired or doesn't exist. Run it again.", 404)
        return job

    def position(self, job_id: str) -> int:
        """How many queued jobs are ahead of this one (0 = next to start)."""
        with self._lock:
            ahead = 0
            for other_id, other in self._jobs.items():
                if other_id == job_id:
                    return ahead
                ahead += other.status == "queued"
        return 0

    def cancel(self, job_id: str) -> None:
        """Stop a job. A queued job never starts; a running one stops at its next batch."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in _FINISHED:
                return
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"

    def discard_for(self, owner: str) -> None:
        """Cancel and forget every job that belongs to a dataset the user deleted."""
        with self._lock:
            for job_id in [k for k, v in self._jobs.items() if v.owner == owner]:
                job = self._jobs.pop(job_id)
                job.cancel_requested = True
                job.result = None

    def purge(self) -> None:
        with self._lock:
            self._purge()

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)

    def _run(self, job: Job, work: Work) -> None:
        def progress(done: int, total: int) -> None:
            job.done, job.total = done, total
            if job.cancel_requested:
                raise _Stop("cancelled")
            if self._clock() - job.started_at > self._timeout:
                raise _Stop("timeout")

        with self._lock:
            if job.cancel_requested:
                job.status = "cancelled"
                return
            job.status = "running"
            job.started_at = self._clock()
        try:
            result = work(progress)
            if job.cancel_requested:
                job.status = "cancelled"
            else:
                job.result, job.status = result, "done"
        except _Stop as stop:
            if str(stop) == "timeout":
                job.error = (
                    f"The analysis took longer than {self._timeout // 60} minutes and was stopped. "
                    "Try a smaller file."
                )
                job.status = "failed"
            else:
                job.status = "cancelled"
        except UserError as exc:
            job.error, job.status = exc.message, "failed"
        except Exception as exc:
            # The message may quote cell values, so only the type is logged.
            logger.error("analysis failed: %s", type(exc).__name__)
            job.error, job.status = "The analysis failed on our side. Please try again.", "failed"

    def _purge(self) -> None:
        now = self._clock()
        for job_id in [k for k, v in self._jobs.items() if v.expires_at <= now]:
            job = self._jobs.pop(job_id)
            job.cancel_requested = True  # a job still running past its TTL is told to stop
            job.result = None


jobs = JobRunner()
