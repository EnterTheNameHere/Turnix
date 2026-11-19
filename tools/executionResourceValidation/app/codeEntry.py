# file: tools/executionResourceValidation/app/codeEntry.py ; version: 2
"""Small validation Application using only normal invocation facades and owned handles."""
from backend.llm.executionSessions import FreshContext
from backend.llm.llmTypes import LlmQuery
from backend.observation import ObservationLifetime, ObservationRequest

_state = {}


def transition(ctx, payload):
    """Explicitly request an already host-granted immutable idle profile."""
    return ctx.llm.transitionProfile(ctx.llm.profile(providerName='llama.cpp', model=payload))


def reserve(ctx, payload):
    """Retain the issued exclusive conversation, never an invocation Context."""
    if 'reservation' in _state:
        raise RuntimeError('Validation reservation already exists.')
    _state['reservation'] = ctx.llm.reserveConversation(ctx.llm.profile(providerName='llama.cpp', model=payload))


def release(ctx, payload):
    """Dispose only this application's explicit conversation reservation."""
    handle = _state.pop('reservation', None)
    if handle is not None:
        handle.close()


def run(ctx, payload):
    """Execute one bounded fresh request through admitted estimation/stream/cleanup."""
    return ctx.llm.run(providerName='llama.cpp', model=payload.get('model', 'A'),
        query=LlmQuery(formatId='text/plain', payload=payload['prompt']),
        providerOptions=payload['options'], streamObserver=payload.get('observer'),
        session=_state.get('reservation'), context=FreshContext(),
        observation=_state.get('observation') if payload.get('observe') else None)


def estimate(ctx, payload):
    """Exercise actual template/tokenize endpoints under generation-scoped admission."""
    return ctx.llm.estimateInputTokens(providerName='llama.cpp', model=payload['model'],
        query=LlmQuery(formatId='text/plain', payload=payload['prompt']), providerOptions=payload['options'])


def observe(ctx, payload):
    """Explicitly opt into bounded PM hardware/stream collection and same-app publication."""
    session = ctx.observations.request(ObservationRequest(sampleIntervalSeconds=0.1,
        historySamples=32, windowSeconds=0.1, historyWindows=64), lifetime=ObservationLifetime.APPLICATION)
    _state['observation'] = session
    grant = ctx.observations.export(session)
    _state['grant'] = grant
    publication = ctx.capabilities.call('observation.bridge.publish@1', {'grant': grant})
    return {'publication': publication, 'connection': ctx.capabilities.call('observation.bridge.connection@1')}


def revoke(ctx, payload):
    """Revoke read authority and release the exact explicitly owned observation source."""
    grant = _state.pop('grant', None)
    if grant is not None:
        grant.revoke()
    session = _state.pop('observation', None)
    if session is not None:
        session.release()


def snapshot(ctx, payload):
    """Return bounded disposable PM evidence without granting provider authority."""
    return _state['observation'].snapshot()


def onLoad(ctx):
    """Stage validation capabilities; no inference or collection occurs during activation."""
    _state.clear()
    for name, handler in [('transition', transition), ('reserve', reserve), ('release', release),
                          ('run', run), ('estimate', estimate), ('observe', observe), ('revoke', revoke), ('snapshot', snapshot)]:
        ctx.capabilities.register('er04.' + name + '@1', handler)
    return _state


def onUnload(ctx, state):
    """Release only validation-owned observation/reservation handles during teardown."""
    revoke(ctx, None)
    release(ctx, None)
