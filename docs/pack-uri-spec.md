# Turnix Pack URI and Resolution Specification

**Authority note:** `pack-manifest-structure.txt` is the canonical reference for manifest fields and their semantics. Identity, semantic versioning, and PackMeta/PackResolver behaviour continue to be covered in `pack-and-asset-resolution.txt`. This document only describes the URI surface and save-metadata examples. When in doubt, defer to the authoritative specs.

This document defines the addressing scheme for packs and resources in Turnix. It is written as if for an automatic code generator: every term is precise and every component is described with clear responsibilities and inputs/outputs.

The focus is on **what** must exist (interfaces, behaviours, invariants), not **how** it is implemented.

---

## 1. Design goals

1. **Stable identities for packs**  
   Every pack (application pack, view pack, mod, content pack, save pack) has a stable identifier that can be referenced in manifests and at runtime.

2. **Uniform URI scheme**  
   The same grammar is used to reference files and packs of different kinds, with a `scheme://author@id` prefix indicating how to interpret the reference.

3. **Deterministic resolution**  
   Given the same configuration, discovery set, and save metadata, the resolver must deterministically select the same concrete pack and version unless the user explicitly chooses to upgrade.

4. **Support for embedded and global packs**  
   Packs may embed other packs (for example, an application pack containing local user interface mods), and there are also globally installed packs. Resolution must consider both scopes in a predictable way.

5. **Upgradeable but backwards-safe saves**  
   Saves remember both:
   - the original semantic request (for example, `listbox:^1.0.0`) and
   - the exact resolved pack (for example, `mod://Jan@listbox:1.1.0`),
   allowing controlled upgrades with rollback.

---

## 2. Core concepts and glossary

These terms are used consistently throughout the system.

### 2.1 Pack kinds

A **pack** is a directory with a manifest and additional files. Pack kinds include:

- `appPack` – an application bundle that can create and own runtime instances and saves.
- `viewPack` – a bundle that provides one or more debugging or auxiliary views.
- `mod` – a reusable functionality unit attached to an application or view.
- `contentPack` – optional: data-only or mostly-data packs.
- `savePack` – optional conceptual kind for per-save resources (often represented by a directory under `saves/`).

### 2.2 Manifest fields (common)

Manifest fields mirror the PackMeta inputs described in `pack-manifest-structure.txt`:

```json5
{
  "kind": "appPack" | "viewPack" | "mod" | "contentPack" | "savePack",
  "author": "Turnix",           // declaredAuthor; may be inherited by children
  "id": "main-menu",            // PackLocalId; must not contain '@', '.' or semver
  "name": "Main Menu",          // optional user-facing name (defaults to id)
  "version": "1.0.0",           // declaredSemVerPackVersion
  "visibility": "public",       // optional; see pack-manifest-structure.txt for defaults
  "importFromParent": false       // optional; viewPack defaults to false, others true}
```

Important notes:

- `id` is the **stable, local slug** that feeds into the hierarchical `packTreeId` built during discovery.
- `name` is purely for user interfaces and may contain spaces, capitalization, etc.; omit to fall back to `id`.
- `kind` drives how the pack is interpreted and where it is stored on disk.
- Visibility and dependency inheritance (`importFromParent`) follow the defaults described in `pack-manifest-structure.txt`.
- The effective author and version may be inherited from a parent pack as defined in the PackMeta rules.

### 2.3 Source kinds

Each pack additionally belongs to a **source kind**, derived from its author and installation location:

- `first-party` – maintained by Turnix itself (usually `author == "Turnix"`).
- `third-party` – installed from external sources, organized by author.
- `custom` – user-created or local development packs.
- `save` – per-save generated data (for example, generated application packs or embedded mods inside saves).

Source kind is used for filesystem layout and for decisions about mutability and overwrite rules.

### 2.4 Pack identity and selectors

- **Pack identity**: `(effectiveAuthor, packTreeId)`
  - `packTreeId` is the dotted hierarchy (`ui.trace.trace-view`) derived from manifest `id` values; see `pack-and-asset-resolution.txt` for construction rules.

- **Pack reference string (PackRefString / PackRequest)**: a textual reference that may be partial and requires
  resolution:
  ```text
  <author?>@<packTreeId>[@<SemVerPackRequirement>]
  ```

  where `author` and version requirement are optional wildcards. Variant IDs are not part of the authoritative spec and should be handled separately if retained for tooling compatibility.

- **Resolved pack id (ResolvedPackId)**: a canonical, fully-resolved reference that includes an exact version and scheme:

  ```text
  <scheme>://<authorId>@<packTreeId>:<SemVerPackVersion>
  ```

  Examples:
  - `appPack://Turnix@main-menu:1.2.0`
  - `viewPack://Turnix@trace-monitor:1.0.0`
  - `mod://Enter@ui.trace.listbox:1.0.0`
  - `mod://Jan@ui.trace.listbox:1.1.0`

> **Compatibility note:** Older references to `packId` in this document should be interpreted as the
> `packTreeId` root segment. Where dotted `packTreeId` values appear, map each segment to a directory level
> when constructing file-system paths.

### 2.5 Resource URIs

A **resource URI** references a file inside a pack or directory:

```text
<scheme>://<authorId?>@<packTreeIdOrDir>[@<SemVerPackRequirement>][/inner/path...]
```

Examples:

- `file://Turnix@config/defaults/global.json5`
- `appPack://Turnix@main-menu/saves/manifest.json5`
- `mod://Turnix@toast/toast.js`
- `viewPack://Turnix@trace-monitor/trace-monitor.js`

Where:

- `scheme` identifies the resolution strategy:
  - `file` – direct mapping to `first-party` configuration and similar files.
  - `appPack` – application packs.
  - `viewPack` – view packs.
  - `mod` – mods.
  - More schemes can be added later (`contentPack`, `savePack`, etc.).
- `inner/path...` is an internal path relative to the resolved pack root (if applicable).

---

## 3. URI grammar and mapping rules

### 3.1 General grammar

The general grammar for resource URIs is:

```text
<scheme>://<authorId?>@<packTreeIdOrDir>[@<SemVerPackRequirement>][/inner/path...]
```

Where:

- `scheme` is a lowercase identifier (no spaces; no `://` inside).
- `authorId` is taken from the manifest `author` field and is optional in requests but mandatory in canonical URIs.
- `packTreeIdOrDir` is either:
  - the dotted `packTreeId` of a pack (for pack-ish schemes), or
  - a top-level directory (`config`, etc.) for `file://`.
- `SemVerPackRequirement` follows the semantics from `pack-and-asset-resolution.txt`.
- `inner/path` is optional. When omitted, the root of the pack is referenced.

### 3.2 Scheme-specific mapping rules

#### 3.2.1 `file://` scheme

Used for first-party configuration and similar plain files.

Example:

```text
file://Turnix@config/defaults/global.json5
```

Mapping:

```text
<root>/first-party/config/defaults/global.json5
```

Resolution rules for `file://`:

1. `authorId == "Turnix"`:
   - Base directory: `<root>/first-party/`.
   - `packIdOrDir` is used as the top-level directory.
2. For other authors, optional extension (if needed in the future):
   - `<root>/third-party/config/<authorId>/...` or a similar pattern.

No semantic pack resolution is performed; `file://` is primarily a convenience for first-party configuration files and similar resources.

#### 3.2.2 `appPack://`, `viewPack://`, `mod://`

All pack-based schemes follow the same mapping pattern.

First-party packs:

```text
appPack://Turnix@main-menu
-> <root>/first-party/appPacks/main-menu/

viewPack://Turnix@trace-monitor
-> <root>/first-party/viewPacks/trace-monitor/

mod://Turnix@toast
-> <root>/first-party/mods/toast/
```

Third-party packs (versioned by author):

```text
mod://Enter@listbox:1.0.0
-> <root>/third-party/mods/Enter/listbox/1.0.0/

mod://Jan@listbox:1.1.0
-> <root>/third-party/mods/Jan/listbox/1.1.0/
```

The resolver combines:

- `scheme` → pack `kind` and base directory name (`appPacks`, `viewPacks`, `mods`, etc.).
- `authorId` and `packId` → path segments (for third-party).
- `versionSpec` → resolved to an exact version; used as an extra directory level where appropriate.

#### 3.2.3 Source kind and mutability

Source kind controls mutability rules:

- `first-party` directories under `<root>/first-party/` are treated as **read-only at runtime**.
- `third-party` directories under `<root>/third-party/` are also treated as **read-only** except for
  controlled updates (installer or package manager).
- `custom` and `save` directories are considered writable by the engine and tools.

Any generator or runtime code attempting to write into a read-only directory must raise a controlled exception, which may be caught and handled by higher-level logic (for example, migrating writes into `saves/` or `userdata/`).

---

## 4. Filesystem layout

The following layout is the reference target state.

### 4.1 First-party layout

```text
<root>/first-party/appPacks/<packId>/...
<root>/first-party/viewPacks/<packId>/...
<root>/first-party/mods/<packId>/...
<root>/first-party/contentPacks/<packId>/... (optional)
```

Concrete example from the main-menu and 100floors scenario:

```text
<root>/first-party/appPacks/100floors/manifest.json5
<root>/first-party/appPacks/100floors/generator.js
<root>/first-party/appPacks/100floors/src/100floors.js

<root>/first-party/appPacks/main-menu/manifest.json5
<root>/first-party/appPacks/main-menu/generator.js
<root>/first-party/appPacks/main-menu/mods/main-menu-ui/manifest.json5
<root>/first-party/appPacks/main-menu/mods/main-menu-ui/main-menu-ui.js
<root>/first-party/appPacks/main-menu/mods/main-menu-ui/template/css/style.something
<root>/first-party/appPacks/main-menu/mods/main-menu-ui/template/html.something

<root>/first-party/viewPacks/trace-monitor/manifest.json5
<root>/first-party/viewPacks/trace-monitor/trace-monitor.js

<root>/first-party/mods/toast/manifest.json5
<root>/first-party/mods/toast/toast.js

<root>/first-party/mods/ui/manifest.json5
<root>/first-party/mods/ui/button.js
<root>/first-party/mods/ui/textarea.js
<root>/first-party/mods/ui/messageslist.js
<root>/first-party/mods/ui/dialog.js
<root>/first-party/mods/ui/menu.js
```

### 4.2 Third-party layout

Third-party packs are organized by author and version:

```text
<root>/third-party/appPacks/<authorId>/<packId>/<version>/...
<root>/third-party/viewPacks/<authorId>/<packId>/<version>/...
<root>/third-party/mods/<authorId>/<packId>/<version>/...
```

Example:

```text
<root>/third-party/mods/Enter/listbox/1.0.0/manifest.json5
<root>/third-party/mods/Enter/listbox/1.0.0/listbox.js

<root>/third-party/mods/Jan/listbox/1.1.0/manifest.json5
<root>/third-party/mods/Jan/listbox/1.1.0/listbox.js
```

This layout allows multiple versions of a pack to coexist.

### 4.3 Saves and userdata

While not fully specified here, the expected pattern for saves is:

```text
<root>/saves/<appPackId>/<runtimeInstanceId>/...
```

Each save directory may itself be treated as a `savePack` with a manifest that records:

- original pack selectors,
- resolved pack ids,
- runtime instance identifiers,
- other metadata.

User-global configuration and data can live under:

```text
<root>/userdata/...
```

---

## 5. Resolution pipeline

The resolution pipeline is organized into several conceptual components that Codex (or any code generator) can target as separate services or modules.

### 5.1 PackSelectorParser

**Input:** string of the form `<author?>@<packTreeId>[@<SemVerPackRequirement>]`
**Output:** structured selector object compatible with `PackRequest`:

```ts
type PackSelector = {
  authorId?: string;        // optional wildcard
  packTreeId: string;       // dotted hierarchy built from manifest ids
  semverRequirement?: string; // semver range per pack-and-asset-resolution
}
```

Responsibilities:

- Parse a selector string into fields.
- Enforce syntax rules (characters allowed in `authorId`, `packTreeId`).
- Reject invalid or ambiguous strings with clear error messages.

### 5.2 PackDiscoveryIndex

**Input:** list of root directories (first-party, third-party, custom, saves)  
**Output:** in-memory index of available packs.

The index stores entries like:

```ts
type PackIndexEntry = {
  sourceKind: "first-party" | "third-party" | "custom" | "save";
  kind: "appPack" | "viewPack" | "mod" | "contentPack" | "savePack";
  authorId: string;
  packTreeId: string;
  exactVersion: string;
  variantId?: string;
  rootPath: string;  // filesystem path to pack root
  manifestPath: string;
}
```

Responsibilities:

- Walk the directories under `<root>/first-party`, `<root>/third-party`, `<root>/custom`, and `saves/`.
- Detect manifests (`manifest.json5`, `manifest.json`) and parse kind, author, `id`, version, etc., building the dotted `packTreeId` during traversal.
- Build a lookup structure keyed by `(kind, authorId, packTreeId)` and full `(kind, authorId, packTreeId, exactVersion)`.

### 5.3 VersionChooser

**Input:**

- `versionSpec` (may be undefined),
- list of candidate exact versions for the given pack.

**Output:** one chosen `exactVersion`.

Responsibilities:

- If `versionSpec` is undefined:
  - Use a default policy (usually highest available version).
- If `versionSpec` is defined:
  - Filter candidates that satisfy the constraint (`^1.0.0`, `>=1.0.0 <2.0.0`, etc.).
  - Choose the highest matching version.
- If no candidate matches:
  - Return an error; the higher level will decide whether to abort or provide a fallback.

### 5.4 ScopeResolver (local vs global)

Packs can be embedded inside other packs (for example, UI mods in an application pack). The ScopeResolver determines where to look first.

For a requesting pack `P` wanting a dependency with selector `S`:

Search order:

1. **Local scope** of `P`  
   For example, for `appPack://Turnix@main-menu`, check:

   ```text
   <root>/first-party/appPacks/main-menu/mods/<packId>/manifest.json5
   ```

   If found, treat this as a pack with:
   - `authorId` inherited from `P` unless overridden in the embedded manifest.
   - `packId` from the embedded manifest or directory name.

2. **Global scope**  
   If not found locally, use the `PackDiscoveryIndex` to find global candidates among first-party, third-party, and custom packs.

The ScopeResolver returns a set of candidate `PackIndexEntry` objects to pass into `VersionChooser`.

### 5.5 PackResolver

**Input:**

- `scheme` (determines `kind`)
- `PackSelector` (authorId may be missing)
- requesting context (for example, requesting pack identity for local scope)

**Output:** `ResolvedPackId` and `PackIndexEntry`

Responsibilities:

1. Ensure `authorId` is known. If missing:
   - In local scope, use the author of the requesting pack.
   - In global scope, if multiple authors exist for the same `packId`, this is an error unless disambiguated by configuration or user choice.
2. Use `ScopeResolver` to gather candidate packs by `(kind, authorId, packId)`.
3. Use `VersionChooser` to pick an exact version.
4. Return a canonical `ResolvedPackId`:

   ```text
   <scheme>://<authorId>@<packId>:<exactVersion>[:<variantId>]
   ```

   along with the corresponding `PackIndexEntry` (which contains the filesystem paths).

### 5.6 ResourceUriResolver

**Input:** resource URI string  
**Output:** filesystem path to a file or directory

Responsibilities:

1. Parse the URI into:
   - `scheme`
   - `authorId`
   - `packIdOrDir`
   - `versionSpec`
   - `variantId`
   - `innerPath`

2. If `scheme == "file"`:
   - Map directly to `<root>/first-party/<packIdOrDir>/<innerPath>` when `authorId == "Turnix"`.
   - Optionally support extension to other authors in `third-party` or `custom`.

3. If `scheme` is a pack scheme (`appPack`, `viewPack`, `mod`, `contentPack`, `savePack`):
   - Derive pack `kind` from `scheme`.
   - Treat `packIdOrDir` as `packId`.
   - Construct a `PackSelector` from `authorId`, `packId`, `versionSpec`, `variantId`.
   - Use `PackResolver` to get `PackIndexEntry`.
   - Return `PackIndexEntry.rootPath` combined with `innerPath` (if any).

---

## 6. Save manifests and upgrade behaviour

Saves must store both the semantic intent and the concrete resolution of dependencies.

### 6.1 Save metadata structure

Example metadata snippet inside a save manifest:

```json5
{
  "appPack": "appPack://Turnix@100floors:1.0.0",
  "requestedMods": {
    "ui": "ui:^1.0.0",
    "listbox": "listbox:^1.0.0"
  },
  "resolvedMods": {
    "ui": "mod://Turnix@ui:1.0.0",
    "listbox": "mod://Enter@listbox:1.0.0"
  }
}
```

Notes:

- `requestedMods` stores **PackSelectors** as originally requested by manifests (or `generator.js` logic).
- `resolvedMods` stores the exact **ResolvedPackId** chosen at the time of save creation or last upgrade.

### 6.2 Load-time resolution and upgrade checks

When loading a save:

1. For each entry in `requestedMods`:
   - Use the corresponding selector (for example `listbox:^1.0.0`).

2. Use `PackResolver` to determine the **current best candidate** based on the available packs.

3. Compare with the stored `resolvedMods` entry:
   - If they are identical, load that pack directly.
   - If they differ (for example, a newer version is now available that still satisfies `^1.0.0`):
     - Prompt the user whether to upgrade to the new version.
     - If user chooses to upgrade:
       - Attempt to load the new pack and run any validation.
       - If it is successful, update `resolvedMods` in the save metadata and create a backup of the old state.
       - If it fails in a detectable way, revert to the previous version and inform the user.

4. If the pack referenced in `resolvedMods` no longer exists:
   - This is an error; the save is no longer directly reproducible with the current installation.
   - The system may offer to re-resolve from `requestedMods`, but must clearly indicate that behaviour is no longer guaranteed identical.

---

## 7. Worked examples

### 7.1 Main menu bootstrap

Default startup sequence when no `--loadSave` and no `--createAppPack` are provided:

1. Try to load:

   ```text
   appPack://Turnix@main-menu/saves
   ```

2. `ResourceUriResolver` maps this to:

   ```text
   <root>/first-party/appPacks/main-menu/saves/manifest.json5
   ```

3. If this manifest does not exist:
   - Fallback to `appPack://Turnix@main-menu`.
   - Load `<root>/first-party/appPacks/main-menu/manifest.json5`.
   - Read fields such as:

     ```json5
     {
       "kind": "appPack",
       "author": "Turnix",
       "id": "main-menu",
       "displayName": "Main Menu",
       "version": "1.0.0",
       "defaultRuntimeInstanceId": "turnix-main-menu",
       "overrideSaveUri": "appPack://Turnix@main-menu/save/",
       "mods": {
         "main-menu-ui": "^1.0.0",
         "toast": "^1.0.0"
       }
     }
     ```

   - Run `generator.js` to create a save (or a `savePack`) and initialize `resolvedMods` based on the manifest and discovered packs.
   - Respect read-only rules on `<root>/first-party` paths and redirect actual writable output to `saves/` or `userdata/` as needed, while still maintaining logical `overrideSaveUri` semantics.

4. Once the save is created, load it using the `savePack` semantics and normal resolution rules.

### 7.2 100floors uses `ui` mod

`100floors` application pack manifest:

```json5
{
  "kind": "appPack",
  "author": "Turnix",
  "id": "100floors",
  "displayName": "100floors",
  "description": "A 100 floors dungeon example.",
  "version": "1.0.0",
  "runtime": {
    "javascript": {
      "generator": "generator.js",
      "entry": "src/100floors.js"
    }
  },
  "mods": {
    "ui": "^1.0.0"
  }
}
```

`ui` mod manifest:

```json5
{
  "kind": "mod",
  "author": "Turnix",
  "id": "ui",
  "displayName": "Basic UI",
  "description": "Provides basic UI for application packs.",
  "version": "1.0.0",
  "runtimes": {
    "javascript": {
      "entries": [
        "button.js",
        "textarea.js",
        "menu.js",
        "dialog.js",
        "messageslist.js"
      ]
    }
  }
}
```

Resolution flow for `"ui": "^1.0.0"`:

1. Parse selector: `packId = "ui"`, `versionSpec = "^1.0.0"`, no explicit author.
2. ScopeResolver:
   - Local scope in 100floors: no embedded `ui` mod.
   - Global scope: candidates from `PackDiscoveryIndex`:
     - `mod://Turnix@ui:1.0.0`
3. VersionChooser:
   - Only one candidate, version `1.0.0`, which matches `^1.0.0`.
4. PackResolver returns:
   - `ResolvedPackId = "mod://Turnix@ui:1.0.0"`.
5. Save metadata records:
   - `requestedMods["ui"] = "ui:^1.0.0"`
   - `resolvedMods["ui"] = "mod://Turnix@ui:1.0.0"`

### 7.3 trace-monitor and listbox versions

`trace-monitor` view pack manifest:

```json5
{
  "kind": "viewPack",
  "author": "Turnix",
  "id": "trace-monitor",
  "displayName": "Trace Monitor",
  "version": "1.0.0",
  "mods": {
    "ui": "^1.0.0",
    "listbox": "^1.0.0"
  }
}
```

Listbox mod manifests:

```json5
// Enter's listbox
{
  "kind": "mod",
  "author": "Enter",
  "id": "listbox",
  "displayName": "listbox",
  "description": "Scrollable list of items.",
  "version": "1.0.0"
}

// Jan's listbox v2
{
  "kind": "mod",
  "author": "Jan",
  "id": "listbox",
  "displayName": "listbox v2",
  "description": "Buffed listbox with filters.",
  "version": "1.1.0",
  "extends": "Enter@listbox" // optional inheritance metadata
}
```

When first loading trace-monitor in a world where only Enter's listbox exists:

1. Selector: `"listbox": "^1.0.0"`.
2. ScopeResolver → candidates:
   - `mod://Enter@listbox:1.0.0`.
3. VersionChooser → choose `1.0.0`.
4. Save metadata:
   - `requestedMods["listbox"] = "listbox:^1.0.0"`
   - `resolvedMods["listbox"] = "mod://Enter@listbox:1.0.0"`

Later, Jan's `listbox:1.1.0` is installed:

1. On save load, the engine again evaluates `requestedMods["listbox"]`:
   - Candidates now:
     - `mod://Enter@listbox:1.0.0`
     - `mod://Jan@listbox:1.1.0`
2. VersionChooser:
   - Both satisfy `^1.0.0`.
   - Policy chooses the highest version: `1.1.0` (Jan's).
3. Because this differs from stored `resolvedMods["listbox"]` (`Enter@listbox:1.0.0`):
   - Prompt the user to upgrade.
   - On acceptance, attempt to load Jan's listbox.
   - If successful, update `resolvedMods["listbox"]` to `mod://Jan@listbox:1.1.0` and create a backup of the previous save state.

This behaviour ensures that saves can evolve to newer compatible versions while still allowing explicit control and rollback.

---

## 8. Summary of components for implementation

For code generation or manual implementation, the following main components are required:

1. **PackSelectorParser**
   - Parses `<author?>@<packTreeId>[@<SemVerPackRequirement>]` into structured data compatible with `PackRequest`.

2. **PackDiscoveryIndex**
   - Discovers packs on disk.
   - Indexes them by `kind`, `authorId`, `packTreeId`, and `exactVersion`.

3. **VersionChooser**
   - Given a version spec and a list of exact versions, chooses the best match or reports failure.

4. **ScopeResolver**
   - Implements local-then-global search for packs embedded inside other packs.

5. **PackResolver**
   - Combines selector parsing, scope resolution, and version choice to produce a `ResolvedPackId` and a concrete pack entry.

6. **ResourceUriResolver**
   - Handles complete URIs (scheme + author + packId/directories + inner paths) and returns filesystem paths.

7. **SaveManifestManager**
   - Writes and reads `requestedMods` and `resolvedMods` sections in save manifests.
   - Provides helpers to iterate over requested dependencies.

8. **UpgradeManager**
   - Compares stored `resolvedMods` with current resolution results.
   - Decides when to prompt for upgrade.
   - Applies upgrades and manages backups.

All higher-level behaviour (application bootstrapping, generator execution, runtime instance management) is
expected to use these components rather than bypass them, to keep behaviour consistent across Turnix.
