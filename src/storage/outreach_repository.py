"""Local, privacy-minimized SQLite storage for outreach contacts and drafts."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from src.models import OutreachContact
from src.outreach.service import validate_profile_url


RELATIONSHIPS = {"hiring_team", "recruiter", "employee", "alumni", "other"}


class OutreachRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS outreach_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    role_title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    profile_url TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    UNIQUE(job_id, profile_url)
                );
                CREATE TABLE IF NOT EXISTS outreach_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    contact_id INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    basis_fingerprint TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    copied_at TEXT,
                    FOREIGN KEY(contact_id) REFERENCES outreach_contacts(id)
                );
                """
            )

    def save_contact(self, contact: OutreachContact) -> int:
        contact.profile_url = validate_profile_url(contact.profile_url)
        if not contact.display_name.strip() or not contact.role_title.strip() or not contact.company.strip():
            raise ValueError("Name, role title, and company are required.")
        if contact.relationship not in RELATIONSHIPS:
            raise ValueError("Unknown contact relationship.")
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """INSERT INTO outreach_contacts
                    (job_id, display_name, role_title, company, profile_url, relationship,
                     location, notes, created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (contact.job_id, contact.display_name.strip(), contact.role_title.strip(),
                     contact.company.strip(), contact.profile_url, contact.relationship,
                     contact.location.strip(), contact.notes.strip(), contact.created_at.isoformat(),
                     contact.status),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("This profile is already saved for the job.") from error
        contact.id = int(cursor.lastrowid)
        return contact.id

    def list_contacts_for_job(self, job_id: int) -> list[OutreachContact]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM outreach_contacts WHERE job_id = ? ORDER BY created_at DESC, id DESC",
                (job_id,),
            ).fetchall()
        return [_contact_from_row(row) for row in rows]

    def get_contact(self, contact_id: int, job_id: int) -> OutreachContact | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM outreach_contacts WHERE id = ? AND job_id = ?", (contact_id, job_id)
            ).fetchone()
        return _contact_from_row(row) if row else None

    def save_approved_draft(self, job_id: int, contact_id: int, body: str, fingerprint: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO outreach_drafts
                (job_id, contact_id, body, basis_fingerprint, approved_at)
                VALUES (?, ?, ?, ?, ?)""",
                (job_id, contact_id, body, fingerprint, datetime.now().astimezone().isoformat()),
            )
        return int(cursor.lastrowid)

    def list_approved_drafts(self, job_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """SELECT d.*, c.display_name FROM outreach_drafts d
                JOIN outreach_contacts c ON c.id = d.contact_id
                WHERE d.job_id = ? ORDER BY d.approved_at DESC, d.id DESC""", (job_id,)
            ).fetchall()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _contact_from_row(row: sqlite3.Row) -> OutreachContact:
    return OutreachContact(
        id=row["id"], job_id=row["job_id"], display_name=row["display_name"],
        role_title=row["role_title"], company=row["company"], profile_url=row["profile_url"],
        relationship=row["relationship"], location=row["location"], notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]), status=row["status"],
    )
