from pathlib import Path

import pytest
from docx import Document

from src.resume.resume_manager import get_active_resume, save_active_resume


def _docx_bytes(tmp_path: Path, name: str, text: str) -> bytes:
    path = tmp_path / name
    document = Document()
    document.add_paragraph(text)
    document.save(path)
    return path.read_bytes()


def test_new_resume_becomes_active_without_removing_previous_version(tmp_path: Path) -> None:
    first = save_active_resume(
        tmp_path, "../first.docx", _docx_bytes(tmp_path, "source-one.docx", "First Resume")
    )
    second = save_active_resume(
        tmp_path, "second.docx", _docx_bytes(tmp_path, "source-two.docx", "Second Resume")
    )

    assert first.is_file()
    assert second.is_file()
    assert first != second
    assert get_active_resume(tmp_path) == second
    assert second.parent == tmp_path / "uploaded"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("resume.exe", b"MZ"),
        ("resume.pdf", b"not a pdf"),
        ("resume.docx", b"not a zip"),
        ("resume.PDF.exe", b"%PDF-1.4"),
    ],
)
def test_invalid_resume_is_rejected_without_active_marker(
    tmp_path: Path, filename: str, content: bytes
) -> None:
    with pytest.raises(ValueError):
        save_active_resume(tmp_path, filename, content)

    assert get_active_resume(tmp_path) is None


def test_truncated_pdf_is_normalized_to_validation_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not be read"):
        save_active_resume(tmp_path, "broken.pdf", b"%PDF-1.4\nnot a real PDF")

    assert get_active_resume(tmp_path) is None


def test_active_marker_cannot_escape_resume_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4")
    (tmp_path / ".active_resume").write_text("../outside.pdf", encoding="utf-8")

    assert get_active_resume(tmp_path) is None


def test_newest_root_resume_is_the_default_without_marker(tmp_path: Path) -> None:
    older = tmp_path / "older.docx"
    newer = tmp_path / "newer.docx"
    older.write_bytes(_docx_bytes(tmp_path, "source-old.docx", "Older Resume"))
    newer.write_bytes(_docx_bytes(tmp_path, "source-new.docx", "Newer Resume"))
    older.touch()
    newer.touch()
    older_mtime = newer.stat().st_mtime - 10
    import os
    os.utime(older, (older_mtime, older_mtime))

    assert get_active_resume(tmp_path) == newer


def test_upload_directory_symlink_cannot_escape_resume_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-uploads"
    outside.mkdir()
    try:
        (tmp_path / "uploaded").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is not available on this system")

    with pytest.raises(ValueError, match="unsafe"):
        save_active_resume(
            tmp_path,
            "resume.docx",
            _docx_bytes(tmp_path, "source.docx", "Safe resume text"),
        )

    assert not list(outside.iterdir())
