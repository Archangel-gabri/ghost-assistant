"""Owner-local routing without ambient CLI customizations or model requests."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.mark.parametrize("local,explicit", [(False, False), (True, False), (True, True)])
def test_launcher_uses_local_config_but_explicit_choice_wins(tmp_path, local, explicit):
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copyfile(ROOT / "run.sh", project / "run.sh")
    if local:
        (project / "config.local.yaml").write_text("backend: {}\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    interpreter = binaries / "python3"
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import argparse,json,sys\n"
        "parser=argparse.ArgumentParser()\n"
        "parser.add_argument('--config')\n"
        "args,rest=parser.parse_known_args(sys.argv[2:])\n"
        "print(json.dumps({'config':args.config,'rest':rest}))\n"
    )
    interpreter.chmod(0o700)
    args = ["bash", str(project / "run.sh")]
    if explicit:
        args += ["--config", "explicit profile.yaml"]
    args += ["--once", "synthetic question"]
    result = subprocess.run(
        args, cwd=tmp_path, capture_output=True, text=True, timeout=5,
        env={**os.environ, "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}"},
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    expected = ("explicit profile.yaml" if explicit else
                str(project / ("config.local.yaml" if local else "src/config-fast.yaml")))
    assert payload == {"config": expected, "rest": ["--once", "synthetic question"]}


SPEC = {"name": "owner_test", "base_url": "https://provider.example.test/v1",
        "wire_api": "responses", "env_key": "GHOST_TEST_PROVIDER_KEY"}


@pytest.mark.parametrize("value", [None, "", " \t\n"])
def test_missing_custom_environment_fails_before_cli_readiness_probe(monkeypatch, tmp_path, value):
    from orchestrator import LLMSession

    if value is None:
        monkeypatch.delenv(SPEC["env_key"], raising=False)
    else:
        monkeypatch.setenv(SPEC["env_key"], value)
    monkeypatch.setattr(LLMSession, "_find_binary", lambda *args: pytest.fail("no CLI probe"))
    session = LLMSession(provider="codex", workdir=tmp_path, codex_provider_spec=SPEC)
    assert session.start() is False
    assert session.mode == "configuration-error"
    assert "provider_env_missing" in session.startup_error
    assert SPEC["env_key"] in session.startup_error
    assert session.ask("question").status == "error"
    session.close()


@pytest.mark.parametrize("entry", ["ask", "stream", "direct"])
def test_missing_custom_environment_fails_before_screenshot_or_spawn(monkeypatch, tmp_path, entry):
    import orchestrator

    monkeypatch.delenv(SPEC["env_key"], raising=False)
    monkeypatch.setattr(orchestrator, "read_screenshot", lambda *args: pytest.fail("no image read"))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("no spawn"))
    session = orchestrator.LLMSession(provider="codex", workdir=tmp_path, codex_provider_spec=SPEC)
    if entry == "ask":
        result = session.ask("question", screenshot_path="not-read.png")
    elif entry == "stream":
        result = session.ask_stream("question", lambda text: pytest.fail("no chunk"),
                                    screenshot_path="not-read.png")
    else:
        result = session._ask_codex("question")
    assert result.status == "error"
    assert "provider_env_missing" in result
    assert SPEC["env_key"] in result
    assert "provider.example.test" not in result
    assert session._active_processes == set()


def test_present_custom_environment_preserves_explicit_isolation_without_echoing_key(monkeypatch, tmp_path):
    from orchestrator import LLMSession, LLMResult

    monkeypatch.setenv(SPEC["env_key"], "private-test-value-must-not-echo")
    monkeypatch.setattr(LLMSession, "_find_binary", lambda *args: True)
    session = LLMSession(provider="codex", model="chosen-model", workdir=tmp_path,
                         codex_provider_spec=SPEC)
    captured = []
    monkeypatch.setattr(session, "_run_simple",
                        lambda provider, args, *a, **kw: (captured.append(args) or LLMResult("ok")))
    assert session.start() is True
    assert session.startup_error is None
    assert session.ask("question").status == "success"
    assert "--ignore-user-config" in captured[0]
    assert "--ignore-rules" in captured[0]
    assert 'model_provider="owner_test"' in captured[0]
    assert "private-test-value-must-not-echo" not in repr(captured)
    session.close()


@pytest.mark.parametrize("spec", [{"name": "openai"}, {
    "name": "owner_auth", "base_url": "https://provider.example.test/v1",
    "requires_openai_auth": True,
}])
def test_auth_backed_providers_do_not_require_an_environment_key(monkeypatch, tmp_path, spec):
    from orchestrator import LLMSession

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(SPEC["env_key"], raising=False)
    monkeypatch.setattr(LLMSession, "_find_binary", lambda *args: True)
    session = LLMSession(provider="codex", workdir=tmp_path, codex_provider_spec=spec)
    assert session.start() is True
    session.close()


def test_worker_reports_configuration_failure_without_starting_audio(monkeypatch, tmp_path):
    from orchestrator import LLMSession
    import worker

    monkeypatch.delenv(SPEC["env_key"], raising=False)
    monkeypatch.setattr(LLMSession, "_find_binary", lambda *args: True)
    monkeypatch.setattr(worker, "AudioCapture", lambda **kwargs: pytest.fail("no audio capture"))
    instance = worker.PipelineWorker({"screenshot": {"enabled": False}, "backend": {
        "provider": "codex", "workdir": str(tmp_path), "codex_provider_spec": SPEC,
    }})
    errors = []
    instance.error_occurred.connect(errors.append)
    instance.run()
    assert len(errors) == 1
    assert "provider_env_missing" in errors[0]
    assert SPEC["env_key"] in errors[0]
    assert instance.cleanup_complete is True


@pytest.fixture
def cli_resources(monkeypatch):
    import config_helper

    state = SimpleNamespace(captures=0, grabbers=0, grabber_closed=0, session_closed=0,
                            questions=[], fail_answer=False, fail_capture=False,
                            fail_grabber_close=False, configuration_error=False)

    class Session:
        provider = "test"
        model = "test-model"
        startup_error = "provider_env_missing: GHOST_TEST_PROVIDER_KEY"

        @property
        def mode(self):
            return "configuration-error" if state.configuration_error else "test"

        def start(self):
            return not state.configuration_error

        def ask(self, question, screenshot_path=None):
            state.questions.append((question, screenshot_path))
            if state.fail_answer:
                raise RuntimeError("synthetic answer failure")
            return "synthetic answer"

        def close(self):
            state.session_closed += 1

    class Grabber:
        def grab_force(self):
            state.captures += 1
            return Path("synthetic.png")

        def close(self):
            state.grabber_closed += 1
            if state.fail_grabber_close:
                raise RuntimeError("synthetic cleanup failure")

    def create_grabber(self):
        state.grabbers += 1
        if state.fail_capture:
            raise RuntimeError("synthetic capture initialization failure")
        return Grabber()

    monkeypatch.setattr(config_helper.ConfigBuilder, "create_llm_session", lambda self: Session())
    monkeypatch.setattr(config_helper.ConfigBuilder, "create_screen_grabber", create_grabber)
    return state


@pytest.mark.parametrize("mode", ["once", "interactive"])
@pytest.mark.parametrize("enabled", [False, True, None])
def test_cli_respects_screenshot_flag_and_preserves_enabled_default(cli_resources, monkeypatch, mode, enabled):
    import main

    config = {} if enabled is None else {"screenshot": {"enabled": enabled}}
    if mode == "once":
        main.run_once_cli(config, "question")
    else:
        inputs = iter(["question", "exit"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
        main.run_cli(config)
    capture = enabled is not False
    assert cli_resources.questions == [("question", "synthetic.png" if capture else None)]
    assert cli_resources.captures == int(capture)
    assert cli_resources.grabbers == int(capture)
    assert cli_resources.grabber_closed == int(capture)
    assert cli_resources.session_closed == 1


@pytest.mark.parametrize("failure", ["fail_answer", "fail_capture", "fail_grabber_close"])
def test_one_shot_cli_releases_session_even_if_capture_or_answer_or_cleanup_fails(cli_resources, failure):
    import main

    setattr(cli_resources, failure, True)
    with pytest.raises(RuntimeError, match="synthetic"):
        main.run_once_cli({}, "question")
    assert cli_resources.session_closed == 1
    assert cli_resources.grabber_closed == (0 if failure == "fail_capture" else 1)


@pytest.mark.parametrize("mode", ["once", "interactive"])
def test_cli_configuration_failure_does_not_open_screenshot_or_ask(cli_resources, monkeypatch, mode):
    import main

    cli_resources.configuration_error = True
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("no input after failed configuration"))
    with pytest.raises(ValueError, match="provider_env_missing"):
        if mode == "once":
            main.run_once_cli({}, "question")
        else:
            main.run_cli({})
    assert cli_resources.questions == []
    assert cli_resources.grabbers == 0
    assert cli_resources.session_closed == 1


@pytest.mark.parametrize("enabled", [False, True, None])
def test_overlay_initial_screenshot_toggle_preserves_configuration(monkeypatch, enabled):
    from PySide6.QtWidgets import QApplication
    from overlay_window import OverlayWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(OverlayWindow, "_setup_tray", lambda self: None)
    monkeypatch.setattr(OverlayWindow, "_position", lambda self: None)
    config = {} if enabled is None else {"screenshot": {"enabled": enabled}}
    window = OverlayWindow(config)
    try:
        assert window._scr.isChecked() is (enabled is not False)
    finally:
        window._quit_close = True
        window.close()
        window.deleteLater()
        app.processEvents()
