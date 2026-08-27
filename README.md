<div align="center">
  <img src="assets/ghost-256.png" width="96" alt="Ghost"/>
  <h1>Ghost</h1>
  <p><b>Voice + screen AI assistant.</b> Listens to a spoken question, reads what's on screen, and streams an answer into a frosted-glass overlay.</p>
</div>

---

## What it does

```
🎙 voice  ─►  STT  ─►  ┌──────────────┐
📸 screen ─────────►   │  LLM (stream)│ ─►  glass overlay (types out live)
                       └──────────────┘
```

- **Speech-to-text** — Groq `whisper-large-v3-turbo` (~0.5 s) when `GROQ_API_KEY` is set, otherwise local `faster-whisper` (offline).
- **Answer** — Claude Code or Codex, streamed **token-by-token** so text appears as it's generated.
- **Screen aware** — attaches a screenshot so it can answer "what's on line 5?".
- **Glass UI** — light iOS-style frosted overlay, always on top, system tray.
- **Configurable** — pick the tool + model in the UI; add your own tools by editing [`src/tools.yaml`](src/tools.yaml).

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q tests
ruff check src tests
```

Smoke-level by design: the suite asserts that every module imports and that the tool registry in
`src/tools.yaml` parses into usable entries. Both are the failures that actually happen here —
a Qt enum moved between versions and once aborted the app on the first answer, and a malformed
tools file silently leaves the model selector empty.

## Install

```bash
git clone <repo> ghost && cd ghost
./install.sh            # deps + app-menu entry + Desktop shortcut
```

Then launch **Ghost** from the application menu, or double-click the Desktop icon.
Run without installing: `./run.sh`

For fast cloud STT, grab a free key at [console.groq.com](https://console.groq.com):

```bash
export GROQ_API_KEY=...   # add to ~/.bashrc to persist
```

## Configure

| File | Purpose |
|------|---------|
| [`src/config-fast.yaml`](src/config-fast.yaml) | default profile (Sonnet + auto STT) |
| [`src/config-ultra.yaml`](src/config-ultra.yaml) | max-speed profile |
| [`src/tools.yaml`](src/tools.yaml) | tools & models shown in the UI — **add your own here** |

## Project layout

```
src/          application code
  main.py            entry point (GUI / --cli / --once)
  overlay_window.py  glass UI
  worker.py          audio → STT → LLM pipeline (QThread)
  audio_capture.py   loopback capture + VAD
  stt_fast.py        STT backends (Groq / faster-whisper)
  orchestrator.py    LLM adapters (Claude / Codex / generic)
  screen_monitor.py  screenshot + change detection
  icons.py           inline SVG icons
  utils.py           shared helpers + tools registry
  tools.yaml         user-editable tool registry
  config*.yaml       profiles
assets/       app icon
docs/         architecture notes
tests/        tests
```

**The line that matters is the thread boundary.** Qt repaints on one thread, and everything slow
lives on another: `worker.py` is a `QThread` that owns the whole audio → STT → LLM chain, and it
talks to the overlay only through signals. Nothing in `audio_capture`, `stt_fast` or `orchestrator`
touches a widget — that is why a two-second transcription or a stalled model does not freeze the
window it is typing into.

`orchestrator.py` holds one adapter per CLI tool and streams stdout token by token, so text appears
while it is being generated rather than after. Adding a tool means adding an entry to
`src/tools.yaml`, not writing code.

## Docs
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit together.
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup.

## License
[MIT](LICENSE)
