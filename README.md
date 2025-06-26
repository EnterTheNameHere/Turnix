# Turnix

## How To Run

- Requires Python 3.10+
- Install dependencies in requirements.txt
- Run llama.cpp on your machine on port 1234 (https://github.com/ggerganov/llama-cpp)


### Backend

```
uvicorn backend.server:app --reload
```

## Modding

- Mods are stored in the `mods` folder.
- Both backend and frontend use manifest.yaml format
- Stage hooks available for both sides (backend, frontend) 
