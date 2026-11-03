# Experiment Runner (EX-01/EX-02)

This first-party AppPack runs one ordered conversation as one Actant Job through
the managed llama.cpp provider. The v1 bootstrap supports one configuration, one
conversation of 2–16 user turns, one repetition, replayed successful answers,
stop-on-error, and best-effort fresh per-call context. These are bootstrap limits;
EX-03/04/05, exclusive conversations, crash durability and comparison are unassigned.

From the repository root, using this checkout's Python 3.15 runtime:

```powershell
.\python-embedded\python.exe -X frozen_modules=off first-party/applications/experimentRunner/run.py validate --definition first-party/applications/experimentRunner/example.definition.json --host-config python-embedded/experiment-runner-local/host.json
.\python-embedded\python.exe -X frozen_modules=off first-party/applications/experimentRunner/run.py run --definition first-party/applications/experimentRunner/example.definition.json --host-config python-embedded/experiment-runner-local/host.json --results python-embedded/experiment-runner-local/results
.\python-embedded\python.exe -X frozen_modules=off first-party/applications/experimentRunner/run.py inspect --result PRIVATE_RESULT_FILE
```

`validate` parses the entire definition and host catalog without activation,
model loading or binary probes. `run` prints only outcome/count/path summaries.
`inspect` explicitly prints private prompts, answers, reasoning and selected host
evidence. Exit 0 requires successful run **and** Job, successful result persistence
and completed host cleanup. Ctrl+C requests cancellation on the retained Job and
waits for ordinary provider cleanup. There is no abort probe, process takeover,
automatic retry, transition restoration, tombstone repair or unmanaged fallback.

The separately supplied **trusted host** JSON has this shape (replace placeholders
with authorized local assets; never put startup settings in the definition):

```json
{
  "schemaId": "actant.experiment.host@1",
  "aliases": {"qwen-demo": "Qoff"},
  "limits": {"maxTokens": 24, "timeoutSeconds": 30, "maxTurns": 4, "maxOutputCharacters": 4096},
  "persistConfigurationEvidence": true,
  "llamaCpp": {
    "manageServer": true,
    "executable": "ABSOLUTE_AUTHORIZED_SERVER_PATH",
    "host": "127.0.0.1",
    "port": 18761,
    "defaultModel": "Qoff",
    "startupTimeoutSeconds": 90,
    "capabilityCheck": "required",
    "extraArgs": ["--slots", "--no-webui", "--no-op-offload"],
    "models": {"Qoff": {
      "modelPath": "ABSOLUTE_AUTHORIZED_QWEN3_4B_PATH",
      "contextWindowTokens": 1024,
      "threads": 4,
      "threadsBatch": 4,
      "gpuLayers": 0,
      "batchSize": 128,
      "ubatchSize": 128,
      "parallelSlots": 1,
      "kvOffload": false,
      "serverReasoning": "off",
      "specMethods": ["none"]
    }}
  }
}
```

Choose an unused port; do not displace another listener. The launcher grants only
the selected catalog selector to the exact Application CodeEntry source identity,
with explicit transition authority, and pins the manifest provider's exact source.
Aliases do not mint grants or descriptor compatibility. All turns and merged
options validate before RuntimeHost creation. Activation publishes capabilities
without inference; the Job resolves and explicitly transitions once before calls.
The host owns the catalog, aliases, caps and config-evidence selection. Definitions
cannot provide paths, URLs, argv, environment, ports, domains, grants or required
physical freshness/retention. The general schema bounds do not authorize increasing
the real demonstration's CPU/context1024/token24/threads4 ceiling.

The definition file is strict UTF-8 JSON capped at 1 MiB before parsing. Duplicate
keys, unknown fields, nonfinite/bool-as-number/null controls and unsupported policies
reject. Names/IDs are exact nonblank text up to 128 characters; user content is
exact nonempty text up to 16384 characters. Whitespace is preserved. Defaults require
`maxTokens` and `timeoutSeconds`; omitted temperature becomes 0.7. Supported options:

| Option | Bound |
| --- | --- |
| temperature | finite 0–2 |
| maxTokens | exact integer 1–4096, additionally host-capped |
| timeoutSeconds | finite >0–120, additionally host-capped |
| topP / minP | finite 0–1 |
| topK | exact integer 1–100000 |
| repeatPenalty | finite >0–10 |
| seed | signed 32-bit exact integer |
| reasoningFormat | none / auto / deepseek / deepseek-legacy |
| reasoningEffort | nonblank text, at most 128 characters; no effectiveness promise |
| chatTemplateKwargs | only exact boolean enable_thinking / preserve_reasoning |

`allowedOverrides` is a unique subset of those option names. Every merged turn is
validated, including the conflict between effort `none` and thinking `true`.
The host additionally limits turns and combined answer/reasoning characters per
turn (1–65536). Excess output fails the turn and retains only bounded partial
diagnostics. A partial answer is never replayed. Context overflow is a provider
error; this package does not clip, summarize or rewrite the conversation.

Turn one sends `[user1]`; turn two sends
`[user1, assistant1.rawText, user2]`. Empty completed answers remain valid exact
answers. Separate `llamaCppReasoning.content` never enters history. Each call uses
canonical immutable `LlmMessages` bytes and `FreshContext(required=False,
retain=False)`. Per-call admission/cleanup is retained; there is no conversation
reservation. Other authorized work may intervene between calls, causing the next
call to fail without automatic profile restoration.

`prepare@1`, `run@1`, `inspect@1`, and `finish@1` are prefixed
`experimentRunner.`. `onLoad` owns a bounded single-invocation recorder; overlapping
prepared/running invocations reject. `inspect` returns a frozen byte snapshot with
detached inspection copies, including after Job rollback. It retains no Context,
transaction, IO facade, estimator or execution handle. Until the launcher supplies
the independently observed terminal Job outcome to diagnostic `finish`, recorder
`status` is null; application completion alone cannot certify run success. These
diagnostics are ephemeral and are not committed authoritative mutation.

The launcher atomically writes `<opaque-runId>.json` through ManagedIo before
runtime teardown, then updates the same file with host cleanup if possible. A new
invocation gets a new run ID, even with an identical experiment ID. The outer
`actant.experiment.execution@1` envelope keeps `run` (`actant.experiment.result@1`),
actual `jobOutcome`, `launcherFailure`, `resultWriteFailure` and `hostCleanup`
separate. Turn statuses include explicit `not-started`; unavailable evidence stays
null/unknown. Cleanup comes from execution evidence or an exception's public
`llmCleanup`, independently of original success/failure/cancellation. Failed-call
private deltas are explicitly partial. Native usage/model/finish summaries and
safe resolved profile metadata are bounded; native task IDs and bearer handles
are excluded. The definition hash is SHA-256 of sorted-key compact JSON encoded
as UTF-8 (`ensure_ascii=False`, `allow_nan=False`), including explicit default
temperature. Structured query snapshots describe Application-sent messages, not
wire-level serialization proof.

Choose a directory private to the local user. The example uses ignored
`python-embedded/` for config/results. There is no ACL manager or durable archive.
Explicit `persistConfigurationEvidence=true` retains the composer's private
planned/actual evidence with its existing effective-unknown labels; omission
retains none. Default tracing discards records and no monitoring is activated.
Prompts, raw exceptions and host settings are not printed by ordinary execution.
If an atomic write/update fails, the CLI reports it and exits nonzero; it cannot
persist its own write failure into the failed destination. An earlier successful
file may therefore retain the pre-teardown snapshot. The in-process return value
keeps original outcomes and final failure diagnostics. Process crashes can lose
ephemeral results; EX-04 owns durability and complete wire provenance.

Post-Job interruptions during finalization, evidence capture or persistence still
enter the enclosing owned host-teardown boundary. A `KeyboardInterrupt` or other
non-`Exception` exit is re-raised after cleanup, with an immutable diagnostic
snapshot attached as `error.experimentExecution` for in-process callers. It cannot
produce a successful CLI exit. Original run/turn/Job outcomes remain separate from
the interrupted launcher/write and host cleanup; interrupted or uncertain cleanup
stays `failed-or-unknown`. The first interruption remains primary if cleanup also
raises. A failed/interrupted write is not retried. This is not a guarantee against
process termination, repeated interruption inside provider cleanup, or a real
provider abort validation.

Focused verification (synthetic process/HTTP IO, actual Actant runtime/provider):

```powershell
.\python-embedded\python.exe -X frozen_modules=off -m pytest -q --import-mode=importlib --tb=short tests/first_party/experimentRunner
```

Real acceptance remains separately gated on the supplied executable/model identity,
hashes and effective build evidence. It requires **two nonempty actual Qwen
responses** through this path, ordered history, separate reasoning evidence and
cleanup evidence. Synthetic tests and semantic correctness of a memory answer
cannot substitute for that demonstration.
