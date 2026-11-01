# llama.cpp startup controls and configuration evidence

The manifest loads `structuredCodeEntry.py`; `codeEntry.py` supplies its driver.
Managed registration leases a host driver and leaves startup lazy. The final
lease releases its server. These controls do not add a restart, scheduling or
resource arbitration policy. Startup controls are LC-01, request/reasoning evidence
is LC-02, and speculative startup configuration below is LC-03.

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

Before managed lease acquisition, the adapter freezes config and pinned relevant
environment values **including absence**. Their identity is shared across application
leases. A new application under different relevant inputs gets the existing typed
`SharedServiceConflictError`; it cannot share a mismatched driver. Existing leases
keep their snapshot through ambient mutations. At launch, ordinary non-owned
variables inherit normally and frozen owned values/absences overwrite ambient ones.
Explicit structured settings collide with corresponding environment authorities;
template kwargs and legacy paired-switch `LLAMA_ARG_NO_*` aliases are included.
The launch working directory is captured in the same identity and passed to Popen,
so relative input/experimental paths cannot change when the ambient directory does.
No raw environment dump is retained. The owned set is in `ENV_NAMES`; LC-03 adds
speculative inputs and automatic-config discovery as described below. Arbitrary
other environment effects remain uncertified.

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
Response contexts close on success/failure/consumer close; ordinary Job cancellation
still closes the active HTTP response and rolls back incomplete processing. Passive
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
require an explicit active method and remain uncertified. A new application under
changed inputs gets typed lease conflict; existing leases keep their old inputs.

The pinned parser also loads system/user `config.ini` automatically. Explicit
speculation profiles reject present or unreadable automatic files, including benign
files, rather than parsing private defaults or trusting a CLI `none` to erase them.
Discovery roots (`PROGRAMDATA`/`APPDATA` on Windows; `XDG_CONFIG_HOME`/`HOME` on Unix)
and observed file absence/presence bind to lease identity. Relative discovery paths
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
observation remain unchanged; no restart, arbitration or consumer adoption is added.
