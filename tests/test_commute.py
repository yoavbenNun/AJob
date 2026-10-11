from src.location.commute import evaluate_commute, resolve_job_destination


class FakeProvider:
    name = "fake driving provider"

    def __init__(self, minutes: int | None) -> None:
        self.minutes = minutes

    def driving_minutes(self, origin: str, destination: str) -> int | None:
        return self.minutes


class DestinationProvider:
    name = "fake driving provider"

    def driving_minutes(self, origin: str, destination: str) -> int | None:
        return {"Tel Aviv, Israel": 35, "Haifa, Israel": 95}[destination]


def test_commute_boundaries() -> None:
    assert evaluate_commute("A, Israel", "B, Israel", FakeProvider(90)).status == "suitable"
    assert evaluate_commute("A, Israel", "B, Israel", FakeProvider(91)).status == "borderline"
    assert evaluate_commute("A, Israel", "B, Israel", FakeProvider(120)).eligible
    assert not evaluate_commute("A, Israel", "B, Israel", FakeProvider(121)).eligible


def test_remote_is_eligible_without_provider() -> None:
    result = evaluate_commute("Rishon Lezion, Israel", "Remote", None)
    assert result.status == "remote"
    assert result.eligible


def test_country_limited_remote_role_is_not_treated_as_global_remote() -> None:
    result = evaluate_commute("Rishon Lezion, Israel", "Remote, Poland", None)
    assert result.status == "outside_israel"
    assert not result.eligible


def test_parenthesized_israel_remote_role_is_eligible() -> None:
    result = evaluate_commute("Rishon Lezion, Israel", "Remote (Israel)", None)
    assert result.status == "remote"
    assert result.eligible


def test_multiple_israel_locations_use_shortest_drive() -> None:
    result = evaluate_commute(
        "Rishon Lezion, Israel", "Tel Aviv, Israel; Haifa, Israel", DestinationProvider()
    )
    assert result.minutes == 35
    assert result.status == "suitable"


def test_foreign_location_is_rejected_without_routing() -> None:
    result = evaluate_commute("Rishon Lezion, Israel", "Warsaw, Poland", None)
    assert result.status == "outside_israel"
    assert not result.eligible


def test_unknown_location_requires_manual_review() -> None:
    result = evaluate_commute("Rishon Lezion, Israel", "Not specified", FakeProvider(20))
    assert result.status == "unknown"
    assert not result.eligible


def test_detail_location_fallback_reads_google_place_marker() -> None:
    description = "Role title\nplace\nWarsaw, Poland\n; Kraków, Poland\nbar_chart\nIntern"
    assert resolve_job_destination("Not specified", description) == "Warsaw, Poland; Kraków, Poland"
