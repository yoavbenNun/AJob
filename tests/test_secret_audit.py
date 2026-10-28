"""Verify the exact public-URL exception without installing optional scanners."""

import ast
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def test_public_workday_fixture_exception_is_exact_and_history_safe(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture = ast.parse((root / "tests/test_workday_locations.py").read_text(encoding="utf-8"))
    public_path = next(ast.literal_eval(node.value) for node in fixture.body
                       if isinstance(node, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id == "PATH" for target in node.targets))
    tree = ast.parse((root / "scripts/audit_secrets.py").read_text(encoding="utf-8"))
    inspector = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "inspect_text")
    secret = SimpleNamespace(secret_value=public_path, type="Base64 High Entropy String", line_number=18)
    namespace = dict(Path=Path, TemporaryDirectory=lambda **kw: TemporaryDirectory(dir=tmp_path, **kw), hashlib=hashlib,
                     scan_file=lambda _: [secret])
    exec(compile(ast.Module(body=[inspector], type_ignores=[]), "audit-policy", "exec"), namespace)
    inspect = namespace["inspect_text"]
    path = "tests/test_workday_locations.py"
    assert inspect(b"public fixture", path) == []
    assert inspect(b"public fixture", path, blob="historical-blob") == []
    assert len(inspect(b"other file", "tests/other.py")) == 1
    secret.type = "Secret Keyword"
    assert len(inspect(b"other detector", path)) == 1
    secret.type, secret.secret_value = "Base64 High Entropy String", "different-value"
    assert len(inspect(b"other value", path)) == 1
