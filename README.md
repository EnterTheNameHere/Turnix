# Tertalis

**Status: experimental platform development — not a stable release.**

Tertalis is a local-first, persistence-oriented modular platform for applications
that need ongoing work, coordination, reusable capabilities and continuity across
execution and interruptions. AI is an important motivation, but Tertalis is not
limited to chatbots or even AI applications.

The intended uses include AI buddies and assistants, code completion, experiment
and analysis tools, model orchestration, games and simulations. Applications
are assembled from Packs; an application itself can be delivered as an AppPack.
Tertalis supplies shared runtime infrastructure and contracts rather than
being any one application or language runtime.

## Should you try it?

If you want to explore an evolving Python-based platform, inspect its
architecture, run its tests, or help develop its internals, **yes**.

If you want a polished AI assistant that you can install and start immediately,
**not yet**. This repository does not currently offer a complete end-user
application distribution, a root `run.bat`, or a finished public
PackageManager/registry/CLI installation workflow. Do not mistake intended
architecture for implemented features.

The current platform work includes shared execution/runtime infrastructure,
Packs and Python code-entry support, persistence-oriented value and lifecycle
facilities, tracing, and tests. Some design areas extend beyond what is
implemented today. Refer to the source and test suite for exact current
behavior and to `design/index.txt` for architectural direction.

## Set up the platform (Windows)

You need Git, Windows, and access to install the Python requirements from
your configured package index. The repository includes the supported embedded
Python archives; the default is Python 3.15.0b4, with an explicit Python
3.14.6 fallback.

The repository is currently hosted under its former GitHub name while the
Tertalis migration is in progress. To inspect the current development branch:

```powershell
git clone --branch actant-integration https://github.com/EnterTheNameHere/Turnix.git
cd Turnix
.\setup.bat
```

You can alternatively run `setup.ps1` directly from PowerShell. The BAT
wrapper uses Windows PowerShell, preserves the setup exit code, and makes
double-click failures visible. Detailed options and troubleshooting are in
[SETUP.md](SETUP.md).

Root setup prepares **Tertalis internals only**. It does not discover, configure
or run applications. Running setup does not start a Tertalis service.

### Verify the development environment

After a successful setup, from the repository root:

```powershell
.\python-embedded\python.exe -X frozen_modules=off -m pytest tests
.\python-embedded\python.exe -X frozen_modules=off .\scripts\verify_tools.py
```

These are commands for the Windows environment described in `SETUP.md`;
they are not claims that the full test suite was rerun while writing this
README. Testing individual modules is also possible using pytest paths.
Ruff and ty executables are installed in `python-embedded/Scripts/`.

### Running an application

Tertalis applications have separate configuration and setup ownership.
In a combined application checkout, the expected sequence is:

1. Run root `setup.bat` (Tertalis internals).
2. Run `first-party/applications/<name>/setup.bat` (that application),
   when the application supplies it.
3. Run `first-party/applications/<name>/run.bat`, when supplied.

The corresponding PowerShell setup scripts can also be invoked directly.
An application must not silently rerun root setup or change the platform's
setup behavior. **This platform-only branch does not currently include
an application launcher at that path.** Consult each application's own
README/SETUP instructions for its actual requirements.

## Developing Tertalis

- [SETUP.md](SETUP.md): current setup, Python fallback and developer tools.
- [AGENTS.md](AGENTS.md): entry point for AI coding agents.
- [design/index.txt](design/index.txt): orientation to platform design.
- [design/platform.txt](design/platform.txt): platform goals and boundaries.
- [docs/CMR-04_CANONICAL_INDEX_TERTALIS_SPECIFICATION.txt](docs/CMR-04_CANONICAL_INDEX_TERTALIS_SPECIFICATION.txt):
  existing authoritative specification index.
- [ai-workflow/index.txt](ai-workflow/index.txt): workstream coordination.
- [ai-workflow/coordination-protocol.txt](ai-workflow/coordination-protocol.txt):
  ownership, messages, start/resume and handoff rules.

Development presently centers on Windows and repository-local embedded Python.
The platform is evolving and changes may be breaking. A protected `main`
branch is intended to present accepted repository work; that does not by
itself mean a stable product release. At the time of this document, the
Tertalis migration is still being completed on `actant-integration`.

## Future direction

The intended experience is to obtain Tertalis with its PackageManager,
discover AppPacks through registries, and resolve their declared requirements.
Application-first distributions should be able to bundle a compatible full
Tertalis installation. A proper `tertalis` CLI is also intended.

**These are goals, not instructions for features already available.** The
supported entry points today remain the repository's setup scripts and the
individual applications' own entry points.
