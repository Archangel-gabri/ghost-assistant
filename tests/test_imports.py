"""Smoke tests: modules import and core factories behave."""

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def test_core_modules_import():
    import utils, orchestrator, stt_fast, screen_monitor, config_helper  # noqa: F401
    assert hasattr(orchestrator, "LLMSession")
    assert hasattr(stt_fast, "create_stt")


def test_tools_registry_loads():
    from utils import load_tools
    tools = load_tools()
    assert tools and all("id" in t and "models" in t for t in tools)


def test_stt_auto_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from stt_fast import create_stt, FasterWhisperSTT
    assert isinstance(create_stt("auto"), FasterWhisperSTT)


def test_generic_tool_adapter_runs():
    from orchestrator import LLMSession
    s = LLMSession(provider="generic", model="m1", command="echo {model}")
    assert s.start()
    assert "m1" in s.ask("hi")
