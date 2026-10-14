"""Driving-commute eligibility kept separate from professional fit scoring."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# Country names are matched as complete labels, not substrings, so an unknown
# city-only location remains eligible for routing/manual review.
_FOREIGN_COUNTRY_LABELS = frozenset(
    {
        "australia", "austria", "belgium", "brazil", "canada", "china",
        "czech republic", "denmark", "finland", "france", "germany", "india",
        "ireland", "italy", "japan", "mexico", "netherlands", "norway", "poland",
        "portugal", "romania", "singapore", "south korea", "spain", "sweden",
        "switzerland", "taiwan", "uk", "united kingdom", "united states", "usa",
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
        "kiryat gat", "yavne", "holon", "ashdod", "modiin", "modi in",
        "rosh haayin", "rosh ha ayin", "nes ziona",
        "ישראל", "תל אביב", "חיפה", "ירושלים", "הרצליה",
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


def evaluate_commute(
    origin: str,
    destination: str,
    provider: DrivingTimeProvider | None,
) -> CommuteResult:
    """Classify a commute using the agreed 90/120 minute driving bands."""
    destination = destination.strip()
    destinations = tuple(
        value.strip() for value in destination.split(";") if value.strip()
    ) or (destination,)
    if not destination or destination.casefold() == "not specified":
        return CommuteResult("unknown", False, None, "Job location is unknown; manual review required.")
    if all(_is_clearly_outside_israel(value) for value in destinations):
        return CommuteResult("outside_israel", False, None, f"Job location is outside Israel: {destination}.")
    if _is_remote(destination):
        return CommuteResult("remote", True, None, "Remote role; no commute required.")
    if not origin.strip():
        return CommuteResult("unknown", False, None, "Resume location was not found; manual review required.")
    if provider is None:
        return CommuteResult("unknown", False, None, "Driving-time provider is unavailable; manual review required.")
    try:
        estimates = [
            provider.driving_minutes(origin, value)
            for value in destinations
            if not _is_clearly_outside_israel(value)
        ]
        verified = [value for value in estimates if value is not None]
        minutes = min(verified) if verified else None
    except (OSError, ValueError, TimeoutError, KeyError, IndexError, TypeError):
        minutes = None
    if minutes is None:
        return CommuteResult(
            "unknown", False, None,
            "Driving time could not be verified; manual review required.", provider.name,
        )
    if minutes <= 90:
        return CommuteResult("suitable", True, minutes, "Driving time is within 90 minutes.", provider.name)
    if minutes <= 120:
        return CommuteResult("borderline", True, minutes, "Driving time is 91-120 minutes; commute is borderline.", provider.name)
    return CommuteResult("too_far", False, minutes, "Driving time exceeds 120 minutes.", provider.name)


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
    location = location.strip()
    if not location or location.casefold() == "not specified":
        return False
    if _is_unrestricted_remote(location):
        return True
    normalized = " ".join(re.sub(r"[^\w]+", " ", location.casefold()).split())
    return any(
        re.search(rf"(?<!\w){re.escape(label)}(?!\w)", normalized)
        for label in _ISRAEL_LOCATION_LABELS
    )


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


def _is_remote(location: str) -> bool:
    lowered = " ".join(location.casefold().split())
    if lowered in {"remote", "fully remote", "worldwide remote", "remote worldwide"}:
        return True
    return bool(re.fullmatch(r"remote\s*[,(-]\s*israel\s*\)?", lowered))


def _is_unrestricted_remote(location: str) -> bool:
    """Accept only remote labels that explicitly permit Israel or worldwide work."""
    lowered = " ".join(location.casefold().split())
    if lowered in {"worldwide remote", "remote worldwide", "global remote", "remote anywhere"}:
        return True
    return bool(re.fullmatch(r"remote\s*[,(-]\s*israel\s*\)?", lowered))


def _is_clearly_outside_israel(location: str) -> bool:
    normalized = " ".join(re.sub(r"[^\w]+", " ", location.casefold()).split())
    return any(
        re.search(rf"(?<!\w){re.escape(label)}(?!\w)", normalized)
        for label in _FOREIGN_COUNTRY_LABELS | _FOREIGN_CITY_LABELS
    )
