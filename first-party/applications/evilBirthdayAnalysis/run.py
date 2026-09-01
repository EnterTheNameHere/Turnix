# file: first-party/applications/evilBirthdayAnalysis/run.py ; version: 1
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


def _normalizePaths(config: dict[str, object], *, configDirectory: Path) -> dict[str, object]:
    normalized = dict(config)
    for key in ("promptsFile", "transcriptFile", "chatFile", "outputDirectory"):
        value = normalized.get(key)
        if type(value) is str:
            path = Path(value)
            if not path.is_absolute():
                normalized[key] = str((configDirectory / path).resolve())
    llama = normalized.get("llamaCpp")
    if isinstance(llama, dict):
        llama = dict(llama)
        for key in ("executable", "modelPath", "mmprojPath"):
            value = llama.get(key)
            if type(value) is str:
                path = Path(value)
                if not path.is_absolute():
                    llama[key] = str((configDirectory / path).resolve())
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
            saved = result.get("saved")
            if isinstance(saved, dict) and saved.get("path"):
                sys.stdout.write(f"\n\nSaved result: {saved['path']}\n")
        return 0
    finally:
        loader.close()
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
