# Engineering status

This file keeps dated verification evidence and machine-specific compatibility details out of the
project overview. Results below are historical checkpoints, not claims about every current machine
or provider account. The last full checkpoint recorded in the previous README was 2026-09-07.

## Offline test gate

```bash
pip install -r requirements.txt -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python -m pytest -q tests
ruff check src tests
```

The suite covers bounded streaming and completed CLI output, deadlines, cancellation,
process-group cleanup, explicit provider/config arguments, screenshot snapshots, the STT process,
audio cleanup, and queued Qt events across Stop and restart. It uses real local subprocesses with
synthetic backends and an offscreen Qt event queue. Default tests do not call live AI providers,
capture the screen or microphone, or load native speech models.

The earlier 65-test and 216-test results are historical. On 2026-09-07, the default gate reported
**242 passed / 1 skipped**. The opt-in `GHOST_NATIVE_CODEX_CONFIG=1` gate reported **243 passed**.
That opt-in check stopped on an intentional unknown configuration field before inference; it did
not prove model access.

## Documentation refresh gate (2026-10-16)

The README-only refresh repeated the default suite without changing application code. Its first
full run reported 241 passed, 1 skipped, and one timeout in
`test_timeout_does_not_close_the_session`. The failed timing test then passed in isolation, and a
second full run reported **242 passed / 1 skipped** in 23.19 seconds. `ruff check src tests`, both
README image audits, local-link checks, and `git diff --check` passed.

## Separately approved native checks

These checks were narrower than an end-to-end acceptance test:

| Check | Evidence and limit |
|---|---|
| Explicit Codex router, `gpt-5.6-sol`, text | Returned the exact control marker in 11.398 seconds and left zero owned processes after close. Earlier direct-OpenAI 401/403/timeout evidence used a different provider. |
| Same Codex route, synthetic image | Returned the exact `HARBOR 628` marker in 11.463 seconds through Ghost staging and native `--image`. Desktop capture calls were zero; temporary images and owned processes were cleaned up. |
| Claude, explicit `opus[1m]` | Inference failed. A further bounded diagnostic returned `api_error` without an exact HTTP code. Earlier Sonnet checks returned HTTP 403 `model_not_permitted`; that exact cause was not established for the Opus check. |
| Local native STT, cached base model | An offline synthetic Russian-voice fixture returned text but matched 0/5 predeclared semantic stems: quality **FAIL**. The child and WAV were cleaned up; one semaphore shutdown warning remained unresolved. |
| Owner-local desktop launcher | 31 separate local-only tests passed. The real GUI entry point opened and closed without a worker, screen capture, or model request. This machine-specific launcher is not part of the portable repository or installer. |

No new model downloads, microphone capture, or global authentication, account, or billing changes
were part of those checks. They do not establish Claude image access, real desktop-capture fidelity,
full CLI/managed-policy isolation, microphone quality, or the complete audio-to-answer workflow.

## CLI compatibility and configuration notes

The provider-specific invocation contracts at that checkpoint targeted Claude Code 2.1.263 and
Codex 0.153.4. Synthetic argument tests do not prove how a different installed version or
machine-managed policy behaves. Generic CLI commands have bounded I/O and cancellation, but do not
inherit the Claude/Codex isolation contract.

`./run.sh` prefers a repository-root `config.local.yaml` when present, otherwise it uses the bundled
FAST profile. The local file is ignored by Git. An explicit `--config path/to/profile.yaml` wins over
both. The desktop installer continues to point to its bundled profile.

A private custom-provider profile may contain nonsecret routing metadata:

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

Ghost does not source shell files, place credential values in YAML, or import an ambient Codex
provider. A missing custom-provider environment variable fails before CLI startup. The diagnostic
names the variable but not its value. This preflight does not prove that a present credential is
valid or that the selected model is allowed.

The bundled Codex entry explicitly selects OpenAI. A different provider must be declared with its
own `codex_provider_spec` in [`../src/tools.yaml`](../src/tools.yaml), using either an environment
variable name or `requires_openai_auth: true`. Text-only one-shot mode is available as:

```bash
./run.sh --config config.local.yaml --once "Your question"
```

This command makes a live model request. Changing capture configuration alone does not validate
native image transport or speech recognition.

## README media

The files in [`../assets/readme/`](../assets/readme/) were captured on 2026-10-16 from the real
`OverlayWindow` widget under `QT_QPA_PLATFORM=offscreen`. The demo invoked the same question,
streaming-chunk, and final-answer handlers used by the application, then captured each state with
`QWidget.grab()`. The content is synthetic; no microphone, live model, screen capture, or personal
data was used. The images are UI-rendering evidence, not an end-to-end provider check.

## Related documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) explains the data flow and resource ownership contracts.
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) contains development setup and verification commands.
