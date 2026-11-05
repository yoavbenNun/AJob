from pathlib import Path
from src.models import JobPosting
from src.web.app import _description_requirements, _summarize_role

FIXTURE = Path(__file__).parent / "fixtures" / "taboola_10437_description.txt"

def job(text):
    return JobPosting("Software Engineer Intern", "Taboola", "Israel", "https://example.test/job", description=text)

def test_taboola_real_capture_and_separate_qualifications():
    text = FIXTURE.read_text(encoding="utf-8-sig")
    original = job(text)
    expected = next(line for line in text.splitlines() if line.startswith("As a Software Engineer Intern on the Ad Creatives"))
    assert _summarize_role(original) == expected
    sections = _description_requirements(text)
    assert len(sections["required"]) == 5 and len(sections["preferred"]) == 2
    assert all(any(term.casefold() in item.casefold() for item in sections["required"]) for term in ("Java", "object-oriented", "SQL", "distributed", "Generative AI", "fundamentals"))
    assert "Spark" in sections["preferred"][0] and "cloud" in sections["preferred"][1]
    assert all("Taboola" not in line for lines in sections.values() for line in lines)
    assert original.description == text

def test_explicit_role_and_requirements():
    text = "Role overview:\nDevelop backend services. Maintain API integrations.\nRequirements:\nPython experience\nPreferred qualifications:\nCloud knowledge\nAbout the company:\nMarketing"
    assert _summarize_role(job(text)) == "Develop backend services. Maintain API integrations."
    assert _description_requirements(text) == {"required": ["Python experience"], "preferred": ["Cloud knowledge"]}

def test_duties_and_unknown_heading_boundary():
    text = "Responsibilities:\nBuild backend APIs. Maintain CI pipelines.\nQualifications:\nPython experience\nBenefits:\nExcellent benefits"
    assert _summarize_role(job(text)) == "Build backend APIs. Maintain CI pipelines."
    assert _description_requirements(text) == {"required": ["Python experience"]}

def test_ambiguous_sources_do_not_invent_sections():
    assert _description_requirements("We develop APIs and use Python.") == {}
    assert _summarize_role(job("Welcome to our company. We are hiring great people.")) == ""

def test_adjacent_restriction_and_partial_sentence():
    text = "About the role:\nBuild deployment tools. These tools must never target production. Maintain CI pipelines.\nRequirements:\nPython"
    assert "must never target production" in _summarize_role(job(text))
    partial = "About the role:\nBuild APIs. Maintain CI pipelines. You will\nRequirements:\nPython"
    assert _summarize_role(job(partial)) == ""

def test_taboola_detail_renders_sections_without_changing_job(tmp_path):
    from test_web_dashboard import _client_with_jobs
    from src.storage.job_repository import JobRepository
    client, _ = _client_with_jobs(tmp_path)
    repo = JobRepository(tmp_path / 'jobs.db')
    text = FIXTURE.read_text(encoding='utf-8-sig')
    j = job(text)
    job_id = repo.save(j)
    before = repo.get_by_id(job_id)
    page = client.get(f'/jobs/{job_id}')
    assert page.status_code == 200
    overview = page.text.split('id="overview"', 1)[1].split('</details>', 1)[0]
    requirements = page.text.split('id="requirements"', 1)[1].split('</details>', 1)[0]
    assert 'Ad Creatives team' in overview and 'Not enough information' not in overview
    assert 'Required qualifications' in requirements and 'Nice to have' in requirements
    assert 'Java' in requirements and 'Spark' in requirements and 'cloud' in requirements
    assert 'Why Taboola' not in requirements and 'Equal opportunity' not in requirements
    assert repo.get_by_id(job_id) == before
