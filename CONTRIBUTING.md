# Contributing to Ghost — voice + screen assistant

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing.

## Code of Conduct

Be respectful, inclusive, and constructive. We value all contributors.

## How to Contribute

### 1. Fork & Clone

```bash
git clone https://github.com/yourusername/ghost-assistant.git
cd ghost-assistant
```

### 2. Create Feature Branch

```bash
git checkout -b feature/my-feature
# or
git checkout -b fix/my-bug
```

### 3. Make Changes

- Keep changes focused and atomic
- Follow the code style (see below)
- Add tests for new features
- Update documentation

### 4. Code Style

```bash
# Format
black src/

# Lint
flake8 src/

# Type check
mypy src/
```

### 5. Test

```bash
pytest tests/
```

### 6. Commit

```bash
git commit -am "Brief description of change

Optional longer explanation of why and what changed."
```

### 7. Push & Create PR

```bash
git push origin feature/my-feature
```

Then create a Pull Request on GitHub with:
- Clear title
- Description of changes
- Any related issues (close #123)
- Screenshots if UI changes

## Development Setup

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Format before commit
black src/ && flake8 src/
```

## Project Structure

```
src/ghost/              # Main package
├── main.py             # GUI entry point
├── cli.py              # CLI entry point
├── orchestrator.py     # LLM integration
├── audio_capture.py    # Audio + VAD
├── screen_monitor.py   # Screenshot
├── stt_fast.py         # 4 STT backends
└── utils.py            # Utilities

tests/                  # Test suite
docs/                   # Documentation
install/                # Installation files
scripts/                # Utility scripts
```

## Areas for Contribution

- 🐛 **Bug fixes** — Found an issue? Fix it!
- 🎨 **UI improvements** — Better layouts, themes, icons
- ⚡ **Performance** — Faster STT, better caching
- 📚 **Documentation** — Clearer guides, examples
- 🧪 **Tests** — More coverage, edge cases
- 🌍 **Localization** — Translations, regional settings
- 🔌 **Integrations** — New LLM providers, STT backends

## Commit Message Guidelines

- Use imperative mood ("Add feature" not "Added feature")
- Start with emoji if relevant:
  - ✨ New feature
  - 🐛 Bug fix
  - 📚 Documentation
  - ⚡ Performance
  - 🔧 Refactoring
- Keep first line under 50 characters
- Reference issues: "Closes #123"

Examples:
```
✨ Add Ollama LLM backend support
🐛 Fix audio capture crash on PulseAudio disconnect
📚 Update STT documentation with examples
⚡ Optimize Whisper model caching with thread-safe lock
```

## Testing Guidelines

- Test new features
- Test edge cases
- Mock external dependencies
- Aim for > 80% coverage

```bash
pytest tests/ --cov=src/ghost --cov-report=html
```

## Performance Considerations

- Lazy-load models where possible
- Cache results appropriately
- Minimize blocking operations
- Use threading/async for I/O

## Questions?

- 📋 Check [FAQ](docs/SPEED_GUIDE.md#faq)
- 💬 Open a [Discussion](https://github.com/yourusername/ghost-assistant/discussions)
- 🐛 Search [Issues](https://github.com/yourusername/ghost-assistant/issues)

---

**Thank you for making Ghost better!** ⭐
