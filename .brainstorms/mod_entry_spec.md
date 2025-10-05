**ModEntry Specification**

The `ModEntry` represents the **live, executable instance** of a mod entry (script/module). It is not passed directly to mods — instead, it is embedded inside the `ModContext`, which is the central object mods interact with.

---

## Purpose

- Encapsulates a single script/module entry point.
- Enables lifecycle function execution (`onActivate`, `onDeactivate`, etc.).
- Provides a direct callable interface for Turnix runtime.

---

## Location

`ModEntry` is a **member of `ModContext`**, under `ctx.entry`.

---

## Usage

Instead of wrapping or nesting the loaded module, `ModEntry` **is the loaded module** itself.

Example:
```python
ctx.entry.onActivate(ctx)
ctx.entry.onSessionCreated(ctx)
```

This avoids indirection (e.g., no `ctx.entry.module`) and ensures that lifecycle functions and custom handlers are called directly on the `ModEntry`.

---

## Benefits

- Cleaner and more intuitive access pattern
- Prevents duplication of metadata (already available in `ctx` and `ctx.manifest`)
- Keeps runtime interface flat and efficient

---

## Lifecycle

- Mod loader sets `ctx.entry` to the imported module object
- Runtime invokes lifecycle functions directly:

```python
ctx.entry.onActivate(ctx)
ctx.entry.onDeactivate(ctx)
```

Mods access metadata and helpers exclusively through `ctx`, not through `ModEntry`.

---

## Future Extensions (Optional)

- Reloading logic handled by replacing `ctx.entry` directly
- Optional wrapper could be added later if necessary for state tracking, but not by default

