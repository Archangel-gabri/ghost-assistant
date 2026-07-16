# Architecture

Ghost is a Qt (PySide6) desktop app. One background thread runs the
audio→STT→LLM pipeline; the main thread owns the glass overlay and receives
updates through Qt signals.

## Data flow

```
AudioCapture (loopback mic + VAD)
      │  silence gap → temp_question.wav
      ▼
stt_fast.create_stt(...)            groq (cloud) │ faster-whisper (local)
      │  question text
      ▼
ScreenGrabber.grab_force()          screenshot (parallel, change-detected)
      │
      ▼
LLMSession.ask_stream(q, on_chunk, screenshot)
      │  claude / codex / generic ; token deltas
      ▼  Qt signals: question_detected → answer_chunk* → answer_ready
OverlayWindow                        types the answer out live
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `main.py` | arg parsing, config load, GUI/CLI entry |
| `worker.py` | `PipelineWorker(QThread)` — wires capture → STT → LLM, emits signals |
| `audio_capture.py` | loopback capture, energy/Silero VAD, WAV save, STT trigger |
| `stt_fast.py` | STT backends behind `create_stt()`; Groq cloud + faster-whisper |
| `orchestrator.py` | `LLMSession` — Claude (`--print` stream-json), Codex, generic CLI |
| `screen_monitor.py` | `mss` capture + perceptual-hash change detection |
| `overlay_window.py` | frosted-glass window, tool/model selectors, live streaming |
| `icons.py` | inline-SVG line icons rendered via QtSvg |
| `utils.py` | ANSI strip, enums, `load_tools()` registry loader |
| `tray_icon.py` | system tray |
| `config_helper.py` | builds pipeline objects from a config dict |

## Streaming

`LLMSession._ask_claude_stream` runs `claude --print --output-format
stream-json --verbose --include-partial-messages`, parses newline-delimited
events, and forwards **only** `content_block_delta` events of type
`text_delta` (the model's `thinking` deltas and all system/hook events are
dropped). Each text delta is emitted as an `answer_chunk` signal; the overlay
appends it inline so the answer types out in real time.

## STT selection

`create_stt("auto")` picks Groq when `GROQ_API_KEY` is present (cloud,
~0.5 s, multilingual) and otherwise falls back to local `faster-whisper`
(offline, ~2 s on CPU). `moonshine` / `distil-whisper` exist but are
English-only and never auto-selected.

## Tools registry

`utils.load_tools()` reads `src/tools.yaml`. Each entry has an `id`, `label`,
`provider` (`claude` | `codex` | `generic`) and `models`. `generic` tools
supply a `command` template (`{model}` substituted, prompt on stdin) so new
CLIs can be added without code changes. The overlay builds the tool/model
dropdowns from this list.
```
