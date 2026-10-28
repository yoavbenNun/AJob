"""The complete public feed used by Elbit's own careers search page."""

from src.scrapers.greenhouse import html_text


# Published Cities identifiers from the official careers site's location map.
# Unknown IDs (including relocation/hybrid/region labels) remain unverified.
CITIES = {
    "2191": "נצרת עילית", "416": "חיפה", "104": "קריית שמונה",
    "524": "כרמיאל", "106": "פארק הייטק בר לב", "966": "רמת השרון",
    "975": "רמלה", "109": "איירפורט סיטי", "816": "נתניה",
    "1137": "תל אביב", "1203": "יבנה", "134": "באר שבע",
    "443": "חצרים", "812": "נס ציונה", "992": "רחובות",
    "1008": "ראש העין", "960": "רעננה", "467": "חולון",
    "72": "ערד", "149": "בני ברק", "1050": "שדרות",
    "878": "אופקים", "85": "אשדוד", "628": "לוד",
    "935": "קיסריה", "129": "תל חי", "964": "רמת גן",
    "770": "מודיעין", "1227": "יקנעם",
}


def listings(payload):
    """Project only public job content; never retain staff/extended metadata."""
    if not isinstance(payload, list):
        raise ValueError("Elbit careers response has no listings list")
    result = []
    for item in payload:
        if not isinstance(item, dict):
            result.append({})
            continue
        job_id = item.get("jobId")
        if (type(job_id) is not int or job_id <= 0 or type(item.get("status")) is not int
                or item["status"] != 1 or not isinstance(item.get("jobTitle"), str)):
            result.append({})
            continue
        properties = item.get("extendedProperties")
        city = next((p.get("Value") for p in properties
                     if isinstance(p, dict) and p.get("PropertyName") == "Cities"), None
                    ) if isinstance(properties, list) else None
        location = CITIES.get(str(city), "Not specified")
        content = []
        for key, heading in (("description", ""), ("requirements", "Requirements"), ("skills", "Skills")):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                content.append((heading + "\n" if heading else "") + html_text(value))
        result.append(dict(title=item["jobTitle"], location=location,
                           url=f"https://elbitsystemscareer.com/job/?jid={job_id}",
                           description="\n".join(content)))
    return result
