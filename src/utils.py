"""Shared utilities across Ghost modules."""

import re
from pathlib import Path
from typing import Optional, Any
from enum import Enum


class Provider(Enum):
    """LLM provider."""
    CLAUDE = "claude"
    CODEX = "codex"


class Model(Enum):
    """LLM models."""
    # Claude
    HAIKU = "haiku"
    SONNET = "sonnet"
    FABLE = "fable"
    OPUS = "opus"

    # Codex
    GPT_5_6_LUNA = "gpt-5.6-luna"
    GPT_5_6_SOL = "gpt-5.6-sol"
    O3 = "o3"
    GPT_5_5 = "gpt-5.5"


MODELS_BY_PROVIDER = {
    Provider.CLAUDE: [Model.HAIKU, Model.SONNET, Model.FABLE, Model.OPUS],
    Provider.CODEX: [Model.GPT_5_6_LUNA, Model.GPT_5_6_SOL, Model.O3, Model.GPT_5_5],
}


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[A-Za-z]')


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from text."""
    return _ANSI_RE.sub('', text)


def ensure_project_root(workdir: Optional[str | Path]) -> Path:
    """Convert workdir to Path, default to script root."""
    if workdir:
        return Path(workdir)
    return Path(__file__).resolve().parent


def get_nested(d: dict, *keys: str, default: Any = None) -> Any:
    """Get deeply nested dict value: get_nested(cfg, 'backend', 'model')."""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, {})
        else:
            return default
    return d if d != {} else default


# ---------------------------------------------------------------------------
# Tools registry (user-editable tools.yaml drives the UI dropdowns)
# ---------------------------------------------------------------------------

_BUILTIN_TOOLS = [
    {"id": "claude", "label": "Claude Code", "provider": "claude",
     "models": ["sonnet", "haiku", "opus", "fable"]},
    {"id": "codex", "label": "Codex", "provider": "codex",
     "models": ["gpt-5.6-luna", "gpt-5.6-sol", "o3", "gpt-5.5"]},
]


def load_tools(path: Optional[str | Path] = None) -> list[dict]:
    """Load the tools registry from tools.yaml (next to this file by default).

    Falls back to the built-in Claude+Codex list if the file is missing or
    malformed, so the UI always has something to show.
    """
    if path is None:
        path = Path(__file__).resolve().parent / "tools.yaml"
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        tools = data.get("tools") or []
        # keep only well-formed entries
        clean = [t for t in tools if t.get("id") and t.get("models")]
        return clean or _BUILTIN_TOOLS
    except Exception:
        return _BUILTIN_TOOLS


def tool_by_id(tools: list[dict], tool_id: str) -> dict:
    """Return the tool dict for an id (first tool if not found)."""
    for t in tools:
        if t.get("id") == tool_id:
            return t
    return tools[0] if tools else _BUILTIN_TOOLS[0]
