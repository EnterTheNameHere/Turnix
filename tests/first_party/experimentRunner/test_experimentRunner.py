# file: tests/first_party/experimentRunner/test_experimentRunner.py ; version: 1
"""EX-01/02 architectural tests: actual Application/provider composition, synthetic IO."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from backend.orchestration import Job

launcher = importlib.import_module('first-party.applications.experimentRunner.launcher')
schema = launcher.schema
entry = importlib.import_module('first-party.applications.experimentRunner.codeEntry')


def definition():
    """Return an independent machine-neutral two-turn example for each test."""
    return launcher.readJson(launcher.APP / 'example.definition.json')


def hostValue(tmp_path):
    """Select only fake local assets and explicit host caps for synthetic verification."""
    executable, model = tmp_path / 'server.exe', tmp_path / 'model.gguf'
    executable.touch()
    model.touch()
    return {'schemaId': 'actant.experiment.host@1',
            'llamaCpp': {'manageServer': True, 'executable': str(executable), 'defaultModel': 'Q',
                         'models': {'Q': {'modelPath': str(model), 'contextWindowTokens': 1024,
                                          'serverReasoning': 'off', 'parallelSlots': 1}}},
            'aliases': {'qwen-demo': 'Q'}, 'limits': {'maxTokens': 24, 'timeoutSeconds': 30,
                                                   'maxTurns': 4, 'maxOutputCharacters': 4096},
            'persistConfigurationEvidence': True}


class Process:
    """Synthetic exact owned child; only wait confirms its exit."""

    def __init__(self, args, **kwargs):
        """Retain fake process arguments without launching an executable."""
        self.args, self.returncode, self.terminated = args, None, False

    def poll(self):
        """Report the test-controlled child disposition."""
        return self.returncode

    def terminate(self):
        """Record ordinary driver disposal of this exact fake process."""
        self.terminated = True

    def kill(self):
        """Record only this fake child's forced disposal if requested by the driver."""
        self.terminated = True

    def wait(self, **kwargs):
        """Confirm synthetic reap."""
        self.returncode = 0
        return 0


class Response:
    """Synthetic HTTP boundary with independent stream and local-close state."""

    def __init__(self, data):
        """Retain the exact configured bytes or iterator."""
        self.data, self.closed = data, False

    def __enter__(self):
        """Enter this fake transport only."""
        return self

    def __exit__(self, *args):
        """Close locally without implying server quiescence."""
        self.close()

    def read(self, limit=-1):
        """Serve synchronous template/tokenizer JSON."""
        return self.data if limit < 0 else self.data[:limit]

    def read1(self, limit):
        """Expose bounded slot JSON chunks then real fake EOF."""
        result, self.data = self.data[:limit], self.data[limit:]
        return result

    def __iter__(self):
        """Deliver configured SSE events, including deliberately failed iterators."""
        return iter(self.data)

    def close(self):
        """Mark only local close."""
        self.closed = True


def stream(answer, reasoning='private reasoning', *, done=True):
    """Create separate answer/reasoning deltas and native completion evidence."""
    events = [{'id': 'synthetic-completion', 'choices': [{'index': 0, 'delta':
               {'content': answer, 'reasoning_content': reasoning}, 'finish_reason': None}]},
              {'id': 'synthetic-completion', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
              {'id': 'synthetic-completion', 'choices': [], 'usage': {'completion_tokens': 1}}]
    lines = [('data: ' + json.dumps(event) + '\n').encode() for event in events]
    return lines + ([b'data: [DONE]\n'] if done else [])


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Run production manifests, host, broker and provider; fake OS/HTTP only."""
    host = hostValue(tmp_path)
    adapter = launcher.loadAdapter()
    state = {'processes': [], 'requests': [], 'calls': 0, 'streams': [stream(' amber '), stream('answer two')]}

    def popen(args, **kwargs):
        """Record exact synthetic launches."""
        process = Process(args, **kwargs)
        state['processes'].append(process)
        return process

    def ready(*args):
        """Avoid launching a health endpoint for this synthetic process."""
        return None

    def urlopen(request, **kwargs):
        """Record actual provider wire payloads without replacing application behavior."""
        body = None if request.data is None else json.loads(request.data)
        state['requests'].append((request.full_url, body))
        if request.full_url.endswith('/slots'):
            return Response(b'[{"id":0,"is_processing":false}]')
        if request.full_url.endswith('/apply-template'):
            return Response(b'{"prompt":"synthetic rendered prompt"}')
        if request.full_url.endswith('/tokenize'):
            return Response(b'{"tokens":[1,2,3]}')
        index = state['calls']
        state['calls'] += 1
        if 'onCall' in state:
            state['onCall'](index)
        selected = state['streams'][index]
        if isinstance(selected, Exception):
            raise selected
        return Response(selected)

    class Transport:
        """Synthetic owned transport; interruption/native cleanup is still production code."""

        def __init__(self):
            """Initialize separate send and stop facts."""
            self.mayHaveSent, self.stopped, self.response = False, False, None

        def open(self, request, **kwargs):
            """Pass actual bytes into the fake endpoint once, never retrying."""
            if self.stopped:
                raise InterruptedError('Synthetic stop before send.')
            self.mayHaveSent = True
            self.response = urlopen(request, **kwargs)
            return self.response

        def stop(self):
            """Signal stop without supplying a server-idle acknowledgement."""
            self.stopped = True
            if 'onStop' in state:
                state['onStop']()

        def close(self):
            """Close only the exact local synthetic response."""
            if self.response is not None:
                self.response.close()

        def setReadTimeout(self, seconds):
            """Accept bounded remaining reader timeout without real IO."""
            self.timeout = seconds

    monkeypatch.setattr(adapter._impl.subprocess, 'Popen', popen)
    monkeypatch.setattr(adapter._impl.LlamaCppDriver, '_waitReady', ready)
    monkeypatch.setattr(adapter.http, 'urlopen', urlopen)
    monkeypatch.setattr(adapter._transport, 'OwnedHttpTransport', Transport)
    return state, host, tmp_path / 'results'


def execute(rig, **kwargs):
    """Invoke the production trusted launcher with the default validated experiment."""
    state, host, results = rig
    snapshot, path = launcher.execute(launcher.prepare(definition(), host), results,
                                      handleInterrupt=False, **kwargs)
    return snapshot.value(), path


def test_ordered_history_reasoning_cleanup_and_private_result(rig):
    """Exact answer-only replay composes with actual Job, evidence and lifecycle cleanup."""
    value, path = execute(rig)
    state, _, _ = rig
    assert launcher.exitCode(value) == 0, value
    run = value['run']
    assert run['status'] == 'succeeded' and value['jobOutcome'] == 'Succeeded'
    assert [turn['status'] for turn in run['turns']] == ['succeeded', 'succeeded']
    turns = definition()['conversation']['turns']
    expected = [[{'role': 'user', 'content': turns[0]['content']}],
                [{'role': 'user', 'content': turns[0]['content']}, {'role': 'assistant', 'content': ' amber '},
                 {'role': 'user', 'content': turns[1]['content']}]]
    wire = [body for url, body in state['requests'] if url.endswith('/completions')]
    assert [body['messages'] for body in wire] == expected
    assert [turn['query']['messages'] for turn in run['turns']] == expected
    assert all(turn['reasoning'] == 'private reasoning' for turn in run['turns'])
    assert all(turn['cleanup']['quiescence'] == 'confirmed' for turn in run['turns'])
    assert all(body['cache_prompt'] is False and body['id_slot'] == 0 for body in wire)
    assert len(state['processes']) == 1 and state['processes'][0].returncode == 0
    assert json.loads(path.read_text(encoding='utf-8')) == value
    assert value['configurationEvidence'] is not None
    assert 'id_task' not in path.read_text(encoding='utf-8')
    state['streams'].extend([stream('new first'), stream('new second')])
    again, other = execute(rig)
    # A second invocation uses the same declarative identity but a fresh opaque run identity.
    assert again['run']['experimentId'] == run['experimentId'] and other != path
    assert again['run']['status'] == 'succeeded'


@pytest.mark.parametrize('path,new', [
    (('unknown',), 1), (('configurations',), []), (('repetitions',), True), (('repetitions',), 2),
    (('contextPolicy',), 'required-fresh'), (('errorPolicy',), 'continue'),
    (('configurations', 0, 'profile'), 'Q'), (('configurations', 0, 'defaults', 'maxTokens'), True),
    (('configurations', 0, 'defaults', 'maxTokens'), 25), (('configurations', 0, 'defaults', 'timeoutSeconds'), None),
    (('configurations', 0, 'defaults', 'temperature'), float('inf')),
    (('configurations', 0, 'defaults', 'port'), 1234),
    (('configurations', 0, 'defaults', 'chatTemplateKwargs'), {'arbitrary': True}),
    (('conversation', 'historyPolicy'), 'retain'), (('conversation', 'turns', 0, 'role'), 'system'),
    (('conversation', 'turns', 0, 'content'), ''), (('conversation', 'turns', 1, 'id'), 't1'),
    (('conversation', 'turns', 1, 'overrides'), {'temperature': 1}),
    (('configurations', 0, 'allowedOverrides'), ['maxTokens', 'maxTokens']),
])
def test_invalid_input_never_launches(rig, path, new):
    """Malformed/unauthorized definition fields are rejected before runtime activation."""
    state, host, _ = rig
    value = definition()
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = new
    with pytest.raises(schema.ValidationError):
        launcher.prepare(value, host)
    assert state['processes'] == [] and state['requests'] == []


@pytest.mark.parametrize('data', [b'{"a":1,"a":2}', b'{"x":{"a":1,"a":2}}', b'{"x":NaN}',
                                 b'{"x":1e999}', b'\xff', b' ' * (schema.MAX_BYTES + 1)])
def test_strict_json(data):
    """Duplicate keys, invalid UTF-8, oversized JSON and nonfinite values never enter models."""
    with pytest.raises(schema.ValidationError):
        schema.decode(data)


def test_frozen_definition_host_aliases_and_queries(rig):
    """Mutation of caller dictionaries or inspection copies cannot alter approved inputs."""
    state, host, results = rig
    source = definition()
    prepared = launcher.prepare(source, host)
    before = prepared[0].data
    source['conversation']['turns'][0]['content'] = 'mutated'
    host['aliases']['qwen-demo'] = 'unauthorized'
    prepared[0].value()['configurations'][0]['defaults']['maxTokens'] = 4096
    assert prepared[0].data == before
    snapshot, _ = launcher.execute(prepared, results, handleInterrupt=False)
    value = snapshot.value()
    assert value['run']['status'] == 'succeeded'
    value['run']['turns'][0]['answer'] = 'mutated'
    assert snapshot.value()['run']['turns'][0]['answer'] == ' amber '


def test_merged_conflicts_and_host_alias_missing_never_launch(rig):
    """Validate conflicts after merging defaults/overrides and resolve aliases independently."""
    state, host, _ = rig
    value = definition()
    value['configurations'][0]['allowedOverrides'] = ['chatTemplateKwargs']
    value['configurations'][0]['defaults']['reasoningEffort'] = 'none'
    value['conversation']['turns'][1]['overrides'] = {'chatTemplateKwargs': {'enable_thinking': True}}
    with pytest.raises(schema.ValidationError):
        launcher.prepare(value, host)
    host['aliases']['qwen-demo'] = 'missing'
    with pytest.raises(schema.ValidationError):
        launcher.prepare(definition(), host)
    assert state['processes'] == []


def test_failure_second_turn_preserves_first_and_unknown_cleanup(rig):
    """Native incomplete second turn fails fast without retry or tombstone repair."""
    state, _, _ = rig
    state['streams'][1] = stream('partial answer', done=False)
    value, path = execute(rig)
    assert value['jobOutcome'] == 'Failed' and value['run']['status'] == 'failed'
    first, second = value['run']['turns']
    assert first['answer'] == ' amber ' and first['status'] == 'succeeded'
    assert second['status'] == 'failed' and second['answer'] is None
    assert second['partial']['answer'] == 'partial answer'
    assert second['cleanup']['quiescence'] == 'unknown'
    assert value['hostCleanup'] == 'failed-or-unknown'
    assert state['calls'] == 2 and len(state['processes']) == 1
    assert path.is_file() and launcher.exitCode(value) == 1


def test_cancel_before_job_has_diagnostics_without_transition(rig):
    """A Pending cancelled Job never enters the capability but retains its prepared run."""
    job = Job.new()
    job.requestCancellation()
    value, _ = execute(rig, job=job)
    assert value['jobOutcome'] == 'Cancelled' and value['run']['status'] == 'cancelled'
    assert all(turn['status'] == 'not-started' for turn in value['run']['turns'])
    assert rig[0]['processes'] == []


def test_cancel_during_stream_preserves_partial_and_separate_unknown(rig):
    """Cooperative Job cancellation remains primary even when native binding is unavailable."""
    state, _, _ = rig
    entered, stopped = Event(), Event()
    job = Job.new()

    def blocked():
        """Wait at the fake native read until the exact transport stop callback fires."""
        yield stream('partial')[0]
        entered.set()
        assert stopped.wait(5)
        raise OSError('private synthetic transport detail')

    state['streams'][0] = blocked()
    state['onStop'] = stopped.set
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute, rig, job=job)
        assert entered.wait(5)
        job.requestCancellation()
        value, _ = future.result(timeout=10)
    assert value['jobOutcome'] == 'Cancelled' and value['run']['status'] == 'cancelled'
    assert value['run']['turns'][0]['status'] == 'cancelled'
    assert value['run']['turns'][1]['status'] == 'not-started'
    assert value['run']['turns'][0]['partial']['answer'] == 'partial'
    assert value['run']['turns'][0]['cleanup']['quiescence'] == 'unknown'
    assert state['calls'] == 1 and len(state['processes']) == 1


def test_result_write_failure_preserves_successful_job(rig, monkeypatch):
    """Filesystem failure changes CLI projection without relabeling successful inference."""
    def fail(*args):
        """Inject only the private result persistence failure."""
        raise OSError('private path detail')
    monkeypatch.setattr(launcher, 'persist', fail)
    value, path = execute(rig)
    assert path is None and value['jobOutcome'] == 'Succeeded' and value['run']['status'] == 'succeeded'
    assert value['resultWriteFailure']['stage'] == 'before-teardown'
    assert launcher.exitCode(value) == 1 and value['hostCleanup'] == 'completed'


def runtimeFor(prepared, tmp_path):
    """Create the actual runtime for capability lifetime and independent-owner tests."""
    definitionValue, hostConfig, composer = prepared
    host = launcher.RuntimeHost(applicationStore=launcher.ApplicationStore(tmp_path / 'saves'),
        packResolver=launcher.PackResolver(roots=(launcher.DRIVER, launcher.APP)), providerComposers=(composer,))
    host.start()
    config = {'llamaCpp': hostConfig.value()['llamaCpp'], 'experimentRunner': launcher.policyFor(hostConfig)}
    plan = launcher.ManualActivationPlan(packIds=('llmDrivers.llamaCpp', 'applications.experimentRunner'))
    runtime = host.createApplication(appPackId='applications.experimentRunner', plan=plan, config=config)
    return host, runtime, plan, config


def test_recorder_overlap_inspection_and_application_isolation(rig, tmp_path):
    """Only one prepared invocation exists per app; foreign apps have independent recorders."""
    host, runtime, plan, config = runtimeFor(launcher.prepare(definition(), rig[1]), tmp_path)
    try:
        runId = runtime.invokeCapability('experimentRunner.prepare@1', definition())
        before = runtime.invokeCapability('experimentRunner.inspect@1')
        with pytest.raises(RuntimeError):
            runtime.invokeCapability('experimentRunner.prepare@1', definition())
        peer = host.createApplication(appPackId='applications.experimentRunner', plan=plan, config=config)
        assert peer.invokeCapability('experimentRunner.inspect@1') is None
        result = runtime.runJob('experimentRunner.run@1', runId)
        assert result.state.value == 'Succeeded'
        assert before.value()['turns'][0]['answer'] is None
        assert runtime.runJob('experimentRunner.run@1', runId).state.value == 'Failed'
        with pytest.raises(RuntimeError):
            runtime.invokeCapability('experimentRunner.run@1', runId)
        runtime.invokeCapability('experimentRunner.finish@1', {'runId': runId, 'jobOutcome': result.state.value})
        assert runtime.invokeCapability('experimentRunner.prepare@1', definition()) != runId
        assert peer.invokeCapability('experimentRunner.inspect@1') is None
    finally:
        host.stop()


def test_cancel_between_turns_keeps_completed_answer(rig, monkeypatch):
    """A cancellation immediately after the first complete call prevents the second send."""
    from backend.context.codeEntryContext import _LlmFacade
    original = _LlmFacade.run
    job = Job.new()

    def cancelAfterFirst(self, **kwargs):
        """Inject cancellation after actual provider finalization, before the next turn."""
        response = original(self, **kwargs)
        job.requestCancellation()
        return response

    monkeypatch.setattr(_LlmFacade, 'run', cancelAfterFirst)
    value, _ = execute(rig, job=job)
    assert value['jobOutcome'] == 'Cancelled' and value['run']['status'] == 'cancelled'
    assert value['run']['turns'][0]['status'] == 'succeeded'
    assert value['run']['turns'][0]['cleanup']['quiescence'] == 'confirmed'
    assert value['run']['turns'][1]['status'] == 'not-started'
    assert rig[0]['calls'] == 1 and value['hostCleanup'] == 'completed'


def test_unknown_cleanup_stops_remaining_turns_without_repair(rig):
    """A third configured turn remains explicitly not-started after second-turn UNKNOWN."""
    state, host, resultsDirectory = rig
    value = definition()
    value['conversation']['turns'].append({'id': 't3', 'role': 'user', 'content': 'Never sent'})
    state['streams'][1] = stream('incomplete', done=False)
    snapshot, _ = launcher.execute(launcher.prepare(value, host), resultsDirectory, handleInterrupt=False)
    run = snapshot.value()['run']
    assert [turn['status'] for turn in run['turns']] == ['succeeded', 'failed', 'not-started']
    assert state['calls'] == 2 and len(state['processes']) == 1


def test_empty_answer_is_valid_and_replayed_exactly(rig):
    """Empty ordinary answer differs from missing/failed output and still enters history."""
    state, _, _ = rig
    state['streams'][0] = stream('', reasoning='separate only')
    value, _ = execute(rig)
    assert value['run']['status'] == 'succeeded'
    assert value['run']['turns'][0]['answer'] == ''
    assert value['run']['turns'][1]['query']['messages'][1] == {'role': 'assistant', 'content': ''}


def test_restricted_transition_grant_cannot_be_invented_by_alias(rig):
    """An approved alias is not transition authority when the exact caller lacks its grant."""
    state, host, resultsDirectory = rig
    prepared = launcher.prepare(definition(), host)
    adapter = launcher.loadAdapter()
    old = prepared[2]
    grant = old.grants[0]
    composer = adapter.LlamaCppComposition(domainId=old.domainId, catalog=old.catalog,
        providerEntries=old.providerEntries, grants=(adapter.LlamaCppGrant(grant.entry, grant.selectors, False),))
    snapshot, _ = launcher.execute((prepared[0], prepared[1], composer), resultsDirectory, handleInterrupt=False)
    value = snapshot.value()
    assert value['jobOutcome'] == 'Failed' and value['run']['status'] == 'failed'
    assert state['processes'] == [] and state['calls'] == 0


def test_wrong_provider_source_identity_fails_before_launch(rig):
    """Profile labels cannot substitute for exact provider manifest/source compatibility."""
    state, host, resultsDirectory = rig
    prepared = launcher.prepare(definition(), host)
    adapter = launcher.loadAdapter()
    old = prepared[2]
    wrong = adapter.EntrySelector('llmDrivers.llamaCpp', '0.3.0', 'driver', '0' * 64)
    composer = adapter.LlamaCppComposition(domainId=old.domainId, catalog=old.catalog,
                                         providerEntries=(wrong,), grants=old.grants)
    snapshot, path = launcher.execute((prepared[0], prepared[1], composer), resultsDirectory, handleInterrupt=False)
    value = snapshot.value()
    assert value['launcherFailure'] is not None and value['jobOutcome'] == 'Pending'
    assert value['run']['status'] == 'failed' and path.is_file()
    assert all(turn['status'] == 'not-started' for turn in value['run']['turns'])
    assert state['processes'] == []


def test_output_bound_keeps_only_bounded_partial_diagnostics(rig):
    """Excess output fails the turn without truncating an answer into replay history."""
    state, host, _ = rig
    host['limits']['maxOutputCharacters'] = 4
    state['streams'][0] = stream('too long', reasoning='also long')
    value, _ = execute(rig)
    turn = value['run']['turns'][0]
    assert value['jobOutcome'] == 'Failed' and turn['answer'] is None
    assert turn['partial']['truncated'] and len(turn['partial']['answer']) + len(turn['partial']['reasoning']) <= 4
    assert turn['cleanup']['quiescence'] == 'confirmed'
    assert value['run']['turns'][1]['status'] == 'not-started'


def test_launcher_inspection_failure_preserves_recorded_turns(rig, monkeypatch):
    """Finalizer/launcher failure cannot overwrite complete turn outcomes or the Job."""
    original = launcher.RuntimeHost.createApplication

    def create(self, **kwargs):
        """Fail only the launcher's diagnostic finish seam after an actual complete Job."""
        runtime = original(self, **kwargs)
        invoke = runtime.invokeCapability

        def wrapped(capability, *args, **options):
            """Leave normal Application/runtime execution unchanged."""
            if capability == 'experimentRunner.finish@1':
                raise RuntimeError('Synthetic launcher finalization failure')
            return invoke(capability, *args, **options)
        runtime.invokeCapability = wrapped
        return runtime

    monkeypatch.setattr(launcher.RuntimeHost, 'createApplication', create)
    value, path = execute(rig)
    assert value['jobOutcome'] == 'Succeeded' and value['launcherFailure'] is not None
    assert value['run']['status'] == 'succeeded'
    assert all(turn['status'] == 'succeeded' for turn in value['run']['turns'])
    assert value['run']['applicationOutcome'] == 'completed' and path.is_file()
    assert launcher.exitCode(value) == 1


@pytest.mark.parametrize('key,bad', [('temperature', -0.1), ('temperature', 2.1), ('topP', 1.01),
    ('minP', -0.01), ('topK', 0), ('topK', 100001), ('topK', 1.0), ('repeatPenalty', 0),
    ('repeatPenalty', 10.1), ('seed', 2147483648), ('seed', -2147483649), ('seed', True),
    ('timeoutSeconds', 0), ('timeoutSeconds', 121), ('reasoningFormat', 'other'),
    ('reasoningEffort', ' '), ('chatTemplateKwargs', {'enable_thinking': 1}),
    ('chatTemplateKwargs', {'preserve_reasoning': None})])
def test_supported_option_bounds_reject_before_launch(rig, key, bad):
    """Every supported request option has strict type/range validation before activation."""
    value = definition()
    value['configurations'][0]['defaults'][key] = bad
    with pytest.raises(schema.ValidationError):
        launcher.prepare(value, rig[1])
    assert rig[0]['processes'] == [] and rig[0]['requests'] == []


def test_unknown_domain_rejects_next_invocation_without_restoration(rig, tmp_path):
    """A new run cannot use a later idle observation to repair an UNKNOWN residency."""
    state, hostValue, _ = rig
    state['streams'][0] = stream('unproven', done=False)
    host, runtime, _, _ = runtimeFor(launcher.prepare(definition(), hostValue), tmp_path)
    firstId = runtime.invokeCapability('experimentRunner.prepare@1', definition())
    first = runtime.runJob('experimentRunner.run@1', firstId)
    runtime.invokeCapability('experimentRunner.finish@1', {'runId': firstId, 'jobOutcome': first.state.value})
    secondId = runtime.invokeCapability('experimentRunner.prepare@1', definition())
    second = runtime.runJob('experimentRunner.run@1', secondId)
    snapshot = runtime.invokeCapability('experimentRunner.finish@1', {'runId': secondId, 'jobOutcome': second.state.value})
    assert first.state.value == second.state.value == 'Failed'
    assert snapshot.value()['error']['category'] == 'execution-conflict'
    assert snapshot.value()['error']['stage'] == 'transition'
    assert all(turn['status'] == 'not-started' for turn in snapshot.value()['turns'])
    assert state['calls'] == 1 and len(state['processes']) == 1
    from backend.llm.errors import LlmExecutionCleanupError
    with pytest.raises(LlmExecutionCleanupError):
        host.stop()


def test_validate_cli_never_launches_and_default_run_hides_content(rig, tmp_path, capsys):
    """The terminal surface exposes private content only through explicit inspect."""
    cli = importlib.import_module('first-party.applications.experimentRunner.run')
    state, host, resultsDirectory = rig
    definitionFile, hostFile = tmp_path / 'definition.json', tmp_path / 'host.json'
    definitionFile.write_bytes(schema.canonical(definition()))
    hostFile.write_bytes(schema.canonical(host))
    arguments = ['--definition', str(definitionFile), '--host-config', str(hostFile)]
    assert cli.main(['validate', *arguments]) == 0
    assert state['processes'] == []
    capsys.readouterr()
    assert cli.main(['run', *arguments, '--results', str(resultsDirectory)]) == 0
    output = capsys.readouterr().out
    assert 'private reasoning' not in output and 'Remember the word' not in output and 'amber' not in output
    path = json.loads(output)['path']
    assert cli.main(['inspect', '--result', path]) == 0
    assert 'private reasoning' in capsys.readouterr().out


def test_busy_peer_reservation_denies_runner_without_takeover(rig, tmp_path):
    """An existing authorized reservation blocks the runner's initial transition once."""
    from backend.registration import RegistrationScope
    state, hostValue, _ = rig
    host, runtime, plan, config = runtimeFor(launcher.prepare(definition(), hostValue), tmp_path)
    peer = host.createApplication(appPackId='applications.experimentRunner', plan=plan, config=config)
    held = {}

    def reserve(ctx, payload):
        """Test-only peer policy; production Runner does not implement EX-03 reservation."""
        profile = ctx.llm.profile(providerName='llama.cpp', model='Q')
        ctx.llm.transitionProfile(profile)
        held['reservation'] = ctx.llm.reserveConversation(profile)

    identity = next(identity for identity, _ in peer._codeEntries.values() if identity.codeEntryId == 'main')
    scope = RegistrationScope()
    peer.capabilities.register(scope, ownerId=identity.codeEntryInstanceId, capabilityId='test.reserve@1', handler=reserve)
    scope.publish()
    try:
        peer.invokeCapability('test.reserve@1')
        runId = runtime.invokeCapability('experimentRunner.prepare@1', definition())
        job = runtime.runJob('experimentRunner.run@1', runId)
        snapshot = runtime.invokeCapability('experimentRunner.finish@1', {'runId': runId, 'jobOutcome': job.state.value})
        assert job.state.value == 'Failed'
        assert snapshot.value()['error']['category'] == 'execution-conflict'
        assert state['calls'] == 0 and len(state['processes']) == 1 and not state['processes'][0].terminated
    finally:
        held['reservation'].close()
        host.stop()


def test_host_cleanup_failure_does_not_relabel_completed_job(rig, monkeypatch):
    """A separately failed host teardown makes CLI nonzero while preserving run/turn/Job outcomes."""
    original = launcher.RuntimeHost.stop

    def failAfterClose(self):
        """Exercise actual disposal before injecting a separate launcher teardown failure."""
        original(self)
        raise RuntimeError('Synthetic host cleanup diagnostic')

    monkeypatch.setattr(launcher.RuntimeHost, 'stop', failAfterClose)
    value, path = execute(rig)
    assert value['run']['status'] == 'succeeded' and value['jobOutcome'] == 'Succeeded'
    assert value['hostCleanup'] == 'failed-or-unknown' and launcher.exitCode(value) == 1
    assert all(turn['cleanup']['quiescence'] == 'confirmed' for turn in value['run']['turns'])
    assert json.loads(path.read_text(encoding='utf-8'))['hostCleanup'] == 'failed-or-unknown'


def test_second_write_failure_preserves_initial_private_file(rig, monkeypatch):
    """Failure updating cleanup evidence preserves the before-teardown file and primary outcomes."""
    original = launcher.persist
    writes = []

    def failSecond(*args):
        """Allow the mandatory initial atomic write, then reject only the cleanup update."""
        writes.append(True)
        if len(writes) == 2:
            raise OSError('Synthetic cleanup update failure')
        return original(*args)

    monkeypatch.setattr(launcher, 'persist', failSecond)
    value, path = execute(rig)
    assert value['run']['status'] == 'succeeded' and value['jobOutcome'] == 'Succeeded'
    assert value['resultWriteFailure']['stage'] == 'after-teardown'
    assert value['hostCleanup'] == 'completed' and launcher.exitCode(value) == 1
    assert json.loads(path.read_text(encoding='utf-8'))['hostCleanup'] == 'not-started'
