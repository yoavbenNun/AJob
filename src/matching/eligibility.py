"""Conservative student eligibility, independent of technology overlap and storage."""

import re
from calendar import monthrange
from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.models import JobPosting, ResumeProfile
from src.job_content import description_problem, clean_job_text


DEGREE = r"\b(?:bsc|bs|bachelor'?s?|degree)\b"
STUDY = r"\b(?:students?|undergraduate|pursuing|enrolled|studying|in progress|expected)\b"
PREFERRED = r"\b(?:preferred|nice to have|advantage|bonus|not required|not strictly required)\b"
STUDENT_EXCLUSION = r'\b(?:not open to|not suitable for|do not accept|cannot accommodate)\s+(?:current |university )?students?\b'
MASTERS = r"\b(?:master'?s?|msc|ms)\b"
BACHELORS = r"\b(?:bachelor'?s?|bsc|bs|undergraduate)\b"
ENROLLED = r'\b(?:pursuing|enrolled|enrollment|studying|registered|student(?:s)?)\b'
STUDY_COUNTRIES = {
    'Israel': r'\bisrael(?:i)?\b',
    'Czech Republic': r'\bczech(?:ia| republic)?\b',
    'Germany': r'\bgerman(?:y)?\b',
    'France': r'\bfrance\b|\bfrench\b',
    'United Kingdom': r'\bunited kingdom\b|\buk\b|\bbritish\b',
    'United States': r'\bunited states\b|\busa\b|\bamerican\b',
    'Canada': r'\bcanad(?:a|ian)\b',
    'Netherlands': r'\bnetherlands\b|\bdutch\b',
    'India': r'\bindia(?:n)?\b',
    'China': r'\bchina\b|\bchinese\b',
    'Australia': r'\baustralia(?:n)?\b',
    'Poland': r'\bpoland\b|\bpolish\b',
}


def _normalize(text: str) -> str:
    text = text.casefold().replace("’", "'").replace("–", "-")
    text = re.sub(r'\bb\.?\s*sc\.?|\bb\.s\.?', 'bsc', text)
    return re.sub(r'\bm\.?\s*sc\.?|\bm\.s\.?', 'msc', text)


def _academic_restrictions(clause, resume, job, *, hard, optional):
    """Use education evidence, never residence, to check study restrictions."""
    if optional:
        return [], []
    conflicts, doubts = [], []
    education = _normalize(resume.education)
    enrollment = bool(re.search(
        r'\b(?:currently\s+)?(?:pursuing|enrolled|studying|registered)\b', clause))
    masters = bool(re.search(MASTERS, clause) and re.search(
        r"\b(?:degree|students?|studies|program)\b|\b(?:msc|ms)\b|^master'?s\b", clause))
    if re.search(r'\b(?:mentor|supervise|collaborate with|work with)\b', clause) and not enrollment:
        masters = False
    graduate_student = bool(re.search(r'\b(?:postgraduate|graduate)[ -]students?\b', clause))
    # Mentioning graduate students in mentorship/responsibilities is not a
    # candidate qualification, even inside a requirements excerpt.
    graduate_requirement = graduate_student and bool(re.search(
        r'^(?:must be (?:a |an )?|currently (?:a |an )?|(?:a |an )?)'
        r'(?:postgraduate|graduate)[ -]student\b|'
        r'\b(?:postgraduate|graduate)[ -]students?\s+(?:only|required)\b|'
        r'\bmust be\s+(?:a |an )?(?:postgraduate|graduate)[ -]students?\b', clause) or enrollment)
    lower_alternative = bool(re.search(BACHELORS, clause) and re.search(
        r'\bor\b|/|\bboth\b', clause))
    lower_alternative = lower_alternative or bool(re.search(
        BACHELORS + r'.{0,25}\band\b.{0,10}' + MASTERS
        + r'.{0,30}\b(?:students|accepted|welcome)\b', clause))
    if (masters or graduate_requirement) and not lower_alternative:
        candidate_context = enrollment or graduate_requirement or hard
        if candidate_context:
            alternative = bool(re.search(r'\bequivalent\s+(?:experience|qualification)', clause))
            advanced = bool(re.search(MASTERS + r'|\b(?:phd|ph\.d|doctorate)\b', education))
            undergraduate = bool(re.search(BACHELORS, education)) and not advanced
            if not advanced:
                reason = 'Master\'s/postgraduate study required; candidate documents only bachelor\'s-level studies.'
                if undergraduate and (hard or enrollment) and not alternative:
                    conflicts.append(reason)
                else:
                    doubts.append('Postgraduate requirement or equivalent experience needs confirmation.')
            elif enrollment and not candidate_is_studying(resume):
                doubts.append('Current postgraduate enrollment is required but is not documented.')
        elif re.match(r"^(?:a |an )?master'?s?\s+degree\b", clause):
            # A bare degree mention outside qualifications is not a hard rule.
            doubts.append('Master\'s degree mentioned without a clear requirement; confirm accepted study levels.')

    university = bool(re.search(r'\b(?:universit\w*|college|academic institution|school)\b', clause))
    if university and re.search(ENROLLED, clause) and (hard or enrollment):
        required = {country for country, pattern in STUDY_COUNTRIES.items()
                    if re.search(pattern, clause)}
        local_only = bool(re.search(r'\blocal\s+(?:universit\w*|college|academic institution)\b', clause))
        if not required and local_only:
            required = {country for country, pattern in STUDY_COUNTRIES.items()
                        if re.search(pattern, _normalize(job.title + ' ' + job.location))}
        documented = {country for country, pattern in STUDY_COUNTRIES.items()
                      if re.search(pattern, education)}
        if required and not (required & documented):
            reason = 'University enrollment in ' + ' or '.join(sorted(required)) + ' required.'
            if documented:
                conflicts.append(reason + ' Documented education is in a different country.')
            else:
                doubts.append(reason + ' Candidate study country is not documented.')
        elif local_only and not required:
            doubts.append('Local university enrollment required; its country could not be verified.')
        unknown_adjective = re.search(
            r'(?:\b(?:at|in)\s+(?:an?\s+)?|^)([a-z]+)\s+(?:university|college)\b', clause)
        regional = unknown_adjective and unknown_adjective[1] not in {
            'a', 'an',
            'accredited', 'recognized', 'recognised', 'reputable', 'international',
            'public', 'private', 'technical', 'research', 'any', 'partner', 'local'}
        if not required and (regional or (re.search(
                r'\b(?:university|college|institution)\b.{0,30}\bin\b', clause) and not re.search(
                r'\b(?:computer|software|electrical|engineering|science|mathematics)\b', clause))):
            # Unrecognized countries/regions remain reviewable, not eligible by
            # omission. This deliberately avoids an exhaustive geography parser.
            doubts.append('Location-specific university enrollment needs confirmation.')
    return conflicts, doubts


def student_acceptance_evidence(text: str) -> bool:
    """Candidate-context evidence, excluding generic company student outreach."""
    text = re.sub(STUDENT_EXCLUSION, '', _normalize(text))
    return bool(re.search(
        r'\b(?:currently\s+)?(?:pursuing|enrolled in|studying towards)\b[^.;\n]{0,100}' + DEGREE
        + r'|\b(?:open to|suitable for|welcomes?|accepts?|flexib\w* for)\s+(?:\w+\s+){0,3}students?\b'
        + r'|\b(?:third|3rd|final)[ -]year\s+(?:\w+\s+){0,3}students?\b', text))


def candidate_is_studying(resume: ResumeProfile) -> bool:
    candidate = _normalize(resume.education + '\n' + resume.summary)
    years = [int(year) for year in re.findall(r'\b20\d{2}\b', _normalize(resume.education))]
    return bool(re.search(STUDY, candidate)) or any(year > date.today().year for year in years)


def _remaining_studies(clause: str, resume: ResumeProfile, today: date):
    requirement = re.search(
        r'\b(\d+|one|two|three|four|five|six)\s+(?:(?:full|academic)\s+)?'
        r'(years?|semesters?|terms?)\s+(?:of\s+(?:studies|study|your degree)\s+)?'
        r'(?:remaining|left|(?:until|before|to)\s+(?:your\s+)?graduation)\b',
        clause,
    )
    if not requirement:
        return None
    number, unit = requirement.groups()
    count = int(number) if number.isdigit() else {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}[number]
    if unit.startswith('year') and not re.search(r'graduat|degree|stud', clause):
        return None
    if not unit.startswith('year'):
        stated = re.search(r'\b(?:(at least|minimum)\s+)?(\d+)\s+(?:(?:full|academic)\s+)?'
                           r'(semesters?|terms?)\s+(?:remaining|left)\b',
                           _normalize(resume.education + '\n' + resume.summary))
        if stated and stated[3].rstrip('s') == unit.rstrip('s'):
            if int(stated[2]) >= count:
                return None
            if not stated[1]:
                return True, f'{count} {unit} remaining required; candidate documents only {stated[2]}.'
        # Calendar time does not establish completed/full academic semesters.
        return False, f'{count} {unit} remaining required; remaining academic terms are not documented.'
    candidate = resume.education + '\n' + resume.summary
    exact = re.findall(r'\b(20\d{2}-\d{2}-\d{2})\b', candidate)
    years = re.findall(r'\b(20\d{2})\b', candidate)
    try:
        if exact and max(date.fromisoformat(value).year for value in exact) == max(int(value) for value in years):
            earliest = latest = max(date.fromisoformat(value) for value in exact)
        elif years:
            year = max(int(value) for value in years)
            earliest, latest = date(year, 1, 1), date(year, 12, 31)
        else:
            return False, f'{count} years until graduation required; graduation date is not documented.'
        required = date(today.year + count, today.month,
                        min(today.day, monthrange(today.year + count, today.month)[1]))
    except ValueError:
        return False, 'Graduation date could not be verified against the remaining-study requirement.'
    if latest < required:
        return True, f'{count} years until graduation required; even the latest possible graduation ({latest.isoformat()}) is too early.'
    if earliest < required:
        return False, f'{count} years until graduation required; graduation year alone cannot establish eligibility.'
    return None


def assess_student_eligibility(resume: ResumeProfile, job: JobPosting) -> tuple[str, tuple[str, ...]]:
    """Reject demonstrated conflicts; unknown facts remain reviewable.

    Full-time and Graduate alone never establish ineligibility. A degree in a
    required qualifications list means an awarded degree unless the advert
    explicitly accepts ongoing studies or equivalent experience.
    """
    studying = candidate_is_studying(resume)
    problem = description_problem(job.description, job.requirements)
    if problem:
        return 'potentially_eligible', (problem + '; student eligibility cannot be verified.',)
    text = _normalize(clean_job_text(job.description) + '\n' + clean_job_text(job.requirements))
    title = _normalize(job.title)
    # Student references in company boilerplate are not candidate evidence.
    student_evidence = student_acceptance_evidence(text)
    student_role = student_evidence or bool(re.search(r'\b(?:student|intern|internship|internships|working student)\b', title))
    conflicts, doubts = [], []
    today = datetime.now(ZoneInfo('Asia/Jerusalem')).date()
    # Process both sources, preserving each source's heading scope. Description
    # starts outside qualifications; requirements excerpts start inside them.
    for source, initial in ((job.description, 'unknown'), (job.requirements, 'required')):
        mode = initial
        normalized = _normalize(clean_job_text(source))
        for clause in re.split(r'[\n;.]|\s+but\s+', normalized):
            clause = clause.strip(' -•\t')
            if not clause:
                continue
            if re.search(r'preferred qualifications|nice to have', clause):
                mode = 'preferred'
            elif re.match(r'(?:(?:minimum|basic|required)\s+)?(?:qualifications|requirements)\b', clause):
                mode = 'required'
            elif re.match(r'responsibilities|our commitment|who we are|benefits|about us', clause):
                mode = 'unknown'
            optional = (bool(re.search(PREFERRED, clause)) or mode == 'preferred'
                        or bool(re.search(r'\bno\b[^.;\n]{0,70}\brequired\b', clause)))
            hard = not optional and (mode == 'required' or bool(re.search(r'\b(?:must|required|minimum|at least)\b', clause)))
            academic_conflicts, academic_doubts = _academic_restrictions(
                clause, resume, job, hard=hard, optional=optional)
            conflicts.extend(academic_conflicts)
            doubts.extend(academic_doubts)
            remaining = _remaining_studies(clause, resume, today) if hard else None
            if remaining:
                rejected, reason = remaining
                (conflicts if rejected else doubts).append(reason)
            if studying and not optional and re.search(STUDENT_EXCLUSION + r'|\bgraduates only\b|\bmust have graduated\b', clause):
                if student_evidence:
                    doubts.append('Conflicting student acceptance and graduation requirements; confirm eligibility.')
                else:
                    conflicts.append('Posting explicitly excludes current students or requires graduation.')
            if studying and re.search(DEGREE, clause) and hard:
                accepts_study = bool(re.search(STUDY, clause)) or bool(re.search(r'\b(?:remaining|left)\b', clause))
                alternative = bool(re.search(r'\bor\s+(?:\w+\s+){0,3}equivalent|equivalent experience', clause))
                completed = bool(re.search(r'\b(?:completed|awarded|earned|graduated|graduate[s]?)\b', clause))
                if not accepts_study and not alternative:
                    if completed or not student_role:
                        conflicts.append('Completed bachelor\'s degree required; candidate is still studying.')
                    else:
                        doubts.append('Degree requirement conflicts with student wording; confirm whether ongoing studies qualify.')
            years = re.search(r'\b(\d+)\s*(?:\+|-(?:\s*\d+))?\s+years?\b[^.;\n]{0,60}\bexperience\b', clause)
            if years and int(years[1]) >= 2 and hard:
                experience = _normalize(resume.experience)
                shown = re.search(r'\b(\d+)\+?\s+years?\b[^.;\n]{0,40}\b(?:professional|industry|industrial|software|development)\b', experience)
                professional = bool(re.search(r'\b(?:professional|industry|industrial|commercial|production)\b', clause))
                if professional and (not experience.strip() or (shown and int(shown[1]) < int(years[1]))):
                    conflicts.append(f'{years[1]} years of professional experience required; not demonstrated by the resume.')
                elif not shown:
                    doubts.append(f'{years[1]} years of experience required; duration is unverified.')
            if studying and hard and re.search(r'\b(?:40\s*hours?|five days|5 days)\b', clause) and not student_evidence:
                conflicts.append('Mandatory full-week availability conflicts with ongoing studies; no student flexibility stated.')
    if conflicts:
        return 'ineligible', tuple(dict.fromkeys(conflicts))
    if doubts:
        return 'potentially_eligible', tuple(dict.fromkeys(doubts))
    if student_role and text.strip():
        return 'student_eligible', ('Posting accepts students or internships; no demonstrated prerequisite conflict.',)
    return 'potentially_eligible', ('Student eligibility or availability is not explicit; confirm with the employer.',)
