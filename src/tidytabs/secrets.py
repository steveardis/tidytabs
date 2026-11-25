"""Find the OpenRouter API key: the environment first, then the macOS Keychain."""

import subprocess
from typing import Callable, Mapping

KEYCHAIN_SERVICE = "tidytabs"
KEYCHAIN_ACCOUNT = "openrouter"


def keychain_reader() -> str | None:
    """Read the key from the login Keychain. Return None if the item does not exist."""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    key = proc.stdout.strip()
    return key if proc.returncode == 0 and key else None


def resolve_api_key(
    env: Mapping[str, str], read_keychain: Callable[[], str | None] = keychain_reader
) -> str | None:
    return env.get("OPENROUTER_API_KEY") or read_keychain()
