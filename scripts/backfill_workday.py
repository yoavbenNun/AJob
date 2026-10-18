"""Manual two-phase Workday repair. No scans, mail, migrations or secret changes.

Run --dry-run --backup <path outside checkout>, inspect counts, then --apply
--backup <same path>. DATABASE_URL is supplied securely by the operator.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess

import psycopg
from psycopg.rows import dict_row

from src.storage.workday_backfill import prepare_backfill, apply_backfill, restore_backfill, summary


def protected_json(value, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('This backup command requires current-user Windows DPAPI')
    # SecureString conversion truncates large backups at 65,536 characters.
    # Protect raw UTF-8 bytes instead, and verify the full restored artifact.
    operation = ("$b=[Security.Cryptography.ProtectedData]::Unprotect("
                 "[Convert]::FromBase64String($v),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);"
                 "[Console]::Write([Text.Encoding]::UTF8.GetString($b))" if decrypt else
                 "$b=[Security.Cryptography.ProtectedData]::Protect("
                 "[Text.Encoding]::UTF8.GetBytes($v),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);"
                 "[Console]::Write([Convert]::ToBase64String($b))")
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-Command',
         "$ErrorActionPreference='Stop';Add-Type -AssemblyName System.Security;"
         "$v=[Console]::In.ReadToEnd();" + operation],
        input=value, text=True, encoding='utf-8', capture_output=True,
    )
    if result.returncode:
        raise RuntimeError('Protected backup operation failed')
    return result.stdout if decrypt else result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--restore', action='store_true')
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--blocked-source', action='append', default=[])
    parser.add_argument('--resume-backup', type=Path,
                        help='Reuse verified results only for unchanged rows in a previous dry run')
    args = parser.parse_args()
    backup = args.backup.resolve()
    if backup.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Protected backup must be outside the checkout')
    if args.dry_run:
        previous = (json.loads(protected_json(args.resume_backup.read_text('ascii'), decrypt=True))
                    if args.resume_backup else None)
        with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
                             options='-c default_transaction_read_only=on', connect_timeout=10) as connection:
            plan = prepare_backfill(connection, blocked=args.blocked_source, previous=previous)
        raw = json.dumps(plan, ensure_ascii=True, sort_keys=True)
        encrypted = protected_json(raw)
        if json.loads(protected_json(encrypted, decrypt=True)) != plan:
            raise RuntimeError('Backup restoration verification failed')
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open('x', encoding='ascii') as output:
            output.write(encrypted)
        # Verify the actual saved artifact as well, before permitting any write.
        if json.loads(protected_json(backup.read_text('ascii'), decrypt=True)) != plan:
            raise RuntimeError('Saved backup restoration verification failed')
        print(json.dumps({'mode': 'dry-run', 'backup_restore_verified': True, **summary(plan)}))
    else:
        plan = json.loads(protected_json(backup.read_text('ascii'), decrypt=True))
        with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, autocommit=True,
                             connect_timeout=10) as connection:
            changed = (restore_backfill if args.restore else apply_backfill)(connection, plan)
        print(json.dumps({'mode': 'restored' if args.restore else 'applied',
                          'changed': len(changed), 'ids': changed, **summary(plan)}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Driver/HTTP messages can contain connection information: output type only.
        print(json.dumps({'error': type(error).__name__, 'completed': False}))
        raise SystemExit(1)
