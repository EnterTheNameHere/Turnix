# llama.cpp startup controls and configuration evidence

The manifest loads `structuredCodeEntry.py`; `codeEntry.py` supplies its driver.
Managed registration is a controlled-only facade: a trusted host composer installs
ER-01/02 bindings before application hooks/save/start. Both source entry paths
require the same admission. Loading stays lazy; initial loading and idle profile
switches require explicit authorized transitions. Final drained binding release
unloads the owned residency. There is no scheduler, automatic restart or recovery.
Startup controls are LC-01, request/reasoning evidence is LC-02, speculation is
LC-03, and controlled host composition/lifetime is ER-03 as described below.

## Controlled managed composition and migration (ER-03)

Managed JSON alone no longer authorizes a process. Construct
`executionAdapter.LlamaCppComposition` in trusted host code and pass it in
`RuntimeHost(providerComposers=(composer,))`. The generic host sees only the
composition protocol. `ApplicationRuntime` also accepts `providerComposers` for
standalone `ApplicationLifecycle.create/load` and direct PackLoader activation.
Without approved composition, managed activation fails **before** application
hooks, creation commit/save/start or public host registration with
`Managed llama.cpp requires trusted host composition.` External mode is unchanged.
Existing live old-style shared-driver leases reject migration; they are not stolen
or stopped. Consumer owners must migrate their host setup and initial transition.

Host setup, with an existing AppPack `example@1` whose `main` entry is `entry.py`:

```python
import importlib.util
import sys
from pathlib import Path
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore
from backend.packs.runtime import PackResolver, ManualActivationPlan

repo = Path.cwd()
app = Path("C:/actant/example-packs/example")
path = repo / "first-party/llmDrivers/llamaCpp/executionAdapter.py"
spec = importlib.util.spec_from_file_location("hostLlamaComposition", path)
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)
config = {
    "manageServer": True, "executable": "C:/llama/llama-server.exe",
    "defaultModel": "A", "models": {"A": {"modelPath": "C:/models/target.gguf"}},
}
caller = adapter.EntrySelector.fromSource("example", "1", "main", app / "entry.py")
provider = adapter.EntrySelector.fromSource("llmDrivers.llamaCpp", "0.3.0", "driver",
    repo / "first-party/llmDrivers/llamaCpp/structuredCodeEntry.py")
composer = adapter.LlamaCppComposition(domainId="primary-llama",
    catalog=adapter.LlamaCppCatalog.fromConfig(config), providerEntries=(provider,),
    grants=(adapter.LlamaCppGrant(caller, (None, "A"), True),))
host = RuntimeHost(applicationStore=ApplicationStore("C:/actant/example-saves"),
    packResolver=PackResolver(roots=(repo / "first-party", app.parent)),
    providerComposers=(composer,))
host.start()
try:
    runtime = host.createApplication(appPackId="example", config={"llamaCpp": config},
        plan=ManualActivationPlan(packIds=("llmDrivers.llamaCpp", "example")))
    runtime.invokeCapability("example.first@1")
    private = composer.configurationEvidence(host.executionResources, "A")
finally:
    host.stop()
```

The AppPack's capability uses the existing neutral facade:

```python
from backend.llm.llmTypes import LlmQuery
from backend.llm.executionSessions import FreshContext

def first(ctx, payload):
    """Explicitly materialize an approved idle profile before the first call."""
    profile = ctx.llm.profile(providerName="llama.cpp", model="A")
    ctx.llm.transitionProfile(profile)
    session = ctx.llm.reserveConversation(profile)
    try:
        return ctx.llm.run(providerName="llama.cpp", session=session,
            query=LlmQuery(formatId="text/plain", payload="Hello"),
            context=FreshContext()).rawText
    finally:
        session.close()

def onLoad(ctx):
    """Stage registration; lifecycle/onLoad Contexts cannot execute controlled LLMs."""
    ctx.capabilities.register("example.first@1", first)
```

A creation/load/run hook can call `ctx.capabilities.call("example.first@1")`
after composition. No LLM execution access is granted directly to that lifecycle
Context. Helper CodeEntries must be present in the initial plan with their own
exact `EntrySelector` and `LlamaCppGrant(..., allowTransition=False)` as appropriate.
Entry selection checks actual loaded Pack/version/entry/source SHA-256; it does
not certify transitive imports. Runtime JSON must match the approved catalog.
Bindings copy initial grants. Later entries/reactivated incarnations receive no
implicit grant or live extension. A full drained unload and trusted full-plan
reactivation issues a new provider Registration and new actual-owner grants; it
never revives the previous registration's authority. Unsafe activation inside a
Job or lifecycle callback rejects. Partial later-batch failure preserves older
bindings, and a revoked binding cannot satisfy the composition barrier.

Runtime lifecycle admission chooses invocation, activation and shutdown atomically.
A detached readiness check is not execution authority: live invocation tickets are
checked again at the actual serialized entry. Composer callbacks and deferred
cleanup run outside lanes, while issued tickets exclude conflicting activation.
Permanent runtime/host shutdown closes future admission before provider finalizers;
Pack-only unload keeps a reversible gate until its whole cleanup boundary succeeds.
Pending invocations, composition or cleanup report typed incomplete disposition.

Actual broker cleanup callbacks and resource disposal reserve mutually exclusive
ownership on the exact resource service before effects begin. This ownership
survives runtime removal from host tables. A live callback keeps its authorized
unload path available; concurrent shutdown reports pending before closing that
service or stopping its process. Callbacks run outside lanes. After the owner
unwinds, explicit retry can drain pending cleanup; unknown disposition remains
reported without repeating a failed process-stop attempt.

Changing sampling/request controls reuses loaded weights. Different startup flags,
context capacity, template defaults or auxiliary speculative weights are a new
residency, requiring an explicit authorized idle transition. Busy, exclusive or
unknown resources reject immediately; there is no hidden wait or preemption.
`profileId` is configuration evidence, not the ER-01 residency generation.

Stock adapter support is `supportsFresh=True`, `supportsRequiredFresh=False`,
`supportsRetention=False`. Best-effort fresh inference owns an explicit slot and
sends `cache_prompt=false`, reporting **attempted** logical freshness. It neither
resizes context nor reloads weights. Required fresh reset, `retain=True` and required
reuse cannot obtain a provider promise: requests reject before inference/provider
mutation, with no fake handle or silent replay. Physical retained-context adoption
remains unmet despite backend ER-01/02 handles/pins. No slot erase/save/restore,
all-cache reset, GPU-cold or determinism promise is implemented. Packs own their
authoritative conversation history and overflow policy.

Normal cleanup requires native terminal `finish_reason`, a consistent nonempty
completion ID, valid `[DONE]`, then a bounded **post-terminal** `/slots` observation
of the exact owned slot idle in the same live residency/process. Bare `[DONE]`,
local HTTP closure, EOF, cancellation, or an idle observation preceding a queued
request does not establish quiescence. Malformed/disabled slot observations and
ambiguous failures quarantine through ER-01; host close reports pending/unknown
without automatic kill/restart/recovery. Normal completion releases claims and
permits later explicit transitions. Configure the server's slots endpoint for
this policy; `--no-slots` cannot provide its normal-completion evidence.

Managed inference and slot acknowledgements now use private per-operation owned
HTTP transports. Stop marks cancellation and interrupts the exact raw socket;
short nonblocking socket waits check stop independently of Windows buffered-read
behavior. The signal thread never closes `HTTPResponse`/`BufferedReader`. The
owning reader closes them once, including pre-header and partial-send failures.
There is no proxy/redirect fallback, process stop, resumable header or new public
application authority in this path. The external provider path is unchanged.

Abort cleanup can confirm drain **before claim finalization** when all of these
conditions hold: the admitted slot was idle before send; a consistent native
stream has bound a new integer `id_task` while that slot was active; the exact
process object/residency is unchanged; and two subsequent owned-slot observations
report that same task idle. SSE completion IDs are distinct from `id_task`.
Managed slot ownership excludes queued reuse of the same slot until cleanup.
Independent clients must not bypass that ownership at the managed HTTP endpoint;
this mechanism is not isolation from hostile local clients.

The observation budget is at most two seconds, also limited by the request's
timeout. Each slot JSON response has its own remaining absolute deadline and
1 MiB limit. Wrong/absent/stale IDs, malformed acknowledgements, changed ownership,
deadline expiry and uncertain reader close preserve UNKNOWN. Neither EOF, a stop
attempt, an arbitrary idle slot nor a cancelled Job supplies this evidence.
Duplicate finalization returns the original result without polling or repairing
a tombstone. Job cancellation/failure and rollback remain independent of safe
provider cleanup, including natural completion racing cancellation.

A stop before any possible request transmission is known unsent. A transmitted
request interrupted before native task binding remains UNKNOWN even if later
physical idle is observed. Pre-header interruption works, but cannot manufacture
missing identity. OS address resolution is not promised interruptible. Header-free
disconnect is preferred; resumable DELETE remains a separately tested alternative,
not a production default. Actual bounded evidence and acceptance are recorded in
`ai-workflow/knowledge/`, separately from deterministic transport/claim tests.

Private `composer.configurationEvidence(resources, model)` explicitly retrieves
matching actual launch evidence or planned/unlaunched catalog evidence, with the
existing LC immutable schema. It conveys no execution/control authority. Do not
publish it indiscriminately; automatic profiles/PM/G-01 retain safe projections.
Owned process references survive failed stop/wait; a final lease never detaches an
unreaped child or shuts down a peer call. Shutdown during pending composition
revokes admission and reports incomplete until the callback unwinds and cleanup
can be explicitly retried. No real binary, model or consumer compatibility is
established by synthetic tests; ER-04 remains a separate gate.

## Startup configuration (LC-01/03)

Settings below may appear in `llamaCpp` or `llamaCpp.models[name]`. A model value
replaces the corresponding driver default; explicit `null` suppresses that
default. The entire kwargs object and extra-argument list replace inherited
objects/lists. Omitted/null fields emit no new switches and preserve unspecified
upstream/environment/template behavior. Existing minimal profiles gain no thinking
switch. Python booleans do not count as integers; JSON must be finite and acyclic.

| Setting | Accepted values | Meaning of explicit values |
| --- | --- | --- |
| `moeCacheMiB` | integer 0..2147483647 | GPU cache for CPU-resident MoE experts; 0 disables it; separate from KV cache |
| `cpuMoe` | boolean | true emits `--cpu-moe`; false adds no all-expert override, without promising GPU placement |
| `cpuMoeLayers` | integer 0..2147483647 | existing first-N-layer CPU expert placement |
| `serverReasoning` | `on`, `off`, `auto` | startup template thinking mode |
| `serverReasoningEffort` | nonblank string | `default` removes template effort upstream; other strings are template-defined |
| `serverReasoningBudget` | integer -1..2147483647 | -1 unrestricted, 0 immediate thinking termination, positive token budget |
| `serverReasoningBudgetMessage` | string, including empty | message before end-of-thinking when budget is exhausted |
| `serverReasoningFormat` | `none`, `auto`, `deepseek`, `deepseek-legacy` | reasoning extraction/representation, independent of generation enablement |
| `serverReasoningPreserve` | boolean | explicit positive/negative preservation switch; template support required |
| `chatTemplateKwargs` | JSON object | arbitrary template kwargs, recursively copied/frozen |

Do not combine `cpuMoe=true` with `cpuMoeLayers`, even zero. A dedicated reasoning
setting and its corresponding kwargs key are duplicate authorities, even when
equal: `serverReasoning`/`enable_thinking`, `serverReasoningEffort`/`reasoning_effort`,
`serverReasoningPreserve`/`preserve_reasoning`. The first/last kwargs require exact
booleans and effort requires nonblank text. Kwargs alone remain possible, although
upstream deprecates the enable/preserve forms in favor of dedicated flags.

Mode, effort, budget/message, extraction format and preservation remain independent.
In particular, `off` with effort and a positive budget is valid; values can be
dormant defaults for a later request override. Startup defaults are not a policy
that prevents per-request overrides. Existing request `reasoningEffort` is unchanged;
the server interprets request `none` as disabling thinking, whereas server effort
`none` is a template string. Request controls and output retention are described
below. `--no-jinja` conflicts with startup template controls.

Example configuration for a separately supplied local executable/model:

```json
{
  "llamaCpp": {
    "manageServer": true,
    "executable": "C:/llama/llama-server.exe",
    "capabilityCheck": "required",
    "models": {
      "example": {
        "modelPath": "C:/models/example.gguf",
        "moeCacheMiB": 0,
        "cpuMoe": true,
        "serverReasoning": "off",
        "serverReasoningEffort": "default",
        "serverReasoningBudget": -1,
        "serverReasoningFormat": "deepseek",
        "chatTemplateKwargs": {"custom_template_parameter": false}
      }
    }
  }
}
```

This example is not a tested model/backend compatibility claim. No files are
downloaded by configuration inspection or the capability check.

`extraArgs` preserves ordered experimental arguments, but every structured flag
family is reserved even if omitted. `configuration.py` is the pinned alias/arity
table: examples include `-rea`, `-cmoe`, `-ncmoe`, `--gpu-layers`, `-fa`, `-ctk`,
`-ctv`, `-lm`, and KV positive/negative aliases. Model/endpoint selectors cannot
replace structured ownership. Recognized removed draft options fail. LC-03 reuses
this same ownership/arity machinery for the speculative controls below.

Known experimental string options such as `--chat-template`, `--chat-template-file`,
`--grammar`, and `--override-tensor` consume their values even if those begin with
`-`. Equals forms work. Unknown option arity is unknowable: use
`--experimental=-value` for a dash-leading value. An unknown option cannot conceal
a reserved flag in the next token. Known duplicate switch families and multiple
template sources fail. Unknown/experimental arguments are always uncertified,
including after successful capability checking. Extra arguments that encode
tensor overrides, grammars or other experimental semantics are not an independent
model/template compatibility guarantee.

The trusted catalog freezes config and pinned relevant environment values
**including absence**, plus cwd and automatic-config discovery. Each selected
profile has its own immutable residency identity. Different inputs can be approved
profiles in the same stable host domain; selecting one never changes a resident
process without an explicit authorized idle transition. Stable executable/endpoint
policy conflicts reject rather than create a second authority for that domain.
Existing profiles retain their captured inputs through ambient mutation. At launch,
ordinary non-owned variables inherit normally and frozen owned values/absences
overwrite ambient ones. No raw environment dump is retained. The owned set is in
`ENV_NAMES`; LC-03 adds speculative inputs and automatic-config discovery. Arbitrary
other environment effects remain uncertified. Unknown extraArgs are conservative
ordered startup identity, not certified semantics.

`manageServer=false` remains usable with just `baseUrl` and optional context size.
It rejects all non-null new startup controls, even false/zero/empty kwargs, and
`capabilityCheck=required`. Configure that external server separately. Legacy
external startup-shaped settings remain readable but are marked unapplied;
neither local file probing nor HTTP success certifies a remote server.

`capabilityCheck` is a driver-level `none` (default) or `required`. The default
does not implicitly execute probes and all unchecked settings remain unverified.
`driver.checkCapabilities(model=None, refresh=False)` explicitly probes without
model startup; required mode checks lazily before launch. Each subprocess receives
only `--version` or `--help`, no model, with `LLAMA_ARG_*` removed. Each has a
5-second deadline and retains at most 256 KiB output. A temporary capture file is
polled every 10 ms; it can overshoot the byte threshold between polls and is
deleted after capture. Overflow/timeout kills and reaps the child. A bounded
16-entry cache keys resolved path, stat and observed executable SHA-256.

Required success checks reported `060b2fea4` source-prefix identity plus advertised
emitted structured flags and recognized inherited controls. It is called
`advertised-pinned-surface`, never effective
model support. Version text alone is insufficient, and a contradictory full source
digest fails. Missing/unreadable executable, wrong identity, missing switches,
malformed help, failed child, timeout and overflow are explicit outcomes. The
source pin is `060b2fea4ed528810ddc91d48fb22b3e4d63eb85`; executable file identity
is an observation before launch/probe, not an OS attestation of loaded bytes.
Changing files during the observation/launch window remains a limitation.

`driver.configurationEvidence(model=None)` returns an immutable **private** snapshot
with schema `actant.llamaCpp.configuration@1` and no probe/startup side effects:

- requested resolved values, omission/null distinction and driver/model provenance;
- target/mmproj/template input roles and configured/resolved paths, with null
  content hashes because model/input contents have not been hashed;
- planned argv versus actual ordered argv after successful Popen, launch generation,
  relevant environment and build snapshot for that launch, readiness/failure/stop;
- separately user-reported build context, configured executable, observed executable
  SHA-256/stat, private help/version evidence and capability-check outcomes;
- `providerReportedEffective.status=unknown-unqueried` until actual provider evidence
  exists. No effective fields are fabricated from health, help or fake tests.

Full evidence can contain credentials, private paths/kwargs/arguments and budget
messages. Only explicitly selected consumers should retain it; do not log it
indiscriminately. Nothing here sends it to telemetry, observation/G-01 or another
application. `profileId` hashes configuration/input identities and ordered arguments;
it is not a model content hash or behavior identity. A later probe leaves prior
snapshots and launch build evidence intact.

Automatic `LlmExecutionProfile.metadata` includes a safe `configurationEvidence`
summary (schema/profile IDs, mode and status) and sanitized `launchProfile` of
allowlisted typed scalars labeled `requested-not-effective`. Both adapters omit raw
kwargs/argv, paths, arbitrary cache/effort/message strings, URL and model title.
This deliberately narrows the previous automatic metadata shape; private evidence
is the retrieval path for full requested configuration. Cancellation, lazy shared
leases and optional observation are preserved.

## Request controls and matching estimation (LC-02)

`providerOptions` accepts `chatTemplateKwargs` (optional finite JSON object) and
`reasoningFormat` (`none`, `auto`, `deepseek`, `deepseek-legacy`), retaining the
existing optional nonblank `reasoningEffort`. Omission/`None` omits a control;
an explicit empty kwargs object is sent as `{}`. Nested caller-owned values are
copied into immutable mappings/tuples before later payload creation. Every payload
receives fresh JSON containers. Server startup controls remain invalid per-call
options; no request budgets or real-time reasoning-control endpoints are added.

Known kwargs `enable_thinking` and `preserve_reasoning` require exact booleans,
and `reasoning_effort` requires nonblank text. Other template keys are preserved
without inventing a universal template compatibility table. Effort strings are
template-defined, not limited to an invented high/medium/low enum.

Precedence follows pinned source: request kwargs override corresponding startup
kwargs, then request `reasoningEffort` overrides startup template effort. Within
one request, supplying both `reasoningEffort` and kwargs `reasoning_effort` fails
even if equal. `reasoningEffort="none"` disables thinking and clears template
effort upstream; it conflicts with kwargs `enable_thinking=true`, and may coexist
with explicit false. Other effort strings with `enable_thinking=false` are valid
independent/dormant controls. A kwargs effort of `"none"` is just a template
string; request effort `"default"` is also passed through, unlike the special
server startup effort `"default"`. Formatting never implicitly enables/disables
thinking. Request overrides leave LC-01 startup identity, argv and leases intact.

The profile-created token estimator uses the same messages, `chat_template_kwargs`,
`reasoning_effort` and `reasoning_format` as inference. Explicit external `model`
routing reaches both `/apply-template` and `/tokenize`; managed requests use the
already-selected resident target. This corrects older estimation that omitted
request controls and explicit external model selection. `/tokenize` must return
nonnegative exact integer token IDs; an invalid template/tokenizer response or
endpoint error raises, with no fallback count for a different prompt/settings.

Pinned `tools/server/server-context.cpp` routes `/apply-template` through the same
`oaicompat_chat_params_parse` used by chat inference; `server-common.cpp` performs
the request merge/effort/template rendering. This establishes source equivalence
for the supported surface, not a real binary/template attestation. Safe automatic
profile metadata `tokenEstimation` records the method, matched-request forwarding,
source pin, `effectiveHonor="unverified-binary-and-template"`, and failure policy.
Returned counts describe the server-returned rendered prompt; correctness still
requires the deployed endpoints/template to honor the pinned semantics. No raw
request kwargs/body are copied into automatic profile metadata.

## Separate private reasoning evidence

When the provider exposes `delta.reasoning_content`, the adapter retains it as
auxiliary provider evidence using the existing immutable event metadata contract.
It introduces no generic event/query/history schema. A reasoning-only delta has
empty `event.text`; mixed deltas retain only `delta.content` as ordinary text.
The fragment is in:

```python
event.metadata["llamaCppReasoning"] == {
    "schemaId": "actant.llamaCpp.reasoning@1",
    "content": "exact provider reasoning fragment",
    "source": "delta.reasoning_content",
}
```

At `[DONE]`, the completed event's same namespace holds all reasoning fragments
concatenated in order. Final native `usage` (including any reasoning-token details),
`timings`, `model`, `system_fingerprint`, reported `reasoning_format` when present,
and choice `finish_reason` remain available separately. Finish reason does not
prematurely terminate the stream: trailing native usage/timings still survive.
An exposed empty reasoning string is retained; an absent namespace means reasoning
was not separately exposed, and is not proof thinking was disabled.

Retrieve it through the existing complete processing result:

```python
result = pipeline.runProcessing(
    memoryKey="example", inputValue={}, providerName="llama.cpp",
    buildQueryItemsCapabilityId="build-items@1",
    buildQueryCapabilityId="build-query@1",
    providerOptions={
        "reasoningFormat": "deepseek",
        "chatTemplateKwargs": {"enable_thinking": True},
    },
)
answer = result.llm.rawText
evidence = result.llm.providerMetadata.get("llamaCppReasoning")
reasoning = None if evidence is None else evidence["content"]
```

The same final evidence reaches the existing completion capability at
`payload["llm"]["providerMetadata"]["llamaCppReasoning"]` and successful persisted
`processing/example/runs/{result.processingRunId}` under `providerMetadata`.
Direct `pipeline.run(...)` returns it in `StreamingLlmResult.providerMetadata`.
An explicitly selected stream observer can consume fragments. This is private
provider output, retained through the existing processing/result boundaries like
other provider evidence; it is not automatically inserted into future chat history.
Processing already retains provider options/query/response privately. Treat these
as private artifacts, not an indiscriminate diagnostic or telemetry payload.

The adapter never concatenates separate reasoning into ordinary answer text.
Formats `none` and `deepseek-legacy` can make the provider itself include thoughts
in `content`; those bytes are preserved as returned, without heuristic stripping
or pretending separately recoverable reasoning was exposed. Real-time controls,
history re-injection and consumer adoption are outside LC-02.

Malformed reasoning/content/choice shapes, invalid UTF-8/JSON/metadata and missing
`[DONE]` fail as protocol errors without reflecting raw private provider errors.
Response contexts close on success/failure/consumer close. Managed Job cancellation
interrupts its owned raw transport and the reader finalizes the response; external
mode retains its direct response-close behavior. Incomplete processing rolls back. Passive
observation counts nonempty answer text only; reasoning and raw request bodies are
not added to observation, telemetry or G-01. No backend API/export change is made.

## Speculative startup configuration (LC-03)

These are managed-server startup fields with the same driver-default/model-override
and explicit-null suppression rules as LC-01. Omitted speculation emits no new flag
and is **unspecified**, not a proven disabled baseline. `specMethods=["none"]`
is the explicit disabled request. It rejects every non-null draft field, even
false/zero/default values, and all speculative escape-hatch or inherited draft
inputs. It never loads draft metadata to infer a method. External mode rejects
non-null LC-03 fields rather than pretending to apply them remotely.

| Field | Accepted structured values | Canonical flag |
| --- | --- | --- |
| `specMethods` | nonempty ordered list of distinct pinned names below, or `["none"]` alone | `--spec-type` (comma-separated) |
| `draftMaxTokens` | exact integer 0..2147483647 | `--spec-draft-n-max` |
| `draftMinTokens` | exact integer 0..2147483647; must not exceed resolved max | `--spec-draft-n-min` |
| `draftUbatchSize` | exact integer 0..2147483647; zero inherits target ubatch upstream | `--spec-draft-n-ubatch` |
| `draftModelPath` | nonblank existing local auxiliary file path | `--spec-draft-model` |
| `draftDevices` | nonempty ordered list of individual device names, or `["none"]` alone | `--spec-draft-device` (comma-separated) |
| `draftGpuLayers` | signed 32-bit exact integer, `"auto"`, or `"all"` | `--spec-draft-ngl` |
| `draftSampling` | `"greedy"` or `"probabilistic"` | `--spec-draft-sampling` |
| `draftBackendSampling` | exact boolean | `--spec-draft-backend-sampling` / `--no-spec-draft-backend-sampling` |
| `draftPMin` | finite exact int/float in [0, 1], excluding bool | `--spec-draft-p-min` |
| `draftPSplit` | finite exact int/float in [0, 1], excluding bool | `--spec-draft-p-split` |

The source parser accepts broader signed values for minimum tokens and broader
floating-point thresholds. This structured subset rejects negative minimums
(downstream casts them to `size_t`) and non-probability thresholds. It preserves
signed GPU-layer integers, including -1/-2 and other negative integers accepted
by the parser, instead of incorrectly treating all placement fields as nonnegative.
Actual interpretation remains backend-dependent. For the min/max relation, omitted
pinned defaults are 0/3; validated inherited values replace those defaults.

Pinned upstream priority, in order:

1. `ngram-simple`, `ngram-map-k`, `ngram-map-k4v`, `ngram-mod`, `ngram-cache`
2. `draft-simple`, `draft-eagle3`, `draft-mtp`, `draft-dflash`, `draft-dspark`

`common_speculative_init` uses a bitset and that fixed priority, not arbitrary CLI
ordering. The provider therefore preserves the requested list and rejects lists
that ask for a different order instead of silently normalizing or claiming custom
priority was honored. Duplicate methods and `none` mixed with active methods fail.
Multiple active methods in the source priority order are permitted; this is not
a universal model/backend compatibility guarantee.

`draft-simple`, `draft-eagle3`, `draft-dflash`, and `draft-dspark` require an explicit
local auxiliary file. `draft-mtp` may use the target's MTP context without auxiliary
weights, as in the source. Draft fields require a draft method, and auxiliary
weights never select a method implicitly. Draft fields with ngram-only methods
fail. Device names are structurally checked, not probed or certified available.
DFlash/DSpark can clamp draft lengths to a trained block size; DSpark confidence
threshold behavior also depends on metadata/head support. Requested settings and
advertised flags never establish that a model honored them.

No-speculation example (with separately supplied local assets):

```json
{
  "llamaCpp": {
    "manageServer": true,
    "executable": "C:/llama/llama-server.exe",
    "capabilityCheck": "required",
    "modelPath": "C:/models/target.gguf",
    "specMethods": ["none"]
  }
}
```

Enabled example; model pairing is illustrative and unverified:

```json
{
  "llamaCpp": {
    "manageServer": true,
    "executable": "C:/llama/llama-server.exe",
    "capabilityCheck": "required",
    "modelPath": "C:/models/target.gguf",
    "specMethods": ["ngram-mod", "draft-simple"],
    "draftModelPath": "C:/models/local-draft.gguf",
    "draftMaxTokens": 6,
    "draftMinTokens": 1,
    "draftDevices": ["none"],
    "draftGpuLayers": 0,
    "draftSampling": "greedy",
    "draftBackendSampling": false,
    "draftPMin": 0.1,
    "extraArgs": ["--spec-ngram-mod-n-max", "64"]
  }
}
```

The example ngram extra argument remains uncertified. No auxiliary weights are
downloaded. Local draft existence is checked without loading its contents or
hashing model data. If switching an inherited active profile to `none`, explicitly
null out every inherited draft field; dormant values are not accepted as disabled.

The pinned model handler can infer speculation from a draft GGUF or repository
sidecar while method state is NONE. Accordingly, `--spec-type none` alone is not
the gate: all structured/inherited auxiliary inputs and speculative extraArgs are
checked. Reserved aliases include `-md`/`--model-draft`, `-devd`/`--device-draft`,
`-ngld`/`--gpu-layers-draft`/`--n-gpu-layers-draft`, `--draft-p-min`/`--draft-p-split`,
and both backend-sampling polarities. The source's long-option underscore aliases
are normalized for ownership checks, including equals forms; consumed string
values are not reinterpreted as flags. Recognition of an equals form for conflict
checking does not certify that an experimental argv form is accepted by the binary.

Alternate draft HF/download selectors (`--spec-draft-hf`, `-hfd`, `-hfrd`,
`--hf-repo-draft`), preset method activators (`--spec-default`, `--mtp`, `--dflash`,
`--eagle3`) and target/draft download presets are rejected. Removed draft/max/min
options, generic old ngram options and inherited `LLAMA_ARG_DRAFT_MAX/MIN` fail.
Known and future `LLAMA_ARG_SPEC_*`, `LLAMA_ARG_NO_SPEC_*`, `LLAMA_ARG_DRAFT_*` and
`LLAMA_ARG_*_DRAFT` values/absences bind to shared identity before acquisition.
New ambient speculative variables are removed at launch if absent in the frozen
snapshot. Typed inherited inputs get the same validation; unknown/deferred inputs
require an explicit active method and remain uncertified. A new trusted catalog under
changed inputs produces different immutable profile references; existing profiles
keep their captured inputs. Binding alone does not transition resident weights.

The pinned parser also loads system/user `config.ini` automatically. Explicit
speculation profiles reject present or unreadable automatic files, including benign
files, rather than parsing private defaults or trusting a CLI `none` to erase them.
Discovery roots (`PROGRAMDATA`/`APPDATA` on Windows; `XDG_CONFIG_HOME`/`HOME` on Unix)
and observed file absence/presence bind to each residency identity. Relative discovery paths
resolve against the captured launch directory. Absence is rechecked before probes
and launch; later changes do not restart a resident process. Omitted legacy profiles
may retain upstream defaults, with status `omitted-unverified`. Files are never
rewritten or deleted by the provider. Observation/launch races remain a limitation;
these are controlled requested inputs, not an OS attestation of effective behavior.

Method-specific options deferred to recognized, arity-aware `extraArgs`:

| Method | Deferred canonical controls (unvalidated semantics/ranges) |
| --- | --- |
| `ngram-mod` | `--spec-ngram-mod-n-min`, `--spec-ngram-mod-n-max`, `--spec-ngram-mod-n-match` |
| `ngram-simple` | `--spec-ngram-simple-size-n`, `--spec-ngram-simple-size-m`, `--spec-ngram-simple-min-hits` |
| `ngram-map-k` | `--spec-ngram-map-k-size-n`, `--spec-ngram-map-k-size-m`, `--spec-ngram-map-k-min-hits` |
| `ngram-map-k4v` | `--spec-ngram-map-k4v-size-n`, `--spec-ngram-map-k4v-size-m`, `--spec-ngram-map-k4v-min-hits` |
| `ngram-cache` | `--lookup-cache-static`/`-lcs`, `--lookup-cache-dynamic`/`-lcd` |

These require the matching method. Draft CPU/thread/affinity/priority/polling,
draft KV types, draft tensor overrides and draft MoE placement remain experimental
`extraArgs` under an active draft method; exact aliases are in `SPEC_EXTRAS`.
Synthetic acceptance controls (`--spec-synth-len`, `--spec-synth-rates`) also remain
uncertified extras under active methods. Unknown experimental flags can remain
uncertified; recognizable speculative ones require an active declared method and
are rejected with `none`. Ordinary unrelated unknown extras do not become certified
or evidence of an effective disabled baseline.

Private configuration evidence adds `speculation` with requested status/methods,
resolved structured/inherited inputs, known priority and uncertified input names,
plus automatic-config observations and the auxiliary `draft` input role. Input
paths are never content hashes. Profile identity includes these startup choices;
ordered args, actual launch environment/directory and build snapshots remain
separate. Automatic metadata exposes only safe typed controls and speculation
status; draft paths/device names and arbitrary experimental values remain private.
Required capability checking covers emitted and recognized inherited speculative
flags together with LC-01 controls, still meaning advertised pinned surface only.
LC-02 request/estimation/separate reasoning semantics and optional answer-only
observation remain unchanged. ER-03 adds the controlled adoption below; no automatic
restart or consumer adoption is provided.

### Private request evidence (EX-04; awaiting independent acceptance)

Trusted composition optionally accepts `requestEvidence=PrivateRequestEvidence()`
and immutable `evidencePolicies=(RequestEvidencePolicy(entry, required=True,
maxBodyBytes=2097152),)`. Default is off. Explicit disabled policies and omitted
owners capture nothing. Each selector resolves to an actual runtime-issued caller;
repeated composition rejects policy replacement. Capture does not grant execution.

The trusted host obtains that actual owner from its activation composition snapshot
and wraps exactly one managed call in
`broker.scope(owner, correlation=(suiteId, unitId, turnId), sink=privateSink)`.
Correlation is an immutable tuple of one to four ASCII identifiers, each at most
128 characters. Sinks receive frozen `RequestEvidence` records and must return the
singleton `True` only after their own required acknowledgement. The broker does
not implement filesystem durability, timeouts or background workers; host sinks
must provide bounded waits/cancellation. Scope exit must always be observed.

A required matching owner fails before inference send without its live scope.
Scopes are instance-local, thread-bound and atomically single-use across copied
contexts; nested/cross-thread/reused scopes reject. Estimator/template/slot traffic
is not captured. Serialized records contain the exact immutable `Request.data`
bytes, byte length and SHA-256, with JSON/UTF-8 declarations. Default body cap is
2 MiB, hard maximum 8 MiB. The body is intentional private prompt capture; sinks
must protect it. No headers, Authorization, cookies, URLs, live owners or grants
are included in records, metadata or generic events. Ordinary sink errors become
fixed errors without retained exception cause/context chains.

Phases are serialized, transport-attempt (intention before open), successful-open
response-headers-observed, and final transport observations. Final mayHaveSent is
not consumption; serverConsumption is always unknown. Error-path headers and
unobserved body facts remain unavailable. An on-disk preparation/attempt receipt
cannot prove send or receipt after process death. Native cleanup remains separate.
Final callbacks run after actual provider cleanup, outside its locks. A final sink
failure is sticky and raised on host scope exit if there was no earlier primary;
an already returned successful Job remains a successful Job with a separate host
capture failure. Earlier provider/cancellation/non-Exception exits keep precedence.

Adapter admission delegates forward validation to its exact issuing backend call,
using a private weak method reference. Missing/expired validators fail closed;
retaining admission does not retain the closed call or its mutation scope. The
provider rechecks after required forward callbacks, immediately before inference
open (including disabled/default-off), and at stream-read boundaries. Owner/access,
registration, mutation/conversation and claim checks remain backend-owned. Old
ticket cleanup/final diagnostics do not require forward validity. No authority lock
is held across sinks or IO: revocation after a final check can still race with send.
This is a boundary check, not an atomic send-versus-revoke guarantee. Independent
shared acceptance precedes Application adoption. No complete EX-04 archive acceptance,
real-provider archive evidence or stronger delivery/durability guarantee is claimed.
