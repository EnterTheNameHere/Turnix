# file: first-party/applications/evilBirthdayAnalysis/run.py ; version: 5
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.io.managedIo import ManagedIo  # noqa: E402
from backend.packs.runtime import ManualActivationPlan, PackLoader, PackResolver  # noqa: E402
from backend.runtime.runtimeHost import RuntimeHost  # noqa: E402


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
    args = parser.parse_args()

    io = ManagedIo()
    configPath = Path(args.config).expanduser().resolve()
    config = _normalizePaths(io.readJson(configPath), configDirectory=configPath.parent)
    planPath = Path(__file__).with_name("activation-plan.json")
    plan = ManualActivationPlan.fromJson(io.readJson(planPath))

    host = RuntimeHost(config=config)
    resolver = PackResolver(roots=(REPO_ROOT / "first-party",))
    loader = PackLoader(host=host, resolver=resolver)
    host.start()

    def observe(event) -> None:
        if event.eventType == "delta" and event.text:
            sys.stdout.write(event.text)
            sys.stdout.flush()

    try:
        loader.activate(plan)
        job = host.runJob("evilAnalysis.run@1", {"streamObserver": observe})
        if job.error is not None:
            raise job.error
        result = job.result
        if isinstance(result, dict):
            results = result.get("results")
            if isinstance(results, list):
                sys.stdout.write(f"\n\nCompleted {len(results)} analysis windows.\n")
                for entry in results:
                    if not isinstance(entry, dict):
                        continue
                    saved = entry.get("saved")
                    if isinstance(saved, dict) and saved.get("path"):
                        sys.stdout.write(f"[{entry.get('windowIndex')}] {saved['path']}\n")
        return 0
    finally:
        try:
            loader.close()
        finally:
            host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
