# file: backend/packManager/__init__.py ; version: 1

class PackManager:
    """Future graph-preparation boundary.

    The proving-ground runtime intentionally does not depend on PackManager.
    ManualActivationPlan plus PackResolver uniqueness checks provide the current
    explicit input to activation.
    """

    def preparePackGraph(self, *args, **kwargs):
        del args, kwargs
        raise NotImplementedError("PackManager graph preparation is intentionally not implemented yet.")

    def prepareActivationPlan(self, *args, **kwargs):
        del args, kwargs
        raise NotImplementedError("PackManager activation-plan preparation is intentionally not implemented yet.")


__all__ = ["PackManager"]
