TURNIX ROOT SYSTEM & DISCOVERY SPECIFICATION (PLAIN TEXT VERSION)
Status: Finalized model (2025-11)

Authority note: docs/pack-and-asset-resolution.txt holds the authoritative rules for pack discovery, PackMeta construction, and resolution invariants. This document describes the root layout and complements those rules without redefining them.

Scope: Root selection, directory semantics, discovery rules, write-permissions, and pack constraints.

OVERVIEW

Turnix resolves all filesystem-related paths through a layered root selection system.

Each root contains exactly five top-level directories:
first-party/
third-party/
custom/
userdata/
saves/

Only userdata/ and saves/ are writable during execution.

Pack discovery (mods, viewPacks, contentPacks, appPacks, savePacks) must cover these roots as defined by PackMeta registry rules:
first-party/
third-party/
custom/
saves/

userdata/ never contains pack manifests.

PRIORITY RULES FOR ROOT SELECTION

Roots are resolved in descending priority.

Priority 1 — Command-line flags
--root=<dir> defines root for all five directories
--userdata=<dir> overrides userdata path only
--saves=<dir> overrides saves path only

Rules:
--root is authoritative; if provided, it becomes the primary root.
--userdata and --saves override only their respective directories.
Selected directories are created if missing.
All required subdirectories inside the chosen --root must be created.

Priority 2 — Environment variable
TURNIX_ROOT=/absolute/path

Rules:
Must be absolute.
Created if missing.
Subdirectories created if missing.
Does not override command-line flags.
Adds a lower-priority root for pack discovery.

Priority 3 — Platform-standard directories
Examples:
Windows: %APPDATA%/Turnix/
Linux: ~/.local/share/turnix/
macOS: ~/Library/Application Support/turnix/

Rules:
Used only if the directory already exists.
Never created automatically.
Added as low-priority pack discovery roots.
Do not override userdata or saves if higher priority roots exist.

Priority 4 — Repo root (fallback)
Must exist.
Must contain all five directories.
Failure to meet these conditions results in a ReactorScramError.
Always included as the lowest-priority root.

DIRECTORY SEMANTICS

Writable vs read-only:
first-party/ read-only shipped packs
third-party/ read-only downloaded packs
custom/ read-only during execution (user workspace)
userdata/ writable global config/state across AppInstances
saves/ writable per-AppInstance save data

Reasons for prohibiting writes to -party and custom:
Prevents modification of shipped or downloaded packs.
Prevents corruption of reproducible packs.
Separates save data from authored content.
Keeps the authoring workflow explicit.

PACK DISCOVERY

Path traversal must never escape root boundaries.

Where packs may appear:
first-party/
third-party/
custom/
saves/

userdata/ never contains pack manifests.

saves/ primarily contains SavePacks. SavePacks may optionally contain copies of packs, which must override external versions when constructing the PackMeta registry.

Directory scanning rules:
For each root:
Scan only immediate child directories of first-party, third-party, custom.
If a directory contains a manifest.json5, treat it as a pack root.
Do not recurse under discovered pack roots.
Top-level root directory may not contain manifests.

Symlinks:
Controlled by roots.followSymlinks.
If enabled, the resolved path must stay inside an allowed pack directory.
Symlink loops must be rejected.

Pack manifest requirements:
Each manifest must declare kind, one of:
appPack
viewPack
mod
contentPack
savePack

The loader rejects missing or ambiguous kinds.
The kind defines loading rules and nesting allowed.
Pack id must follow naming rules in pack-manifest-structure.txt.

PACK TYPES AND NESTING

AppPack:
Defines an application or game.
Provides initialization logic to create an AppInstance.
May contain viewPacks and contentPacks.

ViewPack:
Defines UI and view-specific content.
Instantiable as a Turnix View (such as main or tracing-monitor).
May contain contentPacks.

Mod:
Provides backend or frontend logic.
Loaded by the mod loader.
May depend on other mods.

ContentPack:
Arbitrary assets or data.
May be nested inside AppPacks or ViewPacks.

SavePack:
Represents the saved state of an AppInstance.
Located under saves/<appPackId>/<appInstanceId>/.
May contain copied pack versions that override external ones.

Nesting model:
ContentPack may contain other ContentPacks or Mods.
ViewPack may contain ContentPacks.
AppPack may contain ViewPacks or ContentPacks.
SavePack may contain AppPacks, ViewPacks, or ContentPacks.

ASSET RESOLUTION AND ROUTING

Packs may contain code files, structured text, media files, binary files.

Assets are served through routes such as:
/packs/<packId>/assets/<assetPath>

Execution may load assets only from:
first-party/
third-party/
custom/
saves/<appPackId>/<appInstanceId>/
explicit allowlisted backend/frontend directories

Execution must never load arbitrary filesystem paths.

SAVING RULES

Global configuration:
Stored in <root>/userdata/
--userdata overrides the path.

AppInstance saves:
Stored in <root>/saves/<appPackId>/<appInstanceId>/
Packs copied inside SavePacks override external versions.

Never save into:
first-party/
third-party/
custom/

EFFECTIVE ROOT RESOLUTION SUMMARY

Pack discovery search order:

--root

TURNIX_ROOT

Platform-standard locations (only if turnix/ exists)

Repo root

Writable locations:
userdata/
saves/

No other directory is writable during execution.

INVARIANTS

No manifest may exist directly in a root directory.
The Turnix process must not write into shipped pack directories.
Pack discovery must never escape allowed directories.
PackMeta registry must always be fully built before resolution.
Repo root must contain all required directories.
Search paths are deterministic.
If a SavePack contains pack copies that conflict in version, the SavePack version wins.
