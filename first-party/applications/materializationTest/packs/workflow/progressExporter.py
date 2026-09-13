# file: first-party/applications/materializationTest/packs/workflow/progressExporter.py ; version: 1
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_IMPLEMENTATION_NAME = "materializationTestExporterImplementation"
_IMPLEMENTATION_PATH = Path(__file__).with_name("exporter.py")


def _loadImplementation():
    """Load the terminal exporter implementation wrapped by this status adapter."""
    existing = sys.modules.get(_IMPLEMENTATION_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(_IMPLEMENTATION_NAME, _IMPLEMENTATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to construct the Materialization exporter module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_IMPLEMENTATION_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_IMPLEMENTATION_NAME, None)
        raise
    return module


_impl = _loadImplementation()


def _export(ctx, payload):
    """Run the normal exporter, then atomically mark its terminal projection complete."""
    result = _impl._export(ctx, payload)
    path = _impl._exportPath(ctx)
    evidence = ctx.io.readJson(path)
    if not isinstance(evidence, dict):
        raise RuntimeError("Materialization terminal evidence must be an object.")
    processingRuns = evidence.get("processingRuns")
    if not isinstance(processingRuns, list):
        raise RuntimeError("Materialization terminal evidence ProcessingRuns must be a list.")
    evidence["runStatus"] = {
        "state": "completed",
        "restorable": False,
        "completedProcessingRunCount": len(processingRuns),
    }
    ctx.io.writeJsonAtomic(path, evidence)
    return result


def onLoad(ctx):
    """Register terminal export with an explicit completed run-status marker."""
    ctx.capabilities.register("materializationTest.export@1", _export)
