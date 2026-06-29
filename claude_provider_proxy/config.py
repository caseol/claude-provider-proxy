"""Runtime config: load API keys from the .env file into the environment, then
load providers. Bind 127.0.0.1 only."""
from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~/.config/claude-provider-proxy"))
ENV_FILE = CONFIG_DIR / ".env"

HOST = "127.0.0.1"
PORT = int(os.environ.get("CLAUDE_PROVIDER_PROXY_PORT", "3460"))


def load_env() -> None:
    """Load KEY=VALUE lines from ~/.config/claude-provider-proxy/.env into os.environ
    (without overriding values already set in the environment)."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
