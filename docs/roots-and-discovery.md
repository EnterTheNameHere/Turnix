# Turnix Root System & Discovery Specification

**Status:** Finalized model (2025-11)

**Scope:** Root selection, directory semantics, discovery rules, write-permissions, and pack constraints.

## 1. Overview

Turnix resolves all filesystem-related paths through a layered *root selection system*.

Each root contains exactly **five** top-level directories:

- `first-party/`
- `third-party/`
- `custom/`
- `userdata/`
- `saves/`

Only `userdata/` and `saves/` are writable at runtime.

All pack discovery (mods, viewPacks, contentPacks, appPacks) happens inside these three roots:
- `first-party/`
- `third-party/`
- `custom/`

`userdata/` and `saves/` **never** contains pack manifests.

## 2. Priority Rules for Root Selection

Roots are resolved in descending priority:

### Priority 1 — Command-line flags

```ini
--root=<dir>       # defines root for all 5 directories  
--userdata=<dir>   # overrides userdata path only
--saves=<dir>      # overrides saves path only
```
Rules:
- `--root=` is authoritative; if provided, it becomes primary root.
- `--userdata=` and `--saves=` override only their respective directories, even if `--root` is set.
- When a directory is chosen, create it if it does not exist.
- Create all required subdirectories inside `--root`.

### Priority 2 — Environment variable

```ini
TURNIX_ROOT=/absolute/path
```
Rules:
- Must be **absolute path**.
- If directory doesn't exist → create it.
- If subdirectories(`first-party`, `third-party`, `custom`, `userdata`, `saves`) don't exist → create them.
- Does **not override** any directory controlled by command-line flags.
- Contributes an additional root for discovery (lower priority than CLI).

### Priority 3 — Platform standard directories

Example locations:
- Windows: `%APPDATA%/Turnix/`
- Linux: `~/.local/share/turnix/`
- macOS: `~/Library/Application Support/turnix/`

Rules:
- Only used if a directory named `turnix/` already exists.
- Never created by Turnix automatically.
- Contribute lower-priority discovery roots.
- Do not override userdata/saves if higher-priority values exist.

> **Note:** Multiple platform-standard directories may exist (for example, both `%APPDATA%/Turnix/` and
> `%LOCALAPPDATA%/Turnix/`). Each of them becomes its own discovery root, ordered after environment
> variables and before the repo root. Turnix never creates these directories automatically.

### Priority 4 — Repo root (fallback)

- Must exist.
- Must contain all five directories.
  If not → **ReactorScramError**.
- Always included as the lowest-priority root.

## 3. Directory Semantics

### 3.1 Writable vs. Read-only:
| Directory | Writable at runtime | Purpose |
| :--- | :---: | :--- |
| `first-party/` | No | Turnix-shipped packs |
| `third-party/` | No | Downloaded packs (mods/content/view/app) |
| `custom/` | No | User project workspace; editable by user, but runtime does not write |
| `userdata/` | Yes | Global non-app-specific config/state |
| `saves/` | Yes | Per-app save game data |

### 3.2 *Why not write to -party or custom?*
- Prevents overwriting shipped or downloaded content.
- Prevents authoring mistakes during runtime.
- Keeps mod reproducibility.
- Makes "developer workspace" manual rather than implicit.
- Saves and global config have well-defined locations.

## 4. Pack Discovery

Path traversal must never escape root boundaries.

### 4.1 Where packs may appear

Packs (mods, viewPacks, contentPacks, appPacks) are discoverable only in subdirectories of:
- `first-party/`
- `third-party/`
- `custom/`

`userdata/` never contains pack manifests.

`saves/` contains **only SavePacks**.

A SavePack may contain **copies** of mods/content/view/app packs. If present, these overrides must be used to resolve pack versions for that save.

### 4.2 Directory scanning rules

For each root:

#### 1. **Scan only the immediate subdirectories of the pack directories**, e.g.:

```bash
first-party/*
third-party/*
custom/*
```

#### 2. If a subdirectory contains a recognized manifest (e.g. `manifest.json5`), treat it as a **pack root**.

#### 3. **Stop recursion** below a discovered pack root – internal sub-folders are left to the loader of that pack.

#### 4. No manifests are allowed in top-level directory.

### 4.3 Symlinks

Symlinks may be **allowed or forbidden** depending on Turnix configuration (`roots.followSymlinks`).

When allowed:
- **"resolved path MUST remain inside one of the allowed pack"**
- **"symlink loops must be detected and rejected"**

### 4.4 Pack Manifest Requirements

Every pack manifest must explicitly declare its **pack type**. Valid values are:

- `appPack`
- `viewPack`
- `mod`
- `contentPack`
- `savePack`

Rules:
- `type` must appear at the top level of the manifest.
- The value must be exactly one of the permitted pack types.
- The loader must reject missing or ambiguous values.
- The declared `type` determines how the pack is loaded and which nesting rules apply.

## 5. Pack Types and Nesting

Turnix defines the following types of packs:

#### 5.1 **AppPack**
- Defines a full application or game.
- Provides initialization logic for creating a runtime.
- May contain viewPacks and contentPacks.

#### 5.2 **ViewPack**
- Defines UI and view-specific content.
- Instantiable as a Turnix `View` (`main`, `tracing-monitor`, `vtuber-rig`, etc.)
- May contain contentPacks.

#### 5.3 **Mod**
- Functional code providing backend or frontend logic.
- Can depend on other mods.
- Loaded through the mod manager/loader.

#### 5.4 **ContentPack**
- Arbitrary assets or data
- May be nested inside AppPack or ViewPack.

#### 5.5 **SavePack**
- Defines saved state of active `RuntimeInstance`.
- Expected to be found at `saves/` directory, under `<appPackId>` and `<runtimeInstanceId>` sub-directories.
- Can contain copies of packs copied during appPack initialization, in which case it should load those packs with higher priority for compatibility.

**Nesting model**:
```
ContentPack := ContentPack | Mod (multiple allowed)

ViewPack := ContentPack (multiple)

AppPack := ViewPack | ContentPack (multiple)

SavePack := AppPack | ViewPack | ContentPack (multiple)
```

## 6. Asset Resolution and Routing

Files inside any pack can include:
- Code (`.py`, `.js`, etc.)
- UTF-8 text (`json`, `json5`, `jsonl`, `txt`, etc.)
- Media files (images, audio, video, etc.)
- Specialized formats (sqlite, embeddings, etc.)

Turnix exposes then via FastAPI routes such as:
`/packs/<packId>/assets/<assetPath>`

Runtime code may load only from:
- `first-party/`
- `third-party/`
- `custom/`
- `saves/<appPackId>/<runtimeInstanceId>/` - if packs are copied inside it
- explicit "allowlisted" code directories like `/backend/` or `/frontend/`

Runtime code must **never load arbitrary paths**.

## 7. Saving Rules

### 7.1 Global configuration

Always saved to:
```bash
<root>/userdata/
```
If `--userdata=` is used, that wins over all others. This folder never contains packs.

### 7.2 Game/App saves

Saved to:
```bash
<root>/saves/<appPackId>/<runtimeId>/
```

If `--saves=` is used, that wins over all others. If pack copies exist inside the SavePack, they take **priority** over any external version for this runtime instance.

### 7.3 Never save anything into
- `first-party/`
- `third-party/`
- `custom/`

Creator workflows can use the filesystem manually or through authoring tools, but runtime **never** writes there.

## 8. Effective Root Resolution Summary

Runtime loads from directories in the following order:
```markdown
1. --root
2. TURNIX_ROOT
3. platform standard directories (if `turnix/` exists there)
4. repo-root
```

Runtime priority for **writing** (only one):
```markdown
Writes allowed only in:
<effective-userdata>/
<effective-saves>/
```

## 9. Invariants (Hard Rules)

- No manifest may exist in the root directory itself.

  Only **inside** subdirectories of `first-party/`, `third-party`, `custom/`.

- Runtime **must not** write into shipped pack directories.

  Use `userdata/` or `saves/<appPackId>/<runtimeInstanceId>/` instead.

- Pack scanning or loading must never traverse outside the permitted directories, including symlinks.
- Repo-root must contain all 5 directories (`first-party/`, `third-party`, `custom/`, `userdata/`, `saves/`) or launch fails.
- Effective search path order is always deterministic.
- If a SavePack contains a pack copy whose version conflicts with first/third/custom packs, the SavePack version must win.
