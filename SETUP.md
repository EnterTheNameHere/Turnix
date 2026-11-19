# Repository-local Windows setup

Double-click root `setup.bat`, or run it from a terminal. It calls the adjacent
root `setup.ps1` using Windows PowerShell, works from any working directory and
returns the PowerShell exit code. Setup parameters can be passed through, for
example `setup.bat -Requirements requirements.txt`. On failure it prints the exit
code and waits for a key so a double-click console remains visible. Automation
can set `TERTALIS_SETUP_NO_PAUSE=1` to suppress that failure-only wait.

You can also run `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1`.
Python 3.15.0b4 remains the primary runtime. Setup verifies the tracked vendor ZIP,
configures the embedded standard library/site-packages, installs requirements and
the Git pre-commit trampoline, then installs and verifies shared Ruff and ty.
Internet access to the configured pip package index is needed for missing packages.

`requirements-tools.txt` owns exact native-tool versions; `requirements.txt` includes
it. A custom `-Requirements` file does not disable shared tool installation.
Setup uses the selected embedded Python's pip and verifies its own `Scripts/ruff.exe`
and `Scripts/ty.exe`, their package/native versions and isolated checks on a temporary
sample. No repository-wide lint/type policy runs during setup. Failure stops setup
before its completion message. Rerunning verifies the tools again.

Use the reported absolute paths in trusted application host configurations. With
the default runtime, typical invocations are:

```powershell
.\python-embedded\python.exe -X frozen_modules=off -m pip --version
.\python-embedded\Scripts\ruff.exe --version
.\python-embedded\Scripts\ty.exe --version
.\python-embedded\python.exe -X frozen_modules=off .\scripts\verify_tools.py
```

The Windows EX-06 ty recipe is `check --config-file NUL --output-format concise FILE`.
`NUL` supplies an empty host-controlled configuration; it is not an artifact path.

For an explicitly selected Python 3.14.6 fallback, use `setup314.ps1`. It delegates
to the same installer/verifier and retains its historical default `python-embedded`
destination. To keep an existing 3.15 installation, select a separate destination:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup314.ps1 -EmbedDir python-embedded\fallback314
```

The Git documentation hook continues to use the default `python-embedded/python.exe`;
an alternate destination does not change that hook contract. An explicit fallback
into the default destination replaces that environment as before; there is no
automatic downgrade. Applications must use the selected runtime/tool paths.

Root setup provisions only Tertalis internals. It never discovers Applications or
invokes their setup scripts. For Application-local initialization, run that
Application's own `setup.bat` or `setup.ps1` separately after root setup. Those
scripts own local templates and user-file preservation and should check existing
platform prerequisites; they do not invoke root setup. Platform checkouts need
no Application source to provision tools.

## Development checks

The commands below run from the repository root after successful root setup.
They use the repository-local default Python installation rather than a
machine-global Python:

```powershell
.\python-embedded\python.exe -X frozen_modules=off -m pytest tests
.\python-embedded\python.exe -X frozen_modules=off .\scripts\verify_tools.py
.\python-embedded\Scripts\ruff.exe --version
.\python-embedded\Scripts\ty.exe --version
```

The pytest command runs the repository's test suite; it does not launch an
application. Some tests require Windows or additional external prerequisites,
and may be skipped or require further configuration. Running setup does not
itself mean repository-wide tests, formatting or static analysis have passed.
Check and report the actual results for the revision you are working with.

For the explicitly selected 3.14 fallback, invoke the corresponding
`python.exe` at the `-EmbedDir` you configured; do not assume it resides at
the default path. Consult the relevant test, application or workstream
documentation for any additional real-model or operating-system checks.

## Application checkouts

In a checkout that includes a separately owned application, preserve the
platform/application setup distinction. Execute the root `setup.bat` or
`setup.ps1` first, and then the application-owned
`first-party/applications/<name>/setup.bat` or `setup.ps1`, if provided.
Launch using the application's own `run.bat`, if provided. The root platform
scripts never discover, configure, or run the application. Consult the
application's README and setup instructions for local settings, dependencies,
optional assets, and required services.

For an overview of the platform and its present development status, see
`README.md`. AI contributors should start with `AGENTS.md` as well as the
repository's coordination protocol.
