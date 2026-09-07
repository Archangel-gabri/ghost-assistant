"""Per-invocation contracts; never import ambient CLI config or persist/echo credentials.

Targets Codex 0.153.4 and Claude Code 2.1.263. Managed machine policy remains
outside the app's control. Provider metadata is explicit, nonsecret app input.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit


MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
_PROVIDER_FIELDS = {"name", "base_url", "wire_api", "env_key", "requires_openai_auth"}


class CLIContractError(ValueError):
    """An actionable failure that is safe to show without configuration values."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def codex_isolation_args(workdir: Path, provider_spec: dict | None, *,
                         check_environment: bool = False) -> list[str]:
    """Return isolated exec flags and an explicitly chosen provider configuration."""
    if provider_spec is None:
        raise CLIContractError(
            "provider_required", "Set codex_provider_spec explicitly: OpenAI or a custom provider."
        )
    if not isinstance(provider_spec, dict) or set(provider_spec) - _PROVIDER_FIELDS:
        raise CLIContractError("provider_invalid", "Unsupported codex_provider_spec fields.")
    name = provider_spec.get("name")
    if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None:
        raise CLIContractError("provider_invalid", "Provider name must be a short identifier.")
    overrides = {"model_provider": name}
    if name == "openai":
        if set(provider_spec) != {"name"}:
            raise CLIContractError("provider_invalid", "Built-in OpenAI accepts only its name.")
        overrides["openai_base_url"] = "https://api.openai.com/v1"
    else:
        base_url = provider_spec.get("base_url")
        try:
            url = urlsplit(base_url) if isinstance(base_url, str) else None
            valid_url = bool(
                url and url.hostname and not url.username and not url.password
                and not url.query and not url.fragment
                and (url.scheme == "https" or (
                    url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
                ))
                and not any(character.isspace() for character in base_url)
            )
        except ValueError:
            valid_url = False
        if not valid_url:
            raise CLIContractError("provider_invalid", "Custom provider needs a credential-free HTTPS base_url (HTTP only on loopback).")
        if provider_spec.get("wire_api", "responses") != "responses":
            raise CLIContractError("provider_invalid", "This Codex adapter supports the responses wire_api.")
        auth = provider_spec.get("requires_openai_auth", False)
        env_key = provider_spec.get("env_key")
        if type(auth) is not bool or (env_key is not None and (
            not isinstance(env_key, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_key) is None
        )) or bool(auth) == bool(env_key):
            raise CLIContractError("provider_invalid", "Choose one custom auth source: env_key or requires_openai_auth=true.")
        prefix = f"model_providers.{name}."
        overrides.update({prefix + "name": name, prefix + "base_url": base_url,
                          prefix + "wire_api": "responses", prefix + "requires_openai_auth": auth})
        if env_key:
            overrides[prefix + "env_key"] = env_key
    # Metadata-only callers remain independent of credentials. Runtime callers
    # preflight the validated variable name without reading/copying CLI config.
    env_key = provider_spec.get("env_key")
    if check_environment and env_key and not os.environ.get(env_key, "").strip():
        raise CLIContractError(
            "provider_env_missing",
            f"Required environment variable {env_key} is missing or empty; "
            "launch Ghost from the configured environment. Do not put its value in YAML.",
        )
    cwd = str(Path(workdir).resolve())
    # CLI override keys split on dots without TOML quoted-key parsing. Keep
    # the dynamic path in the TOML value so quotes/dots/spaces remain literal.
    project_trust = f'projects={{{json.dumps(cwd, ensure_ascii=False)}={{trust_level="untrusted"}}}}'
    args = ["--ignore-user-config", "--ignore-rules", "--strict-config",
            "--disable", "hooks", "--disable", "plugins", "--disable", "apps",
            "--enable", "skip_host_skill_discovery", "--skip-git-repo-check",
            "-c", "project_root_markers=[]", "-c", "project_doc_max_bytes=0",
            "-c", project_trust]
    for key, value in overrides.items():
        args += ["-c", f"{key}={json.dumps(value, ensure_ascii=False)}"]
    return args


@dataclass(frozen=True)
class ScreenshotInput:
    data: bytes
    suffix: str

    @contextmanager
    def staged(self):
        """Expose one immutable request snapshot, not the source's parent folder."""
        with tempfile.TemporaryDirectory(prefix="ghost-screen-") as directory:
            path = Path(directory) / ("screen" + self.suffix)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(self.data)
            yield path


def read_screenshot(path: str | Path, *, max_bytes: int = MAX_SCREENSHOT_BYTES) -> ScreenshotInput:
    """Read a bounded regular file through no-follow directory descriptors."""
    if not isinstance(path, (str, Path)) or not str(path):
        raise CLIContractError("screenshot_invalid", "Screenshot path must name an image file.")
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise CLIContractError("screenshot_unsupported", "Safe screenshot access is unsupported on this platform.")
    absolute = Path(os.path.abspath(os.path.expanduser(path)))
    descriptors = []
    try:
        directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(directory)
        for component in absolute.parts[1:-1]:
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        descriptor = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CLIContractError("screenshot_invalid", "Screenshot must be a regular file.")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise CLIContractError("screenshot_size", "Screenshot is empty or exceeds the 8 MiB limit.")
        data = bytearray()
        while len(data) <= max_bytes:
            block = os.read(descriptor, min(65536, max_bytes + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(descriptor)
        if len(data) > max_bytes or len(data) != before.st_size or (
            before.st_size, before.st_mtime_ns, before.st_ctime_ns
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise CLIContractError("screenshot_changed", "Screenshot changed during capture; retry the request.")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            suffix = ".png"
        elif data.startswith(b"\xff\xd8\xff"):
            suffix = ".jpg"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            suffix = ".webp"
        else:
            raise CLIContractError("screenshot_invalid", "Screenshot must have a PNG, JPEG, or WebP image signature.")
        return ScreenshotInput(bytes(data), suffix)
    except (OSError, ValueError) as error:
        if isinstance(error, CLIContractError):
            raise
        raise CLIContractError("screenshot_invalid", "Cannot safely read the selected screenshot file.") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
