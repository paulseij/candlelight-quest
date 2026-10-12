"""API key management — load/save keys from .env file."""
from __future__ import annotations

import os
from pathlib import Path

# Map provider names to environment variable names
_PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def load_env() -> None:
    """Read .env from project root into os.environ (won't override existing vars)."""
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def get_api_key(provider: str) -> str | None:
    """Get the API key for a provider from os.environ."""
    env_var = _PROVIDER_ENV_VARS.get(provider)
    if not env_var:
        return None
    return os.environ.get(env_var)


def save_api_key(provider: str, key: str) -> None:
    """Write or update an API key in the .env file."""
    env_var = _PROVIDER_ENV_VARS.get(provider)
    if not env_var:
        return

    env_path = _PROJECT_ROOT / ".env"
    lines: list[str] = []
    found = False

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{env_var}="):
                    lines.append(f"{env_var}={key}\n")
                    found = True
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")

    if not found:
        lines.append(f"{env_var}={key}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)

    # Also set in current process
    os.environ[env_var] = key
