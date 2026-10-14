"""Local SQLite storage for reusable answers and approved application packages."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import hashlib

from src.applications.service import ApplicationPackagePreview, CandidateApplicationProfile
from src.urls import canonical_url


class ApplicationRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_application_profile (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    profile_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS application_packages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    job_title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    job_url TEXT NOT NULL,
                    resume_pdf_path TEXT NOT NULL,
                    resume_docx_path TEXT NOT NULL,
                    resume_pdf_sha256 TEXT NOT NULL,
                    resume_docx_sha256 TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    cover_note TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    submitted_at TEXT,
                    submission_status TEXT NOT NULL DEFAULT 'prepared'
                        CHECK (submission_status IN ('prepared', 'submitted_manually'))
                );
                CREATE INDEX IF NOT EXISTS application_packages_job_idx
                    ON application_packages(job_id, approved_at DESC);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(application_packages)"
                ).fetchall()
            }
            if "submitted_at" not in columns:
                connection.execute(
                    "ALTER TABLE application_packages ADD COLUMN submitted_at TEXT"
                )
            for column in ("resume_pdf_sha256", "resume_docx_sha256"):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE application_packages ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )

    def save_profile(self, profile: CandidateApplicationProfile) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO candidate_application_profile (id, profile_json, updated_at)
                VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET
                profile_json = excluded.profile_json, updated_at = excluded.updated_at""",
                (json.dumps(asdict(profile), ensure_ascii=False),
                 datetime.now().astimezone().isoformat()),
            )

    def get_profile(self) -> CandidateApplicationProfile:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_json FROM candidate_application_profile WHERE id = 1"
            ).fetchone()
        return CandidateApplicationProfile(**json.loads(row["profile_json"])) if row else CandidateApplicationProfile()

    def save_approved_package(self, preview: ApplicationPackagePreview) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO application_packages
                (job_id, job_title, company, job_url, resume_pdf_path, resume_docx_path,
                 resume_pdf_sha256, resume_docx_sha256, profile_json, cover_note, fingerprint, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (preview.job_id, preview.job_title, preview.company, canonical_url(preview.job_url),
                 preview.resume_pdf_path, preview.resume_docx_path,
                 preview.resume_pdf_sha256, preview.resume_docx_sha256,
                 json.dumps(asdict(preview.profile), ensure_ascii=False),
                 preview.cover_note, preview.fingerprint,
                 datetime.now().astimezone().isoformat()),
            )
        return int(cursor.lastrowid)

    def list_packages_for_job(self, job_id: int, job_url: str) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """SELECT * FROM application_packages
                WHERE job_id = ? AND job_url = ? ORDER BY approved_at DESC, id DESC""",
                (job_id, canonical_url(job_url)),
            ).fetchall()

    def mark_submitted_manually(self, package_id: int, job_id: int, job_url: str) -> bool:
        """Record the user's report of an external manual submission."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM application_packages WHERE id = ? AND job_id = ?
                AND job_url = ? AND submission_status = 'prepared'""",
                (package_id, job_id, canonical_url(job_url)),
            ).fetchone()
            if row is None:
                return False
            if not self._files_match_approved_hashes(row):
                raise ValueError("Approved resume files changed after package approval.")
            cursor = connection.execute(
                """UPDATE application_packages
                SET submission_status = 'submitted_manually', submitted_at = ?
                WHERE id = ? AND job_id = ? AND job_url = ? AND submission_status = 'prepared'""",
                (datetime.now().astimezone().isoformat(), package_id, job_id,
                 canonical_url(job_url)),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _files_match_approved_hashes(row: sqlite3.Row) -> bool:
        for path_column, hash_column in (
            ("resume_pdf_path", "resume_pdf_sha256"),
            ("resume_docx_path", "resume_docx_sha256"),
        ):
            path = Path(row[path_column])
            expected = row[hash_column]
            if not expected or not path.is_file():
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return False
        return True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
