# file: first-party/applications/experimentRunner/results.py ; version: 1
"""Detached initial diagnostics shared by the Application and trusted launcher."""
from uuid import uuid4
import time


def initialResult(definition):
    """Allocate one opaque invocation identity with explicit unavailable turn evidence."""
    value = definition.value()
    config = value['configurations'][0]
    return {'schemaId': 'actant.experiment.result@1', 'runId': uuid4().hex,
            'experimentId': value['experimentId'], 'definition': value, 'definitionSha256': definition.sha256,
            'configurationName': config['name'], 'profileAlias': config['profile'],
            'status': None, 'applicationOutcome': 'not-started', 'startedTimeNs': time.time_ns(),
            'endedTimeNs': None, 'durationNs': None, 'error': None,
            'turns': [dict(turnId=turn['id'], input=turn['content'], status='not-started', query=None,
                           requestedOptions=None, answer=None, reasoning=None, native=None, executionProfile=None,
                           executionEvidence=None, cleanup={'status': 'unavailable', 'quiescence': 'unknown'},
                           startedTimeNs=None, endedTimeNs=None, durationNs=None, error=None, partial=None)
                      for turn in value['conversation']['turns']]}
