"""Test that Ghost modules can be imported."""

import pytest


def test_import_utils():
    """Test utils module import."""
    from ghost import utils
    assert utils.Provider.CLAUDE.value == "claude"


def test_import_orchestrator():
    """Test orchestrator module import."""
    from ghost import orchestrator
    assert hasattr(orchestrator, "LLMSession")


def test_import_config_helper():
    """Test config_helper module import."""
    from ghost import config_helper
    assert hasattr(config_helper, "ConfigBuilder")


def test_import_stt_fast():
    """Test stt_fast module import."""
    from ghost import stt_fast
    assert hasattr(stt_fast, "create_stt")
    assert hasattr(stt_fast, "MoonshineSTT")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
