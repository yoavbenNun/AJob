import json
from io import BytesIO

from src.models import Company
from src.scrapers.careers_scraper import (
    CareersScraper,
    WORKDAY_SEARCH_TERMS,
    extract_detail_location,
    extract_jobs_from_links,
    extract_requirements,
    extract_workday_jobs,
    preview_companies,
)


def test_workday_queries_cover_common_early_career_wording() -> None:
    assert WORKDAY_SEARCH_TERMS == (
        "student software", "software intern", "junior software", "graduate software"
    )


def test_extract_detail_location_from_google_markers() -> None:
    description = "Role\nplace\nWarsaw, Poland\n; Kraków, Poland\nbar_chart\nIntern"

    assert extract_detail_location(description) == "Warsaw, Poland; Kraków, Poland"


def test_extract_detail_location_from_apple_page_header() -> None:
    description = (
        "Back to search results\nSoftware Engineering Student\n"
        "Jerusalem, Jerusalem District, Israel\nHardware\nSubmit Resume"
    )

    assert extract_detail_location(description) == "Jerusalem, Jerusalem District, Israel"


def test_preview_companies_prints_only_enabled_companies(capsys) -> None:
    companies = [
        Company("Enabled", "https://example.com/jobs"),
        Company("Disabled", "https://example.org/jobs", enabled=False),
    ]

    count = preview_companies(companies)
    output = capsys.readouterr().out

    assert count == 1
    assert "Enabled" in output
    assert "Disabled" not in output


def test_extract_workday_jobs_normalizes_public_api_response() -> None:
    company = Company(
        "Intel",
        "https://intel.wd1.myworkdayjobs.com/en-US/External",
        allowed_job_hosts=("intel.wd1.myworkdayjobs.com",),
    )
    payload = {
        "jobPostings": [
            {
                "title": "Software Student",
                "externalPath": "/job/Haifa/Software-Student_JR123",
                "locationsText": "Israel, Haifa",
                "bulletFields": ["Student", "Part time"],
            },
            {"title": "Unsafe", "externalPath": "https://evil.example/job/1"},
        ]
    }

    jobs = extract_workday_jobs(company, payload)

    assert len(jobs) == 1
    assert jobs[0].title == "Software Student"
    assert jobs[0].location == "Israel, Haifa"
    assert jobs[0].job_url.startswith("https://intel.wd1.myworkdayjobs.com/en-US/External/job/")


def test_workday_scan_pages_deduplicates_and_keeps_partial_success(monkeypatch) -> None:
    company = Company(
        "Example", "https://example.wd1.myworkdayjobs.com/en-US/External",
        allowed_job_hosts=("example.wd1.myworkdayjobs.com",),
    )
    calls: list[tuple[str, int]] = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        calls.append((body["searchText"], body["offset"]))
        if body["searchText"] == "software intern":
            raise TimeoutError("temporary")
        count = 19 if body["searchText"] == "student software" and body["offset"] == 0 else 1
        postings = [
            {
                "title": f"Software Student {index}",
                "externalPath": f"/job/Israel/Software-Student-{body['offset'] + index}",
                "locationsText": "Israel",
            }
            for index in range(count)
        ]
        if body["searchText"] == "student software" and body["offset"] == 0:
            postings.append({"title": "Unsafe", "externalPath": "https://evil.example/job/1"})
        return BytesIO(json.dumps({"jobPostings": postings}).encode())

    monkeypatch.setattr("src.scrapers.careers_scraper.urlopen", fake_urlopen)

    result = CareersScraper(None, max_pages=2)._scan_workday(company)

    assert result.error is None
    assert len(result.jobs) == 20
    assert ("student software", 20) in calls
    assert ("software intern", 0) in calls


def test_workday_scan_reports_error_when_every_query_fails(monkeypatch) -> None:
    company = Company(
        "Example", "https://example.wd1.myworkdayjobs.com/en-US/External",
        allowed_job_hosts=("example.wd1.myworkdayjobs.com",),
    )

    def fail_urlopen(request, timeout):
        raise TimeoutError("offline")

    monkeypatch.setattr("src.scrapers.careers_scraper.urlopen", fail_urlopen)

    result = CareersScraper(None)._scan_workday(company)

    assert result.jobs == []
    assert result.error and "TimeoutError" in result.error


def test_extract_jobs_from_links_filters_and_deduplicates() -> None:
    company = Company(
        "Example",
        "https://example.com/careers",
        job_url_patterns=("/positions/",),
    )
    links = [
        {
            "href": "https://example.com/positions/123#details",
            "text": "Software Engineering Intern",
            "context": "Software Engineering Intern\nTel Aviv, Israel",
        },
        {
            "href": "https://example.com/positions/123#apply",
            "text": "Software Engineering Intern",
            "context": "Software Engineering Intern\nTel Aviv, Israel",
        },
        {"href": "https://example.com/about", "text": "About", "context": "About"},
    ]

    jobs = extract_jobs_from_links(company, links)

    assert len(jobs) == 1
    assert jobs[0].title == "Software Engineering Intern"
    assert jobs[0].location == "Tel Aviv, Israel"
    assert jobs[0].job_url == "https://example.com/positions/123"


def test_extract_jobs_prefers_title_link_over_generic_apply_link() -> None:
    company = Company(
        "Example",
        "https://example.com/careers",
        job_url_patterns=("/positions/",),
    )
    links = [
        {
            "href": "https://example.com/positions/123",
            "text": "Apply",
            "context": "Apply",
        },
        {
            "href": "https://example.com/positions/123",
            "text": "Software Engineering Intern",
            "context": "Software Engineering Intern\nTel Aviv, Israel",
        },
    ]

    jobs = extract_jobs_from_links(company, links)

    assert len(jobs) == 1
    assert jobs[0].title == "Software Engineering Intern"
    assert jobs[0].location == "Tel Aviv, Israel"


def test_extract_jobs_prefers_rendered_title_over_url_fallback() -> None:
    company = Company(
        "Example",
        "https://example.com/careers",
        job_url_patterns=("/positions/",),
    )
    url = "https://example.com/positions/123-python-intern"

    jobs = extract_jobs_from_links(
        company,
        [
            {"href": url, "text": "", "context": ""},
            {"href": url, "text": "Python Developer Student", "context": "Python Developer Student\nHaifa, Israel"},
        ],
    )

    assert jobs[0].title == "Python Developer Student"
    assert jobs[0].location == "Haifa, Israel"


def test_extract_jobs_deduplicates_equivalent_query_encodings_and_order() -> None:
    company = Company(
        "Example",
        "https://example.com/careers",
        job_url_patterns=("/positions/",),
    )
    links = [
        {
            "href": "https://example.com/positions/123?q=software%20intern&team=platform",
            "text": "Software Engineering Intern",
            "context": "Software Engineering Intern\nTel Aviv, Israel",
        },
        {
            "href": "HTTPS://EXAMPLE.com/positions/123?team=platform&q=software+intern",
            "text": "Software Engineering Intern",
            "context": "Software Engineering Intern\nTel Aviv, Israel",
        },
    ]

    jobs = extract_jobs_from_links(company, links)

    assert len(jobs) == 1
    assert jobs[0].job_url == "https://example.com/positions/123?q=software+intern&team=platform"


def test_extract_jobs_uses_url_slug_when_anchor_has_no_text() -> None:
    company = Company(
        "Example",
        "https://example.com/jobs/results",
        job_url_patterns=("/jobs/results/",),
    )

    jobs = extract_jobs_from_links(
        company,
        [
            {
                "href": "https://example.com/jobs/results/123-software-engineering-intern",
                "text": "",
                "context": "",
            }
        ],
    )

    assert jobs[0].title == "Software Engineering Intern"
    assert jobs[0].location == "Not specified"


def test_extract_requirements_starts_at_known_heading() -> None:
    description = "About the role\nBuild useful things.\nMinimum qualifications\nPython\nGit"

    requirements = extract_requirements(description)

    assert requirements == "Minimum qualifications\nPython\nGit"


def test_extract_jobs_does_not_store_ui_label_as_location() -> None:
    company = Company(
        "Example",
        "https://example.com/jobs",
        job_url_patterns=("/jobs/",),
    )

    jobs = extract_jobs_from_links(
        company,
        [
            {
                "href": "https://example.com/jobs/123-software-intern",
                "text": "Software Intern",
                "context": "Learn more",
            }
        ],
    )

    assert jobs[0].location == "Not specified"


def test_extract_jobs_rejects_matching_path_on_unapproved_host() -> None:
    company = Company(
        "Example",
        "https://careers.example.com/jobs",
        job_url_patterns=("/jobs/",),
        allowed_job_hosts=("ats.example.com",),
    )

    jobs = extract_jobs_from_links(
        company,
        [
            {
                "href": "https://malicious.example/jobs/123-software-intern",
                "text": "Software Intern",
                "context": "Remote",
            },
            {
                "href": "https://ats.example.com/jobs/456-software-intern",
                "text": "Software Intern",
                "context": "Remote",
            },
        ],
    )

    assert [job.job_url for job in jobs] == [
        "https://ats.example.com/jobs/456-software-intern"
    ]
