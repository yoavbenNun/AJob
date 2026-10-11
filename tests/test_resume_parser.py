from pathlib import Path

import pytest

from src.resume.resume_parser import extract_resume_text, parse_resume, parse_resume_text


SAMPLE_RESUME = """Dana Developer
dana@example.com | +972 50-123-4567

SUMMARY
Computer science student who builds backend services.

TECHNICAL SKILLS
Python, FastAPI, PostgreSQL, Docker, Git, JavaScript

EXPERIENCE
Software Engineering Intern, Example Ltd
- Built REST APIs and tested them with Pytest.

PROJECTS
Job Search Agent
- Used Playwright and SQLite.

EDUCATION
B.Sc. Computer Science, Example University
"""


def test_parse_resume_text_extracts_profile() -> None:
    profile = parse_resume_text(SAMPLE_RESUME)

    assert profile.name == "Dana Developer"
    assert profile.location == ""
    assert profile.email == "dana@example.com"
    assert profile.phone == "+972 50-123-4567"
    assert profile.skills == (
        "Python", "JavaScript", "FastAPI", "PostgreSQL", "SQLite", "Docker",
        "Git", "REST APIs", "Playwright", "Pytest",
    )
    assert "Example Ltd" in profile.experience
    assert "Job Search Agent" in profile.projects
    assert "Computer Science" in profile.education


def test_parse_resume_reads_text_file(tmp_path: Path) -> None:
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text(SAMPLE_RESUME, encoding="utf-8")
    profile = parse_resume(resume_path)
    assert profile.source_path == str(resume_path)
    assert profile.name == "Dana Developer"


def test_extract_resume_text_rejects_unsupported_format(tmp_path: Path) -> None:
    resume_path = tmp_path / "resume.rtf"
    resume_path.write_text("resume", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported resume format"):
        extract_resume_text(resume_path)


def test_skill_matching_does_not_treat_java_as_javascript() -> None:
    profile = parse_resume_text("Developer Name\nSKILLS\nJavaScript")
    assert "JavaScript" in profile.skills
    assert "Java" not in profile.skills


def test_parse_resume_reads_docx_file(tmp_path: Path) -> None:
    from docx import Document

    resume_path = tmp_path / "resume.docx"
    document = Document()
    for line in SAMPLE_RESUME.splitlines():
        document.add_paragraph(line)
    document.save(resume_path)

    profile = parse_resume(resume_path)

    assert profile.name == "Dana Developer"
    assert "Python" in profile.skills
    assert "Example Ltd" in profile.experience


def test_parse_resume_combines_split_uppercase_name_and_extended_skills() -> None:
    profile = parse_resume_text(
        """YOAV
BEN-NOON
Software Engineering | Student
PROJECTS
Built C++ systems with Qt6/QML, CMake, WebSockets, microservices, and unit tests.
"""
    )

    assert profile.name == "Yoav Ben-Noon"
    assert profile.skills == (
        "C++",
        "Qt/QML",
        "CMake",
        "WebSockets",
        "Microservices",
        "Unit Testing",
    )


def test_parse_resume_extracts_location_only_from_header() -> None:
    profile = parse_resume_text(
        "Dana Developer\nRishon Lezion, Israel\nSUMMARY\nWorked with a team in London, UK."
    )

    assert profile.location == "Rishon Lezion, Israel"
