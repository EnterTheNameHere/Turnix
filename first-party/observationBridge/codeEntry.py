# file: first-party/observationBridge/codeEntry.py ; version: 1
"""Explicit optional Pack activation and fresh-context publication capabilities."""
import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path


def _loadImplementation():
    """Loads this Pack's sibling package without depending on repository layout or cwd.

    The process cache contains code only, keyed by the resolved Pack location;
    endpoint/publication state stays per activated CodeEntry instance.
    """
    root = Path(__file__).resolve().parent
    packageName = "_actant_observation_bridge_" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    if packageName not in sys.modules:
        spec = importlib.util.spec_from_file_location(packageName, root / "__init__.py", submodule_search_locations=[str(root)])
        if spec is None or spec.loader is None:
            raise RuntimeError("Observation bridge package could not be loaded.")
        package = importlib.util.module_from_spec(spec)
        sys.modules[packageName] = package
        try:
            spec.loader.exec_module(package)
        except BaseException:
            sys.modules.pop(packageName, None)
            raise
    return importlib.import_module(packageName + ".bridge")


_implementation = _loadImplementation()


def onLoad(ctx):
    """Starts an endpoint only on explicit activation and registers same-app capabilities."""
    configuration = ctx.config.get("observationBridge", {})
    if type(configuration) is not dict or set(configuration) - {"port", "host", "allowedOrigins"}:
        raise ValueError("Invalid observationBridge configuration.")
    bridge = _implementation.ObservationBridge(
        port=configuration.get("port", 0), host=configuration.get("host", "127.0.0.1"),
        allowedOrigins=configuration.get("allowedOrigins", ()),
    )
    def publish(invocation, payload):
        """Accepts only an explicitly mediated same-ApplicationRun selected-session grant."""
        if type(payload) is not dict or set(payload) != {"grant"}:
            raise ValueError("publish requires only a grant.")
        reader = invocation.observations.bindReadGrant(payload["grant"])
        return bridge.publish(reader)
    def withdraw(_invocation, payload):
        """Withdraws publication access only; source observation ownership stays with the caller."""
        if type(payload) is not dict or set(payload) != {"publicationId"} or type(payload["publicationId"]) is not str:
            raise ValueError("withdraw requires a publicationId.")
        return bridge.withdraw(payload["publicationId"])
    def connection(_invocation, payload):
        """Supplies credentials explicitly to the authorized application for out-of-band viewer setup."""
        if payload is not None:
            raise ValueError("connection takes no payload.")
        return bridge.connection()
    try:
        ctx.capabilities.register("observation.bridge.publish@1", publish)
        ctx.capabilities.register("observation.bridge.withdraw@1", withdraw)
        ctx.capabilities.register("observation.bridge.connection@1", connection)
    except BaseException:
        bridge.close()
        raise
    return bridge


def onUnload(_ctx, state):
    """Closes endpoints/access without relying on invocation observation authority."""
    if state is not None:
        state.close()
