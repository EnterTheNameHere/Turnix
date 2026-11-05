# file: first-party/applications/experimentRunner/run.py ; version: 3
"""Terminal entry point; only explicit inspect displays private experiment content."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None):
    """Validate without launch, run through managed Actant, or explicitly inspect a private result."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('validate', 'run'):
        child = commands.add_parser(command)
        child.add_argument('--definition', required=True)
        child.add_argument('--host-config', required=True)
        if command == 'run':
            child.add_argument('--results', required=True)
    inspect = commands.add_parser('inspect')
    selection = inspect.add_mutually_exclusive_group(required=True)
    selection.add_argument('--result')
    selection.add_argument('--archive')
    recover = commands.add_parser('recover')
    recover.add_argument('--archive', required=True)
    for child in (inspect, recover):
        child.add_argument('--unit')
        child.add_argument('--turn')
        child.add_argument('--include-content', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command in ('inspect', 'recover') and args.archive:
            reader = importlib.import_module('first-party.applications.experimentRunner.archiveReader')
            value = reader.inspectArchive(args.archive, unitId=args.unit, turnId=args.turn, includeContent=args.include_content)
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 1 if value['status'] == 'corrupt' else 0
        if args.command == 'inspect':
            # Results can exceed the definition's 1 MiB cap but remain bounded by v1 limits.
            with Path(args.result).open('rb') as source:
                data = source.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError('Result exceeds inspection bound.')
            print(json.dumps(json.loads(data.decode('utf-8')), ensure_ascii=False, indent=2))
            return 0
        launcher = importlib.import_module('first-party.applications.experimentRunner.launcher')
        prepared = launcher.prepare(launcher.readJson(args.definition), launcher.readJson(args.host_config))
        if args.command == 'validate':
            print('Valid definition and managed host configuration; no model launched.')
            return 0
        snapshot, path = launcher.execute(prepared, args.results)
        value = snapshot.value()
        suite = value['suite']
        print(json.dumps({'suiteId': suite['suiteId'], 'status': suite['status'],
                          'unitsSucceeded': sum(unit['status'] == 'succeeded' for unit in suite['units']),
                          'turnsSucceeded': sum(turn['status'] == 'succeeded' for unit in suite['units'] for turn in unit['run']['turns']),
                          'path': None if path is None else str(path), 'launcherFailure': value['launcherFailure'],
                          'archivePath': value['archive']['path'], 'archiveId': value['archive']['archiveId'],
                          'archiveFailure': value['archive']['failure'], 'archiveClose': value['archive']['close'],
                          'resultWriteFailure': value['resultWriteFailure'], 'hostCleanup': value['hostCleanup']}))
        return launcher.exitCode(value)
    except Exception:
        print('Experiment input or launcher failed; private content was not printed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
