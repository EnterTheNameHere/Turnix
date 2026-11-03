# ER-04 local provider verification

This bounded harness exercises the accepted ER-01/02/03 host, two real validation
Applications and the production llama.cpp composer. It is not Experiment Runner
or a consumer migration. It never downloads/copies models, takes over ports,
automatically changes profiles, or kills a quarantined provider.

Use Python 3.15 from the repository root. Supply a **private local** JSON file,
preferably below ignored `python-embedded/er04-local/`:

```json
{
  "executable": "<EXISTING_LOCAL_LLAMA_SERVER>",
  "models": {
    "nanbeige": "<EXISTING_LOCAL_GGUF>",
    "qwen": "<EXISTING_LOCAL_QWEN_GGUF>"
  },
  "contextTokens": 1024,
  "maxTokens": 24,
  "threads": 4,
  "timeoutSeconds": 90,
  "startupSeconds": 90,
  "hashModels": true,
  "prompt": "Reply with the word Ready.",
  "reasoningPrompt": "What is 17 multiplied by 19? Give a short answer.",
  "playwright": "<OPTIONAL_EXISTING_PLAYWRIGHT_PACKAGE_DIRECTORY>",
  "browser": "<OPTIONAL_EXISTING_EDGE_EXECUTABLE>"
}
```

Omit optional browser fields unless requesting browser verification. No local asset
paths are source defaults. Startup uses CPU-only weights/KV, bounded context,
batch/ubatch 128, one slot, explicit no-speculation and disabled web UI/op offload.
Model-free capability checks run before each permitted materialization. A/B use
the first GGUF with different startup threads/batch; Qoff/Qon use the second with
explicit server reasoning off/on. All transitions use actual granted capabilities.

```powershell
.\python-embedded\python.exe -X frozen_modules=off -m tools.executionResourceValidation.run --config python-embedded/er04-local/config.json --phase preflight
.\python-embedded\python.exe -X frozen_modules=off -m tools.executionResourceValidation.run --config python-embedded/er04-local/config.json --phase core
.\python-embedded\python.exe -X frozen_modules=off -m tools.executionResourceValidation.run --config python-embedded/er04-local/config.json --phase all
```

`preflight` inspects files/hashes/GGUF metadata and model-free help/version only.
Other modes first run preflight and ordinary/core safety cases. `observe` adds PM,
authenticated same-app G-01 and installed-browser G-02 connection/revocation;
`reasoning` adds Qwen request/template/server comparisons; `all` adds both.
Node must be installed for the explicit browser phase. Nothing is installed for it.

Exit status is 0 for a completed selected scope, 1 for failure or unresolved owned
cleanup, and 2 when a selected validation gate remains blocked. Thus `all` currently
returns 2 even when its safe real cases complete: active aborts in 04C remain blocked.

Each invocation creates a new private run directory. Raw paths/argv, provider
requests, output, reasoning, server logs and configuration are private artifacts.
Transport capture is bounded and transparent: it forwards actual original bytes
and keeps at most 128 KiB per response/128 requests. No authentication headers or
environment secrets are logged. GGUF metadata parsing is bounded and never loads
tensor data; optional complete file hashes stream in bounded memory.

Only `summary.json` is designed for separate review before sharing: fixed check
names/statuses, counts, requested-control labels, output lengths and executable
hash. Do not commit the other artifacts or raw outputs. Private evidence inside
the repository is permitted only under ignored `python-embedded/`. Other output
folders must be outside the checkout. Screenshots show observation metrics and
cleared credential controls, never prompts/reasoning/read credentials.

Phase outcomes remain distinct. A nonsensical answer or absent reasoning does not
fail the structural lifecycle gate. Qwen lengths report observed behavior and
server acceptance only, never a universal thinking toggle. Unsupported required
physical freshness/retention is not turned into a fake capability.

## Active abort boundary

Safe 04C cases exercise real active-stream busy denial, exclusive compatible-peer
denial, explicit drain/switch, owner unload and cancellation before send. Active
cancellation, early transport close and timeout are reported **blocked** in real
mode: the accepted stock adapter cannot confirm their quiescence, and ER-01 has
no authorized recovery/disposal for that unknown operation. Synthetic regression
tests retain those fail-closed contracts. This harness does not silently run aborts
and orphan or kill their servers. A separately reviewed cleanup/operator policy is
needed to run those real cases responsibly; 04C is not claimed wholly verified.

Any unexpected uncertainty in an otherwise ordinary run also stops dependent
phases. Accepted host cleanup is attempted; a live unknown provider remains owned
by the CLI guardian, which reports pending and retains the exact objects. It does
not stop/restart/reuse the quarantined process. Local evidence identifies its PID.
The guardian exits only after that process actually exits; process exit alone
does not turn the recorded unknown validation result into success. Operator
direction is required. Do not kill a process by port/PID lookup as a workaround.

## Verification

```powershell
.\python-embedded\python.exe -X frozen_modules=off -m pytest -q --import-mode=importlib tests/tools/test_executionResourceValidation.py
```

These tests use synthetic GGUF/OS/HTTP boundaries and real runtime/composer/facade
composition. They do not establish actual binary/model behavior. Real evidence
must name its exact source commit, private asset identities, selected phases,
commands, successful/pending cleanup and limitations.
