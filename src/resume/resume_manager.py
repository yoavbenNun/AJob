"""Safe local storage for the resume selected in the dashboard."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

from src.resume.resume_parser import parse_resume


MAX_RESUME_BYTES = 10 * 1024 * 1024
ALLOWED_RESUME_SUFFIXES = frozenset({".pdf", ".docx"})
_ACTIVE_MARKER = ".active_resume"


def save_active_resume(root: Path, original_name: str, content: bytes) -> Path:
    """Validate and atomically save a new resume without replacing older copies."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("The configured resume directory does not exist.")
    safe_name = _safe_filename(original_name)
    suffix = Path(safe_name).suffix.casefold()
    if suffix not in ALLOWED_RESUME_SUFFIXES:
        raise ValueError("Choose a PDF or DOCX resume.")
    if not content:
        raise ValueError("The selected resume is empty.")
    if len(content) > MAX_RESUME_BYTES:
        raise ValueError("The resume must be 10 MB or smaller.")
    _validate_file_structure(content, suffix)

    upload_root = root / "uploaded"
    upload_root.mkdir(exist_ok=True)
    resolved_upload_root = upload_root.resolve()
    is_junction = getattr(upload_root, "is_junction", lambda: False)()
    if (
        upload_root.is_symlink()
        or is_junction
        or not resolved_upload_root.is_dir()
        or root not in resolved_upload_root.parents
    ):
        raise ValueError("The resume upload directory is unsafe.")
    upload_root = resolved_upload_root
    final_name = f"{Path(safe_name).stem[:60]}-{uuid.uuid4().hex[:8]}{suffix}"
    final_path = upload_root / final_name
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=upload_root, prefix=".resume-", suffix=suffix, delete=False
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            profile = parse_resume(temporary_path)
        except Exception as error:
            raise ValueError("The resume could not be read.") from error
        if not profile.raw_text.strip():
            raise ValueError("The resume does not contain readable text.")
        temporary_path.replace(final_path)
        temporary_path = None
        _write_active_marker(root, final_path.relative_to(root))
        return final_path
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise


def get_active_resume(root: Path | None) -> Path | None:
    """Return the active resume, falling back to the newest root-level resume."""
    if root is None:
        return None
    root = root.resolve()
    marker = root / _ACTIVE_MARKER
    try:
        relative = Path(marker.read_text(encoding="utf-8").strip())
        candidate = (root / relative).resolve()
    except (OSError, ValueError):
        return _newest_root_resume(root)
    if (
        not relative.parts
        or relative.is_absolute()
        or root not in candidate.parents
        or candidate.suffix.casefold() not in ALLOWED_RESUME_SUFFIXES
        or not candidate.is_file()
    ):
        return _newest_root_resume(root)
    return candidate


def _newest_root_resume(root: Path) -> Path | None:
    try:
        candidates = []
        for path in root.iterdir():
            if path.is_symlink() or path.name.startswith("."):
                continue
            resolved = path.resolve()
            if (
                resolved.is_file()
                and root in resolved.parents
                and resolved.suffix.casefold() in ALLOWED_RESUME_SUFFIXES
            ):
                candidates.append(resolved)
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None
    except OSError:
        return None


def _safe_filename(original_name: str) -> str:
    name = Path(original_name or "").name.strip()
    name = re.sub(r"[^\w .()-]+", "_", name, flags=re.UNICODE).strip(" .")
    if not name:
        raise ValueError("The resume filename is invalid.")
    return name


def _validate_file_structure(content: bytes, suffix: str) -> None:
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("The selected file is not a valid PDF.")
        return
    try:
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2_000 or sum(item.file_size for item in entries) > 50 * 1024 * 1024:
                raise ValueError("The selected DOCX is too complex.")
            if any(
                Path(item.filename).is_absolute() or ".." in Path(item.filename).parts
                for item in entries
            ):
                raise ValueError("The selected DOCX contains unsafe paths.")
            names = {item.filename for item in entries}
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("The selected file is not a valid DOCX.") from error
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise ValueError("The selected file is not a valid DOCX.")


def _write_active_marker(root: Path, relative_path: Path) -> None:
    marker = root / _ACTIVE_MARKER
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=root, prefix=".active-", delete=False
    ) as temporary:
        temporary.write(relative_path.as_posix())
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    temporary_path.replace(marker)
