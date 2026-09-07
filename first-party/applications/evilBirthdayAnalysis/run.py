# file: first-party/applications/evilBirthdayAnalysis/run.py ; version: 14
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
    if type(value) is not str:
        return value
    path = Path(value)
    return str(path if path.is_absolute() else (configDirectory / path).resolve())


def _normalizePaths(config: dict[str, object], *, configDirectory: Path) -> dict[str, object]:
    normalized = dict(config)
    for key in ("promptsFile", "transcriptFile", "chatFile", "chatEmotesFile", "outputDirectory"):
        if key in normalized:
            normalized[key] = _normalizePath(normalized[key], configDirectory=configDirectory)

    llama = normalized.get("llamaCpp")
    if isinstance(llama, dict):
        llama = dict(llama)
        for key in ("executable", "modelPath", "mmprojPath"):
            if key in llama:
                llama[key] = _normalizePath(llama[key], configDirectory=configDirectory)

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
    parser = argparse.ArgumentParser(description="Run the Evil Birthday analysis AppPack.")
    parser.add_argument("config", nargs="?", default=str(Path(__file__).with_name("config.json")))
    application = parser.add_mutually_exclusive_group(required=True)
    application.add_argument(
        "--new-application",
        action="store_true",
        help="Create a new durable Evil Birthday analysis Application.",
    )
    application.add_argument(
        "--application-id",
        help="Load and run one existing durable Application by exact applicationId.",
    )
    parser.add_argument(
        "--preview-prompt",
        metavar="HH:MM:SS",
        help=(
            "Prepare and print one exact model-facing analysis prompt at the requested "
            "stream position without invoking model generation."
        ),
    )
    parser.add_argument(
        "--preview-output",
        help=(
            "Write prompt preview text to this path. Defaults to "
            "outputDirectory/prompt-previews/prompt-<HH-MM-SS>.txt."
        ),
    )
    parser.add_argument(
        "--saves-root",
        default=str(REPO_ROOT / "saves"),
        help="Application persistence root. Defaults to the repository saves/ directory.",
    )
    args = parser.parse_args()

    io = ManagedIo()
    configPath = Path(args.config).expanduser().resolve()
    config = _normalizePaths(io.readJson(configPath), configDirectory=configPath.parent)
    planPath = Path(__file__).with_name("activation-plan.json")
    plan = ManualActivationPlan.fromJson(io.readJson(planPath))

    store = ApplicationStore(Path(args.saves_root).expanduser().resolve())
    host = RuntimeHost(
        applicationStore=store,
        packResolver=PackResolver(roots=(REPO_ROOT / "first-party",)),
    )
    host.start()
    if args.new_application:
        runtime = host.createApplication(
            appPackId="evilBirthdayAnalysis",
            plan=plan,
            config=config,
        )
        sys.stdout.write(
            f"Created Application {runtime.applicationRun.application.applicationId}\n"
        )
    else:
        runtime = host.loadApplication(
            appPackId="evilBirthdayAnalysis",
            applicationId=args.application_id,
            plan=plan,
            config=config,
        )
        sys.stdout.write(
            f"Loaded Application {runtime.applicationRun.application.applicationId}\n"
        )

    def observe(event) -> None:
        if event.eventType == "delta" and event.text:
            sys.stdout.write(event.text)
            sys.stdout.flush()

    try:
        if args.preview_prompt is not None:
            job = runtime.runJob(
                "evilAnalysis.previewPrompt@1",
                {"position": args.preview_prompt},
            )
            if job.authoritativeStateAccepted:
                savedBundle = runtime.saveApplication()
                sys.stdout.write(
                    "Saved Application "
                    f"{runtime.applicationRun.application.applicationId} "
                    f"generation {savedBundle.generation}.\n"
                )
            if job.error is not None:
                raise job.error
            preview = job.result
            if not isinstance(preview, dict):
                raise RuntimeError("Prompt preview returned an invalid result.")
            query = preview.get("query")
            if not isinstance(query, dict) or type(query.get("payload")) is not str:
                raise RuntimeError("Prompt preview did not return exact text/plain payload.")
            outputDirectory = config.get("outputDirectory")
            if args.preview_output is None:
                if type(outputDirectory) is not str or not outputDirectory.strip():
                    raise ValueError(
                        "Prompt preview requires outputDirectory when --preview-output is not supplied."
                    )
                safePosition = args.preview_prompt.replace(":", "-")
                previewPath = (
                    Path(outputDirectory)
                    / "prompt-previews"
                    / f"prompt-{safePosition}.txt"
                )
            else:
                previewPath = Path(args.preview_output).expanduser()
                if not previewPath.is_absolute():
                    previewPath = (configPath.parent / previewPath).resolve()

            metadataPath = previewPath.with_suffix(".json")
            io.writeTextAtomic(previewPath, query["payload"])
            io.writeJsonAtomic(
                metadataPath,
                {
                    key: value
                    for key, value in preview.items()
                    if key != "query"
                }
                | {
                    "query": {
                        "formatId": query.get("formatId"),
                        "metadata": query.get("metadata"),
                        "promptFile": str(previewPath),
                    }
                },
            )

            executionProfile = preview.get("executionProfile")
            contextWindow = (
                executionProfile.get("contextWindowTokens")
                if isinstance(executionProfile, dict)
                else None
            )
            sys.stdout.write(
                f"\nPrompt preview at {args.preview_prompt}\n"
                f"Provider: {preview.get('provider')}\n"
                f"Model: {preview.get('model')}\n"
                f"Input tokens: {preview.get('inputTokens')}\n"
                f"Context window: {contextWindow}\n"
                f"Prompt file: {previewPath}\n"
                f"Metadata file: {metadataPath}\n"
            )
            return 0

        job = runtime.runJob("evilAnalysis.run@1", {"streamObserver": observe})
        result = job.result
        if isinstance(result, dict):
            results = result.get("results")
            if isinstance(results, list):
                sys.stdout.write(f"\n\nCompleted {len(results)} analysis windows.\n")
                for entry in results:
                    if not isinstance(entry, dict):
                        continue
                    warnings = entry.get("warnings", [])
                    if isinstance(warnings, list):
                        for warning in warnings:
                            if type(warning) is str and warning:
                                sys.stdout.write(
                                    f"[WARNING window {entry.get('windowIndex')}] {warning}\n"
                                )
                    exportError = entry.get("exportError")
                    if isinstance(exportError, dict):
                        message = exportError.get("message")
                        if type(message) is str and message:
                            sys.stdout.write(
                                f"[EXPORT ERROR window {entry.get('windowIndex')}] {message}\n"
                            )
                    saved = entry.get("saved")
                    if isinstance(saved, dict) and saved.get("path"):
                        sys.stdout.write(f"[{entry.get('windowIndex')}] {saved['path']}\n")
        if job.authoritativeStateAccepted:
            savedBundle = runtime.saveApplication()
            sys.stdout.write(
                "Saved Application "
                f"{runtime.applicationRun.application.applicationId} "
                f"generation {savedBundle.generation}.\n"
            )

        if job.error is not None:
            raise job.error

        return 0
    finally:
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
