**ModContext Specification**

The `ModContext` is the **central runtime interface** between Turnix and each loaded mod. It encapsulates everything a mod needs to access: metadata, entry point, logging, state, and lifecycle utilities.

All mod interaction — including lifecycle callbacks, state storage, and hook registration — occurs via `ModContext`.

---

## 🧩 Core Fields

| Field      | Type             | Description                                                |
| ---------- | ---------------- | ---------------------------------------------------------- |
| `modId`    | `str`            | Unique identifier for the mod (e.g., `author@modname`)     |
| `manifest` | `ModManifest`    | The full manifest describing the mod's metadata            |
| `entry`    | `ModEntry`       | The imported and callable mod module (e.g., Python module) |
| `logger`   | `ModLogger`      | Logger scoped to `mod.{modId}`                             |
| `state`    | `dict` or object | Mod-managed state container                                |

---

## 🔧 Utilities

| Method                                              | Description                                          |
| --------------------------------------------------- | ---------------------------------------------------- |
| `registerHook(stage, handler, sessionId?, config?)` | Register a handler to a pipeline stage for a session |
| `unregisterHook(stage, handler, sessionId?)`        | Remove a previously registered handler               |
| `addError(message)`                                 | Log an error and record it in manifest.errors        |
| `addWarning(message)`                               | Log a warning and record it in manifest.warnings     |

All errors and warnings are timestamped automatically.

---

## 🧠 Persistence and Responsibility

Mods that wish to survive reloads, timeouts, crashes, or full save/load cycles must use the provided `ModContext` features for all persistent logic.

- **Module-level memory is not persistent.**
- If a mod stores state in global variables, it will be lost upon reload or crash.
- Only `ctx.state` and other registered or serialized structures will be rehydrated.

> If something fails, it’s our fault.\
> If something recovers, it’s thanks to our framework.

---

## 🧪 Development Notes

- All core mod functions (e.g., `onActivate`, `onSessionCreated`) receive `ModContext` as their first and only argument.
- Context is constructed by the Turnix loader at runtime.
- Turnix owns `ModContext` and may reinstantiate or patch it during live sessions.

---

## 🔒 Design Goals

- **Unified access**: All metadata, tools, and lifecycle methods exposed via one object
- **Reloadable**: Decouples mod entry and persistent state
- **Safe & inspectable**: Clearly structured for diagnostics and debugging

---

## 🔄 Example

```python
def onActivate(ctx):
    ctx.logger.info("Mod is activating")
    ctx.state["count"] = ctx.state.get("count", 0) + 1
```

```python
ctx.entry.onActivate(ctx)
ctx.entry.onSessionCreated(ctx)
```

---

## 📎 Future Extensions

- `ctx.reload()` — Force hot-reload of this mod
- `ctx.disable()` — Temporarily deactivate mod
- `ctx.meta` — Derived properties and dependency info

