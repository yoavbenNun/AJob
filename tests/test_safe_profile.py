import json

import pytest

from src.resume.resume_parser import parse_resume_text
from src.resume.safe_profile import build_safe_profile, load_safe_profile_json


def test_safe_profile_excludes_contact_details_and_resume_prose() -> None:
    profile = parse_resume_text(
        "Dana Developer\nTel Aviv, Israel\ndana@example.com\n+972 50 1234567\n"
        "SKILLS\nPython, Docker\nEXPERIENCE\nBuilt a Python payment system for Secret Corp\n"
        "EDUCATION\nB.Sc. Computer Science"
    )

    encoded = build_safe_profile(profile).to_json()
    payload = json.loads(encoded)

    assert payload["location"] == "Tel Aviv, Israel"
    assert payload["experience_skills"] == ["Python"]
    assert payload["education_level"] == "Bachelor's"
    assert "dana@example.com" not in encoded
    assert "Secret Corp" not in encoded
    assert "+972" not in encoded


def test_safe_profile_loader_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unexpected"):
        load_safe_profile_json(
            '{"location":"","skills":[],"experience_skills":[],"project_skills":[],'
            '"education_level":"","email":"secret@example.com"}'
        )
