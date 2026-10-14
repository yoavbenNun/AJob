from src.location.commute import (
    evaluate_commute,
    is_israel_or_remote_location,
    resolve_job_destination,
)


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


def test_china_and_beijing_locations_are_rejected_without_routing() -> None:
    for destination in ("Beijing, China", "Beijing", "Shanghai, China"):
        result = evaluate_commute("Rishon Lezion, Israel", destination, None)
        assert result.status == "outside_israel"
        assert not result.eligible


def test_common_foreign_country_labels_are_rejected_without_routing() -> None:
    for destination in (
        "Berlin, Germany", "Germany - Hybrid", "Germany.",
        "Toronto, Canada", "Remote (United Kingdom)",
    ):
        result = evaluate_commute("Rishon Lezion, Israel", destination, None)
        assert result.status == "outside_israel"
        assert not result.eligible


def test_israeli_and_unknown_city_only_locations_are_not_rejected() -> None:
    for destination in ("Tel Aviv, Israel", "Rishon Lezion", "Exampleville"):
        result = evaluate_commute("Rishon Lezion, Israel", destination, None)
        assert result.status == "unknown"


def test_unknown_location_requires_manual_review() -> None:
    result = evaluate_commute("Rishon Lezion, Israel", "Not specified", FakeProvider(20))
    assert result.status == "unknown"
    assert not result.eligible


def test_dashboard_location_scope_accepts_only_israel_and_safe_remote() -> None:
    for location in (
        "Tel Aviv, ISR", "Haifa, Israel", "Jerusalem District", "Kiryat Gat",
        "Remote (Israel)", "Worldwide remote", "תל אביב, ישראל",
    ):
        assert is_israel_or_remote_location(location)
    for location in (
        "Warsaw, Poland", "London, UK", "Zürich, Switzerland",
        "Bengaluru, India", "Remote, Poland", "Not specified", "Remote", "Austin, TX",
        "Seattle", "Remote - Europe", "US Remote", "EMEA Remote",
        "Fully remote (Germany only)",
    ):
        assert not is_israel_or_remote_location(location)


def test_detail_location_fallback_reads_google_place_marker() -> None:
    description = "Role title\nplace\nWarsaw, Poland\n; Kraków, Poland\nbar_chart\nIntern"
    assert resolve_job_destination("Not specified", description) == "Warsaw, Poland; Kraków, Poland"
