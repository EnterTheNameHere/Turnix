# file: first-party/applications/experimentRunner/run.py ; version: 1
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
launcher = importlib.import_module('first-party.applications.experimentRunner.launcher')


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
    commands.add_parser('inspect').add_argument('--result', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            # Results can exceed the definition's 1 MiB cap but remain bounded by v1 limits.
            with Path(args.result).open('rb') as source:
                data = source.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError('Result exceeds inspection bound.')
            print(json.dumps(json.loads(data.decode('utf-8')), ensure_ascii=False, indent=2))
            return 0
        prepared = launcher.prepare(launcher.readJson(args.definition), launcher.readJson(args.host_config))
        if args.command == 'validate':
            print('Valid definition and managed host configuration; no model launched.')
            return 0
        snapshot, path = launcher.execute(prepared, args.results)
        value = snapshot.value()
        run = value['run']
        print(json.dumps({'runId': None if run is None else run['runId'],
                          'status': None if run is None else run['status'], 'jobOutcome': value['jobOutcome'],
                          'turnsSucceeded': 0 if run is None else sum(turn['status'] == 'succeeded' for turn in run['turns']),
                          'path': None if path is None else str(path), 'launcherFailure': value['launcherFailure'],
                          'resultWriteFailure': value['resultWriteFailure'], 'hostCleanup': value['hostCleanup']}))
        return launcher.exitCode(value)
    except Exception:
        print('Experiment input or launcher failed; private content was not printed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
