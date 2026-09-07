"""Local worker lifecycle checks; audio, screenshots and LLMs are controlled fakes."""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication


SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import worker as worker_module  # noqa: E402


class Result(str):
    def __new__(cls, text, status="success"):
        instance = super().__new__(cls, text)
        instance.status = status
        return instance


@pytest.fixture(scope="module")
def app():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
        application = QApplication.instance() or QApplication([])
    return application


def wait_for(app, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        threading.Event().wait(0.005)
    assert predicate(), "local worker did not reach the expected state"


@pytest.fixture
def harness(monkeypatch, app):
    state = SimpleNamespace(
        sessions=[], audio=[], grabbers=[], workers=[], threads=[], releases=[],
        audio_ok=True, answer=Result("answer"), behavior=None,
    )

    class FakeSession:
        provider = "fake"

        def __init__(self, **kwargs):
            self.closed = 0
            self.calls = []
            self.warmup = threading.Event()
            self.cancelled = threading.Event()
            state.sessions.append(self)

        def start(self):
            return True

        def ask(self, question):
            self.warmup.set()
            return Result("warmup")

        def ask_stream(self, question, on_chunk, screenshot_path=None):
            self.calls.append(question)
            if state.behavior:
                return state.behavior(self, question, on_chunk)
            on_chunk("draft")
            return state.answer

        def close(self):
            self.closed += 1
            self.cancelled.set()

    class FakeAudio:
        def __init__(self, on_question, **kwargs):
            self.callback = on_question
            self.started = False
            self.stopped = 0
            state.audio.append(self)

        def start(self):
            self.started = True
            return state.audio_ok

        def stop(self):
            self.stopped += 1

    class FakeGrabber:
        def __init__(self, **kwargs):
            self.closed = 0
            state.grabbers.append(self)

        def grab_force(self):
            raise AssertionError("the worker must not capture a screenshot in this test")

        def close(self):
            self.closed += 1

    monkeypatch.setattr(worker_module, "LLMSession", FakeSession)
    monkeypatch.setattr(worker_module, "AudioCapture", FakeAudio)
    monkeypatch.setattr(worker_module, "ScreenGrabber", FakeGrabber)

    def new_worker():
        worker = worker_module.PipelineWorker({
            "screenshot": {"enabled": False, "path": "unused.png", "monitor": 0,
                           "hash_threshold": 5},
            "backend": {"provider": "fake", "response_timeout": 1},
        })
        state.workers.append(worker)
        return worker

    def thread(target):
        job = threading.Thread(target=target, daemon=True)
        state.threads.append(job)
        job.start()
        return job

    state.new_worker = new_worker
    state.thread = thread
    state.new_session = FakeSession
    yield state
    # Always release local fakes, even when a RED assertion fails on old code.
    for event in state.releases:
        event.set()
    for session in state.sessions:
        session.cancelled.set()
    for worker in state.workers:
        worker._stop_event.set()
    for job in state.threads:
        job.join(timeout=2)
    for worker in state.workers:
        assert worker.wait(3000), "test worker failed to stop during cleanup"
    app.processEvents()


@pytest.mark.parametrize("status,text", [
    ("error", "[fake failed: exit 7]"),
    ("timeout", "[timeout]"),
    ("cancelled", "[cancelled]"),
])
def test_failure_after_chunk_is_error_not_ready(harness, status, text):
    worker = harness.new_worker()
    worker._session = harness.new_session()
    harness.answer = Result(text, status)
    ready, errors, chunks = [], [], []
    worker.answer_ready.connect(ready.append)
    worker.error_occurred.connect(errors.append)
    worker.answer_chunk.connect(chunks.append)

    worker._on_question("question")

    assert chunks == ["draft"]
    assert ready == []
    assert errors == [text]


def test_literal_failure_token_can_be_a_successful_answer(harness):
    worker = harness.new_worker()
    worker._session = harness.new_session()
    harness.answer = Result("[timeout]", "success")
    ready, errors = [], []
    worker.answer_ready.connect(ready.append)
    worker.error_occurred.connect(errors.append)

    worker._on_question("question")

    assert ready == ["[timeout]"]
    assert errors == []


@pytest.mark.parametrize("answer,error", [
    (Result("mock answer", "mock"), "[fake offline]"),
    ("", "[fake failed: no output]"),
])
def test_mock_and_empty_answers_do_not_enter_success_channel(harness, answer, error):
    worker = harness.new_worker()
    worker._session = harness.new_session()
    harness.answer = answer
    ready, errors = [], []
    worker.answer_ready.connect(ready.append)
    worker.error_occurred.connect(errors.append)

    worker._on_question("question")

    assert ready == []
    assert errors == [error]


def test_stop_during_screenshot_prevents_model_request(harness):
    worker = harness.new_worker()
    worker._session = harness.new_session()
    worker._cfg["screenshot"]["enabled"] = True

    def finish_fake_screenshot():
        worker.stop()
        return Path("unused-screenshot.png")

    worker._grabber = SimpleNamespace(grab_force=finish_fake_screenshot)
    ready, errors = [], []
    worker.answer_ready.connect(ready.append)
    worker.error_occurred.connect(errors.append)

    worker._on_question("question")

    assert harness.sessions[0].calls == []
    assert ready == []
    assert errors == []


def test_exception_after_chunk_is_error_not_ready(harness):
    def fail(session, question, on_chunk):
        on_chunk("draft")
        raise RuntimeError("local test failure")

    harness.behavior = fail
    worker = harness.new_worker()
    worker._session = harness.new_session()
    ready, errors = [], []
    worker.answer_ready.connect(ready.append)
    worker.error_occurred.connect(errors.append)

    worker._on_question("question")

    assert ready == []
    assert errors == ["[error: RuntimeError]"]


def test_stopped_worker_does_not_start_resources(harness, app):
    worker = harness.new_worker()
    # Models Stop arriving before QThread enters run(), without a timing race.
    worker._stop_event.set()
    worker.start()
    wait_for(app, lambda: worker.isFinished())

    assert harness.sessions == []
    assert harness.audio == []
    assert harness.grabbers == []


def test_audio_start_failure_closes_initialized_resources(harness, app):
    harness.audio_ok = False
    worker = harness.new_worker()
    # Construct the fake screen handle so this path must close both resources.
    worker._cfg["screenshot"]["enabled"] = True
    errors = []
    worker.error_occurred.connect(errors.append)
    worker.start()
    wait_for(app, lambda: worker.isFinished())

    assert harness.sessions[0].closed == 1
    assert harness.grabbers[0].closed == 1
    assert harness.audio[0].stopped == 1
    assert errors == ["Audio capture failed — no loopback device"]


def test_start_does_not_make_hidden_model_warmup_call(harness, app):
    worker = harness.new_worker()
    worker.start()
    wait_for(app, lambda: bool(harness.audio) and harness.audio[0].started)

    assert not harness.sessions[0].warmup.wait(0.05)
    assert harness.sessions[0].calls == []


def test_stop_is_nonblocking_cancels_request_and_rejects_late_callback(harness, app):
    entered = threading.Event()

    def blocked(session, question, on_chunk):
        on_chunk("draft")
        entered.set()
        assert session.cancelled.wait(2), "stop did not close the fake LLM session"
        on_chunk("late chunk")
        return Result("late final")

    harness.behavior = blocked
    worker = harness.new_worker()
    chunks, ready, errors = [], [], []
    worker.answer_chunk.connect(chunks.append)
    worker.answer_ready.connect(ready.append)
    worker.error_occurred.connect(errors.append)
    worker.start()
    wait_for(app, lambda: bool(harness.audio) and harness.audio[0].started)
    job = harness.thread(lambda: harness.audio[0].callback("first"))
    wait_for(app, entered.is_set)

    before = time.monotonic()
    worker.stop()
    assert time.monotonic() - before < 0.2, "Stop must not wait for audio/LLM on the GUI thread"
    wait_for(app, lambda: worker.isFinished() and not job.is_alive())
    harness.audio[0].callback("late question")
    app.processEvents()

    assert chunks == ["draft"]
    assert ready == []
    assert errors == []
    assert harness.sessions[0].calls == ["first"]
    assert harness.sessions[0].closed == 1


def test_questions_are_serialized_within_one_worker(harness, app):
    first_entered, release_first, second_entered = (threading.Event() for _ in range(3))
    harness.releases.append(release_first)
    timeline = []

    def ordered(session, question, on_chunk):
        timeline.append((question, "start"))
        if question == "first":
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        on_chunk(question)
        timeline.append((question, "end"))
        return Result(question)

    harness.behavior = ordered
    worker = harness.new_worker()
    worker._session = harness.new_session()
    first = harness.thread(lambda: worker._on_question("first"))
    assert first_entered.wait(1)
    second = harness.thread(lambda: worker._on_question("second"))
    assert not second_entered.wait(0.05), "two questions entered the LLM concurrently"
    release_first.set()
    wait_for(app, lambda: not first.is_alive() and not second.is_alive())

    assert timeline == [("first", "start"), ("first", "end"),
                        ("second", "start"), ("second", "end")]


def test_worker_forwards_explicit_codex_provider_spec(harness, monkeypatch):
    arguments = []

    def make_session(**kwargs):
        arguments.append(kwargs)
        return harness.new_session(**kwargs)

    monkeypatch.setattr(worker_module, "LLMSession", make_session)
    harness.audio_ok = False
    worker = harness.new_worker()
    provider_spec = {"name": "local-test", "base_url": "https://example.invalid/v1"}
    worker._cfg["backend"]["codex_provider_spec"] = provider_spec

    worker.run()

    assert arguments[0].get("codex_provider_spec") == provider_spec


@pytest.mark.parametrize("resource", ["audio", "session", "grabber", None])
def test_cleanup_outcome_is_true_only_after_all_resources_close(harness, monkeypatch, resource):
    worker = harness.new_worker()
    assert worker.cleanup_complete is False
    harness.audio_ok = False
    worker._cfg["screenshot"]["enabled"] = True

    def failed_close():
        raise RuntimeError("PRIVATE cleanup exception")

    if resource is not None:
        attr = {"audio": "AudioCapture", "session": "LLMSession", "grabber": "ScreenGrabber"}[resource]
        original = getattr(worker_module, attr)

        def make_resource(**kwargs):
            instance = original(**kwargs)
            setattr(instance, "stop" if resource == "audio" else "close", failed_close)
            return instance

        monkeypatch.setattr(worker_module, attr, make_resource)

    worker.run()

    assert worker.cleanup_complete is (resource is None)


def test_real_audio_startup_failure_is_signalled_before_shared_cancellation(harness, monkeypatch):
    import audio_capture

    def fail(capture):
        raise RuntimeError("PRIVATE device exception")

    monkeypatch.setattr(audio_capture.AudioCapture, "_start_capture", fail)
    monkeypatch.setattr(worker_module, "AudioCapture", audio_capture.AudioCapture)
    worker = harness.new_worker()
    errors = []
    worker.error_occurred.connect(errors.append)

    worker.run()

    assert errors == ["[audio startup error: RuntimeError]"]
    assert worker._stop_event.is_set()
    assert worker.cleanup_complete is True
