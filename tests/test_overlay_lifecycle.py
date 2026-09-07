"""Overlay lifecycle regressions using real queued Qt signals and fake workers."""

import sys
import threading
import types
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication


SRC = Path(__file__).resolve().parent.parent / "src"


class FakeWorker(QObject):
    question_detected = Signal(str)
    answer_chunk = Signal(str)
    answer_ready = Signal(str)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    finished = Signal()

    def __init__(self, config):
        super().__init__()
        self.stop_calls = 0
        self.cleanup_complete = True

    def start(self):
        pass

    def stop(self):
        self.stop_calls += 1

    def wait(self, *args, **kwargs):
        raise AssertionError("The GUI must not wait for worker cleanup")


@pytest.fixture(scope="session")
def qt_app():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def overlay(monkeypatch, qt_app):
    monkeypatch.syspath_prepend(str(SRC))
    import overlay_window

    fake_module = types.ModuleType("worker")
    fake_module.PipelineWorker = FakeWorker
    monkeypatch.setitem(sys.modules, "worker", fake_module)
    monkeypatch.setattr(overlay_window.OverlayWindow, "_setup_tray", lambda self: None)
    monkeypatch.setattr(overlay_window.OverlayWindow, "raise_", lambda self: None)
    window = overlay_window.OverlayWindow({"backend": {"workdir": ""}})
    yield window
    window._on_stop()
    if window._worker is not None:
        window._worker.finished.emit()
        qt_app.processEvents()
    window._quit_close = True
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def enqueue(*events):
    """Emit off the GUI thread, leaving delivery to the real Qt event queue."""
    def emit_events():
        for signal, args in events:
            signal.emit(*args)

    thread = threading.Thread(target=emit_events)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.parametrize("error", ["[claude failed: exit 7]", "[timeout]"])
def test_error_after_chunks_replaces_draft_and_never_enters_history(overlay, qt_app, error):
    overlay._on_start()
    worker = overlay._worker
    enqueue((worker.question_detected, ("question",)), (worker.answer_chunk, ("draft answer",)))
    assert "draft answer" not in overlay._answer_area.toPlainText()
    qt_app.processEvents()
    assert "draft answer" in overlay._answer_area.toPlainText()

    enqueue((worker.error_occurred, (error,)))
    qt_app.processEvents()

    text = overlay._answer_area.toPlainText()
    assert error in text
    assert "draft answer" not in text
    assert "Q: question" in text
    assert list(overlay._history) == []
    enqueue((worker.answer_chunk, ("late chunk",)))
    qt_app.processEvents()
    assert "late chunk" not in overlay._answer_area.toPlainText()


def test_success_uses_final_text_once_and_does_not_parse_error_like_content(overlay, qt_app):
    overlay._on_start()
    worker = overlay._worker
    enqueue(
        (worker.question_detected, ("what does the marker mean?",)),
        (worker.answer_chunk, ("draft",)),
        (worker.answer_ready, ("[timeout] is the literal marker",)),
    )
    qt_app.processEvents()

    text = overlay._answer_area.toPlainText()
    assert "draft" not in text
    assert text.count("[timeout] is the literal marker") == 1
    assert list(overlay._history) == ["[timeout] is the literal marker"]
    enqueue((worker.answer_ready, ("duplicate final",)), (worker.answer_chunk, ("late chunk",)))
    qt_app.processEvents()
    assert overlay._answer_area.toPlainText() == text
    assert list(overlay._history) == ["[timeout] is the literal marker"]


def test_stop_retains_worker_and_disables_restart_until_cleanup_finishes(overlay, qt_app):
    overlay._on_start()
    worker = overlay._worker
    enqueue((worker.question_detected, ("queued before stop",)), (worker.answer_chunk, ("old",)))

    overlay._on_stop()

    assert worker.stop_calls == 1
    assert overlay._worker is worker
    assert not overlay._start.isEnabled()
    assert not overlay._stop.isEnabled()
    stopped_text = overlay._answer_area.toPlainText()
    overlay._on_start()
    assert overlay._worker is worker
    qt_app.processEvents()
    assert overlay._answer_area.toPlainText() == stopped_text
    assert list(overlay._history) == []

    enqueue((worker.finished, ()))
    qt_app.processEvents()
    assert overlay._worker is None
    assert overlay._start.isEnabled()
    assert not overlay._stop.isEnabled()


def test_old_queued_events_cannot_change_restarted_overlay(overlay, qt_app):
    overlay._on_start()
    old_worker = overlay._worker
    overlay._on_stop()
    enqueue((old_worker.finished, ()))
    qt_app.processEvents()
    overlay._on_start()
    current_worker = overlay._worker
    enqueue((current_worker.question_detected, ("current question",)),
            (current_worker.answer_chunk, ("current draft",)))
    qt_app.processEvents()
    before = (overlay._answer_area.toPlainText(), overlay._status_lbl.text(), list(overlay._history))

    enqueue(
        (old_worker.question_detected, ("old question",)),
        (old_worker.answer_chunk, ("old chunk",)),
        (old_worker.answer_ready, ("old answer",)),
        (old_worker.status_changed, ("listening",)),
        (old_worker.error_occurred, ("old error",)),
        (old_worker.finished, ()),
    )
    qt_app.processEvents()

    assert overlay._worker is current_worker
    assert (overlay._answer_area.toPlainText(), overlay._status_lbl.text(), list(overlay._history)) == before
    assert not overlay._start.isEnabled()
    assert overlay._stop.isEnabled()


def test_events_queued_before_restart_are_rejected_after_restart(overlay, qt_app):
    overlay._on_start()
    old_worker = overlay._worker
    overlay._on_stop()

    class RestartAfterCleanup(QObject):
        def __init__(self):
            super().__init__()
            self.restarted = False

        @Slot()
        def restart(self):
            if not self.restarted:
                self.restarted = True
                overlay._on_start()

    restarter = RestartAfterCleanup()
    old_worker.finished.connect(restarter.restart, Qt.QueuedConnection)
    enqueue(
        (old_worker.finished, ()),
        (old_worker.question_detected, ("stale question",)),
        (old_worker.answer_chunk, ("stale chunk",)),
        (old_worker.answer_ready, ("stale answer",)),
        (old_worker.status_changed, ("listening",)),
        (old_worker.error_occurred, ("stale error",)),
        (old_worker.finished, ()),
    )
    assert overlay._worker is old_worker
    assert not restarter.restarted
    qt_app.processEvents()

    assert restarter.restarted
    assert overlay._worker is not None
    assert overlay._worker is not old_worker
    assert "stale" not in overlay._answer_area.toPlainText()
    assert "Запуск" in overlay._status_lbl.text()
    assert list(overlay._history) == []
    assert not overlay._start.isEnabled()
    assert overlay._stop.isEnabled()


def test_quit_is_idempotent_and_waits_asynchronously_for_cleanup(overlay, qt_app, monkeypatch):
    overlay._on_start()
    worker = overlay._worker
    closed = []
    monkeypatch.setattr(overlay, "close", lambda: closed.append(True))

    overlay._quit()
    overlay._quit()

    assert worker.stop_calls == 1
    assert overlay._worker is worker
    assert closed == []
    assert not overlay._start.isEnabled()
    enqueue((worker.question_detected, ("late question",)), (worker.finished, ()))
    qt_app.processEvents()
    assert overlay._worker is None
    assert closed == [True]
    assert "late question" not in overlay._answer_area.toPlainText()
    assert list(overlay._history) == []


def test_untrusted_question_and_answer_remain_literal_text(overlay, qt_app):
    overlay._on_start()
    worker = overlay._worker
    question = '<b>question</b> & <img src="file:///not-a-real-image.png">'
    answer = '<i>answer</i> & <a href="https://example.invalid">link</a>'
    enqueue((worker.question_detected, (question,)), (worker.answer_ready, (answer,)))
    qt_app.processEvents()

    text = overlay._answer_area.toPlainText()
    assert question in text
    assert answer in text
    assert list(overlay._history) == [answer]


def test_legacy_show_answer_still_displays_literal_text(overlay):
    overlay.show_answer("<b>legacy</b>")
    assert "<b>legacy</b>" in overlay._answer_area.toPlainText()
    assert list(overlay._history) == ["<b>legacy</b>"]


def test_selected_codex_provider_spec_reaches_worker_config(overlay, qt_app):
    index = next(index for index, tool in enumerate(overlay._tools) if tool["id"] == "codex")
    overlay._tool.setCurrentIndex(index)
    overlay._on_start()
    assert overlay._cfg["backend"]["provider"] == "codex"
    assert overlay._cfg["backend"]["codex_provider_spec"] == {"name": "openai"}


@pytest.mark.parametrize("quit_requested", [False, True])
def test_cleanup_failure_keeps_owner_and_blocks_restart_and_quit(overlay, qt_app, monkeypatch, quit_requested):
    overlay._on_start()
    worker = overlay._worker
    worker.cleanup_complete = False
    closed = []
    monkeypatch.setattr(overlay, "close", lambda: closed.append(True))
    if quit_requested:
        overlay._quit()
    else:
        overlay._on_stop()
    enqueue((worker.finished, ()))
    qt_app.processEvents()
    assert overlay._worker is worker
    assert not overlay._start.isEnabled()
    assert not overlay._stop.isEnabled()
    assert "очистка" in overlay._status_lbl.text().lower()
    before = worker.stop_calls
    overlay._on_start()
    overlay._quit()
    assert overlay._worker is worker
    assert worker.stop_calls == before
    assert closed == []

    from PySide6.QtGui import QCloseEvent
    event = QCloseEvent()
    overlay.closeEvent(event)
    assert not event.isAccepted()

    # Teardown can release this synthetic worker explicitly; no production
    # cleanup retry or main-thread wait is introduced by the UI contract.
    worker.cleanup_complete = True


@pytest.fixture
def configured_overlay(overlay, qt_app):
    windows = []

    def create(backend):
        window = type(overlay)({"backend": dict(backend)})
        windows.append(window)
        return window

    yield create
    for window in windows:
        window._on_stop()
        if window._worker is not None:
            window._worker.finished.emit()
            qt_app.processEvents()
        window._quit_close = True
        window.close()
        window.deleteLater()
        qt_app.processEvents()


CUSTOM_SPEC = {"name": "configured_proxy", "base_url": "https://proxy.example.test/v1", "env_key": "PROXY_KEY"}


@pytest.mark.parametrize("backend", [
    {"provider": "codex", "model": "proxy-model", "codex_provider_spec": CUSTOM_SPEC},
    {"provider": "codex", "model": "gpt-5.6-sol", "codex_provider_spec": {"name": "openai"}},
    {"provider": "claude", "model": "opus"},
    {"provider": "generic", "model": "custom-model", "command": "custom-cli --model {model}"},
])
def test_initial_backend_selection_reaches_worker_without_silent_reroute(configured_overlay, backend):
    window = configured_overlay(backend)
    assert window._current_tool()["provider"] == backend["provider"]
    assert window._model.currentText() == backend["model"]
    window._on_start()
    assert window._worker is not None
    for key, value in backend.items():
        assert window._cfg["backend"][key] == value


@pytest.mark.parametrize("spec", [None, {"name": "proxy", "api_key": "must-not-be-shown"}])
def test_invalid_initial_codex_configuration_blocks_start_without_fallback(configured_overlay, spec):
    window = configured_overlay({"provider": "codex", "model": "configured-model", "codex_provider_spec": spec})
    assert window._current_tool()["provider"] == "codex"
    window._on_start()
    assert window._worker is None
    assert window._cfg["backend"]["provider"] == "codex"
    assert "provider_" in window._status_lbl.text()
    assert "must-not-be-shown" not in window._status_lbl.text()


@pytest.mark.parametrize("chosen", ["claude", "codex"])
def test_explicit_manual_ui_choice_overrides_initial_custom_backend(configured_overlay, chosen):
    window = configured_overlay({"provider": "codex", "model": "proxy-model", "codex_provider_spec": CUSTOM_SPEC})
    index = next(index for index, tool in enumerate(window._tools) if tool["id"] == chosen)
    window._tool.setCurrentIndex(index)
    window._on_start()
    assert window._cfg["backend"]["provider"] == chosen
    assert window._cfg["backend"]["codex_provider_spec"] == ({"name": "openai"} if chosen == "codex" else None)


def test_registry_matching_does_not_coerce_invalid_provider_auth_integer(configured_overlay, monkeypatch):
    import overlay_window

    valid = {"name": "example_proxy", "base_url": "https://example.invalid/v1", "requires_openai_auth": True}
    invalid = {**valid, "requires_openai_auth": 1}
    monkeypatch.setattr(overlay_window, "load_tools", lambda: [
        {"id": "proxy", "label": "Proxy", "provider": "codex", "models": ["proxy-model"], "codex_provider_spec": valid}
    ])
    window = configured_overlay({"provider": "codex", "model": "proxy-model", "codex_provider_spec": invalid})
    window._on_start()
    assert window._worker is None
    assert "provider_invalid" in window._status_lbl.text()
    assert type(window._current_tool()["codex_provider_spec"]["requires_openai_auth"]) is int
