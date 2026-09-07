<div align="center">
  <img src="assets/ghost-256.png" width="96" alt="Ghost"/>
  <h1>Ghost</h1>
  <p><b>Voice + screen AI assistant.</b> Turns spoken questions into answers in a frosted-glass overlay, with optional local screenshot context.</p>
</div>

---

## What it does

```
🎙 voice  ─►  STT  ─►  ┌──────────────┐
📸 screen image ───►   │     LLM     │ ─►  glass overlay
                       └──────────────┘
```

- **Speech-to-text** — with the `auto` backend, Groq `whisper-large-v3-turbo` when `GROQ_API_KEY` is set, otherwise local `faster-whisper`. Model loading and transcription run in a separate cancellable process.
- **Answer** — Claude Code streams text deltas; Codex and generic CLI tools return a completed answer.
- **Screen context** — stages a private image snapshot. Claude receives its path with `Read` access to the staging directory; Codex receives `--image`. Generic tools reject screenshots without a declared image transport. One synthetic image was successfully interpreted through the explicitly configured Codex router; Claude image access and actual desktop-capture fidelity remain unverified.
- **Glass UI** — light iOS-style frosted overlay, always on top, system tray.
- **Configurable** — pick the tool + model in the UI; add your own tools by editing [`src/tools.yaml`](src/tools.yaml).

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python -m pytest -q tests
ruff check src tests
```

The local suite covers bounded streaming and completed CLI output, deadlines, cancellation,
process-group cleanup, explicit provider/config arguments, screenshot snapshots, STT process
and audio cleanup, and queued Qt events across Stop/restart. It uses real local subprocesses
with synthetic backends and an offscreen Qt event queue. Default tests do not call live AI
providers, capture the screen or microphone, or load native speech models.

The earlier 65-test and 216-test checkpoints are historical. The current 2026-09-07 gate is
**242 passed / 1 skipped** by default, or **243 passed** with native config-only validation
enabled by `GHOST_NATIVE_CODEX_CONFIG=1`. Its intentional unknown-field guard stops before
inference. The default suite also checks local-profile launch selection, missing environment-key
preflight, disabled screenshot behavior in GUI/CLI, and CLI resource cleanup.

Separately approved native checks on 2026-09-07 produced these narrower results:

| Check | Evidence and limit |
|-------|--------------------|
| Explicit Codex router, `gpt-5.6-sol`, text | Exact control marker in 11.398 s; zero owned processes after close. Earlier direct-OpenAI 401/403/timeout used a different provider and does not invalidate this result. |
| Same Codex route, synthetic image | Exact `HARBOR 628` in 11.463 s; marker absent from the text prompt. Actual Ghost staging and native `--image` were used; desktop capture calls were zero, temporary images removed, owned processes after close zero. |
| Claude, explicit `opus[1m]` | Inference failed; one further bounded diagnostic returned `api_error`, without an exact HTTP code. The earlier Sonnet HTTP 403 `model_not_permitted` must not be attributed to this Opus probe. |
| Local native STT, cached base model | One offline synthetic Russian-voice fixture returned text but matched 0/5 predeclared semantic stems: quality **FAIL**. Child and WAV were cleaned up; a one-semaphore shutdown warning remains unresolved. |
| Owner-local desktop launcher | 31 separate local-only tests passed; the real GUI entrypoint opened/closed without a worker, screen capture or model request. This machine-specific launcher is not included in the portable repository or installer. |

No new model downloads, microphone capture or global auth/account/billing changes were made.
The STT fixture does not isolate synthesizer versus model versus pipeline failure; real speech
quality and clean native-library shutdown remain unproven. The owner launcher trusts installed
system/package code and is not an OS sandbox. Neither offline tests nor these bounded probes
establish Claude image access, actual desktop-capture fidelity, full CLI/managed-policy isolation,
or the complete audio-to-answer workflow. Current evidence and open checks are recorded in
[CLAUDE.md](CLAUDE.md).

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

`./run.sh` prefers a repository-root `config.local.yaml` when present, otherwise it keeps the
bundled FAST profile. This local file is ignored by Git. An explicit
`./run.sh --config path/to/profile.yaml` overrides either choice. The existing desktop installer
still points to its bundled profile; it is not changed by this local launcher recipe.

For a private custom-provider profile, create `config.local.yaml` with nonsecret metadata:

```yaml
screenshot:
  enabled: false
backend:
  provider: codex
  model: your-allowed-model
  codex_provider_spec:
    name: your_proxy
    base_url: https://your-provider.example/v1
    wire_api: responses
    env_key: YOUR_PROXY_API_KEY
  stt_backend: faster-whisper
  whisper_model: base
  language: ru
```

Use the environment already configured for that provider; Ghost does not source shell files,
load credentials from YAML, or import ambient CLI configuration. A missing or empty custom
`env_key` produces a configuration error before CLI startup or a request. The diagnostic names
the variable, never its value. GUI capture does not start after that configuration failure.
This preflight does not establish whether a present key is valid or the selected model is allowed.

`screenshot.enabled: false` is honored by the initial GUI toggle and both CLI modes. You can
enable capture explicitly in the GUI. A text-only one-shot invocation is
`./run.sh --config config.local.yaml --once "Your question"`; it sends the question to the
chosen provider and is a live model request, not an offline test. Changing capture configuration
does not itself validate native image transport or speech recognition.

The bundled **Codex (OpenAI)** entry explicitly selects OpenAI. To use another provider, add
its `codex_provider_spec` to the tool entry; terminal/API callers pass the same metadata through
`backend.codex_provider_spec`. The adapter does not import the provider from ambient Codex
configuration. Missing or invalid metadata produces an error instead of choosing a provider.
Custom entries contain a provider name, endpoint and either an environment-variable name for
authentication or `requires_openai_auth: true`; never put credential values in the registry.
The example in `src/tools.yaml` shows the supported format.

Claude and Codex receive explicit flags to limit user/project configuration, hooks, plugins
and tools. These invocation contracts target Claude Code 2.1.263 and Codex 0.153.4; machine-managed
policy and actual CLI enforcement remain outside the synthetic test guarantee. Generic CLI
commands have bounded I/O and cancellation, but do not inherit the Claude/Codex isolation contract.

## Project layout

```
src/          application code
  main.py            entry point (GUI / --cli / --once)
  overlay_window.py  glass UI
  worker.py          audio → STT → LLM pipeline (QThread)
  audio_capture.py   loopback capture + VAD
  stt_fast.py        STT backends (Groq / faster-whisper)
  stt_process.py     cancellable persistent STT child and bounded JSON IPC
  orchestrator.py    LLM adapters (Claude / Codex / generic)
  cli_contract.py    explicit Codex provider and safe screenshot snapshot contracts
  screen_monitor.py  screenshot + change detection
  icons.py           inline SVG icons
  utils.py           shared helpers + tools registry
  tools.yaml         user-editable tool registry
  config*.yaml       profiles
assets/       app icon
docs/         architecture notes
tests/        tests
```

**The line that matters is the thread boundary.** The main thread owns the widgets.
`PipelineWorker(QThread)` supervises resources; audio callback threads perform serialized
question processing while a lazily created STT child owns model loading and inference.
Explicit queued signals deliver updates through a worker-identity and generation guard.
Stop requests cancellation without waiting in the GUI; restart remains disabled until cleanup
is confirmed. Failed cleanup retains resource ownership and is reported rather than treated as
successful shutdown. Native audio-device and speech-model behavior still needs separate validation.

`orchestrator.py` adapts Claude streaming and completed Codex/generic responses. Streamed text
is provisional: only a successful final result enters history; errors replace the draft.
Adding a compatible generic CLI means adding its command template to `src/tools.yaml`.

## Docs
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit together.
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup.

## License
[MIT](LICENSE)
