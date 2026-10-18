"""Wall-clock bounded source workers; only the parent writes to the database."""

from dataclasses import dataclass, field
import json
import multiprocessing
import math
import os
import signal
import subprocess
import time

from src.filtering.job_filter import classify_job, prefilter_job
from src.scrapers.careers_scraper import CareersScraper, _enrich_job
from src.scrapers.greenhouse import is_greenhouse_board
from src.scrapers.paused_sources import supported
from src.job_content import description_problem
from src.scrapers.workday_details import legacy_workday_url


@dataclass
class DurableSummary:
    status: str = "failed"
    companies_scanned: int = 0
    jobs_found: int = 0
    jobs_relevant: int = 0
    jobs_saved: int = 0
    failures: int = 0
    new_jobs: list = field(default_factory=list)
    metrics: list = field(default_factory=list)

    def counters(self):
        return {key: getattr(self, key) for key in (
            "status", "companies_scanned", "jobs_found", "jobs_relevant", "jobs_saved", "failures"
        )}


class LazyBrowser:
    def __init__(self, headless):
        self.headless = headless
        self.playwright = self.browser = None

    def new_page(self):
        if self.browser is None:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=self.headless)
        return self.browser.new_page()

    def close(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()


def source_worker(company, timeout_ms, max_pages, headless, pipe):
    # Linux workers and Chromium descendants get a separate process group so a
    # source timeout cannot leave an orphan browser consuming the next budget.
    if os.name != "nt":
        os.setsid()
    browser = LazyBrowser(headless)
    try:
        pipe.send(("phase", "http"))
        started = time.monotonic()
        result = CareersScraper(browser, timeout_ms, max_pages).scan_company(company)
        pipe.send(("timing", "http_seconds", time.monotonic() - started))
        # Fetch time includes HTTP, browser rendering and feed decoding. CPU
        # filtering is measured separately; no claim of pure wire-level timing.
        if result.error:
            pipe.send(("error", "SourceFetchError"))
            return
        pipe.send(("found", len(result.jobs)))
        detail_failures = 0
        detail_blocked = False
        for job in result.jobs:
            pipe.send(("phase", "processing"))
            started = time.monotonic()
            preliminary = prefilter_job(job)
            pipe.send(("timing", "processing_seconds", time.monotonic() - started))
            if preliminary.relevant:
                detail_error = None
                if not ((is_greenhouse_board(job.source) or supported(company)) and job.description):
                    pipe.send(("phase", "http"))
                    started = time.monotonic()
                    detail_error = 'WorkdayAccessDenied' if detail_blocked else _enrich_job(browser, job, timeout_ms)
                    detail_blocked = detail_error == 'WorkdayAccessDenied'
                    pipe.send(("timing", "http_seconds", time.monotonic() - started))
                pipe.send(("phase", "processing"))
                started = time.monotonic()
                invalid_detail = detail_error or description_problem(job.description, job.requirements)
                if invalid_detail:
                    detail_failures += 1
                accepted = bool(invalid_detail) or classify_job(job).relevant
                pipe.send(("timing", "processing_seconds", time.monotonic() - started))
            else:
                accepted = False
            pipe.send(("job", accepted, job))
        pipe.send(("error", "JobDetailUnavailable") if detail_failures else ("done",))
    except Exception as error:
        pipe.send(("error", type(error).__name__))
    finally:
        browser.close()
        pipe.close()


def stop_worker(process):
    if process.is_alive():
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                # Still reap the owned Python worker if tree cleanup failed.
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)


def scan_with_budgets(companies, store, run_id, slot, *, source_seconds=300,
                      scan_seconds=3600, timeout_ms=30000, max_pages=2,
                      headless=True, worker=source_worker, cancelled=lambda: False):
    if (not math.isfinite(source_seconds) or source_seconds <= 0
            or not math.isfinite(scan_seconds) or not 0 < scan_seconds <= 3600):
        raise ValueError("Positive budgets required; total scan budget cannot exceed 60 minutes")
    enabled = [company for company in companies if company.enabled]
    summary = DurableSummary()
    started = time.monotonic()
    deadline = started + scan_seconds
    known = store.known_urls()  # one snapshot avoids thousands of no-op DB updates
    ctx = multiprocessing.get_context("spawn")
    completed = 0
    try:
        for company in enabled:
            if time.monotonic() >= deadline or cancelled():
                break
            summary.companies_scanned += 1
            source_started = time.monotonic()
            source_deadline = min(deadline, source_started + source_seconds)
            metrics = dict(company=company.name, http_seconds=0.0, processing_seconds=0.0,
                           db_seconds=0.0, db_calls=0, no_op_updates_avoided=0, status="failed")
            parent, child = ctx.Pipe(duplex=False)
            process = ctx.Process(target=worker, args=(company, timeout_ms, max_pages, headless, child))
            phase = None
            phase_started = time.monotonic()
            try:
                process.start()
                child.close()
                while time.monotonic() < source_deadline and not cancelled():
                    if not parent.poll(min(0.1, max(0, source_deadline - time.monotonic()))):
                        if not process.is_alive():
                            break
                        continue
                    try:
                        event = parent.recv()
                    except EOFError:
                        break
                    if event[0] == "phase":
                        phase, phase_started = event[1], time.monotonic()
                    elif event[0] == "timing":
                        metrics[event[1]] += event[2]
                        phase = None
                    elif event[0] == "found":
                        summary.jobs_found += event[1]
                    elif event[0] == "job":
                        _, accepted, job = event
                        alias = legacy_workday_url(job.source, job.job_url)
                        if job.job_url not in known and alias in known:
                            job.job_url = alias
                        db_started = time.monotonic()
                        before = store.statements
                        if accepted:
                            job.is_relevant = True
                            summary.jobs_relevant += 1
                            if store.save_discovery(job, slot):
                                summary.new_jobs.append(job)
                            known.add(job.job_url)
                            summary.jobs_saved += 1
                        elif job.job_url in known:
                            store.reject_known(job.job_url)
                        else:
                            metrics["no_op_updates_avoided"] += 1
                        metrics["db_seconds"] += time.monotonic() - db_started
                        metrics["db_calls"] += store.statements - before
                    elif event[0] == "done":
                        metrics["status"] = "full"
                        completed += 1
                        break
                    elif event[0] == "error":
                        metrics["error_type"] = event[1]
                        break
                else:
                    metrics["status"] = "partial"
                    metrics["error_type"] = "Cancelled" if cancelled() else "BudgetExceeded"
            finally:
                if phase:
                    metrics[phase + "_seconds"] += time.monotonic() - phase_started
                if process.pid is not None:
                    stop_worker(process)
                    process.join(timeout=2)
                child.close()
                parent.close()
                metrics["elapsed_seconds"] = time.monotonic() - source_started
                if metrics["status"] != "full":
                    summary.failures += 1
                summary.metrics.append(metrics)
                store.record_metrics(run_id, company.name, metrics)
                print(json.dumps({"event": "source_metrics", **metrics}), flush=True)
    except KeyboardInterrupt:
        summary.failures += 1
    summary.status = ("full" if completed == len(enabled) else
                      "partial" if completed or summary.jobs_saved else "failed")
    return summary
