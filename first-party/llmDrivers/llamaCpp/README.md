# llama.cpp startup controls and configuration evidence

The manifest loads `structuredCodeEntry.py`; `codeEntry.py` supplies its driver.
Managed registration leases a host driver and leaves startup lazy. The final
lease releases its server. These controls do not add a restart, scheduling or
resource arbitration policy. This document covers LC-01 only.

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
`none` is a template string. No new request options, estimator behavior or reasoning
output retention are provided here. `--no-jinja` conflicts with template controls.

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
replace structured ownership. Recognized removed draft options fail; this does not
implement speculative decoding or prove a no-speculation baseline.

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
No raw environment dump is retained. The owned set is in `ENV_NAMES`; arbitrary
other environment effects, including full speculative inputs, are not certified.

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
