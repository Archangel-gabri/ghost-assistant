# Contributing to Ghost — voice + screen assistant

Thank you for helping improve Ghost. Keep changes focused, include tests where they add useful
coverage, and update documentation when behavior or setup changes.

## Code of Conduct

Be respectful, inclusive, and constructive.

## Development Setup

Ghost is a flat application source tree, not an installable Python package. Install its dependency
sets from the requirements files rather than using an editable install:

```bash
git clone https://github.com/Archangel-gabri/ghost-assistant.git
cd ghost-assistant

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Run the existing smoke suite and lint checks before changing anything:

```bash
python -m pytest -q tests
python -m ruff check src tests
```

The repository does not currently enforce a full-tree formatting or static type-checking gate.
Avoid reformatting unrelated files as part of a focused contribution.

## Contribution Workflow

1. Fork the repository on GitHub and clone your fork (or clone the upstream repository shown
   above for read-only work).
2. Create a focused branch:

   ```bash
   git switch -c feature/my-feature
   # or
   git switch -c fix/my-bug
   ```

3. Make the change:

   - Keep the diff focused and atomic.
   - Follow the surrounding code style.
   - Add regression coverage for bug fixes and useful coverage for new behavior.
   - Update the relevant documentation.

4. Re-run the checks:

   ```bash
   python -m pytest -q tests
   python -m ruff check src tests
   ```

5. Review and commit exactly the intended files:

   ```bash
   git status --short
   git diff --check
   git add --patch
   git commit -m "Brief description of change"
   ```

6. Push the branch and open a pull request:

   ```bash
   git push -u origin feature/my-feature
   ```

Include a clear description, any related issue, the checks you ran, and screenshots for visible UI
changes.

## Project Structure

```text
src/                    flat application modules
├── main.py               GUI entry point and command-line modes
├── worker.py             background audio → STT → LLM pipeline
├── orchestrator.py       Claude, Codex, and generic CLI adapters
├── audio_capture.py      audio capture and voice activity detection
├── screen_monitor.py     screenshot capture and change detection
├── stt_fast.py           speech-to-text backends
├── overlay_window.py     Qt overlay UI
├── tray_icon.py          system tray UI
├── utils.py              shared helpers and tool-registry loading
└── tools.yaml            user-editable tool registry

tests/                  smoke and documentation-contract tests
docs/                   architecture notes
assets/                 application icons
install.sh              desktop installation script
run.sh                  run without desktop installation
requirements.txt        runtime dependencies
requirements-dev.txt    pytest and Ruff
ruff.toml               repository lint policy
```

## Testing Guidelines

- Exercise the smallest relevant test first, then run the full smoke suite.
- Mock microphones, screen capture, subprocess tools, and external services in automated tests.
- Do not require API keys or a running Claude/Codex process for the default test suite.
- Preserve existing regression coverage when refactoring.

The current suite is intentionally smoke-level. Broader GUI, audio-device, speech-model, and live
AI-provider behavior needs explicit integration or manual testing and should be reported separately
from the default test result.

## Areas for Contribution

- Bug fixes and regression coverage
- UI and accessibility improvements
- Performance and reliability
- Documentation
- Localization
- New LLM or STT integrations

## Questions?

- Read the [project overview](README.md) and [architecture notes](docs/ARCHITECTURE.md).
- Search or open a [GitHub issue](https://github.com/Archangel-gabri/ghost-assistant/issues).

Thank you for making Ghost better.
