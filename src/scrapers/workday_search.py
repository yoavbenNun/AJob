"""Official Workday facet discovery and collection-only coverage measurements."""

import re

from src.location.commute import is_israel_or_remote_location, evaluate_commute


def israel_facet(payload):
    """Discover tenant-owned IDs, including nested locationMainGroup facets."""
    candidates = set()
    cities = {}
    def visit(node, parameter='', depth=0):
        if depth > 8 or not isinstance(node, dict):
            return
        parameter = node.get('facetParameter', parameter)
        values = node.get('values', [])
        if not isinstance(values, list):
            return
        if (not values and isinstance(parameter, str) and re.search('country|location', parameter, re.I)
                and isinstance(node.get('id'), str) and node['id']):
            label = str(node.get('descriptor', '')).strip().casefold()
            if label in {'israel', 'ישראל'}:
                candidates.add((parameter, node['id']))
            elif re.match(r'^(?:israel|ישראל)\s*[,/]\s*\S', label):
                cities.setdefault(parameter, set()).add(node['id'])
        for child in values:
            visit(child, parameter, depth + 1)
    facets = payload.get('facets', [])
    if not isinstance(facets, list):
        return None
    for facet in facets:
        visit(facet)
    countries = {item for item in candidates if 'country' in item[0].casefold()}
    choices = countries or candidates
    if len(choices) == 1:
        parameter, value = choices.pop()
        return {parameter: [value]}
    if not choices and len(cities) == 1:
        parameter, values = next(iter(cities.items()))
        return {parameter: sorted(values)}  # OR all officially labeled Israeli offices.
    return None


def explicitly_israel(job):
    # Reuse existing city/country labels, excluding worldwide-only remote labels.
    location = re.sub(r'\b(remote|worldwide|global|anywhere|fully)\b', '', job.location, flags=re.I)
    return is_israel_or_remote_location(location)


def contradicts_israel_filter(jobs):
    return bool(jobs) and all(evaluate_commute('', job.location, None).status == 'outside_israel'
                              for job in jobs)


def search_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('jobPostings'), list):
        raise ValueError('Workday response has no posting list')
    if any(not isinstance(item, dict) for item in payload['jobPostings']):
        raise ValueError('Workday response contains an invalid posting')
    return payload
