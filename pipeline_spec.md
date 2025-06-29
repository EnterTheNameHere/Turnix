# Turnix Pipeline Execution Specification

## Purpose

This document describes the full lifecycle of a user interaction in Turnix that initiates a main pipeline execution. It outlines each pipeline stage, the responsibilities of mods, the structure of data flow, and how the Turnix core components (View, Game, Session, Pipeline, Driver) interact to bring time forward in a running game.

---

## 📦 Components

### Game

- The current world instance.
- Owns:
  - Persistent memory
  - Active mod list
  - Views attached to it
  - A running `MainSession` (or `None` if idle)

### Session (Execution Context)

- Base class for all reasoning contexts.
- Owns active pipelines and determines hook visibility.
- Variants:

#### `MainSession`

- Drives gameplay and narrative progression.
- Persistent throughout game lifetime.
- Notifies all registered mods.
- Shared mod memory and full UI visibility.

#### `TemporarySession`

- Used for on-demand internal pipelines (e.g., complex logic, AI summarization).
- Notifies all mods.
- Lives only for one run.

#### `HiddenSession`

- Isolated sub-process triggered by mod.
- Does not notify global hooks unless explicitly allowed.
- Useful for debug tools, lore fetchers, or lightweight analysis.
- May optionally appear in debug logs.

### View

- Represents a UI tab or window.
- Can initiate session-bound pipeline calls.
- Has its own mod memory space and hook registrations.

### Pipeline

- A full reasoning execution triggered by a player action (e.g., message in chat).
- Goes through 7 hook stages.
- Always belongs to a Session.

### Driver

- An engine that processes the request (LLM, TTS, etc.).
- E.g., `llmDriver.sendRequest(...)` sends final prompt and returns generated content.

---

## 🔁 Example Flow: Player Sends Chat Message (LLM Pipeline)

### 0. Trigger (User Action)

- View captures input from user in a chat textbox.
- Calls:
  ```python
  game.sendMessageFromView(viewId, message)
  ```
- Game triggers its active `MainSession` to start a new `Pipeline`.

---

## 🧩 LLM Pipeline Stages (Mod Hooks)

### 1️⃣ `validateInput`

Sanitize or reject the raw user message.

### 2️⃣ `inputAccepted`

Input passed validation; mods can augment world state (e.g., context injection).

### 3️⃣ `generateQueryItems`

Mods generate `QueryItem`s based on the current world state.

### 4️⃣ `finalizePrompt`

Reorder, prune, and finalize prompt structure. Count tokens.

### 🚀 Driver Call (LLM)

- `llmDriver.sendRequest(prompt)`

### 5️⃣ `sanitizeAndValidateResponse`

Clean and structure the raw model output. No memory mutation.

### 6️⃣ `processResponseAndUpdateState`

Extract facts, events, or commands and update game memory.

### 7️⃣ `updateUI`

Trigger UI updates, send messages to views, animate elements, etc.

---

## 🧠 Mod Behavior by Stage (LLM)

| Stage                          | Mod Role                             |
|--------------------------------|--------------------------------------|
| `validateInput`               | Block or sanitize raw user input     |
| `inputAccepted`              | Inject shared facts or view state    |
| `generateQueryItems`         | Build prompt data from state         |
| `finalizePrompt`             | Analyze/prune for prompt efficiency  |
| `sanitizeAndValidateResponse`| Clean raw model output               |
| `processResponseAndUpdateState` | Apply memory or game changes      |
| `updateUI`                   | Trigger final display/logging        |

---

## ✅ Post-Pipeline Completion

- Assistant response returned to calling view.
- Mods may customize or intercept display.
- Errors (e.g., blocked input, model fail) handled gracefully.

---

## 🎧 Generic Pipeline for Non-LLM Drivers (Image, TTS, Audio, etc.)

### Universal Stages

| Stage               | Description |
|---------------------|-------------|
| `prepareRequest`    | Mods generate the request object (text, tags, settings) |
| `finalizeRequest`   | Final tweaks or validation (optional) |
| 🚀 `driver.sendRequest()` | Driver is invoked and returns data (image, audio, etc.) |
| `processResult`     | Check or normalize result (e.g. validate audio file) |
| `handleResult`      | Display result, notify user, or store it |

### Streaming-Specific Hooks (Optional)

If the driver supports streaming:

| Hook             | Trigger                                        |
|------------------|-------------------------------------------------|
| `onStreamStart`  | Stream begins (first chunk emitted)             |
| `onStreamChunk`  | Each streamable part (audio chunk, preview)     |
| `onStreamEnd`    | Final chunk or close of stream                  |

### Example (TTS Streaming)

```python
hookTo("prepareRequest", buildTtsRequest)
hookTo("onStreamStart", startAudioPlayback)
hookTo("onStreamChunk", appendAudioChunk)
hookTo("onStreamEnd", completeAudioPlayback)
hookTo("handleResult", notifyUserDone)
```

This flow supports progressive feedback while keeping the core driver abstraction intact.

---

## 📝 Notes

- Mods register hooks per view, but share backend memory.
- View memory is isolated; only backend shares across views.
- Game can be `None` if idle; no session/pipeline runs in that state.
- Only one `MainSession` is active per Game.
- Views remain usable even if game isn't loaded (e.g., UI settings).
- Ephemeral sessions (`TemporarySession`, `HiddenSession`) allow modular reasoning without interrupting main gameplay flow.

