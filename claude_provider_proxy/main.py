"""Entrypoint: uvicorn on 127.0.0.1."""
from __future__ import annotations

import uvicorn

from . import config


def main() -> None:
    config.load_env()
    print(f"claude-provider-proxy on http://{config.HOST}:{config.PORT}")
    print("point Claude Code at  ANTHROPIC_BASE_URL=http://127.0.0.1:"
          f"{config.PORT}/<provider>")
    uvicorn.run("claude_provider_proxy.app:app", host=config.HOST, port=config.PORT,
                log_level="warning")


if __name__ == "__main__":
    main()
