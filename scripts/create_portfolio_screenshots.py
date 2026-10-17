"""Render four offline portfolio screenshots using fictional, disposable data.

Run with the project venv and installed Playwright Chromium. No cloud database,
career-site request, email, GitHub update, or personal resume is used.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from threading import Thread


ROOT = Path(__file__).resolve().parents[1]


def main():
    # Do this before importing the web module: its default app initializes storage.
    for key in tuple(os.environ):
        if key in {"DATABASE_URL", "RESUME_DIRECTORY", "CANDIDATE_PROFILE_JSON"} or key.startswith("SMTP_"):
            os.environ.pop(key)
    sys.path.insert(0, str(ROOT))
    output = ROOT / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    previous_cwd = Path.cwd()
    with TemporaryDirectory(prefix="ajob-portfolio-") as directory:
        base = Path(directory)
        os.chdir(base)
        try:
            from docx import Document
            from fastapi.testclient import TestClient
            from playwright.sync_api import sync_playwright
            from src.models import JobPosting
            from src.storage.job_repository import JobRepository
            from src.web.app import create_app
            import re

            resume_root = base / "resume"
            resume_root.mkdir()
            document = Document()
            for line in [
                "Alex Example", "SUMMARY", "Software engineering student building backend services.",
                "TECHNICAL SKILLS", "Python, FastAPI, PostgreSQL, SQLite, Docker, Git, JavaScript",
                "EXPERIENCE", "Student Developer, Example Lab", "Built Python APIs with FastAPI and PostgreSQL.",
                "PROJECTS", "Opportunity Tracker", "Developed and tested Python services with SQLite and Docker.",
                "EDUCATION", "B.Sc. Software Engineering, Example University",
            ]:
                document.add_paragraph(line)
            document.save(resume_root / "demo-resume.docx")
            companies = base / "companies.yaml"
            companies.write_text("companies:\n" + "".join(
                f"  - name: {name}\n    careers_url: https://example.test/{index}/jobs\n"
                for index, name in enumerate(["Example Systems", "Sample Labs", "Demo Cloud"])
            ))
            repository = JobRepository(base / "jobs.db")
            repository.initialize()
            for index, (title, company, state, skills) in enumerate([
                ("Backend Software Engineering Intern", "Example Systems", "new", "Python, FastAPI, PostgreSQL, Git"),
                ("Software Development Student", "Sample Labs", "reviewed", "Python, SQLite, Docker"),
                ("Junior Full Stack Developer", "Demo Cloud", "new", "JavaScript, Python, PostgreSQL"),
                ("Python Platform Intern", "Example Systems", "applied", "Python, Docker, Git"),
            ], 1):
                repository.save(JobPosting(
                    title, company, "Tel Aviv, Israel", f"https://example.test/jobs/{index}",
                    description=("Fictional portfolio example. Build maintainable backend services and REST APIs. "
                                 "Work with engineers on data processing, automated tests, and reliable integrations. "
                                 "This student role welcomes candidates pursuing a software engineering degree."),
                    requirements=f"Currently pursuing a B.Sc. in Software Engineering. {skills}.",
                    status=state,
                ))
            app = create_app(
                base / "jobs.db", resume_directory=resume_root,
                outreach_database_path=base / "outreach.db", application_database_path=base / "applications.db",
                notification_recipient_updater=None, company_config_path=companies,
            )
            public = base / "preview"
            public.mkdir()
            shutil.copytree(ROOT / "src/web/static", public / "static")
            with TestClient(app, base_url="http://127.0.0.1") as client:
                detail = client.get("/jobs/1").text
                token = re.search(r'name="csrf_token" value="([^"]+)"', detail)[1]
                snapshots = {
                    "index.html": client.get("/").text,
                    "detail.html": detail,
                    "match.html": client.post("/jobs/1/match", data={"csrf_token": token}).text,
                }
                for name, content in snapshots.items():
                    content = content.replace("http://127.0.0.1/", "/")
                    content = content.replace("</body>", '<div style="position:fixed;bottom:0;left:0;right:0;z-index:10000;text-align:center;background:#12253c;color:white;padding:8px;font:12px sans-serif">PORTFOLIO DEMO · Fictional companies, roles and resume</div></body>')
                    (public / name).write_text(content, encoding="utf-8")
            class QuietHandler(SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(public)))
            Thread(target=server.serve_forever, daemon=True).start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
                        # Capture only static local pages; block all external traffic and writes.
                        page.route("**/*", lambda route: route.continue_() if (
                            route.request.url.startswith(origin + "/") and route.request.method == "GET"
                        ) else route.abort())
                        for filename, url, anchor in [
                            ("dashboard.png", "/index.html", None),
                            ("job-detail.png", "/detail.html", None),
                            ("resume-match.png", "/match.html", "#resume-match"),
                        ]:
                            page.goto(origin + url, wait_until="networkidle")
                            if anchor:
                                page.locator(anchor).evaluate("element => { element.open = true; element.scrollIntoView({behavior: 'instant', block: 'start'}); }")
                            page.screenshot(path=str(output / filename), animations="disabled")
                        page.set_viewport_size({"width": 390, "height": 844})
                        page.goto(origin + "/index.html", wait_until="networkidle")
                        page.screenshot(path=str(output / "mobile.png"), animations="disabled")
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
        finally:
            os.chdir(previous_cwd)
    print("Created four fictional-data screenshots in docs/images.")


if __name__ == "__main__":
    main()
