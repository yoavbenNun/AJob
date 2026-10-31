"""Offline-only migration helper; never reads DATABASE_URL or connects to cloud."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.storage.private_data_migration import backup_sqlite, restore_sqlite, build_plan, write_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('backup', 'restore'):
        command = commands.add_parser(name)
        command.add_argument('source')
        command.add_argument('destination', help='Must not exist')
    plan = commands.add_parser('dry-run')
    plan.add_argument('--jobs', required=True, help='Local SQLite source jobs')
    plan.add_argument('--notes', required=True, help='Local SQLite source notes/undo')
    plan.add_argument('--target-snapshot', required=True, help='Local target id/job_url JSON export')
    plan.add_argument('--output', required=True, help='Sensitive plan file; must not exist')
    args = parser.parse_args()
    if args.action == 'dry-run':
        snapshot = json.loads(Path(args.target_snapshot).read_text(encoding='utf-8'))
        result = build_plan(args.jobs, args.notes, snapshot)
        write_plan(result, args.output)
        print(f"Dry run only: {len(result['notes'])} notes, {len(result['dismissals'])} undo records. No database changed.")
    else:
        operation = backup_sqlite if args.action == 'backup' else restore_sqlite
        operation(args.source, args.destination)
        print('Verified local copy created. Application configuration unchanged.')


if __name__ == '__main__':
    main()
