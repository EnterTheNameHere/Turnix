# file: tests/first_party/experimentRunner/test_experimentRunner.py ; version: 6
"""EX-01/02/R1/03 architectural tests: actual runtime/provider, synthetic process/HTTP IO."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from uuid import uuid4

from backend.orchestration import Job

launcher = importlib.import_module('first-party.applications.experimentRunner.launcher')
schema = launcher.schema
entry = importlib.import_module('first-party.applications.experimentRunner.codeEntry')


def definition():
    """Return an independent machine-neutral two-turn example for each test."""
    return launcher.readJson(launcher.APP / 'example.definition.json')


def unitPayload(value):
    """Build the versioned unit invocation seam without modifying declarative definitions."""
    return {'unitId': uuid4().hex, 'definition': value}


def hostValue(tmp_path):
    """Select only fake local assets and explicit host caps for synthetic verification."""
    executable, model = tmp_path / 'server.exe', tmp_path / 'model.gguf'
    executable.touch()
    model.touch()
    return {'schemaId': 'actant.experiment.host@2',
            'llamaCpp': {'manageServer': True, 'executable': str(executable), 'defaultModel': 'Q',
                         'models': {'Q': {'modelPath': str(model), 'contextWindowTokens': 1024,
                                          'serverReasoning': 'off', 'parallelSlots': 1}}},
            'aliases': {'qwen-demo': 'Q'}, 'limits': {'maxTokens': 24, 'timeoutSeconds': 30,
                                                   'maxTurns': 4, 'maxOutputCharacters': 4096, 'maxConfigurations': 8,
                                                   'maxRepetitions': 10, 'maxExecutions': 32, 'maxTurnCalls': 256},
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
    state = {'processes': [], 'requests': [], 'requestBytes': [], 'calls': 0,
             'streams': [stream(' amber '), stream('answer two')]}

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
        state['requestBytes'].append((request.full_url, request.data))
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
    run = value['suite']['units'][0]['run']
    assert run['status'] == 'succeeded' and value['suite']['units'][0]['jobOutcome'] == 'Succeeded'
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
    assert value['suite']['units'][0]['configurationEvidence'] is not None
    assert 'id_task' not in path.read_text(encoding='utf-8')
    state['streams'].extend([stream('new first'), stream('new second')])
    again, other = execute(rig)
    # A second invocation uses the same declarative identity but a fresh opaque run identity.
    assert again['suite']['units'][0]['run']['experimentId'] == run['experimentId'] and other != path
    assert again['suite']['units'][0]['run']['status'] == 'succeeded'


@pytest.mark.parametrize('path,new', [
    (('unknown',), 1), (('configurations',), []), (('repetitions',), True), (('repetitions',), 0),
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
    assert value['suite']['units'][0]['run']['status'] == 'succeeded'
    value['suite']['units'][0]['run']['turns'][0]['answer'] = 'mutated'
    assert snapshot.value()['suite']['units'][0]['run']['turns'][0]['answer'] == ' amber '


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
    assert value['suite']['units'][0]['jobOutcome'] == 'Failed' and value['suite']['units'][0]['run']['status'] == 'failed'
    first, second = value['suite']['units'][0]['run']['turns']
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
    assert value['suite']['status'] == 'cancelled' and value['suite']['units'][0]['jobOutcome'] is None
    assert all(turn['status'] == 'not-started' for turn in value['suite']['units'][0]['run']['turns'])
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
    assert value['suite']['units'][0]['jobOutcome'] == 'Cancelled' and value['suite']['units'][0]['run']['status'] == 'cancelled'
    assert value['suite']['units'][0]['run']['turns'][0]['status'] == 'cancelled'
    assert value['suite']['units'][0]['run']['turns'][1]['status'] == 'not-started'
    assert value['suite']['units'][0]['run']['turns'][0]['partial']['answer'] == 'partial'
    assert value['suite']['units'][0]['run']['turns'][0]['cleanup']['quiescence'] == 'unknown'
    assert state['calls'] == 1 and len(state['processes']) == 1


def test_result_write_failure_preserves_successful_job(rig, monkeypatch):
    """Filesystem failure changes CLI projection without relabeling successful inference."""
    def fail(*args):
        """Inject only the private result persistence failure."""
        raise OSError('private path detail')
    monkeypatch.setattr(launcher, 'persist', fail)
    value, path = execute(rig)
    assert path is None and value['suite']['units'][0]['jobOutcome'] == 'Succeeded' and value['suite']['units'][0]['run']['status'] == 'succeeded'
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
        runId = runtime.invokeCapability('experimentRunner.prepare@1', unitPayload(definition()))
        before = runtime.invokeCapability('experimentRunner.inspect@1')
        with pytest.raises(RuntimeError):
            runtime.invokeCapability('experimentRunner.prepare@1', unitPayload(definition()))
        peer = host.createApplication(appPackId='applications.experimentRunner', plan=plan, config=config)
        assert peer.invokeCapability('experimentRunner.inspect@1') is None
        result = runtime.runJob('experimentRunner.run@1', runId)
        assert result.state.value == 'Succeeded'
        assert before.value()['turns'][0]['answer'] is None
        assert runtime.runJob('experimentRunner.run@1', runId).state.value == 'Failed'
        with pytest.raises(RuntimeError):
            runtime.invokeCapability('experimentRunner.run@1', runId)
        runtime.invokeCapability('experimentRunner.finish@1', {'runId': runId, 'jobOutcome': result.state.value})
        assert runtime.invokeCapability('experimentRunner.prepare@1', unitPayload(definition())) != runId
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
    assert value['suite']['units'][0]['jobOutcome'] == 'Cancelled' and value['suite']['units'][0]['run']['status'] == 'cancelled'
    assert value['suite']['units'][0]['run']['turns'][0]['status'] == 'succeeded'
    assert value['suite']['units'][0]['run']['turns'][0]['cleanup']['quiescence'] == 'confirmed'
    assert value['suite']['units'][0]['run']['turns'][1]['status'] == 'not-started'
    assert rig[0]['calls'] == 1 and value['hostCleanup'] == 'completed'


def test_unknown_cleanup_stops_remaining_turns_without_repair(rig):
    """A third configured turn remains explicitly not-started after second-turn UNKNOWN."""
    state, host, resultsDirectory = rig
    value = definition()
    value['conversation']['turns'].append({'id': 't3', 'role': 'user', 'content': 'Never sent'})
    state['streams'][1] = stream('incomplete', done=False)
    snapshot, _ = launcher.execute(launcher.prepare(value, host), resultsDirectory, handleInterrupt=False)
    run = snapshot.value()['suite']['units'][0]['run']
    assert [turn['status'] for turn in run['turns']] == ['succeeded', 'failed', 'not-started']
    assert state['calls'] == 2 and len(state['processes']) == 1


def test_empty_answer_is_valid_and_replayed_exactly(rig):
    """Empty ordinary answer differs from missing/failed output and still enters history."""
    state, _, _ = rig
    state['streams'][0] = stream('', reasoning='separate only')
    value, _ = execute(rig)
    assert value['suite']['units'][0]['run']['status'] == 'succeeded'
    assert value['suite']['units'][0]['run']['turns'][0]['answer'] == ''
    assert value['suite']['units'][0]['run']['turns'][1]['query']['messages'][1] == {'role': 'assistant', 'content': ''}


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
    assert value['suite']['units'][0]['jobOutcome'] == 'Failed' and value['suite']['units'][0]['run']['status'] == 'failed'
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
    assert value['launcherFailure'] is not None and value['suite']['units'][0]['jobOutcome'] is None
    assert value['suite']['status'] == 'failed' and path.is_file()
    assert all(turn['status'] == 'not-started' for turn in value['suite']['units'][0]['run']['turns'])
    assert state['processes'] == []


def test_output_bound_keeps_only_bounded_partial_diagnostics(rig):
    """Excess output fails the turn without truncating an answer into replay history."""
    state, host, _ = rig
    host['limits']['maxOutputCharacters'] = 4
    state['streams'][0] = stream('too long', reasoning='also long')
    value, _ = execute(rig)
    turn = value['suite']['units'][0]['run']['turns'][0]
    assert value['suite']['units'][0]['jobOutcome'] == 'Failed' and turn['answer'] is None
    assert turn['partial']['truncated'] and len(turn['partial']['answer']) + len(turn['partial']['reasoning']) <= 4
    assert turn['cleanup']['quiescence'] == 'confirmed'
    assert value['suite']['units'][0]['run']['turns'][1]['status'] == 'not-started'


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
    assert value['suite']['units'][0]['jobOutcome'] == 'Succeeded' and value['launcherFailure'] is not None
    assert value['suite']['units'][0]['run']['status'] == 'succeeded'
    assert all(turn['status'] == 'succeeded' for turn in value['suite']['units'][0]['run']['turns'])
    assert value['suite']['units'][0]['run']['applicationOutcome'] == 'completed' and path.is_file()
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
    """A different prepared peer cannot repair UNKNOWN through a later transition."""
    state, hostValue, _ = rig
    state['streams'][0] = stream('unproven', done=False)
    host, runtime, plan, config = runtimeFor(launcher.prepare(definition(), hostValue), tmp_path)
    peer = host.createApplication(appPackId='applications.experimentRunner', plan=plan, config=config)
    firstReader = runtime.invokeCapability('experimentRunner.reader@1')
    secondReader = peer.invokeCapability('experimentRunner.reader@1')
    firstId = runtime.invokeCapability('experimentRunner.prepare@1', unitPayload(definition()))
    secondId = peer.invokeCapability('experimentRunner.prepare@1', unitPayload(definition()))
    first = runtime.runJob('experimentRunner.run@1', firstId)
    second = peer.runJob('experimentRunner.run@1', secondId)
    assert first.state.value == second.state.value == 'Failed'
    assert firstReader.read().value()['turns'][0]['cleanup']['quiescence'] == 'unknown'
    snapshot = secondReader.read()
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
        runId = runtime.invokeCapability('experimentRunner.prepare@1', unitPayload(definition()))
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
    assert value['suite']['units'][0]['run']['status'] == 'succeeded' and value['suite']['units'][0]['jobOutcome'] == 'Succeeded'
    assert value['hostCleanup'] == 'failed-or-unknown' and launcher.exitCode(value) == 1
    assert all(turn['cleanup']['quiescence'] == 'confirmed' for turn in value['suite']['units'][0]['run']['turns'])
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
    assert value['suite']['units'][0]['run']['status'] == 'succeeded' and value['suite']['units'][0]['jobOutcome'] == 'Succeeded'
    assert value['resultWriteFailure']['stage'] == 'after-teardown'
    assert value['hostCleanup'] == 'completed' and launcher.exitCode(value) == 1
    assert json.loads(path.read_text(encoding='utf-8'))['hostCleanup'] == 'not-started'


def test_review_interrupt_during_persist_still_closes_owned_host(rig, monkeypatch):
    """Interrupt result persistence after inference and require owned child reap."""
    def interrupt(*args):
        """Represent Ctrl+C after the Job signal handler has been restored."""
        raise KeyboardInterrupt()
    monkeypatch.setattr(launcher, 'persist', interrupt)
    with pytest.raises(KeyboardInterrupt):
        execute(rig)
    assert all(process.returncode is not None for process in rig[0]['processes']), 'owned child remains alive'


@pytest.mark.parametrize('boundary', ['first-write', 'evidence', 'finalization', 'second-write'])
def test_post_job_interrupt_reaps_child_and_preserves_primary_outcomes(rig, monkeypatch, boundary):
    """Every post-Job diagnostic boundary defers the same interrupt through owned teardown."""
    interruption = KeyboardInterrupt('private interrupt detail')
    writes = []
    originalPersist = launcher.persist

    def persist(*args):
        """Inject once at the selected write, preserving ordinary atomic writes otherwise."""
        writes.append(True)
        if boundary == 'first-write' and len(writes) == 1 or boundary == 'second-write' and len(writes) == 2:
            raise interruption
        return originalPersist(*args)

    monkeypatch.setattr(launcher, 'persist', persist)
    if boundary == 'evidence':
        def evidence(*args):
            """Interrupt only the composer's explicit private evidence accessor."""
            raise interruption
        monkeypatch.setattr(launcher.loadAdapter().LlamaCppComposition, 'configurationEvidence', evidence)
    if boundary == 'finalization':
        originalCreate = launcher.RuntimeHost.createApplication

        def create(self, **kwargs):
            """Keep the actual runtime, intercepting diagnostic finish after Job completion."""
            runtime = originalCreate(self, **kwargs)
            originalInvoke = runtime.invokeCapability

            def invoke(capability, *args, **options):
                """Leave run/inspect and all provider behavior unchanged."""
                if capability == 'experimentRunner.finish@1':
                    raise interruption
                return originalInvoke(capability, *args, **options)
            runtime.invokeCapability = invoke
            return runtime
        monkeypatch.setattr(launcher.RuntimeHost, 'createApplication', create)
    with pytest.raises(KeyboardInterrupt) as caught:
        execute(rig)
    assert caught.value is interruption
    value = caught.value.experimentExecution.value()
    assert value['suite']['units'][0]['jobOutcome'] == 'Succeeded' and value['suite']['units'][0]['run']['status'] == 'succeeded'
    assert [turn['status'] for turn in value['suite']['units'][0]['run']['turns']] == ['succeeded', 'succeeded']
    assert all(turn['cleanup']['quiescence'] == 'confirmed' for turn in value['suite']['units'][0]['run']['turns'])
    assert value['hostCleanup'] == 'completed' and launcher.exitCode(value) == 1
    assert value['launcherFailure']['category'] == 'launcher-interrupted'
    assert 'private interrupt detail' not in json.dumps(value)
    assert len(rig[0]['processes']) == 1 and rig[0]['processes'][0].returncode == 0
    if boundary in ('first-write', 'second-write'):
        assert value['resultWriteFailure']['stage'] == ('before-teardown' if boundary == 'first-write' else 'after-teardown')
    else:
        saved = list(rig[2].glob('*.json'))
        assert len(saved) == 1 and json.loads(saved[0].read_text(encoding='utf-8')) == value


def test_persist_interrupt_keeps_failed_job_and_unknown_cleanup(rig, monkeypatch):
    """Interrupting diagnostics neither repairs an UNKNOWN provider nor relabels the failed Job."""
    rig[0]['streams'][1] = stream('incomplete', done=False)

    def interrupt(*args):
        """Interrupt the first write after the real runtime has observed provider failure."""
        raise KeyboardInterrupt()
    monkeypatch.setattr(launcher, 'persist', interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        execute(rig)
    value = caught.value.experimentExecution.value()
    assert value['suite']['units'][0]['jobOutcome'] == 'Failed' and value['suite']['units'][0]['run']['status'] == 'failed'
    assert [turn['status'] for turn in value['suite']['units'][0]['run']['turns']] == ['succeeded', 'failed']
    assert value['suite']['units'][0]['run']['turns'][1]['cleanup']['quiescence'] == 'unknown'
    assert value['hostCleanup'] == 'failed-or-unknown'
    assert rig[0]['calls'] == 2 and len(rig[0]['processes']) == 1
    assert rig[0]['processes'][0].returncode is None  # No unauthorized UNKNOWN disposal or repair.


def test_persist_interrupt_keeps_cancelled_job(rig, monkeypatch):
    """Post-Job interruption retains cancellation independently of private write failure."""
    job = Job.new()
    job.requestCancellation()

    def interrupt(*args):
        """Represent an interrupt while persisting the prepared cancelled invocation."""
        raise KeyboardInterrupt()
    monkeypatch.setattr(launcher, 'persist', interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        execute(rig, job=job)
    value = caught.value.experimentExecution.value()
    assert value['suite']['status'] == 'cancelled' and value['suite']['units'][0]['jobOutcome'] is None
    assert all(turn['status'] == 'not-started' for turn in value['suite']['units'][0]['run']['turns'])
    assert value['hostCleanup'] == 'completed' and rig[0]['processes'] == []


def test_cleanup_interrupt_cannot_replace_earlier_persist_interrupt(rig, monkeypatch):
    """Secondary cleanup interruption remains unknown while the first interrupt stays primary."""
    first, second = KeyboardInterrupt('first'), KeyboardInterrupt('second')
    originalStop = launcher.RuntimeHost.stop

    def persist(*args):
        """Supply the primary post-Job interruption."""
        raise first

    def stop(self):
        """Dispose the synthetic child normally, then report an independent cleanup interruption."""
        originalStop(self)
        raise second
    monkeypatch.setattr(launcher, 'persist', persist)
    monkeypatch.setattr(launcher.RuntimeHost, 'stop', stop)
    with pytest.raises(KeyboardInterrupt) as caught:
        execute(rig)
    assert caught.value is first
    value = caught.value.experimentExecution.value()
    assert value['hostCleanup'] == 'failed-or-unknown'
    assert value['suite']['units'][0]['jobOutcome'] == 'Succeeded' and value['suite']['units'][0]['run']['status'] == 'succeeded'
    assert len(rig[0]['processes']) == 1 and rig[0]['processes'][0].returncode == 0


def test_actual_post_job_sigint_restores_handler_and_reaps_child(rig, monkeypatch):
    """A real Python SIGINT at first write follows the restored handler and still tears down."""
    import signal
    before = signal.getsignal(signal.SIGINT)

    def persist(*args):
        """Trigger Python's actual SIGINT handling after runJob restored the caller's handler."""
        assert signal.getsignal(signal.SIGINT) is before
        signal.raise_signal(signal.SIGINT)
    monkeypatch.setattr(launcher, 'persist', persist)
    with pytest.raises(KeyboardInterrupt) as caught:
        launcher.execute(launcher.prepare(definition(), rig[1]), rig[2])
    assert signal.getsignal(signal.SIGINT) is before
    assert caught.value.experimentExecution.value()['hostCleanup'] == 'completed'
    assert len(rig[0]['processes']) == 1 and rig[0]['processes'][0].returncode == 0


def suiteDefinition(rig, *, differentStartup=True):
    """Configure a synthetic two-profile/two-repetition suite within the authorized ceiling."""
    state, host, _ = rig
    value = definition()
    second = json.loads(json.dumps(value['configurations'][0]))
    second.update(name='second', profile='qwen-three' if differentStartup else 'qwen-demo')
    second['defaults']['temperature'] = 0.2
    value['configurations'].append(second)
    value['repetitions'] = 2
    host['llamaCpp']['models']['Q']['threads'] = 4
    host['llamaCpp']['models']['Q3'] = dict(host['llamaCpp']['models']['Q'], threads=3)
    host['aliases']['qwen-three'] = 'Q3'
    state['streams'] = [stream('answer-' + str(index), 'separate-' + str(index)) for index in range(8)]
    return value


def executeSuite(rig, value, **kwargs):
    """Run a validated expanded suite through the production launcher and actual runtime."""
    snapshot, path = launcher.execute(launcher.prepare(value, rig[1]), rig[2], handleInterrupt=False, **kwargs)
    return snapshot.value(), path


def test_ex03_order_history_sessions_generations_and_job_boundaries(rig, monkeypatch):
    """Config-major 2x2x2 execution uses independent histories and exact per-unit sessions."""
    from backend.context.codeEntryContext import _LlmFacade
    value = suiteDefinition(rig)
    sessions, jobs, boundaries = [], [], []
    originalRun, originalJob, originalTransition = _LlmFacade.run, launcher.RuntimeHost.createApplication, _LlmFacade.transitionProfile

    def run(self, **kwargs):
        """Observe exact session identity while exercising every actual admitted call."""
        sessions.append(kwargs['session'])
        return originalRun(self, **kwargs)

    def transition(self, profile):
        """Verify a prior Job is terminal and neutral exclusion drained before next transition."""
        if jobs:
            assert jobs[-1].terminal
            assert boundaries[-1].exclusive is False and boundaries[-1].operations == 0
        return originalTransition(self, profile)

    def create(self, **kwargs):
        """Record returned actual Jobs and the public resource snapshot at each boundary."""
        runtime = originalJob(self, **kwargs)
        runJob = runtime.runJob

        def wrapped(*args, **options):
            """Observe only after runtime lifetime finalization has returned."""
            result = runJob(*args, **options)
            jobs.append(result)
            boundaries.append(self.executionResources.snapshots[0])
            return result
        runtime.runJob = wrapped
        return runtime

    monkeypatch.setattr(_LlmFacade, 'run', run)
    monkeypatch.setattr(_LlmFacade, 'transitionProfile', transition)
    monkeypatch.setattr(launcher.RuntimeHost, 'createApplication', create)
    result, path = executeSuite(rig, value)
    assert launcher.exitCode(result) == 0, result
    units = result['suite']['units']
    assert [(unit['configurationName'], unit['repetition']) for unit in units] == [('baseline', 1), ('baseline', 2), ('second', 1), ('second', 2)]
    assert len({unit['unitId'] for unit in units}) == 4 and len({job.jobId for job in jobs}) == 4
    assert all(unit['run']['runId'] == unit['unitId'] for unit in units)
    assert len(sessions) == 8 and len({id(session) for session in sessions}) == 4
    for index, unit in enumerate(units):
        assert sessions[index * 2] is sessions[index * 2 + 1]
        first, second = unit['run']['turns']
        assert first['query']['messages'] == [{'role': 'user', 'content': value['conversation']['turns'][0]['content']}]
        assert second['query']['messages'][1] == {'role': 'assistant', 'content': 'answer-' + str(index * 2)}
        assert unit['run']['reservation'] == {'acquisition': 'acquired', 'close': 'queued', 'releaseReceipt': 'unavailable'}
        assert unit['lifetimeBoundary'] == 'completed' and unit['jobOutcome'] == 'Succeeded'
        assert all(turn['reasoning'].startswith('separate-') for turn in unit['run']['turns'])
    generations = [unit['run']['turns'][0]['executionEvidence']['residencyGeneration'] for unit in units]
    assert generations[0] == generations[1] and generations[2] == generations[3] and generations[0] != generations[2]
    assert len(rig[0]['processes']) == 2 and all(process.returncode == 0 for process in rig[0]['processes'])
    assert rig[0]['calls'] == 8 and json.loads(path.read_text(encoding='utf-8')) == result
    wire = [body['messages'] for url, body in rig[0]['requests'] if url.endswith('/completions')]
    assert wire == [turn['query']['messages'] for unit in units for turn in unit['run']['turns']]


def test_ex03_sampling_only_configurations_reuse_same_residency(rig):
    """Names and sampling controls cannot force reloads of compatible startup profiles."""
    result, _ = executeSuite(rig, suiteDefinition(rig, differentStartup=False))
    assert result['suite']['status'] == 'succeeded'
    assert len(rig[0]['processes']) == 1
    assert len({unit['run']['turns'][0]['executionEvidence']['residencyIdentity'] for unit in result['suite']['units']}) == 1


@pytest.mark.parametrize('bad', ['later-alias', 'later-options', 'duplicate-name', 'bool', 'zero', 'negative',
                                'config-cap', 'repetition-cap', 'execution-cap', 'turn-call-cap', 'memory-cap', 'version'])
def test_ex03_all_preflight_errors_precede_first_launch(rig, bad):
    """Invalid later configurations and every expansion/memory ceiling reject the entire suite."""
    value = suiteDefinition(rig)
    if bad == 'later-alias':
        value['configurations'][1]['profile'] = 'unapproved'
    elif bad == 'later-options':
        value['configurations'][1]['defaults']['maxTokens'] = 25
    elif bad == 'duplicate-name':
        value['configurations'][1]['name'] = 'baseline'
    elif bad in ('bool', 'zero', 'negative'):
        value['repetitions'] = {'bool': True, 'zero': 0, 'negative': -1}[bad]
    elif bad == 'version':
        value['schemaId'] = 'actant.experiment.definition@1'
    elif bad == 'memory-cap':
        rig[1]['limits']['maxOutputCharacters'] = 65536
        value['repetitions'] = 10
        value['conversation']['turns'] *= 2
        for index, turn in enumerate(value['conversation']['turns']):
            value['conversation']['turns'][index] = dict(turn, id=str(index))
    else:
        key = {'config-cap': 'maxConfigurations', 'repetition-cap': 'maxRepetitions', 'execution-cap': 'maxExecutions', 'turn-call-cap': 'maxTurnCalls'}[bad]
        rig[1]['limits'][key] = 1
    with pytest.raises(schema.ValidationError):
        launcher.prepare(value, rig[1])
    assert rig[0]['processes'] == [] and rig[0]['requests'] == []


def test_ex03_only_selected_deduplicated_grants_and_hash(rig):
    """Aliases do not broaden grants; canonical frozen suite hashes include ordered configs."""
    value = suiteDefinition(rig, differentStartup=False)
    prepared = launcher.prepare(value, rig[1])
    assert prepared[2].grants[0].selectors == ('Q',)
    before = prepared[0].sha256
    value['configurations'][1]['name'] = 'mutated'
    assert prepared[0].sha256 == before
    assert prepared[0].value()['configurations'][1]['name'] == 'second'
    reversedValue = prepared[0].value()
    reversedValue['configurations'].reverse()
    assert launcher.prepare(reversedValue, rig[1])[0].sha256 != before


def test_ex03_cancellation_between_units_prevents_next_job(rig, monkeypatch):
    """A suite flag survives the absence of a Running Job and keeps completed Jobs intact."""
    value = suiteDefinition(rig)
    control = launcher.SuiteControl()
    original = control.end

    def end(job):
        """Cancel in the actual gap after dropping the first terminal Job handle."""
        original(job)
        assert job.state.value == 'Succeeded'
        control.cancel()
    monkeypatch.setattr(control, 'end', end)
    result, _ = executeSuite(rig, value, control=control)
    assert result['suite']['status'] == 'cancelled'
    assert [unit['status'] for unit in result['suite']['units']] == ['succeeded', 'not-started', 'not-started', 'not-started']
    assert result['suite']['units'][0]['jobOutcome'] == 'Succeeded'
    assert all(unit['jobOutcome'] is None for unit in result['suite']['units'][1:])
    assert rig[0]['calls'] == 2 and result['hostCleanup'] == 'completed'


def test_ex03_later_unit_unknown_preserves_earlier_units_and_stops(rig):
    """No later configuration is loaded after a failed reserved conversation quarantines cleanup."""
    value = suiteDefinition(rig)
    rig[0]['streams'][3] = stream('unproven partial', done=False)
    result, _ = executeSuite(rig, value)
    assert [unit['status'] for unit in result['suite']['units']] == ['succeeded', 'failed', 'not-started', 'not-started']
    failed = result['suite']['units'][1]
    assert failed['jobOutcome'] == 'Failed' and failed['lifetimeBoundary'] == 'failed-or-unknown'
    assert failed['run']['reservation']['close'] == 'queued'
    assert failed['run']['turns'][1]['partial']['answer'] == 'unproven partial'
    assert failed['run']['turns'][1]['cleanup']['quiescence'] == 'unknown'
    assert rig[0]['calls'] == 4 and len(rig[0]['processes']) == 1
    assert result['hostCleanup'] == 'failed-or-unknown'


@pytest.mark.parametrize('cancelled', [False, True])
def test_ex03_close_failure_keeps_primary_outcomes_and_no_next_unit(rig, monkeypatch, cancelled):
    """Reservation-close failure is separate from successful turns or primary cancellation."""
    from backend.llm.executionSessions import LlmConversationSession
    from backend.context.codeEntryContext import _LlmFacade
    value = suiteDefinition(rig)
    originalClose, originalRun = LlmConversationSession.close, _LlmFacade.run
    control = launcher.SuiteControl()

    def close(self):
        """Queue actual release, then inject an independent close-reporting failure."""
        originalClose(self)
        raise RuntimeError('Synthetic close failure')

    def run(self, **kwargs):
        """Cancel only after an actual complete first call when this variant requires it."""
        result = originalRun(self, **kwargs)
        if cancelled:
            control.cancel()
        return result
    monkeypatch.setattr(LlmConversationSession, 'close', close)
    monkeypatch.setattr(_LlmFacade, 'run', run)
    result, _ = executeSuite(rig, value, control=control)
    first = result['suite']['units'][0]
    assert first['jobOutcome'] == ('Cancelled' if cancelled else 'Failed')
    assert first['run']['reservation']['close'] == 'failed' and first['lifetimeBoundary'] == 'completed'
    assert first['run']['turns'][0]['status'] == 'succeeded'
    assert all(unit['status'] == 'not-started' for unit in result['suite']['units'][1:])


def publishTestCapability(runtime, capability, handler):
    """Grant test-only operations to the real initially composed Application caller."""
    from backend.registration import RegistrationScope
    identity = next(identity for identity, _ in runtime._codeEntries.values() if identity.codeEntryId == 'main')
    scope = RegistrationScope()
    runtime.capabilities.register(scope, ownerId=identity.codeEntryInstanceId, capabilityId=capability, handler=handler)
    scope.publish()
    return scope


def test_ex03_peer_exclusion_between_turns_and_release_after_job(rig, tmp_path, monkeypatch):
    """Even same-profile peer calls are denied through the idle gap between reserved turns."""
    from backend.context.codeEntryContext import _LlmFacade
    from backend.llm.errors import LlmExecutionConflictError, LlmExecutionOwnershipError
    value = suiteDefinition(rig)
    prepared = launcher.prepare(value, rig[1])
    host, runtime, plan, config = runtimeFor(prepared, tmp_path)
    peer = host.createApplication(appPackId='applications.experimentRunner', plan=plan, config=config)
    entered, release = Event(), Event()
    original = _LlmFacade.run
    held = {}

    def pause(self, **kwargs):
        """Pause after complete provider cleanup while the actual conversation remains reserved."""
        response = original(self, **kwargs)
        if rig[0]['calls'] == 1:
            held['session'] = kwargs['session']
            entered.set()
            assert release.wait(5)
        return response

    def infer(ctx, payload):
        """Use only the normal admitted peer facade, optionally with a foreign issued session."""
        return ctx.llm.run(providerName='llama.cpp', model='Q',
            query=entry.LlmMessages((entry.LlmMessage('user', 'peer'),)).toQuery(),
            session=held['session'] if payload else None)

    def switch(ctx, payload):
        """Request an actual incompatible transition using independently granted authority."""
        return ctx.llm.transitionProfile(ctx.llm.profile(providerName='llama.cpp', model='Q3'))

    publishTestCapability(peer, 'test.infer@1', infer)
    publishTestCapability(peer, 'test.switch@1', switch)
    publishTestCapability(runtime, 'test.stale@1', infer)
    monkeypatch.setattr(_LlmFacade, 'run', pause)
    single = schema.unitDefinition(prepared[0], 0)
    runId = runtime.invokeCapability('experimentRunner.prepare@1', unitPayload(single.value()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.runJob, 'experimentRunner.run@1', runId)
        assert entered.wait(5)
        try:
            assert host.executionResources.snapshots[0].exclusive is True
            with pytest.raises(LlmExecutionConflictError):
                peer.invokeCapability('test.infer@1', False)
            with pytest.raises(LlmExecutionConflictError):
                peer.invokeCapability('test.switch@1')
            with pytest.raises(LlmExecutionOwnershipError):
                peer.invokeCapability('test.infer@1', True)
            assert len(rig[0]['processes']) == 1 and not rig[0]['processes'][0].terminated
        finally:
            release.set()
        assert future.result(timeout=10).state.value == 'Succeeded'
    assert host.executionResources.snapshots[0].exclusive is False
    with pytest.raises(LlmExecutionOwnershipError):
        runtime.invokeCapability('test.stale@1', True)
    assert peer.invokeCapability('test.infer@1', False).rawText == 'answer-2'
    peer.invokeCapability('test.switch@1')
    assert len(rig[0]['processes']) == 2
    host.stop()


def test_ex03_transition_reservation_race_preserves_winner(rig, tmp_path, monkeypatch):
    """Another owner winning after transition causes one denial without takeover or retry."""
    from backend.context.codeEntryContext import _LlmFacade
    value = suiteDefinition(rig)
    prepared = launcher.prepare(value, rig[1])
    host, runtime, plan, config = runtimeFor(prepared, tmp_path)
    peer = host.createApplication(appPackId='applications.experimentRunner', plan=plan, config=config)
    reader = runtime.invokeCapability('experimentRunner.reader@1')
    original = _LlmFacade.reserveConversation
    winner, racing = {}, [False]

    def reserve(ctx, payload):
        """Install the actual peer reservation in the explicit transition/reserve gap."""
        profile = ctx.llm.profile(providerName='llama.cpp', model='Q')
        winner['session'] = original(ctx.llm, profile)

    def race(self, profile):
        """Let the peer win exactly once before admitting the runner's reservation."""
        if not racing[0]:
            racing[0] = True
            peer.invokeCapability('test.reserve@1')
        return original(self, profile)

    publishTestCapability(peer, 'test.reserve@1', reserve)
    monkeypatch.setattr(_LlmFacade, 'reserveConversation', race)
    runId = runtime.invokeCapability('experimentRunner.prepare@1', unitPayload(schema.unitDefinition(prepared[0], 0).value()))
    job = runtime.runJob('experimentRunner.run@1', runId)
    assert job.state.value == 'Failed'
    run = reader.read().value()
    assert run['error']['category'] == 'execution-conflict' and run['error']['stage'] == 'reservation'
    assert run['reservation']['acquisition'] == 'failed' and run['reservation']['close'] == 'not-attempted'
    assert host.executionResources.snapshots[0].exclusive is True
    assert rig[0]['calls'] == 0 and len(rig[0]['processes']) == 1 and not rig[0]['processes'][0].terminated
    winner['session'].close()
    host.stop()


def test_ex03_lifetime_boundary_failure_stops_after_successful_job(rig, monkeypatch):
    """A failed returned-boundary report cannot certify a unit merely because its Job succeeded."""
    value = suiteDefinition(rig)
    create = launcher.RuntimeHost.createApplication

    def wrappedCreate(self, **kwargs):
        """Keep actual runtime execution and inject a separate lifetime-boundary failure."""
        runtime = create(self, **kwargs)
        original = runtime.runJob

        def wrapped(*args, **options):
            """Return no success receipt after the actual Job has terminally completed."""
            original(*args, **options)
            raise RuntimeError('Synthetic lifetime boundary failure')
        runtime.runJob = wrapped
        return runtime
    monkeypatch.setattr(launcher.RuntimeHost, 'createApplication', wrappedCreate)
    result, _ = executeSuite(rig, value)
    first = result['suite']['units'][0]
    assert first['jobOutcome'] == 'Succeeded' and first['run']['status'] == 'succeeded'
    assert first['status'] == 'failed' and first['lifetimeBoundary'] == 'failed-or-unknown'
    assert result['suite']['status'] == 'failed' and rig[0]['calls'] == 2
    assert all(unit['status'] == 'not-started' for unit in result['suite']['units'][1:])


def test_ex03_cancellation_before_first_unit_has_no_job_admission(rig):
    """Already cancelled suites retain bounded planned units without any transition or Job."""
    value = suiteDefinition(rig)
    control = launcher.SuiteControl()
    control.cancel()
    result, _ = executeSuite(rig, value, control=control)
    assert result['suite']['status'] == 'cancelled'
    assert all(unit['status'] == 'not-started' and unit['jobOutcome'] is None for unit in result['suite']['units'])
    assert rig[0]['calls'] == 0 and rig[0]['processes'] == []


def test_ex03_cancellation_in_later_reserved_stream_keeps_earlier_job(rig):
    """Suite cancellation targets the exact active Job and leaves earlier terminal Jobs unchanged."""
    value = suiteDefinition(rig)
    entered, stopped = Event(), Event()
    control = launcher.SuiteControl()

    def blocked():
        """Pause the second unit's first provider stream until its exact stop callback fires."""
        yield stream('later partial')[0]
        entered.set()
        assert stopped.wait(5)
        raise OSError('Synthetic cancelled read')
    rig[0]['streams'][2] = blocked()
    rig[0]['onStop'] = stopped.set
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executeSuite, rig, value, control=control)
        assert entered.wait(5)
        control.cancel()
        result, _ = future.result(timeout=10)
    units = result['suite']['units']
    assert [unit['status'] for unit in units] == ['succeeded', 'cancelled', 'not-started', 'not-started']
    assert units[0]['jobOutcome'] == 'Succeeded' and units[1]['jobOutcome'] == 'Cancelled'
    assert units[1]['run']['turns'][0]['partial']['answer'] == 'later partial'
    assert units[1]['run']['turns'][0]['cleanup']['quiescence'] == 'unknown'
    assert units[1]['run']['reservation']['close'] == 'queued'
    assert rig[0]['calls'] == 3 and result['suite']['status'] == 'cancelled'


@pytest.mark.parametrize('boundary', ['aggregate-write', 'unit-evidence'])
def test_ex03_suite_interrupt_preserves_unit_outcomes_and_owned_teardown(rig, monkeypatch, boundary):
    """R1 interruption protection applies to aggregate persistence and between-unit evidence."""
    value = suiteDefinition(rig)
    interrupt = KeyboardInterrupt('private suite interruption')
    if boundary == 'aggregate-write':
        def persist(*args):
            """Interrupt only the final aggregate first write after all actual Jobs complete."""
            raise interrupt
        monkeypatch.setattr(launcher, 'persist', persist)
    else:
        original = launcher.loadAdapter().LlamaCppComposition.configurationEvidence
        captured = []

        def evidence(self, *args):
            """Interrupt evidence for the second completed unit without changing provider execution."""
            captured.append(True)
            if len(captured) == 2:
                raise interrupt
            return original(self, *args)
        monkeypatch.setattr(launcher.loadAdapter().LlamaCppComposition, 'configurationEvidence', evidence)
    with pytest.raises(KeyboardInterrupt) as caught:
        executeSuite(rig, value)
    assert caught.value is interrupt
    result = caught.value.experimentExecution.value()
    units = result['suite']['units']
    assert all(unit['status'] == 'succeeded' for unit in units[:2])
    assert all(unit['jobOutcome'] == 'Succeeded' for unit in units[:2])
    assert result['hostCleanup'] == 'completed' and all(process.returncode == 0 for process in rig[0]['processes'])
    if boundary == 'aggregate-write':
        assert result['suite']['status'] == 'succeeded' and rig[0]['calls'] == 8
    else:
        assert result['suite']['status'] == 'failed' and rig[0]['calls'] == 4
        assert all(unit['status'] == 'not-started' for unit in units[2:])
    assert launcher.exitCode(result) == 1


def test_ex03_cli_aggregate_counts_and_explicit_private_inspection(rig, tmp_path, capsys):
    """The versioned terminal suite surface emits counts without raw unit content."""
    cli = importlib.import_module('first-party.applications.experimentRunner.run')
    value = suiteDefinition(rig)
    definitionFile, hostFile = tmp_path / 'suite.json', tmp_path / 'host.json'
    definitionFile.write_bytes(schema.canonical(value))
    hostFile.write_bytes(schema.canonical(rig[1]))
    args = ['--definition', str(definitionFile), '--host-config', str(hostFile)]
    assert cli.main(['validate', *args]) == 0 and rig[0]['processes'] == []
    capsys.readouterr()
    assert cli.main(['run', *args, '--results', str(rig[2])]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['status'] == 'succeeded' and summary['unitsSucceeded'] == 4 and summary['turnsSucceeded'] == 8
    assert 'answer-' not in json.dumps(summary) and 'separate-' not in json.dumps(summary)
    assert cli.main(['inspect', '--result', summary['path']]) == 0
    assert 'answer-7' in capsys.readouterr().out


@pytest.mark.parametrize('ceiling', ['configurations', 'repetitions', 'executions', 'turn-calls'])
def test_ex03_schema_product_ceilings_are_independent_of_host_caps(rig, ceiling):
    """The definition cannot exceed schema expansion maxima even under maximum valid host caps."""
    value = suiteDefinition(rig)
    if ceiling == 'repetitions':
        value['repetitions'] = 11
    else:
        count = {'configurations': 9, 'executions': 4, 'turn-calls': 8}[ceiling]
        config = value['configurations'][0]
        value['configurations'] = [dict(config, name=str(index)) for index in range(count)]
        value['repetitions'] = 9 if ceiling == 'executions' else 3 if ceiling == 'turn-calls' else 1
        if ceiling == 'turn-calls':
            rig[1]['limits']['maxTurns'] = 16
            turn = value['conversation']['turns'][0]
            value['conversation']['turns'] = [dict(turn, id=str(index)) for index in range(12)]
    with pytest.raises(schema.ValidationError):
        launcher.prepare(value, rig[1])
    assert rig[0]['processes'] == [] and rig[0]['requests'] == []


def test_ex03_unused_catalog_selector_is_not_actual_authority(rig, tmp_path):
    """A configured but unselected profile cannot be resolved by the actual Application owner."""
    from backend.llm.errors import LlmExecutionOwnershipError
    value = suiteDefinition(rig, differentStartup=False)
    host, runtime, _, _ = runtimeFor(launcher.prepare(value, rig[1]), tmp_path)

    def resolve(ctx, payload):
        """Attempt only public profile resolution for an ungranted catalog selector."""
        return ctx.llm.profile(providerName='llama.cpp', model='Q3')
    publishTestCapability(runtime, 'test.unused@1', resolve)
    try:
        with pytest.raises(LlmExecutionOwnershipError):
            runtime.invokeCapability('test.unused@1')
        assert rig[0]['calls'] == 0 and rig[0]['processes'] == []
    finally:
        host.stop()


def test_ex03_close_failure_before_queue_does_not_continue(rig, monkeypatch):
    """An unqueued reservation-close failure stops the suite and delegates only normal owner teardown."""
    from backend.llm.executionSessions import LlmConversationSession
    value = suiteDefinition(rig)

    def close(self):
        """Fail before queueing release; never assert that this reservation was released."""
        raise RuntimeError('Synthetic unqueued close failure')
    monkeypatch.setattr(LlmConversationSession, 'close', close)
    result, _ = executeSuite(rig, value)
    first = result['suite']['units'][0]
    assert first['jobOutcome'] == 'Failed' and first['run']['reservation']['close'] == 'failed'
    assert first['run']['reservation']['releaseReceipt'] == 'unavailable'
    assert all(unit['status'] == 'not-started' for unit in result['suite']['units'][1:])
    assert rig[0]['calls'] == 2 and len(rig[0]['processes']) == 1
    assert result['hostCleanup'] == 'completed' and rig[0]['processes'][0].returncode == 0


def test_ex03_private_configuration_evidence_has_a_recorded_size_limit(rig, monkeypatch):
    """Oversized explicit private evidence is unavailable rather than defeating result preflight bounds."""
    value = suiteDefinition(rig)

    def oversized(*args):
        """Return a deliberately oversized JSON-only private composition snapshot."""
        return {'private': 'x' * 262145}
    monkeypatch.setattr(launcher.loadAdapter().LlamaCppComposition, 'configurationEvidence', oversized)
    result, _ = executeSuite(rig, value)
    assert result['suite']['status'] == 'succeeded'
    assert all(unit['configurationEvidence'] == {'status': 'unavailable-over-limit'} for unit in result['suite']['units'])


def test_ex03_checked_in_templates_validate_without_probing_assets(rig):
    """The exact documented examples validate with explicit local substitutions and zero launches."""
    host = launcher.readJson(launcher.APP / 'host.example.json')
    host['llamaCpp']['executable'] = rig[1]['llamaCpp']['executable']
    for model in host['llamaCpp']['models'].values():
        model['modelPath'] = rig[1]['llamaCpp']['models']['Q']['modelPath']
    for filename in ('example.definition.json', 'example.suite.definition.json'):
        definitionValue = launcher.readJson(launcher.APP / filename)
        prepared = launcher.prepare(definitionValue, host)
        assert prepared[0].value()['schemaId'] == 'actant.experiment.definition@2'
    assert rig[0]['processes'] == [] and rig[0]['requests'] == []


RAW_SIGINT_REPRO = '''import importlib, signal, sys
from pathlib import Path
snapshot = Path(sys.argv[1])
original = Path(sys.argv[2])
sys.path = [str(snapshot)] + [p for p in sys.path if p and Path(p).resolve() != original]
launcher = importlib.import_module('first-party.applications.experimentRunner.launcher')
from backend.orchestration import Job
control = launcher.SuiteControl()
signal.signal(signal.SIGINT, lambda signum, frame: control.cancel())
originalCheck = control._cancelled.is_set
once = [False]
def check():
    """Dispatch once at a real locked flag check, simulating normal signal timing."""
    if not once[0]:
        once[0] = True
        print('dispatching SIGINT while begin owns controller lock', flush=True)
        signal.raise_signal(signal.SIGINT)
    return originalCheck()
control._cancelled.is_set = check
control.begin(Job.new())
print('controller returned', flush=True)
'''


def test_review_ex03_sigint_during_begin_completes_in_bounded_child():
    """Run Integration's unchanged model-free reproduction under an exact-child deadline."""
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-X', 'frozen_modules=off', '-c', RAW_SIGINT_REPRO,
                             str(launcher.ROOT), str(launcher.ROOT)], capture_output=True, text=True, timeout=4,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr
    assert 'dispatching SIGINT while begin owns controller lock' in result.stdout
    assert 'controller returned' in result.stdout


SIGNAL_HANDOFF_CHILD = '''import importlib, inspect, signal, sys, time
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
launcher = importlib.import_module('first-party.applications.experimentRunner.launcher')
from backend.orchestration import Job
assert Path(launcher.__file__).resolve().is_relative_to(root)
mode = sys.argv[2]
control = launcher.SuiteControl()
job = Job.new()
completed = Job.new()
completed.start()
completed.succeed()
if mode.startswith('end'):
    assert control.begin(completed)
elif mode == 'active-event-lock':
    assert control.begin(job)
    job.start()
relay = launcher._SignalCancellationRelay(control)
relay.start()
previous = signal.signal(signal.SIGINT, lambda signum, frame: control.requestSignalCancellation())
beginLines, beginStart = inspect.getsourcelines(launcher.SuiteControl.begin)
publish = next(beginStart + i for i, line in enumerate(beginLines) if 'self._job = job' in line)
postcheck = max(beginStart + i for i, line in enumerate(beginLines) if 'if self.requested:' in line)
lastReturn = next(beginStart + i for i, line in enumerate(beginLines) if 'return True' in line)
endLines, endStart = inspect.getsourcelines(launcher.SuiteControl.end)
clear = next(endStart + i for i, line in enumerate(endLines) if 'self._job = None' in line)
fired = [False]
def trace(frame, event, arg):
    """Dispatch once at an exact production handoff or a held Job Event condition lock."""
    if fired[0]:
        return trace
    code = frame.f_code
    trigger = (event == 'line' and code is launcher.SuiteControl.begin.__code__ and
               frame.f_lineno == {'begin-before-publish': publish, 'begin-after-publish': postcheck,
                                 'begin-after-check': lastReturn}.get(mode))
    trigger |= (mode == 'end-before-clear' and event == 'line' and
                code is launcher.SuiteControl.end.__code__ and frame.f_lineno == clear)
    trigger |= (mode == 'end-after-clear' and event == 'return' and code is launcher.SuiteControl.end.__code__)
    if mode == 'active-event-lock' and event == 'line' and code.co_name == 'set' and frame.f_locals.get('self') is job.cancellationSignal._event:
        trigger |= job.cancellationSignal._event._cond._is_owned()
    if trigger:
        fired[0] = True
        signal.raise_signal(signal.SIGINT)
    return trace
try:
    sys.settrace(trace)
    if mode.startswith('begin'):
        admitted = control.begin(job)
    elif mode.startswith('end'):
        control.end(completed)
    else:
        control.cancel()
    sys.settrace(None)
    assert fired[0] and control.requested
    if mode in ('begin-before-publish', 'begin-after-publish'):
        assert admitted is False and job.state.value == 'Cancelled'
    if mode == 'begin-after-check':
        deadline = time.monotonic() + 1
        while not job.cancellationRequested and time.monotonic() < deadline:
            time.sleep(0.005)
        assert job.cancellationRequested
        control.end(job)
    if mode == 'active-event-lock':
        assert job.cancellationRequested and job.state.value == 'Running'
        control.end(job)
    assert completed.state.value == 'Succeeded'
    assert not control.begin(Job.new())
finally:
    sys.settrace(None)
    signal.signal(signal.SIGINT, previous)
    relay.close()
assert not relay._thread.is_alive()
print('handoff returned without late admission', flush=True)
'''


@pytest.mark.parametrize('boundary', ['begin-before-publish', 'begin-after-publish', 'begin-after-check',
                                    'end-before-clear', 'end-after-clear', 'active-event-lock'])
def test_ex03_r1_actual_signals_at_handoff_have_bounded_completion(boundary):
    """Actual SIGINT can interrupt locked handoff/Event state without self-deadlock or lost flags."""
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-X', 'frozen_modules=off', '-c', SIGNAL_HANDOFF_CHILD,
                             str(launcher.ROOT), boundary], capture_output=True, text=True, timeout=4,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr
    assert 'handoff returned without late admission' in result.stdout


@pytest.mark.parametrize('handoff', ['begin', 'end'])
def test_ex03_r1_signal_at_later_handoff_prevents_next_job_and_transition(rig, monkeypatch, handoff):
    """Installed signal handling stops at a later gap, preserves prior Job truth and reaps its child."""
    import inspect
    import signal
    import sys
    from backend.context.codeEntryContext import _LlmFacade
    value = suiteDefinition(rig)
    control = launcher.SuiteControl()
    transitions, begun, seenJobs = [], [], []
    originalBegin, originalEnd = control.begin, control.end
    originalTransition = _LlmFacade.transitionProfile

    def transition(self, profile):
        """Count actual transition attempts, independently of compatible process reuse."""
        transitions.append(profile)
        return originalTransition(self, profile)

    def atBoundary(method, job, target):
        """Raise one actual SIGINT while the production handoff frame owns its lane."""
        lines, start = inspect.getsourcelines(method)
        lineNumber = next(start + index for index, line in enumerate(lines) if target in line)
        previous = sys.gettrace()
        once = []

        def trace(frame, event, arg):
            """Inject only on the selected actual handoff line; no controller behavior is replaced."""
            if not once and event == 'line' and frame.f_code is method.__func__.__code__ and frame.f_lineno == lineNumber:
                once.append(True)
                signal.raise_signal(signal.SIGINT)
            return trace
        try:
            sys.settrace(trace)
            return method(job)
        finally:
            sys.settrace(previous)
            assert once

    def begin(job):
        """Inject begin cancellation before publication of the second unit's pending Job."""
        begun.append(job)
        if handoff == 'begin' and len(begun) == 2:
            return atBoundary(originalBegin, job, 'self._job = job')
        return originalBegin(job)

    def end(job):
        """Inject end cancellation while detaching the first actual terminal Job."""
        seenJobs.append(job)
        if handoff == 'end' and len(seenJobs) == 1:
            return atBoundary(originalEnd, job, 'self._job = None')
        return originalEnd(job)
    monkeypatch.setattr(control, 'begin', begin)
    monkeypatch.setattr(control, 'end', end)
    monkeypatch.setattr(_LlmFacade, 'transitionProfile', transition)
    before = signal.getsignal(signal.SIGINT)
    snapshot, _ = launcher.execute(launcher.prepare(value, rig[1]), rig[2], control=control)
    result = snapshot.value()
    assert signal.getsignal(signal.SIGINT) is before
    assert result['suite']['status'] == 'cancelled'
    assert [unit['status'] for unit in result['suite']['units']] == ['succeeded', 'not-started', 'not-started', 'not-started']
    assert result['suite']['units'][0]['jobOutcome'] == seenJobs[0].state.value == 'Succeeded'
    assert all(unit['jobOutcome'] is None for unit in result['suite']['units'][1:])
    assert len(transitions) == 1 and rig[0]['calls'] == 2
    assert result['hostCleanup'] == 'completed' and len(rig[0]['processes']) == 1 and rig[0]['processes'][0].returncode == 0


def test_ex03_r1_actual_sigint_cancels_exact_active_job_off_stack(rig):
    """The installed leaf handler lets the relay stop the actual retained Job during a blocked stream."""
    import signal
    from threading import get_ident
    value = suiteDefinition(rig)
    job, unrelated = Job.new(), Job.new()
    unrelated.start()
    unrelated.succeed()
    stopped, deliveries = Event(), []
    callerThread = get_ident()

    def delivered():
        """Count cancellation of this Job, not a newly created or previously completed Job."""
        deliveries.append(get_ident())
    job.cancellationSignal.registerCallback(delivered)

    def blocked():
        """Dispatch actual SIGINT, then require off-stack cancellation before releasing fake IO."""
        yield stream('signal partial')[0]
        signal.raise_signal(signal.SIGINT)
        assert stopped.wait(3), 'active Job cancellation was not delivered'
        raise OSError('Synthetic stopped stream')
    rig[0]['streams'][0] = blocked()
    rig[0]['onStop'] = stopped.set
    before = signal.getsignal(signal.SIGINT)
    snapshot, _ = launcher.execute(launcher.prepare(value, rig[1]), rig[2], job=job)
    result = snapshot.value()
    assert signal.getsignal(signal.SIGINT) is before
    assert job.state.value == 'Cancelled' and unrelated.state.value == 'Succeeded'
    assert len(deliveries) == 1 and deliveries[0] != callerThread
    assert [unit['status'] for unit in result['suite']['units']] == ['cancelled', 'not-started', 'not-started', 'not-started']
    assert result['suite']['units'][0]['run']['turns'][0]['partial']['answer'] == 'signal partial'
    assert result['suite']['units'][0]['run']['turns'][0]['cleanup']['quiescence'] == 'unknown'
    assert result['hostCleanup'] == 'failed-or-unknown' and rig[0]['calls'] == 1


def test_ex03_r1_relay_failure_is_separate_from_successful_jobs(rig, monkeypatch):
    """A separately failed relay close cannot relabel successful Jobs or skip owned host teardown."""
    value = suiteDefinition(rig)
    original = launcher._SignalCancellationRelay.close

    def close(self):
        """Join the actual relay, then inject only its independent cleanup-report failure."""
        original(self)
        raise RuntimeError('Synthetic relay close failure')
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'close', close)
    snapshot, _ = launcher.execute(launcher.prepare(value, rig[1]), rig[2])
    result = snapshot.value()
    assert result['suite']['status'] == 'succeeded'
    assert all(unit['jobOutcome'] == 'Succeeded' for unit in result['suite']['units'])
    assert result['launcherFailure'] == {'category': 'launcher-failed', 'stage': 'signal-cleanup'}
    assert result['hostCleanup'] == 'completed' and launcher.exitCode(result) == 1


def test_ex03_r1_interrupted_relay_start_remains_owned_and_preserves_first_exit(rig, monkeypatch):
    """The relay is cleanup-rooted before thread startup can be interrupted."""
    original = launcher._SignalCancellationRelay.start
    started = []
    interruption = KeyboardInterrupt()

    def start(self):
        """Start the actual inert relay, then inject an interruption before handler installation."""
        original(self)
        started.append(self)
        raise interruption
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'start', start)
    with pytest.raises(KeyboardInterrupt) as caught:
        launcher.execute(launcher.prepare(definition(), rig[1]), rig[2])
    assert caught.value is interruption
    assert len(started) == 1 and not started[0]._thread.is_alive()
    result = caught.value.experimentExecution.value()
    assert result['hostCleanup'] == 'completed' and rig[0]['processes'] == []
    assert all(unit['jobOutcome'] is None for unit in result['suite']['units'])


def test_review_ex03_first_start_interrupt_survives_relay_close_failure(rig, monkeypatch):
    """Ordinary relay cleanup failure must not swallow the primary startup interrupt."""
    originalStart = launcher._SignalCancellationRelay.start
    originalClose = launcher._SignalCancellationRelay.close
    first = KeyboardInterrupt('primary-start-interrupt')
    relays = []
    def start(self):
        """Start the exact owned helper, then simulate Ctrl+C before handler installation."""
        originalStart(self)
        relays.append(self)
        raise first
    def close(self):
        """Join the helper normally, then report an independent ordinary cleanup failure."""
        originalClose(self)
        raise RuntimeError('secondary-relay-close-failure')
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'start', start)
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'close', close)
    with pytest.raises(KeyboardInterrupt) as caught:
        launcher.execute(launcher.prepare(definition(), rig[1]), rig[2])
    assert caught.value is first
    assert relays and not relays[0]._thread.is_alive()
    assert caught.value.experimentExecution.value()['hostCleanup'] == 'completed'


@pytest.mark.parametrize('kind', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('site,cleanup', [('relay-start', 'relay-close'),
    ('handler-install-before', 'handler-restoration'), ('handler-install-after', 'relay-close'),
    ('handler-install-after', 'both'), ('loop-admission', 'handler-restoration'), ('loop-admission', 'both')])
def test_ex03_r2_primary_exit_survives_paired_signal_cleanup_faults(rig, monkeypatch, kind, site, cleanup):
    """Capture primary startup/setup/loop exits before independently failing restore and relay close."""
    import signal
    value = suiteDefinition(rig)
    primary = kind('private primary exit')
    originalStart, originalClose = launcher._SignalCancellationRelay.start, launcher._SignalCancellationRelay.close
    nativeSignal = signal.signal
    before = signal.getsignal(signal.SIGINT)
    starts, closes, signalCalls, admissions = [], [], [], []
    control = launcher.SuiteControl()
    originalBegin = control.begin

    def start(self):
        """Root and start the actual helper before injecting the selected primary exit."""
        originalStart(self)
        starts.append(self)
        if site == 'relay-start':
            raise primary

    def close(self):
        """Always join the actual helper, then independently fail its cleanup report if selected."""
        originalClose(self)
        closes.append(self)
        if cleanup in ('relay-close', 'both'):
            raise RuntimeError('private secondary relay failure')

    def setSignal(signum, handler):
        """Exercise real installation/restoration, including an interrupt after installation took effect."""
        signalCalls.append(handler)
        if len(signalCalls) == 1 and site == 'handler-install-before':
            raise primary
        result = nativeSignal(signum, handler)
        if len(signalCalls) == 1 and site == 'handler-install-after':
            raise primary
        if len(signalCalls) == 2 and cleanup in ('handler-restoration', 'both'):
            raise RuntimeError('private secondary restoration failure')
        return result

    def begin(job):
        """Leave the first actual Job untouched, then interrupt the next admission before any transition."""
        admissions.append(job)
        if len(admissions) == 2 and site == 'loop-admission':
            raise primary
        return originalBegin(job)

    monkeypatch.setattr(launcher._SignalCancellationRelay, 'start', start)
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'close', close)
    monkeypatch.setattr(signal, 'signal', setSignal)
    monkeypatch.setattr(control, 'begin', begin)
    try:
        with pytest.raises(kind) as caught:
            launcher.execute(launcher.prepare(value, rig[1]), rig[2], control=control)
        assert caught.value is primary
        result = caught.value.experimentExecution.value()
        assert result['schemaId'] == 'actant.experiment.execution@3'
        expectedStage = 'relay-start' if site == 'relay-start' else 'unit-admission' if site == 'loop-admission' else 'signal-handler-installation'
        assert result['launcherFailure'] == {'category': 'launcher-interrupted', 'stage': expectedStage}
        expected = (['handler-restoration', 'relay-close'] if cleanup == 'both' else [cleanup])
        assert [failure['operation'] for failure in result['signalCleanupFailures']] == expected
        assert all(failure['category'] == 'failed' for failure in result['signalCleanupFailures'])
        assert result['hostCleanup'] == 'completed' and launcher.exitCode(result) == 1
        assert starts == closes and len(starts) == 1 and not starts[0]._thread.is_alive()
        assert signal.getsignal(signal.SIGINT) is before
        assert 'private primary exit' not in json.dumps(result) and 'private secondary' not in json.dumps(result)
        if site == 'loop-admission':
            assert result['suite']['units'][0]['jobOutcome'] == 'Succeeded'
            assert [unit['status'] for unit in result['suite']['units']] == ['succeeded', 'not-started', 'not-started', 'not-started']
            assert rig[0]['calls'] == 2 and rig[0]['processes'][0].returncode == 0
        else:
            assert all(unit['jobOutcome'] is None for unit in result['suite']['units'])
            assert rig[0]['processes'] == []
        persisted = list(rig[2].glob('*.json'))
        assert len(persisted) == 1 and json.loads(persisted[0].read_text(encoding='utf-8')) == result
        result['signalCleanupFailures'].clear()
        assert caught.value.experimentExecution.value()['signalCleanupFailures']
    finally:
        nativeSignal(signal.SIGINT, before)


def test_ex03_r2_restoration_interrupt_precedes_relay_failure_after_completed_jobs(rig, monkeypatch):
    """First interruption during restoration remains primary while all completed Job outcomes survive."""
    import signal
    primary = KeyboardInterrupt('private restoration interrupt')
    nativeSignal = signal.signal
    before = signal.getsignal(signal.SIGINT)
    originalClose = launcher._SignalCancellationRelay.close
    calls, closed = [], []

    def setSignal(signum, handler):
        """Restore the actual previous handler, then interrupt the restoration result."""
        calls.append(handler)
        result = nativeSignal(signum, handler)
        if len(calls) == 2:
            raise primary
        return result

    def close(self):
        """Join normally before injecting the independent secondary ordinary error."""
        originalClose(self)
        closed.append(self)
        raise RuntimeError('private secondary close error')
    monkeypatch.setattr(signal, 'signal', setSignal)
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'close', close)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            launcher.execute(launcher.prepare(suiteDefinition(rig), rig[1]), rig[2])
        assert caught.value is primary
        result = caught.value.experimentExecution.value()
        assert result['signalCleanupFailures'] == [
            {'category': 'interrupted', 'operation': 'handler-restoration'},
            {'category': 'failed', 'operation': 'relay-close'}]
        assert result['suite']['status'] == 'succeeded'
        assert all(unit['jobOutcome'] == 'Succeeded' for unit in result['suite']['units'])
        assert result['hostCleanup'] == 'completed' and launcher.exitCode(result) == 1
        assert closed and not closed[0]._thread.is_alive()
        assert signal.getsignal(signal.SIGINT) is before
    finally:
        nativeSignal(signal.SIGINT, before)


@pytest.mark.parametrize('site', ['relay-start', 'loop-admission'])
def test_ex03_r2_ordinary_primary_failure_keeps_its_phase_across_cleanup_errors(rig, monkeypatch, site):
    """Consumed ordinary launcher failures retain their original phase and separate secondary diagnostics."""
    import signal
    nativeSignal = signal.signal
    before = signal.getsignal(signal.SIGINT)
    originalStart, originalClose = launcher._SignalCancellationRelay.start, launcher._SignalCancellationRelay.close
    control = launcher.SuiteControl()
    begin = control.begin
    calls, jobs, joined = [], [], []

    def start(self):
        """Start the actual helper, then fail normally before handler installation if selected."""
        originalStart(self)
        if site == 'relay-start':
            raise RuntimeError('private primary startup failure')

    def admit(job):
        """Complete one unit before injecting an ordinary next-admission failure."""
        jobs.append(job)
        if len(jobs) == 2:
            raise RuntimeError('private primary admission failure')
        return begin(job)

    def setSignal(signum, handler):
        """Exercise real restoration before the secondary reporting fault."""
        calls.append(handler)
        result = nativeSignal(signum, handler)
        if len(calls) == 2:
            raise RuntimeError('private secondary restore failure')
        return result

    def close(self):
        """Always join, then fail separately without replacing the primary launcher phase."""
        originalClose(self)
        joined.append(self)
        raise RuntimeError('private secondary close failure')
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'start', start)
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'close', close)
    monkeypatch.setattr(control, 'begin', admit)
    monkeypatch.setattr(signal, 'signal', setSignal)
    try:
        snapshot, _ = launcher.execute(launcher.prepare(suiteDefinition(rig), rig[1]), rig[2], control=control)
        result = snapshot.value()
        assert result['launcherFailure'] == {'category': 'launcher-failed', 'stage': site if site == 'relay-start' else 'unit-admission'}
        expected = ['relay-close'] if site == 'relay-start' else ['handler-restoration', 'relay-close']
        assert [item['operation'] for item in result['signalCleanupFailures']] == expected
        assert result['suite']['status'] == 'failed' and result['hostCleanup'] == 'completed'
        assert launcher.exitCode(result) == 1 and joined and not joined[0]._thread.is_alive()
        assert signal.getsignal(signal.SIGINT) is before
        assert 'private primary' not in json.dumps(result)
    finally:
        nativeSignal(signal.SIGINT, before)


def test_ex03_r2_first_interrupt_beats_secondary_cleanup_interrupt(rig, monkeypatch):
    """Secondary non-Exception cleanup exit cannot replace the exact previously captured interrupt."""
    first, secondary = KeyboardInterrupt(), SystemExit(17)
    originalStart, originalClose = launcher._SignalCancellationRelay.start, launcher._SignalCancellationRelay.close

    def start(self):
        """Start normally and raise the primary exit before signal-handler installation."""
        originalStart(self)
        raise first

    def close(self):
        """Join normally and then raise a distinct secondary non-Exception exit."""
        originalClose(self)
        raise secondary
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'start', start)
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'close', close)
    with pytest.raises(KeyboardInterrupt) as caught:
        launcher.execute(launcher.prepare(definition(), rig[1]), rig[2])
    assert caught.value is first
    result = caught.value.experimentExecution.value()
    assert result['signalCleanupFailures'] == [{'category': 'interrupted', 'operation': 'relay-close'}]
    assert result['hostCleanup'] == 'completed'


@pytest.mark.parametrize('cancelled', [False, True])
def test_ex03_r2_cleanup_error_keeps_primary_job_and_unknown_outcomes(rig, monkeypatch, cancelled):
    """Separate relay cleanup errors cannot repair UNKNOWN or replace failed/cancelled unit outcomes."""
    value = suiteDefinition(rig)
    control = launcher.SuiteControl()
    originalClose = launcher._SignalCancellationRelay.close
    if cancelled:
        def onCall(index):
            """Cancel the actual active Job after native-send admission in this synthetic case."""
            control.cancel()
        rig[0]['onCall'] = onCall
    else:
        rig[0]['streams'][1] = stream('incomplete', done=False)

    def close(self):
        """Join the actual relay before independently failing its cleanup report."""
        originalClose(self)
        raise RuntimeError('private secondary close error')
    monkeypatch.setattr(launcher._SignalCancellationRelay, 'close', close)
    snapshot, _ = launcher.execute(launcher.prepare(value, rig[1]), rig[2], control=control)
    result = snapshot.value()
    first = result['suite']['units'][0]
    assert first['jobOutcome'] == ('Cancelled' if cancelled else 'Failed')
    assert first['status'] == ('cancelled' if cancelled else 'failed')
    assert any(turn['cleanup']['quiescence'] == 'unknown' for turn in first['run']['turns'])
    assert result['hostCleanup'] == 'failed-or-unknown'
    assert result['signalCleanupFailures'] == [{'category': 'failed', 'operation': 'relay-close'}]
    assert all(unit['status'] == 'not-started' and unit['jobOutcome'] is None for unit in result['suite']['units'][1:])
    assert len(rig[0]['processes']) == 1 and rig[0]['processes'][0].returncode is None


def test_ex04_planning_required_checkpoint_cannot_rely_on_observer_raise(rig, tmp_path):
    """Demonstrate current best-effort observer semantics through an actual admitted Job/session."""
    host, runtime, _, _ = runtimeFor(launcher.prepare(definition(), rig[1]), tmp_path)

    def observe(event):
        """Represent a required checkpoint failure using only fixed synthetic diagnostic text."""
        raise OSError('synthetic-required-checkpoint-failed')

    def infer(ctx, payload):
        """Use public managed session/stream APIs; no archive implementation or provider patch."""
        profile = ctx.llm.profile(providerName='llama.cpp', model='Q')
        ctx.llm.transitionProfile(profile)
        session = ctx.llm.reserveConversation(profile)
        try:
            return ctx.llm.run(providerName='llama.cpp', model='Q',
                query=entry.LlmMessages((entry.LlmMessage('user', 'synthetic planning probe'),)).toQuery(),
                providerOptions={'maxTokens': 24, 'timeoutSeconds': 30}, session=session,
                context=entry.FreshContext(required=False, retain=False), streamObserver=observe)
        finally:
            session.close()
    publishTestCapability(runtime, 'test.ex04-observer@1', infer)
    try:
        job = runtime.runJob('test.ex04-observer@1')
        assert job.state.value == 'Succeeded'
        assert job.result.rawText == ' amber '
        assert job.result.observerErrors and all('synthetic-required-checkpoint-failed' in error for error in job.result.observerErrors)
        assert job.result.executionEvidence.snapshot()['quiescence'] == 'confirmed'
        assert rig[0]['calls'] == 1
    finally:
        host.stop()


def test_ex04_planning_structured_snapshot_is_not_serialized_provider_body(rig):
    """Compare actual Request.data at the synthetic transport boundary to the recorded planned query."""
    import hashlib
    result, _ = execute(rig)
    turns = result['suite']['units'][0]['run']['turns']
    bodies = [body for url, body in rig[0]['requestBytes'] if url.endswith('/completions')]
    assert len(bodies) == 2 and result['suite']['status'] == 'succeeded'
    for turn, body in zip(turns, bodies, strict=True):
        planned = schema.canonical(turn['query']['messages'])
        actual = json.loads(body)
        assert actual['messages'] == turn['query']['messages']
        assert actual['id_slot'] == 0 and actual['cache_prompt'] is False and actual['stream'] is True
        assert 'id_slot' not in turn['query'] and 'cache_prompt' not in turn['query']
        assert actual['max_tokens'] == turn['requestedOptions']['maxTokens']
        assert planned != body and hashlib.sha256(planned).digest() != hashlib.sha256(body).digest()
