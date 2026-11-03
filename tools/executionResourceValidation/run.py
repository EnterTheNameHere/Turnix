# file: tools/executionResourceValidation/run.py ; version: 4
"""Explicit ER-04 local validation; never downloads models or bypasses managed admission."""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import socket
import subprocess
import sys
import time
import traceback
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from backend.llm.errors import LlmExecutionConflictError
from backend.orchestration import Job, JobState
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore
from backend.telemetry import SignalUnavailable
from tools.executionResourceValidation.preflight import inspect_assets, load_config

ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).with_name('app')
DRIVER = ROOT / 'first-party/llmDrivers/llamaCpp'


def plain(value):
    """Serialize private evidence without introspecting arbitrary live authority objects."""
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    if isinstance(value, bytes):
        return {'hex': value.hex()}
    if value is None or type(value) in (bool, int, float, str):
        return value
    return {'opaqueType': type(value).__name__}


def write_json(path, value):
    """Write private bounded evidence only within the caller-selected local run folder."""
    Path(path).write_text(json.dumps(plain(value), ensure_ascii=False, indent=2), encoding='utf-8')


def unavailable_signals(value, prefix=''):
    """Report missing nested sensor names separately from actual zero measurements."""
    if isinstance(value, SignalUnavailable):
        return [prefix]
    if dataclasses.is_dataclass(value):
        return [name for field in dataclasses.fields(value)
                for name in unavailable_signals(getattr(value, field.name), (prefix + '.' if prefix else '') + field.name)]
    return []


def transport_facts(records):
    """Project fixed wire/protocol facts without exporting prompts, raw IDs or responses."""
    calls = []
    for record in records:
        if record['endpoint'] != '/v1/chat/completions':
            continue
        request = json.loads(record['request'])
        identities, finishes, done = set(), [], False
        for line in ''.join(record['response']).splitlines():
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                done = True
                continue
            item = json.loads(payload)
            if type(item.get('id')) is str:
                identities.add(item['id'])
            for choice in item.get('choices', []):
                reason = choice.get('finish_reason')
                if reason is not None:
                    finishes.append(reason if reason in ('stop', 'length', 'tool_calls', 'content_filter', 'function_call') else 'other')
        effort = request.get('reasoning_effort')
        calls.append({'ownedSlotRequested': request.get('id_slot') == 0,
            'cacheReuseDisabled': request.get('cache_prompt') is False, 'nativeDone': done,
            'nativeIdConsistent': len(identities) == 1, 'finishReasons': finishes,
            'enableThinkingForwarded': request.get('chat_template_kwargs', {}).get('enable_thinking'),
            'reasoningEffortForwarded': effort if effort in ('none', 'low', 'medium', 'high') else None})
    return {'streamCalls': calls, 'postTerminalSlotReads': sum(record['endpoint'] == '/slots' for record in records),
            'templateCalls': sum(record['endpoint'] == '/apply-template' for record in records),
            'tokenizerCalls': sum(record['endpoint'] == '/tokenize' for record in records)}


def load_adapter():
    """Load the production trusted composer using the same pattern as the provider guide."""
    name = 'er04TrustedAdapter'
    spec = importlib.util.spec_from_file_location(name, DRIVER / 'executionAdapter.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def unused_port():
    """Choose an unoccupied IPv4 loopback port without taking over another listener."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


class ResponseEvidence:
    """Pass through actual transport bytes unchanged while retaining bounded private evidence."""

    def __init__(self, response, record):
        """Keep the exact response and its private request-correlated record."""
        self.response, self.record = response, record
        self.retained = 0

    def __enter__(self):
        """Enter the actual response context without replacing provider behavior."""
        self.response.__enter__()
        return self

    def __exit__(self, *args):
        """Delegate transport exit exactly once through the original response protocol."""
        return self.response.__exit__(*args)

    def capture(self, data):
        """Bound private capture at 128 KiB while returning original bytes untouched."""
        if type(data) is bytes:
            keep = data[:max(0, 131072 - self.retained)]
            self.retained += len(keep)
            self.record['response'].append(keep.decode('utf-8', errors='replace'))
            if len(keep) < len(data):
                self.record['truncated'] = True
        return data

    def read(self, *args):
        """Retain synchronous JSON responses without changing their validation input."""
        return self.capture(self.response.read(*args))

    def __iter__(self):
        """Retain native SSE/finish/usage evidence without changing event ordering."""
        for line in self.response:
            yield self.capture(line)

    def close(self):
        """Close only the actual transport; this supplies no quiescence assertion."""
        return self.response.close()

    def __getattr__(self, name):
        """Delegate ordinary response attributes such as status to the original object."""
        return getattr(self.response, name)


class Recorder:
    """Instrument actual OS/HTTP boundaries without modifying admission, bytes or outcomes."""

    def __init__(self, adapter, executable, base_url, folder):
        """Capture only this explicit executable/endpoint; never record environment secrets."""
        self.adapter, self.executable, self.base_url, self.folder = adapter, str(Path(executable).resolve()), base_url, folder
        self.requests, self.launches, self.processes = [], [], []

    def __enter__(self):
        """Install transparent boundary recording for this single-threaded harness owner."""
        self.popen = self.adapter._impl.subprocess.Popen
        self.urlopen = self.adapter.http.urlopen
        self.log = (self.folder / 'server.log').open('ab')

        def launch(args, **kwargs):
            """Redirect only the exact harness child log and preserve its actual Popen owner."""
            if str(Path(args[0]).resolve()) != self.executable or any(flag in args for flag in ('--version', '--help')):
                return self.popen(args, **kwargs)
            kwargs = dict(kwargs, stdout=self.log, stderr=self.log)
            process = self.popen(args, **kwargs)
            self.processes.append(process)
            self.launches.append({'pid': process.pid, 'argv': list(args), 'cwd': kwargs.get('cwd')})
            return process

        def open_response(request, **kwargs):
            """Record provider requests privately; never log authentication headers or bridge reads."""
            url = request if type(request) is str else request.full_url
            if not url.startswith(self.base_url + '/'):
                return self.urlopen(request, **kwargs)
            body = getattr(request, 'data', None)
            record = {'endpoint': url[len(self.base_url):], 'request': None if body is None else body.decode('utf-8'),
                      'response': [], 'truncated': False}
            if len(self.requests) >= 128:
                raise RuntimeError('Validation evidence request bound exceeded.')
            self.requests.append(record)
            response = self.urlopen(request, **kwargs)
            return ResponseEvidence(response, record)

        self.adapter._impl.subprocess.Popen = launch
        self.adapter.http.urlopen = open_response
        return self

    def __exit__(self, *args):
        """Restore original transport/process factories and persist private provenance."""
        self.adapter._impl.subprocess.Popen = self.popen
        self.adapter.http.urlopen = self.urlopen
        write_json(self.folder / 'transport.json', self.requests)
        write_json(self.folder / 'launches.json', self.launches)
        self.log.close()


class StreamGate:
    """Event-controlled consumer pause, holding a real admitted stream without timing races."""

    def __init__(self):
        """Use the first actual delivered event as the contention test boundary."""
        self.entered, self.release = Event(), Event()
        self.events = 0

    def observe(self, event):
        """Pause once; the finite guard detects a harness failure, not provider quiescence."""
        self.events += 1
        if not self.entered.is_set():
            self.entered.set()
            if not self.release.wait(30):
                raise RuntimeError('Validation stream gate was not released.')


class Validation:
    """Run actual two-Application scenarios through the accepted host and invocation facade."""

    def __init__(self, config, folder, recorder, adapter, viewer_origin=None):
        """Prepare a private approved catalog with CPU-only bounded startup profiles."""
        self.config, self.folder, self.recorder, self.adapter = config, folder, recorder, adapter
        self.checks, self.private = [], []
        model = {'modelPath': config['models']['nanbeige'], 'contextWindowTokens': config['contextTokens'],
                 'threads': config['threads'], 'threadsBatch': config['threads'], 'gpuLayers': 0,
                 'batchSize': 128, 'ubatchSize': 128, 'parallelSlots': 1, 'kvOffload': False,
                 'serverReasoning': 'off', 'specMethods': ['none']}
        self.launch_config = {'manageServer': True, 'executable': config['executable'], 'host': '127.0.0.1',
            'port': int(recorder.base_url.rsplit(':', 1)[1]), 'startupTimeoutSeconds': config['startupSeconds'],
            'defaultModel': 'A', 'capabilityCheck': 'required',
            'extraArgs': ['--slots', '--no-webui', '--no-op-offload'],
            'models': {'A': model, 'B': dict(model, threads=max(1, config['threads'] - 1)),
                       'Qoff': dict(model, modelPath=config['models']['qwen']),
                       'Qon': dict(model, modelPath=config['models']['qwen'], serverReasoning='on')}}
        if self.launch_config['models']['B'] == model:
            self.launch_config['models']['B']['batchSize'] = 64
        caller = adapter.EntrySelector.fromSource('er04.validation', '1.0.0', 'main', APP / 'codeEntry.py')
        provider = adapter.EntrySelector.fromSource('llmDrivers.llamaCpp', '0.3.0', 'driver', DRIVER / 'structuredCodeEntry.py')
        self.composer = adapter.LlamaCppComposition(domainId='er04-local',
            catalog=adapter.LlamaCppCatalog.fromConfig(self.launch_config), providerEntries=(provider,),
            grants=(adapter.LlamaCppGrant(caller, (None, 'A', 'B', 'Qoff', 'Qon'), True),))
        self.host = RuntimeHost(applicationStore=ApplicationStore(folder / 'saves'),
            packResolver=PackResolver(roots=(ROOT / 'first-party/llmDrivers', ROOT / 'first-party/observationBridge', APP)),
            providerComposers=(self.composer,))
        self.host.start()
        packs = ['llmDrivers.llamaCpp', 'er04.validation']
        if viewer_origin:
            packs.append('observation.bridge')
        runtime_config = {'llamaCpp': self.launch_config}
        if viewer_origin:
            runtime_config['observationBridge'] = {'allowedOrigins': [viewer_origin]}
        self.runtimes = [self.host.createApplication(appPackId='er04.validation',
            plan=ManualActivationPlan(packIds=tuple(packs)), config=runtime_config) for _ in range(2)]

    def check(self, name, condition):
        """Require structural evidence, never textual intelligence or reproducibility."""
        self.checks.append({'case': name, 'passed': bool(condition)})
        if not condition:
            raise RuntimeError('ER-04 assertion failed: ' + name)

    def call(self, index, operation, payload=None):
        """Invoke an actual capability with its own issued CodeEntry execution authority."""
        result = self.runtimes[index].invokeCapability('er04.' + operation + '@1', payload)
        if operation == 'transition':
            self.check('transition-ready', result.snapshot.state.value == 'ready')
        return result

    def infer(self, index=0, model='A', options=None, observer=None, observe=False, prompt=None):
        """Run bounded fresh inference and retain raw output only in private evidence."""
        request = {'model': model, 'prompt': self.config['prompt'] if prompt is None else prompt,
                   'options': {'temperature': 0.0, 'maxTokens': self.config['maxTokens'],
                               'timeoutSeconds': self.config['timeoutSeconds'], 'reasoningFormat': 'deepseek', **(options or {})},
                   'observer': observer, 'observe': observe}
        result = self.call(index, 'run', request)
        evidence = result.executionEvidence.snapshot()
        self.check('completion-confirmed', evidence['quiescence'] == 'confirmed')
        self.private.append({'model': model, 'result': result, 'evidence': evidence})
        return result

    def generation(self):
        """Read safe neutral snapshots, not an inferred profile label or mutable estimator."""
        snapshot = self.host.executionResources.snapshots[0]
        return None if snapshot.residency is None else snapshot.residency.generation

    def ordinary(self):
        """Prove real normal completion, compatible fresh calls and explicit startup transition."""
        self.check('lazy-activation', len(self.recorder.processes) == 0)
        self.call(0, 'transition', 'A')
        first, generation = self.recorder.processes[-1], self.generation()
        measured = self.call(0, 'estimate', {'model': 'A', 'prompt': self.config['prompt'],
            'options': {'reasoningFormat': 'deepseek', 'timeoutSeconds': self.config['timeoutSeconds']}})
        self.check('same-generation-template-tokenize', type(measured) is int and measured > 0
                   and generation == self.generation() and first.poll() is None)
        self.private.append({'estimatedInputTokens': measured})
        self.infer()
        self.infer(options={'temperature': 0.2})
        self.check('same-weight-fresh-no-reload', len(self.recorder.processes) == 1 and first.poll() is None and generation == self.generation())
        self.call(1, 'transition', 'B')
        self.check('idle-startup-transition', first.poll() is not None and self.generation() != generation)
        self.infer(1, 'B')
        self.call(0, 'transition', 'A')

    def refusal(self, model, operation='transition'):
        """Require a typed immediate denial through the peer's actual application facade."""
        try:
            if operation == 'run':
                self.infer(1, model)
            else:
                self.call(1, 'transition', model)
        except LlmExecutionConflictError as error:
            self.check('typed-' + operation + '-conflict', error.conflict.reason.value in ('busy', 'draining', 'transitioning'))
            return
        raise RuntimeError('A protected peer operation was not refused.')

    def safety(self):
        """Hold real active work and exclusive reservations; test ordinary owner lifetime."""
        first, generation = self.recorder.processes[-1], self.generation()
        gate = StreamGate()
        with ThreadPoolExecutor(max_workers=1) as pool:
            inference = pool.submit(self.infer, observer=gate.observe)
            try:
                if not gate.entered.wait(self.config['timeoutSeconds'] + 5):
                    inference.result(1)
                    raise RuntimeError('No real stream event reached the contention boundary.')
                self.refusal('B')
                self.check('active-stream-process-unchanged', first.poll() is None and generation == self.generation())
            finally:
                gate.release.set()
            inference.result(self.config['timeoutSeconds'] + 5)
        self.call(0, 'reserve', 'A')
        try:
            self.refusal('A', 'run')
            self.refusal('B')
        finally:
            self.call(0, 'release')
        self.call(1, 'transition', 'B')
        self.infer(1, 'B')
        self.host.closeApplicationRun(self.runtimes[1].applicationRun.applicationRunId)
        self.check('owner-unload-preserves-peer', self.recorder.processes[-1].poll() is None)
        self.call(0, 'transition', 'A')
        pending = Job.new()
        pending.requestCancellation()
        result = self.runtimes[0].runJob('er04.run@1', {}, job=pending)
        self.check('cancel-before-send', result.state is JobState.CANCELLED)

    def reasoning(self):
        """Compare explicit Qwen request/server controls without asserting model intelligence."""
        result = []
        for model, thinking, effort in [('Qoff', False, 'none'), ('Qon', True, 'low'), ('Qon', False, 'none'), ('Qon', True, 'high')]:
            self.call(0, 'transition', model)
            reply = self.infer(model=model, options={'chatTemplateKwargs': {'enable_thinking': thinking}, 'reasoningEffort': effort},
                               prompt=self.config['reasoningPrompt'])
            reasoning = reply.providerMetadata.get('llamaCppReasoning', {}).get('content', '')
            result.append({'profile': model, 'enableThinking': thinking, 'reasoningEffort': effort,
                           'serverAccepted': True, 'answerCharacters': len(reply.rawText),
                           'separateReasoningCharacters': len(reasoning)})
        return result

    def close(self):
        """Use accepted cleanup only; report unknown and retain exact owned objects on failure."""
        try:
            self.host.stop()
        except Exception as error:
            self.private.append({'cleanupErrorType': type(error).__name__, 'snapshots': self.host.executionResources.snapshots})
            return False
        return all(process.poll() is not None for process in self.recorder.processes)


def observe_browser(validation, viewer, config, folder):
    """Exercise actual PM/G-01/G-02 and revoke the source before explicit teardown."""
    validation.call(0, 'transition', 'Qoff')
    descriptor = validation.call(0, 'observe')
    validation.infer(model='Qoff', options={'chatTemplateKwargs': {'enable_thinking': False},
                                          'reasoningEffort': 'none'}, observe=True)
    observation = validation.call(0, 'snapshot')
    validation.check('pm-hardware-history', bool(observation.hardware) or observation.hardwareUnavailableReason is not None)
    validation.private.append({'observation': observation})
    request = validation.adapter.http.Request(descriptor['publication']['snapshotUrl'],
        headers={'Authorization': 'Bearer ' + descriptor['connection']['readCredential']})
    with validation.adapter.http.urlopen(request, timeout=5) as response:
        snapshot = json.loads(response.read())
    validation.private.append({'bridgeSnapshot': snapshot})
    if not config.get('playwright') or not config.get('browser'):
        validation.call(0, 'revoke')
        return {'status': 'blocked', 'reason': 'explicit-browser-paths-missing', 'bridgeVerified': True}
    process = subprocess.Popen(['node', str(Path(__file__).with_name('browser.cjs'))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        process.stdin.write(json.dumps({'viewer': viewer.origin, **descriptor, 'playwright': config['playwright'],
                                       'browser': config['browser'], 'output': str(folder)}) + '\n')
        process.stdin.flush()
        reader = ThreadPoolExecutor(max_workers=1)
        try:
            ready = reader.submit(process.stdout.readline)
            if ready.result(30).strip() != 'connected':
                raise RuntimeError('G-02 browser did not connect.')
        finally:
            reader.shutdown(wait=False, cancel_futures=True)
        validation.call(0, 'revoke')
        process.stdin.write('{}\n')
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=30)
        (folder / 'browser-diagnostics.txt').write_text(stderr, encoding='utf-8')
        validation.check('g02-revoked-and-private', process.returncode == 0 and 'revoked' in stdout)
    finally:
        validation.call(0, 'revoke')
        if process.poll() is None:
            process.terminate()  # Exact test-owned browser helper, never a provider recovery path.
            process.wait(timeout=10)
    missing = sorted({name for sample in observation.hardware for name in unavailable_signals(sample)})
    return {'status': 'verified', 'hardwareSamples': len(observation.hardware), 'unavailableGroups': missing,
            'hardwareUnavailable': observation.hardwareUnavailableReason is not None}


def execute(config, folder, phases):
    """Run explicitly selected phases and return separate public/private outcomes and ownership."""
    summary = {'schema': 'actant.er04.validation@1', 'phases': {}, 'limits': [
        'No physical retained-reuse or required freshness guarantee.', 'No universal thinking toggle or bitwise determinism.',
        'Abort quiescence may remain unknown; no forced process recovery.']}
    assets = inspect_assets(config)
    write_json(folder / 'assets.private.json', assets)
    write_json(folder / 'config.private.json', config)
    summary['phases']['04A'] = {'status': 'verified', 'buildPinned': True, 'modelsInspected': 2,
                               'executableSha256': assets['executable']['sha256']}
    if phases == 'preflight':
        return summary, None
    adapter = load_adapter()
    from tools.observationViewer.server import ViewerServer
    viewer = ViewerServer(0) if phases in ('all', 'observe') else None
    worker = None
    if viewer:
        worker = Thread(target=viewer.serve_forever, daemon=True)
        worker.start()
    validation = None
    with Recorder(adapter, config['executable'], 'http://127.0.0.1:' + str(unused_port()), folder) as recorder:
        try:
            validation = Validation(config, folder, recorder, adapter, None if viewer is None else viewer.origin)
            validation.ordinary()
            summary['phases']['04B'] = {'status': 'verified'}
            validation.safety()
            summary['phases']['04C'] = {'status': 'blocked', 'safeCasesVerified': True,
                'reason': 'active-abort-quiescence-and-safe-disposal-policy-unavailable',
                'remainingCases': ['active-cancellation', 'early-stream-close', 'transport-timeout']}
            if phases in ('all', 'observe'):
                summary['phases']['04D'] = observe_browser(validation, viewer, config, folder)
            if phases in ('all', 'reasoning'):
                summary['phases']['04E'] = {'status': 'verified', 'observations': validation.reasoning(),
                                          'meaning': 'forwarded-controls/server-acceptance/observed-lengths-only'}
        except Exception as error:
            summary['failure'] = {'type': type(error).__name__}
            (folder / 'failure.private.txt').write_text(traceback.format_exc(), encoding='utf-8')
        finally:
            if validation:
                summary['checks'] = validation.checks
                summary['cleanup'] = 'confirmed' if validation.close() else 'blocked'
                summary['wireEvidence'] = transport_facts(recorder.requests)
                write_json(folder / 'results.private.json', validation.private)
                write_json(folder / 'configuration.private.json', validation.composer.configurationEvidence(validation.host.executionResources))
            if viewer:
                viewer.shutdown()
                viewer.server_close()
                worker.join(timeout=5)
    for phase in ('04B', '04C', '04D', '04E'):
        summary['phases'].setdefault(phase, {'status': 'failed' if 'failure' in summary else 'blocked',
                                            'reason': 'earlier-phase-failed' if 'failure' in summary else 'not-selected'})
    return summary, validation


def main():
    """Require explicit local config; keep failed owned cleanup rooted rather than kill/restart."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--phase', choices=('preflight', 'core', 'observe', 'reasoning', 'all'), default='preflight')
    parser.add_argument('--output', type=Path, default=ROOT / 'python-embedded/er04-local')
    args = parser.parse_args()
    config = load_config(args.config)
    output = args.output.resolve()
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / 'python-embedded'):
        parser.error('Private evidence inside the repository must be under ignored python-embedded.')
    folder = output / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    summary, validation = execute(config, folder, args.phase)
    write_json(folder / 'summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    if summary.get('cleanup') == 'blocked' and validation and any(p.poll() is None for p in validation.recorder.processes):
        print('Pending provider ownership retained. Local evidence records exact owned PIDs; operator direction required.', flush=True)
        while any(process.poll() is None for process in validation.recorder.processes):
            Event().wait(1)  # Guard process lifetime only; never recovery or quiescence proof.
    return 1 if summary.get('failure') or summary.get('cleanup') == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
