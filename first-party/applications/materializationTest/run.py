# file: first-party/applications/materializationTest/run.py ; version: 4
from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.io.managedIo import ManagedIo  # noqa: E402
from backend.packs.runtime import ManualActivationPlan, PackResolver  # noqa: E402
from backend.runtime.runtimeHost import RuntimeHost  # noqa: E402
from backend.save import ApplicationStore  # noqa: E402


def _mergeConfig(base: object, override: object) -> object:
    """Recursively overlay one local configuration value onto a base value.

    Mapping values merge recursively so a local file can extend registries such
    as ``llamaCpp.models`` without copying unrelated defaults. Every non-mapping
    value, including lists and null, replaces the base value as one atomic value.

    Args:
        base: Default configuration value.
        override: Local value with higher precedence.

    Returns:
        A detached merged value. Neither input is mutated.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _mergeConfig(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(override)


def _loadConfig(io: ManagedIo, configPath: Path) -> dict[str, object]:
    """Load base configuration and automatically apply its optional local shadow.

    For ``config.json`` the sibling shadow is ``config.local.json``. The same
    convention applies to another explicitly selected JSON file, for example
    ``benchmark.json`` -> ``benchmark.local.json``. Absence of the local file is
    normal and leaves the base configuration unchanged.

    Args:
        io: Managed I/O service used to read configuration JSON.
        configPath: Absolute path of the selected base configuration.

    Returns:
        Detached recursively merged configuration object.

    Raises:
        TypeError: If either configuration document is not a JSON object.
    """
    base = io.readJson(configPath)
    if not isinstance(base, dict):
        raise TypeError("Materialization base configuration must be an object.")
    localPath = configPath.with_name(f"{configPath.stem}.local{configPath.suffix}")
    if not localPath.is_file():
        return deepcopy(base)
    local = io.readJson(localPath)
    if not isinstance(local, dict):
        raise TypeError("Materialization local configuration must be an object.")
    merged = _mergeConfig(base, local)
    if not isinstance(merged, dict):
        raise TypeError("Merged Materialization configuration must be an object.")
    return merged


def _normalizePath(value: object, *, configDirectory: Path) -> object:
    """Resolve one application-owned path relative to its configuration file."""
    if type(value) is not str:
        return value
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (configDirectory / path).resolve())


def _normalizePaths(config: dict[str, object], *, configDirectory: Path) -> dict[str, object]:
    """Normalize filesystem paths owned by application/runtime configuration."""
    normalized = dict(config)
    for key in (
        "promptsFile",
        "questionnaireDirectory",
        "workspaceDirectory",
        "outputDirectory",
    ):
        if key in normalized:
            normalized[key] = _normalizePath(normalized[key], configDirectory=configDirectory)

    processTools = normalized.get("processTools")
    if isinstance(processTools, dict):
        normalized["processTools"] = {
            name: _normalizePath(executable, configDirectory=configDirectory)
            for name, executable in processTools.items()
        }

    llama = normalized.get("llamaCpp")
    if isinstance(llama, dict):
        llama = dict(llama)
        if "executable" in llama:
            llama["executable"] = _normalizePath(llama["executable"], configDirectory=configDirectory)
        models = llama.get("models")
        if isinstance(models, dict):
            normalizedModels: dict[object, object] = {}
            for name, definition in models.items():
                if not isinstance(definition, dict):
                    normalizedModels[name] = definition
                    continue
                normalizedDefinition = dict(definition)
                for key in ("modelPath", "mmprojPath"):
                    if key in normalizedDefinition:
                        normalizedDefinition[key] = _normalizePath(
                            normalizedDefinition[key],
                            configDirectory=configDirectory,
                        )
                normalizedModels[name] = normalizedDefinition
            llama["models"] = normalizedModels
        normalized["llamaCpp"] = llama
    return normalized


def _saveAccepted(runtime, job) -> None:
    """Persist authoritative state accepted by a Job before propagating its error.

    Actant may accept authoritative memory before a later side-effect publication
    fails. Saving before re-raising preserves that accepted state rather than
    losing it merely because the Job also carries an error.
    """
    if not job.authoritativeStateAccepted:
        return
    savedBundle = runtime.saveApplication()
    sys.stdout.write(
        "Saved Application "
        f"{runtime.applicationRun.application.applicationId} "
        f"generation {savedBundle.generation}.\n"
    )


def main() -> int:
    """Run, inspect, persist, and export one durable Materialization Test Application."""
    parser = argparse.ArgumentParser(description="Run the Materialization Test AppPack.")
    parser.add_argument("config", nargs="?", default=str(Path(__file__).with_name("config.json")))
    application = parser.add_mutually_exclusive_group(required=True)
    application.add_argument("--new-application", action="store_true")
    application.add_argument("--application-id")
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Inspect the configured workflow skeleton without invoking the model.",
    )
    parser.add_argument("--saves-root", default=str(REPO_ROOT / "saves"))
    args = parser.parse_args()

    io = ManagedIo()
    configPath = Path(args.config).expanduser().resolve()
    config = _normalizePaths(
        _loadConfig(io, configPath),
        configDirectory=configPath.parent,
    )
    plan = ManualActivationPlan.fromJson(io.readJson(Path(__file__).with_name("activation-plan.json")))
    store = ApplicationStore(Path(args.saves_root).expanduser().resolve())
    host = RuntimeHost(
        applicationStore=store,
        packResolver=PackResolver(roots=(REPO_ROOT / "first-party",)),
    )
    host.start()
    try:
        if args.new_application:
            runtime = host.createApplication(
                appPackId="materializationTest",
                plan=plan,
                config=config,
            )
            sys.stdout.write(f"Created Application {runtime.applicationRun.application.applicationId}\n")
        else:
            runtime = host.loadApplication(
                appPackId="materializationTest",
                applicationId=args.application_id,
                plan=plan,
                config=config,
            )
            sys.stdout.write(f"Loaded Application {runtime.applicationRun.application.applicationId}\n")

        if args.describe:
            job = runtime.runJob("materializationTest.describe@1", None)
            _saveAccepted(runtime, job)
            if job.error is not None:
                raise job.error
            sys.stdout.write(f"{job.result!r}\n")
            return 0

        def observe(event) -> None:
            """Stream model text to the terminal without altering persisted evidence."""
            if event.eventType == "delta" and event.text:
                sys.stdout.write(event.text)
                sys.stdout.flush()

        job = runtime.runJob("materializationTest.run@1", {"streamObserver": observe})
        _saveAccepted(runtime, job)
        if job.error is not None:
            raise job.error
        sys.stdout.write("\nMaterialization workflow run completed.\n")

        exportJob = runtime.runJob("materializationTest.export@1", None)
        _saveAccepted(runtime, exportJob)
        if exportJob.error is not None:
            raise exportJob.error
        if not isinstance(exportJob.result, dict) or type(exportJob.result.get("path")) is not str:
            raise RuntimeError("Materialization evidence export returned an invalid result.")
        sys.stdout.write(f"Evidence: {exportJob.result['path']}\n")
        return 0
    finally:
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
