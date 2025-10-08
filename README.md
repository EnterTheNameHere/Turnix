# Turnix

**Turnix** is a modular AI framework built for **Game Master–style simulations** and other multi-agent, LLM-driven applications.  
It consists of a **Python backend** (FastAPI + Pydantic v2) and a **JavaScript frontend** (browser or Electron) connected via an **RPC protocol over WebSocket**.

The goal is to provide a general and extensible foundation for modular AI interaction — from interactive fiction and world simulation to experimental multi-agent environments.

---

## 🧩 Architecture Overview

```
┌──────────────┐
│ Electron /   │
│ Browser UI   │
└──────┬───────┘
       │  (WebSocket RPC)
┌──────▼──────────┐
│  FastAPI Server │  ← Truth owner
│  (Python 3.12)  │
└──────┬──────────┘
       │
┌──────▼────────────────────────────┐
│ Mods & Pipelines (e.g. Llama.cpp) │
└───────────────────────────────────┘
```

- **Backend:**  
  Python 3.12 with FastAPI / Starlette and Pydantic v2.  
  Owns all canonical state, serves static files, manages mods, and handles RPC.

- **Frontend:**  
  Browser-based UI or Electron shell.  
  Uses `turnixImport()` for dynamic module loading with session cache-busting.  
  Communicates with backend via a lane-based RPC protocol with ACKs, heartbeats, and streamed responses.

- **LLM Server:**  
  Local [`llama.cpp`](https://github.com/ggerganov/llama.cpp) server used for inference, connected through `mods/first-party/drivers/llamacpp`.

---

## ⚙️ Features

- Modular mod system (loadable frontend/back-end extensions)
- Typed RPC protocol with sequence IDs, ACKs, and graceful reconnection
- WebSocket streaming for model responses
- Electron launcher for standalone desktop usage
- Dev launcher (`launcher.py`) to manage backend, Electron, and `llama.cpp`
- Configurable default settings via `settings_default.json5`

---

## 🧠 Repository Structure

```
Turnix/
├── backend/
│   ├── http_client.py
│   ├── settings_default.json5
│   └── ...
│
├── mods/
│   └── first-party/
│       └── drivers/
│           └── llamacpp/
│               └── llamacpp_client.py
│
├── frontend/
│   ├── index.html
│   ├── bootstrap.js
│   ├── assets/
│   │   ├── mod-loader.js
│   │   ├── style.css
│   │   └── types.d.ts
│   └── core/
│       └── turnixImport.js
│
├── electron/
│   ├── main.js
│   ├── preload.js
│   └── package.json
│
├── launcher.py
├── launcher_llama_cpp_presets.json5
├── launcher.bat
└── requirements.txt
```

---

## 🚀 Getting Started

### 1. Install Dependencies

Make sure you’re using **Python 3.12** and have **Node.js** (for Electron).

```bash
setup.ps1
```

This setup script does all required to setup development environment for Turnix.

- unzips Python embedded into python-embedded/
- gets pip
- installs requirements.txt
- runs npm install in electron/
- creates link or junction in root/node_modules/ to electron/node_modules/ for eslint to function

### 2. Start Backend Manually

```bash
launcher.bat
```

or manually

```bash
python-embedded/python.exe -m uvicorn backend.server:app --port 63726
```

### 3. Start Electron Frontend

```bash
launcher.bat
```

or manually from the `electron/` directory:

```bash
npm run start
```

This launches the desktop shell that loads the frontend from  
`http://localhost:63726/`.

### 4. Start Llama.cpp

```bash
launcher.bat
```

Currently models and server location are hard-coded. Since you obviously don't have them where I have, edit `launcher_llama_cpp_presets.json5` and set your preferred model paths. Launcher automatically escapes Windows paths.

---

## 🧰 Development Notes

- Frontend uses **`turnixImport()`** for dynamic ES module loading with stable session cache busting.
- RPC messages are typed; see `frontend/assets/types.d.ts` or pydantic models.
- Backend is designed to be modular and stateless between requests; mods own runtime contexts.
- Electron is purely a rendering convenience — no Node.js access in renderer context.
- All static assets are served directly by the backend for consistency.

---

## 🧪 Roadmap (Work in Progress)

- Full mod dependency graph
- Persistent mod state and hot-reload
- LLM session pipelines with subscription hooks
- GUI mod manager
- Offline packaging & auto-update for Electron
- Advanced streaming telemetry
- Much more to list

---

## 🧑‍💻 License

MIT License

---

## 💬 Credits

- EnterTheNameHere Bohemian  
- Built with ❤️ and curiosity to explore emergent AI systems.
