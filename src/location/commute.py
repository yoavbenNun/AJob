"""Job location eligibility and optional driving estimates."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# Match whole words and phrases. Unrecognized labels require manual review.
_FOREIGN_COUNTRY_LABELS = frozenset(
    {
        "australia", "austria", "belgium", "brazil", "canada", "china",
        "czech republic", "denmark", "finland", "france", "germany", "india",
        "ireland", "italy", "japan", "mexico", "netherlands", "norway", "poland",
        "portugal", "romania", "singapore", "south korea", "spain", "sweden",
        "serbia", "slovakia", "malaysia", "vietnam", "switzerland", "taiwan", "uk", "united kingdom", "united states", "usa", "prc",
    }
)
_FOREIGN_CITY_LABELS = frozenset(
    {"beijing", "krakow", "kraków", "london", "munich", "warsaw", "zurich", "zürich"}
)
_ISRAEL_LOCATION_LABELS = frozenset(
    {
        "israel", "isr", "tel aviv", "haifa", "jerusalem", "herzliya",
        "petah tikva", "rishon lezion", "rehovot", "raanana", "ra anana",
        "kfar saba", "netanya", "beer sheva", "beersheba", "yokneam",
        "caesarea", "hod hasharon", "ramat gan", "bnei brak",
        "eilat", "safed", "tzfat", "tiberias", "nahariya", "akko",
        "kiryat shmona", "kiryat shemona", "dimona", "arad", "karmiel",
        "kiryat gat", "yavne", "holon", "ashdod", "modiin", "modi in",
        "rosh haayin", "rosh ha ayin", "nes ziona",
        "ישראל", "תל אביב", "חיפה", "ירושלים", "הרצליה",
        "אילת", "צפת", "טבריה", "נהריה", "עכו", "קריית שמונה",
        "קריית גת", "יבנה", "חולון", "אשדוד", "מודיעין", "ראש העין", "נס ציונה",
    }
)


@dataclass(frozen=True, slots=True)
class CommuteResult:
    status: str
    eligible: bool
    minutes: int | None
    reason: str
    provider: str | None = None


class DrivingTimeProvider(Protocol):
    name: str

    def driving_minutes(self, origin: str, destination: str) -> int | None: ...


def classify_job_location(destination: str) -> CommuteResult:
    """Prefer a verified Israeli option; keep unresolved alternatives reviewable."""
    destinations = _location_options(destination)
    if any(_is_unrestricted_remote(value) for value in destinations):
        return CommuteResult("remote", True, None, "Remote work is available from Israel.")
    if any(_is_israeli_destination(value) for value in destinations):
        return CommuteResult("israel", True, None, "An Israeli job location is available.")
    if destinations and all(
        _is_clearly_outside_israel(value) and not _has_location_label(value, _ISRAEL_LOCATION_LABELS)
        for value in destinations
    ):
        return CommuteResult("outside_israel", False, None, "Job location is outside Israel.")
    return CommuteResult("unknown", False, None, "Job location needs manual review.")


def evaluate_commute(
    origin: str,
    destination: str,
    provider: DrivingTimeProvider | None,
) -> CommuteResult:
    """Add an optional driving estimate without changing location eligibility."""
    location = classify_job_location(destination)
    if location.status != "israel" or not origin.strip() or provider is None:
        return location

    estimates = []
    for value in _location_options(destination):
        if not _is_israeli_destination(value):
            continue
        try:
            minutes = provider.driving_minutes(origin, value)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
        if minutes is not None:
            estimates.append(minutes)
    return CommuteResult(
        location.status, location.eligible, min(estimates) if estimates else None,
        location.reason, provider.name,
    )


def _location_options(destination: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in re.split(r";|\s+/\s+", destination) if value.strip())


def _is_israeli_destination(location: str) -> bool:
    return _has_location_label(location, _ISRAEL_LOCATION_LABELS) and not _is_clearly_outside_israel(location)


def resolve_job_destination(location: str, description: str) -> str:
    """Prefer a stored location, with a conservative detail-text fallback."""
    if location.strip() and location.casefold() != "not specified":
        return location.strip()
    lines = [" ".join(line.split()) for line in description.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.casefold() != "place":
            continue
        values: list[str] = []
        for candidate in lines[index + 1 : index + 8]:
            if candidate.casefold() in {"bar_chart", "apply", "info_outline"}:
                break
            normalized = candidate.strip(" ;")
            if normalized and not normalized.startswith("+"):
                values.append(normalized)
        if values:
            return "; ".join(values)
    return location.strip() or "Not specified"


def is_israel_or_remote_location(location: str) -> bool:
    """Return whether a location is explicitly in Israel or safely remote."""
    return classify_job_location(location).eligible


class OpenStreetMapDrivingProvider:
    """No-key geocoding and OSRM routing for low-volume personal use."""

    name = "OpenStreetMap/OSRM (estimated, no live traffic)"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._coordinates: dict[str, tuple[float, float] | None] = {}
        self._routes: dict[tuple[str, str], int | None] = {}
        self._last_geocode_at = 0.0

    def driving_minutes(self, origin: str, destination: str) -> int | None:
        key = (origin.casefold(), destination.casefold())
        if key in self._routes:
            return self._routes[key]
        start = self._geocode(origin)
        end = self._geocode(destination)
        if start is None or end is None:
            self._routes[key] = None
            return None
        start_lat, start_lon = start
        end_lat, end_lon = end
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{start_lon},{start_lat};{end_lon},{end_lat}?overview=false"
        )
        payload = self._get_json(url)
        routes = payload.get("routes", [])
        minutes = round(float(routes[0]["duration"]) / 60) if routes else None
        self._routes[key] = minutes
        return minutes

    def _geocode(self, query: str) -> tuple[float, float] | None:
        key = query.casefold()
        if key in self._coordinates:
            return self._coordinates[key]
        elapsed = time.monotonic() - self._last_geocode_at
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        url = "https://nominatim.openstreetmap.org/search?" + urlencode(
            {"q": query, "format": "jsonv2", "limit": 1}
        )
        payload = self._get_json(url)
        self._last_geocode_at = time.monotonic()
        coordinates = (
            (float(payload[0]["lat"]), float(payload[0]["lon"])) if payload else None
        )
        self._coordinates[key] = coordinates
        return coordinates

    def _get_json(self, url: str):
        request = Request(url, headers={"User-Agent": "AJob/1.0 personal-use"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def _is_unrestricted_remote(location: str) -> bool:
    """Accept only remote labels that explicitly permit Israel or worldwide work."""
    lowered = " ".join(location.casefold().split())
    if lowered in {"worldwide remote", "remote worldwide", "global remote", "remote anywhere"}:
        return True
    return bool(re.fullmatch(r"remote\s*[,(-]\s*israel\s*\)?", lowered))


def _is_clearly_outside_israel(location: str) -> bool:
    return (
        _has_location_label(location, _FOREIGN_COUNTRY_LABELS | _FOREIGN_CITY_LABELS)
        or bool(re.fullmatch(r"(?:US|U\.S\.?)\s+remote|remote[ ,(\-]+(?:US|U\.S\.?)(?:\s+only)?\)?",
                             location.strip(), re.IGNORECASE))
    )


def _has_location_label(location: str, labels: frozenset[str]) -> bool:
    normalized = " " + " ".join(re.sub(r"[^\w]+", " ", location.casefold()).split()) + " "
    return any(f" {label} " in normalized for label in labels)
