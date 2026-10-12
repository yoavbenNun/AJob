import pytest

from src.models import OutreachContact
from src.storage.outreach_repository import OutreachRepository


def _contact(job_id: int = 7, url: str = "https://www.linkedin.com/in/ari?trk=feed") -> OutreachContact:
    return OutreachContact(job_id, "Ari", "Engineer", "Example", url)


def test_repository_normalizes_and_rejects_duplicate_profile_for_same_job(tmp_path) -> None:
    repository = OutreachRepository(tmp_path / "outreach.db")
    repository.initialize()
    repository.save_contact(_contact())
    with pytest.raises(ValueError, match="already saved"):
        repository.save_contact(_contact(url="https://www.linkedin.com/in/ari/"))


def test_repository_scopes_contacts_and_drafts_to_job(tmp_path) -> None:
    repository = OutreachRepository(tmp_path / "outreach.db")
    repository.initialize()
    contact = _contact()
    contact_id = repository.save_contact(contact)
    assert repository.get_contact(contact_id, 8) is None
    assert repository.list_contacts_for_job(8) == []
    repository.save_approved_draft(7, contact_id, "Approved body", "fingerprint")
    drafts = repository.list_approved_drafts(7)
    assert len(drafts) == 1
    assert drafts[0]["body"] == "Approved body"
    assert repository.list_approved_drafts(8) == []


def test_repository_enforces_contact_foreign_key(tmp_path) -> None:
    repository = OutreachRepository(tmp_path / "outreach.db")
    repository.initialize()
    with pytest.raises(ValueError, match="does not belong"):
        repository.save_approved_draft(7, 999, "Body", "fingerprint")


def test_repository_rejects_contact_from_another_job(tmp_path) -> None:
    repository = OutreachRepository(tmp_path / "outreach.db")
    repository.initialize()
    contact_id = repository.save_contact(_contact(job_id=7))
    with pytest.raises(ValueError, match="does not belong"):
        repository.save_approved_draft(8, contact_id, "Body", "fingerprint")


def test_repository_migrates_and_tracks_activity_without_changing_body(tmp_path) -> None:
    repository = OutreachRepository(tmp_path / "outreach.db")
    repository.initialize()
    contact_id = repository.save_contact(_contact())
    draft_id = repository.save_approved_draft(
        7, contact_id, "Immutable approved body", "fingerprint", "connection_note"
    )
    assert repository.update_draft_activity(draft_id, 7, "copied")
    assert repository.update_draft_activity(draft_id, 7, "sent", "2030-01-02")
    assert repository.update_draft_activity(draft_id, 7, "replied", response_note="Positive reply")
    draft = repository.list_approved_drafts(7)[0]
    assert draft["body"] == "Immutable approved body"
    assert draft["kind"] == "connection_note"
    assert draft["copied_at"] and draft["sent_at"] and draft["replied_at"]
    assert draft["follow_up_at"] == "2030-01-02"
    assert draft["response_note"] == "Positive reply"
