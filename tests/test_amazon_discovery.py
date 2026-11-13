from http.client import IncompleteRead
from urllib.parse import parse_qs, urlsplit
import time
import pytest

from src.config_loader import load_companies
from src.models import JobPosting
from src.scrapers import amazon
from src.scrapers.budgeted_scan import scan_with_budgets
from src.scrapers.careers_scraper import CareersScraper
from test_scan_reliability import store


def company():
    return next(c for c in load_companies("config/companies.yaml") if c.name == "Amazon")


def row(identity, title="Software Development Student", city="Haifa", slug="software-development-student"):
    return dict(id_icims=str(identity), job_path=f"/en/jobs/{identity}/{slug}", title=title,
                city=city, country_code="ISR")


def test_two_queries_union_and_paginate_with_delayed_response(monkeypatch):
    calls = []

    def fetch(url, timeout):
        query = parse_qs(urlsplit(url).query)
        assert query["country"] == ["ISR"] and 0 < timeout <= 1
        term, offset = query["base_query"][0], int(query["offset"][0])
        calls.append((term, offset))
        if term == "software student":
            return dict(hits=11, jobs=[row(i) for i in range(1, 11)] if offset == 0 else [row(10558894)])
        time.sleep(.02)
        return dict(hits=3, jobs=[
            row(10558894, slug="renamed?ref=example"),
            row(10592104, "2027 Software Dev Engineer Intern", "Tel Aviv"),
            row(10592105, "2027 Software Dev Engineer Intern"),
        ])

    monkeypatch.setattr(amazon, "_fetch", fetch)
    result = CareersScraper(None, timeout_ms=2000).scan_company(company())
    assert not result.error
    assert calls == [("software student", 0), ("software student", 10), ("software intern", 0)]
    assert len(result.jobs) == 13
    by_id = {amazon.amazon_job_id(j.job_url): j for j in result.jobs}
    assert by_id["10592104"].location == "Tel Aviv, ISR"
    assert by_id["10592105"].location == "Haifa, ISR"
    assert by_id["10558894"].job_url.endswith("/software-development-student")


@pytest.mark.parametrize("failure", [TimeoutError(), IncompleteRead(b"")])
def test_partial_query_retains_previous_page_and_other_query(monkeypatch, failure):
    def fetch(url, _timeout):
        query = parse_qs(urlsplit(url).query)
        if query["base_query"] == ["software intern"]:
            return dict(hits=2, jobs=[row(10592104), {**row(10592105), "id_icims": "999"}])
        if query["offset"] == ["10"]:
            raise failure
        return dict(hits=11, jobs=[row(i) for i in range(1, 11)])

    monkeypatch.setattr(amazon, "_fetch", fetch)
    result = CareersScraper(None).scan_company(company())
    assert result.error == "AmazonDiscoveryIncomplete"
    assert len(result.jobs) == 11 and not result.closure_snapshot
    assert amazon.amazon_job_id(result.jobs[-1].job_url) == "10592104"


def _alias_worker(source, timeout, pages, headless, pipe):
    for identity in (10558894, 10592104):
        pipe.send(("job", True, JobPosting("Software Development Student", source.name, "Haifa, ISR",
                  f"https://www.amazon.jobs/en/jobs/{identity}/new-slug?ref=example", source=source.careers_url)))
    pipe.send(("error", "AmazonDiscoveryIncomplete"))
    pipe.close()


def test_partial_scan_preserves_ignored_alias_and_notification_history(store):
    source = company()
    original = JobPosting("Software Development Student", source.name, "Haifa, ISR",
                          "https://www.amazon.jobs/en/jobs/10558894/old-slug", status="ignored")
    assert store.save_discovery(original, "old")
    history = tuple(store.execute("SELECT * FROM notification_outbox WHERE job_id=?", (original.id,)).fetchone())
    for slot in ("first", "second"):
        result = scan_with_budgets([source], store, store.start_run(slot), slot, worker=_alias_worker)
        assert result.failures == 1 and result.jobs_saved == 2
        assert len(result.new_jobs) == (1 if slot == "first" else 0)
    assert len(store.known_urls()) == 2
    assert store.repository.get_by_id(original.id).status == "ignored"
    assert tuple(store.execute("SELECT * FROM notification_outbox WHERE job_id=?", (original.id,)).fetchone()) == history
    assert len(store.execute("SELECT * FROM notification_outbox").fetchall()) == 2
