# Architecture

Ghost is a Qt (PySide6) desktop app. `PipelineWorker(QThread)` supervises the
audio/STT/LLM resources; audio callback threads perform serialized question
processing. The main thread owns the overlay and receives explicitly queued
Qt signals through a worker-identity and generation guard.

## Data flow

```
AudioCapture (loopback mic + VAD)
      │  silence gap → unique request-owned WAV
      ▼
STTProcess                         persistent spawn child; bounded JSON IPC
      │  lazy stt_fast.create_stt: Groq (cloud) / faster-whisper (local)
      │  question text
      ▼
ScreenGrabber.grab_force()          synchronous screenshot, when enabled
      │  private snapshot: Claude Read/add-dir; Codex --image
      ▼
LLMSession.ask_stream(q, on_chunk, screenshot)
      │  Claude text deltas; Codex/generic completed output
      ▼  Qt signals: question_detected → answer_chunk* → answer_ready OR error_occurred
OverlayWindow                        provisional draft → final success/error
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `main.py` | arg parsing, config load, GUI/CLI entry |
| `worker.py` | `PipelineWorker(QThread)` — wires capture → STT → LLM, emits signals |
| `audio_capture.py` | loopback capture, energy/Silero VAD, WAV save, STT trigger |
| `stt_fast.py` | STT backends behind `create_stt()`; Groq cloud + faster-whisper |
| `stt_process.py` | lazy persistent backend process, bounded JSON frames, cancellation/reap |
| `orchestrator.py` | `LLMSession` — Claude (`--print` stream-json), Codex, generic CLI |
| `cli_contract.py` | explicit Codex provider metadata and bounded private screenshot snapshots |
| `screen_monitor.py` | `mss` capture + perceptual-hash change detection |
| `overlay_window.py` | frosted-glass window, tool/model selectors, live streaming |
| `icons.py` | inline-SVG line icons rendered via QtSvg |
| `utils.py` | ANSI strip, enums, `load_tools()` registry loader |
| `tray_icon.py` | system tray |
| `config_helper.py` | builds pipeline objects from a config dict |

## Streaming

`LLMSession._ask_claude_stream` runs `claude --print --output-format
stream-json --verbose --include-partial-messages`, parses newline-delimited
events, and forwards text deltas from `content_block_delta` events. Thinking
and unrelated event types are not shown. A successful terminal result's full
text is authoritative; the overlay replaces the provisional draft without
duplicating it. A failure after partial text replaces the draft with an error
and does not enter history. Questions and answers are inserted as plain text.

Binary stdin/stdout/stderr are multiplexed with nonblocking selectors and a
monotonic deadline. Partial lines are buffered; limits are 1 MiB per line,
2 MiB accumulated text and an 8 KiB stderr tail. Nonzero exit, terminal error,
incomplete protocol, unsent prompt or empty result cannot become success.
Completed-output requests use a separate binary selector loop: prompt and stdout
are each capped at 2 MiB, stderr retention at 8 KiB. The loop drains output while
writing stdin, validates output UTF-8 and includes the wait after EOF in its deadline.
Codex currently passes its bounded prompt as an argument; operating-system argv
limits can reject a prompt below the application's cap. A completed Codex/generic
answer is not presented as token streaming.

## Cancellation and terminal outcomes

`LLMResult(str)` carries a per-request status: `success`, `error`, `timeout`,
`cancelled` or `mock`. The worker emits `answer_ready` only for nonempty success.
The status is not inferred from answer text (a literal `[timeout]` can be success).

`LLMSession.close()` prevents new spawns and cleans up the owned POSIX process group,
including descendants that close inherited pipes before the parent exits successfully.
The direct child is reaped. Failed cleanup retains ownership so a later close can retry;
it does not report resources as released. Timeout cleans up the request without permanently
closing the session. Descendants that deliberately escape the group are outside this guarantee.

Stop invalidates the UI generation and only sets the worker stop event; it
does not wait in the GUI. Worker cleanup closes the model session before audio,
then waits for the active question callback before closing screen resources.
Restart requires confirmed cleanup, not merely a `QThread.finished` event; Quit also
waits asynchronously. Failed cleanup retains the worker and blocks restart/exit visibly.
Late data/status/error signals cannot update a different worker generation.

## CLI configuration and screenshots

Codex requires an explicit `codex_provider_spec`. The bundled tool entry explicitly selects
OpenAI; custom tools declare their own provider metadata and authentication source by name.
UI selection and ConfigBuilder pass that metadata to LLMSession; missing/invalid metadata is
an error before a model request. Credential values are not stored in the tool registry.

Claude requests safe mode, restricted Read tools and strict MCP configuration. Codex requests
ignored user config/rules, disabled hooks/plugins/apps and restricted project context. The
contracts target Claude Code 2.1.263 and Codex 0.153.4. Synthetic tests check constructed argv and
hostile fixtures; actual CLI enforcement and machine-managed policy require separate evidence.
Generic command templates do not inherit this provider-specific isolation contract.

Screenshot input is a bounded regular file (8 MiB maximum) read through no-follow directory
descriptors, checked for PNG/JPEG/WebP signatures and changes during the read. This does not
fully decode or validate image content. Only a private staged copy is exposed during the request:
Claude gets a prompt path plus `Read` access through `--add-dir` to the staging directory;
Codex gets native `--image`. Generic screenshots fail explicitly. Snapshot bytes and removal are
tested with local fake CLIs; native image access and interpretation are separate acceptance checks.

## STT selection

`create_stt("auto")` picks Groq when `GROQ_API_KEY` is present and otherwise falls
back to local `faster-whisper`. `moonshine` / `distil-whisper` exist but are
English-only and never auto-selected. This selection does not imply measured latency.

AudioCapture runs one transcription at a time and owns a unique WAV until the job finishes
or its child is reaped. STTProcess lazily spawns a persistent backend, caching model loading
while healthy. Parent/child request and response frames are bounded JSON (16 KiB / 64 KiB);
partial frames, stale request IDs, deadlines and cancellation are handled by the parent.
Stop closes STT before waiting for capture callbacks. Incomplete cleanup retains child/WAV
ownership for retry. STT failures use an error callback rather than becoming spoken questions.

## Verification status

The expanded offline suite uses real local subprocesses with synthetic CLI/STT backends,
controlled capture and an actual offscreen Qt event queue. It covers cancellation and resource
ownership without native models, live network services, microphone or screen capture.
The 65-test and 216-test results are historical checkpoints. The current 2026-09-07 gate
passed **243 tests** with the opt-in native Codex config-only test enabled; the default
suite passed **242 tests / 1 skip**, also in a fresh minimal CI environment. The opt-in
test terminates strict configuration validation on a sentinel unknown key before
inference; it does not establish model access.

Earlier native Claude probes returned valid stream-json, including a terminal
`subtype=success` with `is_error=true`, but exited 1 with HTTP 403 `model_not_permitted`;
an explicit Sonnet pin reproduced that error. The later separately approved `opus[1m]`
probe also failed, and one further diagnostic returned `api_error` without recovering
an exact HTTP code. Do not infer that the Opus probe had the earlier Sonnet 403.
The exact current cause remains unresolved; no automatic auth/account/billing change
or additional model call follows from these results.

Earlier native Codex exposed a quoted dotted project-key parsing error, fixed with a
complete TOML inline `projects` table retaining the exact untrusted cwd. A subsequent
direct-OpenAI text request produced auth/permission diagnostics (401/403), reached its
45-second deadline and left zero owned processes. Image calls were skipped at that
historical stage. Later separately approved probes explicitly selected the owner's
working router and `gpt-5.6-sol`: text returned its exact control marker in 11.398 s;
a synthetic image sent through actual Ghost staging/native `--image` returned exact
`HARBOR 628` in 11.463 s, without that marker in the text prompt. Desktop capture calls
were zero, temporary images were removed and each session left zero owned processes.
Those earlier direct-provider failures are not a current blocker for this proven route.

One approved native STT probe used the existing cached base model with offline/
local-files-only settings and synthetic Russian speech; there was no download or
microphone capture. It returned text in 10.231 s but matched 0/5 predeclared semantic
stems: **quality FAIL**. The child and WAV were cleaned up; a one-semaphore resource-
tracker warning occurred at shutdown. That named semaphore was absent after tracker
shutdown, but clean native-library shutdown is unproven. A single artificial-voice
fixture does not isolate synthesizer, model or pipeline causes, or validate real speech.

A separately reviewed owner-local launcher passed 31 local-only tests, and the actual
`main.main()` GUI entrypoint opened/closed a visible xcb window without a worker, screen
capture or model request. The launcher trusts installed system/package code, is not
an OS sandbox, and is not part of the portable public repository or installer.
Claude image input, actual desktop-capture fidelity, full managed-policy isolation,
microphone operation and the complete audio-to-answer workflow remain unverified.

## Tools registry

`utils.load_tools()` reads `src/tools.yaml`. Each entry has an `id`, `label`,
`provider` (`claude` | `codex` | `generic`) and `models`. `generic` tools
supply a `command` template (`{model}` substituted, prompt on stdin) so new
CLIs can be added without code changes. The overlay builds the tool/model
dropdowns from this list.
