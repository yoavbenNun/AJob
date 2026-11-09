"""Verify the exact public-URL exception without installing optional scanners."""

import ast
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import urlsplit


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
    secret.type = "Base64 High Entropy String"
    setattr(secret, "secret_value", "different-value")
    assert len(inspect(b"other value", path)) == 1


def test_synthetic_cloud_credential_exception_is_exact_and_history_safe(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture = ast.parse((root / 'tests/test_cloud_auth.py').read_text(encoding='utf-8'))
    value = next(ast.literal_eval(node.value) for node in fixture.body
                 if isinstance(node, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id == 'PASSWORD' for target in node.targets))
    tree = ast.parse((root / 'scripts/audit_secrets.py').read_text(encoding='utf-8'))
    inspector = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'inspect_text')
    secret = SimpleNamespace(secret_value=value, type='Secret Keyword', line_number=17)
    namespace = dict(Path=Path, TemporaryDirectory=lambda **kw: TemporaryDirectory(dir=tmp_path, **kw),
                     hashlib=hashlib, scan_file=lambda _: [secret])
    exec(compile(ast.Module(body=[inspector], type_ignores=[]), 'audit-policy', 'exec'), namespace)
    inspect = namespace['inspect_text']
    assert inspect(b'fixture', 'tests/test_cloud_auth.py') == []
    assert inspect(b'fixture', 'tests/test_cloud_auth.py', blob='historical-blob') == []
    assert len(inspect(b'fixture', 'src/web/cloud_auth.py')) == 1
    secret.secret_value += '-different'
    assert len(inspect(b'fixture', 'tests/test_cloud_auth.py')) == 1


def test_contact_invalid_url_fixture_exception_is_exact_and_history_safe(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture = ast.parse((root / "tests/test_contact_discovery.py").read_text(encoding="utf-8"))
    url = next(node.value for node in ast.walk(fixture)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and node.value.startswith("https://user:"))
    value = urlsplit(url).password
    tree = ast.parse((root / "scripts/audit_secrets.py").read_text(encoding="utf-8"))
    inspector = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "inspect_text")
    secret = SimpleNamespace(secret_value=value, type="Basic Auth Credentials", line_number=1)
    namespace = dict(Path=Path, TemporaryDirectory=lambda **kw: TemporaryDirectory(dir=tmp_path, **kw),
                     hashlib=hashlib, scan_file=lambda _: [secret])
    exec(compile(ast.Module(body=[inspector], type_ignores=[]), "audit-policy", "exec"), namespace)
    inspect = namespace["inspect_text"]
    assert inspect(b"synthetic invalid URL", "tests/test_contact_discovery.py") == []
    assert inspect(b"synthetic invalid URL", "tests/test_contact_discovery.py", blob="history") == []
    assert len(inspect(b"other path", "src/outreach/discovery.py")) == 1
    secret.type = "Secret Keyword"
    assert len(inspect(b"other detector", "tests/test_contact_discovery.py")) == 1
    secret.type, secret.secret_value = "Basic Auth Credentials", "different-fixture-value"
    assert len(inspect(b"other value", "tests/test_contact_discovery.py")) == 1
