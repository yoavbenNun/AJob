"""Offline secret detection for tracked source and publishable Git history.

Requires requirements-audit.txt. Findings contain locations, never secret values.
No verification requests, baseline writes, history rewrites, or credential changes.
"""

import argparse
import json
import hashlib
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from detect_secrets.core.scan import scan_file
from detect_secrets.settings import default_settings


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def inspect_text(raw, path, blob=None):
    if b"\0" in raw:
        return []
    findings = []
    # scan_line is an eager ad-hoc classifier, not a file scanner: it deliberately
    # treats ordinary words as entropy candidates. Use the file API and its normal
    # thresholds/context. Temporary copies are removed, never uploaded.
    with TemporaryDirectory(prefix="ajob-secret-check-") as directory:
        target = Path(directory) / Path(path).name
        target.write_bytes(raw)
        for secret in scan_file(str(target)):
            # Exact example fingerprints, scoped to path and detector type.
            examples = {
                ("tests/test_email_notifier.py", "Secret Keyword"): "ed24bc0bb03d0e89e8bacafb1545ee4ed6a2b273f3db69199787632b32545179",  # pragma: allowlist secret -- SHA256 of a public example
                ("README.md", "Basic Auth Credentials"): "0be64ae89ddd24e225434de95d501711339baeee18f009ba9b4369af27d30d60",  # pragma: allowlist secret -- SHA256 of a public example
                ("tests/test_workday_locations.py", "Base64 High Entropy String"): "f6a5f14458ceb19a10a51ba2ea6bafe345c867041f984524aefdaf2935c5aff1",  # pragma: allowlist secret -- SHA256 of Intel's public requisition path
                ("tests/test_secret_audit.py", "Secret Keyword"): "afd546925353636cc3a93a1fbf241a5c5d5f7d66c423915f25cb3d194012c439",  # pragma: allowlist secret -- SHA256 of a public detector label in a historical test
            }
            fingerprint = hashlib.sha256(secret.secret_value.encode()).hexdigest()
            if examples.get((path.replace("\\", "/"), secret.type)) == fingerprint:
                continue
            findings.append({"path": path, "line": secret.line_number, "type": secret.type,
                             **({"blob": blob} if blob else {})})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--working-tree", action="store_true", help="Also inspect nonignored, untracked files")
    args = parser.parse_args()
    findings = []
    files = git("ls-files", "-z").decode().rstrip("\0").split("\0")
    if args.working_tree:
        files += [p for p in git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0") if p]
    blobs = 0
    with default_settings():
        # Fail closed if a tool/API change makes ordinary code noisy or misses
        # a synthetic provider token. This never verifies a token online.
        canary = "ghp_" + "Ab3dEfGhIjKlMnOpQrStUvWxYz0123456789"  # pragma: allowlist secret -- synthetic canary
        detected = inspect_text(('value = "' + canary + '"\n').encode(), "canary.py")
        if inspect_text(b"ordinary_configuration = True\n", "example.py") or not any(
            "GitHub" in item["type"] for item in detected
        ):
            raise RuntimeError("Secret detector regression check failed")
        for path in files:
            file = ROOT / path
            if file.is_file():
                findings.extend(inspect_text(file.read_bytes(), path))
        if args.history:
            # Branches, remote-tracking branches, and tags; exclude Codex's private
            # worktree snapshots, which must never be included in a mirror push.
            objects = git("rev-list", "--objects", "--branches", "--tags", "--remotes").decode().splitlines()
            with subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
                for obj in objects:
                    oid, _, path = obj.partition(" ")
                    process.stdin.write((oid + "\n").encode())
                    process.stdin.flush()
                    header = process.stdout.readline().split()
                    if len(header) != 3:
                        raise RuntimeError("Unexpected Git object response")
                    raw = process.stdout.read(int(header[2]))
                    process.stdout.read(1)
                    if header[1] == b"blob":
                        blobs += 1
                        findings.extend(inspect_text(raw, path, oid[:12]))
                process.stdin.close()
    print(json.dumps({"files": len(files), "history_blobs": blobs,
                      "finding_count": len(findings), "findings": findings[:30],
                      "output_truncated": len(findings) > 30}, indent=2))
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
