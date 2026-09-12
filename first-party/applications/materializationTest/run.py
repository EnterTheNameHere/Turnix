# file: first-party/applications/materializationTest/run.py ; version: 1
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.io.managedIo import ManagedIo  # noqa: E402
from backend.packs.runtime import ManualActivationPlan, PackResolver  # noqa: E402
from backend.runtime.runtimeHost import RuntimeHost  # noqa: E402
from backend.save import ApplicationStore  # noqa: E402


def _normalizePath(value: object, *, configDirectory: Path) -> object:
    """Resolves one application-owned path relative to its configuration file."""
    if type(value) is not str:
        return value
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (configDirectory / path).resolve())


def _normalizePaths(config: dict[str, object], *, configDirectory: Path) -> dict[str, object]:
    """Normalizes only paths currently owned by the Materialization Test config."""
    normalized = dict(config)
    for key in ("promptsFile", "workspaceDirectory", "outputDirectory"):
        if key in normalized:
            normalized[key] = _normalizePath(normalized[key], configDirectory=configDirectory)

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


def main() -> int:
    """Runs or inspects one durable Materialization Test Application."""
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
    config = _normalizePaths(io.readJson(configPath), configDirectory=configPath.parent)
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
            if job.error is not None:
                raise job.error
            sys.stdout.write(f"{job.result!r}\n")
        else:
            def observe(event) -> None:
                if event.eventType == "delta" and event.text:
                    sys.stdout.write(event.text)
                    sys.stdout.flush()

            job = runtime.runJob("materializationTest.run@1", {"streamObserver": observe})
            if job.error is not None:
                raise job.error
            sys.stdout.write("\nMaterialization workflow run completed.\n")

        if job.authoritativeStateAccepted:
            savedBundle = runtime.saveApplication()
            sys.stdout.write(
                "Saved Application "
                f"{runtime.applicationRun.application.applicationId} "
                f"generation {savedBundle.generation}.\n"
            )
        return 0
    finally:
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
