# file: first-party/applications/experimentRunner/codeEntry.py ; version: 1
"""One bounded conversation Job with ephemeral, rollback-independent diagnostics."""
from __future__ import annotations

import importlib
import json
import time
from threading import Lock

from backend.llm.executionSessions import FreshContext
from backend.llm.errors import (LlmExecutionConflictError, LlmExecutionCleanupError,
                                LlmExecutionOwnershipError, LlmProviderConnectionError,
                                LlmProviderProtocolError, LlmProviderRequestError)
from backend.llm.structuredMessages import LlmMessage, LlmMessages
from backend.orchestration.cancellation import ExecutionCancelled

schema = importlib.import_module('first-party.applications.experimentRunner.definition')
results = importlib.import_module('first-party.applications.experimentRunner.results')


def safeError(error, stage):
    """Expose fixed categories only; raw provider exception messages remain private."""
    category = 'cancelled' if isinstance(error, ExecutionCancelled) else 'execution-failed'
    for kind, label in ((LlmExecutionConflictError, 'execution-conflict'), (LlmExecutionCleanupError, 'cleanup-failed'),
                        (LlmExecutionOwnershipError, 'execution-ownership'), (LlmProviderConnectionError, 'provider-connection'),
                        (LlmProviderProtocolError, 'provider-protocol'), (LlmProviderRequestError, 'provider-request')):
        if isinstance(error, kind):
            category = label
            break
    result = {'category': category, 'stage': stage}
    if isinstance(error, LlmExecutionConflictError):
        result['conflictReason'] = error.conflict.reason.value
    return result


def evidenceSnapshot(value):
    """Read only the public safe execution snapshot; absent evidence remains absent."""
    return None if value is None else value.snapshot()


def cleanupSnapshot(evidence):
    """Keep cleanup disposition independent of inference and Job success."""
    if evidence is None:
        return {'status': 'unavailable', 'quiescence': 'unknown'}
    return {'status': evidence['cleanupStatus'], 'quiescence': evidence['quiescence'],
            'iteratorCloseFailed': evidence['iteratorCloseFailed']}


def boundedNative(metadata):
    """Retain bounded allowlisted native facts, excluding task IDs and live authority."""
    selected = {key: value for key, value in metadata.items()
                if key in ('usage', 'timings', 'model', 'finish_reason', 'reasoning_format')}
    # Provider metadata is already recursively immutable; convert JSON containers only.
    def plain(value):
        """Detach immutable mappings/tuples without introspecting arbitrary objects."""
        if hasattr(value, 'items'):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [plain(item) for item in value]
        return value
    encoded = schema.canonical(plain(selected))
    return json.loads(encoded) if len(encoded) <= 16384 else {'truncated': True}


class Recorder:
    """Own one immutable bounded diagnostic snapshot, without retaining any Context."""

    def __init__(self, policy):
        """Freeze only host-approved aliases/caps, not host configuration authority."""
        self.policy = schema.Snapshot(schema.canonical(policy))
        self.snapshot = None
        self.definition = None
        self.pending = False
        self.lock = Lock()

    def prepare(self, ctx, payload):
        """Validate/freeze a new invocation before its caller-retained Job is started."""
        definition = schema.validate(payload, self.policy.value())
        with self.lock:
            if self.pending:
                raise RuntimeError('An experiment invocation is already pending.')
            self.definition = definition
            self.snapshot = schema.Snapshot(schema.canonical(results.initialResult(definition)))
            self.startedNs = time.monotonic_ns()
            self.pending = True
            return self.snapshot.value()['runId']

    def inspect(self, ctx, payload):
        """Return immutable ephemeral diagnostics, including after Job rollback."""
        return self.snapshot

    def publish(self, result):
        """Replace the recorder with detached canonical bytes only."""
        self.snapshot = schema.Snapshot(schema.canonical(result))

    def execute(self, ctx, runId):
        """Transition once, then replay only successfully completed assistant answers."""
        with self.lock:
            if not self.pending or self.snapshot.value()['runId'] != runId:
                raise RuntimeError('No matching prepared invocation.')
            # The runtime serializes Jobs; this separate guard also protects direct calls.
            if self.snapshot.value()['applicationOutcome'] != 'not-started':
                raise RuntimeError('Invocation has already been executed.')
            result = self.snapshot.value()
            result['applicationOutcome'] = 'running'
            self.publish(result)
        definition = self.definition.value()
        config = definition['configurations'][0]
        selector = self.policy.value()['aliases'][config['profile']]
        limit = self.policy.value()['maxOutputCharacters']
        history = []
        started = self.startedNs
        stage, current = 'transition', None
        try:
            ctx.cancellation.raiseIfRequested()
            profile = ctx.llm.profile(providerName='llama.cpp', model=selector)
            ctx.llm.transitionProfile(profile)
            for turn, current in zip(definition['conversation']['turns'], result['turns'], strict=True):
                stage = 'before-turn'
                ctx.cancellation.raiseIfRequested()
                query = LlmMessages(tuple(history + [LlmMessage('user', turn['content'])]))
                requested = config['defaults'] | turn.get('overrides', {})
                current.update(status='running', query={'formatId': query.toQuery().formatId, 'messages': query.snapshot()},
                               requestedOptions=requested, startedTimeNs=time.time_ns())
                turnStart = time.monotonic_ns()
                partial = {'answer': '', 'reasoning': '', 'partial': True, 'truncated': False}

                def observe(event):
                    """Keep bounded private deltas as diagnostics only, never as replay history."""
                    if event.eventType != 'delta':
                        return
                    reasoning = event.metadata.get('llamaCppReasoning', {}).get('content', '')
                    for key, text in (('answer', event.text), ('reasoning', reasoning)):
                        room = max(0, limit - len(partial['answer']) - len(partial['reasoning']))
                        partial[key] += text[:room]
                        partial['truncated'] |= len(text) > room
                    current['partial'] = dict(partial)
                    self.publish(result)

                stage = 'inference'
                self.publish(result)
                try:
                    response = ctx.llm.run(providerName='llama.cpp', model=selector, query=query.toQuery(),
                                           providerOptions=requested, context=FreshContext(required=False, retain=False),
                                           streamObserver=observe)
                    evidence = evidenceSnapshot(response.executionEvidence)
                    current['executionEvidence'] = evidence
                    current['cleanup'] = cleanupSnapshot(evidence)
                    reasoning = response.providerMetadata.get('llamaCppReasoning', {}).get('content')
                    if len(response.rawText) + len(reasoning or '') > limit:
                        raise RuntimeError('Host output bound exceeded.')
                    current.update(answer=response.rawText, reasoning=reasoning, native=boundedNative(response.providerMetadata),
                                   executionProfile={'contextWindowTokens': response.executionProfile.contextWindowTokens,
                                                     'metadata': boundedProfile(response.executionProfile.metadata)}, partial=None)
                    if current['cleanup']['quiescence'] != 'confirmed':
                        raise RuntimeError('Execution cleanup is not confirmed.')
                    current['status'] = 'succeeded'
                    history.extend((LlmMessage('user', turn['content']), LlmMessage('assistant', response.rawText)))
                except Exception as error:
                    attached = getattr(error, 'llmCleanup', None)
                    if attached is not None:
                        current['executionEvidence'] = evidenceSnapshot(attached)
                        current['cleanup'] = cleanupSnapshot(current['executionEvidence'])
                    current['status'] = 'cancelled' if isinstance(error, ExecutionCancelled) else 'failed'
                    current['error'] = safeError(error, stage)
                    # Re-raise a fixed message so RuntimeHost's generic error tracing cannot expose content.
                    if isinstance(error, ExecutionCancelled):
                        raise ExecutionCancelled('Experiment execution cancelled.') from None
                    raise RuntimeError('Experiment inference failed.') from None
                finally:
                    current.update(endedTimeNs=time.time_ns(), durationNs=time.monotonic_ns() - turnStart)
                    self.publish(result)
            ctx.cancellation.raiseIfRequested()
            result['applicationOutcome'] = 'completed'
        except Exception as error:
            result['applicationOutcome'] = 'cancelled' if isinstance(error, ExecutionCancelled) else 'failed'
            result['error'] = (current['error'] if current is not None and current['error'] is not None
                               else safeError(error, stage))
            if isinstance(error, ExecutionCancelled):
                raise ExecutionCancelled('Experiment execution cancelled.') from None
            raise RuntimeError('Experiment execution failed.') from None
        finally:
            result.update(endedTimeNs=time.time_ns(), durationNs=time.monotonic_ns() - started)
            self.publish(result)
        return runId

    def finish(self, ctx, payload):
        """Release the prepared-run guard after the launcher observes a terminal Job.

        This is ephemeral diagnostic finalization, not an authoritative Job mutation.
        The containing launcher records the actual Job object independently.
        """
        with self.lock:
            schema.fields(payload, ('runId', 'jobOutcome'))
            schema.require(self.pending and self.snapshot.value()['runId'] == payload['runId'], 'Unknown invocation.')
            schema.require(payload['jobOutcome'] in ('Succeeded', 'Failed', 'Cancelled'), 'Job is not terminal.')
            value = self.snapshot.value()
            if value['endedTimeNs'] is None:
                value.update(endedTimeNs=time.time_ns(), durationNs=time.monotonic_ns() - self.startedNs)
            value['status'] = ('cancelled' if payload['jobOutcome'] == 'Cancelled' else
                               'succeeded' if payload['jobOutcome'] == 'Succeeded' and value['applicationOutcome'] == 'completed' else 'failed')
            self.publish(value)
            self.pending = False
            self.definition = None
            return self.snapshot


def boundedProfile(metadata):
    """Detach safe provider profile summaries without copying estimators or handles."""
    return boundedNative({'usage': metadata}).get('usage')


def onLoad(ctx):
    """Create one per-Application recorder and register capabilities without inference."""
    recorder = Recorder(ctx.config['experimentRunner'])
    for name, handler in (('prepare', recorder.prepare), ('run', recorder.execute),
                          ('inspect', recorder.inspect), ('finish', recorder.finish)):
        ctx.capabilities.register('experimentRunner.' + name + '@1', handler)
    return recorder
