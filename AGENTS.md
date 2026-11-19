# AI agent entry point — Tertalis

This file provides navigation, not a substitute for the repository's
coordination protocol or the user's current instructions. Humans and AI
use the same setup scripts, dependencies and verification procedures.

## Before working

1. Read [README.md](README.md) to establish purpose, current status and
   what this checkout does and does not offer.
2. Read [SETUP.md](SETUP.md) to prepare the actual local environment.
3. Read [design/index.txt](design/index.txt) for platform design orientation
   and the relevant area documents for the task.
4. Read [ai-workflow/index.txt](ai-workflow/index.txt) from the canonical
   coordination ref and [ai-workflow/coordination-protocol.txt](ai-workflow/coordination-protocol.txt).
5. Follow the protocol's start/resume barrier: identify the workstream,
   working branch, STATE, HANDOFF, inbox, ownership and tested baseline.
6. Check the currently authorized work and any migration/freeze instructions.
   Do not acquire ownership or change scope by inference.

The coordination ref during the current migration is
`actant-integration`; the user must explicitly authorize changing it.
A working branch and the coordination ref are different concepts.
Do not assume a new session retains past conversations.

## Code and documentation boundaries

- Platform internals, reusable first-party Packs and shared tests belong to
  the platform; application-exclusive AppPacks, settings and tests belong
  to their application workstream/repository.
- Do not merge complete application branches into the platform to
  synchronize them. Read the coordination protocol for accepted import,
  review, Integrator, and application-adoption procedures.
- `design/` contains architecture and working design material;
  `docs/` contains indexed specifications. A document under
  `design/working/` is not automatically authoritative.
- An actual implementation, a design goal, and a tested capability are
  different things. Report which is supported by evidence.
- Preserve branch/history identity and do not rewrite external history,
  publish, transfer repositories, delete branches or perform destructive
  tests without the required authorization.

## Environment and validation

Root `setup.bat` or `setup.ps1` prepares Tertalis internals only.
The local Python is normally `python-embedded/python.exe`; use
`-X frozen_modules=off` for the current embedded 3.15 setup.
The explicit 3.14 fallback is described in SETUP.md.

Common development checks from repository root (after setup):

```powershell
.\python-embedded\python.exe -X frozen_modules=off -m pytest tests
.\python-embedded\python.exe -X frozen_modules=off .\scripts\verify_tools.py
.\python-embedded\Scripts\ruff.exe --version
.\python-embedded\Scripts\ty.exe --version
```

Do not claim to have executed checks that were not actually run.
Use the applicable requirements for linting, types and targeted tests;
do not infer that setup runs repository-wide checks.

In an application checkout, root setup and
`first-party/applications/<name>/setup.bat` are separate steps.
Use application-specific instructions for optional models, data and
its `run.bat`. Do not invent a generic Tertalis CLI that does not yet exist.

## Before handing off

Follow the coordination protocol's finish barrier: record changed
files, actual test outcomes, exact Git refs, unresolved issues,
workstream state, outgoing messages and handoff. Failed or partial
integration is not a successful accepted result. Do not publish
new dependency/Pack revisions as accepted without the appropriate
review and authorization.
