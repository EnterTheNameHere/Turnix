**ModLogger Specification**

The `ModLogger` provides scoped logging utilities for Turnix mods. Each mod receives its own logger instance, automatically bound to its `modId`. All logs are timestamped, namespaced, and routed to the unified logging system.

---

## Logger Methods

| Method                 | Description                                              | Active In Prod |
| ---------------------- | -------------------------------------------------------- | -------------- |
| `debug(msg)`           | Low-level debug information.                             | Yes            |
| `log(msg)`             | Alias of `info(msg)`.                                    | Yes            |
| `info(msg)`            | General information about mod execution.                 | Yes            |
| `warn(msg)`            | Alias of `warning(msg)`.                                 | Yes            |
| `warning(msg)`         | Warning about deprecated or non-critical issues.         | Yes            |
| `error(msg)`           | Error during execution; mod continues running.           | Yes            |
| `exception(msg, err?)` | Logs a message with full stack trace of the exception.   | Yes            |
| `trace(msg)`           | Ultra-verbose internal debugging. Only logs in dev mode. | **No**         |

---

## `exception()` Behavior

- **Python**: Equivalent to `logger.exception()` in the standard logging module.
- **JavaScript**: Accepts an optional second argument (error object). If not provided, attempts to infer from the call stack.

All exceptions log:

- The provided message
- The exception name (e.g., `TypeError`)
- The exception message (e.g., `undefined is not a function`)
- A full stack trace

Example log object:

```json
{
  "mod": "author@coolmod",
  "level": "error",
  "message": "Something failed",
  "exception": {
    "name": "TypeError",
    "message": "Cannot read property 'x' of undefined",
    "stack": "at ..."
  },
  "timestamp": "2025-07-31T22:10:00Z"
}
```

---

## `trace()` Behavior

- Logs only when Turnix is in **developer mode**.
- Silently ignored in production builds.
- Can be used freely in mods without runtime cost in release mode.
- Internally mapped to `debug()` level with `[TRACE]` prefix.

---

## Integration

- Each `ModContext` has a `logger` bound to the current mod's `modId`.
- All logs are routed to Turnix's central logger.
- Mod log entries are prefixed: `mod.{modId}`.
- Logs may be streamed to frontend dev tools and/or mod manager UI.

---

## Example Usage

```python
ctx.logger.info("Mod activated")
ctx.logger.warning("Deprecated field in manifest")
ctx.logger.trace("Loading assets cache")

try:
    dangerous_operation()
except Exception:
    ctx.logger.exception("Failed to load resource")
```

```js
ctx.logger.info("UI button registered")
ctx.logger.trace("State sync started")

try {
  riskyCode()
} catch (err) {
  ctx.logger.exception("Script failed", err)
}
```

