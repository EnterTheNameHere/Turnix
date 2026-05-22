from __future__ import annotations

from backend.pipeline.modelProvider import ModelProvider
from backend.runtime.appInstance import AppInstance, AppInstanceIdentity, AppInstanceState
from backend.runtime.roots import RepoOnlyRootLocator
from backend.runtime.workspace import RuntimeWorkspace


class RuntimeHost:
    """Minimal RuntimeHost implementation."""

    def __init__(self, *, rootLocator: RepoOnlyRootLocator | None = None) -> None:
        self.rootLocator = rootLocator or RepoOnlyRootLocator()
        self.workspace: RuntimeWorkspace | None = None
        self.appInstances: dict[str, AppInstance] = {}
        self.isRunning = False

    def start(self) -> RuntimeWorkspace:
        print("Turnix RuntimeHost starting...")
        roots = self.rootLocator.locate()
        self.rootLocator.ensureRuntimeDirectories(roots)
        self.workspace = RuntimeWorkspace(roots=roots)
        self.isRunning = True
        print("Workspace acquired.")
        return self.workspace

    def startAppInstance(self, appPackId: str, *, modelProvider: ModelProvider | None = None) -> AppInstance:
        if not self.isRunning:
            raise RuntimeError("RuntimeHost is not running")

        appInstance = AppInstance(
            identity=AppInstanceIdentity(
                appPackId=appPackId,
                appInstanceId=f"{appPackId}-current-run",
            ),
            modelProvider=modelProvider,
        )
        print(f"Starting AppInstance: {appPackId}")
        appInstance.start()
        self.appInstances[appInstance.appInstanceId] = appInstance
        return appInstance

    def stop(self) -> None:
        for appInstance in list(self.appInstances.values()):
            if appInstance.state != AppInstanceState.STOPPED:
                print("Stopping AppInstance...")
                appInstance.stop()
        self.appInstances.clear()
        self.isRunning = False
        print("Turnix stopped cleanly.")
