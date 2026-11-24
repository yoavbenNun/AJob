"""Deterministic filtering for early-career software roles."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from src.models import JobPosting
from src.matching.eligibility import student_acceptance_evidence


SOFTWARE_TERMS = (
    "software",
    "developer",
    "full stack",
    "frontend",
    "front end",
    "backend",
    "back end",
    "mobile engineer",
    "devops",
    "site reliability",
    "machine learning engineer",
    "qa automation",
    "firmware",
    "embedded software",
)
HARDWARE_ROLE_TERMS = (
    "electrical engineer", "electrical engineering", "hardware engineer",
    "hardware engineering", "asic", "rtl", "vlsi", "physical design",
    "design verification", "silicon validation", "board design", "circuit design",
    "analog", "rf engineer", "fpga",
)
HARDWARE_FOCUS_TERMS = (
    "electrical engineer", "electrical engineering", "hardware engineer",
    "hardware engineering", "rtl", "vlsi", "physical design",
    "design verification", "silicon validation", "board design", "circuit design",
    "analog", "rf engineer", "fpga",
)
DETAIL_SOFTWARE_TERMS = (
    "software development", "develop software",
    "developing software", "build software", "building software", "write software",
    "writing software", "build and maintain scripts", "python-based tooling",
    "develop data pipelines", "maintain data pipelines", "low-level software", "backend", "frontend",
    "firmware development", "develop firmware", "web applications",
)
EARLY_CAREER_TERMS = (
    "intern",
    "internship",
    "internships",
    "student",
    "new grad",
    "new graduate",
    "new graduates",
    "graduate program",
    "junior",
    "entry level",
    "entry-level",
    "apprentice",
    "early career",
)
SENIOR_TERMS = (
    "senior",
    "sr.",
    "staff",
    "principal",
    "lead",
    "manager",
    "director",
    "architect",
    "head of",
)
NON_ENGINEERING_ROLE_TERMS = (
    "analyst",
    "coordinator",
    "marketing",
    "product manager",
    "program manager",
    "recruiter",
    "sales",
)

# Headings, rather than qualification prose, delimit duty evidence.  Keep
# unqualified "Experience" ambiguous: it can describe the role itself.
QUALIFICATION_BOUNDARY = (
    r"minimum qualifications|basic qualifications|preferred qualifications|"
    r"^[ \t]*:?[ \t]*(?:"
    r"(?:(?:job|minimum|basic|preferred|required)[ \t]+)?"
    r"(?:requirements(?:[ \t]+and[ \t]+qualifications)?|qualifications(?:[ \t]+and[ \t]+requirements)?)|"
    r"(?:experience|education|skills)[ \t]+(?:required|requirements|qualifications)|"
    r"(?:required|minimum|basic|preferred)[ \t]+(?:experience|education|skills)"
    r"(?:[ \t]+(?:requirements|qualifications))?|"
    r"(?:(?:required|minimum|basic|preferred)[ \t]+)?"
    r"(?:skills|experience|education|qualifications|requirements)"
    r"(?:(?:[ \t]+and[ \t]+|[ \t]*[&/][ \t]*)"
    r"(?:skills|experience|education|qualifications|requirements))+"
    r"(?:[ \t]+(?:required|requirements))?|"
    r"what you(?:'|’)ll (?:need|bring)|what you will (?:need|bring)|"
    r"what we(?:'|’)re looking for|what we are looking for|what we look for|"
    r"your (?:experience|skills|qualifications|education)"
    r")[ \t]*(?::|$)|\brequirements\s*:"
)


@dataclass(frozen=True, slots=True)
class FilterDecision:
    relevant: bool
    reason: str


def classify_job(job: JobPosting) -> FilterDecision:
    """Classify a role using title plus enriched detail-page evidence."""
    potential = prefilter_job(job)
    if not potential.relevant:
        return potential

    title = _normalize(job.title)
    details = _normalize(f"{job.description} {job.requirements}")
    if needs_google_early_evidence(job):
        if _google_early_qualifications(job.description, job.title):
            return FilterDecision(True, "Google early-career label and minimum software experience confirmed")
        return FilterDecision(False, "Google Engineer II requires verified early-career evidence")
    software_title = any(_contains_phrase(title, term) for term in SOFTWARE_TERMS)
    hardware_focused_title = any(
        _contains_phrase(title, term) for term in HARDWARE_FOCUS_TERMS
    )
    # Process-compliance jobs can say "software" without any programming duties.
    # Keep them eligible for enrichment, but require actual development evidence.
    process_quality_title = _contains_phrase(title, "software quality")
    if not software_title or hardware_focused_title or process_quality_title:
        responsibility_text = re.split(
            QUALIFICATION_BOUNDARY, job.description, maxsplit=1,
            flags=re.IGNORECASE | re.MULTILINE,
        )[0]
        responsibilities = _normalize(responsibility_text)
        develops_code = any(
            re.search(r"\b(?:develop(?:ing)?|writ(?:e|ing))\s+(?:production\s+)?code\b", clause)
            and not re.search(r"\b(?:not|never|no|without)\b|\b\w+n['’]t\b", clause)
            for clause in (_normalize(part) for part in re.split(r"[.;!\n]", responsibility_text))
        )
        automation_pattern = (
            r"(?<!not )(?<!never )\b(?:develop(?:ing)?|build(?:ing)?|implement(?:ing)?)\b"
            r"[^.;!\n]{0,80}\b(?:test\s+)?automation\s+"
            r"(?:frameworks?|infrastructure|tools?)\b"
        )
        automation_match = re.search(automation_pattern, responsibilities)
        develops_automation = (
            (_contains_phrase(title, "automation") or process_quality_title)
            and automation_match
        )
        detail_terms = DETAIL_SOFTWARE_TERMS
        if process_quality_title:
            # Nouns like "software development" in process/audit prose are not
            # evidence that this role develops software. Retain direct duties.
            detail_terms = tuple(term for term in DETAIL_SOFTWARE_TERMS
                                 if term.startswith(('develop ', 'developing ', 'build ',
                                                     'building ', 'write ', 'writing ', 'maintain ')))
        # A rejected duty is not evidence of development work. Check separate
        # responsibility clauses so a negative contrast does not hide a later
        # positive duty, and keep "not only" as affirmative wording.
        software_clauses = tuple(
            _normalize(re.sub(r"\bnot only\b", "", part, flags=re.IGNORECASE))
            for part in re.split(r"[.;!\n]|\bbut\b", responsibility_text, flags=re.IGNORECASE)
        )
        # Coded tests in an existing framework are software work too. Keep the
        # action, language and framework in one affirmative duty clause so a
        # manual testing role's separate Python qualification cannot qualify it.
        if _contains_phrase(title, "automation"):
            develops_automation = develops_automation or any(
                _positive_software_phrase(clause, match.group())
                and any(_contains_phrase(clause, language) for language in
                        ("python", "java", "c#", "c++", "javascript", "typescript"))
                and re.search(r"\b(?:integration\s+)?test(?:ing)?\s+framework\b", clause)
                for clause in software_clauses
                for match in re.finditer(
                    r"\b(?:develop(?:ing)?|extend(?:ing)?|writ(?:e|ing)|implement(?:ing)?)\b"
                    r"[^.;!\n]{0,70}\bautomated tests?\b", clause)
            )
        develops_software = any(
            any(_positive_software_phrase(clause, term) for term in detail_terms)
            for clause in software_clauses
        )
        develops_software = develops_software or any(
            _positive_software_phrase(clause, match.group('duty'))
            and not re.match(r'(?:\s+|\s*:\s*)(?:(?:is|are)\s+)?(?:(?:a|an)\s+)?'
                             r'(?:required|preferred|experience|qualification|prerequisite|skill)\b',
                             clause[match.end():])
            for clause in software_clauses
            for match in [re.match(
                r'^(?:[-*•]\s*)?(?:(?:you will(?: be)?|you are expected to|'
                r'in this role you will|your responsibilities include)\s+)?'
                r'(?P<duty>(?:build|building|develop|developing)\s+(?:a\s+)?full[- ]stack\s+applications?)\b', clause)]
            if match
        )
        if process_quality_title:
            # Preserve genuine coded-test work when the quality title alone no
            # longer suffices. Scope negation to the duty, not supervision or
            # experience wording elsewhere in its clause.
            develops_code = develops_code or any(
                _positive_software_phrase(clause, match.group())
                for clause in software_clauses
                for match in re.finditer(r"\b(?:develop(?:ing)?|writ(?:e|ing))\s+(?:production\s+)?code\b", clause)
            )
            develops_automation = any(
                _positive_software_phrase(clause, match.group())
                for clause in software_clauses
                for match in re.finditer(automation_pattern, clause)
            ) or any(
                _positive_software_phrase(clause, match.group())
                and any(_contains_phrase(clause, language) for language in
                        ('python', 'c++', 'c#', 'java', 'javascript', 'typescript'))
                for clause in software_clauses
                for match in re.finditer(
                    r"\b(?:develop(?:ing)?|writ(?:e|ing)|implement(?:ing)?|build(?:ing)?)\b"
                    r"[^.;!\n]{0,60}\b(?:automated tests?|test automation)\b", clause)
            )
        if not develops_code and not develops_automation and not develops_software:
            return FilterDecision(False, "no software-development responsibilities were found")
    if any(_contains_phrase(title, term) for term in EARLY_CAREER_TERMS):
        return FilterDecision(True, "title contains an early-career marker")

    if student_acceptance_evidence(details):
        return FilterDecision(True, "job details explicitly accept ongoing studies")

    if re.search(
        r"\b(?:currently pursuing\b[^.;\n]{0,80}\b(?:bachelor|master|b\.sc|m\.sc|degree|phd|ph\.d)|currently enrolled|pursuing (?:an? |your )?(?:bachelor|master|b\.sc|m\.sc|degree|phd|ph\.d)|students? (?:in|of|who)|"
        r"suitable for (?:students?|new graduates)|"
        r"(?:internship|intern|new graduate|new grad|entry.level|early career) (?:position|role|program)|"
        r"(?:open to|designed for|for) (?:recent |new )?graduates?|"
        r"(?:recent |new )graduates? (?:are welcome|are encouraged|may apply))\b",
        details,
    ):
        return FilterDecision(True, "job details contain an early-career marker")
    if re.search(r"\b(?:0|zero)\s*(?:-|to)\s*[123]\s+years?\b", details):
        return FilterDecision(True, "job details allow zero years of experience")
    return FilterDecision(False, "no early-career evidence was found")


def prefilter_job(job: JobPosting) -> FilterDecision:
    """Identify detail pages worth opening without claiming final relevance."""
    title = _normalize(job.title)
    if any(_contains_phrase(title, term) for term in NON_ENGINEERING_ROLE_TERMS):
        return FilterDecision(False, "title describes a non-engineering role")
    software_title = any(_contains_phrase(title, term) for term in SOFTWARE_TERMS)
    explicit_software_role = any(_contains_phrase(title, term) for term in (
        "software engineer", "software engineering", "software developer",
        "software development", "firmware engineer", "firmware developer",
        "firmware development",
    ))
    hardware_focused = any(_contains_phrase(title, term) for term in HARDWARE_FOCUS_TERMS)
    if any(_contains_phrase(title, term) for term in HARDWARE_ROLE_TERMS) and (
        not software_title or (hardware_focused and not explicit_software_role)
    ):
        return FilterDecision(False, "title describes a hardware or electrical role")
    if not software_title and not any(
        _contains_phrase(title, term) for term in EARLY_CAREER_TERMS
    ):
        return FilterDecision(False, "title is not software-oriented")
    if (any(_contains_phrase(title, term) for term in SENIOR_TERMS)
            or re.search(r"\b(?:iii|iv)\b", title)
            or (re.search(r"\bii\b", title) and not needs_google_early_evidence(job))):
        return FilterDecision(False, "title contains a senior-level marker")
    return FilterDecision(True, "software-oriented candidate")


def needs_google_early_evidence(job: JobPosting) -> bool:
    """Only allow enrichment of Google Software Engineer II listings."""
    try:
        url = urlsplit(job.job_url)
        return bool(url.scheme == 'https' and url.netloc.lower() == 'www.google.com'
                    and re.fullmatch(r'/about/careers/applications/jobs/results/\d+(?:-[^/]*)?/?', url.path)
                    and re.search(r'\bsoftware engineer ii\b', _normalize(job.title)))
    except ValueError:
        return False


def _google_early_qualifications(description: str, title: str) -> bool:
    # The first Apply control ends the structured listing header. Do not let
    # later body text supply a missing or contradictory career-level label.
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    def title_key(value):
        return ' '.join(re.sub(r'[^\w]+', ' ', value.casefold()).split())
    title_indices = [i for i, line in enumerate(lines) if title_key(line) == title_key(title)]
    if len(title_indices) != 1:
        return False
    start = title_indices[0]
    apply_index = next((i for i, line in enumerate(lines[start + 1:], start + 1)
                        if line.casefold() == 'apply'), None)
    if apply_index is None:
        return False
    header = [line.casefold() for line in lines[start:apply_index]]
    levels = [i for i, line in enumerate(header) if line == 'bar_chart']
    if (len(levels) != 1 or levels[0] + 1 >= len(header)
            or header[levels[0] + 1] != 'early'
            or 'place' not in header[:levels[0]]):
        return False
    body = '\n'.join(lines[apply_index + 1:])
    section = re.search(r'(?is)\bminimum qualifications\s*:\s*(.*?)(?:preferred qualifications\s*:|about the job|responsibilities\s*:|$)', body)
    if not section:
        return False
    minimum = section[1]
    clauses = re.split(r'(?<!\d)\.(?!\d)|;|\n', minimum)
    if not any(re.search(r'\b1\s+year\s+of\s+experience\s+with\s+software\s+development\b', clause, re.I)
               and not re.search(r'\b(?:not|never|no|without|preferred|optional)\b', clause, re.I)
               for clause in clauses):
        return False
    return not any(_exceeds_early_experience(clause) for clause in clauses)


def _exceeds_early_experience(clause: str) -> bool:
    """Fail closed on mandatory experience longer than one year."""
    clause = clause.casefold()
    if (re.search(r'\b(?:preferred|optional|advantage|nice to have)\b', clause)
            and not re.search(r'\b(?:required|minimum|must|at least)\b', clause)):
        return False
    if re.search(r'\b(?:more than|over|exceeding)\s+(?:1|one)\s*(?:year|yr)\b', clause):
        return True
    if re.search(r'\b(?:one|two|three|four|five|\d+)\s+and\s+a\s+half\s+(?:years?|yrs?)\b', clause):
        return True
    numbers = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
               'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
               'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
               'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
               'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40,
               'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
    words = '|'.join(numbers)
    pattern = (rf'\b(\d+(?:\.\d+)?|(?:{words})(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?)'
               rf'(?:\s*\(\s*(?:\d+|{words})\s*\))?(?:\s*(?:\+|or\s+more))?\s*'
               rf'(years?|yrs?\.?|months?|mos?\.?)\b')
    durations = []
    parsed_units = set()
    for match in re.finditer(pattern, clause):
        value, unit = match.groups()
        if value[0].isdigit():
            count = float(value)
        else:
            count = sum(numbers[part] for part in re.split(r'[- ]', value))
        durations.append((count, unit))
        parsed_units.add(match.start(2))
    if re.search(r'\b(?:several|multiple|many)\s+(?:years?|yrs?|months?|mos?)\b', clause):
        return True
    if (re.search(r'\bexperience\b', clause) and any(
            match.start() not in parsed_units
            for match in re.finditer(r'\b(?:years?|yrs?|months?|mos?)\b', clause))):
        return True
    years = [n for n, unit in durations if unit.startswith(('y',))]
    months = [n for n, unit in durations if unit.startswith('m')]
    return any(n > 1 for n in years) or any(n > 12 for n in months) or bool(years and months and max(years) >= 1 and max(months) > 0)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("\u2013", "-").split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(
        rf"(?<![\w]){re.escape(phrase)}(?![\w])",
        text,
    ) is not None


def _positive_software_phrase(clause: str, phrase: str) -> bool:
    for match in re.finditer(rf"(?<![\w]){re.escape(phrase)}(?![\w])", clause):
        # Bind negation to the duty, not unrelated experience/supervision or
        # hardware clauses before it. This is deliberately not general NLU.
        if re.search(
            r"\b(?:not|never|no|without|\w+n['’]t)\s+"
            r"(?:(?:involve|involving|include|including|any|be|being|require|requires|requiring|required|"
            r"expected|responsible|related|for|to|doing|perform|performing|focus|on)\s+){0,5}$",
            clause[:match.start()],
        ):
            continue
        if re.match(r"\s+(?:(?:is|are|will be)\s+)?(?:not|never)\b", clause[match.end():]):
            continue
        return True
    return False
