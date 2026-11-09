# Repository-local Windows setup

Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1` from a checkout.
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

Optional Application-local bootstrap discovery is proposed in canonical workflow
mail and awaits agreement with the Application owner. This tooling delivery does
not implement or promise creation of an Application's `local/run.bat` yet. Platform
checkouts need no Application source to provision tools.
