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


def test_historical_fixture_exceptions_are_bound_to_exact_blob_and_location(tmp_path):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'scripts/audit_secrets.py').read_text(encoding='utf-8'))
    inspector = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'inspect_text')
    secret = SimpleNamespace(secret_value='synthetic-canary', type='', line_number=0)
    namespace = dict(Path=Path, TemporaryDirectory=lambda **kw: TemporaryDirectory(dir=tmp_path, **kw),
                     hashlib=hashlib, scan_file=lambda _: [secret])
    exec(compile(ast.Module(body=[inspector], type_ignores=[]), 'audit-policy', 'exec'), namespace)
    inspect = namespace['inspect_text']
    cases = [
        ('602f0ddb' '42f8d7f0' '05652bc4' '388324af' '0fe0d750', 'scripts/audit_secrets.py', 44, 'Hex High Entropy String'),
        ('602f0ddb' '42f8d7f0' '05652bc4' '388324af' '0fe0d750', 'scripts/audit_secrets.py', 45, 'Hex High Entropy String'),
        ('6c15b0b7' '58a1a0be' 'b2d82521' '4899d52d' 'ac4e6f26', 'tests/test_contact_discovery.py', 93, 'Basic Auth Credentials'),
        ('facbe215' '7546c661' '3da80a8b' 'ce8699ae' '85346a1a', 'tests/test_secret_audit.py', 75, 'Secret Keyword'),
    ]
    for blob, path, line, detector in cases:
        secret.line_number, secret.type = line, detector
        assert inspect(b'fixture', path, blob) == []
        assert len(inspect(b'fixture', path, '0' * 40)) == 1
        assert len(inspect(b'fixture', 'tests/other.py', blob)) == 1
        assert len(inspect(b'fixture', path)) == 1
        secret.line_number = 999
        assert len(inspect(b'fixture', path, blob)) == 1
        secret.line_number, secret.type = line, 'A different detector'
        assert len(inspect(b'fixture', path, blob)) == 1


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
    secret.type = "Basic Auth Credentials"
    setattr(secret, "secret_value", "different-fixture-value")
    assert len(inspect(b"other value", "tests/test_contact_discovery.py")) == 1
    secret.type = "Secret Keyword"
    setattr(secret, "secret_value", "Basic Auth Credentials")
    assert inspect(b"public detector label", "tests/test_secret_audit.py", blob="history") == []
    assert len(inspect(b"public label in other file", "src/outreach/discovery.py")) == 1
    setattr(secret, "secret_value", "unrecognized-label")
    assert len(inspect(b"other label", "tests/test_secret_audit.py")) == 1
    secret.type = "Hex High Entropy String"
    setattr(secret, "secret_value", hashlib.sha256("Basic Auth Credentials".encode()).hexdigest())
    assert inspect(b"public fingerprint", "scripts/audit_secrets.py", blob="history") == []
    assert len(inspect(b"fingerprint elsewhere", "src/outreach/discovery.py")) == 1
    setattr(secret, "secret_value", hashlib.sha256("unrecognized-label".encode()).hexdigest())
    assert len(inspect(b"unknown fingerprint", "scripts/audit_secrets.py")) == 1
