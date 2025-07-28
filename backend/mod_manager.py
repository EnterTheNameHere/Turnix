from pathlib import Path
import json, yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from itertools import chain

import logging
logger = logging.getLogger(__name__)

class ModManifest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    path: str | None = None                         # Filesystem path to mod folder
    modId: str | None = None                        # Required ID, may be inferred from dir or code
    displayName: str | None = None                  # Human-readable name
    order: int = 0                                  # For load ordering
    description: str | None = None                  # Optional description
    executable: str | None = None                   # Main file (e.g., mod.py / mod.js)
    executables: list[str] | None = None            # Extra scripts/files
    author: str | None = None                       # Author name/email/etc.
    repository: str | None = None                   # Repo URL
    semver: str | None = None                       # Semantic version
    tags: list[str] = Field(default_factory=list)   # e.g., "ui", "library", "debug"
    hidden: bool = False                            # Is hidden in mod list?
    dependencies: list = Field(default_factory=list)# Mod IDs or dicts with version
    before: list = Field(default_factory=list)      # List of modIds to run before
    after: list = Field(default_factory=list)       # List of modIds to run after


class ModManager():
    def __init__(self):
        self.modManifests = {}
        self.modManifestsWithUnresolvedModIds = {}
        self.sortedModManifestsOrder = []
    
    async def loadAllMods(self):
        for _, manifest in self.sortedModManifestsOrder:
            logger.debug(f"Loading mod from manifest {manifest}")
            await self.loadJSMod(manifest)
        return

    async def loadJSMod(self, manifest: ModManifest):
        # We're loading mods only on main view.
        from backend.server import Turnix
        view = Turnix.viewManager.getView("main", 0)
        await view.sendAndWait("loadJSMod", manifest.model_dump())
        #logger.debug(f"\n\nloadJSMod result={result}\n\n")

    async def sortModManifestsOrder(self):
        # Mod order goes from -9999 to 9999, with lower numbers first
        self.sortedModManifestsOrder = sorted(
            chain(self.modManifests.items(), self.modManifestsWithUnresolvedModIds.items()),
            key=lambda x: x[1].order
        )
        return self.sortedModManifestsOrder

    async def loadModManifest(self, manifestFile: Path) -> ModManifest:
        text = manifestFile.read_text(encoding="utf-8")
        
        if manifestFile.name.endswith(".json"):
            rawDict = json.loads(text)
        elif manifestFile.name.endswith(".yaml"):
            rawDict = yaml.safe_load(text)
        else:
            logger.error(f"Unknown file type for {manifestFile}")

        # { "turnix": { ... } }
        if "turnix" in rawDict: # Extract "turnix" property
            rawSubDict = rawDict["turnix"]
            # If some property is missing, try looking if it's in root and copy it.
            if rawSubDict.get("modId") is None:
                rawSubDict.setdefault("modId", rawDict.get("name", None))
            if rawSubDict.get("semver") is None:
                rawSubDict.setdefault("semver", rawDict.get("version", None))
            if rawSubDict.get("description") is None:
                rawSubDict.setdefault("description", rawDict.get("description", None))
            if rawSubDict.get("author") is None:
                rawSubDict.setdefault("author", rawDict.get("author", None))
            if rawSubDict.get("repository") is None:
                rawSubDict.setdefault("repository", rawDict.get("repository", None))
            if rawSubDict.get("dependencies") is None:
                rawSubDict.setdefault("dependencies", rawDict.get("dependencies", None))
            rawDict = rawSubDict
        
        rawDict["path"] = manifestFile.parent.as_posix()
        
        manifest = ModManifest.model_validate(rawDict)
        return manifest

    async def createEmptyModManifest(self, modFileNamePath: Path) -> ModManifest:
        rawDict = {
            "path": modFileNamePath.as_posix()
        }
        manifest = ModManifest.model_validate(rawDict)
        return manifest

    async def scanForAllMods(self, modsPath: Path | str):
        defaultExecutables = ["mod.py", "mod.js"]
        defaultManifests = ["manifest.yaml", "package.json", "manifest.json", "package.yaml"]

        if isinstance(modsPath, str):
            modsPath = Path(modsPath)

        for filename in defaultExecutables + defaultManifests:
            if (modsPath / filename).exists():
                logger.error(f"File {filename} found in {modsPath}. Mods should be placed in their own folder.")

        for path in modsPath.rglob("*"):
            if path.is_dir() and path != modsPath:
                manifest = None
                logger.debug(f"Scanning {path} for mods.")
                # Look for manifest file first
                for filename in defaultManifests:
                    manifestFile = path / filename
                    if manifestFile.exists():
                        
                        logger.debug(f"Found mod manifest {manifestFile}")
                        
                        manifest = await self.loadModManifest(manifestFile)
                        
                        logger.info(f"Loaded mod manifest: {manifest}")
                        
                        # Mods where we don't have modId goes to special dictionary
                        if manifest.modId is None:
                            if manifest.path not in self.modManifestsWithUnresolvedModIds:
                                self.modManifestsWithUnresolvedModIds[manifest.path] = manifest
                            else:
                                logger.error(f"Duplicate mod path {manifest.path} found while resolving mods!\n{self.modManifestsWithUnresolvedModIds[manifest.path].path}\n{manifest.path}\n{manifest.path} will not be loaded.")
                        else:
                            if manifest.modId not in self.modManifests:
                                self.modManifests[manifest.modId] = manifest
                            else:
                                logger.error(f"Duplicate modId {manifest.modId} found while resolving mods!\n{manifest.path}\n{self.modManifests[manifest.modId].path}\n{manifest.path} will not be loaded.")
                
                # If mod manifest file not found, look for executable file second
                if manifest is None:
                    for filename in defaultExecutables:
                        executableFile = path / filename
                        if executableFile.exists():
                            logger.debug(f"Found mod executable {executableFile}")
                            
                            manifest = await self.createEmptyModManifest(executableFile)
                            
                            logger.info(f"Loaded mod manifest: {manifest}")

                            if manifest.path not in self.modManifestsWithUnresolvedModIds:
                                self.modManifestsWithUnresolvedModIds[manifest.path] = manifest
                            else:
                                logger.error(f"Duplicate mod path {manifest.path} found while resolving mods!\n{self.modManifestsWithUnresolvedModIds[manifest.path].path}\n{manifest.path}\n{manifest.path} will not be loaded.")

        return
