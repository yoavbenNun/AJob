"""Shared, conservative boundaries for captured job content (no network or DB)."""

from html.parser import HTMLParser
import re

QUALIFICATION_HEADING = (
    r'(?:(?:minimum|basic|preferred|required)\s+(?:qualifications|requirements)\b|'
    r'(?:qualifications|requirements)(?=\s*:|\s*$)|'
    r"what you(?:'|’)ll need\b|what you will need\b|your experience\b)"
)


class _JobText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], []

    def handle_starttag(self, tag, attrs):
        if tag in {'nav', 'footer', 'aside', 'script', 'style'}:
            self.hidden.append(tag)
        if not self.hidden and tag in {'p', 'li', 'div', 'section', 'br', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in {'p', 'li', 'div', 'section', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def clean_job_text(text: str) -> str:
    """Remove only explicit page chrome; retain genuine requirement lines."""
    if re.search(r'<(?:html|body|div|p|li|section|nav|footer|h[123])\b', text, re.I):
        parser = _JobText()
        parser.feed(text)
        text = ''.join(parser.parts)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if re.fullmatch(r'job alerts\s*[:.]?', line, re.I):
            continue  # Often a navigation label above the actual posting.
        if re.fullmatch(r'(?:recommended jobs|related jobs|similar jobs|recently viewed jobs|'
                        r'privacy policy|cookie policy|our inclusive culture|our commitment)\s*[:.]?', line, re.I):
            break
        if re.match(r'our inclusive culture empowers|if you have a disability and need a workplace accommodation', line, re.I):
            break
        # URLs, including .html and cloud-provider domains, are not skill evidence.
        line = re.sub(r'https?://\S+', '', line)
        if line:
            lines.append(line)
    return '\n'.join(lines)


def description_problem(text: str, requirements: str = '', status_code: int | None = None) -> str | None:
    if status_code is not None and status_code >= 400:
        return f'HTTP {status_code}: job detail unavailable'
    plain = clean_job_text(text)
    if not plain.strip():
        return description_problem(requirements) if requirements.strip() else 'Job description and requirements are missing'
    first = plain.splitlines()[:8]
    marker = r'(?:page not found[.!]?|(?:error\s*)?(?:404|403)(?:\s*[-:]?\s*(?:not found|forbidden))?[.!]?|'
    marker += r'access denied[.!]?|access to this page has been denied[.!]?|'
    marker += r'the (?:url|page) you (?:have )?(?:provided|requested) is (?:invalid|not available)[.!]?|'
    marker += r'verify (?:that )?you are (?:a )?human[.!]?|please complete the (?:captcha|security check)[.!]?|'
    marker += r'just a moment\.{0,3}|captcha)'
    if any(re.fullmatch(marker, line, re.I) for line in first):
        return 'Captured content is an error, access challenge, or CAPTCHA page'
    return None


def qualification_text(text: str) -> str:
    cleaned = clean_job_text(text)
    heading = re.search(r'(?im)^\s*' + QUALIFICATION_HEADING, cleaned)
    return cleaned[heading.start():] if heading else cleaned


def professional_job_text(title: str, description: str, requirements: str) -> str:
    """Prefer explicit qualifications; otherwise keep actual role responsibilities."""
    cleaned_requirements = qualification_text(requirements)
    if cleaned_requirements.strip():
        # Preserve explicit requirements outside the captured excerpt as well.
        extra = [line for line in clean_job_text(description).splitlines() if re.search(
            r'\b(?:must|required|proficien\w*|experience (?:with|in)|knowledge of|programming (?:in|experience))\b|'
            r"^(?:[-•]\s*)?(?:(?:you will|you'll)\s+)?(?:build|develop|implement|design|write|maintain|test|debug)\b", line, re.I)]
        return '\n'.join((title, cleaned_requirements, *extra))
    lines = clean_job_text(description).splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(
        r'^' + QUALIFICATION_HEADING, line, re.I)]
    if starts:
        lines = lines[min(starts):]
    else:
        role_starts = [i for i, line in enumerate(lines) if re.fullmatch(
            r'(?:job summary|job description|responsibilities|the role|your role|what you will do)\s*:', line, re.I)
            or re.fullmatch(r'(?:job summary|job description|responsibilities|the role|your role|what you will do)', line, re.I)]
        if role_starts:
            lines = lines[min(role_starts):]
    return title + '\n' + '\n'.join(lines)
