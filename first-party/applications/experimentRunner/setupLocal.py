# file: first-party/applications/experimentRunner/setupLocal.py ; version: 1
"""Check existing platform readiness, then initialize missing private App files only."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

APP = Path(__file__).resolve().parent
ROOT = APP.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def prerequisites():
    """Import actual execution dependencies and verify only the selected root tools.

    This performs native tool version/diagnostic checks on a harmless temporary
    fixture. It does not launch a model, install packages or call root setup.
    """
    if Path(sys.executable).resolve() != (ROOT / "python-embedded/python.exe").resolve():
        raise RuntimeError("Repository embedded runtime required.")
    launcher = importlib.import_module("first-party.applications.experimentRunner.launcher")
    launcher.loadAdapter()
    importlib.import_module("first-party.applications.experimentRunner.codeEntry")
    importlib.import_module("json5")
    tools = importlib.import_module("scripts.verify_tools").verifyTools()
    return tools


def initialize(tools):
    """Create absent private templates exclusively; existing user bytes always win.

    The materialization host uses root-managed executable paths from the readiness
    check. Model paths remain explicit placeholders requiring operator edits.
    """
    local = APP / "local"
    for directory in (local, local / "experiments", local / "results"):
        directory.mkdir(exist_ok=True)
    host = json.loads((APP / "examples/python-materialization/host.template.json").read_text(encoding="utf-8"))
    host["processTools"] = {name: item["executable"] for name, item in tools["tools"].items()}
    fixtures = {
        "run.bat": (APP / "run.bat").read_bytes(),
        "host.json": (json.dumps(host, indent=2) + "\n").encode("utf-8"),
        "host.basic.json": (APP / "examples/basic-conversation/host.template.json").read_bytes(),
    }
    for name, data in fixtures.items():
        try:
            with (local / name).open("xb") as output:
                output.write(data)
        except FileExistsError:
            pass


def main():
    """Distinguish platform prerequisites (2) from local filesystem setup failure (1)."""
    try:
        tools = prerequisites()
    except Exception:
        print("Experiment Runner prerequisite check failed. Run repository ROOT setup.bat separately; "
              "it must provision the embedded runtime, Actant imports, Ruff 0.17.4 and ty 0.0.99.", file=sys.stderr)
        return 2
    try:
        initialize(tools)
    except Exception:
        print("Experiment Runner local setup failed. Check Application local/ permissions and file/directory conflicts; "
              "existing user files were preserved.", file=sys.stderr)
        return 1
    print("Experiment Runner local setup complete. Existing files preserved.\n"
          "Edit local/host.basic.json (conversation) or local/host.json (materialization) with authorized model/server paths.\n"
          "Edit local/run.bat, then launch it. Models have not been checked or started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
