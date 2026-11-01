# G-02 verification evidence — 2026-11-01

Starting Integration: `4cf1ebd57d2d2a5de507ac4b818d4dee91fd0959`.
Implementation parent/concrete proposal: `88277401abaf16ffe3469a07b8dd2032739b6d8e`.
Branch: `g02-observation-viewer`. Acceptance belongs to independent Integration review.

Environment: Windows, Python **3.15.0b4**, Node **v23.11.0**, Playwright **1.62.1**,
headless installed Microsoft Edge / Chromium **156.0.4314.50**. Browser viewport
1440×1100; full-page PNGs. Actual local accepted G-01 activation, ephemeral bridge
and viewer origins; synthetic whole-host hardware and stream evidence. Test samples
include >2^60 nanoseconds, delayed 200 ms reads, missing GPU/VRAM, silence, bounded
history loss and a display wall-clock jump. No real GPU/server inference was run.

Commands run from the isolated checkout:

```powershell
node --test tests/tools/observationViewer/viewer.test.mjs
# 41 passed; zero skipped

.\python-embedded\python.exe -X frozen_modules=off -m pytest -q --import-mode=importlib tests/tools/observationViewer
# 12 passed in 0.60 s (includes the Node suite)

.\python-embedded\python.exe -X frozen_modules=off -m pytest -q --import-mode=importlib tests/tools/observationViewer tests/first_party/observationBridge tests/backend/telemetry tests/backend/application tests/backend/context tests/backend/runtime tests/backend/packs tests/backend/llm tests/first_party/llmDrivers
# 304 passed in 6.06 s; zero skipped

.\python-embedded\python.exe -X frozen_modules=off -m pytest -q --import-mode=importlib tests
# 1156 passed in 6.87 s; zero skipped (Actant directory only, final rerun)

.\python-embedded\python.exe -X frozen_modules=off tests/tools/observationViewer/smoke.py --playwright C:\Users\Enter\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\playwright --browser "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --output tools/observationViewer/verification
# passed; browser 156.0.4314.50, maximum outstanding GETs per viewer 1
```

`result.json` is the machine-readable smoke summary. Two readers and pause/resume
left the source's one collection claim intact, with **41 synthetic hardware reads**
unchanged by browser reads. Revocation returned actual 410, withdrawal actual 404,
wrong credential actual 401, and bridge unload produced a connection failure.
All endpoint workers, browser processes and fixture resources were explicitly closed.
Actual smoke caught an unbound browser timer call that Node's injected scheduler
did not expose; timer defaults now call `globalThis` through arrow wrappers.

Screenshots were visually inspected for plot alignment, interval/gap/silence versus
unavailable presentation, units, whole-host labels, truncation, exact inspection,
empty credential field and cleared/error evidence:

- [Connected, all retained](connected.png)
- [Paused, last 10 seconds](paused.png)
- [Unchanged hardware / stale](stale.png)
- [Unauthorized](unauthorized.png)
- [Revoked](revoked.png)
- [Disconnected](disconnected.png)

Limitations: only this Edge runtime/desktop viewport was browser-exercised; no
Firefox/Safari, real hardware/provider, real server, consumer adoption, optimization
or persistent recording. CORS-blocked errors can be opaque to browser Fetch. Literal
IPv4 loopback and explicit non-default ports preserve G-01's Host/Origin contract.
The embedded Python prints its existing platform-standard-library warning, while
all test commands exit successfully. Ruff was not run.

A later sandboxed focused rerun initially failed its eleven loopback cases with
Windows socket permission error 10013 (the Node wrapper passed). Repeating that
exact focused command with approved loopback permissions passed all twelve in
0.60 s; no source change or skip was used to bypass the environment restriction.
