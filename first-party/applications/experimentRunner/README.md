# Experiment Runner (EX-01/02/R1 and EX-03)

This Application runs ordered configurations and repetitions through the managed
Actant llama.cpp provider. Each conversation execution is one actual Job with its
own exclusive conversation session. Histories start empty for every execution.
The complete imported platform remains colocated with this AppPackage; Application
source/tests belong on `actant-experiment-runner`, not the platform branch.

From this checkout root, with its own Python 3.15 runtime:

```powershell
.\python-embedded\python.exe -X frozen_modules=off first-party/applications/experimentRunner/run.py validate --definition first-party/applications/experimentRunner/example.suite.definition.json --host-config python-embedded/experiment-runner-local/host.json
.\python-embedded\python.exe -X frozen_modules=off first-party/applications/experimentRunner/run.py run --definition first-party/applications/experimentRunner/example.suite.definition.json --host-config python-embedded/experiment-runner-local/host.json --results python-embedded/experiment-runner-local/results
.\python-embedded\python.exe -X frozen_modules=off first-party/applications/experimentRunner/run.py inspect --result PRIVATE_RESULT_FILE
```

Copy `host.example.json` to a private ignored location and replace its placeholders
with explicitly authorized assets. Both startup profiles use the SAME Qwen3-4B
GGUF; the only startup difference is threads4 versus threads3. Reducing threads
below the existing ceiling supplies a distinct startup identity without increasing
CPU/context1024/token24/threads<=4 limits. Choose an unused port; never displace a
listener. The template does not identify or download assets. Real execution needs
the supplied executable/model hashes/build independently verified. The suite example
requests 2 configs x 2 repetitions x 2 turns = 8 calls. `example.definition.json`
remains the single-config/single-repetition example under the new schema.

`validate` performs full definition/catalog preflight without activation, binary
probes or model launch. `run` prints only suite identity/status/counts/path and
separate launcher/write/host diagnostics. `inspect` explicitly displays private
content. Exit 0 requires complete suite success, successful persistence and completed
host cleanup. Ctrl+C cancels the active exact Job or prevents the next unit when
between Jobs. A post-loop interruption is re-raised after owned teardown.

## Explicit schema migration and preflight

EX-03 accepts `actant.experiment.definition@2` and trusted
`actant.experiment.host@2`. Version-1 inputs explicitly reject; there is no silent
reinterpretation or compatibility adapter. Single-config/single-repetition behavior
and EX-01/02/R1 architectural assertions remain covered with migrated fixtures.

The definition retains one conversation of 2..16 ordered user-only text turns,
`historyPolicy: replay-answers`, `errorPolicy: stop`, and
`contextPolicy: fresh-best-effort`. Configurations are an ordered nonempty list of
unique names, approved profile aliases, defaults and unique `allowedOverrides`.
Repetitions must be an exact positive integer, excluding bool. Execution order is
configuration 1 repetitions 1..N, then configuration 2 repetitions 1..N, and so on.
There is no reordering for residency, concurrent execution, implicit queue/wait,
retry, continuation after failure, restoration or reservation across the suite.

All configurations, including later ones, validate before ANY model startup.
The input is strict UTF-8 JSON capped at 1 MiB before parsing. Duplicate keys,
unknown fields, nonfinite values, bool-as-number/null controls, unsupported policies
and invalid merged options reject. Names/IDs are exact nonblank text up to 128
characters; content is exact nonempty text up to 16384 characters. No whitespace
normalization. Defaults require tokens/time; omitted temperature becomes 0.7.
Definitions and nested options/aliases are copied into immutable canonical bytes.
SHA-256 hashes compact sorted-key UTF-8 JSON (`ensure_ascii=False`,
`allow_nan=False`), including explicit defaults and configuration order.

| Bound | Schema ceiling; host may narrow |
| --- | --- |
| configurations | 8 (`maxConfigurations`) |
| repetitions | 10 (`maxRepetitions`) |
| conversation executions | 32 (`maxExecutions`) |
| total requested turn calls | 256 (`maxTurnCalls`) |
| turns per conversation | 16 (`maxTurns`) |
| combined answer/reasoning chars per turn | 65536 (`maxOutputCharacters`) |
| retained canonical result | 64 MiB, conservative expansion check before startup |

The host also supplies `maxTokens`/`timeoutSeconds`. The retained-data estimate
includes query-history replay, output escaping, repeated definitions, native/profile
metadata and up to 256 KiB private configuration evidence per unit. Oversized
expansion rejects rather than truncating units. This is a logical JSON-data bound,
not a process RSS or provider buffering promise. Atomic result writing uses compact
canonical JSON with a final 64 MiB byte guard; inspect has the same byte ceiling.

| Request option | Bound |
| --- | --- |
| temperature | finite 0..2 |
| maxTokens | exact integer 1..4096, additionally host-capped |
| timeoutSeconds | finite >0..120, additionally host-capped |
| topP / minP | finite 0..1 |
| topK | exact integer 1..100000 |
| repeatPenalty | finite >0..10 |
| seed | signed 32-bit exact integer |
| reasoningFormat | none / auto / deepseek / deepseek-legacy |
| reasoningEffort | nonblank text <=128 chars; no effectiveness promise |
| chatTemplateKwargs | exact booleans enable_thinking / preserve_reasoning only |

Every turn's overrides must be permitted by EVERY selected configuration. Validate
merged defaults/overrides, including effort `none` versus thinking `true` conflicts.
Definitions cannot supply startup settings, paths, argv/env/ports/domains/grants,
URLs or required physical freshness/retention. Schema ceilings do not authorize
increasing the actual demonstration's resource limits.

## Managed ownership and unit boundaries

The host independently freezes LlamaCppCatalog/aliases/caps. LlamaCppComposition
pins exact provider and actual Application source EntrySelectors and grants only
the deduplicated SELECTED selectors plus explicit transition authority. Unused
catalog models receive no grants. Alias/name equality does not establish authority
or descriptor compatibility. Activation publishes capabilities without inference.

Each unit resolves its profile, explicitly transitions, then reserves a conversation.
Transition and reservation are separate admissions: another owner can win the gap;
that denial stops this suite without takeover/retry. The exact issued session is
passed to every `ctx.llm.run`. Even same-profile peers are excluded between turns.
Each call retains `FreshContext(required=False, retain=False)`; replay is Application
policy, not retained KV. Completed empty answers remain valid. Partial output and
separate reasoning never enter history. Queries are immutable canonical LlmMessages:
`[user1]`, then `[user1, assistant1.rawText, user2]`. No clipping or summarization;
provider/context overflow stops the suite.

The session closes in `finally` after per-call cleanup. Close queues release; it
is NOT a synchronous release receipt. The launcher waits for the actual Job's
runtime lifetime boundary to return before the next transition/reservation. Existing
public cleanup diagnostics distinguish failed/unknown boundaries; no private drain,
manual claim finish, provider stop or recovery is called. Exclusion is released
between successful units, so other authorized work may intervene. UNKNOWN, busy,
revocation or a reservation race stops the suite and leaves explicit unstarted units.

## Private outcomes and interruption handling

`actant.experiment.execution@2` wraps a fresh `actant.experiment.suite@1`. The suite
has a fresh opaque suiteId, immutable definition/hash, timing/status and ordered
units with stable independent unit IDs, config index/name/alias and repetition.
Each unit has an actual Job outcome (null when unadmitted), independent unit status,
lifetime-boundary status, optional private configuration evidence and a
`actant.experiment.result@2` conversation result whose runId equals unitId.
Conversation results retain turns/options/exact queries/answers/separate reasoning,
timings, bounded native summaries and public executionEvidence snapshots. The unit
is successful only when its Job, conversation, reservation close and lifetime
boundary all meet their normal completion conditions. Suite success needs EVERY
scheduled unit successful. Launcher/file/host failure remains separate and makes
CLI nonzero even when every conversation succeeded.

Reservation diagnostics distinguish acquisition attempted/acquired/failed, close
not-attempted/queued/failed, and releaseReceipt always `unavailable`. A returned
lifetime boundary is not a fabricated provider release receipt. Per-call cleanup
comes from executionEvidence or exception llmCleanup; missing evidence is unknown.
Reasoning null means unavailable, not proof internal thinking was disabled. Native
task IDs, grants, live estimators and session handles are excluded. Configuration
planned/actual/effective-unknown labels come from composition, only if explicitly
selected by host; oversized private evidence is marked unavailable-over-limit.

The per-Application recorder holds immutable snapshots, never Context, transactions,
IO facades or sessions. Prepare rejects overlap; run executes one unit; finish
projects the independently observed Job for ephemeral diagnostics; inspect returns
a frozen snapshot. Before execution the launcher obtains a read-only `reader@1`
view. `reader.read()` returns immutable snapshots without another runtime invocation:
a pending UNKNOWN reservation can make later capability boundaries fail. This
Application-only diagnostic view has no execution/drain rights and preserves data
without repairing/quiescing that resource. Old snapshots remain unchanged.

SuiteControl holds a suite-owned cancellation flag and only the active exact Job.
Cancellation before a unit prevents Job admission and transition; during a unit it
cancels that Job. Earlier terminal Jobs are never relabeled. Results for units that
never start remain explicit with no invented Job/provider evidence.

The installed Python SIGINT handler only stores a monotonic flag: it takes no
controller/Event/Job lock and invokes no provider callbacks. A launcher-owned
relay polls that flag and calls the exact active Job's ordinary cancellation API
outside the interrupted stack. The relay is rooted before startup, exists only
for the installed handler's lifetime, and is stopped/boundedly joined after handler
restoration. Polling supplies no real-time cancellation latency promise. A failed
relay cleanup is a separate launcher failure and cannot certify provider cleanup.
Ordinary cross-thread `cancel()` captures the Job under a reentrant controller lane
and invokes callbacks after releasing that lane. Handoff rechecks cancellation
after Job publication, and the launcher checks it again before preparation. A signal
in the check/publication gap cannot be lost; a cancelled suite cannot admit the
next unit. A request observed before orchestration ends keeps suite status cancelled
even if the final Job won its completion race; completed Job/turn outcomes stay intact.

One private `<suiteId>.json` is written atomically through ManagedIo before host
teardown, then updated with host cleanup if possible. A failed/interrupted write
is not retried. It may leave no file or a pre-teardown snapshot; returned diagnostics
report failure separately. R1's enclosing host finally protects finalization,
evidence capture and aggregate persistence. The first KeyboardInterrupt or other
non-Exception exit is re-raised after cleanup, with immutable `experimentExecution`
diagnostics attached. Cleanup failure/uncertainty remains failed-or-unknown and
cannot overwrite original Job/cancellation outcomes. Repeated interruption inside
provider cleanup or process termination/crash cannot promise completed shutdown.

Select a directory private to the local user; the example uses ignored
`python-embedded/`. No ACL manager, journal, crash recovery, automatic archive,
comparison, PM/G UI or full wire-provenance system is added. Default trace sinks
discard records and no monitoring activates. Structured snapshots are Application-sent
queries, not proof of exact HTTP serialization. EX-04/05 remain unassigned.

```powershell
.\python-embedded\python.exe -X frozen_modules=off -m pytest -q --import-mode=importlib --tb=short tests/first_party/experimentRunner
```

Deterministic tests use actual Actant Application/Job/managed composition with
explicitly SYNTHETIC process/HTTP IO. Real EX-03 acceptance separately requires eight
actual nonempty Qwen responses across two startup profiles/two repetitions/two
turns, verified identities/build, exact order/history, reuse/transitions and cleanup.
Worker-local asset absence must be reported as a blocker; prior EX-01/02 evidence
and synthetic tests do not substitute. No semantic answer-quality oracle.
