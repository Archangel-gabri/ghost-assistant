"""Explicit CLI routing and screenshot contracts, without models or network."""

import base64
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
CAPTURE = r'''
import base64,json,os,sys
from pathlib import Path
args=sys.argv[1:]
prompt=sys.stdin.read() if args[0] != 'codex' else args[-1]
files={}
directories=[]
if '--image' in args:
    image=Path(args[args.index('--image')+1])
    files[str(image)]=base64.b64encode(image.read_bytes()).decode()
if '--add-dir' in args:
    directory=Path(args[args.index('--add-dir')+1])
    directories.append({'path':str(directory),'mode':directory.stat().st_mode & 0o777,
                        'entries':sorted(path.name for path in directory.iterdir())})
    for image in directory.iterdir():
        files[str(image)]=base64.b64encode(image.read_bytes()).decode()
answer=json.dumps({'args':args,'prompt':prompt,'files':files,'directories':directories,'cwd':os.getcwd()})
if '--output-format' in args and args[args.index('--output-format')+1]=='stream-json':
    print(json.dumps({'type':'result','result':answer,'subtype':'success'}))
else:
    print(answer)
'''


@pytest.fixture
def capture_cli(monkeypatch):
    original = subprocess.Popen
    calls = []

    def capture(args, **kwargs):
        calls.append((args, kwargs))
        return original([sys.executable, "-u", "-c", CAPTURE, *args], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture)
    return calls


@pytest.mark.parametrize("stream", [False, True])
def test_explicit_claude_sonnet_is_pinned_in_both_invocation_paths(tmp_path, capture_cli, stream):
    from orchestrator import LLMSession

    session = LLMSession(provider="claude", model="sonnet", workdir=tmp_path)
    result = (session.ask_stream("question", lambda text: None) if stream
              else session.ask("question"))
    assert result.status == "success"
    args = json.loads(result)["args"]
    assert "--model" in args
    assert args[args.index("--model") + 1] == "sonnet"
    assert "--safe-mode" in args and "--restricted" in args


def test_codex_provider_is_required_instead_of_inheriting_ambient_config(tmp_path):
    from cli_contract import CLIContractError, codex_isolation_args

    with pytest.raises(CLIContractError) as error:
        codex_isolation_args(tmp_path, None)
    assert error.value.code == "provider_required"
    assert "codex_provider_spec" in str(error.value)


@pytest.mark.parametrize("public", [False, True])
def test_missing_codex_spec_is_typed_error_before_any_spawn(monkeypatch, tmp_path, public):
    from orchestrator import LLMSession

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    session = LLMSession(provider="codex", workdir=tmp_path)
    result = session.ask("question") if public else session._ask_codex("question")
    assert result.status == "error"
    assert "provider_required" in result
    assert "codex_provider_spec" in result


def test_missing_codex_spec_is_not_reported_ready_or_mock(monkeypatch, tmp_path):
    from orchestrator import LLMSession

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not probe a CLI"))
    session = LLMSession(provider="codex", workdir=tmp_path)
    assert session.start() is False
    assert session.ask("question").status == "error"


def test_builtin_codex_registry_and_fallback_explicitly_select_openai(tmp_path):
    from utils import load_tools

    for tools in (load_tools(), load_tools(tmp_path / "absent.yaml")):
        codex = next(tool for tool in tools if tool["id"] == "codex")
        assert codex["label"] == "Codex (OpenAI)"
        assert codex["codex_provider_spec"] == {"name": "openai"}


def test_config_builder_preserves_explicit_provider_metadata(monkeypatch):
    import config_helper

    spec = {"name": "openai"}
    monkeypatch.setattr(config_helper, "LLMSession", lambda **kwargs: kwargs)
    session = config_helper.ConfigBuilder({"backend": {"provider": "codex", "codex_provider_spec": spec}}).create_llm_session()
    assert session["provider"] == "codex"
    assert session["codex_provider_spec"] == spec


def test_builtin_openai_endpoint_is_explicit_not_an_ambient_proxy(tmp_path):
    from cli_contract import codex_isolation_args

    args = codex_isolation_args(tmp_path, {"name": "openai"})
    assert 'openai_base_url="https://api.openai.com/v1"' in args


@pytest.mark.parametrize("directory", ["plain", "path.with.dots", "space . точка", 'quote"and\\slash'])
def test_codex_project_trust_override_preserves_exact_path_as_toml_value(tmp_path, directory):
    from cli_contract import codex_isolation_args

    workdir = tmp_path / directory
    args = codex_isolation_args(workdir, {"name": "openai"})
    settings = [args[index + 1] for index, arg in enumerate(args) if arg == "-c"]
    # Codex splits override keys on dots; quoted dynamic keys are not TOML keys.
    # Put the path inside the TOML value, which is actually parsed as TOML.
    project_settings = [setting for setting in settings if setting.startswith("projects=")]
    assert len(project_settings) == 1
    assert tomllib.loads(project_settings[0]) == {
        "projects": {str(workdir.resolve()): {"trust_level": "untrusted"}}
    }
    assert "--strict-config" in args and "--ignore-user-config" in args
    assert "project_root_markers=[]" in settings


@pytest.mark.skipif(os.environ.get("GHOST_NATIVE_CODEX_CONFIG") != "1",
                    reason="opt-in native Codex config validation; no inference")
def test_native_codex_accepts_trust_override_before_controlled_config_error(tmp_path):
    from cli_contract import codex_isolation_args

    workdir = tmp_path / 'space . точка "quote"'
    workdir.mkdir()
    args = codex_isolation_args(workdir, {"name": "openai"})
    # Never run this probe if the fail-closed config guard is absent. The
    # unknown top-level sentinel forces config validation to stop before any
    # session/model startup, while still validating the projects override.
    assert "--strict-config" in args and "--ignore-user-config" in args
    result = subprocess.run(
        ["codex", "exec", *args, "--ephemeral", "--sandbox", "read-only",
         "-c", "zzzz_ghost_config_probe=true", "config-only probe must not reach inference"],
        cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == (
        "Error loading config.toml: unknown configuration field "
        "`zzzz_ghost_config_probe` in -c/--config override"
    )


def test_codex_explicit_provider_overrides_ambient_environment_and_project(tmp_path, capture_cli, monkeypatch):
    from orchestrator import LLMSession

    monkeypatch.setenv("GHOST_PROVIDER_KEY", "fixture-value-never-copied")
    spec = {"name": "ghost_proxy", "base_url": "https://llm.example.test/v1",
            "wire_api": "responses", "env_key": "GHOST_PROVIDER_KEY"}
    result = LLMSession(provider="codex", workdir=tmp_path,
                        codex_provider_spec=spec).ask("question")
    assert result.status == "success"
    payload = json.loads(result)
    args = payload["args"]
    assert "--ignore-user-config" in args and "--ignore-rules" in args
    settings = [args[index + 1] for index, arg in enumerate(args) if arg == "-c"]
    assert 'model_provider="ghost_proxy"' in settings
    assert 'model_providers.ghost_proxy.env_key="GHOST_PROVIDER_KEY"' in settings
    assert "project_doc_max_bytes=0" in settings
    assert "project_root_markers=[]" in settings
    projects = next(setting for setting in settings if setting.startswith("projects="))
    assert tomllib.loads(projects) == {
        "projects": {str(tmp_path.resolve()): {"trust_level": "untrusted"}}
    }
    for feature in ("hooks", "plugins", "apps"):
        assert any(args[index:index + 2] == ["--disable", feature] for index in range(len(args)))
    assert "fixture-value-never-copied" not in json.dumps(payload)
    assert payload["cwd"] == str(tmp_path)
    assert "env" not in capture_cli[0][1]  # Auth environment retained; no fabricated home.


@pytest.mark.parametrize("spec", [
    {}, {"name": "openai", "base_url": "https://example.test"},
    {"name": "proxy", "base_url": "https://example.test", "api_key": "must-not-leak"},
    {"name": "proxy", "base_url": "https://user:must-not-leak@example.test", "env_key": "KEY"},
    {"name": "proxy", "base_url": "https://example.test?token=must-not-leak", "env_key": "KEY"},
    {"name": "proxy", "base_url": "https://example.test", "wire_api": "chat", "env_key": "KEY"},
    {"name": "proxy", "base_url": "https://example.test", "env_key": "KEY", "requires_openai_auth": True},
    {"name": "proxy", "base_url": "https://example.test"},
])
def test_unsupported_or_secret_provider_fields_fail_without_echoing_values(tmp_path, spec):
    from cli_contract import CLIContractError, codex_isolation_args

    with pytest.raises(CLIContractError) as error:
        codex_isolation_args(tmp_path, spec)
    assert error.value.code == "provider_invalid"
    assert "must-not-leak" not in str(error.value)


@pytest.mark.parametrize("provider,stream", [("claude", False), ("claude", True), ("codex", False)])
def test_screenshot_outside_cwd_is_exact_private_snapshot_and_cleaned(tmp_path, capture_cli, provider, stream):
    from orchestrator import LLMSession

    working = tmp_path / "work"
    working.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    image = outside / "selected.png"
    image.write_bytes(PNG)
    (outside / "CLAUDE.md").write_text("must not become instructions")
    (outside / "private.txt").write_text("must not be granted access")
    session = LLMSession(provider=provider, workdir=working, codex_provider_spec={"name": "openai"})
    result = (session.ask_stream("question", lambda text: None, str(image)) if stream
              else session.ask("question", str(image)))
    assert result.status == "success"
    payload = json.loads(result)
    assert list(payload["files"].values()) == [base64.b64encode(PNG).decode()]
    staged = Path(next(iter(payload["files"])))
    assert staged != image and staged.parent != outside
    assert not staged.exists() and not staged.parent.exists()
    assert image.read_bytes() == PNG
    if provider == "claude":
        assert "--safe-mode" in payload["args"]
        assert "--restricted" in payload["args"]
        assert payload["directories"] == [{"path": str(staged.parent), "mode": 0o700, "entries": [staged.name]}]
        assert str(staged) in payload["prompt"]
        assert str(image) not in payload["prompt"]
    else:
        assert "--image" in payload["args"]
        assert "--add-dir" not in payload["args"]


def test_generic_screenshot_is_explicitly_unsupported_before_read_or_spawn(monkeypatch, tmp_path):
    from orchestrator import LLMSession

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    result = LLMSession(provider="generic", command="anything", workdir=tmp_path).ask("question", "/nonexistent.png")
    assert result.status == "error"
    assert "screenshot_unsupported" in result


@pytest.mark.parametrize("kind", ["file_symlink", "parent_symlink", "directory", "fifo", "empty", "oversize", "not_image"])
def test_screenshot_validation_rejects_unsafe_inputs(tmp_path, kind):
    from cli_contract import CLIContractError, read_screenshot

    source = tmp_path / "capture.png"
    source.write_bytes(PNG)
    if kind == "file_symlink":
        target = tmp_path / "link.png"
        target.symlink_to(source)
    elif kind == "parent_symlink":
        directory = tmp_path / "real"
        directory.mkdir()
        (directory / "capture.png").write_bytes(PNG)
        link = tmp_path / "linked"
        link.symlink_to(directory, target_is_directory=True)
        target = link / "capture.png"
    elif kind == "directory":
        target = tmp_path
    elif kind == "fifo":
        target = tmp_path / "pipe.png"
        os.mkfifo(target)
    else:
        target = source
        source.write_bytes({"empty": b"", "oversize": b"x" * 100, "not_image": b"ordinary text"}[kind])
    with pytest.raises(CLIContractError):
        read_screenshot(target, max_bytes=80)


def test_staged_screenshot_is_removed_on_exception(tmp_path):
    from cli_contract import read_screenshot

    source = tmp_path / "capture.png"
    source.write_bytes(PNG)
    with pytest.raises(RuntimeError):
        with read_screenshot(source).staged() as staged:
            assert staged.read_bytes() == PNG
            assert staged.stat().st_mode & 0o777 == 0o600
            raise RuntimeError("request failed")
    assert not staged.parent.exists()
