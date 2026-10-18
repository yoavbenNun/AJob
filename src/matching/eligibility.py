"""Conservative student eligibility, independent of technology overlap and storage."""

import re
from datetime import date

from src.models import JobPosting, ResumeProfile


DEGREE = r"\b(?:bsc|bs|bachelor'?s?|degree)\b"
STUDY = r"\b(?:students?|undergraduate|pursuing|enrolled|studying|in progress|expected)\b"
PREFERRED = r"\b(?:preferred|nice to have|advantage|bonus|not required|not strictly required)\b"
STUDENT_EXCLUSION = r'\b(?:not open to|not suitable for|do not accept|cannot accommodate)\s+(?:current |university )?students?\b'


def _normalize(text: str) -> str:
    text = text.casefold().replace("’", "'").replace("–", "-")
    return re.sub(r'\bb\.?\s*sc\.?|\bb\.s\.?', 'bsc', text)


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


def assess_student_eligibility(resume: ResumeProfile, job: JobPosting) -> tuple[str, tuple[str, ...]]:
    """Reject demonstrated conflicts; unknown facts remain reviewable.

    Full-time and Graduate alone never establish ineligibility. A degree in a
    required qualifications list means an awarded degree unless the advert
    explicitly accepts ongoing studies or equivalent experience.
    """
    studying = candidate_is_studying(resume)
    text = _normalize(job.description + '\n' + job.requirements)
    title = _normalize(job.title)
    # Student references in company boilerplate are not candidate evidence.
    student_evidence = student_acceptance_evidence(text)
    student_role = student_evidence or bool(re.search(r'\b(?:student|intern|internship|internships|working student)\b', title))
    conflicts, doubts = [], []
    # Process both sources, preserving each source's heading scope. Description
    # starts outside qualifications; requirements excerpts start inside them.
    for source, initial in ((job.description, 'unknown'), (job.requirements, 'required')):
        mode = initial
        normalized = _normalize(source)
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
            if studying and not optional and re.search(STUDENT_EXCLUSION + r'|\bgraduates only\b|\bmust have graduated\b', clause):
                if student_evidence:
                    doubts.append('Conflicting student acceptance and graduation requirements; confirm eligibility.')
                else:
                    conflicts.append('Posting explicitly excludes current students or requires graduation.')
            if studying and re.search(DEGREE, clause) and hard:
                accepts_study = bool(re.search(STUDY, clause))
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
