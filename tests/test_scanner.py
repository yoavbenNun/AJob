from src.models import Company
from src.scrapers.careers_scraper import (
    extract_jobs_from_links,
    extract_requirements,
    preview_companies,
)


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
