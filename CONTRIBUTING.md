# Contributing to Ghost

Thank you for helping improve Ghost. Keep changes focused, preserve existing behavior unless an
issue says otherwise, and add regression coverage for bug fixes.

## Start with an Issue

Search the [issue tracker](https://github.com/Archangel-gabri/ghost-assistant/issues) first. Open
an issue for a bug or proposal so its UI, process-lifecycle, and provider impact can be agreed.

## Local Setup

Ghost is a flat source tree, not an installable Python package. Install its requirements directly:

```bash
git clone https://github.com/Archangel-gabri/ghost-assistant.git
cd ghost-assistant
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
./run.sh
```

At least one supported model CLI must be installed and authenticated for live answers. Do not use
live providers, microphones, or screen capture as part of the default regression suite.

## Tests and Linting

Run the offline Qt suite and Ruff from the repository root:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q tests
python -m ruff check src tests
git diff --check
```

The repository does not enforce full-tree formatting or static type checking. Do not reformat
unrelated files. Passing offline tests does not prove speech quality, live provider access, or
desktop capture on a particular host.

## Code Style and Tests

- Follow the nearby Python and PySide6 patterns and keep the Qt main thread non-blocking.
- Preserve bounded I/O, cancellation, process cleanup, and late-event rejection contracts.
- Use controlled fake CLIs, STT backends, audio, and screen capture for default tests.
- Exercise the smallest relevant test first, then run the full offline suite.
- Update documentation when setup, configuration, or externally visible behavior changes.

## Propose a Change

1. Agree the scope in an issue.
2. Create a branch such as `fix/worker-shutdown` from `master`.
3. Make the smallest complete change and add or update tests and documentation.
4. Run the checks above and review the complete diff.
5. Open a pull request with the issue link, test output, and UI screenshots if relevant.

## Data and Secrets

Do not submit recordings, screenshots with personal data, transcripts, API keys, tokens,
credentials, provider configs, database dumps, private logs, or captured desktop contents.

## Security Vulnerabilities

Do not open a public issue for a suspected vulnerability. Use GitHub Security's
[Report a vulnerability](https://github.com/Archangel-gabri/ghost-assistant/security/advisories/new)
form and include safe reproduction steps without secrets or personal data.
